'''Builds the deterministic candidate evidence catalog.'''

import hashlib
from typing import List, Optional

from resume_assistant import models
from resume_assistant.models import (
    CandidateEvidenceCatalog,
    DocumentSnapshot,
    EvidenceRecord,
    EvidenceType,
    ExtractionCompleteness,
    SourceDocumentInfo,
    SourceLocation,
)
from resume_assistant.section_detector import SectionDetector

CONTACT_PREFIXES = (
    'phone',
    'mobile',
    'email',
    'e-mail',
    'location',
    'address',
    'linkedin',
    'github',
    'portfolio',
    'website',
    'personal website',
)


def _evidence_type_for_section(identifier: Optional[str]) -> EvidenceType:
    if identifier is None:
        return EvidenceType.UNCLASSIFIED
    return {
        'professional-identity': EvidenceType.PROFESSIONAL_SUMMARY,
        'technical-profile': EvidenceType.TECHNICAL_SKILL,
        'education': EvidenceType.EDUCATION,
        'professional-experience-and-engineering-training': EvidenceType.EXPERIENCE,
        'independent-software-and-ai-projects': EvidenceType.PROJECT,
        'additional-ai-and-multimodal-applications': EvidenceType.PROJECT,
        'alx-software-engineering-project-archive': EvidenceType.PROJECT,
        'technical-skills-database': EvidenceType.TECHNICAL_SKILL,
        'certifications-and-professional-development': EvidenceType.CERTIFICATION,
        'leadership-and-demonstrated-competencies': EvidenceType.LEADERSHIP,
        'achievement-and-metrics-database': EvidenceType.METRIC,
        'engineering-decisions-and-interview-evidence': EvidenceType.INTERVIEW_EVIDENCE,
        'technical-documentation-and-public-project-showcases': EvidenceType.SHOWCASE,
        'languages': EvidenceType.LANGUAGE,
    }.get(identifier, EvidenceType.UNCLASSIFIED)


def _evidence_type_for_text(text: str) -> EvidenceType:
    lowered = (text or '').strip().lower()
    for prefix in CONTACT_PREFIXES:
        if lowered.startswith(prefix):
            return EvidenceType.CONTACT
    return EvidenceType.UNCLASSIFIED


def _source_location_for_block(block, table_context=None) -> Optional[models.SourceLocation]:
    location = models.SourceLocation()
    location.document_part = 'word/document.xml'
    location.body_block_index = block.index
    paragraph = block.paragraph
    if paragraph is not None:
        location.paragraph_index = paragraph.index
        # paragraph_position_in_cell is only meaningful for paragraphs inside table cells.
        location.paragraph_position_in_cell = None
    if block.table is not None:
        location.table_index = block.table.index
        if table_context is not None:
            location.row_index = table_context.row_index
            location.cell_index = table_context.cell_index
            location.nested_table_path = table_context.nested_table_path
    return location


def _row_evidence_id(source_sha256: Optional[str], table_index: int, row_index: int) -> str:
    digest_input = f'{source_sha256}|{table_index}|row:{row_index}'
    return hashlib.sha256(digest_input.encode('utf-8')).hexdigest()


