'''Portable independent Mission 3.3 acceptance matrix adopted at Stage E.

The external review suite remains untouched. Its four legacy assertions are
represented here by stronger current-contract controls: filtered empty sections
are omitted, and entry budgets count source rows rather than arbitrary prose.
'''

import copy
import hashlib
import io
import json
import os
import stat
import subprocess
import sys
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest
from docx import Document
from pydantic import ValidationError

from resume_assistant import cli
from resume_assistant.evidence import build_evidence_catalog
from resume_assistant.extractor import MasterResumeExtractor
from resume_assistant.job_models import JobDescriptionAnalysis
from resume_assistant.matching import CandidateJobIntegrityError, CandidateJobMatcher
from resume_assistant.matching_models import CandidateJobMatch
from resume_assistant.models import DocumentSection, DocumentSnapshot, DocumentStatistics, EvidenceRecord, EvidenceType, SourceLocation
from resume_assistant.publication_reader import load_committed_pair
from resume_assistant.section_planner import DEFAULT_BUDGETS, PlanBudgets, ResumeSectionPlan, SectionPlanner, SectionPlannerError
from tests.fixtures import builders
from tests.fixtures.jobs import empty_job_payload, synthetic_candidate_catalog

TECH = 'technical-profile'
EXPERIENCE = 'professional-experience-and-engineering-training'
PROJECTS = 'independent-software-and-ai-projects'
MARKER = 'SYNTHETIC_PRIVATE_REVIEW_MARKER'


def record(label, text, kind=EvidenceType.TECHNICAL_SKILL, section=TECH, order=1):
    return EvidenceRecord(
        evidence_id=hashlib.sha256(label.encode()).hexdigest(), evidence_type=kind,
        original_content=text, section_reference=section,
        source_document='synthetic-master-resume.docx', source_sha256='a' * 64,
        source_location=SourceLocation(body_block_index=order, paragraph_index=order),
    )


def catalog(records, **coverage):
    refs = list(dict.fromkeys(item.section_reference for item in records if item.section_reference))
    value = synthetic_candidate_catalog(records, sections_detected=len(refs), **coverage)
    value.section_references = refs
    return value


def analysis(required=(), preferred=(), uncertain=()):
    data = empty_job_payload()
    for category, names in (('required_skills', required), ('preferred_skills', preferred), ('uncertain_skills', uncertain)):
        data[category] = [
            {'name': name, 'quotation': f'{category}: {name}.', 'classification_context': f'{category}: {name}.'}
            for name in names
        ]
    return JobDescriptionAnalysis.model_validate({**data, 'prompt_version': '2.0.0'})


def match_for(job, evidence):
    return CandidateJobMatcher(evidence).assess(job)


def plan_for(evidence=None, job=None, match=None, budgets=None):
    evidence = catalog([record('python', 'Python')]) if evidence is None else evidence
    job = analysis(required=('Python',)) if job is None else job
    match = match_for(job, evidence) if match is None else match
    return SectionPlanner(budgets).plan(job, match, evidence)


def write_bundle(root, evidence=None, job=None, match=None):
    evidence = catalog([record('python', 'Python')]) if evidence is None else evidence
    job = analysis(required=('Python',)) if job is None else job
    match = match_for(job, evidence) if match is None else match
    processed = root / 'data/private/processed'
    processed.mkdir(parents=True, exist_ok=True)
    paths = {name: processed / f'{name}.json' for name in ('analysis', 'match', 'candidate', 'output')}
    for key, item in (('analysis', job), ('match', match), ('candidate', evidence)):
        paths[key].write_text(item.model_dump_json(), encoding='utf-8')
    paths['output'].write_bytes(b'SYNTHETIC_PREVIOUS_PLAN')
    return paths


def invoke(root, paths, output=None):
    stdout, stderr = io.StringIO(), io.StringIO()
    with patch.object(cli, 'PROJECT_ROOT', root), redirect_stdout(stdout), redirect_stderr(stderr):
        code = cli.main([
            'plan-sections', '--analysis', str(paths['analysis']), '--match', str(paths['match']),
            '--candidate-evidence', str(paths['candidate']),
            '--output', str(paths['output'] if output is None else output),
        ])
    return code, stdout.getvalue() + stderr.getvalue()


