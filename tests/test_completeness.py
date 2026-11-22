'''Synthetic regression tests for assessed coverage and completeness invariants.'''

import json

import pytest
from pydantic import ValidationError

from resume_assistant import cli, evidence
from resume_assistant.extractor import MasterResumeExtractor
from resume_assistant.models import CandidateEvidenceCatalog, DocumentSnapshot, ExtractionCompleteness
from resume_assistant.publication_reader import load_committed_pair
from tests.fixtures import builders


@pytest.mark.parametrize('gap', [
    'sdt_body', 'sdt_cell', 'header_table', 'footer_table', 'alternate_content',
    'unknown_body', 'first_page_header', 'comments', 'footnotes', 'endnotes',
    'internal_anchor',
])
def test_known_unextracted_content_is_a_coverage_gap(tmp_path, gap):
    source = builders.build_completeness_document(tmp_path / 'coverage.docx', gap=gap)
    snapshot = MasterResumeExtractor(source).extract()
    catalog = evidence.build_evidence_catalog(snapshot)
    completeness = catalog.extraction_completeness
    assert snapshot.coverage_assessed is True
    assert snapshot.unsupported_content
    assert completeness.unsupported_content_count == len(snapshot.unsupported_content)
    assert completeness.source_preservation_complete is False
    assert completeness.section_classification_complete is True
    assert completeness.complete is False
    assert not any('Synthetic unextracted evidence' in record.original_content for record in catalog.evidence_records)
    if gap in ('sdt_body', 'sdt_cell'):
        assert any(item.tag.endswith('}sdt') for item in snapshot.unsupported_content)
    elif gap in ('header_table', 'footer_table'):
        assert any(item.tag.endswith('}tbl') for item in snapshot.unsupported_content)
        part_prefix = 'word/header' if gap == 'header_table' else 'word/footer'
        assert any(item.parent_path.startswith(part_prefix) for item in snapshot.unsupported_content)
    elif gap in ('comments', 'footnotes', 'endnotes'):
        assert any(item.parent_path == f'word/{gap}.xml' for item in snapshot.unsupported_content)


@pytest.mark.parametrize('scenario, expected', [
    ('clean', (True, True, True)),
    ('preservation_gap', (False, True, False)),
    ('unclassified', (True, False, False)),
    ('unresolved_hyperlink', (True, True, False)),
    ('unresolved_numbering', (True, True, False)),
    ('resolved_list_style', (True, True, True)),
    ('resolved_custom_numbered_style', (True, True, True)),
])
def test_completeness_components_are_independent_and_survive_reconstruction(tmp_path, scenario, expected):
    source = builders.build_completeness_document(
        tmp_path / 'components.docx',
        gap='header_table' if scenario == 'preservation_gap' else None,
        preamble=scenario == 'unclassified',
        unresolved=(
            scenario.removeprefix('unresolved_').removeprefix('resolved_')
            if scenario.startswith(('unresolved_', 'resolved_')) else None
        ),
    )
    snapshot = MasterResumeExtractor(source).extract()
    catalog = evidence.build_evidence_catalog(snapshot)
    state = catalog.extraction_completeness
    assert (state.source_preservation_complete, state.section_classification_complete, state.complete) == expected
    restored_snapshot = DocumentSnapshot.model_validate_json(snapshot.model_dump_json())
    rebuilt = evidence.build_evidence_catalog(restored_snapshot)
    assert rebuilt.model_dump() == catalog.model_dump()
    if scenario.startswith('unresolved_'):
        assert snapshot.unresolved_extraction_details
        assert state.unresolved_extraction_details == snapshot.unresolved_extraction_details
        assert evidence.build_evidence_catalog(restored_snapshot, unresolved_details=[]).extraction_completeness == state
    elif scenario.startswith('resolved_'):
        assert snapshot.unresolved_extraction_details == []
        assert snapshot.blocks[1].paragraph.numbering_format in ('decimal', 'bullet')
        assert snapshot.blocks[1].paragraph.numbering_num_id is not None


