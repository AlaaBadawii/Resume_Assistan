'''Regression tests for the local extraction CLI persistence and error paths.'''

import json
import os
import urllib.error
from pathlib import Path
from unittest.mock import Mock

import pytest
from pydantic import BaseModel, ValidationError, model_serializer
from pydantic_core import PydanticSerializationError

from resume_assistant import cli as cli_module
from resume_assistant import models
from resume_assistant import private_output
from resume_assistant import llm_client
from resume_assistant.extractor import MasterResumeExtractor
from resume_assistant.job_analyzer import JobAnalysisInputError
from resume_assistant.job_models import JobDescriptionAnalysis
from resume_assistant.llm_client import LLMNetworkError, LLMProviderError
from resume_assistant.models import CandidateEvidenceCatalog, DocumentSnapshot
from resume_assistant.publication_reader import load_committed_pair
from tests.fixtures import builders
from tests.fixtures.jobs import SYNTHETIC_JOB_TEXT, grounded_job_payload

SYNTHETIC_SECRET_MARKER = 'SYNTHETIC_SECRET_MARKER_7a3f91'


def _synthetic_master(tmp_path: Path) -> Path:
    source = tmp_path / 'master_interleaved.docx'
    builders.build_interleaved_document(source)
    return source


@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.setattr(cli_module, 'PROJECT_ROOT', tmp_path)
    source = _synthetic_master(tmp_path)
    evidence_path = tmp_path / 'data' / 'private' / 'processed' / 'candidate_evidence.json'
    snapshot_path = tmp_path / 'data' / 'private' / 'processed' / 'document_snapshot.json'
    return source, evidence_path, snapshot_path


def _arguments(project):
    source, evidence_path, snapshot_path = project
    return [
        'extract-master', '--master', str(source), '--output', str(evidence_path),
        '--snapshot-output', str(snapshot_path),
    ]


def _valid_existing_artifacts(project):
    source, evidence_path, snapshot_path = project
    cli_module.run_extract_master(source, evidence_path, snapshot_path)
    snapshot, catalog, manifest, gen_id = load_committed_pair(
        cli_module.PROJECT_ROOT, evidence_path
    )
    return {
        source: source.read_bytes(),
        evidence_path: evidence_path.read_bytes(),
    }


def _failing_validator(artifact, monkeypatch):
    model = CandidateEvidenceCatalog if artifact == 'catalog' else DocumentSnapshot
    original = model.model_validate_json

    def validate(payload):
        data = json.loads(payload)
        if artifact == 'catalog':
            data['source_document']['sha256'] = [SYNTHETIC_SECRET_MARKER]
        else:
            data['source_sha256'] = [SYNTHETIC_SECRET_MARKER]
        try:
            return original(json.dumps(data))
        except ValidationError as error:
            assert SYNTHETIC_SECRET_MARKER in str(error)
            raise

    validator = Mock(side_effect=validate)
    monkeypatch.setattr(model, 'model_validate_json', validator)
    return validator


@pytest.mark.parametrize('artifact', ['catalog', 'snapshot'])
def test_validation_error_does_not_leak_sensitive_value(project, monkeypatch, capsys, artifact):
    validator = _failing_validator(artifact, monkeypatch)
    writer = Mock()
    monkeypatch.setattr(cli_module, '_atomic_write_json', writer)
    exit_code = cli_module.main(_arguments(project))
    captured = capsys.readouterr()
    combined = f'{captured.out}\n{captured.err}'
    assert SYNTHETIC_SECRET_MARKER not in combined
    assert 'artifact validation failed' in captured.out
    validator.assert_called_once()
    writer.assert_not_called()
    assert exit_code == 2


