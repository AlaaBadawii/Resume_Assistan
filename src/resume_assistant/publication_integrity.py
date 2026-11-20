'''Deterministic local integrity validators for publication pairs.

Validators run before evidence projection can hide duplicate or
conflicting source structures, and again when a committed generation is
read back. They do not recalculate extraction statistics, redesign
section classification, or introduce Word parsing. Valid incomplete
extractions are preserved; duplicate conflicting source structures are
never silently discarded.
'''

import hashlib
from typing import Optional

from resume_assistant import models
from resume_assistant.models import (
    CandidateEvidenceCatalog,
    DocumentSnapshot,
    EvidenceRecord,
    TableBlock,
)
from resume_assistant.publication import PublicationError
from resume_assistant.section_detector import SectionDetector


def validate_snapshot_structure(snapshot: DocumentSnapshot) -> None:
    '''Validate snapshot structure before evidence projection.

    Runs against the raw DocumentSnapshot produced by the extractor,
    before build_evidence_catalog can collapse duplicate identifiers.
    '''
    if snapshot.schema_version != models.SCHEMA_VERSION:
        raise PublicationError('Snapshot schema version is not supported.')

    seen_indexes: set[int] = set()
    for block in snapshot.blocks:
        if block.index in seen_indexes:
            raise PublicationError('Duplicate block index in snapshot.')
        seen_indexes.add(block.index)

        if block.type == 'paragraph':
            if block.paragraph is None or block.table is not None:
                raise PublicationError('Paragraph block has inconsistent payload.')
        elif block.type == 'table':
            if block.table is None or block.paragraph is not None:
                raise PublicationError('Table block has inconsistent payload.')
            _validate_table_structure(block.table, seen_indexes := seen_indexes)
        else:
            raise PublicationError(f'Unknown block type: {block.type}')

    for part in snapshot.header_footer_parts:
        if part.source_sha256 != snapshot.source_sha256:
            raise PublicationError('Header/footer part source hash disagrees with snapshot.')
        if part.source_document != snapshot.source_filename:
            raise PublicationError('Header/footer part source filename disagrees with snapshot.')

    for reference in snapshot.header_footer_references:
        if reference.document_part is not None:
            matches = [
                part for part in snapshot.header_footer_parts
                if part.document_part == reference.document_part
            ]
            if not matches:
                raise PublicationError('Resolved header/footer reference points to no part.')


def _validate_table_structure(table: TableBlock, seen_indexes: set[int]) -> None:
    if table.row_count < 0 or table.column_count < 0:
        raise PublicationError('Table has negative dimensions.')
    if len(table.rows) != table.row_count:
        raise PublicationError('Table row count disagrees with row list.')
    # Physical cell boundaries are preserved as extracted: rows may legitimately
    # carry fewer cells than the widest row (omitted grid cells), while the
    # column count is defined as the widest physical row.
    if table.column_count != max((len(row) for row in table.rows), default=0):
        raise PublicationError('Table column count disagrees with its rows.')
    for row in table.rows:
        for cell in row:
            if cell.row_index is not None and cell.row_index < 0:
                raise PublicationError('Table cell has negative row index.')
            for nested in cell.nested_tables:
                _validate_table_structure(nested, seen_indexes)


def validate_catalog_structure(catalog: CandidateEvidenceCatalog) -> None:
    '''Validate catalog structure and identifier uniqueness.'''
    if catalog.schema_version != models.SCHEMA_VERSION:
        raise PublicationError('Catalog schema version is not supported.')

    seen_ids: dict[str, EvidenceRecord] = {}
    for record in catalog.evidence_records:
        if not record.evidence_id or not any(c.isalnum() for c in record.evidence_id):
            raise PublicationError('Evidence identifier is not meaningful.')
        previous = seen_ids.get(record.evidence_id)
        if previous is not None and previous.model_dump() != record.model_dump():
            raise PublicationError('Conflicting evidence records share one identifier.')
        seen_ids[record.evidence_id] = record

    completeness = catalog.extraction_completeness
    if completeness.evidence_records != len(catalog.evidence_records):
        raise PublicationError('Evidence record counter disagrees with record list.')

    _validate_unsupported_content_consistency(completeness)


def _validate_unsupported_content_consistency(completeness: models.ExtractionCompleteness) -> None:
    '''O-2: explicit contradictions between count, list, and snapshot are rejected.

    An absent legacy list is unavailable information, not an empty list.
    An explicitly supplied list must agree with its counter.
    '''
    model_fields = completeness.model_fields_set
    has_detailed_list = 'unsupported_content' in model_fields
    count = completeness.unsupported_content_count

    if has_detailed_list:
        detailed = completeness.unsupported_content
        if len(detailed) != count:
            raise PublicationError(
                'Unsupported-content detailed list disagrees with its counter.'
            )


