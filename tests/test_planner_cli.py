'''CLI and security tests for the plan-sections command (Mission 3.3).'''

import errno
import hashlib
import io
import json
import stat
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

import pytest
from docx import Document

import resume_assistant.cli as cli_module
from resume_assistant import private_input
from resume_assistant.job_models import JobDescriptionAnalysis
from resume_assistant.matching import CandidateJobIntegrityError, CandidateJobMatcher
from resume_assistant.matching_models import CandidateJobMatch
from resume_assistant.models import EvidenceType, SourceLocation
from resume_assistant.publication_reader import load_committed_pair
from resume_assistant.section_planner import PLAN_SCHEMA_VERSION, SectionPlanner
from tests.fixtures import builders
from tests.fixtures.jobs import empty_job_payload, grounded_job_payload, synthetic_candidate_catalog, synthetic_evidence
from tests.test_section_planner import _cross_artifact_inputs


def _write_json(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding='utf-8')


def _build_artifacts(tmp_path: Path):
    processed = tmp_path / 'data' / 'private' / 'processed'
    processed.mkdir(parents=True, exist_ok=True)

    catalog = synthetic_candidate_catalog([
        synthetic_evidence('ev-py', EvidenceType.TECHNICAL_SKILL, 'Python').model_copy(
            update={
                'section_reference': 'technical-profile',
                'source_location': SourceLocation(body_block_index=1),
            }
        ),
        synthetic_evidence('ev-sql', EvidenceType.TECHNICAL_SKILL, 'SQL').model_copy(
            update={
                'section_reference': 'technical-profile',
                'source_location': SourceLocation(body_block_index=2),
            }
        ),
        synthetic_evidence('ev-exp', EvidenceType.EXPERIENCE, 'Senior Engineer at Acme').model_copy(
            update={
                'section_reference': 'professional-experience-and-engineering-training',
                'source_location': SourceLocation(body_block_index=3),
            }
        ),
        synthetic_evidence('ev-proj', EvidenceType.PROJECT, 'Alpha Project').model_copy(
            update={
                'section_reference': 'independent-software-and-ai-projects',
                'source_location': SourceLocation(body_block_index=4),
            }
        ),
    ]).model_copy(
        update={
            'section_references': [
                'technical-profile',
                'professional-experience-and-engineering-training',
                'independent-software-and-ai-projects',
            ]
        }
    )

    analysis_payload = dict(grounded_job_payload())
    analysis_payload['prompt_version'] = '2.0.0'
    analysis = JobDescriptionAnalysis.model_validate(analysis_payload)

    match = CandidateJobMatcher(catalog).assess(analysis)

    plan = SectionPlanner().plan(analysis, match, catalog)

    analysis_path = processed / 'job_description_analysis.json'
    match_path = processed / 'job_candidate_match.json'
    catalog_path = processed / 'candidate_evidence.json'
    output_path = processed / 'resume_section_plan.json'

    _write_json(analysis_path, json.loads(analysis.model_dump_json()))
    _write_json(match_path, json.loads(match.model_dump_json()))
    _write_json(catalog_path, json.loads(catalog.model_dump_json()))

    return analysis_path, match_path, catalog_path, output_path, plan


def _run(argv, tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cli_module, 'PROJECT_ROOT', tmp_path)
    return cli_module.main(argv)


def _plan_argv(paths, output=None):
    return [
        'plan-sections',
        '--analysis', str(paths['analysis']),
        '--match', str(paths['match']),
        '--candidate-evidence', str(paths['candidate']),
        '--output', str(output if output is not None else paths['output']),
    ]


def _synthetic_analysis(required=('Python',)):
    data = empty_job_payload()
    data['required_skills'] = [
        {
            'name': name,
            'quotation': f'required_skills: {name}.',
            'classification_context': f'required_skills: {name}.',
        }
        for name in required
    ]
    return JobDescriptionAnalysis.model_validate({**data, 'prompt_version': '2.0.0'})


def _managed_bundle(tmp_path):
    '''Run the real extractor + matcher over a synthetic DOCX.'''
    document = Document()
    document.add_heading('1. Professional Identity', level=1)
    document.add_paragraph('Software engineer building local synthetic systems.')
    document.add_heading('2. Technical Profile', level=1)
    document.add_paragraph('Python and SQL')
    source = builders.save(document, tmp_path / 'synthetic.docx')
    processed = tmp_path / 'data' / 'private' / 'processed'
    candidate_path = processed / 'candidate.json'
    snapshot_selector = processed / 'snapshot-selector.json'
    with patch.object(cli_module, 'PROJECT_ROOT', tmp_path):
        cli_module.run_extract_master(source, candidate_path, snapshot_selector)
        snapshot, evidence, manifest, _ = load_committed_pair(tmp_path, candidate_path)
    job = _synthetic_analysis()
    paths = {
        'analysis': processed / 'analysis.json',
        'match': processed / 'match.json',
        'candidate': candidate_path,
        'output': processed / 'plan.json',
    }
    paths['analysis'].write_text(job.model_dump_json(), encoding='utf-8')
    with patch.object(cli_module, 'PROJECT_ROOT', tmp_path):
        cli_module.run_match_job(paths['analysis'], candidate_path, paths['match'])
    generation = (
        processed / '.extractions' / manifest.bundle_key
        / 'generations' / manifest.generation_id
    )
    return paths, snapshot, evidence, manifest, generation


# --------------------------------------------------------------- positive controls


def test_plan_sections_successful_execution(tmp_path, monkeypatch, capsys):
    analysis_path, match_path, catalog_path, output_path, _plan = _build_artifacts(tmp_path)
    rc = _run(
        _plan_argv({
            'analysis': analysis_path, 'match': match_path,
            'candidate': catalog_path, 'output': output_path,
        }),
        tmp_path, monkeypatch, capsys,
    )
    assert rc == 0
    assert output_path.exists()
    mode = stat.S_IMODE(output_path.stat().st_mode)
    assert mode == 0o600, f'expected 0o600, got {oct(mode)}'
    document = json.loads(output_path.read_text(encoding='utf-8'))
    assert document['schema_version'] == PLAN_SCHEMA_VERSION
    assert document['human_review_required'] is True
    assert len(document['selected_evidence_ids']) >= 1

    captured = capsys.readouterr()
    # Console output must be aggregate-only: no candidate resume content.
    assert 'Python' not in captured.out
    assert 'Senior Engineer at Acme' not in captured.out
    assert 'Alpha Project' not in captured.out
    assert 'selected_evidence_records:' in captured.out
    assert 'planned_sections:' in captured.out