@pytest.mark.parametrize('artifact', ['catalog', 'snapshot'])
def test_failed_validation_does_not_overwrite_existing_valid_artifact(
    project, monkeypatch, capsys, artifact,
):
    before = _valid_existing_artifacts(project)
    validator = _failing_validator(artifact, monkeypatch)
    writer = Mock()
    monkeypatch.setattr(cli_module, '_atomic_write_json', writer)
    exit_code = cli_module.main(_arguments(project))
    captured = capsys.readouterr()
    assert 'artifact validation failed' in captured.out
    assert SYNTHETIC_SECRET_MARKER not in captured.out + captured.err
    validator.assert_called_once()
    writer.assert_not_called()
    assert exit_code == 2
    source, evidence_path, _snapshot_path = project
    assert {source: source.read_bytes(), evidence_path: evidence_path.read_bytes()} == before


@pytest.mark.parametrize('artifact', ['catalog', 'snapshot'])
def test_serialization_error_does_not_leak_sensitive_value(project, monkeypatch, capsys, artifact):
    class SensitiveSerializer(BaseModel):
        @model_serializer
        def serialize(self):
            raise ValueError(SYNTHETIC_SECRET_MARKER)

    with pytest.raises(PydanticSerializationError) as failure:
        SensitiveSerializer().model_dump_json()
    assert SYNTHETIC_SECRET_MARKER in str(failure.value)
    before = _valid_existing_artifacts(project)
    model = CandidateEvidenceCatalog if artifact == 'catalog' else DocumentSnapshot
    serializer = Mock(side_effect=failure.value)
    monkeypatch.setattr(model, 'model_dump_json', serializer)
    writer = Mock()
    monkeypatch.setattr(cli_module, '_atomic_write_json', writer)
    exit_code = cli_module.main(_arguments(project))
    captured = capsys.readouterr()
    serializer.assert_called_once()
    writer.assert_not_called()
    assert exit_code == 2
    assert 'artifact serialization failed' in captured.out
    assert SYNTHETIC_SECRET_MARKER not in captured.out + captured.err
    source, evidence_path, _snapshot_path = project
    assert {source: source.read_bytes(), evidence_path: evidence_path.read_bytes()} == before


def test_malformed_document_value_error_is_sanitized(project, monkeypatch, capsys):
    source, _, _ = project
    builders.build_malformed_numbering_document(source, SYNTHETIC_SECRET_MARKER)
    before = source.read_bytes()
    with pytest.raises(ValueError) as failure:
        MasterResumeExtractor(source).extract()
    assert SYNTHETIC_SECRET_MARKER in str(failure.value)
    writer = Mock()
    monkeypatch.setattr(cli_module, '_atomic_write_json', writer)
    exit_code = cli_module.main(_arguments(project))
    captured = capsys.readouterr()
    writer.assert_not_called()
    assert exit_code == 2
    assert 'invalid document data' in captured.out
    assert SYNTHETIC_SECRET_MARKER not in captured.out + captured.err
    assert source.read_bytes() == before


def test_private_output_destination_is_rejected(tmp_path):
    source = _synthetic_master(tmp_path)
    with pytest.raises(ValueError):
        cli_module.run_extract_master(
            master_path=source,
            evidence_output_path=tmp_path / 'out' / 'candidate_evidence.json',
            snapshot_output_path=tmp_path / 'out' / 'document_snapshot.json',
        )


def test_snapshot_and_evidence_artifacts_persisted_and_round_trip(tmp_path, monkeypatch):
    monkeypatch.setattr(cli_module, 'PROJECT_ROOT', tmp_path)
    source = _synthetic_master(tmp_path)
    evidence_path = tmp_path / 'data' / 'private' / 'processed' / 'candidate_evidence.json'
    snapshot_path = tmp_path / 'data' / 'private' / 'processed' / 'document_snapshot.json'
    catalog, snapshot, sections, unclassified, output_path, snapshot_output_path, commit = (
        cli_module.run_extract_master(
            master_path=source,
            evidence_output_path=evidence_path,
            snapshot_output_path=snapshot_path,
        )
    )
    assert evidence_path.exists()
    # The manifest is at --output; snapshot/catalog live in the committed generation.
    restored_snapshot, restored_catalog, manifest, gen_id = load_committed_pair(
        cli_module.PROJECT_ROOT, evidence_path
    )
    assert len(restored_catalog.evidence_records) == len(catalog.evidence_records)
    assert restored_snapshot.source_filename == snapshot.source_filename
    assert restored_snapshot.source_sha256 == snapshot.source_sha256
    assert manifest.format_version == '1.0.0'
    assert manifest.generation_id == commit.generation_id
    assert commit.snapshot_path.exists()
    assert commit.catalog_path.exists()


