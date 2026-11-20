'''Synthetic CLI, input-security, and integrity regressions for match-job.'''

import os
import socket
import stat
from unittest.mock import Mock

import pytest
from pydantic_core import PydanticSerializationError

from resume_assistant import cli as cli_module
from resume_assistant.job_models import JobDescriptionAnalysis
from resume_assistant.matching_models import CandidateJobMatch
from resume_assistant.models import CandidateEvidenceCatalog, EvidenceType
from resume_assistant import private_input, private_output
from tests.fixtures.jobs import grounded_job_payload, synthetic_candidate_catalog, synthetic_evidence


@pytest.fixture
def match_project(tmp_path, monkeypatch):
    monkeypatch.setattr(cli_module, 'PROJECT_ROOT', tmp_path)
    analysis = JobDescriptionAnalysis.model_validate({**grounded_job_payload(), 'prompt_version': '2.0.0'})
    catalog = synthetic_candidate_catalog([
        synthetic_evidence('ev-skill-py', EvidenceType.TECHNICAL_SKILL, 'Senior engineer proficient in Python and SQL.'),
    ])
    processed = tmp_path / 'data' / 'private' / 'processed'
    processed.mkdir(parents=True)
    analysis_path = processed / 'job_description_analysis.json'
    candidate_path = processed / 'candidate_evidence.json'
    output_path = processed / 'job_candidate_match.json'
    monkeypatch.setattr(cli_module, 'DEFAULT_ANALYSIS_OUTPUT_PATH', analysis_path)
    monkeypatch.setattr(cli_module, 'DEFAULT_EVIDENCE_OUTPUT_PATH', candidate_path)
    monkeypatch.setattr(cli_module, 'DEFAULT_JOB_MATCH_OUTPUT_PATH', output_path)
    analysis_path.write_text(analysis.model_dump_json(), encoding='utf-8')
    candidate_path.write_text(catalog.model_dump_json(), encoding='utf-8')
    return analysis_path, candidate_path, output_path


def _arguments(analysis_path, candidate_path, output_path):
    return [
        'match-job',
        '--analysis', str(analysis_path),
        '--candidate-evidence', str(candidate_path),
        '--output', str(output_path),
    ]


def test_match_job_success_writes_valid_artifact_and_aggregate_summary(match_project, capsys):
    analysis_path, candidate_path, output_path = match_project
    exit_code = cli_module.main(_arguments(analysis_path, candidate_path, output_path))
    captured = capsys.readouterr()
    assert exit_code == 0
    restored = CandidateJobMatch.model_validate_json(output_path.read_text(encoding='utf-8'))
    assert restored.source_job_schema_version == '2.0.0'
    assert restored.candidate_evidence_schema_version == '3.0.0'
    assert 'Candidate job match summary' in captured.out
    assert 'candidate_source_hash_present' in captured.out
    assert 'coverage_status' in captured.out
    assert 'human_review_required' in captured.out
    assert 'output:' in captured.out
    assert 'Senior engineer proficient in Python and SQL.' not in captured.out + captured.err
    assert 'a' * 64 not in captured.out + captured.err
    assert stat.S_IMODE(output_path.stat().st_mode) == 0o600


def test_match_job_missing_input_is_controlled(match_project, capsys):
    analysis_path, candidate_path, output_path = match_project
    missing = analysis_path.parent / 'missing_job_analysis.json'
    exit_code = cli_module.main(_arguments(missing, candidate_path, output_path))
    captured = capsys.readouterr()
    assert exit_code == 2
    assert 'could not be found' in captured.out
    assert not output_path.exists()


def test_match_job_invalid_output_path_is_controlled(match_project, capsys):
    analysis_path, candidate_path, output_path = match_project
    outside = analysis_path.parent.parent / 'outside' / 'job_candidate_match.json'
    outside.parent.mkdir(parents=True, exist_ok=True)
    exit_code = cli_module.main(_arguments(analysis_path, candidate_path, outside))
    captured = capsys.readouterr()
    assert exit_code == 2
    assert 'data/private/processed' in captured.out
    assert not outside.exists()


