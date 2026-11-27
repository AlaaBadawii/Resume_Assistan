'''End-to-end managed CLI acceptance with synthetic DOCX/JD and offline transport.'''

import hashlib
import json
import stat
from unittest.mock import patch

import pytest
from docx import Document

from resume_assistant import cli
from resume_assistant.job_models import JobDescriptionAnalysis
from resume_assistant.legacy_section_plan import LegacyResumeSectionPlan
from resume_assistant.llm_client import AgentRouterClient
from resume_assistant.matching_models import CandidateJobMatch
from resume_assistant.matching import CandidateJobMatcher
from resume_assistant.publication_reader import load_committed_pair
from resume_assistant.section_planner import ExtractionGenerationIdentity, ResumeSectionPlan, SectionPlanner
from tests.fixtures import builders
from tests.fixtures.jobs import SYNTHETIC_JOB_TEXT, grounded_job_payload
from tests.fixtures.section_plans import legacy_plan_payload


CANDIDATE_ONLY = 'SYNTHETIC_CANDIDATE_ONLY_PIPELINE_MARKER'
FAILURE_MARKER = 'SYNTHETIC_PIPELINE_FAILURE_MARKER'


def _candidate_document():
    document = Document()
    document.add_heading('1. Professional Identity', level=1)
    document.add_paragraph('Software engineer building local synthetic systems. ' + CANDIDATE_ONLY)
    document.add_heading('2. Technical Profile', level=1)
    document.add_paragraph('Python')
    document.add_paragraph('GitHub Actions')
    document.add_paragraph('Email: candidate-only@example.invalid')
    technical = document.add_table(rows=2, cols=1)
    technical.cell(0, 0).text = 'SQL'
    technical.cell(1, 0).text = 'Rust'
    technical.cell(1, 0).add_table(rows=1, cols=1).cell(0, 0).text = 'Kubernetes'
    document.add_heading('3. Professional Experience And Engineering Training', level=1)
    role = document.add_table(rows=2, cols=2)
    role.cell(0, 0).text = 'Original synthetic engineer at Example Organization'
    role.cell(0, 1).text = 'Original synthetic dates: 2021–2024'
    role.cell(1, 1).text = 'Design and maintain Python services.'
    role.cell(0, 0).merge(role.cell(1, 0))
    title = document.add_paragraph('3.1. Original synthetic training role')
    title.runs[0].bold = True
    document.add_paragraph('Original training supporting source.')
    for number, heading, title_text, detail in (
        (4, 'Independent Software And AI Projects', 'Original synthetic service project', 'Python service with SQL storage.'),
        (5, 'Education', 'Bachelor degree in Computer Science.', 'Original synthetic course details.'),
        (6, 'Certifications And Professional Development', 'AWS Certified Developer', 'Original synthetic qualification details.'),
    ):
        document.add_heading(f'{number}. {heading}', level=1)
        title = document.add_paragraph(f'{number}.1. {title_text}')
        title.runs[0].bold = True
        document.add_paragraph(detail)
    document.add_heading('7. Languages', level=1)
    document.add_paragraph('English (fluent)')
    return document


class OfflineResponsesTransport:
    '''Real client/protocol parsing, with a deterministic synthetic wire response.'''
    def __init__(self):
        self.calls = []
        self.response = grounded_job_payload()
        self.response['required_skills'][0]['name'] = 'Python and SQL'
        self.status = 'completed'

    def __call__(self, url, headers, payload, timeout):
        self.calls.append({'url': url, 'payload': payload, 'timeout': timeout})
        envelope = {
            'status': self.status, 'error': None, 'incomplete_details': None,
            'output': [{
                'type': 'message', 'role': 'assistant', 'status': 'completed',
                'content': [{'type': 'output_text', 'text': json.dumps(self.response)}],
            }],
        }
        return 200, json.dumps(envelope).encode('utf-8')


