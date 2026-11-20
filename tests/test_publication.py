'''Mission 2.2.2 — Artifact integrity and coherent publication regressions.

Covers generation/manifest publication, committed-pair reading, structural
and cross-artifact integrity, legacy compatibility, and failure-injection
scenarios. Uses synthetic fixtures only.
'''

import hashlib
import io
import json
import os
import tempfile
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import Mock, patch

import pytest
from docx import Document
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from pydantic_core import PydanticSerializationError

from resume_assistant import cli as cli_module
from resume_assistant import evidence as evidence_module
from resume_assistant import extractor as extractor_module
from resume_assistant.job_models import JobDescriptionAnalysis
from resume_assistant.matching_models import CandidateJobMatch
from resume_assistant.models import (
    CandidateEvidenceCatalog,
    DocumentSnapshot,
    ExtractionCompleteness,
    SourceDocumentInfo,
    EvidenceRecord,
    EvidenceType,
)
from resume_assistant.publication_reader import load_committed_pair
from resume_assistant.publication_store import resolve_bundle_key
from resume_assistant.publication import (
    MANIFEST_FORMAT_KIND,
    PUBLICATION_FORMAT_VERSION,
    PublicationError,
)
from tests.fixtures import builders


SANDBOX_PREFIX = 'mission222-test-'
MARKER = 'SYNTHETIC_M222_FAILURE'


def _make_project_document(path, version, gap=False):
    document = Document()
    document.add_heading('1. Technical Profile', level=1)
    document.add_paragraph(f'Python SYNTHETIC_GENERATION_{version}')
    document.add_table(rows=1, cols=1).cell(0, 0).text = f'SYNTHETIC_ROW_{version}'
    document.sections[0].header.paragraphs[0].text = 'SYNTHETIC_HEADER'
    if gap:
        p = document.add_paragraph()
        wrapper = OxmlElement('w:moveTo')
        wrapper.set(qn('w:id'), '1')
        wrapper.set(qn('w:author'), 'Synthetic reviewer')
        run = OxmlElement('w:r')
        text = OxmlElement('w:t')
        text.text = 'SYNTHETIC_UNEXTRACTED_CONTENT'
        run.append(text)
        wrapper.append(run)
        p._p.append(wrapper)
    document.save(path)


def _make_project(name, *, existing=True, gap=False):
    root = Path(tempfile.mkdtemp(prefix=SANDBOX_PREFIX + name + '-', dir='/tmp/opencode'))
    input_path = root / 'source.docx'
    catalog = root / 'data/private/processed/candidate_evidence.json'
    snapshot = root / 'data/private/processed/document_snapshot.json'
    _make_project_document(input_path, 'A', gap=gap)
    committed_generation_id = None
    old_source_sha256 = None
    if existing:
        with patch.object(cli_module, 'PROJECT_ROOT', root):
            cli_module.run_extract_master(input_path, catalog, snapshot)
        with patch.object(cli_module, 'PROJECT_ROOT', root):
            _snapshot, _catalog, _manifest, committed_generation_id = load_committed_pair(
                root, catalog
            )
        old_source_sha256 = hashlib.sha256(input_path.read_bytes()).hexdigest()
    old_bytes = {path: path.read_bytes() if path.exists() else None for path in (catalog, snapshot)}
    _make_project_document(input_path, 'B', gap=gap)
    return {
        'root': root, 'source': input_path, 'catalog': catalog, 'snapshot': snapshot,
        'old_bytes': old_bytes,
        'new_source_bytes': input_path.read_bytes(),
        'committed_generation_id': committed_generation_id,
        'old_source_sha256': old_source_sha256,
    }


def _args(item):
    return [
        'extract-master', '--master', str(item['source']),
        '--output', str(item['catalog']),
        '--snapshot-output', str(item['snapshot']),
    ]


def _analysis():
    scalars = ['role_title', 'company_name', 'seniority_level', 'employment_type', 'work_arrangement']
    payload = {key: None for key in scalars}
    payload.update({key: [] for key in [
        'responsibilities', 'required_skills', 'preferred_skills', 'uncertain_skills',
        'education_requirements', 'experience_requirements', 'languages', 'certifications',
        'ambiguities',
    ]})
    payload['missing_information'] = scalars
    payload['required_skills'] = [{'name': 'Python', 'quotation': 'Required: Python.',
                                   'classification_context': 'Required: Python.'}]
    return JobDescriptionAnalysis.model_validate({**payload, 'prompt_version': '2.0.0'})


