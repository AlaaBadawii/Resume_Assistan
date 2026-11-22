'''Filesystem security regressions for both CLI commands, using temporary fixtures.'''

import json
import os
import stat
from pathlib import Path
from unittest.mock import Mock

import pytest

from resume_assistant import cli, private_output
from resume_assistant.models import CandidateEvidenceCatalog
from resume_assistant.private_output import PrivateOutput, PrivateOutputError
from resume_assistant.publication_reader import load_committed_pair
from tests.fixtures import builders
from tests.fixtures.jobs import SYNTHETIC_JOB_TEXT, empty_job_payload


@pytest.fixture
def private_project(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, 'PROJECT_ROOT', tmp_path)
    monkeypatch.setenv('AGENTROUTER_API_KEY', 'synthetic-test-token')
    monkeypatch.setattr(cli.AgentRouterClient, 'generate', Mock(return_value=json.dumps(empty_job_payload())))
    text = tmp_path / 'job.txt'
    text.write_text(SYNTHETIC_JOB_TEXT, encoding='utf-8')
    docx = builders.build_interleaved_document(tmp_path / 'master.docx')
    processed = tmp_path / 'data/private/processed'
    processed.mkdir(parents=True)
    evidence = processed / 'candidate_evidence.json'
    snapshot = processed / 'document_snapshot.json'
    analysis = processed / 'job_analysis.json'
    for path in (evidence, snapshot, analysis):
        path.write_bytes(b'SYNTHETIC_PREVIOUS_ARTIFACT')
    return text, docx, processed, evidence, snapshot, analysis


def _args(project, command, output=None):
    text, docx, _, evidence, snapshot, analysis = project
    if command == 'analyze-job':
        return ['analyze-job', '--input', str(text), '--output', str(output or analysis)]
    return [
        'extract-master', '--master', str(docx), '--output', str(output or evidence),
        '--snapshot-output', str(snapshot),
    ]


@pytest.mark.parametrize('command', ['analyze-job', 'extract-master'])
@pytest.mark.parametrize('component', ['data', 'private', 'processed'])
def test_symlinked_private_components_rejected_before_processing(
    private_project, tmp_path, monkeypatch, capsys, command, component,
):
    text, docx, processed, evidence, snapshot, analysis = private_project
    before = {path: path.read_bytes() for path in (text, docx, evidence, snapshot, analysis)}
    directory = {'data': processed.parents[1], 'private': processed.parent, 'processed': processed}[component]
    original = directory.with_name(directory.name + '-original')
    directory.rename(original)
    outside = tmp_path / 'outside'
    outside.mkdir()
    directory.symlink_to(outside, target_is_directory=True)
    generate, extract = Mock(), Mock()
    monkeypatch.setattr(cli.AgentRouterClient, 'generate', generate)
    monkeypatch.setattr(cli.extractor_module, 'MasterResumeExtractor', extract)

    assert cli.main(_args(private_project, command)) == 2
    captured = capsys.readouterr()
    assert 'not symlinks' in captured.out
    generate.assert_not_called()
    extract.assert_not_called()
    assert list(outside.iterdir()) == []
    assert text.read_bytes() == before[text] and docx.read_bytes() == before[docx]
    for old in (evidence, snapshot, analysis):
        relocated = original / old.relative_to(directory)
        assert relocated.read_bytes() == before[old]
    assert cli._is_private_output(evidence) is False
    assert cli._is_private_output(outside / 'direct.json') is False


@pytest.mark.parametrize('command', ['analyze-job', 'extract-master'])
def test_parent_swapped_during_processing_is_rejected_and_preserves_artifacts(
    private_project, tmp_path, monkeypatch, capsys, command,
):
    text, docx, processed, evidence, snapshot, analysis = private_project
    before = {path: path.read_bytes() for path in (text, docx, evidence, snapshot, analysis)}
    outside = tmp_path / 'outside'
    outside.mkdir()
    original = processed.with_name('processed-original')

    def swap():
        processed.rename(original)
        processed.symlink_to(outside, target_is_directory=True)

    if command == 'analyze-job':
        def generate(self, **kwargs):
            swap()
            return json.dumps(empty_job_payload())
        monkeypatch.setattr(cli.AgentRouterClient, 'generate', generate)
    else:
        build = cli.evidence_module.build_evidence_catalog
        def catalog(*args, **kwargs):
            result = build(*args, **kwargs)
            swap()
            return result
        monkeypatch.setattr(cli.evidence_module, 'build_evidence_catalog', catalog)

    assert cli.main(_args(private_project, command)) == 2
    assert 'identity changed' in capsys.readouterr().out
    assert list(outside.iterdir()) == []
    for path in (evidence, snapshot, analysis):
        assert (original / path.name).read_bytes() == before[path]
    assert text.read_bytes() == before[text] and docx.read_bytes() == before[docx]
    assert not list(original.glob('.*.tmp'))