@pytest.mark.parametrize('alias', [
    'outputs_equal', 'outputs_dotdot', 'outputs_relative', 'outputs_symlink',
    'source_evidence', 'source_snapshot', 'source_dotdot', 'source_relative',
    'source_symlink_input', 'source_symlink_output',
])
def test_output_aliases_rejected_before_extraction_or_writing(project, monkeypatch, alias):
    before = _valid_existing_artifacts(project)
    original_source, evidence_path, snapshot_path = project
    source = evidence_path.parent / 'source.docx'
    builders.build_interleaved_document(source)
    before[source] = source.read_bytes()
    if alias == 'outputs_equal':
        snapshot_path = evidence_path
    elif alias == 'outputs_dotdot':
        snapshot_path = evidence_path.parent / 'unused' / '..' / evidence_path.name
    elif alias == 'outputs_relative':
        monkeypatch.chdir(original_source.parent)
        evidence_path = Path('data/private/processed/candidate_evidence.json')
        snapshot_path = Path('data/private/processed/../processed/candidate_evidence.json')
    elif alias == 'outputs_symlink':
        snapshot_path = evidence_path.parent / 'snapshot_alias.json'
        snapshot_path.symlink_to(evidence_path)
    elif alias == 'source_evidence':
        evidence_path = source
    elif alias == 'source_snapshot':
        snapshot_path = source
    elif alias == 'source_dotdot':
        evidence_path = source.parent / 'unused' / '..' / source.name
    elif alias == 'source_relative':
        monkeypatch.chdir(original_source.parent)
        snapshot_path = Path('data/private/processed/source.docx')
    else:
        link = source.parent / 'source_alias.docx'
        link.symlink_to(source)
        if alias == 'source_symlink_input':
            evidence_path, source = source, link
        else:
            evidence_path = link

    writer, extractor, hasher = Mock(), Mock(), Mock()
    monkeypatch.setattr(cli_module, '_atomic_write_json', writer)
    monkeypatch.setattr(cli_module.extractor_module, 'MasterResumeExtractor', extractor)
    monkeypatch.setattr(cli_module.extractor_module, 'compute_sha256', hasher)
    message = 'distinct files' if alias.startswith('outputs_') else 'alias the source DOCX'
    with pytest.raises(cli_module.ExtractionConfigurationError, match=message):
        cli_module.run_extract_master(source, evidence_path, snapshot_path)
    writer.assert_not_called()
    extractor.assert_not_called()
    hasher.assert_not_called()
    assert {path: path.read_bytes() for path in before} == before


def test_successful_cli_normalizes_paths_and_preserves_source(project, monkeypatch):
    source, evidence_path, snapshot_path = project
    before = source.read_bytes()
    monkeypatch.chdir(source.parent)
    result = cli_module.run_extract_master(
        Path(source.name),
        Path('data/private/processed/../processed/candidate_evidence.json'),
        Path('data/private/processed/document_snapshot.json'),
    )
    catalog, snapshot, sections, unclassified, output_path, snapshot_output_path, commit = result
    assert output_path == evidence_path.resolve()
    assert snapshot_output_path == snapshot_path.resolve()
    assert evidence_path.exists()
    assert commit.manifest_path == evidence_path
    assert commit.generation_id
    assert commit.snapshot_path.exists()
    assert commit.catalog_path.exists()
    assert source.read_bytes() == before


def test_writer_does_not_follow_a_preexisting_temporary_symlink_to_source(project):
    _valid_existing_artifacts(project)
    source, evidence_path, snapshot_path = project
    before = source.read_bytes()
    # A symlinked temporary name at the output path must not be followed
    # during manifest temporary creation.
    poisoned_tmp = evidence_path.with_name(evidence_path.name + '.tmp')
    poisoned_tmp.symlink_to(source)
    cli_module.run_extract_master(source, evidence_path, snapshot_path)
    assert source.read_bytes() == before
    assert poisoned_tmp.is_symlink()
    # Manifest is valid and committed.
    snapshot, catalog, manifest, gen_id = load_committed_pair(
        cli_module.PROJECT_ROOT, evidence_path
    )
    assert manifest.generation_id == gen_id
    assert catalog.source_document.sha256 == snapshot.source_sha256