def build_evidence_catalog(
    snapshot: DocumentSnapshot,
    sections=None,
    unresolved_details: Optional[List[str]] = None,
) -> CandidateEvidenceCatalog:
    if sections is None:
        sections = []
    detector = SectionDetector()
    detected_sections = list(sections) if sections else detector.detect(snapshot)
    # Single source of truth: the snapshot always records exactly the sections
    # used for evidence assignment and unclassified-block detection.
    snapshot.sections = list(detected_sections)
    unclassified = detector.find_unclassified(snapshot)
    records: List[EvidenceRecord] = []
    section_identifiers: List[str] = []
    seen_evidence_ids = set()

    def _append_record(record: EvidenceRecord) -> None:
        nonlocal seen_evidence_ids
        if record.evidence_id in seen_evidence_ids:
            return
        seen_evidence_ids.add(record.evidence_id)
        if record.section_reference is not None and record.section_reference not in section_identifiers:
            section_identifiers.append(record.section_reference)
        records.append(record)

    def _emit_table_evidence(table_block, owner_block_index: int, ancestors) -> None:
        table_index = table_block.index
        for row_index, row in enumerate(table_block.rows):
            context = models.TableEvidenceContext(
                table_index=table_index,
                row_index=row_index,
                cell_index=None,
                column_count=table_block.column_count,
                cell_texts=[(cell.text or '') for cell in row],
                nested_table_path=list(ancestors) if ancestors is not None else None,
                cell_sources=[],
            )
            aggregated_hyperlinks: List[models.HyperlinkBlock] = []
            for cell_index, cell in enumerate(row):
                for paragraph_index, paragraph_block in enumerate(cell.paragraphs):
                    link_location = SourceLocation(
                        document_part='word/document.xml',
                        body_block_index=owner_block_index,
                        table_index=table_index,
                        row_index=row_index,
                        cell_index=cell_index,
                        nested_table_path=list(ancestors) if ancestors is not None else None,
                        paragraph_position_in_cell=paragraph_index,
                    )
                    for hyperlink in paragraph_block.hyperlinks:
                        aggregated_hyperlinks.append(
                            hyperlink.model_copy(update={'source_location': link_location.model_copy(deep=True)})
                        )
                if (cell.text or '').strip():
                    context.cell_sources.append(
                        models.TableCellSource(
                            table_index=table_index,
                            row_index=row_index,
                            cell_index=cell_index,
                            paragraph_positions=[
                                paragraph_index
                                for paragraph_index, paragraph_block in enumerate(cell.paragraphs)
                                if (paragraph_block.text or '').strip()
                            ],
                            nested_table_path=context.nested_table_path,
                        )
                    )
            meaningful = [text for text in context.cell_texts if (text or '').strip()]
            if meaningful:
                section_reference = None
                for section in detected_sections:
                    if (
                        section.start_block_index is not None
                        and section.end_block_index is not None
                        and section.start_block_index <= owner_block_index <= section.end_block_index
                    ):
                        section_reference = section.identifier
                        break
                original_content = '\n'.join(meaningful)
                evidence_type = _evidence_type_for_section(section_reference)
                if section_reference is None:
                    evidence_type = _evidence_type_for_text(original_content)
                location = models.SourceLocation(
                    document_part='word/document.xml',
                    body_block_index=owner_block_index,
                    table_index=table_index,
                    row_index=context.row_index,
                    cell_index=context.cell_index,
                    nested_table_path=context.nested_table_path,
                )
                _append_record(
                    models.EvidenceRecord(
                        evidence_id=_row_evidence_id(snapshot.source_sha256, table_index, context.row_index),
                        evidence_type=evidence_type,
                        original_content=original_content,
                        normalized_content=None,
                        section_reference=section_reference,
                        source_document=snapshot.source_filename,
                        source_sha256=snapshot.source_sha256,
                        source_location=location,
                        formatting={
                            'source': 'table',
                            'table': True,
                            'table_index': table_index,
                            'row_index': context.row_index,
                            'column_count': table_block.column_count,
                        },
                        hyperlinks=aggregated_hyperlinks,
                        table_context=context,
                    )
                )
            for cell_index, cell in enumerate(row):
                for nested in cell.nested_tables:
                    nested_ancestors = list(ancestors) if ancestors is not None else []
                    nested_ancestors.append(table_index)
                    nested_ancestors.append(row_index)
                    nested_ancestors.append(cell_index)
                    _emit_table_evidence(nested, owner_block_index, nested_ancestors)

    for block in snapshot.blocks:
        if block.table is not None:
            _emit_table_evidence(block.table, block.index, None)
            continue

        paragraph = block.paragraph
        if paragraph is None or not (paragraph.text or '').strip():
            continue
        section_reference = None
        for section in detected_sections:
            if (
                section.start_block_index is not None
                and section.end_block_index is not None
                and section.start_block_index <= block.index <= section.end_block_index
            ):
                section_reference = section.identifier
                break
        evidence_type = _evidence_type_for_section(section_reference)
        if section_reference is None:
            evidence_type = _evidence_type_for_text(paragraph.text)
        location = _source_location_for_block(block)
        digest_input = f"{snapshot.source_sha256}|{block.index}|{paragraph.index}"
        evidence_id = hashlib.sha256(digest_input.encode('utf-8')).hexdigest()
        _append_record(
            EvidenceRecord(
                evidence_id=evidence_id,
                evidence_type=evidence_type,
                original_content=paragraph.text,
                normalized_content=None,
                section_reference=section_reference,
                source_document=snapshot.source_filename,
                source_sha256=snapshot.source_sha256,
                source_location=location,
                formatting={
                    'style': paragraph.style,
                    'bold': paragraph.bold,
                    'italic': paragraph.italic,
                    'font_size_pt': paragraph.font_size_pt,
                    'alignment': paragraph.alignment,
                    'bullet': paragraph.bullet,
                    'numbering_format': paragraph.numbering_format,
                    'numbering_level': paragraph.numbering_level,
                    'hyperlink_count': paragraph.hyperlink_count,
                },
                hyperlinks=[
                    hyperlink.model_copy(
                        update={
                            'source_location': SourceLocation(
                                document_part='word/document.xml',
                                body_block_index=block.index,
                                paragraph_index=paragraph.index,
                            )
                        }
                    )
                    for hyperlink in paragraph.hyperlinks
                ],
            )
        )

    unresolved = list(dict.fromkeys([
        *snapshot.unresolved_extraction_details,
        *(unresolved_details or []),
    ]))
    source_preservation_complete = snapshot.coverage_assessed and not snapshot.unsupported_content
    section_classification_complete = bool(detected_sections) and not unclassified
    complete = bool(
        source_preservation_complete
        and section_classification_complete
        and not unresolved
    )
    completeness = ExtractionCompleteness(
        complete=complete,
        coverage_assessed=snapshot.coverage_assessed,
        source_preservation_complete=source_preservation_complete,
        section_classification_complete=section_classification_complete,
        sections_detected=len(detected_sections),
        evidence_records=len(records),
        unclassified_block_indexes=list(unclassified),
        unsupported_content_count=len(snapshot.unsupported_content),
        unsupported_content=[item.model_copy(deep=True) for item in snapshot.unsupported_content],
        unresolved_extraction_details=unresolved,
        warnings=[f'Unclassified block indexes: {list(unclassified)}'] if unclassified else [],
    )
    return CandidateEvidenceCatalog(
        schema_version=models.SCHEMA_VERSION,
        source_document=SourceDocumentInfo(
            filename=snapshot.source_filename,
            sha256=snapshot.source_sha256,
            statistics=snapshot.statistics,
        ),
        evidence_records=records,
        section_references=section_identifiers,
        extraction_completeness=completeness,
    )
