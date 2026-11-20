'''Tests for the Master Resume DOCX extraction layer.'''

from pathlib import Path

import pytest

from resume_assistant import evidence as evidence_module
from resume_assistant import extractor as extractor_module
from resume_assistant import section_detector as section_detector_module
from resume_assistant import models
from resume_assistant.extractor import (
    InvalidMasterResumeError,
    MasterResumeExtractor,
    compute_sha256,
)
from tests.fixtures import builders

MASTER_PATH = Path(__file__).resolve().parent.parent / 'data' / 'private' / 'Master_Resume.docx'


def _extract(tmp_path, builder, name):
    source = tmp_path / f'{name}.docx'
    builder(source)
    extractor = MasterResumeExtractor(source=source)
    return extractor.extract()


def test_paragraph_extraction_and_order(tmp_path):
    snapshot = _extract(tmp_path, builders.build_interleaved_document, 'interleaved')
    assert snapshot.statistics.paragraph_count == 2
    assert snapshot.statistics.table_count == 1
    assert len(snapshot.blocks) == 3
    assert snapshot.blocks[0].type == 'paragraph'
    assert snapshot.blocks[0].paragraph.text == 'Intro paragraph for the synthetic document.'
    assert snapshot.blocks[1].type == 'table'
    assert snapshot.blocks[2].type == 'paragraph'
    assert snapshot.blocks[2].paragraph.text == 'Outro paragraph after the table.'


def test_table_extraction(tmp_path):
    snapshot = _extract(tmp_path, builders.build_table_document, 'table')
    table_block = snapshot.blocks[0].table
    assert table_block is not None
    assert table_block.row_count == 2
    assert table_block.column_count == 3
    assert table_block.rows[0][0].text == 'Header A'
    assert table_block.rows[1][2].text == 'R1C2'


def test_nested_table_extraction(tmp_path):
    snapshot = _extract(tmp_path, builders.build_nested_table_document, 'nested')
    table_block = snapshot.blocks[0].table
    assert table_block is not None
    nested = table_block.rows[0][1].nested_tables[0]
    assert nested is not None
    assert nested.rows[0][0].text == 'nested inner cell'


def test_hyperlink_extraction(tmp_path):
    snapshot = _extract(tmp_path, builders.build_hyperlink_document, 'hyperlink')
    paragraph = snapshot.blocks[0].paragraph
    assert paragraph is not None
    assert paragraph.hyperlink_count == 1
    assert paragraph.hyperlinks[0].text == 'https://example.com/project'


def test_formatting_metadata(tmp_path):
    snapshot = _extract(tmp_path, builders.build_headings_document, 'headings')
    first = snapshot.blocks[0].paragraph
    assert first is not None
    assert first.bold is True
    assert first.font_size_pt == 16.0
    second = snapshot.blocks[1].paragraph
    assert second is not None
    assert second.bold is True
    assert second.font_size_pt == 11.0


def test_bullet_numbering_metadata(tmp_path):
    from docx import Document

    document = Document()
    paragraph = document.add_paragraph('Bullet content')
    paragraph.style = document.styles['List Bullet']
    source = tmp_path / 'bullets.docx'
    document.save(source)
    snapshot = MasterResumeExtractor(source=source).extract()
    paragraph_block = snapshot.blocks[0].paragraph
    assert paragraph_block is not None
    assert paragraph_block.bullet is True
    assert paragraph_block.numbering_format == 'bullet'


def test_header_footer_extraction(tmp_path):
    snapshot = _extract(tmp_path, builders.build_header_footer_document, 'headerfooter')
    assert len(snapshot.headers) >= 1
    header_block = next(
        block for block in snapshot.headers if (block.paragraph is not None and block.paragraph.text)
    )
    assert header_block.paragraph.text == 'Synthetic Header Text'
    assert len(snapshot.footers) >= 1
    footer_block = next(
        block for block in snapshot.footers if (block.paragraph is not None and block.paragraph.text)
    )
    assert footer_block.paragraph.text == 'Synthetic Footer Text'


def test_unsupported_content_reported(tmp_path):
    snapshot = _extract(tmp_path, builders.build_textbox_document, 'textbox')
    assert len(snapshot.unsupported_content) >= 1
    assert snapshot.unsupported_content[0].extracted is False


def test_missing_source_raises(tmp_path):
    extractor = MasterResumeExtractor(source=tmp_path / 'missing.docx')
    try:
        extractor.extract()
        raise AssertionError('Expected FileNotFoundError')
    except FileNotFoundError:
        pass


def test_invalid_docx_raises(tmp_path):
    invalid = tmp_path / 'invalid.docx'
    invalid.write_text('not a zip archive', encoding='utf-8')
    extractor = MasterResumeExtractor(source=invalid)
    try:
        extractor.extract()
        raise AssertionError('Expected InvalidMasterResumeError')
    except InvalidMasterResumeError:
        pass


def test_extraction_does_not_modify_source():
    before = compute_sha256(MASTER_PATH)
    snapshot = MasterResumeExtractor(source=MASTER_PATH).extract()
    after = compute_sha256(MASTER_PATH)
    assert before == after
    assert snapshot.source_sha256 == before
    assert snapshot.source_filename == 'Master_Resume.docx'


def test_hyperlink_relationship_resolution(tmp_path):
    source = tmp_path / 'hyperlink_rel.docx'
    builders.build_hyperlink_relationship_document(source)
    snapshot = MasterResumeExtractor(source=source).extract()
    paragraph = snapshot.blocks[0].paragraph
    assert paragraph is not None
    assert paragraph.hyperlink_count == 1
    hyperlink = paragraph.hyperlinks[0]
    assert hyperlink.relationship_id is not None
    assert hyperlink.url == 'https://example.org/real-project'
    assert 'real project' in hyperlink.text