def managed_bundle(root):
    document = Document()
    document.add_heading('1. Professional Identity', level=1)
    document.add_paragraph('Software engineer building local synthetic systems.')
    document.add_heading('2. Technical Profile', level=1)
    document.add_paragraph('Python and SQL')
    source = builders.save(document, root / 'synthetic.docx')
    processed = root / 'data/private/processed'
    candidate_path = processed / 'candidate.json'
    with patch.object(cli, 'PROJECT_ROOT', root):
        cli.run_extract_master(source, candidate_path, processed / 'snapshot-selector.json')
        snapshot, evidence, manifest, _ = load_committed_pair(root, candidate_path)
    job = analysis(required=('Python',))
    paths = {'analysis': processed / 'analysis.json', 'match': processed / 'match.json', 'candidate': candidate_path, 'output': processed / 'plan.json'}
    paths['analysis'].write_text(job.model_dump_json(), encoding='utf-8')
    with patch.object(cli, 'PROJECT_ROOT', root):
        cli.run_match_job(paths['analysis'], candidate_path, paths['match'])
    generation = processed / '.extractions' / manifest.bundle_key / 'generations' / manifest.generation_id
    return paths, snapshot, evidence, manifest, generation


@pytest.mark.parametrize('tamper', ['foreign_job', 'wrong_candidate_hash', 'wrong_record_count', 'fake_requirement_id', 'wrong_source_index', 'wrong_provenance', 'missing_requirements', 'forged_skill_support'])
def test_cross_artifact_mismatch_is_rejected(tmp_path, tamper):
    evidence = catalog([record('python', 'Python'), record('k8s', 'Kubernetes', order=2)])
    job = analysis(required=('Python',))
    data = match_for(job, evidence).model_dump(mode='json')
    if tamper == 'foreign_job':
        data = match_for(analysis(required=('Kubernetes',)), evidence).model_dump(mode='json')
    elif tamper == 'wrong_candidate_hash':
        data['candidate_source_sha256'] = 'b' * 64
    elif tamper == 'wrong_record_count':
        data['candidate_evidence_record_count'] = 999
    elif tamper == 'fake_requirement_id':
        data['requirements'][0]['requirement_id'] = 'f' * 64
    elif tamper == 'wrong_source_index':
        data['requirements'][0]['source_index'] = 999
    elif tamper == 'wrong_provenance':
        reference = data['requirements'][0]['matched_evidence'][0]
        reference['source_sha256'] = 'b' * 64
        reference['source_location']['body_block_index'] = 999
        reference['source_document'] = 'another-synthetic-document.docx'
    elif tamper == 'missing_requirements':
        data['requirements'] = []
    else:
        job = analysis(required=('Kubernetes',))
        evidence = catalog([record('python', 'Python')])
        data = match_for(job, catalog([record('python', 'Kubernetes')])).model_dump(mode='json')
    supplied = CandidateJobMatch.model_validate_json(json.dumps(data))
    with pytest.raises(CandidateJobIntegrityError):
        CandidateJobMatcher(evidence).validate_result(supplied, job)
    paths = write_bundle(tmp_path, evidence, job, supplied)
    assert invoke(tmp_path, paths)[0] == 2
    assert paths['output'].read_bytes() == b'SYNTHETIC_PREVIOUS_PLAN'


@pytest.mark.parametrize('tamper', ['missing_location', 'missing_hash', 'wrong_hash', 'wrong_filename'])
def test_insufficient_legacy_provenance_is_rejected(tmp_path, tamper):
    item = record('python', 'Python')
    if tamper == 'missing_location':
        item.source_location = None
    elif tamper == 'missing_hash':
        item.source_sha256 = None
    elif tamper == 'wrong_hash':
        item.source_sha256 = 'b' * 64
    else:
        item.source_document = 'unrelated-synthetic-source.docx'
    paths = write_bundle(tmp_path, catalog([item]))
    _, loaded, manifest, _ = load_committed_pair(tmp_path, paths['candidate'])
    assert loaded.evidence_records and manifest is None
    assert invoke(tmp_path, paths)[0] == 2