def test_plan_sections_missing_required_argument(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cli_module, 'PROJECT_ROOT', tmp_path)
    with pytest.raises(SystemExit) as excinfo:
        _run(['plan-sections'], tmp_path, monkeypatch, capsys)
    assert excinfo.value.code == 2


def test_plan_sections_missing_input_path(tmp_path, monkeypatch, capsys):
    processed = tmp_path / 'data' / 'private' / 'processed'
    processed.mkdir(parents=True, exist_ok=True)
    catalog_path = processed / 'candidate_evidence.json'
    _write_json(catalog_path, {'schema_version': '3.0.0'})
    rc = _run(
        _plan_argv({
            'analysis': tmp_path / 'missing.json',
            'match': tmp_path / 'missing.json',
            'candidate': catalog_path,
            'output': processed / 'out.json',
        }),
        tmp_path, monkeypatch, capsys,
    )
    assert rc == 2


def test_plan_sections_symlink_input_rejected(tmp_path, monkeypatch, capsys):
    processed = tmp_path / 'data' / 'private' / 'processed'
    processed.mkdir(parents=True, exist_ok=True)
    catalog_path = processed / 'candidate_evidence.json'
    _write_json(catalog_path, {'schema_version': '3.0.0'})
    match_path = processed / 'match.json'
    _write_json(match_path, {'schema_version': '2.0.0'})

    analysis_path = processed / 'analysis.json'
    real = processed / 'real_analysis.json'
    _write_json(real, {'schema_version': '2.0.0'})
    analysis_path.symlink_to(real)

    rc = _run(
        _plan_argv({
            'analysis': analysis_path, 'match': match_path,
            'candidate': catalog_path, 'output': processed / 'out.json',
        }),
        tmp_path, monkeypatch, capsys,
    )
    assert rc == 2


def test_plan_sections_invalid_schema_version(tmp_path, monkeypatch, capsys):
    processed = tmp_path / 'data' / 'private' / 'processed'
    processed.mkdir(parents=True, exist_ok=True)
    catalog_path = processed / 'candidate_evidence.json'
    _write_json(catalog_path, {'schema_version': '3.0.0', 'evidence_records': [], 'extraction_completeness': {}})
    match_path = processed / 'match.json'
    _write_json(match_path, {'schema_version': '2.0.0', 'requirements': [], 'coverage_status': 'assessed', 'coverage_warnings': [], 'limitations': ['s']})
    analysis_path = processed / 'analysis.json'
    _write_json(analysis_path, {'schema_version': '1.0.0'})

    rc = _run(
        _plan_argv({
            'analysis': analysis_path, 'match': match_path,
            'candidate': catalog_path, 'output': processed / 'out.json',
        }),
        tmp_path, monkeypatch, capsys,
    )
    assert rc == 2


def test_plan_sections_malformed_json(tmp_path, monkeypatch, capsys):
    processed = tmp_path / 'data' / 'private' / 'processed'
    processed.mkdir(parents=True, exist_ok=True)
    catalog_path = processed / 'candidate_evidence.json'
    catalog_path.write_text('{not valid json', encoding='utf-8')
    match_path = processed / 'match.json'
    _write_json(match_path, {'schema_version': '2.0.0', 'requirements': [], 'coverage_status': 'assessed', 'coverage_warnings': [], 'limitations': ['s']})
    analysis_path = processed / 'analysis.json'
    _write_json(analysis_path, {'schema_version': '2.0.0'})

    rc = _run(
        _plan_argv({
            'analysis': analysis_path, 'match': match_path,
            'candidate': catalog_path, 'output': processed / 'out.json',
        }),
        tmp_path, monkeypatch, capsys,
    )
    assert rc == 2


def test_plan_sections_atomic_persisted_output_is_valid_json(tmp_path, monkeypatch, capsys):
    analysis_path, match_path, catalog_path, output_path, _plan = _build_artifacts(tmp_path)
    rc = _run(
        _plan_argv({
            'analysis': analysis_path, 'match': match_path,
            'candidate': catalog_path, 'output': output_path,
        }),
        tmp_path, monkeypatch, capsys,
    )
    assert rc == 0
    document = json.loads(output_path.read_text(encoding='utf-8'))
    assert document['schema_version'] == PLAN_SCHEMA_VERSION


# --------------------------------------------------- Stage B managed selection


@pytest.mark.parametrize('requirement,expected_required,status', [
    ('Python and SQL', ['Python', 'SQL'], 'supported'),
    ('Python and Rust', ['Python'], 'partially_supported'),
])
def test_stage_b_managed_selection_publishes_supported_deduplicated_components(
    tmp_path, monkeypatch, capsys, requirement, expected_required, status,
):
    document = Document()
    document.add_heading('1. Technical Profile', level=1)
    document.add_paragraph('Python')
    document.add_paragraph('PYTHON')
    document.add_paragraph('SQL')
    document.add_paragraph('Email: SYNTHETIC_PRIVATE_STAGE_B@example.invalid')
    document.add_heading('2. Professional Experience And Engineering Training', level=1)
    document.add_paragraph('Software engineer delivering synthetic systems.')
    source = builders.save(document, tmp_path / 'stage-b-synthetic.docx')
    processed = tmp_path / 'data' / 'private' / 'processed'
    monkeypatch.setattr(cli_module, 'PROJECT_ROOT', tmp_path)
    candidate = processed / 'candidate.json'
    cli_module.run_extract_master(source, candidate, processed / 'snapshot-selector.json')
    snapshot, catalog, manifest, _ = load_committed_pair(tmp_path, candidate)
    data = empty_job_payload()
    data['required_skills'] = [{
        'name': requirement, 'quotation': f'Required: {requirement}.',
        'classification_context': f'Required: {requirement}.',
    }]
    data['preferred_skills'] = [{
        'name': 'Python', 'quotation': 'Preferred: Python.', 'classification_context': 'Preferred: Python.',
    }]
    analysis = JobDescriptionAnalysis.model_validate({**data, 'prompt_version': '2.0.0'})
    paths = {
        'analysis': processed / 'analysis.json', 'match': processed / 'match.json',
        'candidate': candidate, 'output': processed / 'plan.json',
    }
    paths['analysis'].write_text(analysis.model_dump_json(), encoding='utf-8')
    match = cli_module.run_match_job(paths['analysis'], candidate, paths['match'])
    generation = cli_module.publication_store.generation_dir_path(tmp_path, manifest.bundle_key, manifest.generation_id)
    protected = [source, paths['analysis'], paths['match'], candidate, generation / 'catalog.json', generation / 'snapshot.json']
    before = {path: path.read_bytes() for path in protected}
    capsys.readouterr()
    assert cli_module.main(_plan_argv(paths)) == 0
    captured = capsys.readouterr()
    assert 'SYNTHETIC_PRIVATE_STAGE_B' not in captured.out + captured.err
    assert 'Python' not in captured.out + captured.err
    published = json.loads(paths['output'].read_text())
    assert [skill['skill_text'] for skill in published['prioritized_skills'] if skill['priority_tier'] == 'required'] == expected_required
    python = next(skill for skill in published['prioritized_skills'] if skill['skill_text'] == 'Python')
    assert len(python['matched_evidence_ids']) == 2
    assert [item['requirement_id'] for item in python['requirement_associations']] == [
        match.requirements[0].requirement_id, match.requirements[1].requirement_id,
    ]
    assert published['requirement_coverage'][0]['status'] == status
    assert 'Rust' not in published['professional_summary_blueprint']['technical_capability_skill_texts']
    by_id = {record.evidence_id: record for record in catalog.evidence_records}
    selected = [by_id[evidence_id] for evidence_id in published['selected_evidence_ids']]
    heading_indexes = {section.start_block_index for section in snapshot.sections}
    assert all(record.source_location.body_block_index not in heading_indexes for record in selected)
    assert all('SYNTHETIC_PRIVATE_STAGE_B' not in record.original_content for record in selected)
    assert any(record.evidence_type == EvidenceType.EXPERIENCE for record in selected)
    assert stat.S_IMODE(paths['output'].stat().st_mode) == 0o600
    assert {path: path.read_bytes() for path in protected} == before