def _catalog_for(tmp_path, builder, name):
    source = tmp_path / f'{name}.docx'
    builder(source)
    extractor = MasterResumeExtractor(source=source)
    snapshot = extractor.extract()
    from resume_assistant import evidence as evidence_module
    from resume_assistant import section_detector as section_detector_module

    sections = section_detector_module.SectionDetector().detect(snapshot)
    catalog = evidence_module.build_evidence_catalog(snapshot, sections)
    return extractor, snapshot, catalog


def test_source_location_body_paragraph_has_no_cell_position(tmp_path):
    _extractor, _snapshot, catalog = _catalog_for(tmp_path, builders.build_interleaved_document, 'interleaved')
    body_records = [
        record
        for record in catalog.evidence_records
        if record.source_location is not None and record.source_location.table_index is None
    ]
    assert body_records, 'Expected body paragraph evidence records to be produced.'
    intro = next((r for r in body_records if (r.original_content or '').startswith('Intro paragraph')), None)
    outro = next((r for r in body_records if (r.original_content or '').startswith('Outro paragraph')), None)
    assert intro is not None and outro is not None
    assert intro.source_location is not None and intro.source_location.paragraph_position_in_cell is None
    assert outro.source_location is not None and outro.source_location.paragraph_position_in_cell is None


def test_source_location_for_table_row(tmp_path):
    _extractor, _snapshot, catalog = _catalog_for(tmp_path, builders.build_table_document, 'table')
    row_records = [r for r in catalog.evidence_records if r.table_context is not None]
    assert row_records, 'Expected table row evidence records to be produced.'
    data_row = next((r for r in row_records if r.table_context is not None and r.table_context.row_index == 1), None)
    assert data_row is not None
    assert data_row.source_location is not None
    assert data_row.source_location.table_index == 0
    assert data_row.source_location.row_index == 1
    assert data_row.source_location.cell_index is None
    assert data_row.table_context is not None and data_row.table_context.row_index == 1
    assert len(data_row.table_context.cell_sources) == 3


def test_source_location_nested_table_path(tmp_path):
    _extractor, _snapshot, catalog = _catalog_for(tmp_path, builders.build_nested_table_document, 'nested')
    outer_rows = [r for r in catalog.evidence_records if r.table_context is not None and r.table_context.table_index == 0]
    nested_rows = [r for r in catalog.evidence_records if r.table_context is not None and r.table_context.table_index != 0]
    assert len(outer_rows) == 1
    assert outer_rows[0].source_location is not None
    assert outer_rows[0].source_location.nested_table_path is None
    assert len(nested_rows) == 1
    nested = nested_rows[0]
    assert nested.source_location is not None
    assert nested.source_location.nested_table_path == [0, 0, 1]
    assert nested.source_location.table_index != 0
    assert nested.source_location.row_index is not None


def test_source_location_multiple_tables(tmp_path):
    _extractor, _snapshot, catalog = _catalog_for(tmp_path, builders.build_multi_table_document, 'multitable')
    row_records = [r for r in catalog.evidence_records if r.table_context is not None]
    assert len(row_records) == 4
    table_indexes = {r.source_location.table_index for r in row_records}
    assert len(table_indexes) == 2


def test_source_location_multiple_paragraphs_in_cell(tmp_path):
    _extractor, _snapshot, catalog = _catalog_for(tmp_path, builders.build_table_cell_multi_paragraph_document, 'multipara')
    row_records = [r for r in catalog.evidence_records if r.table_context is not None]
    assert len(row_records) == 1
    row = row_records[0]
    assert row.table_context is not None and len(row.table_context.cell_sources) == 1
    source_cell = row.table_context.cell_sources[0]
    assert source_cell.cell_index == 0
    assert source_cell.paragraph_positions == [0, 1]


def test_numbering_override_and_start_override_resolution(tmp_path):
    source = tmp_path / 'numbering_overrides.docx'
    builders.build_numbering_override_document(source)
    snapshot = MasterResumeExtractor(source=source).extract()
    paragraphs = [block.paragraph for block in snapshot.blocks if block.paragraph is not None]
    assert len(paragraphs) == 3
    assert paragraphs[0].numbering_format == 'lowerLetter'
    assert paragraphs[1].numbering_format == 'decimal'
    assert paragraphs[2].numbering_format == 'unresolved'


def test_unresolved_hyperlinks_are_recorded(tmp_path):
    source = tmp_path / 'unresolved_hyperlink.docx'
    builders.build_unresolved_hyperlink_document(source)
    extractor = MasterResumeExtractor(source=source)
    snapshot = extractor.extract()
    assert len(extractor.unresolved_hyperlinks) == 1
    from resume_assistant import section_detector as section_detector_module

    sections = section_detector_module.SectionDetector().detect(snapshot)
    catalog = evidence_module.build_evidence_catalog(
        snapshot,
        sections,
        unresolved_details=list(extractor.unresolved_hyperlinks) + list(extractor.unresolved_numbering_details),
    )
    assert len(catalog.extraction_completeness.unresolved_extraction_details) == 1
    assert catalog.extraction_completeness.source_preservation_complete is True
    assert catalog.extraction_completeness.complete is False


def test_numbering_resolution_from_numbering_xml():
    extractor = MasterResumeExtractor(source=MASTER_PATH)
    definitions = extractor._load_numbering_definitions()
    assert definitions, 'Numbering definitions should be loaded from word/numbering.xml.'
    for num_id, definition in definitions.items():
        for ilvl, level in sorted(definition['levels'].items()):
            assert extractor._resolve_numbering_format(num_id, ilvl) == level['numFmt']
    assert extractor._resolve_numbering_format(999999, 0) is None