def test_service_rejects_a_mismatched_snapshot():
    evidence = catalog([record('python', 'Python')])
    snapshot = DocumentSnapshot(
        source_filename='another-synthetic-source.docx', source_sha256='b' * 64,
        extraction_timestamp=datetime(2026, 11, 24, tzinfo=timezone.utc),
        statistics=DocumentStatistics(), blocks=[],
        sections=[DocumentSection(identifier=TECH, heading_text='Technical Profile')],
    )
    job = analysis(required=('Python',))
    with pytest.raises((SectionPlannerError, ValueError)):
        SectionPlanner().plan(job, match_for(job, evidence), evidence, snapshot)


def test_partial_skill_is_not_promoted_to_the_full_compound():
    evidence = catalog([record('python', 'Python')])
    job = analysis(required=('Python and SQL',))
    matched = match_for(job, evidence)
    assert matched.requirements[0].status == 'partially_supported'
    assert matched.requirements[0].unmatched_components == ['SQL']
    planned = plan_for(evidence, job, matched)
    assert 'Python and SQL' not in [skill.skill_text for skill in planned.prioritized_skills]
    assert 'Python and SQL' not in planned.professional_summary_blueprint.technical_capability_skill_texts


def test_same_skill_in_distinct_job_contexts_is_not_duplicated():
    data = empty_job_payload()
    data['required_skills'] = [
        {'name': 'Python', 'quotation': 'Required: Python for services.', 'classification_context': 'Required: Python for services.'},
        {'name': 'Python', 'quotation': 'Required: Python for data.', 'classification_context': 'Required: Python for data.'},
    ]
    job = JobDescriptionAnalysis.model_validate({**data, 'prompt_version': '2.0.0'})
    assert len(job.required_skills) == 2
    labels = [skill.skill_text.casefold().strip() for skill in plan_for(job=job).prioritized_skills]
    assert len(labels) == len(set(labels))


def test_contact_is_not_selected_as_technical_content():
    item = record('contact', 'Email: synthetic@example.invalid', EvidenceType.CONTACT, TECH)
    planned = plan_for(catalog([item]), analysis())
    assert planned.selected_evidence_ids == []
    assert planned.ordered_sections == []
    assert [(excluded.evidence_id, excluded.reason) for excluded in planned.excluded_evidence] == [(item.evidence_id, 'content_not_eligible_for_output_section')]


@pytest.mark.parametrize('tamper', ['unknown_selection', 'duplicate_selection', 'unknown_source_section', 'duplicate_requirement', 'unknown_skill_requirement', 'unsupported_blueprint', 'wrong_item_count', 'selected_and_excluded', 'wrong_flat_union', 'unknown_output_section', 'empty_skill_evidence', 'page_guarantee', 'coercive_types'])
def test_output_schema_rejects_invalid_states(tamper):
    data = plan_for().model_dump(mode='json')
    technical = next(section for section in data['ordered_sections'] if section['section_id'] == 'technical-skills')
    selected = data['selected_evidence_ids'][0]
    if tamper == 'unknown_selection':
        data['selected_evidence_ids'].append('unknown-evidence')
    elif tamper == 'duplicate_selection':
        data['selected_evidence_ids'].append(selected)
        technical['selected_evidence_ids'].append(selected)
        technical['item_count'] = len(technical['selected_evidence_ids'])
    elif tamper == 'unknown_source_section':
        technical['source_section_references'].append('phantom-section')
    elif tamper == 'duplicate_requirement':
        data['requirement_coverage'].append(copy.deepcopy(data['requirement_coverage'][0]))
    elif tamper == 'unknown_skill_requirement':
        data['prioritized_skills'][0]['requirement_id'] = 'f' * 64
    elif tamper == 'unsupported_blueprint':
        data['professional_summary_blueprint']['technical_capability_skill_texts'].append('Principal engineer with twenty years of unsupported leadership')
    elif tamper == 'wrong_item_count':
        technical['item_count'] = 999
    elif tamper == 'selected_and_excluded':
        data['excluded_evidence'].append({'evidence_id': selected, 'reason': 'excluded'})
    elif tamper == 'wrong_flat_union':
        data['selected_evidence_ids'] = []
    elif tamper == 'unknown_output_section':
        technical['section_id'] = 'unsupported-output-section'
    elif tamper == 'empty_skill_evidence':
        data['prioritized_skills'][0]['matched_evidence_ids'] = []
    elif tamper == 'page_guarantee':
        data['budgets']['page_guarantee'] = True
    else:
        data['budgets']['max_skills'] = '15'
        data['planning_metadata']['snapshot_available'] = 'false'
    with pytest.raises(ValidationError):
        ResumeSectionPlan.model_validate_json(json.dumps(data))