def _observed(item, *, generation_id):
    result = {
        'committed_generation_id': generation_id,
        'generation_id': generation_id,
        'generation_selected': 'none',
        'source_hashes_agree': False,
        'catalog': 'missing',
        'snapshot': 'missing',
        'both_standalone_models_valid': False,
        'source_unchanged_by_application': item['source'].read_bytes() == item['new_source_bytes'],
        'remaining_artifact_temporaries': len(list(item['catalog'].parent.glob('.resume-assistant-*.tmp'))),
    }
    manifest_path = item['catalog']
    if not manifest_path.exists():
        return result
    with patch.object(cli_module, 'PROJECT_ROOT', item['root']):
        snapshot, catalog, manifest, gen_id = load_committed_pair(
            cli_module.PROJECT_ROOT, manifest_path
        )
    result['generation_selected'] = 'committed'
    result['generation_id'] = gen_id
    result['catalog'] = catalog.source_document.sha256
    result['snapshot'] = snapshot.source_sha256
    result['source_hashes_agree'] = (
        catalog.source_document.sha256 == snapshot.source_sha256
        and catalog.source_document.sha256 == item['old_source_sha256']
    )
    result['both_standalone_models_valid'] = True
    return result


def _publication_failure(number, *, existing=True):
    item = _make_project(f'replace-{number}-existing-{existing}', existing=existing)
    real_replace = os.replace
    calls = []

    def replace(first, second, **kwargs):
        calls.append(second)
        if len(calls) == number:
            raise OSError(MARKER)
        return real_replace(first, second, **kwargs)

    stdout, stderr = io.StringIO(), io.StringIO()
    with patch.object(cli_module, 'PROJECT_ROOT', item['root']), \
            patch.object(os, 'replace', replace), \
            redirect_stdout(stdout), redirect_stderr(stderr):
        code = cli_module.main(_args(item))
    result = _observed(item, generation_id=item['committed_generation_id'])
    result.update(exit_code=code, replacement_order=calls,
                  error_marker_leaked=MARKER in stdout.getvalue() + stderr.getvalue())
    assert code == 3 and len(calls) == number and not result['error_marker_leaked']
    assert result['source_unchanged_by_application']
    if existing:
        # The manifest is the sole commit point, so a mid-publication failure
        # must leave the previously committed generation selected.
        assert result['generation_id'] == item['committed_generation_id']
        assert result['source_hashes_agree']
        assert result['catalog'] not in (None, 'missing')
        assert result['snapshot'] not in (None, 'missing')
        assert result['both_standalone_models_valid']
    else:
        # Before the first successful commit there is no committed pair to load.
        with pytest.raises(PublicationError):
            load_committed_pair(item['root'], item['catalog'])
    if number == 2 and existing:
        job = item['root'] / 'analysis.json'
        job.write_text(_analysis().model_dump_json(), encoding='utf-8')
        with patch.object(cli_module, 'PROJECT_ROOT', item['root']):
            matched = cli_module.run_match_job(job, item['catalog'], item['catalog'].parent / 'match.json')
        result['match_job_accepted_committed_pair'] = matched.requirements[0].status == 'supported'
        assert result['match_job_accepted_committed_pair']
    return result


def test_first_replacement_failure_leaves_no_committed_pair():
    result = _publication_failure(1, existing=False)
    assert result['generation_selected'] == 'none'


def test_second_replacement_failure_leaves_committed_generation_unchanged():
    result = _publication_failure(2)
    # The manifest is the sole commit point, so the committed generation stays A.
    assert result['generation_id'] == result['committed_generation_id']
    assert result['source_hashes_agree']


def test_first_publication_second_failure_leaves_no_committed_pair():
    result = _publication_failure(2, existing=False)
    assert result['generation_selected'] == 'none'