# --------------------------------------------------- A-01 cross-artifact integrity


@pytest.mark.parametrize('tamper', [
    'foreign_job', 'wrong_candidate_hash', 'wrong_record_count', 'fake_requirement_id',
    'wrong_source_index', 'wrong_provenance', 'missing_requirements', 'forged_skill_support',
])
def test_cross_artifact_mismatch_is_rejected(tmp_path, monkeypatch, capsys, tamper):
    analysis, match, catalog = _cross_artifact_inputs(tamper)
    processed = tmp_path / 'data' / 'private' / 'processed'
    paths = {
        'analysis': processed / 'analysis.json',
        'match': processed / 'match.json',
        'candidate': processed / 'candidate.json',
        'output': processed / 'plan.json',
    }
    for key, artifact in (('analysis', analysis), ('match', match), ('candidate', catalog)):
        _write_json(paths[key], artifact.model_dump(mode='json'))
    paths['output'].write_bytes(b'SYNTHETIC_PREVIOUS_PLAN')
    before = {key: path.read_bytes() for key, path in paths.items()}
    writer = patch.object(cli_module.PrivateOutput, 'write_json')
    with writer as write_json:
        assert _run(_plan_argv(paths), tmp_path, monkeypatch, capsys) == 2
    write_json.assert_not_called()
    assert {key: path.read_bytes() for key, path in paths.items()} == before


@pytest.mark.parametrize('tamper', [
    'wrong_hash', 'wrong_requirement', 'wrong_provenance', 'missing_requirements',
])
def test_managed_inputs_still_require_cross_artifact_validation(
    tmp_path, monkeypatch, capsys, tamper,
):
    paths, _snapshot, _evidence, _manifest, _generation = _managed_bundle(tmp_path)
    data = json.loads(paths['match'].read_text())
    if tamper == 'wrong_hash':
        data['candidate_source_sha256'] = 'b' * 64
    elif tamper == 'wrong_requirement':
        data['requirements'][0]['source_index'] = 999
    elif tamper == 'wrong_provenance':
        data['requirements'][0]['matched_evidence'][0]['source_sha256'] = 'b' * 64
    else:
        data['requirements'] = []
    supplied = CandidateJobMatch.model_validate_json(json.dumps(data))
    # The approved matcher rejects the supplied decisions independently.
    job = JobDescriptionAnalysis.model_validate_json(paths['analysis'].read_bytes())
    with pytest.raises(CandidateJobIntegrityError):
        CandidateJobMatcher(_evidence).validate_result(supplied, job)
    paths['match'].write_text(supplied.model_dump_json(), encoding='utf-8')
    paths['output'].write_bytes(b'SYNTHETIC_PREVIOUS_PLAN')
    protected = [*paths.values(), _generation / 'catalog.json', _generation / 'snapshot.json']
    before = {path: path.read_bytes() for path in protected}
    with patch.object(cli_module.PrivateOutput, 'write_json') as write_json:
        rc = _run(_plan_argv(paths), tmp_path, monkeypatch, capsys)
    assert rc == 2, f'{tamper}: inconsistent artifacts were accepted (exit {rc})'
    write_json.assert_not_called()
    assert {path: path.read_bytes() for path in protected} == before


# ------------------------------------------------------ A-02 provenance and snapshot


def _legacy_bundle(tmp_path, record):
    processed = tmp_path / 'data' / 'private' / 'processed'
    processed.mkdir(parents=True, exist_ok=True)
    refs = [record.section_reference] if record.section_reference else []
    evidence = synthetic_candidate_catalog([record]).model_copy(
        update={'section_references': refs}
    )
    analysis = _synthetic_analysis()
    match = CandidateJobMatcher(evidence).assess(analysis)
    paths = {
        'analysis': processed / 'analysis.json',
        'match': processed / 'match.json',
        'candidate': processed / 'candidate.json',
        'output': processed / 'plan.json',
    }
    _write_json(paths['analysis'], json.loads(analysis.model_dump_json()))
    _write_json(paths['match'], json.loads(match.model_dump_json()))
    _write_json(paths['candidate'], json.loads(evidence.model_dump_json()))
    paths['output'].write_bytes(b'SYNTHETIC_PREVIOUS_PLAN')
    return paths