def test_budget_selection_preserves_required_evidence_before_background():
    items = [record(f'background-{index}', f'Documented background {index}', order=index) for index in range(15)]
    required = record('required-python', 'Python', order=99)
    assert required.evidence_id in plan_for(catalog(items + [required])).selected_evidence_ids


def test_maximum_section_budget_is_enforced():
    planned = plan_for(budgets=PlanBudgets(**{**DEFAULT_BUDGETS, 'max_output_sections': 1}))
    assert len(planned.ordered_sections) <= planned.budgets.max_output_sections


@pytest.mark.parametrize('heading,output,budget_field', [
    ('Professional Experience And Engineering Training', 'professional-experience', 'max_experience_entries'),
    ('Independent Software And AI Projects', 'projects', 'max_projects'),
    ('Certifications And Professional Development', 'certifications', 'max_certifications'),
])
def test_control_source_row_entry_budgets(heading, output, budget_field, tmp_path):
    document = Document()
    document.add_heading('1. ' + heading, level=1)
    table = document.add_table(rows=8, cols=1)
    for index in range(8):
        table.cell(index, 0).text = f'Original documented entry {index}'
    source = builders.save(document, tmp_path / 'numeric-source-rows.docx')
    snapshot = MasterResumeExtractor(source).extract()
    evidence = build_evidence_catalog(snapshot)
    job = analysis()
    config = PlanBudgets(**{**DEFAULT_BUDGETS, budget_field: 2})
    planned = SectionPlanner(config).plan(job, match_for(job, evidence), evidence, snapshot)
    section = next(section for section in planned.ordered_sections if section.section_id == output)
    assert section.item_count == len(section.selected_evidence_ids) == len(section.entries) == 2
    assert section.entry_grouping_complete is True
    selected = set(section.selected_evidence_ids)
    row_records = [record for record in evidence.evidence_records if record.table_context]
    assert section.selected_evidence_ids == [record.evidence_id for record in row_records[:2]]
    assert {item.evidence_id for item in planned.excluded_evidence} == {record.evidence_id for record in evidence.evidence_records} - selected


@pytest.mark.parametrize('kind,reference', [(EvidenceType.CONTACT, None), (EvidenceType.UNCLASSIFIED, None), (EvidenceType.LEADERSHIP, 'leadership-and-demonstrated-competencies')])
def test_unselected_unclassified_and_unmapped_records_have_exclusion_reasons(kind, reference):
    ignored = record('ignored', 'Unselected synthetic background', kind, reference, 2)
    planned = plan_for(catalog([record('python', 'Python'), ignored]))
    assert ignored.evidence_id in {item.evidence_id for item in planned.excluded_evidence}


def test_budget_truncation_has_complete_exclusion_accounting():
    evidence = catalog([record(f'skill-{index}', f'Documented skill {index}', order=index) for index in range(17)])
    planned = plan_for(evidence, analysis())
    assert set(planned.selected_evidence_ids) | {item.evidence_id for item in planned.excluded_evidence} == {item.evidence_id for item in evidence.evidence_records}


