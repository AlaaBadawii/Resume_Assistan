'''Tests for the candidate evidence catalog.'''

from pathlib import Path

from resume_assistant import evidence as evidence_module
from resume_assistant import extractor as extractor_module
from resume_assistant.models import CandidateEvidenceCatalog
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