def test_failed_replacement_preserves_artifacts_and_sanitizes_io_error(project, monkeypatch, capsys):
    before = _valid_existing_artifacts(project)
    replace = Mock(side_effect=OSError(SYNTHETIC_SECRET_MARKER))
    monkeypatch.setattr('resume_assistant.publication_store.os.replace', replace)
    exit_code = cli_module.main(_arguments(project))
    captured = capsys.readouterr()
    assert exit_code == 3
    assert 'extraction failed: OSError' in captured.out
    assert SYNTHETIC_SECRET_MARKER not in captured.out + captured.err
    source, evidence_path, _snapshot_path = project
    assert {source: source.read_bytes(), evidence_path: evidence_path.read_bytes()} == before
    # No orphaned manifest temporary should remain at the output address.
    assert not list(evidence_path.parent.glob('.resume-assistant-*.tmp'))


# ---------------------------------------------------------------------------
# analyze-job (Mission 3.1)
# ---------------------------------------------------------------------------

SYNTHETIC_JD = SYNTHETIC_JOB_TEXT

SYNTHETIC_API_KEY = 'sk-ar-test-key-not-real'


def _analysis_payload():
    return grounded_job_payload()


@pytest.fixture
def analyze_project(tmp_path, monkeypatch):
    monkeypatch.setattr(cli_module, 'PROJECT_ROOT', tmp_path)
    source = tmp_path / 'job.txt'
    source.write_text(SYNTHETIC_JD, encoding='utf-8')
    output = tmp_path / 'data' / 'private' / 'processed' / 'job_description_analysis.json'
    return source, output


def _analyze_arguments(source, output):
    return ['analyze-job', '--input', str(source), '--output', str(output)]


def _patch_generate(monkeypatch, response=None, error=None):
    calls = []

    def fake_generate(self, *, instructions, input_text):
        calls.append({'instructions': instructions, 'input': input_text})
        if error is not None:
            raise error
        return json.dumps(_analysis_payload()) if response is None else response

    monkeypatch.setattr(cli_module.AgentRouterClient, 'generate', fake_generate)
    return calls


def test_analyze_job_success_writes_valid_artifact_and_aggregate_summary(
    analyze_project, monkeypatch, capsys,
):
    source, output = analyze_project
    monkeypatch.setenv('AGENTROUTER_API_KEY', SYNTHETIC_API_KEY)
    calls = _patch_generate(monkeypatch)
    exit_code = cli_module.main(_analyze_arguments(source, output))
    captured = capsys.readouterr()

    assert exit_code == 0
    assert output.exists()
    analysis = JobDescriptionAnalysis.model_validate_json(output.read_text(encoding='utf-8'))
    assert analysis.schema_version == '2.0.0'
    assert analysis.prompt_version == '2.0.0'
    assert analysis.role_title.value == 'Senior Backend Engineer'
    assert analysis.interpretation_status == 'ai_generated_unverified'
    assert len(calls) == 1
    assert SYNTHETIC_JD not in calls[0]['instructions']
    assert calls[0]['input'] == SYNTHETIC_JD

    # Summary shows aggregates and presence booleans only.
    assert 'Job description analysis summary' in captured.out
    assert 'role_title_present: True' in captured.out
    assert 'required_skills: 1' in captured.out
    assert 'preferred_skills: 1' in captured.out
    assert 'interpretations_verified: False' in captured.out
    # Never leak input text, credentials, or extracted field values.
    assert SYNTHETIC_JD not in captured.out
    assert SYNTHETIC_API_KEY not in captured.out
    assert 'Senior Backend Engineer' not in captured.out
    assert 'Acme Corp' not in captured.out