def test_empty_sections_are_omitted():
    assert plan_for(catalog([]), analysis()).ordered_sections == []


def test_source_section_lists_describe_surviving_selections():
    items = [record(f'first-{index}', f'Project record {index}', EvidenceType.PROJECT, PROJECTS, index) for index in range(5)]
    items.append(record('second', 'Another project', EvidenceType.PROJECT, 'additional-ai-and-multimodal-applications', 6))
    evidence = catalog(items)
    section = next(section for section in plan_for(evidence, analysis()).ordered_sections if section.section_id == 'projects')
    by_id = {item.evidence_id: item for item in evidence.evidence_records}
    assert set(section.source_section_references) == {by_id[identifier].section_reference for identifier in section.selected_evidence_ids}


def test_table_row_order_is_not_replaced_by_hash_order():
    first = record('earlier', 'Earlier role', EvidenceType.EXPERIENCE, EXPERIENCE, 5)
    later = record('later', 'Later role', EvidenceType.EXPERIENCE, EXPERIENCE, 5)
    first.evidence_id, later.evidence_id = 'f' * 64, '0' * 64
    first.source_location = SourceLocation(body_block_index=5, table_index=5, row_index=0)
    later.source_location = SourceLocation(body_block_index=5, table_index=5, row_index=1)
    section = next(section for section in plan_for(catalog([first, later]), analysis()).ordered_sections if section.section_id == 'professional-experience')
    assert section.selected_evidence_ids == [first.evidence_id, later.evidence_id]


def test_selected_evidence_keeps_compact_source_provenance():
    planned = plan_for()
    assert planned.selected_evidence_ids
    assert 'source_location' in planned.model_dump_json()


def test_professional_identity_blueprint_uses_existing_extraction_type():
    identity = record('identity', 'Software engineer', EvidenceType.PROFESSIONAL_SUMMARY, 'professional-identity', 1)
    planned = plan_for(catalog([identity, record('python', 'Python', order=2)]))
    assert identity.evidence_id in planned.professional_summary_blueprint.professional_identity_evidence_ids


def test_source_headings_do_not_consume_entire_summary(tmp_path):
    paths, _snapshot, evidence, _manifest, _generation = managed_bundle(tmp_path)
    assert invoke(tmp_path, paths)[0] == 0
    planned = ResumeSectionPlan.model_validate_json(paths['output'].read_bytes())
    summary = next(section for section in planned.ordered_sections if section.section_id == 'professional-summary')
    by_id = {item.evidence_id: item for item in evidence.evidence_records}
    assert any(by_id[identifier].original_content == 'Software engineer building local synthetic systems.' for identifier in summary.selected_evidence_ids)


def test_source_section_heading_is_not_a_documented_skill(tmp_path):
    paths, *_ = managed_bundle(tmp_path)
    assert invoke(tmp_path, paths)[0] == 0
    planned = ResumeSectionPlan.model_validate_json(paths['output'].read_bytes())
    assert '2. Technical Profile' not in [skill.skill_text for skill in planned.prioritized_skills]


def test_experience_budget_counts_roles_not_source_paragraphs(tmp_path):
    document = Document()
    document.add_heading('1. Professional Experience And Engineering Training', level=1)
    for number in (1, 2):
        title = document.add_paragraph(f'1.{number} Synthetic Role {number} at Organization {number}')
        title.runs[0].bold = True
        document.add_paragraph(f'Synthetic original date metadata for Role {number}')
        for index in range(3):
            document.add_paragraph(f'Python task {index} for Synthetic Role {number}')
    source = builders.save(document, tmp_path / 'two-roles.docx')
    snapshot = MasterResumeExtractor(source).extract()
    evidence = build_evidence_catalog(snapshot)
    job = analysis(required=('Python',))
    planned = SectionPlanner().plan(job, match_for(job, evidence), evidence, snapshot)
    section = next(section for section in planned.ordered_sections if section.section_id == 'professional-experience')
    by_id = {item.evidence_id: item for item in evidence.evidence_records}
    assert section.budget >= 2 and len(section.entries) == 2
    assert any('Synthetic Role 2 at Organization 2' in by_id[identifier].original_content for identifier in section.selected_evidence_ids)