@pytest.mark.parametrize('tamper', [
    'missing_location', 'missing_hash', 'wrong_hash', 'wrong_filename', 'missing_body_index',
])
def test_insufficient_legacy_provenance_is_rejected(
    tmp_path, monkeypatch, capsys, tamper,
):
    item = synthetic_evidence(
        'ev-py', EvidenceType.TECHNICAL_SKILL, 'Python'
    ).model_copy(update={
        'section_reference': 'technical-profile',
        'source_location': SourceLocation(body_block_index=1),
    })
    if tamper == 'missing_location':
        item.source_location = None
    elif tamper == 'missing_hash':
        item.source_sha256 = None
    elif tamper == 'wrong_hash':
        item.source_sha256 = 'b' * 64
    elif tamper == 'wrong_filename':
        item.source_document = 'unrelated-synthetic-source.docx'
    else:
        item.source_location = SourceLocation()
    paths = _legacy_bundle(tmp_path, item)
    before = paths['output'].read_bytes()
    rc = _run(_plan_argv(paths), tmp_path, monkeypatch, capsys)
    assert rc == 2, f'{tamper}: insufficient-provenance legacy plan was published'
    assert paths['output'].read_bytes() == before


@pytest.mark.parametrize('tamper', ['missing_source', 'blank_filename', 'blank_hash', 'invalid_hash'])
def test_legacy_catalog_source_information_is_required(tmp_path, monkeypatch, capsys, tamper):
    item = synthetic_evidence('ev-py', EvidenceType.TECHNICAL_SKILL, 'Python').model_copy(update={
        'section_reference': 'technical-profile',
        'source_location': SourceLocation(body_block_index=0),
    })
    paths = _legacy_bundle(tmp_path, item)
    data = json.loads(paths['candidate'].read_text())
    if tamper == 'missing_source':
        data.pop('source_document')
    elif tamper == 'blank_filename':
        data['source_document']['filename'] = ' '
    elif tamper == 'blank_hash':
        data['source_document']['sha256'] = ''
    else:
        data['source_document']['sha256'] = 'not-a-sha256'
    _write_json(paths['candidate'], data)
    before = {key: path.read_bytes() for key, path in paths.items()}
    with patch.object(cli_module.PrivateOutput, 'write_json') as write_json:
        assert _run(_plan_argv(paths), tmp_path, monkeypatch, capsys) == 2
    write_json.assert_not_called()
    assert {key: path.read_bytes() for key, path in paths.items()} == before


def test_legacy_catalog_with_complete_provenance_still_plans(tmp_path, monkeypatch, capsys):
    item = synthetic_evidence(
        'ev-py', EvidenceType.TECHNICAL_SKILL, 'Python'
    ).model_copy(update={
        'section_reference': 'technical-profile',
        'source_location': SourceLocation(body_block_index=1),
    })
    paths = _legacy_bundle(tmp_path, item)
    rc = _run(_plan_argv(paths), tmp_path, monkeypatch, capsys)
    assert rc == 0
    document = json.loads(paths['output'].read_text(encoding='utf-8'))
    assert document['planning_metadata']['snapshot_available'] is False
    assert document['planning_metadata']['manifest_available'] is False


def test_managed_handoff_succeeds(tmp_path, monkeypatch, capsys):
    paths, _snapshot, _evidence, manifest, generation = _managed_bundle(tmp_path)
    assert manifest is not None
    rc = _run(_plan_argv(paths), tmp_path, monkeypatch, capsys)
    assert rc == 0
    document = json.loads(paths['output'].read_text(encoding='utf-8'))
    assert document['planning_metadata']['snapshot_available'] is True
    assert document['planning_metadata']['manifest_available'] is True
    assert stat.S_IMODE(paths['output'].stat().st_mode) == 0o600
    # Checksum tampering in the committed generation must reject without output.
    before = paths['output'].read_bytes()
    snapshot_path = generation / 'snapshot.json'
    snapshot_path.write_bytes(snapshot_path.read_bytes() + b'x')
    rc = _run(_plan_argv(paths), tmp_path, monkeypatch, capsys)
    assert rc == 2
    assert paths['output'].read_bytes() == before


# ------------------------------------------------------------- A-03 error privacy


@pytest.mark.parametrize('case', ['unknown_id', 'unknown_section', 'duplicate_id'])
def test_controlled_integrity_errors_do_not_echo_private_values(
    tmp_path, monkeypatch, capsys, case,
):
    marker = 'SYNTHETIC_PRIVATE_REVIEW_MARKER'
    processed = tmp_path / 'data' / 'private' / 'processed'
    processed.mkdir(parents=True, exist_ok=True)
    item = synthetic_evidence(
        'ev-py', EvidenceType.TECHNICAL_SKILL, 'Python'
    ).model_copy(update={
        'section_reference': 'technical-profile',
        'source_location': SourceLocation(body_block_index=1),
    })
    if case == 'unknown_section':
        item.section_reference = marker
        evidence = synthetic_candidate_catalog([item]).model_copy(
            update={'section_references': ['technical-profile']}
        )
        match = CandidateJobMatcher(evidence).assess(_synthetic_analysis())
    elif case == 'duplicate_id':
        item.evidence_id = marker
        evidence = synthetic_candidate_catalog(
            [item, item.model_copy(deep=True)]
        ).model_copy(update={'section_references': ['technical-profile']})
        match = CandidateJobMatcher(evidence).assess(_synthetic_analysis())
    else:
        evidence = synthetic_candidate_catalog([item]).model_copy(
            update={'section_references': ['technical-profile']}
        )
        match = CandidateJobMatcher(evidence).assess(_synthetic_analysis())
        data = match.model_dump(mode='json')
        data['requirements'][0]['matched_evidence_ids'] = [marker]
        data['requirements'][0]['matched_evidence'][0]['evidence_id'] = marker
        data['requirements'][0]['components'][0]['matched_evidence_ids'] = [marker]
        match = CandidateJobMatch.model_validate_json(json.dumps(data))

    paths = {
        'analysis': processed / 'analysis.json',
        'match': processed / 'match.json',
        'candidate': processed / 'candidate.json',
        'output': processed / 'plan.json',
    }
    analysis = _synthetic_analysis()
    _write_json(paths['analysis'], json.loads(analysis.model_dump_json()))
    _write_json(paths['match'], json.loads(match.model_dump_json()))
    _write_json(paths['candidate'], json.loads(evidence.model_dump_json()))
    paths['output'].write_bytes(b'SYNTHETIC_PREVIOUS_PLAN')
    before = {key: path.read_bytes() for key, path in paths.items()}

    stdout, stderr = io.StringIO(), io.StringIO()
    monkeypatch.setattr(cli_module, 'PROJECT_ROOT', tmp_path)
    with redirect_stdout(stdout), redirect_stderr(stderr):
        rc = cli_module.main(_plan_argv(paths))
    console = stdout.getvalue() + stderr.getvalue()
    assert rc == 2
    assert marker not in console, 'Controlled planner error leaked an input-supplied private value'
    assert {key: path.read_bytes() for key, path in paths.items()} == before


