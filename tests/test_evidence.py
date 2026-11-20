'''Tests for the candidate evidence catalog.'''

import json
from pathlib import Path

from resume_assistant import cli as cli_module
from resume_assistant import evidence as evidence_module
from resume_assistant import extractor as extractor_module
from resume_assistant.models import CandidateEvidenceCatalog
from resume_assistant.publication_reader import load_committed_pair
from tests.fixtures import builders

MASTER_PATH = Path(__file__).resolve().parent.parent / 'data' / 'private' / 'Master_Resume.docx'


def _master_catalog():
    extractor = extractor_module.MasterResumeExtractor(source=MASTER_PATH)
    snapshot = extractor.extract()
    from resume_assistant.section_detector import SectionDetector

    detected = SectionDetector().detect(snapshot)
    return evidence_module.build_evidence_catalog(snapshot, detected), snapshot


def test_evidence_ids_are_deterministic():
    catalog_one, snapshot_one = _master_catalog()
    catalog_two, snapshot_two = _master_catalog()
    assert snapshot_one.source_sha256 == snapshot_two.source_sha256
    assert [record.evidence_id for record in catalog_one.evidence_records] == [
        record.evidence_id for record in catalog_two.evidence_records
    ]
    assert len(catalog_one.evidence_records) > 0


def test_catalog_pydantic_round_trip():
    catalog, _snapshot = _master_catalog()
    payload = catalog.model_dump_json()
    restored = CandidateEvidenceCatalog.model_validate_json(payload)
    assert restored.source_document.sha256 == catalog.source_document.sha256
    assert len(restored.evidence_records) == len(catalog.evidence_records)
    assert restored.extraction_completeness.sections_detected == catalog.extraction_completeness.sections_detected


def test_evidence_catalog_contents(tmp_path):
    source = tmp_path / 'interleaved.docx'
    builders.build_interleaved_document(source)
    extractor = extractor_module.MasterResumeExtractor(source=source)
    snapshot = extractor.extract()
    from resume_assistant.section_detector import SectionDetector

    sections = SectionDetector().detect(snapshot)
    catalog = evidence_module.build_evidence_catalog(snapshot, sections)
    assert isinstance(catalog, CandidateEvidenceCatalog)
    assert catalog.source_document.filename == 'interleaved.docx'
    assert catalog.source_document.sha256 == snapshot.source_sha256
    assert catalog.extraction_completeness.evidence_records == len(catalog.evidence_records)


def test_table_evidence_persisted_to_json(tmp_path, monkeypatch):
    '''Mission 2.1: prove table-based evidence survives the persisted JSON artifact,
    not merely the in-memory extraction object.'''
    import json

    from resume_assistant import cli as cli_module
    from resume_assistant.publication_reader import load_committed_pair

    monkeypatch.setattr(cli_module, 'PROJECT_ROOT', tmp_path)
    source = tmp_path / 'master_table.docx'
    builders.build_table_document(source)
    evidence_path = tmp_path / 'data' / 'private' / 'processed' / 'candidate_evidence.json'
    snapshot_path = tmp_path / 'data' / 'private' / 'processed' / 'document_snapshot.json'
    catalog, snapshot, sections, unclassified, output_path, snapshot_output_path, _commit = (
        cli_module.run_extract_master(
            master_path=source,
            evidence_output_path=evidence_path,
            snapshot_output_path=snapshot_path,
        )
    )
    _, persisted_catalog, _, _ = load_committed_pair(
        cli_module.PROJECT_ROOT, evidence_path
    )
    persisted_table_records = [
        record for record in persisted_catalog.evidence_records if record.table_context
    ]
    assert persisted_table_records, 'Table evidence must be present in the persisted candidate evidence JSON.'


def test_extraction_completeness_semantics(tmp_path):
    source = tmp_path / 'interleaved.docx'
    builders.build_interleaved_document(source)
    extractor = extractor_module.MasterResumeExtractor(source=source)
    snapshot = extractor.extract()
    from resume_assistant.section_detector import SectionDetector

    sections = SectionDetector().detect(snapshot)
    catalog = evidence_module.build_evidence_catalog(snapshot, sections)
    completeness = catalog.extraction_completeness
    assert completeness.source_preservation_complete is True
    assert isinstance(completeness.section_classification_complete, bool)
    assert completeness.unsupported_content_count == len(snapshot.unsupported_content)