def test_publication_validation_order_snapshot_before_catalog():
    item = _make_project('validation-order')
    real_extract = extractor_module.MasterResumeExtractor.extract
    validation_reached = []

    def extract_with_validation(worker):
        snapshot = real_extract(worker)
        validation_reached.append('snapshot')
        return snapshot

    real_build = evidence_module.build_evidence_catalog
    def build_with_validation(snapshot, *values, **kwargs):
        validation_reached.append('catalog')
        return real_build(snapshot, *values, **kwargs)

    with patch.object(cli_module, 'PROJECT_ROOT', item['root']), \
            patch.object(extractor_module.MasterResumeExtractor, 'extract', extract_with_validation), \
            patch.object(evidence_module, 'build_evidence_catalog', build_with_validation):
        cli_module.run_extract_master(item['source'], item['catalog'], item['snapshot'])
    assert validation_reached == ['snapshot', 'catalog']


def test_committed_pair_reader_checksums_verified():
    item = _make_project('checksum-verify')
    with patch.object(cli_module, 'PROJECT_ROOT', item['root']):
        snapshot, catalog, manifest, gen_id = load_committed_pair(
            cli_module.PROJECT_ROOT, item['catalog']
        )
    assert manifest.generation_id == gen_id
    assert catalog.source_document.sha256 == snapshot.source_sha256
    assert snapshot.source_filename == catalog.source_document.filename


def test_manifest_is_at_committed_address():
    item = _make_project('manifest-address')
    assert item['catalog'].exists()
    document = json.loads(item['catalog'].read_text())
    assert document.get('format') == MANIFEST_FORMAT_KIND
    assert document.get('format_version') == PUBLICATION_FORMAT_VERSION


def test_legacy_catalog_still_loadable():
    item = _make_project('legacy-loadable')
    legacy = CandidateEvidenceCatalog(
        schema_version='3.0.0',
        source_document=SourceDocumentInfo(filename='legacy.docx', sha256='a' * 64, statistics=None),
        evidence_records=[
            EvidenceRecord(
                evidence_id='legacy-1', evidence_type=EvidenceType.TECHNICAL_SKILL,
                original_content='Python development', normalized_content=None,
                section_reference=None, source_document='legacy.docx',
                source_sha256='a' * 64, source_location=None, formatting=None,
                hyperlinks=[], table_context=None,
            )
        ],
        section_references=[],
        extraction_completeness=ExtractionCompleteness(
            complete=True, coverage_assessed=True, source_preservation_complete=True,
            section_classification_complete=True, sections_detected=1,
            evidence_records=1, unclassified_block_indexes=[],
            unsupported_content_count=0, unresolved_extraction_details=[],
            warnings=[],
        ),
    )
    item['catalog'].write_text(legacy.model_dump_json(), encoding='utf-8')
    with patch.object(cli_module, 'PROJECT_ROOT', item['root']):
        snapshot, catalog, manifest, gen_id = load_committed_pair(
            cli_module.PROJECT_ROOT, item['catalog']
        )
    assert snapshot is None
    assert manifest is None
    assert catalog.schema_version == '3.0.0'
    assert len(catalog.evidence_records) == 1


def test_publication_pair_source_hash_consistency():
    item = _make_project('hash-consistency')
    with patch.object(cli_module, 'PROJECT_ROOT', item['root']):
        snapshot, catalog, manifest, gen_id = load_committed_pair(
            cli_module.PROJECT_ROOT, item['catalog']
        )
    assert catalog.source_document.sha256 == snapshot.source_sha256
    assert manifest.source_sha256 == snapshot.source_sha256


def test_publication_pair_source_filename_consistency():
    item = _make_project('filename-consistency')
    with patch.object(cli_module, 'PROJECT_ROOT', item['root']):
        snapshot, catalog, manifest, gen_id = load_committed_pair(
            cli_module.PROJECT_ROOT, item['catalog']
        )
    assert catalog.source_document.filename == snapshot.source_filename
    assert manifest.source_filename == snapshot.source_filename


def test_immutable_generation_not_overwritten():
    item = _make_project('immutable')
    from resume_assistant.publication_store import reserve_generation
    with patch.object(cli_module, 'PROJECT_ROOT', item['root']):
        bundle_key = resolve_bundle_key(cli_module.PROJECT_ROOT, item['catalog'])
        generation_dir = item['root'] / 'data/private/processed/.extractions' / bundle_key / 'generations'
        gen_dirs = [d for d in generation_dir.iterdir() if d.is_dir()]
        assert len(gen_dirs) == 1
        gen_id = gen_dirs[0].name
        with pytest.raises(Exception):
            reserve_generation(cli_module.PROJECT_ROOT, bundle_key, gen_id)