@pytest.mark.parametrize('artifact', ['analysis', 'match', 'candidate'])
@pytest.mark.parametrize('failure', ['malformed', 'duplicate_key', 'private_schema', 'validation'])
def test_artifact_parse_and_validation_errors_are_sanitized(
    tmp_path, monkeypatch, capsys, artifact, failure,
):
    analysis, match, candidate, output, _plan = _build_artifacts(tmp_path)
    paths = {'analysis': analysis, 'match': match, 'candidate': candidate, 'output': output}
    marker = 'SYNTHETIC_PRIVATE_REVIEW_MARKER'
    data = json.loads(paths[artifact].read_text())
    if failure == 'malformed':
        text = '{"private": "' + marker + '"'
    elif failure == 'duplicate_key':
        text = '{"' + marker + '": 1, "' + marker + '": 2}'
    elif failure == 'private_schema':
        data['schema_version'] = marker
        text = json.dumps(data)
    else:
        if artifact == 'analysis':
            data['required_skills'][0]['name'] = [marker]
        elif artifact == 'match':
            data['requirements'][0]['status'] = marker
        else:
            data['evidence_records'][0]['original_content'] = [marker]
        text = json.dumps(data)
    paths[artifact].write_text(text, encoding='utf-8')
    output.write_bytes(b'SYNTHETIC_PREVIOUS_PLAN')
    before = {key: path.read_bytes() for key, path in paths.items()}
    with patch.object(cli_module.PrivateOutput, 'write_json') as write_json:
        assert _run(_plan_argv(paths), tmp_path, monkeypatch, capsys) == 2
    captured = capsys.readouterr()
    assert marker not in captured.out
    assert marker not in captured.err
    assert 'error:' in captured.out
    write_json.assert_not_called()
    assert {key: path.read_bytes() for key, path in paths.items()} == before


@pytest.mark.parametrize('artifact', ['catalog', 'snapshot', 'manifest'])
def test_managed_artifact_validation_errors_are_sanitized(tmp_path, monkeypatch, capsys, artifact):
    paths, _snapshot, _catalog, _manifest, generation = _managed_bundle(tmp_path)
    marker = 'SYNTHETIC_PRIVATE_REVIEW_MARKER'
    manifest_data = json.loads(paths['candidate'].read_text())
    if artifact == 'manifest':
        manifest_data['generation_id'] = [marker]
    else:
        path = generation / f'{artifact}.json'
        data = json.loads(path.read_text())
        if artifact == 'catalog':
            data['payload']['evidence_records'][0]['source_location']['body_block_index'] = marker
        else:
            data['payload']['statistics'] = marker
        # Reach Pydantic validation rather than the earlier checksum gate.
        _write_json(path, data)
        content = path.read_bytes()
        manifest_data[artifact]['sha256'] = hashlib.sha256(content).hexdigest()
        manifest_data[artifact]['byte_length'] = len(content)
    _write_json(paths['candidate'], manifest_data)
    paths['output'].write_bytes(b'SYNTHETIC_PREVIOUS_PLAN')
    protected = [*paths.values(), generation / 'catalog.json', generation / 'snapshot.json']
    before = {path: path.read_bytes() for path in protected}
    capsys.readouterr()
    assert _run(_plan_argv(paths), tmp_path, monkeypatch, capsys) == 2
    captured = capsys.readouterr()
    assert 'artifact validation failed' in captured.out
    assert marker not in captured.out
    assert marker not in captured.err
    assert {path: path.read_bytes() for path in protected} == before


# ------------------------------------------- A-04 strict parsing and exit codes


@pytest.mark.parametrize('case', ['missing_version', 'duplicate_key'])
def test_analysis_json_requires_explicit_unambiguous_version(
    tmp_path, monkeypatch, capsys, case,
):
    analysis_path, match_path, catalog_path, output_path, _plan = _build_artifacts(tmp_path)
    if case == 'missing_version':
        data = json.loads(analysis_path.read_text())
        data.pop('schema_version')
        analysis_path.write_text(json.dumps(data), encoding='utf-8')
    else:
        text = analysis_path.read_text().replace(
            '"schema_version": "2.0.0"',
            '"schema_version": "1.0.0", "schema_version": "2.0.0"',
        )
        analysis_path.write_text(text, encoding='utf-8')
    output_path.write_bytes(b'SYNTHETIC_PREVIOUS_PLAN')
    rc = _run(
        _plan_argv({
            'analysis': analysis_path, 'match': match_path,
            'candidate': catalog_path, 'output': output_path,
        }),
        tmp_path, monkeypatch, capsys,
    )
    assert rc == 2, f'{case}: ambiguous/unversioned job analysis was accepted'
    assert output_path.read_bytes() == b'SYNTHETIC_PREVIOUS_PLAN'


@pytest.mark.parametrize('case', ['old_version', 'missing_version'])
def test_match_version_errors_are_expected_input_errors(
    tmp_path, monkeypatch, capsys, case,
):
    analysis_path, match_path, catalog_path, output_path, _plan = _build_artifacts(tmp_path)
    data = json.loads(match_path.read_text())
    if case == 'old_version':
        data['schema_version'] = '1.0.0'
    else:
        data.pop('schema_version')
    match_path.write_text(json.dumps(data), encoding='utf-8')
    output_path.write_bytes(b'SYNTHETIC_PREVIOUS_PLAN')
    rc = _run(
        _plan_argv({
            'analysis': analysis_path, 'match': match_path,
            'candidate': catalog_path, 'output': output_path,
        }),
        tmp_path, monkeypatch, capsys,
    )
    assert rc == 2, f'{case}: expected version error incorrectly returned exit {rc}'
    assert output_path.read_bytes() == b'SYNTHETIC_PREVIOUS_PLAN'