@pytest.fixture
def pipeline(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cli, 'PROJECT_ROOT', tmp_path)
    processed = tmp_path / 'data/private/processed'
    processed.mkdir(parents=True)
    source = builders.save(_candidate_document(), tmp_path / 'synthetic-master.docx')
    job_text = processed / 'synthetic-job.txt'
    job_text.write_text(SYNTHETIC_JOB_TEXT, encoding='utf-8')
    paths = {
        'source': source, 'job_text': job_text, 'candidate': processed / 'candidate.json',
        'analysis': processed / 'analysis.json', 'match': processed / 'match.json',
        'plan': processed / 'plan.json',
    }
    snapshot_selector = processed / 'snapshot-selector.json'
    original_inputs = {path: path.read_bytes() for path in (source, job_text)}
    transport = OfflineResponsesTransport()
    real_client = AgentRouterClient
    monkeypatch.setenv('AGENTROUTER_API_KEY', 'synthetic-test-key-not-real')
    monkeypatch.setenv('AGENTROUTER_BASE_URL', 'https://provider.example.invalid/v1')
    monkeypatch.setenv('AGENTROUTER_MODEL', 'synthetic-analysis-model')
    monkeypatch.setenv('AGENTROUTER_TIMEOUT_SECONDS', '3')
    monkeypatch.setattr(cli, 'AgentRouterClient', lambda config: real_client(config, http_post=transport))
    commands = {
        'extract': ['extract-master', '--master', str(source), '--output', str(paths['candidate']), '--snapshot-output', str(snapshot_selector)],
        'analysis': ['analyze-job', '--input', str(job_text), '--output', str(paths['analysis'])],
        'match': ['match-job', '--analysis', str(paths['analysis']), '--candidate-evidence', str(paths['candidate']), '--output', str(paths['match'])],
        'plan': ['plan-sections', '--analysis', str(paths['analysis']), '--match', str(paths['match']), '--candidate-evidence', str(paths['candidate']), '--output', str(paths['plan'])],
    }
    assert cli.main(commands['extract']) == 0
    assert not snapshot_selector.exists()
    snapshot, catalog, manifest, generation_id = load_committed_pair(tmp_path, paths['candidate'])
    assert manifest is not None and generation_id == manifest.generation_id
    generation = cli.publication_store.generation_dir_path(tmp_path, manifest.bundle_key, generation_id)
    paths['generation_catalog'] = generation / 'catalog.json'
    paths['generation_snapshot'] = generation / 'snapshot.json'
    assert cli.main(commands['analysis']) == 0
    for name in ('AGENTROUTER_API_KEY', 'AGENTROUTER_BASE_URL', 'AGENTROUTER_MODEL', 'AGENTROUTER_TIMEOUT_SECONDS'):
        monkeypatch.delenv(name)
    assert cli.main(commands['match']) == 0
    assert cli.main(commands['plan']) == 0
    assert {path: path.read_bytes() for path in original_inputs} == original_inputs
    capsys.readouterr()
    return paths, commands, transport, snapshot, catalog, manifest


def _read_plan(paths):
    return ResumeSectionPlan.model_validate_json(paths['plan'].read_bytes())


def test_stage_e_complete_managed_workflow_preserves_versions_facts_and_provider_isolation(pipeline):
    paths, _commands, transport, snapshot, catalog, manifest = pipeline
    analysis = JobDescriptionAnalysis.model_validate_json(paths['analysis'].read_bytes())
    matched = CandidateJobMatch.model_validate_json(paths['match'].read_bytes())
    plan = _read_plan(paths)
    assert snapshot.schema_version == catalog.schema_version == '3.0.0'
    assert analysis.schema_version == matched.schema_version == plan.schema_version == '2.0.0'
    assert plan.planning_metadata.extraction_generation == ExtractionGenerationIdentity.from_manifest(manifest)
    assert len(transport.calls) == 1
    request = transport.calls[0]['payload']
    assert request['input'] == SYNTHETIC_JOB_TEXT
    assert CANDIDATE_ONLY not in json.dumps(request)
    assert [skill.skill_text for skill in plan.prioritized_skills if skill.priority_tier == 'required'] == ['Python', 'SQL']
    assert 'Python and SQL' not in plan.professional_summary_blueprint.technical_capability_skill_texts
    assert plan.human_review_required is True and plan.budgets.page_guarantee is False
    selected = set(plan.selected_evidence_ids)
    excluded = {item.evidence_id for item in plan.excluded_evidence}
    assert selected.isdisjoint(excluded)
    assert selected | excluded == {record.evidence_id for record in catalog.evidence_records}
    assert len(plan.evidence_provenance) == len(catalog.evidence_records)
    by_id = {record.evidence_id: record for record in catalog.evidence_records}
    assert all('Email:' not in by_id[identifier].original_content for identifier in selected)
    experience = next(section for section in plan.ordered_sections if section.section_id == 'professional-experience')
    merged = next(entry for entry in experience.entries if entry.grouping_basis == 'table_row')
    assert len(merged.selected_evidence_ids) == 2
    assert 'Original synthetic engineer' in by_id[merged.identity_evidence_id].original_content
    assert plan.professional_summary_blueprint.identity_statements[0].evidence_ids
    assert plan.professional_summary_blueprint.experience_bindings[0].entry_ids
    assert plan.professional_summary_blueprint.project_highlights[0].selected_evidence_ids
    assert SectionPlanner(plan.budgets).validate_result(
        plan, analysis, matched, catalog, snapshot, manifest_available=True,
        extraction_generation=ExtractionGenerationIdentity.from_manifest(manifest),
    ) == plan
    for name in ('candidate', 'analysis', 'match', 'plan', 'generation_catalog', 'generation_snapshot'):
        assert stat.S_IMODE(paths[name].stat().st_mode) == 0o600


