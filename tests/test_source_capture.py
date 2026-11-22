'''Synthetic captured-byte input and parser/hash binding regressions.'''

import hashlib
import os
import zipfile
from io import BytesIO
from unittest.mock import Mock

import pytest
from docx import Document
from docx.oxml import parse_xml

from resume_assistant import cli, evidence, extractor, private_input
from resume_assistant.extractor import InvalidMasterResumeError, MasterResumeExtractor
from resume_assistant.private_input import PrivateInputError, capture_docx_source


def _source(path, text='SYNTHETIC_CAPTURE_A'):
    document = Document()
    document.add_heading('1. Technical Profile', level=1)
    document.add_paragraph(text)
    document.save(path)
    return path


def test_safe_capture_preserves_filename_digest_and_source_bytes(tmp_path):
    source = _source(tmp_path / 'synthetic-capture.docx')
    before = source.read_bytes()
    captured = capture_docx_source(source)
    assert captured.filename == source.name
    assert captured.data == before
    assert captured.sha256 == hashlib.sha256(before).hexdigest()
    assert source.read_bytes() == before
    assert 'SYNTHETIC_CAPTURE_A' not in repr(captured)
    with pytest.raises(AttributeError):
        captured.data = b'other input'


def test_snapshot_catalog_and_parser_share_one_captured_byte_input(tmp_path, monkeypatch):
    source = _source(tmp_path / 'parser-input.docx')
    before = source.read_bytes()
    parser_inputs = []
    real_parser = extractor.Document

    def parse(stream):
        assert isinstance(stream, BytesIO)
        parser_inputs.append(stream.getvalue())
        return real_parser(stream)

    capture = Mock(wraps=private_input.capture_docx_source)
    monkeypatch.setattr(extractor, 'capture_docx_source', capture)
    monkeypatch.setattr(extractor, 'Document', parse)
    monkeypatch.setattr(extractor, 'compute_sha256', Mock(side_effect=AssertionError('No pathname hashing')))
    snapshot = MasterResumeExtractor(source).extract()
    catalog = evidence.build_evidence_catalog(snapshot)
    assert parser_inputs == [before]
    capture.assert_called_once_with(source)
    expected = hashlib.sha256(parser_inputs[0]).hexdigest()
    assert snapshot.source_sha256 == catalog.source_document.sha256 == expected
    assert all(record.source_sha256 == expected for record in catalog.evidence_records)
    assert source.read_bytes() == before


def test_aba_path_replacement_cannot_substitute_parser_input(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, 'PROJECT_ROOT', tmp_path)
    source = _source(tmp_path / 'aba.docx')
    replacement = _source(tmp_path / 'replacement.docx', 'SYNTHETIC_CAPTURE_B')
    original, other = source.read_bytes(), replacement.read_bytes()
    real_parser = extractor.Document
    parser_inputs = []

    def swap_and_parse(stream):
        parser_inputs.append(stream.getvalue())
        source.write_bytes(other)
        try:
            return real_parser(stream)
        finally:
            source.write_bytes(original)

    monkeypatch.setattr(extractor, 'Document', swap_and_parse)
    catalog, snapshot, *_ = cli.run_extract_master(
        source, tmp_path / 'data/private/processed/evidence.json',
        tmp_path / 'data/private/processed/snapshot.json',
    )
    assert parser_inputs == [original]
    assert snapshot.blocks[1].paragraph.text == 'SYNTHETIC_CAPTURE_A'
    assert not any('SYNTHETIC_CAPTURE_B' in record.original_content for record in catalog.evidence_records)
    assert snapshot.source_sha256 == catalog.source_document.sha256 == hashlib.sha256(original).hexdigest()
    assert source.read_bytes() == original


def test_source_changed_after_capture_does_not_change_logical_input(tmp_path, monkeypatch):
    source = _source(tmp_path / 'changed-after-capture.docx')
    replacement = _source(tmp_path / 'other.docx', 'SYNTHETIC_CAPTURE_B')
    original, other = source.read_bytes(), replacement.read_bytes()
    real_capture = extractor.capture_docx_source
    real_parser = extractor.Document
    parsed = []

    def capture_then_change(path):
        captured = real_capture(path)
        source.write_bytes(other)
        return captured

    def parse(stream):
        parsed.append(stream.getvalue())
        return real_parser(stream)

    monkeypatch.setattr(extractor, 'capture_docx_source', capture_then_change)
    monkeypatch.setattr(extractor, 'Document', parse)
    snapshot = MasterResumeExtractor(source).extract()
    assert parsed == [original]
    assert snapshot.blocks[1].paragraph.text == 'SYNTHETIC_CAPTURE_A'
    assert snapshot.source_sha256 == hashlib.sha256(original).hexdigest()
    assert source.read_bytes() == other  # only the simulated external editor changed it


