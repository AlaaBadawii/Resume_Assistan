'''Synthetic document-local identity and exact table provenance regression tests.'''

import pytest

from resume_assistant import cli, evidence
from resume_assistant.extractor import MasterResumeExtractor
from resume_assistant.models import CandidateEvidenceCatalog, DocumentSnapshot
from resume_assistant.publication_reader import load_committed_pair
from tests.fixtures import builders


EXPECTED_ROWS = [
    (2, 0, 2, None, ['root-one r0c0', 'root-one r0c1'], [[0], [0]]),
    (2, 1, 2, None, ['root-one r1c0 first\nroot-one r1c0 second', 'root-one r1c1'], [[0, 1], [0]]),
    (-1, 0, 2, [2, 0, 0], ['child left', 'child right'], [[0], [0]]),
    (-2, 0, 2, [2, 0, 0, -1, 0, 1], ['deep r0c0 first\ndeep r0c0 second'], [[0, 1]]),
    (-2, 1, 2, [2, 0, 0, -1, 0, 1], ['deep r1c0'], [[0]]),
    (-3, 0, 2, [2, 0, 0], ['sibling child'], [[0]]),
    (-4, 0, 2, [2, 0, 1], ['other-cell child'], [[0]]),
    (4, 0, 4, None, ['root-two r0c0', 'root-two r0c1'], [[0], [0]]),
    (-5, 0, 4, [4, 0, 1], ['second-parent child'], [[0]]),
]


def _tables(table):
    yield table
    for row in table.rows:
        for cell in row:
            for nested in cell.nested_tables:
                yield from _tables(nested)


def test_same_instance_and_fresh_instance_have_identical_evidence_and_provenance(tmp_path):
    source = builders.build_integrity_nested_document(tmp_path / 'repeat.docx')
    before = source.read_bytes()
    extractor = MasterResumeExtractor(source)
    snapshots = [extractor.extract(), extractor.extract(), MasterResumeExtractor(source).extract()]
    catalogs = [evidence.build_evidence_catalog(snapshot) for snapshot in snapshots]
    assert catalogs[0].model_dump() == catalogs[1].model_dump() == catalogs[2].model_dump()
    assert snapshots[0].model_dump(exclude={'extraction_timestamp'}) == snapshots[1].model_dump(exclude={'extraction_timestamp'})
    assert snapshots[0].source_sha256 == snapshots[1].source_sha256 == snapshots[2].source_sha256
    assert source.read_bytes() == before


@pytest.mark.parametrize('table_id, row_id, owner, path, texts, positions', EXPECTED_ROWS)
def test_exact_nested_row_and_contributing_cell_coordinates(
    tmp_path, table_id, row_id, owner, path, texts, positions,
):
    source = builders.build_integrity_nested_document(tmp_path / 'coordinates.docx')
    catalog = evidence.build_evidence_catalog(MasterResumeExtractor(source).extract())
    records = [
        record for record in catalog.evidence_records
        if record.table_context is not None
        and (record.table_context.table_index, record.table_context.row_index) == (table_id, row_id)
    ]
    assert len(records) == 1
    record = records[0]
    assert record.original_content == '\n'.join(texts)
    assert record.source_location.document_part == 'word/document.xml'
    assert record.source_location.body_block_index == owner
    assert record.source_location.table_index == table_id
    assert record.source_location.row_index == row_id
    assert record.source_location.cell_index is None
    assert record.source_location.paragraph_index is None
    assert record.source_location.paragraph_position_in_cell is None
    assert record.source_location.nested_table_path == path
    context = record.table_context
    assert context.cell_texts == texts
    assert context.nested_table_path == path
    assert len(context.cell_sources) == len(texts)
    for cell_index, source_cell in enumerate(context.cell_sources):
        assert source_cell.table_index == table_id
        assert source_cell.row_index == row_id
        assert source_cell.cell_index == cell_index
        assert source_cell.paragraph_positions == positions[cell_index]
        assert source_cell.nested_table_path == path


def test_table_ids_row_ids_and_full_locations_are_unique(tmp_path):
    source = builders.build_integrity_nested_document(tmp_path / 'unique.docx')
    snapshot = MasterResumeExtractor(source).extract()
    tables = [table for block in snapshot.blocks if block.table is not None for table in _tables(block.table)]
    assert {table.index for table in tables} == {2, 4, -1, -2, -3, -4, -5}
    assert len(tables) == len({table.index for table in tables})
    catalog = evidence.build_evidence_catalog(snapshot)
    rows = [record for record in catalog.evidence_records if record.table_context is not None]
    assert len(rows) == len(EXPECTED_ROWS)
    assert len({record.evidence_id for record in catalog.evidence_records}) == len(catalog.evidence_records)
    locations = [
        (record.source_location.body_block_index, record.source_location.table_index,
         record.source_location.row_index, tuple(record.source_location.nested_table_path or []))
        for record in rows
    ]
    assert len(set(locations)) == len(rows)
    body = {record.original_content: record.source_location for record in catalog.evidence_records if record.table_context is None}
    assert (body['Before first table'].body_block_index, body['Before first table'].paragraph_index) == (1, 1)
    assert (body['Between tables'].body_block_index, body['Between tables'].paragraph_index) == (3, 2)


def test_deep_provenance_survives_both_artifacts_and_snapshot_reconstruction(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, 'PROJECT_ROOT', tmp_path)
    source = builders.build_integrity_nested_document(tmp_path / 'persist.docx')
    evidence_path = tmp_path / 'data/private/processed/candidate_evidence.json'
    snapshot_path = tmp_path / 'data' / 'private' / 'processed' / 'document_snapshot.json'
    catalog, _, *_ = cli.run_extract_master(source, evidence_path, snapshot_path)
    restored_snapshot, restored_catalog, _, _ = load_committed_pair(
        cli.PROJECT_ROOT, evidence_path
    )
    rebuilt = evidence.build_evidence_catalog(restored_snapshot)
    assert restored_catalog.model_dump() == catalog.model_dump() == rebuilt.model_dump()
    deep = next(record for record in rebuilt.evidence_records if record.original_content.startswith('deep r0c0'))
    assert deep.source_location.nested_table_path == [2, 0, 0, -1, 0, 1]
    assert deep.table_context.cell_sources[0].paragraph_positions == [0, 1]


def test_numbering_cache_tracks_source_bytes_between_repeated_calls(tmp_path, monkeypatch):
    source = builders.build_numbering_override_document(tmp_path / 'numbering.docx')
    original_bytes = source.read_bytes()
    extractor = MasterResumeExtractor(source)
    first = extractor.extract()
    first_catalog = evidence.build_evidence_catalog(first)
    with monkeypatch.context() as patch:
        patch.setattr(builders, 'NUMBERING_OVERRIDE_XML', builders.NUMBERING_OVERRIDE_XML.replace('lowerLetter', 'upperRoman'))
        builders.build_numbering_override_document(source)
    changed = extractor.extract()
    assert changed.source_sha256 != first.source_sha256
    assert changed.blocks[0].paragraph.numbering_format == 'upperRoman'
    source.write_bytes(original_bytes)
    restored = extractor.extract()
    assert restored.blocks[0].paragraph.numbering_format == 'lowerLetter'
    assert evidence.build_evidence_catalog(restored).model_dump() == first_catalog.model_dump()
    assert restored.unresolved_extraction_details == first.unresolved_extraction_details