def test_match_job_output_aliasing_input_is_rejected(match_project, capsys):
    analysis_path, candidate_path, output_path = match_project
    before = candidate_path.read_bytes()
    exit_code = cli_module.main(_arguments(analysis_path, candidate_path, candidate_path))
    captured = capsys.readouterr()
    assert exit_code == 2
    assert 'must not alias' in captured.out
    assert candidate_path.read_bytes() == before


def test_match_job_incompatible_candidate_version_is_controlled(match_project, capsys):
    analysis_path, candidate_path, output_path = match_project
    catalog = CandidateEvidenceCatalog.model_validate_json(candidate_path.read_text(encoding='utf-8'))
    catalog.schema_version = '2.0.0'
    candidate_path.write_text(catalog.model_dump_json(), encoding='utf-8')
    exit_code = cli_module.main(_arguments(analysis_path, candidate_path, output_path))
    captured = capsys.readouterr()
    assert exit_code == 2
    assert 'schema' in captured.out
    assert not output_path.exists()


@pytest.mark.parametrize('artifact', ['analysis', 'candidate'])
def test_missing_matching_artifact_errors_identify_the_correct_input(match_project, monkeypatch, capsys, artifact):
    analysis, candidate, output = match_project
    before = (analysis.read_bytes(), candidate.read_bytes())
    missing = analysis.parent / 'SYNTHETIC_PRIVATE_PATH_missing.json'
    writer = Mock()
    monkeypatch.setattr(cli_module, '_atomic_write_json', writer)
    arguments = _arguments(missing if artifact == 'analysis' else analysis, missing if artifact == 'candidate' else candidate, output)
    assert cli_module.main(arguments) == 2
    captured = capsys.readouterr()
    expected = 'Job analysis artifact input' if artifact == 'analysis' else 'Candidate evidence artifact input'
    assert expected in captured.out
    assert 'Job description input' not in captured.out
    assert 'SYNTHETIC_PRIVATE_PATH' not in captured.out + captured.err
    writer.assert_not_called()
    assert (analysis.read_bytes(), candidate.read_bytes()) == before


@pytest.mark.parametrize('artifact', ['analysis', 'candidate'])
@pytest.mark.parametrize('invalid_json', [
    'SYNTHETIC_PRIVATE_JSON_CONTENT',
    '{"schema_version":"2.0.0","schema_version":"3.0.0"}',
    '{"schema_version":"3.0.0","invalid":NaN}',
])
def test_invalid_or_ambiguous_json_is_sanitized_and_preserves_prior_output(
    match_project, monkeypatch, capsys, artifact, invalid_json,
):
    analysis, candidate, output = match_project
    path = analysis if artifact == 'analysis' else candidate
    path.write_text(invalid_json, encoding='utf-8')
    output.write_bytes(b'SYNTHETIC_PREVIOUS_OUTPUT')
    writer = Mock()
    monkeypatch.setattr(cli_module, '_atomic_write_json', writer)
    assert cli_module.main(_arguments(analysis, candidate, output)) == 2
    captured = capsys.readouterr()
    assert 'valid, unambiguous JSON' in captured.out
    assert 'SYNTHETIC_PRIVATE_JSON_CONTENT' not in captured.out + captured.err
    writer.assert_not_called()
    assert output.read_bytes() == b'SYNTHETIC_PREVIOUS_OUTPUT'