def test_unknown_coverage_never_implies_preservation(tmp_path):
    source = builders.build_completeness_document(tmp_path / 'unknown.docx')
    snapshot = MasterResumeExtractor(source).extract()
    payload = json.loads(snapshot.model_dump_json())
    payload.pop('coverage_assessed')
    unknown = DocumentSnapshot.model_validate(payload)
    state = evidence.build_evidence_catalog(unknown).extraction_completeness
    assert unknown.coverage_assessed is False
    assert state.unsupported_content_count == 0
    assert state.source_preservation_complete is False
    assert state.section_classification_complete is True
    assert state.complete is False


@pytest.mark.parametrize('changes', [
    {'complete': False},
    {'coverage_assessed': False},
    {'source_preservation_complete': False},
    {'section_classification_complete': False},
    {'unsupported_content_count': 1},
    {'unsupported_content_count': -1},
    {'unclassified_block_indexes': [0]},
    {'sections_detected': 0},
    {'unresolved_extraction_details': ['Synthetic unresolved detail']},
])
def test_contradictory_completeness_is_rejected_on_construction_and_json_reload(changes):
    valid = {
        'complete': True,
        'coverage_assessed': True,
        'source_preservation_complete': True,
        'section_classification_complete': True,
        'sections_detected': 1,
    }
    ExtractionCompleteness.model_validate(valid)
    invalid = {**valid, **changes}
    with pytest.raises(ValidationError):
        ExtractionCompleteness.model_validate(invalid)
    with pytest.raises(ValidationError):
        ExtractionCompleteness.model_validate_json(json.dumps(invalid))


def test_unresolved_details_persist_in_both_cli_artifacts(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, 'PROJECT_ROOT', tmp_path)
    source = builders.build_completeness_document(tmp_path / 'persist.docx', unresolved='hyperlink')
    before = source.read_bytes()
    evidence_path = tmp_path / 'data' / 'private' / 'processed' / 'candidate_evidence.json'
    snapshot_path = tmp_path / 'data' / 'private' / 'processed' / 'document_snapshot.json'
    catalog, snapshot, *_ = cli.run_extract_master(source, evidence_path, snapshot_path)
    restored_snapshot, restored_catalog, _, _ = load_committed_pair(
        cli.PROJECT_ROOT, evidence_path
    )
    assert restored_snapshot.unresolved_extraction_details == snapshot.unresolved_extraction_details
    assert evidence.build_evidence_catalog(restored_snapshot).model_dump() == catalog.model_dump()
    assert restored_catalog.model_dump() == catalog.model_dump()
    assert restored_catalog.extraction_completeness.section_classification_complete is True
    assert restored_catalog.extraction_completeness.complete is False
    assert source.read_bytes() == before


@pytest.mark.parametrize('definition_gap', ['missing_format', 'combined_override'])
def test_unhandled_numbering_definitions_are_coverage_gaps(tmp_path, definition_gap):
    from docx import Document
    from docx.oxml import parse_xml

    document = Document()
    document.add_heading('1. Technical Profile', level=1)
    paragraph = document.add_paragraph('Synthetic numbered evidence')
    properties = paragraph._p.get_or_add_pPr().get_or_add_numPr()
    properties.get_or_add_numId().val = 1
    properties.get_or_add_ilvl().val = 0
    xml = builders.NUMBERING_OVERRIDE_XML
    if definition_gap == 'missing_format':
        xml = xml.replace('<w:numFmt w:val="lowerLetter"/>', '<w:numFmt/>')
    else:
        xml = xml.replace(
            '<w:lvlOverride w:ilvl="0"><w:lvl',
            '<w:lvlOverride w:ilvl="0"><w:startOverride w:val="9"/><w:lvl',
            1,
        )
    document.part.numbering_part._element = parse_xml(xml.encode('utf-8'))
    source = tmp_path / 'numbering_gap.docx'
    document.save(source)
    snapshot = MasterResumeExtractor(source).extract()
    state = evidence.build_evidence_catalog(snapshot).extraction_completeness
    assert any(item.parent_path.startswith('word/numbering.xml/') for item in snapshot.unsupported_content)
    assert all(item.source_location.document_part == 'word/numbering.xml' for item in snapshot.unsupported_content)
    assert state.source_preservation_complete is False
    assert state.section_classification_complete is True
    assert state.complete is False