@pytest.mark.parametrize('candidate_json', ['[]', 'null', 'true', '42', '"SYNTHETIC_PRIVATE_REVIEW_MARKER"'])
def test_nonobject_candidate_json_is_an_expected_input_error(tmp_path, monkeypatch, capsys, candidate_json):
    analysis_path, match_path, catalog_path, output_path, _plan = _build_artifacts(tmp_path)
    catalog_path.write_text(candidate_json, encoding='utf-8')
    output_path.write_bytes(b'SYNTHETIC_PREVIOUS_PLAN')
    rc = _run(
        _plan_argv({
            'analysis': analysis_path, 'match': match_path,
            'candidate': catalog_path, 'output': output_path,
        }),
        tmp_path, monkeypatch, capsys,
    )
    assert rc == 2, f'Nonobject candidate JSON incorrectly returned exit {rc}'
    captured = capsys.readouterr()
    assert 'must be a JSON object' in captured.out
    assert 'SYNTHETIC_PRIVATE_REVIEW_MARKER' not in captured.out + captured.err
    assert output_path.read_bytes() == b'SYNTHETIC_PREVIOUS_PLAN'


@pytest.mark.parametrize('candidate_json', ['[]', 'null', 'true', '42', '"SYNTHETIC_PRIVATE_REVIEW_MARKER"'])
def test_match_job_rejects_nonobject_candidate_json(tmp_path, monkeypatch, capsys, candidate_json):
    analysis_path, match_path, catalog_path, output_path, _plan = _build_artifacts(tmp_path)
    catalog_path.write_text(candidate_json, encoding='utf-8')
    output_path.write_bytes(b'SYNTHETIC_PREVIOUS_MATCH')
    rc = _run(
        [
            'match-job',
            '--analysis', str(analysis_path),
            '--candidate-evidence', str(catalog_path),
            '--output', str(output_path),
        ],
        tmp_path, monkeypatch, capsys,
    )
    assert rc == 2, f'match-job accepted nonobject candidate JSON (exit {rc})'
    captured = capsys.readouterr()
    assert 'must be a JSON object' in captured.out
    assert 'SYNTHETIC_PRIVATE_REVIEW_MARKER' not in captured.out + captured.err
    assert output_path.read_bytes() == b'SYNTHETIC_PREVIOUS_MATCH'


def test_planner_candidate_loader_preserves_standalone_contract_and_captures_once(tmp_path, monkeypatch, capsys):
    analysis, match, candidate, output, _plan = _build_artifacts(tmp_path)
    monkeypatch.setattr(cli_module, 'PROJECT_ROOT', tmp_path)
    standalone = cli_module._load_candidate_evidence_artifact(candidate)
    real_capture = private_input._read_local_bytes
    captures = []

    def capture(path, **kwargs):
        captures.append(Path(path))
        return real_capture(path, **kwargs)

    monkeypatch.setattr(private_input, '_read_local_bytes', capture)
    assert cli_module.main(_plan_argv({
        'analysis': analysis, 'match': match, 'candidate': candidate, 'output': output,
    })) == 0
    assert captures == [analysis, match, candidate]
    document = json.loads(output.read_text())
    assert document['planning_metadata']['source_candidate_source_sha256'] == standalone.source_document.sha256


def test_valid_match_job_operation_remains_compatible(tmp_path, monkeypatch, capsys):
    analysis, _match, candidate, output, _plan = _build_artifacts(tmp_path)
    before = (analysis.read_bytes(), candidate.read_bytes())
    assert _run([
        'match-job', '--analysis', str(analysis), '--candidate-evidence', str(candidate),
        '--output', str(output),
    ], tmp_path, monkeypatch, capsys) == 0
    result = CandidateJobMatch.model_validate_json(output.read_bytes())
    job = JobDescriptionAnalysis.model_validate_json(analysis.read_bytes())
    catalog = cli_module._load_candidate_evidence_artifact(candidate)
    assert CandidateJobMatcher(catalog).validate_result(result, job) == result
    assert (analysis.read_bytes(), candidate.read_bytes()) == before
    assert stat.S_IMODE(output.stat().st_mode) == 0o600


@pytest.mark.parametrize('failure', [RuntimeError, OSError, FileNotFoundError])
def test_unexpected_planner_failures_remain_exit_three_and_sanitized(tmp_path, monkeypatch, capsys, failure):
    analysis, match, candidate, output, _plan = _build_artifacts(tmp_path)
    paths = {'analysis': analysis, 'match': match, 'candidate': candidate, 'output': output}
    output.write_bytes(b'SYNTHETIC_PREVIOUS_PLAN')
    before = {key: path.read_bytes() for key, path in paths.items()}
    with patch.object(cli_module.SectionPlanner, 'plan', side_effect=failure('SYNTHETIC_PRIVATE_REVIEW_MARKER')) as fault:
        assert _run(_plan_argv(paths), tmp_path, monkeypatch, capsys) == 3
    fault.assert_called_once()
    captured = capsys.readouterr()
    assert 'SYNTHETIC_PRIVATE_REVIEW_MARKER' not in captured.out + captured.err
    assert {key: path.read_bytes() for key, path in paths.items()} == before
    assert not list(output.parent.glob('.resume-assistant-*.tmp'))


@pytest.mark.parametrize('artifact,label', [
    ('analysis', 'Job analysis artifact input'),
    ('match', 'Candidate job match artifact input'),
    ('candidate', 'Candidate evidence artifact input'),
])
def test_sa_r01_missing_required_input_is_controlled(tmp_path, monkeypatch, capsys, artifact, label):
    analysis, match, candidate, output, _plan = _build_artifacts(tmp_path)
    paths = {'analysis': analysis, 'match': match, 'candidate': candidate, 'output': output}
    output.write_bytes(b'SYNTHETIC_PREVIOUS_PLAN')
    before = {key: path.read_bytes() for key, path in paths.items()}
    supplied = {**paths, artifact: paths[artifact].with_name('SYNTHETIC_PRIVATE_MISSING_INPUT.json')}
    with patch.object(cli_module.PrivateOutput, 'write_json') as write_json:
        assert _run(_plan_argv(supplied), tmp_path, monkeypatch, capsys) == 2
    captured = capsys.readouterr()
    assert label + ' file could not be found' in captured.out
    assert 'SYNTHETIC_PRIVATE_MISSING_INPUT' not in captured.out + captured.err
    write_json.assert_not_called()
    assert {key: path.read_bytes() for key, path in paths.items()} == before
    assert not list(output.parent.glob('.resume-assistant-*.tmp'))