@pytest.mark.parametrize('command', ['analyze-job', 'extract-master'])
def test_destination_symlink_rejected_without_touching_referent(
    private_project, tmp_path, capsys, command,
):
    outside = tmp_path / 'outside.json'
    outside.write_bytes(b'SYNTHETIC_EXTERNAL_ARTIFACT')
    link = private_project[2] / 'destination-link.json'
    link.symlink_to(outside)
    assert cli.main(_args(private_project, command, output=link)) == 2
    assert outside.read_bytes() == b'SYNTHETIC_EXTERNAL_ARTIFACT'
    assert link.is_symlink()
    assert 'not a symlink' in capsys.readouterr().out


@pytest.mark.parametrize('command', ['analyze-job', 'extract-master'])
def test_valid_private_writes_preserve_sources(private_project, command):
    sources = private_project[:2]
    before = {path: path.read_bytes() for path in sources}
    assert cli.main(_args(private_project, command)) == 0
    if command == 'extract-master':
        _, catalog, _, _ = load_committed_pair(
            cli.PROJECT_ROOT, private_project[3]
        )
        assert isinstance(catalog, CandidateEvidenceCatalog)
        assert stat.S_IMODE(private_project[3].stat().st_mode) == 0o600
    else:
        assert isinstance(json.loads(private_project[5].read_text(encoding='utf-8')), dict)
        assert stat.S_IMODE(private_project[5].stat().st_mode) == 0o600
    assert {path: path.read_bytes() for path in sources} == before


def test_temporary_creation_does_not_follow_an_existing_symlink(tmp_path, monkeypatch):
    target = tmp_path / 'data/private/processed/analysis.json'
    target.parent.mkdir(parents=True)
    source = tmp_path / 'source.txt'
    source.write_bytes(b'SYNTHETIC_SOURCE')
    poisoned = target.parent / '.resume-assistant-collision.tmp'
    poisoned.symlink_to(source)
    tokens = iter(('collision', 'exclusive'))
    monkeypatch.setattr(private_output.secrets, 'token_hex', lambda size: next(tokens))
    with PrivateOutput(tmp_path, target) as destination:
        destination.write_json('{"synthetic": true}')
    assert source.read_bytes() == b'SYNTHETIC_SOURCE'
    assert poisoned.is_symlink()
    assert json.loads(target.read_text()) == {'synthetic': True}
    assert not (target.parent / '.resume-assistant-exclusive.tmp').exists()


def test_descriptor_relative_replace_cannot_follow_a_last_moment_parent_swap(tmp_path, monkeypatch):
    parent = tmp_path / 'data/private/processed'
    parent.mkdir(parents=True)
    target = parent / 'analysis.json'
    outside = tmp_path / 'outside'
    outside.mkdir()
    original = parent.with_name('original')
    replace = os.replace
    calls = []

    def swap_at_replace(source, destination, *, src_dir_fd, dst_dir_fd):
        calls.append((source, destination, src_dir_fd, dst_dir_fd))
        parent.rename(original)
        parent.symlink_to(outside, target_is_directory=True)
        return replace(source, destination, src_dir_fd=src_dir_fd, dst_dir_fd=dst_dir_fd)

    monkeypatch.setattr(private_output.os, 'replace', swap_at_replace)
    with PrivateOutput(tmp_path, target) as destination:
        destination.write_json('{"synthetic": true}')
    assert len(calls) == 1
    assert calls[0][2] == calls[0][3]
    assert Path(calls[0][0]).parent == Path('.')
    assert Path(calls[0][1]).parent == Path('.')
    assert list(outside.iterdir()) == []
    assert json.loads((original / 'analysis.json').read_text()) == {'synthetic': True}