def test_manifest_does_not_leak_private_values():
    item = _make_project('no-leak')
    document = json.loads(item['catalog'].read_text())
    combined = json.dumps(document)
    assert MARKER not in combined


def test_reader_rejects_missing_snapshot():
    item = _make_project('missing-snapshot')
    with patch.object(cli_module, 'PROJECT_ROOT', item['root']):
        generation_dir = item['root'] / 'data/private/processed/.extractions' / resolve_bundle_key(
            cli_module.PROJECT_ROOT, item['catalog']
        ) / 'generations'
        gen_dirs = [d for d in generation_dir.iterdir() if d.is_dir()]
        gen_id = gen_dirs[0].name
        snapshot_path = gen_dirs[0] / 'snapshot.json'
        snapshot_path.unlink()
        with pytest.raises(PublicationError):
            load_committed_pair(cli_module.PROJECT_ROOT, item['catalog'])


def test_reader_rejects_missing_catalog():
    item = _make_project('missing-catalog')
    with patch.object(cli_module, 'PROJECT_ROOT', item['root']):
        generation_dir = item['root'] / 'data/private/processed/.extractions' / resolve_bundle_key(
            cli_module.PROJECT_ROOT, item['catalog']
        ) / 'generations'
        gen_dirs = [d for d in generation_dir.iterdir() if d.is_dir()]
        gen_id = gen_dirs[0].name
        catalog_path = gen_dirs[0] / 'catalog.json'
        catalog_path.unlink()
        with pytest.raises(PublicationError):
            load_committed_pair(cli_module.PROJECT_ROOT, item['catalog'])


def test_reader_rejects_checksum_mismatch():
    item = _make_project('checksum-mismatch')
    with patch.object(cli_module, 'PROJECT_ROOT', item['root']):
        load_committed_pair(cli_module.PROJECT_ROOT, item['catalog'])
        generation_dir = item['root'] / 'data/private/processed/.extractions' / resolve_bundle_key(
            cli_module.PROJECT_ROOT, item['catalog']
        ) / 'generations'
        gen_dirs = [d for d in generation_dir.iterdir() if d.is_dir()]
        gen_id = gen_dirs[0].name
        tampered = gen_dirs[0] / 'snapshot.json'
        data = tampered.read_bytes()
        tampered.write_bytes(data + b'x')
        with pytest.raises(Exception):
            load_committed_pair(cli_module.PROJECT_ROOT, item['catalog'])
        tampered.write_bytes(data)


def test_match_job_with_manifest_uses_coherent_pair():
    item = _make_project('coherent-match')
    job = item['root'] / 'analysis.json'
    job.write_text(_analysis().model_dump_json(), encoding='utf-8')
    match_path = item['catalog'].parent / 'match.json'
    with patch.object(cli_module, 'PROJECT_ROOT', item['root']):
        code = cli_module.main([
            'match-job', '--analysis', str(job),
            '--candidate-evidence', str(item['catalog']),
            '--output', str(match_path),
        ])
    assert code == 0
    match = CandidateJobMatch.model_validate_json(match_path.read_bytes())
    assert match.schema_version == '2.0.0'
    assert match.candidate_evidence_schema_version == '3.0.0'
    assert match.requirements[0].matching_method == 'exact_normalized_phrase_v2'