@pytest.mark.parametrize('artifact, mutation', [
    ('analysis', 'old_version'), ('candidate', 'old_version'),
    ('analysis', 'missing_version'), ('candidate', 'missing_version'),
    ('analysis', 'private_version'), ('candidate', 'private_version'),
    ('analysis', 'old_prompt'),
])
def test_artifact_schema_and_prompt_compatibility_is_enforced_without_leaking_values(
    match_project, monkeypatch, capsys, artifact, mutation,
):
    analysis, candidate, output = match_project
    path = analysis if artifact == 'analysis' else candidate
    text = path.read_text(encoding='utf-8')
    current = '2.0.0' if artifact == 'analysis' else '3.0.0'
    field = f'"schema_version":"{current}"'
    assert field in text
    if mutation == 'missing_version':
        if artifact == 'analysis':
            text = text.replace('"schema_version":"2.0.0",', '', 1)
        else:
            text = text.replace('"schema_version":"3.0.0"', '"schema_version":"1.0.0"', 1)
    elif mutation == 'old_prompt':
        text = text.replace('"prompt_version":"2.0.0"', '"prompt_version":"1.0.0"', 1)
    else:
        version = 'SYNTHETIC_PRIVATE_VERSION' if mutation == 'private_version' else '1.0.0'
        text = text.replace(field, f'"schema_version":"{version}"', 1)
    path.write_text(text, encoding='utf-8')
    writer = Mock()
    monkeypatch.setattr(cli_module, '_atomic_write_json', writer)
    assert cli_module.main(_arguments(analysis, candidate, output)) == 2
    captured = capsys.readouterr()
    assert 'SYNTHETIC_PRIVATE_VERSION' not in captured.out + captured.err
    writer.assert_not_called()
    assert not output.exists()


def test_candidate_artifact_types_are_strict_and_coverage_contradictions_are_rejected(match_project, capsys):
    analysis, candidate, output = match_project
    original = candidate.read_text(encoding='utf-8')
    assert '"complete":true' in original
    for invalid in ('"complete":"true"', '"complete":false'):
        candidate.write_text(original.replace('"complete":true', invalid, 1), encoding='utf-8')
        assert cli_module.main(_arguments(analysis, candidate, output)) == 2
        captured = capsys.readouterr()
        assert 'artifact validation failed' in captured.out
        assert not output.exists()


@pytest.mark.parametrize('artifact', ['analysis', 'candidate'])
def test_input_file_symlinks_are_rejected_without_reading_the_referent(match_project, monkeypatch, capsys, artifact):
    analysis, candidate, output = match_project
    source = analysis if artifact == 'analysis' else candidate
    link = source.with_name('artifact-link.json')
    link.symlink_to(source)
    before = source.read_bytes()
    matcher = Mock()
    monkeypatch.setattr(cli_module, 'CandidateJobMatcher', matcher)
    assert cli_module.main(_arguments(link if artifact == 'analysis' else analysis, link if artifact == 'candidate' else candidate, output)) == 2
    captured = capsys.readouterr()
    assert 'not a symlink' in captured.out
    matcher.assert_not_called()
    assert link.is_symlink() and source.read_bytes() == before
    assert not output.exists()


@pytest.mark.parametrize('artifact', ['analysis', 'candidate'])
def test_input_parent_symlinks_are_rejected_by_the_actual_reader(match_project, tmp_path, monkeypatch, capsys, artifact):
    analysis, candidate, output = match_project
    source = analysis if artifact == 'analysis' else candidate
    incoming = tmp_path / 'incoming'
    incoming.mkdir()
    actual = incoming / source.name
    actual.write_bytes(source.read_bytes())
    link = tmp_path / 'incoming-link'
    link.symlink_to(incoming, target_is_directory=True)
    unsafe = link / source.name
    matcher = Mock()
    monkeypatch.setattr(cli_module, 'CandidateJobMatcher', matcher)
    assert cli_module.main(_arguments(unsafe if artifact == 'analysis' else analysis, unsafe if artifact == 'candidate' else candidate, output)) == 2
    captured = capsys.readouterr()
    expected = 'Job analysis artifact input' if artifact == 'analysis' else 'Candidate evidence artifact input'
    assert expected in captured.out
    matcher.assert_not_called()
    assert not output.exists()
    assert actual.read_bytes() == source.read_bytes()