def test_analyze_job_missing_api_key_exits_two_without_calling_client(
    analyze_project, monkeypatch, capsys,
):
    source, output = analyze_project
    monkeypatch.delenv('AGENTROUTER_API_KEY', raising=False)

    def fail(self, **kwargs):
        raise AssertionError('client must not be called without configuration')

    monkeypatch.setattr(cli_module.AgentRouterClient, 'generate', fail)
    exit_code = cli_module.main(_analyze_arguments(source, output))
    captured = capsys.readouterr()

    assert exit_code == 2
    assert 'AGENTROUTER_API_KEY' in captured.out
    assert not output.exists()


def test_analyze_job_missing_input_file_exits_two(analyze_project, monkeypatch, capsys):
    _, output = analyze_project
    monkeypatch.setenv('AGENTROUTER_API_KEY', SYNTHETIC_API_KEY)
    _patch_generate(monkeypatch)
    missing = output.parent / 'does_not_exist.txt'
    exit_code = cli_module.main(_analyze_arguments(missing, output))
    captured = capsys.readouterr()
    assert exit_code == 2
    assert 'could not be found' in captured.out
    assert not output.exists()


def test_analyze_job_non_utf8_input_exits_two(analyze_project, monkeypatch, capsys):
    source, output = analyze_project
    source.write_bytes(b'\xff\xfe\x00not-utf8')
    monkeypatch.setenv('AGENTROUTER_API_KEY', SYNTHETIC_API_KEY)
    _patch_generate(monkeypatch)
    exit_code = cli_module.main(_analyze_arguments(source, output))
    captured = capsys.readouterr()
    assert exit_code == 2
    assert 'UTF-8' in captured.out
    assert not output.exists()


def test_analyze_job_non_private_output_is_rejected(analyze_project, monkeypatch, capsys):
    source, _ = analyze_project
    outside = source.parent / 'outside' / 'job_description_analysis.json'
    monkeypatch.setenv('AGENTROUTER_API_KEY', SYNTHETIC_API_KEY)
    _patch_generate(monkeypatch)
    exit_code = cli_module.main(_analyze_arguments(source, outside))
    captured = capsys.readouterr()
    assert exit_code == 2
    assert 'data/private/processed' in captured.out
    assert not outside.exists()


def test_analyze_job_output_aliasing_input_is_rejected(analyze_project, monkeypatch, capsys):
    source, _ = analyze_project
    inside = source.parent / 'data' / 'private' / 'processed' / 'job.txt'
    inside.parent.mkdir(parents=True, exist_ok=True)
    inside.write_text(SYNTHETIC_JD, encoding='utf-8')
    monkeypatch.setenv('AGENTROUTER_API_KEY', SYNTHETIC_API_KEY)
    _patch_generate(monkeypatch)
    exit_code = cli_module.main(_analyze_arguments(inside, inside))
    captured = capsys.readouterr()
    assert exit_code == 2
    assert 'alias the input' in captured.out
    assert inside.read_text(encoding='utf-8') == SYNTHETIC_JD


def test_analyze_job_provider_error_is_sanitized(analyze_project, monkeypatch, capsys):
    source, output = analyze_project
    monkeypatch.setenv('AGENTROUTER_API_KEY', SYNTHETIC_API_KEY)
    _patch_generate(monkeypatch, error=LLMProviderError('provider request failed with HTTP 502.'))
    exit_code = cli_module.main(_analyze_arguments(source, output))
    captured = capsys.readouterr()
    assert exit_code == 2
    assert 'provider request failed with HTTP 502.' in captured.out
    assert SYNTHETIC_JD not in captured.out
    assert SYNTHETIC_API_KEY not in captured.out
    assert not output.exists()


def test_analyze_job_network_error_is_sanitized(analyze_project, monkeypatch, capsys):
    source, output = analyze_project
    monkeypatch.setenv('AGENTROUTER_API_KEY', SYNTHETIC_API_KEY)
    _patch_generate(monkeypatch, error=LLMNetworkError('provider request failed (URLError).'))
    exit_code = cli_module.main(_analyze_arguments(source, output))
    captured = capsys.readouterr()
    assert exit_code == 2
    assert 'provider request failed (URLError).' in captured.out
    assert not output.exists()