def test_stage_e_json_restored_plan_is_source_validated_immediately_before_publication(pipeline):
    _paths, commands, _transport, *_ = pipeline
    events = []
    produced = []
    real_plan = SectionPlanner.plan
    real_validate = SectionPlanner.validate_result
    real_write = cli.PrivateOutput.write_json
    def plan(*args, **kwargs):
        result = real_plan(*args, **kwargs)
        produced.append(result)
        events.append('plan')
        return result
    def validate(self, result, *args, **kwargs):
        assert result is not produced[0]
        assert result == produced[0]
        events.append('validate-restored')
        return real_validate(self, result, *args, **kwargs)
    def publish(*args, **kwargs):
        events.append('publish')
        return real_write(*args, **kwargs)
    with patch.object(SectionPlanner, 'plan', plan), patch.object(SectionPlanner, 'validate_result', validate), patch.object(cli.PrivateOutput, 'write_json', publish):
        assert cli.main(commands['plan']) == 0
    assert events == ['plan', 'validate-restored', 'publish']


def test_stage_e_matching_and_planning_repeat_without_more_provider_requests(pipeline):
    paths, commands, transport, *_ = pipeline
    baseline = {name: paths[name].read_bytes() for name in ('match', 'plan')}
    for _ in range(3):
        assert cli.main(commands['match']) == 0
        assert cli.main(commands['plan']) == 0
        assert {name: paths[name].read_bytes() for name in baseline} == baseline
    assert len(transport.calls) == 1


@pytest.mark.parametrize('failure', ['unsupported_value', 'unrelated_quotation', 'partial_envelope'])
def test_stage_e_invalid_job_analysis_preserves_all_previous_pipeline_artifacts(pipeline, monkeypatch, capsys, failure):
    paths, commands, transport, *_ = pipeline
    before = {path: path.read_bytes() for path in paths.values()}
    monkeypatch.setenv('AGENTROUTER_API_KEY', 'synthetic-test-key-not-real')
    if failure == 'unsupported_value':
        transport.response['company_name']['value'] = FAILURE_MARKER
    elif failure == 'unrelated_quotation':
        transport.response['responsibilities'][0]['quotation'] = 'Unrelated unsupported source quotation.'
    else:
        transport.status = 'incomplete'
    assert cli.main(commands['analysis']) == 2
    captured = capsys.readouterr()
    assert FAILURE_MARKER not in captured.out + captured.err
    assert {path: path.read_bytes() for path in paths.values()} == before


@pytest.mark.parametrize('tamper', ['analysis_version', 'foreign_analysis', 'match_reference', 'generation_catalog', 'generation_snapshot', 'missing_generation'])
def test_stage_e_tampered_or_incompatible_handoffs_fail_before_plan_publication(pipeline, capsys, tamper):
    paths, commands, _transport, *_ = pipeline
    if tamper == 'analysis_version':
        data = json.loads(paths['analysis'].read_text())
        data['schema_version'] = '1.0.0'
        paths['analysis'].write_text(json.dumps(data), encoding='utf-8')
    elif tamper == 'foreign_analysis':
        data = json.loads(paths['analysis'].read_text())
        data['preferred_skills'] = []
        paths['analysis'].write_text(json.dumps(data), encoding='utf-8')
    elif tamper == 'match_reference':
        data = json.loads(paths['match'].read_text())
        data['requirements'][0]['components'][0]['matched_evidence_ids'] = [FAILURE_MARKER]
        paths['match'].write_text(json.dumps(data), encoding='utf-8')
    elif tamper in ('generation_catalog', 'generation_snapshot'):
        paths[tamper].write_bytes(paths[tamper].read_bytes() + b' ')
    else:
        data = json.loads(paths['candidate'].read_text())
        data['generation_id'] = 'synthetic-missing-generation'
        paths['candidate'].write_text(json.dumps(data), encoding='utf-8')
    before = {path: path.read_bytes() for path in paths.values()}
    with patch.object(cli.PrivateOutput, 'write_json') as writer:
        assert cli.main(commands['plan']) == 2
    writer.assert_not_called()
    captured = capsys.readouterr()
    assert FAILURE_MARKER not in captured.out + captured.err
    assert {path: path.read_bytes() for path in paths.values()} == before