def validate_publication_pair(
    snapshot: DocumentSnapshot,
    catalog: CandidateEvidenceCatalog,
) -> None:
    '''Validate cross-artifact invariants between snapshot and catalog.'''
    if snapshot.schema_version != catalog.schema_version:
        raise PublicationError('Snapshot and catalog schema versions disagree.')
    if snapshot.schema_version != models.SCHEMA_VERSION:
        raise PublicationError('Snapshot and catalog schema version is not supported.')
    if snapshot.source_sha256 != catalog.source_document.sha256:
        raise PublicationError('Snapshot and catalog source hashes disagree.')
    if snapshot.source_filename != catalog.source_document.filename:
        raise PublicationError('Snapshot and catalog source filenames disagree.')

    completeness = catalog.extraction_completeness
    if completeness.sections_detected != len(snapshot.sections):
        raise PublicationError(
            'Catalog sections-detected counter disagrees with snapshot sections.'
        )
    expected_unclassified = SectionDetector().find_unclassified(snapshot)
    if completeness.unclassified_block_indexes != expected_unclassified:
        raise PublicationError(
            'Catalog unclassified block indexes disagree with snapshot sections.'
        )
    if completeness.source_preservation_complete and snapshot.unsupported_content:
        raise PublicationError(
            'Source preservation is claimed complete but snapshot has coverage gaps.'
        )
    if completeness.coverage_assessed and not snapshot.coverage_assessed:
        raise PublicationError(
            'Catalog reports assessed coverage but snapshot inventory is not assessed.'
        )

    _validate_unsupported_content_against_snapshot(completeness, snapshot)
    _validate_evidence_records(snapshot, catalog)
    _validate_section_references(snapshot, catalog)


def _validate_unsupported_content_against_snapshot(
    completeness: models.ExtractionCompleteness,
    snapshot: DocumentSnapshot,
) -> None:
    model_fields = completeness.model_fields_set
    snapshot_gap_count = len(snapshot.unsupported_content)

    if 'unsupported_content_count' in model_fields:
        if completeness.unsupported_content_count != snapshot_gap_count:
            raise PublicationError(
                'Unsupported-content counter disagrees with snapshot gap count.'
            )

    if 'unsupported_content' in model_fields:
        detailed = completeness.unsupported_content
        if len(detailed) != snapshot_gap_count:
            raise PublicationError(
                'Unsupported-content detailed list disagrees with snapshot gap count.'
            )
        for item in detailed:
            if item.reason is not None and item.reason not in (
                'unsupported_structure',
                'unresolved_reference',
                'inventory_unavailable',
            ):
                raise PublicationError('Unsupported-content reason is not valid.')


def _validate_evidence_records(
    snapshot: DocumentSnapshot,
    catalog: CandidateEvidenceCatalog,
) -> None:
    '''Validate every evidence record against the snapshot source.'''
    source_sha256 = snapshot.source_sha256
    blocks_by_index = {block.index: block for block in snapshot.blocks}

    expected_ids: set[str] = set()
    for block in snapshot.blocks:
        if block.paragraph is not None and (block.paragraph.text or '').strip():
            expected_ids.add(
                _evidence_id_paragraph(source_sha256, block.index, block.paragraph.index)
            )
        if block.table is not None:
            for row_index in range(block.table.row_count):
                row = block.table.rows[row_index]
                meaningful = [
                    (cell.text or '')
                    for cell in row
                    if (cell.text or '').strip()
                ]
                if meaningful:
                    expected_ids.add(
                        _evidence_id_table_row(source_sha256, block.table.index, row_index)
                    )
                for cell in row:
                    for nested in cell.nested_tables:
                        _collect_nested_row_ids(
                            source_sha256, nested, expected_ids
                        )

    seen_ids: dict[str, EvidenceRecord] = {}
    for record in catalog.evidence_records:
        previous = seen_ids.get(record.evidence_id)
        if previous is not None and previous.model_dump() != record.model_dump():
            raise PublicationError('Conflicting evidence records share one identifier.')
        seen_ids[record.evidence_id] = record

        if record.evidence_id not in expected_ids:
            raise PublicationError('Evidence record identifier does not match any source location.')
        if record.source_sha256 != source_sha256:
            raise PublicationError('Evidence record source hash disagrees with snapshot source hash.')
        if record.source_document != snapshot.source_filename:
            raise PublicationError('Evidence record source document disagrees with snapshot source filename.')

        _validate_record_text(snapshot, blocks_by_index, record)

    missing = expected_ids - set(seen_ids.keys())
    if missing:
        raise PublicationError('Snapshot content has no matching evidence record.')