def test_nested_table_evidence_end_to_end(tmp_path, monkeypatch):
    monkeypatch.setattr(cli_module, 'PROJECT_ROOT', tmp_path)
    source = tmp_path / 'nested.docx'
    builders.build_nested_table_document(source)
    evidence_path = tmp_path / 'data' / 'private' / 'processed' / 'candidate_evidence.json'
    snapshot_path = tmp_path / 'data' / 'private' / 'processed' / 'document_snapshot.json'
    catalog, snapshot, sections, unclassified, output_path, snapshot_output_path, _commit = (
        cli_module.run_extract_master(
            master_path=source,
            evidence_output_path=evidence_path,
            snapshot_output_path=snapshot_path,
        )
    )
    _, persisted_catalog, _, _ = load_committed_pair(
        cli_module.PROJECT_ROOT, evidence_path
    )
    persisted_payload = json.loads(json.dumps(persisted_catalog.model_dump()))
    nested_records = [
        record
        for record in persisted_payload.get('evidence_records', [])
        if (record.get('source_location') or {}).get('nested_table_path')
    ]
    assert nested_records, 'Nested table evidence must survive persistence with source coordinates.'
    assert 'nested inner cell' in (nested_records[0].get('original_content') or '')
    restored = CandidateEvidenceCatalog.model_validate_json(
        json.dumps(persisted_catalog.model_dump())
    )
    restored_nested = [
        record
        for record in restored.evidence_records
        if record.source_location is not None and record.source_location.nested_table_path
    ]
    assert restored_nested
    assert 'nested inner cell' in restored_nested[0].original_content
    assert snapshot.statistics.table_paragraph_count == 3


def test_table_row_evidence_preserves_cell_hyperlinks(tmp_path):
    source = tmp_path / 'table_cell_hyperlink.docx'
    builders.build_table_cell_hyperlink_document(source)
    extractor = extractor_module.MasterResumeExtractor(source=source)
    snapshot = extractor.extract()
    from resume_assistant import section_detector as section_detector_module

    sections = section_detector_module.SectionDetector().detect(snapshot)
    catalog = evidence_module.build_evidence_catalog(snapshot, sections)
    row_records = [r for r in catalog.evidence_records if r.table_context is not None]
    assert row_records
    row = row_records[0]
    assert len(row.hyperlinks) == 1
    assert row.hyperlinks[0].relationship_id == 'rId99'
    assert row.hyperlinks[0].url == 'https://example.org/rel-project'


def test_extraction_completeness_source_preservation_reflects_unsupported(tmp_path):
    source = tmp_path / 'textbox.docx'
    builders.build_textbox_document(source)
    extractor = extractor_module.MasterResumeExtractor(source=source)
    snapshot = extractor.extract()
    from resume_assistant import section_detector as section_detector_module

    sections = section_detector_module.SectionDetector().detect(snapshot)
    catalog = evidence_module.build_evidence_catalog(snapshot, sections)
    completeness = catalog.extraction_completeness
    assert completeness.source_preservation_complete is False
    assert completeness.unsupported_content_count > 0
    assert completeness.complete is False


def test_extraction_completeness_section_classification_reflects_unclassified(tmp_path):
    source = tmp_path / 'interleaved.docx'
    builders.build_interleaved_document(source)
    extractor = extractor_module.MasterResumeExtractor(source=source)
    snapshot = extractor.extract()
    from resume_assistant import section_detector as section_detector_module

    sections = section_detector_module.SectionDetector().detect(snapshot)
    catalog = evidence_module.build_evidence_catalog(snapshot, sections)
    completeness = catalog.extraction_completeness
    assert completeness.source_preservation_complete is True
    assert completeness.section_classification_complete is False
    assert completeness.complete is False