def test_analyze_job_unexpected_error_reports_type_only(analyze_project, monkeypatch, capsys):
    source, output = analyze_project
    monkeypatch.setenv('AGENTROUTER_API_KEY', SYNTHETIC_API_KEY)
    _patch_generate(monkeypatch, error=RuntimeError(SYNTHETIC_SECRET_MARKER))
    exit_code = cli_module.main(_analyze_arguments(source, output))
    captured = capsys.readouterr()
    assert exit_code == 3
    assert 'analysis failed: RuntimeError' in captured.out
    assert SYNTHETIC_SECRET_MARKER not in captured.out + captured.err
    assert not output.exists()


def test_analyze_job_quotation_failure_exits_two_without_artifact(
    analyze_project, monkeypatch, capsys,
):
    source, output = analyze_project
    monkeypatch.setenv('AGENTROUTER_API_KEY', SYNTHETIC_API_KEY)
    bad = _analysis_payload()
    bad['required_skills'][0]['quotation'] = 'Python quotation absent from the posting'
    bad['required_skills'][0]['classification_context'] = 'Python quotation absent from the posting'
    _patch_generate(monkeypatch, response=json.dumps(bad))
    exit_code = cli_module.main(_analyze_arguments(source, output))
    captured = capsys.readouterr()
    assert exit_code == 2
    assert 'exact substring' in captured.out
    assert 'quotation absent from the posting' not in captured.out
    assert not output.exists()


def _existing_valid_job_artifact(analyze_project, monkeypatch):
    source, output = analyze_project
    monkeypatch.setenv('AGENTROUTER_API_KEY', SYNTHETIC_API_KEY)
    _patch_generate(monkeypatch)
    cli_module.run_analyze_job(source, output)
    JobDescriptionAnalysis.model_validate_json(output.read_text(encoding='utf-8'))
    return source.read_bytes(), output.read_bytes()


def _completed_provider_response(text):
    return {
        'status': 'completed', 'error': None, 'incomplete_details': None,
        'output': [{'type': 'message', 'role': 'assistant', 'status': 'completed',
                    'content': [{'type': 'output_text', 'text': text}]}],
    }


@pytest.mark.parametrize('failure_kind, expected_code', [
    ('http_error', 2), ('network_error', 2), ('malformed_provider_json', 2),
    ('invalid_model_types', 2), ('unsupported_company', 2), ('missing_model_key', 2),
    ('skill_conflict', 2), ('incomplete_provider', 2), ('refusal', 2),
    ('unexpected_transport', 3),
])
def test_real_client_error_path_is_sanitized_and_preserves_valid_analysis(
    analyze_project, monkeypatch, capsys, failure_kind, expected_code,
):
    source, output = analyze_project
    original_generate = cli_module.AgentRouterClient.generate
    before = _existing_valid_job_artifact(analyze_project, monkeypatch)
    # Restore the actual generate method: failures exercise the real client,
    # response validation, analyzer, and CLI rather than a sanitized error stub.
    monkeypatch.setattr(cli_module.AgentRouterClient, 'generate', original_generate)
    monkeypatch.setenv('AGENTROUTER_BASE_URL', 'https://provider.invalid/v1')
    payload = _analysis_payload()
    sensitive = SYNTHETIC_SECRET_MARKER + SYNTHETIC_API_KEY + SYNTHETIC_JD
    transport = Mock()
    if failure_kind == 'http_error':
        transport.side_effect = urllib.error.HTTPError('https://provider.invalid/v1/responses', 401, sensitive, None, None)
    elif failure_kind == 'network_error':
        transport.side_effect = urllib.error.URLError(sensitive)
    elif failure_kind == 'unexpected_transport':
        transport.side_effect = RuntimeError(sensitive)
    elif failure_kind == 'malformed_provider_json':
        transport.return_value = (200, sensitive.encode('utf-8'))
    else:
        if failure_kind == 'invalid_model_types':
            payload['responsibilities'] = sensitive
        elif failure_kind == 'unsupported_company':
            payload['company_name']['value'] = sensitive
        elif failure_kind == 'missing_model_key':
            payload.pop('company_name')
        elif failure_kind == 'skill_conflict':
            payload['preferred_skills'].append(dict(payload['required_skills'][0]))
        document = _completed_provider_response(json.dumps(payload))
        if failure_kind == 'incomplete_provider':
            document['status'] = 'incomplete'
            document['incomplete_details'] = {'reason': sensitive}
        elif failure_kind == 'refusal':
            document['output'][0]['content'].append({'type': 'refusal', 'refusal': sensitive})
        transport.return_value = (200, json.dumps(document).encode('utf-8'))
    monkeypatch.setattr(llm_client, '_urllib_post', transport)
    writer = Mock()
    monkeypatch.setattr(cli_module, '_atomic_write_json', writer)
    assert cli_module.main(_analyze_arguments(source, output)) == expected_code
    combined = capsys.readouterr()
    assert SYNTHETIC_SECRET_MARKER not in combined.out + combined.err
    assert SYNTHETIC_API_KEY not in combined.out + combined.err
    assert SYNTHETIC_JD not in combined.out + combined.err
    if expected_code == 3:
        assert 'analysis failed: RuntimeError' in combined.out
    transport.assert_called_once()
    writer.assert_not_called()
    assert (source.read_bytes(), output.read_bytes()) == before


