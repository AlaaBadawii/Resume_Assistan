'''Schema-3.0.0 legacy/default and approved matcher compatibility regressions.'''

import json
from unittest.mock import Mock

import pytest
from docx import Document
from docx.oxml import OxmlElement

from resume_assistant import cli, evidence, extractor
from resume_assistant.job_models import JobDescriptionAnalysis
from resume_assistant.matching import CandidateJobMatcher
from resume_assistant.matching_models import CandidateJobMatch
from resume_assistant.models import CandidateEvidenceCatalog, DocumentSnapshot
from tests.fixtures.jobs import empty_job_payload


def _analysis():
    payload = empty_job_payload()
    payload['required_skills'] = [
        {'name': name, 'quotation': f'Required: {name}.', 'classification_context': f'Required: {name}.'}
        for name in ['Python', 'Kubernetes']
    ]
    return JobDescriptionAnalysis.model_validate({**payload, 'prompt_version': '2.0.0'})


def _snapshot(tmp_path, gap):
    document = Document()
    document.add_heading('1. Technical Profile', level=1)
    document.add_paragraph('Python development')
    document.sections[0].header.paragraphs[0].text = 'SYNTHETIC_HEADER'
    if gap == 'inline':
        paragraph = document.add_paragraph()
        container = OxmlElement('w:moveTo')
        run = OxmlElement('w:r')
        text = OxmlElement('w:t')
        text.text = 'SYNTHETIC_UNEXTRACTED_CONTENT'
        run.append(text)
        container.append(run)
        paragraph._p.append(container)
    elif gap == 'unresolved_part':
        from docx.oxml.ns import qn

        document.sections[0]._sectPr.append(OxmlElement('w:footerReference', attrs={
            qn('w:type'): 'even', qn('r:id'): 'rIdSyntheticMissing',
        }))
    source = tmp_path / 'SYNTHETIC_PRIVATE_FILENAME.docx'
    document.save(source)
    return source, extractor.MasterResumeExtractor(source).extract()


@pytest.mark.parametrize('gap', [None, 'inline', 'unresolved_part'])
def test_existing_matcher_accepts_current_catalogs_and_retains_coverage_semantics(tmp_path, gap):
    _, snapshot = _snapshot(tmp_path, gap)
    catalog = evidence.build_evidence_catalog(snapshot)
    loaded = CandidateEvidenceCatalog.model_validate_json(catalog.model_dump_json(), strict=True)
    match = CandidateJobMatcher(loaded).assess(_analysis())
    assert snapshot.schema_version == loaded.schema_version == '3.0.0'
    assert match.schema_version == match.source_job_schema_version == match.source_job_prompt_version == '2.0.0'
    assert match.candidate_evidence_schema_version == '3.0.0'
    assert match.requirements[0].status == 'supported'
    assert match.requirements[0].matching_method == 'exact_normalized_phrase_v2'
    assert match.requirements[1].status == ('no_evidence_found' if gap is None else 'not_assessable')
    assert match.human_review_required is True
    assert 'SYNTHETIC_UNEXTRACTED_CONTENT' not in match.model_dump_json()


@pytest.mark.parametrize('gap', [None, 'inline', 'unresolved_part'])
def test_legacy_optional_field_defaults_keep_schema3_artifacts_and_matching_results_compatible(tmp_path, gap):
    _, snapshot = _snapshot(tmp_path, gap)
    catalog = evidence.build_evidence_catalog(snapshot)
    legacy_snapshot = json.loads(snapshot.model_dump_json())
    legacy_snapshot.pop('header_footer_parts')
    legacy_snapshot.pop('header_footer_references')
    for block in [*legacy_snapshot['blocks'], *legacy_snapshot['headers'], *legacy_snapshot['footers']]:
        block.pop('source_location')
    for diagnostic in legacy_snapshot['unsupported_content']:
        diagnostic.pop('source_location')
        diagnostic.pop('reason')
    restored = DocumentSnapshot.model_validate_json(json.dumps(legacy_snapshot))
    assert restored.schema_version == '3.0.0'
    assert restored.header_footer_parts == restored.header_footer_references == []
    assert all(block.source_location is None for block in [*restored.blocks, *restored.headers, *restored.footers])
    assert [block.paragraph.text for block in restored.headers] == [block.paragraph.text for block in snapshot.headers]
    legacy_catalog = json.loads(catalog.model_dump_json())
    legacy_catalog['extraction_completeness'].pop('unsupported_content')
    loaded = CandidateEvidenceCatalog.model_validate_json(json.dumps(legacy_catalog), strict=True)
    assert loaded.extraction_completeness.unsupported_content == []
    assert CandidateJobMatcher(loaded).assess(_analysis()) == CandidateJobMatcher(catalog).assess(_analysis())


@pytest.mark.parametrize('gap', [None, 'inline', 'unresolved_part'])
def test_extract_master_to_approved_match_job_cli_stays_offline_and_private(tmp_path, monkeypatch, capsys, gap):
    monkeypatch.setattr(cli, 'PROJECT_ROOT', tmp_path)
    source, _ = _snapshot(tmp_path, gap)
    processed = tmp_path / 'data/private/processed'
    processed.mkdir(parents=True)
    catalog_path, snapshot_path = processed / 'catalog.json', processed / 'snapshot.json'
    analysis_path, output_path = processed / 'analysis.json', processed / 'match.json'
    analysis_path.write_text(_analysis().model_dump_json(), encoding='utf-8')
    provider = Mock(side_effect=AssertionError('No provider may be constructed'))
    monkeypatch.setattr(cli, 'AgentRouterClient', provider)
    assert cli.main([
        'extract-master', '--master', str(source), '--output', str(catalog_path),
        '--snapshot-output', str(snapshot_path),
    ]) == 0
    capsys.readouterr()
    assert cli.main([
        'match-job', '--analysis', str(analysis_path), '--candidate-evidence', str(catalog_path),
        '--output', str(output_path),
    ]) == 0
    provider.assert_not_called()
    captured = capsys.readouterr()
    assert 'SYNTHETIC_PRIVATE_FILENAME' not in captured.out + captured.err
    assert 'Python development' not in captured.out + captured.err
    assert 'SYNTHETIC_UNEXTRACTED_CONTENT' not in captured.out + captured.err
    match = CandidateJobMatch.model_validate_json(output_path.read_bytes())
    assert match.requirements[0].status == 'supported'
    assert match.requirements[1].status == ('no_evidence_found' if gap is None else 'not_assessable')