@pytest.mark.parametrize('alias', ['analysis', 'match', 'candidate', 'managed_manifest', 'managed_generation'])
def test_output_aliases_do_not_overwrite_input_artifacts(tmp_path, alias):
    if alias.startswith('managed'):
        paths, _snapshot, _evidence, _manifest, generation = managed_bundle(tmp_path)
        output = paths['candidate'] if alias == 'managed_manifest' else generation / 'catalog.json'
    else:
        paths = write_bundle(tmp_path)
        output = paths[alias]
    before = output.read_bytes()
    assert invoke(tmp_path, paths, output=output)[0] == 2
    assert output.read_bytes() == before


@pytest.mark.parametrize('case', ['unknown_id', 'unknown_section', 'duplicate_id'])
def test_controlled_integrity_errors_do_not_echo_private_values(tmp_path, case):
    item = record('python', 'Python')
    evidence = catalog([item])
    job = analysis(required=('Python',))
    matched = match_for(job, evidence)
    if case == 'unknown_id':
        data = matched.model_dump(mode='json')
        requirement = data['requirements'][0]
        requirement['matched_evidence_ids'] = [MARKER]
        requirement['matched_evidence'][0]['evidence_id'] = MARKER
        requirement['components'][0]['matched_evidence_ids'] = [MARKER]
        matched = CandidateJobMatch.model_validate_json(json.dumps(data))
    elif case == 'unknown_section':
        item.section_reference = MARKER
    else:
        item.evidence_id = MARKER
        evidence = catalog([item, item.model_copy(deep=True)])
        matched = match_for(job, evidence)
    paths = write_bundle(tmp_path, evidence, job, matched)
    code, console = invoke(tmp_path, paths)
    assert code == 2 and MARKER not in console


@pytest.mark.parametrize('case', ['missing_version', 'duplicate_key'])
def test_analysis_json_requires_explicit_unambiguous_version(tmp_path, case):
    paths = write_bundle(tmp_path)
    data = json.loads(paths['analysis'].read_text())
    if case == 'missing_version':
        data.pop('schema_version')
        text = json.dumps(data)
    else:
        text = json.dumps(data).replace('"schema_version": "2.0.0"', '"schema_version": "1.0.0", "schema_version": "2.0.0"')
    paths['analysis'].write_text(text, encoding='utf-8')
    assert invoke(tmp_path, paths)[0] == 2


@pytest.mark.parametrize('case', ['old_version', 'missing_version'])
def test_match_version_errors_are_expected_input_errors(tmp_path, case):
    paths = write_bundle(tmp_path)
    data = json.loads(paths['match'].read_text())
    if case == 'old_version':
        data['schema_version'] = '1.0.0'
    else:
        data.pop('schema_version')
    paths['match'].write_text(json.dumps(data), encoding='utf-8')
    assert invoke(tmp_path, paths)[0] == 2


def test_nonobject_candidate_json_is_an_expected_input_error(tmp_path):
    paths = write_bundle(tmp_path)
    paths['candidate'].write_text('[]', encoding='utf-8')
    assert invoke(tmp_path, paths)[0] == 2


def test_control_managed_handoff_and_checksum_failure(tmp_path):
    paths, snapshot, evidence, _manifest, generation = managed_bundle(tmp_path)
    assert snapshot.source_sha256 == evidence.source_document.sha256
    code, console = invoke(tmp_path, paths)
    assert code == 0 and 'Python and SQL' not in console
    planned = ResumeSectionPlan.model_validate_json(paths['output'].read_bytes())
    assert planned.planning_metadata.snapshot_available and planned.planning_metadata.manifest_available
    assert stat.S_IMODE(paths['output'].stat().st_mode) == 0o600
    before = paths['output'].read_bytes()
    tampered = generation / 'snapshot.json'
    tampered.write_bytes(tampered.read_bytes() + b'x')
    assert invoke(tmp_path, paths)[0] == 2
    assert paths['output'].read_bytes() == before