@pytest.mark.parametrize('unsafe_kind', ['traversal', 'url', 'nul', 'directory', 'fifo', 'non_utf8'])
def test_unsafe_or_nonregular_candidate_input_is_controlled(match_project, monkeypatch, capsys, unsafe_kind):
    analysis, candidate, output = match_project
    if unsafe_kind == 'traversal':
        unsafe = candidate.parent / '..' / 'nonexistent.txt'
    elif unsafe_kind == 'url':
        unsafe = 'https://SYNTHETIC_PRIVATE_HOST/candidate.json'
    elif unsafe_kind == 'nul':
        unsafe = str(candidate) + '\x00SYNTHETIC_PRIVATE_NUL'
    elif unsafe_kind == 'directory':
        unsafe = candidate.parent
    elif unsafe_kind == 'fifo':
        unsafe = candidate.with_name('candidate-fifo')
        os.mkfifo(unsafe)
    else:
        unsafe = candidate.with_name('candidate-invalid-utf8.json')
        unsafe.write_bytes(b'\xff\xfeSYNTHETIC_PRIVATE_CONTENT')
    assert cli_module.main(_arguments(analysis, unsafe, output)) == 2
    captured = capsys.readouterr()
    assert 'Candidate evidence artifact input' in captured.out
    assert 'SYNTHETIC_PRIVATE_' not in captured.out + captured.err
    assert not output.exists()


def test_valid_local_input_files_may_be_outside_the_private_output_directory(match_project, tmp_path):
    analysis, candidate, output = match_project
    local_analysis, local_candidate = tmp_path / 'analysis.json', tmp_path / 'catalog.json'
    local_analysis.write_bytes(analysis.read_bytes())
    local_candidate.write_bytes(candidate.read_bytes())
    assert cli_module.main(_arguments(local_analysis, local_candidate, output)) == 0
    assert CandidateJobMatch.model_validate_json(output.read_text()).schema_version == '2.0.0'


def test_input_directory_swap_is_detected_without_following_the_new_symlink(match_project, tmp_path, monkeypatch, capsys):
    analysis, candidate, output = match_project
    incoming = tmp_path / 'incoming'
    incoming.mkdir()
    source = incoming / 'candidate.json'
    source.write_bytes(candidate.read_bytes())
    outside = tmp_path / 'outside'
    outside.mkdir()
    outside_artifact = outside / 'candidate.json'
    outside_artifact.write_bytes(b'SYNTHETIC_REFERENT_MUST_NOT_BE_READ')
    original = tmp_path / 'incoming-original'
    real_check = private_input._check_directories
    swapped = []

    def check_then_swap(links, label):
        real_check(links, label)
        if label.startswith('Candidate evidence') and not swapped:
            incoming.rename(original)
            incoming.symlink_to(outside, target_is_directory=True)
            swapped.append(True)

    monkeypatch.setattr(private_input, '_check_directories', check_then_swap)
    assert cli_module.main(_arguments(analysis, source, output)) == 2
    captured = capsys.readouterr()
    assert swapped == [True]
    assert 'directory identity changed' in captured.out or 'could not be found' in captured.out or 'accessible real directories' in captured.out
    assert not output.exists()
    assert outside_artifact.read_bytes() == b'SYNTHETIC_REFERENT_MUST_NOT_BE_READ'
    assert (original / 'candidate.json').read_bytes() == candidate.read_bytes()


@pytest.mark.parametrize('artifact', ['analysis', 'candidate'])
def test_hardlinked_output_aliases_are_rejected_without_modifying_inputs(match_project, capsys, artifact):
    analysis, candidate, output = match_project
    source = analysis if artifact == 'analysis' else candidate
    before = source.read_bytes()
    os.link(source, output)
    assert cli_module.main(_arguments(analysis, candidate, output)) == 2
    assert 'must not alias' in capsys.readouterr().out
    assert source.read_bytes() == before and output.read_bytes() == before


def test_output_symlink_is_rejected_before_artifact_reads(match_project, tmp_path, monkeypatch, capsys):
    analysis, candidate, output = match_project
    outside = tmp_path / 'outside-output.json'
    outside.write_bytes(b'SYNTHETIC_PREVIOUS_OUTPUT')
    output.symlink_to(outside)
    loader = Mock()
    monkeypatch.setattr(cli_module, '_load_job_analysis_artifact', loader)
    assert cli_module.main(_arguments(analysis, candidate, output)) == 2
    assert 'not a symlink' in capsys.readouterr().out
    loader.assert_not_called()
    assert outside.read_bytes() == b'SYNTHETIC_PREVIOUS_OUTPUT'