def test_analysis_round_trip_validation_failure_is_reached_and_preserves_existing_artifact(
    analyze_project, monkeypatch, capsys,
):
    source, output = analyze_project
    before = _existing_valid_job_artifact(analyze_project, monkeypatch)
    original = JobDescriptionAnalysis.model_validate_json

    def corrupt_then_validate(payload):
        data = json.loads(payload)
        data['company_name']['value'] = [SYNTHETIC_SECRET_MARKER]
        try:
            return original(json.dumps(data))
        except ValidationError as error:
            assert SYNTHETIC_SECRET_MARKER in str(error)
            raise

    validator = Mock(side_effect=corrupt_then_validate)
    writer = Mock()
    monkeypatch.setattr(JobDescriptionAnalysis, 'model_validate_json', validator)
    monkeypatch.setattr(cli_module, '_atomic_write_json', writer)
    assert cli_module.main(_analyze_arguments(source, output)) == 2
    captured = capsys.readouterr()
    assert 'artifact validation failed' in captured.out
    assert SYNTHETIC_SECRET_MARKER not in captured.out + captured.err
    validator.assert_called_once()
    writer.assert_not_called()
    assert (source.read_bytes(), output.read_bytes()) == before


def test_directory_job_input_is_controlled_and_does_not_call_client(analyze_project, monkeypatch, capsys):
    source, output = analyze_project
    calls = _patch_generate(monkeypatch)
    assert cli_module.main(_analyze_arguments(source.parent, output)) == 2
    assert 'regular file' in capsys.readouterr().out
    assert calls == []
    assert not output.exists()


def test_input_identity_permission_error_is_controlled_and_sanitized(analyze_project, monkeypatch, capsys):
    source, output = analyze_project
    before = _existing_valid_job_artifact(analyze_project, monkeypatch)
    original = Path.stat
    calls = _patch_generate(monkeypatch)

    def inaccessible(path, *args, **kwargs):
        if path == source:
            raise PermissionError(SYNTHETIC_SECRET_MARKER)
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, 'stat', inaccessible)
    assert cli_module.main(_analyze_arguments(source, output)) == 2
    captured = capsys.readouterr()
    assert 'file identity could not be checked' in captured.out
    assert SYNTHETIC_SECRET_MARKER not in captured.out + captured.err
    assert calls == []
    assert (source.read_bytes(), output.read_bytes()) == before


