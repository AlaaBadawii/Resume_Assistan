'''Tests for the Master Resume section detection layer.'''

from pathlib import Path

from resume_assistant import models
from resume_assistant import section_detector as section_detector_module
from resume_assistant import extractor as extractor_module
from resume_assistant.section_detector import OBSERVED_TOP_LEVEL_SECTION_COUNT, SectionDetector
from tests.fixtures import builders

MASTER_PATH = Path(__file__).resolve().parent.parent / 'data' / 'private' / 'Master_Resume.docx'


def _snapshot_from(tmp_path, builder, name):
    source = tmp_path / f'{name}.docx'
    builder(source)
    return extractor_module.MasterResumeExtractor(source=source).extract()


def test_detects_top_level_sections_from_heading_fixture(tmp_path):
    snapshot = _snapshot_from(tmp_path, builders.build_headings_document, 'headings')
    sections = SectionDetector().detect(snapshot)
    assert len(sections) == 2
    assert sections[0].heading_text == 'Professional Identity'
    assert sections[0].numbering == '1.'
    assert sections[0].classification_status.value == 'detected'
    assert sections[1].heading_text == 'Technical Profile'
    assert sections[1].numbering == '2.'


def test_sub_heading_is_not_top_level(tmp_path):
    snapshot = _snapshot_from(tmp_path, builders.build_headings_document, 'headings')
    sections = SectionDetector().detect(snapshot)
    identifiers = [section.identifier for section in sections]
    assert identifiers == ['professional-identity', 'technical-profile']
    assert all(
        not section.heading_text.startswith('1.1') for section in sections
    )


def test_master_resume_top_level_section_count(tmp_path):
    snapshot = extractor_module.MasterResumeExtractor(source=MASTER_PATH).extract()
    sections = SectionDetector().detect(snapshot)
    assert len(sections) == OBSERVED_TOP_LEVEL_SECTION_COUNT
    identifiers = [section.identifier for section in sections]
    assert len(identifiers) == len(set(identifiers))


def test_unclassified_blocks_for_preamble(tmp_path):
    snapshot = extractor_module.MasterResumeExtractor(source=MASTER_PATH).extract()
    sections = SectionDetector().detect(snapshot)
    unclassified = SectionDetector().find_unclassified(snapshot)
    first_start = sections[0].start_block_index
    assert unclassified == list(range(first_start))
