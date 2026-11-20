'''Real extraction CLI prevalidation/serialization privacy regressions.'''

from unittest.mock import Mock

import pytest
from docx import Document

from resume_assistant import cli
from resume_assistant.models import CandidateEvidenceCatalog, DocumentSnapshot

MARKER = 'SYNTHETIC_PRIVATE_M221_SERIALIZATION_a79ed21'


@pytest.fixture
def extraction_project(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, 'PROJECT_ROOT', tmp_path)
    document = Document()
    document.add_heading('1. Technical Profile', level=1)
    document.add_paragraph('Python development')
    source = tmp_path / 'private-path.docx'
    document.save(source)
    catalog = tmp_path / 'data/private/processed/evidence.json'
    snapshot = tmp_path / 'data/private/processed/snapshot.json'
    cli.run_extract_master(source, catalog, snapshot)
    return source, catalog, snapshot


def _args(project):
    source, catalog, snapshot = project
    return ['extract-master', '--master', str(source), '--output', str(catalog), '--snapshot-output', str(snapshot)]


@pytest.mark.parametrize('artifact', ['catalog', 'snapshot'])
def test_invalid_mutable_values_are_revalidated_without_serialization_warning_leaks(
    extraction_project, monkeypatch, capsys, artifact,
):
    source, catalog_path, _snapshot_path = extraction_project
    before = {source: source.read_bytes(), catalog_path: catalog_path.read_bytes()}
    capsys.readouterr()
    real_prepare = cli._serialize_extraction_artifact
    reached = []
    model = CandidateEvidenceCatalog if artifact == 'catalog' else DocumentSnapshot
    real_validate = model.model_validate
    validator = Mock(wraps=real_validate)
    monkeypatch.setattr(model, 'model_validate', validator)

    def corrupt_then_prepare(kind, value):
        if kind is model:
            if artifact == 'catalog':
                value.source_document.sha256 = [MARKER]
            else:
                value.statistics.word_count = [MARKER]
            reached.append(True)
        return real_prepare(kind, value)

    monkeypatch.setattr(cli, '_serialize_extraction_artifact', corrupt_then_prepare)
    writer = Mock()
    monkeypatch.setattr(cli, '_atomic_write_json', writer)
    assert cli.main(_args(extraction_project)) == 2
    captured = capsys.readouterr()
    assert reached == [True]
    validator.assert_called_once()
    assert 'artifact validation failed' in captured.out
    assert MARKER not in captured.out + captured.err
    assert 'PydanticSerializationUnexpectedValue' not in captured.err
    writer.assert_not_called()
    assert {source: source.read_bytes(), catalog_path: catalog_path.read_bytes()} == before


@pytest.mark.parametrize('artifact', ['catalog', 'snapshot'])
def test_both_real_json_serializers_use_warning_errors(extraction_project, monkeypatch, artifact):
    model = CandidateEvidenceCatalog if artifact == 'catalog' else DocumentSnapshot
    real_serialize = model.model_dump_json
    calls = []

    def serialize(self, **kwargs):
        calls.append(kwargs)
        assert kwargs['warnings'] == 'error'
        return real_serialize(self, **kwargs)

    monkeypatch.setattr(model, 'model_dump_json', serialize)
    assert cli.main(_args(extraction_project)) == 0
    assert len(calls) == 1


@pytest.mark.parametrize('target', ['catalog', 'record', 'snapshot', 'header_part'])
def test_hash_tampering_cannot_publish_under_a_different_input_hash(extraction_project, monkeypatch, capsys, target):
    source, catalog_path, _snapshot_path = extraction_project
    before = {source: source.read_bytes(), catalog_path: catalog_path.read_bytes()}
    capsys.readouterr()
    real_prepare = cli._serialize_extraction_artifact
    reached = []

    def different_hash(kind, value):
        if kind is CandidateEvidenceCatalog and target in {'catalog', 'record'}:
            if target == 'catalog':
                value.source_document.sha256 = 'b' * 64
            else:
                value.evidence_records[0].source_sha256 = 'b' * 64
            reached.append(True)
        elif kind is DocumentSnapshot and target in {'snapshot', 'header_part'}:
            if target == 'snapshot':
                value.source_sha256 = 'b' * 64
            else:
                from resume_assistant.models import HeaderFooterPart

                value.header_footer_parts.append(HeaderFooterPart(
                    document_part='word/header1.xml', kind='header',
                    source_document=value.source_filename, source_sha256='b' * 64,
                    extraction_status='unsupported',
                ))
            reached.append(True)
        return real_prepare(kind, value)

    monkeypatch.setattr(cli, '_serialize_extraction_artifact', different_hash)
    writer = Mock()
    monkeypatch.setattr(cli, '_atomic_write_json', writer)
    assert cli.main(_args(extraction_project)) == 2
    captured = capsys.readouterr()
    assert 'captured document input' in captured.out
    assert 'b' * 64 not in captured.out + captured.err
    assert reached == [True]
    writer.assert_not_called()
    assert {source: source.read_bytes(), catalog_path: catalog_path.read_bytes()} == before