@pytest.mark.parametrize('bad_bytes', [
    b'', b'SYNTHETIC_PRIVATE_BAD_DOCX', b'PK\x03\x04SYNTHETIC_PRIVATE_TRUNCATED_ZIP',
])
def test_invalid_captured_docx_is_controlled_and_sanitized(tmp_path, monkeypatch, capsys, bad_bytes):
    monkeypatch.setattr(cli, 'PROJECT_ROOT', tmp_path)
    source = tmp_path / 'invalid.docx'
    source.write_bytes(bad_bytes)
    with pytest.raises(InvalidMasterResumeError) as failure:
        MasterResumeExtractor(source).extract()
    assert 'SYNTHETIC_PRIVATE' not in str(failure.value)
    writer = Mock()
    monkeypatch.setattr(cli, '_atomic_write_json', writer)
    assert cli.main([
        'extract-master', '--master', str(source),
        '--output', str(tmp_path / 'data/private/processed/evidence.json'),
        '--snapshot-output', str(tmp_path / 'data/private/processed/snapshot.json'),
    ]) == 2
    captured = capsys.readouterr()
    assert 'SYNTHETIC_PRIVATE' not in captured.out + captured.err
    writer.assert_not_called()
    assert source.read_bytes() == bad_bytes


def test_capture_symlink_and_nonregular_inputs_fail_before_reading(tmp_path):
    source = _source(tmp_path / 'regular.docx')
    link = tmp_path / 'linked.docx'
    link.symlink_to(source)
    with pytest.raises(PrivateInputError, match='not a symlink'):
        capture_docx_source(link)
    with pytest.raises(PrivateInputError, match='regular file'):
        capture_docx_source(tmp_path)


def test_capture_directory_swap_never_reads_new_symlink_referent(tmp_path, monkeypatch):
    incoming = tmp_path / 'incoming'
    incoming.mkdir()
    source = _source(incoming / 'source.docx')
    original = source.read_bytes()
    outside = tmp_path / 'outside'
    outside.mkdir()
    _source(outside / 'source.docx', 'SYNTHETIC_CAPTURE_B')
    previous = tmp_path / 'previous'
    real_check = private_input._check_directories
    swapped = []

    def check_then_swap(links, label):
        real_check(links, label)
        if not swapped:
            incoming.rename(previous)
            incoming.symlink_to(outside, target_is_directory=True)
            swapped.append(True)

    monkeypatch.setattr(private_input, '_check_directories', check_then_swap)
    with pytest.raises(PrivateInputError, match='directory identity changed'):
        capture_docx_source(source)
    assert swapped == [True]
    assert (previous / 'source.docx').read_bytes() == original


def test_numbering_reader_uses_the_same_capture_when_path_changes_after_capture(tmp_path, monkeypatch):
    from tests.fixtures.builders import NUMBERING_OVERRIDE_XML

    source, replacement = tmp_path / 'numbering-a.docx', tmp_path / 'numbering-b.docx'
    for path, fmt in [(source, 'lowerLetter'), (replacement, 'upperRoman')]:
        document = Document()
        document.add_heading('1. Technical Profile', level=1)
        paragraph = document.add_paragraph('SYNTHETIC_NUMBERED_EVIDENCE')
        properties = paragraph._p.get_or_add_pPr().get_or_add_numPr()
        properties.get_or_add_numId().val = 1
        properties.get_or_add_ilvl().val = 0
        document.part.numbering_part._element = parse_xml(NUMBERING_OVERRIDE_XML.replace('lowerLetter', fmt).encode())
        document.save(path)
    original, other = source.read_bytes(), replacement.read_bytes()
    real_capture = extractor.capture_docx_source

    def capture_then_replace(path):
        captured = real_capture(path)
        source.write_bytes(other)
        return captured

    monkeypatch.setattr(extractor, 'capture_docx_source', capture_then_replace)
    worker = MasterResumeExtractor(source)
    snapshot = worker.extract()
    assert snapshot.blocks[1].paragraph.numbering_format == 'lowerLetter'
    assert worker._load_numbering_definitions()[1]['lvl_overrides'][0]['numFmt'] == 'lowerLetter'
    assert snapshot.source_sha256 == hashlib.sha256(original).hexdigest()
    assert source.read_bytes() == other