@pytest.mark.parametrize('stage', ['analysis', 'match', 'plan'])
def test_stage_e_private_atomic_publication_failures_preserve_complete_previous_workflow(pipeline, monkeypatch, capsys, stage):
    paths, commands, _transport, *_ = pipeline
    before = {path: path.read_bytes() for path in paths.values()}
    if stage == 'analysis':
        monkeypatch.setenv('AGENTROUTER_API_KEY', 'synthetic-test-key-not-real')
    with patch('resume_assistant.private_output.os.replace', side_effect=OSError(FAILURE_MARKER)):
        assert cli.main(commands[stage]) == 3
    captured = capsys.readouterr()
    assert FAILURE_MARKER not in captured.out + captured.err
    assert {path: path.read_bytes() for path in paths.values()} == before
    assert not list(paths['plan'].parent.glob('.resume-assistant-*.tmp'))


@pytest.mark.parametrize('target', ['analysis', 'candidate', 'generation_catalog', 'generation_snapshot'])
def test_stage_e_match_output_never_replaces_consumed_managed_inputs(pipeline, capsys, target):
    paths, commands, _transport, *_ = pipeline
    argv = commands['match'][:-1] + [str(paths[target])]
    before = {path: path.read_bytes() for path in paths.values()}
    with patch.object(cli.PrivateOutput, 'write_json') as writer:
        assert cli.main(argv) == 2
    writer.assert_not_called()
    assert {path: path.read_bytes() for path in paths.values()} == before
    capsys.readouterr()


@pytest.mark.parametrize('target', ['generation_catalog', 'generation_snapshot'])
def test_stage_e_alias_created_during_matching_is_rejected_before_publication(pipeline, target):
    paths, commands, _transport, *_ = pipeline
    output = paths['match'].with_name('late-match.json')
    before = {path: path.read_bytes() for path in paths.values()}
    real_assess = CandidateJobMatcher.assess
    def introduce_alias(*args, **kwargs):
        result = real_assess(*args, **kwargs)
        output.hardlink_to(paths[target])
        return result
    with patch.object(CandidateJobMatcher, 'assess', introduce_alias), patch.object(cli.PrivateOutput, 'write_json') as writer:
        assert cli.main(commands['match'][:-1] + [str(output)]) == 2
    writer.assert_not_called()
    assert output.samefile(paths[target])
    assert {path: path.read_bytes() for path in paths.values()} == before


def test_stage_e_analysis_cannot_replace_its_original_job_description(pipeline, monkeypatch):
    paths, commands, transport, *_ = pipeline
    monkeypatch.setenv('AGENTROUTER_API_KEY', 'synthetic-test-key-not-real')
    before = paths['job_text'].read_bytes()
    calls = len(transport.calls)
    assert cli.main(commands['analysis'][:-1] + [str(paths['job_text'])]) == 2
    assert paths['job_text'].read_bytes() == before
    assert len(transport.calls) == calls


def test_stage_e_legacy_inspection_requires_source_backed_managed_replanning(pipeline):
    paths, _commands, _transport, snapshot, catalog, manifest = pipeline
    current = _read_plan(paths)
    data = legacy_plan_payload(current)
    data['professional_summary_blueprint']['technical_capability_skill_texts'].append('Unsupported historical qualification')
    legacy = LegacyResumeSectionPlan.model_validate_json(json.dumps(data))
    analysis = JobDescriptionAnalysis.model_validate_json(paths['analysis'].read_bytes())
    matched = CandidateJobMatch.model_validate_json(paths['match'].read_bytes())
    rebuilt = SectionPlanner(current.budgets).replan_legacy(
        legacy, analysis, matched, catalog, snapshot, manifest_available=True,
        extraction_generation=ExtractionGenerationIdentity.from_manifest(manifest),
    )
    assert rebuilt == current
    assert 'Unsupported historical qualification' not in rebuilt.model_dump_json()
    assert hashlib.sha256(paths['source'].read_bytes()).hexdigest() == catalog.source_document.sha256