def test_match_output_parent_is_pinned_before_matching_and_swaps_are_rejected(match_project, tmp_path, monkeypatch, capsys):
    analysis, candidate, output = match_project
    output.write_bytes(b'SYNTHETIC_PREVIOUS_OUTPUT')
    before = {path.name: path.read_bytes() for path in (analysis, candidate, output)}
    processed = output.parent
    original = processed.with_name('processed-original')
    outside = tmp_path / 'outside'
    outside.mkdir()
    real_assess = cli_module.CandidateJobMatcher.assess

    def assess_then_swap(self, source):
        result = real_assess(self, source)
        processed.rename(original)
        processed.symlink_to(outside, target_is_directory=True)
        return result

    monkeypatch.setattr(cli_module.CandidateJobMatcher, 'assess', assess_then_swap)
    assert cli_module.main(_arguments(analysis, candidate, output)) == 2
    assert 'identity changed' in capsys.readouterr().out
    assert list(outside.iterdir()) == []
    assert {name: (original / name).read_bytes() for name in before} == before
    assert not list(original.glob('.*.tmp'))


@pytest.mark.parametrize('tamper', ['unknown_evidence', 'wrong_requirement', 'wrong_provenance'])
def test_cross_source_validation_is_reached_before_writing_a_tampered_match(
    match_project, monkeypatch, capsys, tamper,
):
    analysis, candidate, output = match_project
    output.write_bytes(b'SYNTHETIC_PREVIOUS_OUTPUT')
    real_assess = cli_module.CandidateJobMatcher.assess
    calls = []

    def tampered_assess(self, source):
        data = real_assess(self, source).model_dump()
        item = data['requirements'][0]
        if tamper == 'unknown_evidence':
            item['matched_evidence_ids'] = ['unknown-id']
            item['matched_evidence'][0]['evidence_id'] = 'unknown-id'
            item['components'][0]['matched_evidence_ids'] = ['unknown-id']
        elif tamper == 'wrong_requirement':
            item['source_index'] = 999
        else:
            item['matched_evidence'][0]['source_sha256'] = 'b' * 64
        result = CandidateJobMatch.model_validate(data)
        calls.append(result)
        return result

    writer = Mock()
    monkeypatch.setattr(cli_module.CandidateJobMatcher, 'assess', tampered_assess)
    monkeypatch.setattr(cli_module, '_atomic_write_json', writer)
    assert cli_module.main(_arguments(analysis, candidate, output)) == 2
    assert len(calls) == 1
    assert 'error:' in capsys.readouterr().out
    writer.assert_not_called()
    assert output.read_bytes() == b'SYNTHETIC_PREVIOUS_OUTPUT'


def test_actual_round_trip_validation_failure_is_sanitized_and_preserves_prior_output(match_project, monkeypatch, capsys):
    analysis, candidate, output = match_project
    output.write_bytes(b'SYNTHETIC_PREVIOUS_OUTPUT')
    real_validate = CandidateJobMatch.model_validate_json
    corrupted = []

    def corrupt_pretty_payload(payload, **kwargs):
        if '\n' in payload:
            changed = payload.replace('"status": "supported"', '"status": "SYNTHETIC_INVALID_STATUS"', 1)
            assert changed != payload
            corrupted.append(True)
            return real_validate(changed, **kwargs)
        return real_validate(payload, **kwargs)

    writer = Mock()
    monkeypatch.setattr(CandidateJobMatch, 'model_validate_json', corrupt_pretty_payload)
    monkeypatch.setattr(cli_module, '_atomic_write_json', writer)
    assert cli_module.main(_arguments(analysis, candidate, output)) == 2
    captured = capsys.readouterr()
    assert corrupted == [True]
    assert 'artifact validation failed' in captured.out
    assert 'SYNTHETIC_INVALID_STATUS' not in captured.out + captured.err
    writer.assert_not_called()
    assert output.read_bytes() == b'SYNTHETIC_PREVIOUS_OUTPUT'