def _collect_nested_row_ids(
    source_sha256: str, table: TableBlock, expected_ids: set[str]
) -> None:
    for row_index in range(table.row_count):
        row = table.rows[row_index]
        meaningful = [
            (cell.text or '')
            for cell in row
            if (cell.text or '').strip()
        ]
        if meaningful:
            expected_ids.add(
                _evidence_id_table_row(source_sha256, table.index, row_index)
            )
        for cell in row:
            for nested in cell.nested_tables:
                _collect_nested_row_ids(source_sha256, nested, expected_ids)


def _validate_record_text(
    snapshot: DocumentSnapshot,
    blocks_by_index: dict[int, models.DocumentBlock],
    record: EvidenceRecord,
) -> None:
    location = record.source_location
    if location is None:
        raise PublicationError('Evidence record has no source location.')

    block = blocks_by_index.get(location.body_block_index)
    if block is None:
        raise PublicationError('Evidence record references a missing block.')

    if block.type == 'paragraph':
        if block.paragraph is None:
            raise PublicationError('Paragraph block has no paragraph payload.')
        expected_text = block.paragraph.text or ''
        if record.original_content != expected_text:
            raise PublicationError('Evidence record text disagrees with snapshot paragraph.')
    elif block.type == 'table':
        table = _find_table_by_index(snapshot, location.table_index)
        if table is None:
            raise PublicationError('Evidence record references a missing table.')
        if location.row_index is None or location.row_index >= len(table.rows):
            raise PublicationError('Evidence record references a missing table row.')
        row = table.rows[location.row_index]
        cell_texts = [(cell.text or '') for cell in row]
        meaningful = [t for t in cell_texts if t.strip()]
        expected_text = '\n'.join(meaningful)
        if record.original_content != expected_text:
            raise PublicationError('Evidence record text disagrees with snapshot table row.')
        if record.table_context is not None:
            ctx = record.table_context
            if ctx.table_index != location.table_index:
                raise PublicationError('Table context table index disagrees with source location.')
            if ctx.row_index != location.row_index:
                raise PublicationError('Table context row index disagrees with source location.')
            if ctx.column_count != table.column_count:
                raise PublicationError('Table context column count disagrees with table.')
            if ctx.cell_texts != cell_texts:
                raise PublicationError('Table context cell texts disagree with table row cells.')
    else:
        raise PublicationError('Evidence record references an unknown block type.')


def _find_table_by_index(
    snapshot: DocumentSnapshot, table_index: int
) -> Optional[TableBlock]:
    for block in snapshot.blocks:
        if block.type == 'table' and block.table is not None:
            found = _find_table_in_nested(block.table, table_index)
            if found is not None:
                return found
    return None


def _find_table_in_nested(table: TableBlock, target_index: int) -> Optional[TableBlock]:
    if table.index == target_index:
        return table
    for row in table.rows:
        for cell in row:
            for nested in cell.nested_tables:
                found = _find_table_in_nested(nested, target_index)
                if found is not None:
                    return found
    return None


def _validate_section_references(
    snapshot: DocumentSnapshot,
    catalog: CandidateEvidenceCatalog,
) -> None:
    '''Validate section references where sufficient source information exists.'''
    section_ids = {
        section.identifier for section in snapshot.sections if section.identifier
    }
    for ref in catalog.section_references:
        if ref not in section_ids:
            raise PublicationError('Catalog section references contain an unknown section.')
    for record in catalog.evidence_records:
        if record.section_reference is not None and record.section_reference not in section_ids:
            raise PublicationError('Evidence record references a missing section.')


def _evidence_id_paragraph(source_sha256: str, body_block_index: int, paragraph_index: int) -> str:
    digest_input = f'{source_sha256}|{body_block_index}|{paragraph_index}'
    return hashlib.sha256(digest_input.encode('utf-8')).hexdigest()


def _evidence_id_table_row(source_sha256: str, table_index: int, row_index: int) -> str:
    digest_input = f'{source_sha256}|{table_index}|row:{row_index}'
    return hashlib.sha256(digest_input.encode('utf-8')).hexdigest()