def test_legacy_catalog_matching_unchanged():
    item = _make_project('legacy-match')
    legacy = CandidateEvidenceCatalog(
        schema_version='3.0.0',
        source_document=SourceDocumentInfo(filename='legacy.docx', sha256='a' * 64, statistics=None),
        evidence_records=[
            EvidenceRecord(
                evidence_id='legacy-py', evidence_type=EvidenceType.TECHNICAL_SKILL,
                original_content='Python development', normalized_content=None,
                section_reference=None, source_document='legacy.docx',
                source_sha256='a' * 64, source_location=None, formatting=None,
                hyperlinks=[], table_context=None,
            )
        ],
        section_references=[],
        extraction_completeness=ExtractionCompleteness(
            complete=True, coverage_assessed=True, source_preservation_complete=True,
            section_classification_complete=True, sections_detected=1,
            evidence_records=1, unclassified_block_indexes=[],
            unsupported_content_count=0, unresolved_extraction_details=[],
            warnings=[],
        ),
    )
    item['catalog'].write_text(legacy.model_dump_json(), encoding='utf-8')
    job = item['root'] / 'analysis.json'
    job.write_text(_analysis().model_dump_json(), encoding='utf-8')
    match_path = item['catalog'].parent / 'match.json'
    with patch.object(cli_module, 'PROJECT_ROOT', item['root']):
        code = cli_module.main([
            'match-job', '--analysis', str(job),
            '--candidate-evidence', str(item['catalog']),
            '--output', str(match_path),
        ])
    assert code == 0
    match = CandidateJobMatch.model_validate_json(match_path.read_bytes())
    assert match.requirements[0].status == 'supported'


def test_no_automatic_generation_deletion():
    item = _make_project('no-deletion')
    with patch.object(cli_module, 'PROJECT_ROOT', item['root']):
        generation_dir = item['root'] / 'data/private/processed/.extractions' / resolve_bundle_key(
            cli_module.PROJECT_ROOT, item['catalog']
        ) / 'generations'
        gen_count_before = len([d for d in generation_dir.iterdir() if d.is_dir()])
        cli_module.run_extract_master(item['source'], item['catalog'], item['snapshot'])
    gen_count_after = len([d for d in generation_dir.iterdir() if d.is_dir()])
    assert gen_count_after == gen_count_before + 1


def test_duplicate_conflicting_snapshot_block_rejected():
    item = _make_project('duplicate-block')
    real_extract = extractor_module.MasterResumeExtractor.extract

    def conflicting_extract(worker):
        snapshot = real_extract(worker)
        duplicate = snapshot.blocks[1].model_copy(deep=True)
        duplicate.paragraph.text = 'SYNTHETIC_CONFLICTING_BLOCK'
        snapshot.blocks.append(duplicate)
        return snapshot

    with patch.object(cli_module, 'PROJECT_ROOT', item['root']), \
            patch.object(extractor_module.MasterResumeExtractor, 'extract', conflicting_extract):
        exit_code = cli_module.main(_args(item))
    assert exit_code == 2


def test_evidence_text_mismatch_rejected():
    item = _make_project('evidence-mismatch')
    real_build = evidence_module.build_evidence_catalog

    def mismatched_build(snapshot, *values, **kwargs):
        catalog = real_build(snapshot, *values, **kwargs)
        if catalog.evidence_records:
            catalog.evidence_records[0].original_content = 'SYNTHETIC_INVENTED_CONTENT'
        return catalog

    with patch.object(cli_module, 'PROJECT_ROOT', item['root']), \
            patch.object(evidence_module, 'build_evidence_catalog', mismatched_build):
        exit_code = cli_module.main(_args(item))
    assert exit_code == 2


def test_evidence_record_counter_mismatch_rejected():
    item = _make_project('counter-mismatch')
    real_build = evidence_module.build_evidence_catalog

    def counter_mismatch_build(snapshot, *values, **kwargs):
        catalog = real_build(snapshot, *values, **kwargs)
        catalog.extraction_completeness.evidence_records = 999
        return catalog

    with patch.object(cli_module, 'PROJECT_ROOT', item['root']), \
            patch.object(evidence_module, 'build_evidence_catalog', counter_mismatch_build):
        exit_code = cli_module.main(_args(item))
    assert exit_code == 2


def test_unsupported_content_detailed_list_disagreement_rejected():
    item = _make_project('diagnostic-disagreement', gap=True)
    real_build = evidence_module.build_evidence_catalog

    def empty_diagnostics_build(snapshot, *values, **kwargs):
        catalog = real_build(snapshot, *values, **kwargs)
        catalog.extraction_completeness.unsupported_content = []
        return catalog

    with patch.object(cli_module, 'PROJECT_ROOT', item['root']), \
            patch.object(evidence_module, 'build_evidence_catalog', empty_diagnostics_build):
        exit_code = cli_module.main(_args(item))
    assert exit_code == 2