def test_unreadable_regular_input_is_controlled(analyze_project, monkeypatch, capsys):
    if not hasattr(os, 'geteuid') or os.geteuid() == 0:
        pytest.skip('a non-root POSIX process is required for a real read-permission failure')
    source, output = analyze_project
    before = _existing_valid_job_artifact(analyze_project, monkeypatch)
    calls = _patch_generate(monkeypatch)
    original_mode = source.stat().st_mode & 0o777
    source.chmod(0)
    try:
        assert cli_module.main(_analyze_arguments(source, output)) == 2
        assert 'input file could not be read' in capsys.readouterr().out
        assert calls == []
    finally:
        source.chmod(original_mode)
    assert (source.read_bytes(), output.read_bytes()) == before


def test_nonregular_fifo_input_is_rejected_before_reading(analyze_project, monkeypatch, capsys):
    if not hasattr(os, 'mkfifo'):
        pytest.skip('FIFO input requires POSIX')
    source, output = analyze_project
    fifo = source.parent / 'fifo.txt'
    os.mkfifo(fifo)
    calls = _patch_generate(monkeypatch)
    assert cli_module.main(_analyze_arguments(fifo, output)) == 2
    assert 'regular file' in capsys.readouterr().out
    assert calls == []
    assert not output.exists()


def test_analyze_job_uses_default_private_output_when_output_flag_is_omitted(
    analyze_project, monkeypatch,
):
    source, output = analyze_project
    monkeypatch.setattr(cli_module, 'DEFAULT_ANALYSIS_OUTPUT_PATH', output)
    monkeypatch.setenv('AGENTROUTER_API_KEY', SYNTHETIC_API_KEY)
    calls = _patch_generate(monkeypatch)
    assert cli_module.main(['analyze-job', '--input', str(source)]) == 0
    assert len(calls) == 1
    assert JobDescriptionAnalysis.model_validate_json(output.read_text()).schema_version == '2.0.0'


def test_extract_master_summary_emits_all_scope_labels_and_counters(tmp_path, monkeypatch, capsys):
    from docx import Document

    monkeypatch.setattr(cli_module, 'PROJECT_ROOT', tmp_path)
    document = Document()
    document.add_heading('1. Technical Profile', level=1)
    document.add_paragraph('Python backend engineer with experience.')
    table = document.add_table(rows=2, cols=2)
    table.rows[0].cells[0].text = 'Skill'
    table.rows[0].cells[1].text = 'Level'
    table.rows[1].cells[0].text = 'Python'
    table.rows[1].cells[1].text = 'Expert'
    header = document.sections[0].header
    header.paragraphs[0].text = 'Header Text'
    footer = document.sections[0].footer
    footer.paragraphs[0].text = 'Footer Text'

    source = tmp_path / 'summary_test.docx'
    builders.save(document, source)
    evidence_path = tmp_path / 'data/private/processed/candidate_evidence.json'
    snapshot_path = tmp_path / 'data/private/processed/document_snapshot.json'

    exit_code = cli_module.main([
        'extract-master',
        '--master', str(source),
        '--output', str(evidence_path),
        '--snapshot-output', str(snapshot_path),
    ])
    assert exit_code == 0
    captured = capsys.readouterr()
    stdout = captured.out

    assert 'Master Resume extraction summary' in stdout
    assert 'body_blocks:' in stdout
    assert 'body_paragraphs: 2' in stdout
    assert 'table_paragraphs: 4' in stdout
    assert 'header_paragraphs: 1' in stdout
    assert 'footer_paragraphs: 1' in stdout
    assert 'total_paragraphs: 8' in stdout
    assert 'tables: 1 rows: 2 cells: 4' in stdout
    assert 'empty_paragraphs: 0' in stdout
    assert 'words:' in stdout
    assert 'hyperlinks:' in stdout
    assert 'images: 0 drawings: 0 embedded_objects: 0 textboxes: 0' in stdout
    assert 'detected_sections: 1' in stdout
    assert 'evidence_records:' in stdout
    assert 'unclassified_blocks: 0' in stdout
    assert 'unsupported_content_items:' in stdout
    assert 'source_preservation_complete: True' in stdout
    assert 'section_classification_complete: True' in stdout
    assert 'complete: True' in stdout
    assert 'evidence_output:' in stdout
    assert 'snapshot_artifact:' in stdout