@pytest.mark.parametrize('target', ['analysis', 'match', 'candidate', 'output'])
def test_control_symlink_files_are_rejected(tmp_path, target):
    paths = write_bundle(tmp_path)
    real = paths[target]
    link = real.with_name('link-' + real.name)
    link.symlink_to(real)
    before = real.read_bytes()
    if target == 'output':
        code, _ = invoke(tmp_path, paths, output=link)
    else:
        code, _ = invoke(tmp_path, {**paths, target: link})
    assert code == 2 and real.read_bytes() == before


def test_control_output_confinement(tmp_path):
    paths = write_bundle(tmp_path)
    output = tmp_path / 'outside-boundary.json'
    assert invoke(tmp_path, paths, output=output)[0] == 2
    assert not output.exists()


def test_control_atomic_replace_failure_preserves_inputs_and_previous_output(tmp_path):
    paths = write_bundle(tmp_path)
    before = {key: path.read_bytes() for key, path in paths.items()}
    with patch('resume_assistant.private_output.os.replace', side_effect=OSError(MARKER)):
        code, console = invoke(tmp_path, paths)
    assert code == 3 and MARKER not in console
    assert {key: path.read_bytes() for key, path in paths.items()} == before
    assert not list(paths['output'].parent.glob('.resume-assistant-*.tmp'))


@pytest.mark.parametrize('target', ['root', 'skill', 'section'])
def test_control_unknown_fields_are_rejected(target):
    data = plan_for().model_dump(mode='json')
    destination = data if target == 'root' else data['prioritized_skills'][0] if target == 'skill' else data['ordered_sections'][0]
    destination['unknown_field'] = True
    with pytest.raises(ValidationError):
        ResumeSectionPlan.model_validate_json(json.dumps(data))


def test_control_all_requirement_statuses_are_copied_without_reinterpretation():
    for required, complete, expected in (('Python', True, 'supported'), ('Python and SQL', True, 'partially_supported'), ('Kubernetes', True, 'no_evidence_found'), ('Kubernetes', False, 'not_assessable')):
        evidence = catalog([record('python', 'Python')], complete=complete)
        job = analysis(required=(required,))
        matched = match_for(job, evidence)
        planned = plan_for(evidence, job, matched)
        assert planned.requirement_coverage[0].status == expected
        assert planned.requirement_coverage[0].matched_evidence_ids == matched.requirements[0].matched_evidence_ids


def test_control_repeated_serialization_is_byte_identical():
    baseline = plan_for().model_dump_json(indent=2).encode('utf-8')
    for _ in range(20):
        assert plan_for().model_dump_json(indent=2).encode('utf-8') == baseline


def test_control_serialization_is_stable_across_hash_seeds():
    results = []
    script = 'import hashlib; from tests.test_planning_acceptance import plan_for; print(hashlib.sha256(plan_for().model_dump_json(indent=2).encode()).hexdigest())'
    for seed in ('1', '17', '103'):
        completed = subprocess.run(
            [sys.executable, '-B', '-c', script], cwd=Path(__file__).resolve().parents[1],
            env={**os.environ, 'PYTHONHASHSEED': seed, 'PYTHONDONTWRITEBYTECODE': '1'},
            text=True, capture_output=True, check=True,
        )
        results.append(completed.stdout.strip())
    assert len(set(results)) == 1


@pytest.mark.parametrize('tamper', ['wrong_hash', 'wrong_requirement', 'wrong_provenance', 'missing_requirements'])
def test_managed_inputs_still_require_cross_artifact_validation(tmp_path, tamper):
    paths, _snapshot, evidence, _manifest, _generation = managed_bundle(tmp_path)
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
    job = JobDescriptionAnalysis.model_validate_json(paths['analysis'].read_bytes())
    with pytest.raises(CandidateJobIntegrityError):
        CandidateJobMatcher(evidence).validate_result(supplied, job)
    paths['match'].write_text(supplied.model_dump_json(), encoding='utf-8')
    assert invoke(tmp_path, paths)[0] == 2
