'''Tests for the Master Resume DOCX extraction layer.'''

from pathlib import Path

import pytest

from resume_assistant import extractor as extractor_module
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