def test_parent_swap_at_temporary_creation_is_rejected_without_modifying_existing_output(tmp_path, monkeypatch):
    parent = tmp_path / 'data/private/processed'
    parent.mkdir(parents=True)
    target = parent / 'analysis.json'
    target.write_bytes(b'SYNTHETIC_PREVIOUS_ARTIFACT')
    outside = tmp_path / 'outside'
    outside.mkdir()
    original = parent.with_name('original')
    real_open = os.open

    def swap_at_temporary_open(name, flags, mode=0o777, *, dir_fd=None):
        fd = real_open(name, flags, mode, dir_fd=dir_fd)
        if str(name).startswith('.resume-assistant-'):
            assert flags & os.O_EXCL and flags & os.O_NOFOLLOW
            parent.rename(original)
            parent.symlink_to(outside, target_is_directory=True)
        return fd

    with PrivateOutput(tmp_path, target) as destination:
        monkeypatch.setattr(private_output.os, 'open', swap_at_temporary_open)
        with pytest.raises(PrivateOutputError, match='identity changed'):
            destination.write_json('{"synthetic": true}')
    assert list(outside.iterdir()) == []
    assert (original / 'analysis.json').read_bytes() == b'SYNTHETIC_PREVIOUS_ARTIFACT'
    assert not list(original.glob('.*.tmp'))


@pytest.mark.parametrize('command', ['analyze-job', 'extract-master'])
def test_hardlinked_source_output_alias_is_rejected_before_processing(
    private_project, monkeypatch, capsys, command,
):
    source = private_project[0] if command == 'analyze-job' else private_project[1]
    before = source.read_bytes()
    link = private_project[2] / 'source-hardlink.json'
    os.link(source, link)
    generate, extract = Mock(), Mock()
    monkeypatch.setattr(cli.AgentRouterClient, 'generate', generate)
    monkeypatch.setattr(cli.extractor_module, 'MasterResumeExtractor', extract)
    assert cli.main(_args(private_project, command, output=link)) == 2
    assert 'alias' in capsys.readouterr().out
    generate.assert_not_called()
    extract.assert_not_called()
    assert source.read_bytes() == before and link.read_bytes() == before


def test_symlinked_project_root_is_not_a_trusted_output_anchor(tmp_path):
    root = tmp_path / 'real-project'
    root.mkdir()
    link = tmp_path / 'project-link'
    link.symlink_to(root, target_is_directory=True)
    with pytest.raises(PrivateOutputError):
        with PrivateOutput(link, link / 'data/private/processed/analysis.json'):
            pytest.fail('A symlinked project root must not be opened as the anchor')
    assert not (root / 'data').exists()


@pytest.mark.parametrize('command', ['analyze-job', 'extract-master'])
def test_unsupported_platform_fails_closed(private_project, monkeypatch, capsys, command):
    before = {path: path.read_bytes() for path in private_project if path.is_file()}
    monkeypatch.setattr(private_output.os, 'supports_dir_fd', set())
    assert cli.main(_args(private_project, command)) == 2
    assert 'requires POSIX' in capsys.readouterr().out
    assert {path: path.read_bytes() for path in before} == before


def test_private_output_check_does_not_create_missing_directories(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, 'PROJECT_ROOT', tmp_path)
    target = tmp_path / 'data/private/processed/nested/artifact.json'
    assert cli._is_private_output(target) is True
    assert not (tmp_path / 'data').exists()


@pytest.mark.parametrize('input_kind', ['directory', 'missing'])
def test_invalid_job_input_is_controlled_before_client_call(
    private_project, monkeypatch, capsys, input_kind,
):
    text, _, processed, *_ = private_project
    source = text.parent if input_kind == 'directory' else text.parent / 'missing.txt'
    generate = Mock()
    monkeypatch.setattr(cli.AgentRouterClient, 'generate', generate)
    result = cli.main(['analyze-job', '--input', str(source), '--output', str(processed / 'new.json')])
    assert result == 2
    generate.assert_not_called()
    expected = 'regular file' if input_kind == 'directory' else 'could not be found'
    assert expected in capsys.readouterr().out
    assert not (processed / 'new.json').exists()