@pytest.mark.parametrize('artifact', ['catalog', 'snapshot'])
def test_sa_r01_missing_generation_input_during_read_is_controlled(tmp_path, monkeypatch, capsys, artifact):
    paths, _snapshot, _catalog, _manifest, generation = _managed_bundle(tmp_path)
    output = paths['output']
    output.write_bytes(b'SYNTHETIC_PREVIOUS_PLAN')
    protected = [*paths.values(), generation / 'catalog.json', generation / 'snapshot.json']
    before = {path: path.read_bytes() for path in protected}
    missing = generation / f'{artifact}.json'
    real_read_bytes = Path.read_bytes
    reached = []

    def missing_at_open(path):
        if path == missing:
            # The store's existence check has succeeded; simulate disappearance
            # at the actual input read without changing the fixture's bytes.
            reached.append(path)
            raise FileNotFoundError(errno.ENOENT, 'SYNTHETIC_PRIVATE_READ_FAILURE', str(path))
        return real_read_bytes(path)

    with patch.object(Path, 'read_bytes', missing_at_open), \
            patch.object(cli_module.PrivateOutput, 'write_json') as write_json:
        assert _run(_plan_argv(paths), tmp_path, monkeypatch, capsys) == 2
    captured = capsys.readouterr()
    assert reached == [missing]
    assert 'Candidate evidence artifact input file could not be found' in captured.out
    assert 'SYNTHETIC_PRIVATE_READ_FAILURE' not in captured.out + captured.err
    assert str(missing) not in captured.out + captured.err
    write_json.assert_not_called()
    assert {path: path.read_bytes() for path in protected} == before
    assert not list(output.parent.glob('.resume-assistant-*.tmp'))


@pytest.mark.parametrize('method', ['model_dump_json', 'model_validate_json'])
def test_sa_r01_unexpected_serialization_file_not_found_is_exit_three(tmp_path, monkeypatch, capsys, method):
    analysis, match, candidate, output, _plan = _build_artifacts(tmp_path)
    paths = {'analysis': analysis, 'match': match, 'candidate': candidate, 'output': output}
    output.write_bytes(b'SYNTHETIC_PREVIOUS_PLAN')
    before = {key: path.read_bytes() for key, path in paths.items()}
    marker = 'SYNTHETIC_PRIVATE_SERIALIZATION_FAILURE'
    private_path = str(output.with_name(marker + '.json'))
    failure = FileNotFoundError(errno.ENOENT, marker, private_path)
    with patch.object(cli_module.ResumeSectionPlan, method, side_effect=failure) as fault, \
            patch.object(cli_module.PrivateOutput, 'write_json') as write_json:
        assert _run(_plan_argv(paths), tmp_path, monkeypatch, capsys) == 3
    captured = capsys.readouterr()
    fault.assert_called_once()
    write_json.assert_not_called()
    assert 'section planning failed: OSError' in captured.out
    assert marker not in captured.out + captured.err
    assert private_path not in captured.out + captured.err
    assert {key: path.read_bytes() for key, path in paths.items()} == before
    assert not list(output.parent.glob('.resume-assistant-*.tmp'))


# ------------------------------------------------------- A-05 output alias safety


def _alias_bundle(tmp_path, alias):
    if alias.startswith('managed'):
        paths, _snapshot, _evidence, _manifest, generation = _managed_bundle(tmp_path)
        targets = {
            'managed_manifest': paths['candidate'],
            'managed_generation': generation / 'catalog.json',
            'managed_snapshot': generation / 'snapshot.json',
        }
        output = targets[alias]
        protected = [generation / 'catalog.json', generation / 'snapshot.json', tmp_path / 'synthetic.docx']
    else:
        analysis_path, match_path, catalog_path, output_path, _plan = _build_artifacts(tmp_path)
        paths = {
            'analysis': analysis_path,
            'match': match_path,
            'candidate': catalog_path,
            'output': output_path,
        }
        output = paths[alias]
        protected = []
    paths['output'].write_bytes(b'SYNTHETIC_PREVIOUS_PLAN')
    return paths, output, [*paths.values(), *protected]


@pytest.mark.parametrize('alias', [
    'analysis', 'match', 'candidate', 'managed_manifest', 'managed_generation', 'managed_snapshot',
])
def test_output_aliases_do_not_overwrite_input_artifacts(tmp_path, monkeypatch, capsys, alias):
    paths, output, protected = _alias_bundle(tmp_path, alias)
    before = {path: path.read_bytes() for path in protected}
    with patch.object(cli_module.PrivateOutput, 'write_json') as write_json:
        rc = _run(_plan_argv(paths, output=output), tmp_path, monkeypatch, capsys)
    assert rc == 2, f'{alias}: CLI overwrote an input artifact with a section plan (exit {rc})'
    write_json.assert_not_called()
    assert {path: path.read_bytes() for path in protected} == before


@pytest.mark.parametrize('alias', [
    'analysis', 'match', 'candidate', 'managed_manifest', 'managed_generation', 'managed_snapshot',
])
@pytest.mark.parametrize('form', [
    'relative_output', 'dotdot_output', 'relative_input', 'symlink_output', 'hardlink_output',
])
def test_alias_path_forms_preserve_every_protected_artifact(tmp_path, monkeypatch, capsys, alias, form):
    paths, target, protected = _alias_bundle(tmp_path, alias)
    monkeypatch.chdir(tmp_path)
    output = target
    if form == 'relative_output':
        output = target.relative_to(tmp_path)
    elif form == 'dotdot_output':
        output = target.parent / '..' / target.parent.name / target.name
    elif form == 'relative_input':
        paths = {key: path.relative_to(tmp_path) for key, path in paths.items()}
    else:
        output = target.parent / 'alias-output.json'
        if form == 'symlink_output':
            output.symlink_to(target)
        else:
            output.hardlink_to(target)
        protected.append(output)
    before = {path: path.read_bytes() for path in protected}
    with patch.object(cli_module.PrivateOutput, 'write_json') as write_json:
        assert _run(_plan_argv(paths, output=output), tmp_path, monkeypatch, capsys) == 2
    write_json.assert_not_called()
    assert {path: path.read_bytes() for path in protected} == before
    if form in ('symlink_output', 'hardlink_output'):
        assert output.samefile(target)
    if form == 'symlink_output':
        assert output.is_symlink()