def test_serialization_failure_does_not_print_sensitive_pydantic_warning_values(match_project, monkeypatch, capsys):
    analysis, candidate, output = match_project
    real_assess = cli_module.CandidateJobMatcher.assess
    failures = []

    def bad_type_result(self, source):
        result = real_assess(self, source).model_copy(update={'candidate_source_sha256': ['SYNTHETIC_PRIVATE_VALUE']})
        with pytest.raises(PydanticSerializationError):
            result.model_dump_json(warnings='error')
        failures.append(True)
        return result

    monkeypatch.setattr(cli_module.CandidateJobMatcher, 'assess', bad_type_result)
    assert cli_module.main(_arguments(analysis, candidate, output)) == 2
    captured = capsys.readouterr()
    assert failures == [True]
    assert 'artifact serialization failed' in captured.out
    assert 'SYNTHETIC_PRIVATE_VALUE' not in captured.out + captured.err
    assert not output.exists()


def test_failed_atomic_replace_preserves_existing_match_and_cleans_temporary_files(match_project, monkeypatch, capsys):
    analysis, candidate, output = match_project
    output.write_bytes(b'SYNTHETIC_PREVIOUS_OUTPUT')
    before = (analysis.read_bytes(), candidate.read_bytes(), output.read_bytes())
    replace = Mock(side_effect=OSError('SYNTHETIC_PRIVATE_IO_ERROR'))
    monkeypatch.setattr('resume_assistant.publication_store.os.replace', replace)
    assert cli_module.main(_arguments(analysis, candidate, output)) == 3
    captured = capsys.readouterr()
    replace.assert_called_once()
    assert 'job match failed: OSError' in captured.out
    assert 'SYNTHETIC_PRIVATE_IO_ERROR' not in captured.out + captured.err
    assert (analysis.read_bytes(), candidate.read_bytes(), output.read_bytes()) == before
    assert not list(output.parent.glob('.*.tmp'))


def test_match_job_is_offline_even_with_provider_configuration_present(match_project, monkeypatch):
    analysis, candidate, output = match_project
    provider = Mock(side_effect=AssertionError('A provider must not be constructed'))
    network = Mock(side_effect=AssertionError('Matching must not use network connections'))
    monkeypatch.setenv('AGENTROUTER_API_KEY', 'synthetic-not-used')
    monkeypatch.setattr(cli_module, 'AgentRouterClient', provider)
    monkeypatch.setattr(socket.socket, 'connect', network)
    assert cli_module.main(_arguments(analysis, candidate, output)) == 0
    provider.assert_not_called()
    network.assert_not_called()


def test_cli_summary_never_prints_candidate_content_names_hashes_or_complete_records(match_project, capsys):
    analysis, candidate, output = match_project
    catalog = CandidateEvidenceCatalog.model_validate_json(candidate.read_text())
    catalog.source_document.filename = 'SYNTHETIC_PRIVATE_FILENAME.docx'
    catalog.evidence_records[0].source_document = 'SYNTHETIC_PRIVATE_FILENAME.docx'
    catalog.evidence_records[0].original_content = 'Python SYNTHETIC_PRIVATE_EVIDENCE_TEXT'
    candidate.write_text(catalog.model_dump_json(), encoding='utf-8')
    assert cli_module.main(_arguments(analysis, candidate, output)) == 0
    captured = capsys.readouterr()
    combined = captured.out + captured.err
    for value in ('SYNTHETIC_PRIVATE_FILENAME', 'SYNTHETIC_PRIVATE_EVIDENCE_TEXT', 'a' * 64, 'Python', 'Acme Corp'):
        assert value not in combined
    assert 'matched_evidence' not in combined
    assert 'Candidate job match summary' in combined


def test_match_job_uses_documented_private_defaults_without_accessing_real_artifacts(match_project):
    _, _, output = match_project
    assert cli_module.main(['match-job']) == 0
    assert CandidateJobMatch.model_validate_json(output.read_text()).schema_version == '2.0.0'