@pytest.mark.parametrize('failure', ['malformed_xml', 'missing_content_types', 'missing_main_part', 'missing_body'])
def test_malformed_captured_packages_have_content_free_cli_errors(tmp_path, monkeypatch, capsys, failure):
    monkeypatch.setattr(cli, 'PROJECT_ROOT', tmp_path)
    source = _source(tmp_path / 'SYNTHETIC_PRIVATE_PATH.docx')
    captured_bytes = source.read_bytes()
    output = BytesIO()
    with zipfile.ZipFile(BytesIO(captured_bytes)) as incoming, zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED) as outgoing:
        for item in incoming.infolist():
            if (failure == 'missing_content_types' and item.filename == '[Content_Types].xml') or (
                failure == 'missing_main_part' and item.filename == 'word/document.xml'
            ):
                continue
            data = incoming.read(item.filename)
            if item.filename == 'word/document.xml':
                if failure == 'malformed_xml':
                    data = b'<SYNTHETIC_PRIVATE_BAD_XML'
                elif failure == 'missing_body':
                    data = b'<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"/>'
            outgoing.writestr(item, data)
    source.write_bytes(output.getvalue())
    before = source.read_bytes()
    writer = Mock()
    monkeypatch.setattr(cli, '_atomic_write_json', writer)
    assert cli.main([
        'extract-master', '--master', str(source), '--output', str(tmp_path / 'data/private/processed/evidence.json'),
        '--snapshot-output', str(tmp_path / 'data/private/processed/snapshot.json'),
    ]) == 2
    captured = capsys.readouterr()
    assert 'readable DOCX' in captured.out
    assert 'SYNTHETIC_PRIVATE' not in captured.out + captured.err
    writer.assert_not_called()
    assert source.read_bytes() == before


def test_real_unreadable_source_is_controlled_without_leaking_its_path(tmp_path):
    if not hasattr(os, 'geteuid') or os.geteuid() == 0:
        pytest.skip('real POSIX read permissions require a non-root process')
    source = _source(tmp_path / 'SYNTHETIC_PRIVATE_PATH.docx')
    before = source.read_bytes()
    mode = source.stat().st_mode & 0o777
    source.chmod(0)
    try:
        with pytest.raises(PrivateInputError) as failure:
            capture_docx_source(source)
        assert 'SYNTHETIC_PRIVATE' not in str(failure.value)
    finally:
        source.chmod(mode)
    assert source.read_bytes() == before


def test_source_fifo_is_rejected_before_open_or_read(tmp_path, monkeypatch):
    source = tmp_path / 'source-fifo'
    os.mkfifo(source)
    read = Mock(side_effect=AssertionError('A FIFO must not be read'))
    monkeypatch.setattr(private_input.os, 'fdopen', read)
    with pytest.raises(PrivateInputError, match='regular file'):
        capture_docx_source(source)
    read.assert_not_called()


def test_changed_contents_during_capture_are_rejected_even_if_restored(tmp_path, monkeypatch):
    source = _source(tmp_path / 'during-capture.docx')
    replacement = _source(tmp_path / 'during-other.docx', 'SYNTHETIC_CAPTURE_B')
    original, other = source.read_bytes(), replacement.read_bytes()
    real_fdopen = private_input.os.fdopen
    reads = []

    class ChangedRead:
        def __init__(self, handle):
            self.handle = handle

        def __enter__(self):
            return self

        def __exit__(self, *args):
            self.handle.close()

        def fileno(self):
            return self.handle.fileno()

        def read(self):
            data = self.handle.read()
            source.write_bytes(other)
            source.write_bytes(original)
            reads.append(data)
            return data

    monkeypatch.setattr(private_input.os, 'fdopen', lambda fd, mode: ChangedRead(real_fdopen(fd, mode)))
    with pytest.raises(PrivateInputError, match='changed during reading'):
        capture_docx_source(source)
    assert reads == [original]
    assert source.read_bytes() == original