@pytest.mark.parametrize('alias', ['analysis', 'match', 'candidate'])
def test_explicit_alias_is_rejected_before_input_reads_and_output_handles(tmp_path, monkeypatch, capsys, alias):
    paths, output, protected = _alias_bundle(tmp_path, alias)
    before = {path: path.read_bytes() for path in protected}
    with patch.object(cli_module, '_load_job_analysis_artifact') as loader, \
            patch.object(cli_module.PrivateOutput, '__enter__') as open_target:
        assert _run(_plan_argv(paths, output=output), tmp_path, monkeypatch, capsys) == 2
    loader.assert_not_called()
    open_target.assert_not_called()
    assert {path: path.read_bytes() for path in protected} == before


@pytest.mark.parametrize('alias', [
    'analysis', 'match', 'candidate', 'managed_manifest', 'managed_generation', 'managed_snapshot',
])
def test_alias_introduced_during_planning_is_rejected_before_publication(tmp_path, monkeypatch, capsys, alias):
    paths, target, protected = _alias_bundle(tmp_path, alias)
    output = paths['output'].with_name('late-output.json')
    before = {path: path.read_bytes() for path in protected}
    real_plan = cli_module.SectionPlanner.plan
    injected = []

    def plan_then_alias(*args, **kwargs):
        plan = real_plan(*args, **kwargs)
        output.hardlink_to(target)
        injected.append(True)
        return plan

    with patch.object(cli_module.SectionPlanner, 'plan', plan_then_alias), \
            patch.object(cli_module.PrivateOutput, 'write_json') as write_json:
        assert _run(_plan_argv(paths, output=output), tmp_path, monkeypatch, capsys) == 2
    assert injected == [True]
    write_json.assert_not_called()
    assert output.samefile(target)
    assert output.read_bytes() == before[target]
    assert {path: path.read_bytes() for path in protected} == before


def test_manifest_alias_protection_uses_the_actual_address(tmp_path, monkeypatch, capsys):
    paths, actual_manifest, protected = _alias_bundle(tmp_path, 'managed_manifest')
    data = json.loads(actual_manifest.read_text())
    data['evidence_output_selector'] = str(paths['output'])
    data['snapshot_output_selector'] = str(paths['output'])
    _write_json(actual_manifest, data)
    # Selectors are metadata, so they must not reserve an unrelated output.
    inputs = [path for path in protected if path != paths['output']]
    before = {path: path.read_bytes() for path in inputs}
    assert _run(_plan_argv(paths), tmp_path, monkeypatch, capsys) == 0
    assert {path: path.read_bytes() for path in inputs} == before
    protected_before_alias = {path: path.read_bytes() for path in protected}
    assert _run(_plan_argv(paths, output=actual_manifest), tmp_path, monkeypatch, capsys) == 2
    assert {path: path.read_bytes() for path in protected} == protected_before_alias


@pytest.mark.parametrize('artifact', ['analysis', 'match', 'candidate'])
def test_invalid_input_path_identity_is_a_sanitized_input_error(tmp_path, monkeypatch, capsys, artifact):
    analysis, match, candidate, output, _plan = _build_artifacts(tmp_path)
    paths = {'analysis': analysis, 'match': match, 'candidate': candidate, 'output': output}
    output.write_bytes(b'SYNTHETIC_PREVIOUS_PLAN')
    before = {path: path.read_bytes() for path in paths.values()}
    paths[artifact] = str(paths[artifact]) + '\x00SYNTHETIC_PRIVATE_REVIEW_MARKER'
    assert _run(_plan_argv(paths), tmp_path, monkeypatch, capsys) == 2
    captured = capsys.readouterr()
    assert 'SYNTHETIC_PRIVATE_REVIEW_MARKER' not in captured.out + captured.err
    assert {path: path.read_bytes() for path in before} == before


@pytest.mark.parametrize('artifact', ['analysis', 'match', 'candidate', 'output'])
def test_control_symlink_files_are_rejected(tmp_path, monkeypatch, capsys, artifact):
    analysis, match, candidate, output, _plan = _build_artifacts(tmp_path)
    paths = {'analysis': analysis, 'match': match, 'candidate': candidate, 'output': output}
    output.write_bytes(b'SYNTHETIC_PREVIOUS_PLAN')
    before = {path: path.read_bytes() for path in paths.values()}
    link = paths[artifact].with_name('symlink-' + paths[artifact].name)
    link.symlink_to(paths[artifact])
    supplied = {**paths, artifact: link}
    assert _run(_plan_argv(supplied), tmp_path, monkeypatch, capsys) == 2
    assert link.is_symlink()
    assert {path: path.read_bytes() for path in before} == before


def test_output_outside_boundary_is_rejected(tmp_path, monkeypatch, capsys):
    analysis_path, match_path, catalog_path, _output, _plan = _build_artifacts(tmp_path)
    outside = tmp_path / 'outside-boundary.json'
    rc = _run(
        _plan_argv({
            'analysis': analysis_path, 'match': match_path,
            'candidate': catalog_path, 'output': outside,
        }),
        tmp_path, monkeypatch, capsys,
    )
    assert rc == 2
    assert not outside.exists()


@pytest.mark.parametrize('failure', [OSError, FileNotFoundError])
def test_atomic_replace_failure_preserves_inputs_and_previous_output(tmp_path, monkeypatch, capsys, failure):
    analysis_path, match_path, catalog_path, output_path, _plan = _build_artifacts(tmp_path)
    paths = {
        'analysis': analysis_path, 'match': match_path,
        'candidate': catalog_path, 'output': output_path,
    }
    # Pre-populate the output with a previous plan so replacement is visible.
    paths['output'].write_bytes(b'SYNTHETIC_PREVIOUS_PLAN')
    before = {key: path.read_bytes() for key, path in paths.items()}
    private_path = str(output_path.with_name('SYNTHETIC_PRIVATE_PUBLICATION_PATH.json'))
    error = (
        FileNotFoundError(errno.ENOENT, 'SYNTHETIC_MARKER', private_path)
        if failure is FileNotFoundError else OSError('SYNTHETIC_MARKER')
    )
    with patch('resume_assistant.private_output.os.replace', side_effect=error) as replace:
        rc = _run(_plan_argv(paths), tmp_path, monkeypatch, capsys)
    captured = capsys.readouterr()
    replace.assert_called_once()
    assert rc == 3
    assert 'SYNTHETIC_MARKER' not in captured.out + captured.err
    assert private_path not in captured.out + captured.err
    assert {key: path.read_bytes() for key, path in paths.items()} == before
    assert not list(paths['output'].parent.glob('.resume-assistant-*.tmp'))