def test_unsupported_content_counter_disagreement_rejected():
    item = _make_project('counter-disagreement', gap=True)
    real_build = evidence_module.build_evidence_catalog

    def zero_counter_build(snapshot, *values, **kwargs):
        catalog = real_build(snapshot, *values, **kwargs)
        catalog.extraction_completeness.unsupported_content_count = 0
        catalog.extraction_completeness.source_preservation_complete = True
        catalog.extraction_completeness.complete = True
        return catalog

    with patch.object(cli_module, 'PROJECT_ROOT', item['root']), \
            patch.object(evidence_module, 'build_evidence_catalog', zero_counter_build):
        exit_code = cli_module.main(_args(item))
    assert exit_code == 2


def test_legacy_absent_vs_explicit_details_distinguished():
    item = _make_project('legacy-details', gap=True)
    with patch.object(cli_module, 'PROJECT_ROOT', item['root']):
        _, catalog, _, _ = load_committed_pair(cli_module.PROJECT_ROOT, item['catalog'])
    data_absent = json.loads(json.dumps(catalog.model_dump()))
    data_absent['extraction_completeness'].pop('unsupported_content')
    model_absent = CandidateEvidenceCatalog.model_validate_json(json.dumps(data_absent), strict=True)
    assert 'unsupported_content' not in model_absent.extraction_completeness.model_fields_set

    data_explicit = json.loads(json.dumps(catalog.model_dump()))
    data_explicit['extraction_completeness']['unsupported_content'] = []
    model_explicit = CandidateEvidenceCatalog.model_validate_json(json.dumps(data_explicit), strict=True)
    assert 'unsupported_content' in model_explicit.extraction_completeness.model_fields_set
    assert model_absent.extraction_completeness == model_explicit.extraction_completeness


def test_validate_publication_pair_rejects_disagreeing_unclassified_blocks():
    from resume_assistant.publication_integrity import validate_publication_pair

    item = _make_project('unclassified-disagreement')
    with patch.object(cli_module, 'PROJECT_ROOT', item['root']):
        snapshot, catalog, _, _ = load_committed_pair(cli_module.PROJECT_ROOT, item['catalog'])
        catalog.extraction_completeness.unclassified_block_indexes = [9999]
        with pytest.raises(PublicationError, match='Catalog unclassified block indexes disagree'):
            validate_publication_pair(snapshot, catalog)


def test_validate_publication_pair_rejects_unknown_section_references():
    from resume_assistant.publication_integrity import validate_publication_pair

    item = _make_project('section-ref-disagreement')
    with patch.object(cli_module, 'PROJECT_ROOT', item['root']):
        snapshot, catalog, _, _ = load_committed_pair(cli_module.PROJECT_ROOT, item['catalog'])
        catalog.section_references.append('phantom-section-id')
        with pytest.raises(PublicationError, match='Catalog section references contain an unknown section'):
            validate_publication_pair(snapshot, catalog)


def test_ragged_table_rows_validate_successfully_in_publication_pair(tmp_path):
    from docx import Document
    from resume_assistant.extractor import MasterResumeExtractor
    from resume_assistant.publication_integrity import (
        validate_catalog_structure,
        validate_publication_pair,
        validate_snapshot_structure,
    )

    document = Document()
    document.add_heading('1. Technical Profile', level=1)
    table = document.add_table(rows=2, cols=3)
    table.rows[0].cells[0].text = 'r0c0'
    table.rows[0].cells[1].text = 'r0c1'
    table.rows[0].cells[2].text = 'r0c2'
    table.rows[1].cells[0].text = 'r1c0'
    # Delete cell 1 and cell 2 from row 1 in oxml to make row 1 ragged (1 cell)
    table.rows[1]._tr.remove(table.rows[1].cells[2]._tc)
    table.rows[1]._tr.remove(table.rows[1].cells[1]._tc)

    source = tmp_path / 'ragged.docx'
    builders.save(document, source)
    snapshot = MasterResumeExtractor(source).extract()
    assert snapshot.blocks[1].table.column_count == 3
    assert len(snapshot.blocks[1].table.rows[0]) == 3
    assert len(snapshot.blocks[1].table.rows[1]) == 1

    catalog = evidence_module.build_evidence_catalog(snapshot)
    validate_snapshot_structure(snapshot)
    validate_catalog_structure(catalog)
    validate_publication_pair(snapshot, catalog)
