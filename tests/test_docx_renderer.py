'''Offline regressions for headless Writer startup and private PDF publication.'''

import stat
import subprocess
from pathlib import Path
from urllib.parse import unquote, urlsplit

import pytest

from resume_assistant import docx_renderer


def test_first_export_owns_its_writer_profile(tmp_path, monkeypatch):
    # Model two Writer processes racing to start on the default profile: the
    # contender exits 1 without converting. A second click would mask the bug.
    source = tmp_path / 'with spaces' / 'JaneDoe_AI_Engineer.docx'
    source.parent.mkdir()
    source.write_bytes(b'synthetic DOCX')
    profiles = []

    def cold_writer(command, **kwargs):
        option = next((arg for arg in command if arg.startswith('-env:UserInstallation=')), None)
        if option is None:
            return subprocess.CompletedProcess(command, 1)
        uri = urlsplit(option.split('=', 1)[1])
        profile = Path(unquote(uri.path))
        assert uri.scheme == 'file' and not uri.netloc and ' ' not in option
        assert profile.parent.parent == source.parent
        assert stat.S_IMODE(profile.parent.stat().st_mode) == 0o700
        profiles.append(profile)
        outdir = Path(command[command.index('--outdir') + 1])
        (outdir / source.with_suffix('.pdf').name).write_bytes(b'%PDF-1.4\n%%EOF\n')
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(subprocess, 'run', cold_writer)
    for _ in range(2):  # sequential exports, e.g. a compact-tier re-render
        pdf = docx_renderer.render_resume_pdf(None, source.with_suffix('.pdf'), docx_path=source,
                                              converter_bin='offline-writer')
        assert stat.S_IMODE(pdf.stat().st_mode) == 0o600
    assert len(profiles) == len(set(profiles)) == 2
    assert all(not path.parent.exists() for path in profiles)


@pytest.mark.parametrize('failure', ['timeout', 'exit', 'missing', 'invalid-pdf'])
def test_pdf_failure_preserves_previous_output_without_replaying(tmp_path, monkeypatch, failure):
    source = tmp_path / 'source.docx'
    source.write_bytes(b'synthetic DOCX')
    target = tmp_path / 'resume.pdf'
    target.write_bytes(b'previous')
    calls = []

    def broken_writer(command, **kwargs):
        calls.append(command)
        if failure == 'timeout':
            raise subprocess.TimeoutExpired(command, timeout=60, stderr=b'synthetic-private-marker')
        if failure == 'invalid-pdf':
            outdir = Path(command[command.index('--outdir') + 1])
            (outdir / 'source.pdf').write_bytes(b'synthetic-private-marker')
        return subprocess.CompletedProcess(command, int(failure == 'exit'), stderr=b'synthetic-private-marker')

    monkeypatch.setattr(subprocess, 'run', broken_writer)
    with pytest.raises(docx_renderer.PdfRenderError, match='LibreOffice') as caught:
        docx_renderer.render_resume_pdf(None, target, docx_path=source, converter_bin='offline-writer')
    assert 'synthetic-private-marker' not in str(caught.value)
    assert len(calls) == 1
    assert target.read_bytes() == b'previous'
    assert not list(tmp_path.glob('.resume-pdf-*'))
