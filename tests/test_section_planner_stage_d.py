'''Synthetic strict-contract, source-binding and publication-boundary regressions.'''

import json
import os
import stat
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest
from docx import Document
from pydantic import ValidationError

from resume_assistant import cli
from resume_assistant.evidence import build_evidence_catalog
from resume_assistant.extractor import MasterResumeExtractor
from resume_assistant.legacy_section_plan import LegacyResumeSectionPlan
from resume_assistant.matching import CandidateJobMatcher
from resume_assistant.models import EvidenceType
from resume_assistant.publication_reader import load_committed_pair
from resume_assistant.section_planner import (
    DEFAULT_BUDGETS, PLAN_SCHEMA_VERSION, ExtractionGenerationIdentity,
    PlanBudgets, ResumeSectionPlan, SectionPlanner, SectionPlannerError,
    content_sha256,
)
from tests.fixtures import builders
from tests.fixtures.section_plans import legacy_plan_payload
from tests.test_section_planner import _stage_b_analysis, _match


MARKER = 'SYNTHETIC_PRIVATE_STAGE_D_MARKER'


def _document():
    document = Document()
    document.add_heading('1. Professional Identity', level=1)
    document.add_paragraph('Software engineer building synthetic local systems.')
    document.add_heading('2. Technical Profile', level=1)
    for text in ('Python', 'SQL', 'GitHub', 'Email: synthetic@example.invalid'):
        document.add_paragraph(text)
    table = document.add_table(rows=1, cols=1)
    table.cell(0, 0).text = 'Rust'
    table.cell(0, 0).add_table(rows=1, cols=1).cell(0, 0).text = 'Kubernetes'
    for number, heading, title, support in (
        (3, 'Professional Experience And Engineering Training', 'Synthetic Engineer at Example', 'Built Python services for a synthetic source.'),
        (4, 'Independent Software And AI Projects', 'Synthetic Backend Project', 'Built SQL project storage.'),
        (5, 'Education', 'Synthetic Computer Science degree', 'Original synthetic course details.'),
        (6, 'Certifications And Professional Development', 'Synthetic certification', 'Original synthetic qualification details.'),
    ):
        document.add_heading(f'{number}. {heading}', level=1)
        title_paragraph = document.add_paragraph(f'{number}.1. {title}')
        title_paragraph.runs[0].bold = True
        document.add_paragraph(support)
    return document


@pytest.fixture
def case(tmp_path):
    source = builders.save(_document(), tmp_path / 'stage-d-synthetic.docx')
    before = source.read_bytes()
    snapshot = MasterResumeExtractor(source).extract()
    catalog = build_evidence_catalog(snapshot)
    job = _stage_b_analysis(required=['Python', 'SQL'], preferred=['Python and GitHub'])
    matched = _match(job, catalog)
    planner = SectionPlanner()
    result = planner.plan(job, matched, catalog, snapshot)
    assert source.read_bytes() == before
    return planner, result, job, matched, catalog, snapshot


def _payload(result):
    return result.model_dump(mode='json')


def _section(data, identifier):
    return next(section for section in data['ordered_sections'] if section['section_id'] == identifier)


def _reference(data, identifier):
    return next(reference for reference in data['evidence_provenance'] if reference['evidence_id'] == identifier)


def _validate(data):
    return ResumeSectionPlan.model_validate_json(json.dumps(data))


@pytest.mark.parametrize('target', [
    'root', 'metadata', 'budgets', 'section', 'entry', 'skill', 'association',
    'coverage', 'component', 'blueprint', 'identity', 'capability',
    'experience', 'project', 'provenance', 'location', 'cell_source',
])
def test_stage_d_unknown_fields_are_rejected_at_every_contract_layer(case, target):
    data = _payload(case[1])
    section = _section(data, 'professional-experience')
    provenance = next(reference for reference in data['evidence_provenance'] if reference['cell_sources'])
    targets = {
        'root': data, 'metadata': data['planning_metadata'], 'budgets': data['budgets'],
        'section': section, 'entry': section['entries'][0],
        'skill': data['prioritized_skills'][0], 'association': data['prioritized_skills'][0]['requirement_associations'][0],
        'coverage': data['requirement_coverage'][0], 'component': data['requirement_coverage'][0]['components'][0],
        'blueprint': data['professional_summary_blueprint'],
        'identity': data['professional_summary_blueprint']['identity_statements'][0],
        'capability': data['professional_summary_blueprint']['technical_capabilities'][0],
        'experience': data['professional_summary_blueprint']['experience_bindings'][0],
        'project': data['professional_summary_blueprint']['project_highlights'][0],
        'provenance': provenance, 'location': provenance['source_location'], 'cell_source': provenance['cell_sources'][0],
    }
    targets[target]['unsupported_field'] = MARKER
    with pytest.raises(ValidationError):
        _validate(data)


@pytest.mark.parametrize('path,value', [
    (('budgets', 'max_skills'), '15'), (('budgets', 'max_skills'), True),
    (('budgets', 'target_pages'), True), (('budgets', 'target_pages'), '2'),
    (('budgets', 'page_guarantee'), True), (('budgets', 'page_guarantee'), 0),
    (('planning_metadata', 'snapshot_available'), 'false'),
    (('planning_metadata', 'manifest_available'), 0),
    (('planning_metadata', 'source_candidate_evidence_record_count'), '1'),
    (('target_job_context', 'required_skill_count'), True),
    (('ordered_sections', 0, 'dropped_empty'), 0),
    (('human_review_required',), 1), (('human_review_required',), False),
])
def test_stage_d_invalid_type_coercions_and_false_guarantees_are_rejected(case, path, value):
    data = _payload(case[1])
    current = data
    for key in path[:-1]:
        current = current[key]
    current[path[-1]] = value
    with pytest.raises(ValidationError):
        _validate(data)


@pytest.mark.parametrize('tamper', [
    'unknown_flat_id', 'duplicate_flat_id', 'unknown_section_id', 'duplicate_section_id',
    'selected_and_excluded', 'missing_exclusion', 'duplicate_exclusion',
    'unknown_provenance_id', 'duplicate_provenance_id', 'record_count', 'wrong_item_count',
    'wrong_flat_union', 'wrong_entry_union', 'duplicate_entry_member', 'unknown_entry_identity',
    'source_reference', 'entry_source_reference', 'provenance_section', 'provenance_entry',
    'section_budget', 'max_sections', 'duplicate_requirement', 'unknown_skill_requirement',
    'empty_skill_evidence', 'unknown_skill_evidence', 'duplicate_skill_label',
    'unsupported_component', 'wrong_component_status', 'empty_association',
])
def test_stage_d_structural_relationships_reject_invalid_states(case, tamper):
    data = _payload(case[1])
    technical = _section(data, 'technical-skills')
    experience = _section(data, 'professional-experience')
    selected = data['selected_evidence_ids'][0]
    if tamper == 'unknown_flat_id':
        data['selected_evidence_ids'].append('unknown-evidence')
    elif tamper == 'duplicate_flat_id':
        data['selected_evidence_ids'].append(selected)
    elif tamper == 'unknown_section_id':
        technical['section_id'] = 'unsupported-output'
    elif tamper == 'duplicate_section_id':
        data['ordered_sections'].append(technical.copy())
    elif tamper == 'selected_and_excluded':
        data['excluded_evidence'].append({'evidence_id': selected, 'reason': 'content_budget_exhausted'})
    elif tamper == 'missing_exclusion':
        data['excluded_evidence'].pop()
    elif tamper == 'duplicate_exclusion':
        data['excluded_evidence'].append(data['excluded_evidence'][0].copy())
    elif tamper == 'unknown_provenance_id':
        data['evidence_provenance'][0]['evidence_id'] = 'unknown-evidence'
    elif tamper == 'duplicate_provenance_id':
        data['evidence_provenance'].append(data['evidence_provenance'][0].copy())
    elif tamper == 'record_count':
        data['planning_metadata']['source_candidate_evidence_record_count'] += 1
    elif tamper == 'wrong_item_count':
        technical['item_count'] = 999
    elif tamper == 'wrong_flat_union':
        data['selected_evidence_ids'] = []
    elif tamper == 'wrong_entry_union':
        experience['entries'][0]['selected_evidence_ids'].pop()
    elif tamper == 'duplicate_entry_member':
        experience['entries'][0]['selected_evidence_ids'].append(experience['selected_evidence_ids'][0])
    elif tamper == 'unknown_entry_identity':
        experience['entries'][0]['identity_evidence_id'] = 'unknown-identity'
    elif tamper == 'source_reference':
        technical['source_section_references'].append('phantom-section')
    elif tamper == 'entry_source_reference':
        experience['entries'][0]['source_section_reference'] = 'education'
    elif tamper == 'provenance_section':
        _reference(data, selected)['output_section_id'] = 'projects'
    elif tamper == 'provenance_entry':
        _reference(data, selected)['resume_entry_id'] = 'unknown-entry'
    elif tamper == 'section_budget':
        technical['budget'] += 1
    elif tamper == 'max_sections':
        data['budgets']['max_output_sections'] = 1
    elif tamper == 'duplicate_requirement':
        data['requirement_coverage'].append(data['requirement_coverage'][0].copy())
    elif tamper == 'unknown_skill_requirement':
        data['prioritized_skills'][0]['requirement_id'] = 'f' * 64
    elif tamper == 'empty_skill_evidence':
        data['prioritized_skills'][0]['matched_evidence_ids'] = []
    elif tamper == 'unknown_skill_evidence':
        data['prioritized_skills'][0]['matched_evidence_ids'].append('unknown-evidence')
    elif tamper == 'duplicate_skill_label':
        data['prioritized_skills'].append(data['prioritized_skills'][0].copy())
    elif tamper == 'unsupported_component':
        data['prioritized_skills'][0]['skill_text'] = 'Unsupported Kubernetes leadership'
    elif tamper == 'wrong_component_status':
        data['requirement_coverage'][0]['status'] = 'no_evidence_found'
    else:
        data['prioritized_skills'][0]['requirement_associations'][0]['matched_evidence_ids'] = []
    with pytest.raises(ValidationError):
        _validate(data)


@pytest.mark.parametrize('coordinate,value', [
    ('body_block_index', None), ('body_block_index', -1), ('body_block_index', '1'),
    ('paragraph_index', -1), ('row_index', -1), ('cell_index', -1),
    ('paragraph_position_in_cell', -1), ('nested_table_path', [1, 0]),
    ('nested_table_path', [1, -1, 0]), ('nested_table_path', ['1', 0, 0]),
    ('document_part', ''),
])
def test_stage_d_invalid_provenance_coordinates_are_rejected(case, coordinate, value):
    data = _payload(case[1])
    data['evidence_provenance'][0]['source_location'][coordinate] = value
    with pytest.raises(ValidationError):
        _validate(data)


@pytest.mark.parametrize('tamper', [
    'identity_without_binding', 'identity_without_text', 'invented_identity_claim',
    'identity_wrong_evidence', 'unbound_technical_claim', 'unsupported_years',
    'unbound_experience', 'unknown_experience_entry', 'unbound_project', 'unknown_project_entry',
])
def test_stage_d_blueprint_rejects_unbound_or_unsupported_claims(case, tamper):
    data = _payload(case[1])
    blueprint = data['professional_summary_blueprint']
    if tamper == 'identity_without_binding':
        blueprint['identity_statements'][0]['evidence_ids'] = []
    elif tamper == 'identity_without_text':
        blueprint['identity_statements'][0]['statement_text'] = ' '
    elif tamper == 'invented_identity_claim':
        blueprint['identity_statements'][0]['statement_text'] = 'Principal engineer with twenty years of leadership'
    elif tamper == 'identity_wrong_evidence':
        blueprint['identity_statements'][0]['evidence_ids'] = [_section(data, 'technical-skills')['selected_evidence_ids'][0]]
    elif tamper == 'unbound_technical_claim':
        blueprint['technical_capability_skill_texts'].append('Unsupported achievement')
    elif tamper == 'unsupported_years':
        blueprint['experience_categories'].append('Twenty years of senior engineering')
    elif tamper == 'unbound_experience':
        blueprint['experience_bindings'] = []
    elif tamper == 'unknown_experience_entry':
        blueprint['experience_bindings'][0]['entry_ids'] = ['unknown-entry']
    elif tamper == 'unbound_project':
        blueprint['project_highlights'] = []
    else:
        blueprint['project_highlights'][0]['entry_id'] = 'unknown-entry'
    with pytest.raises(ValidationError):
        _validate(data)


def test_stage_d_valid_blueprint_and_provenance_round_trip_are_fully_bound(case):
    planner, result, job, matched, catalog, snapshot = case
    restored = _validate(_payload(result))
    assert restored == result
    assert planner.validate_result(restored, job, matched, catalog, snapshot) == result
    assert result.schema_version == PLAN_SCHEMA_VERSION == '2.0.0'
    assert len(result.evidence_provenance) == len(catalog.evidence_records)
    records = {record.evidence_id: record for record in catalog.evidence_records}
    for reference in result.evidence_provenance:
        original = records[reference.evidence_id]
        assert reference.source_document == original.source_document
        assert reference.source_sha256 == original.source_sha256
        assert reference.source_location.model_dump() == original.source_location.model_dump()
        assert reference.content_sha256 == content_sha256(original.original_content)
        assert reference.cell_sources is None if original.table_context is None else reference.cell_sources is not None
    assert any(reference.source_location.nested_table_path for reference in result.evidence_provenance)
    blueprint = result.professional_summary_blueprint
    assert all(records[statement.evidence_ids[0]].evidence_type == EvidenceType.PROFESSIONAL_SUMMARY for statement in blueprint.identity_statements)
    assert all(statement.statement_text == records[statement.evidence_ids[0]].original_content for statement in blueprint.identity_statements)
    assert blueprint.technical_capabilities == result.prioritized_skills
    assert blueprint.experience_bindings[0].entry_ids == [entry.entry_id for entry in next(section for section in result.ordered_sections if section.section_id == 'professional-experience').entries]
    assert blueprint.project_highlights == next(section for section in result.ordered_sections if section.section_id == 'projects').entries
    assert 'original_content' not in result.model_dump_json()


@pytest.mark.parametrize('tamper', ['duplicate_root_key', 'duplicate_identity_key', 'nonstandard_constant', 'malformed_json'])
def test_stage_d_artifact_json_cannot_hide_ambiguous_or_nonstandard_values(case, tamper):
    result = case[1]
    payload = result.model_dump_json()
    if tamper == 'duplicate_root_key':
        payload = payload.replace('"schema_version":"2.0.0"', '"schema_version":"1.0.0","schema_version":"2.0.0"', 1)
    elif tamper == 'duplicate_identity_key':
        text = json.dumps(result.professional_summary_blueprint.identity_statements[0].statement_text)
        payload = payload.replace('"statement_text":' + text, '"statement_text":' + json.dumps(MARKER) + ',"statement_text":' + text, 1)
    elif tamper == 'nonstandard_constant':
        payload = payload.replace('"max_skills":15', '"max_skills":NaN', 1)
    else:
        payload = payload[:-1]
    with pytest.raises(ValidationError) as error:
        ResumeSectionPlan.model_validate_json(payload)
    assert MARKER not in str(error.value)


@pytest.mark.parametrize('tamper', ['filename', 'file_hash', 'body_coordinate', 'paragraph_coordinate', 'nested_coordinate', 'cell_coordinate', 'source_reference', 'eligible_flag', 'identity_claim', 'component_claim', 'job_context', 'generation_context'])
def test_stage_d_source_validation_rejects_self_consistent_but_forged_results(case, tamper):
    planner, result, job, matched, catalog, snapshot = case
    data = _payload(result)
    if tamper == 'filename':
        data['planning_metadata']['source_candidate_filename'] = MARKER + '.docx'
        for reference in data['evidence_provenance']:
            reference['source_document'] = MARKER + '.docx'
    elif tamper == 'file_hash':
        data['planning_metadata']['source_candidate_source_sha256'] = 'f' * 64
        for reference in data['evidence_provenance']:
            reference['source_sha256'] = 'f' * 64
    elif tamper in ('body_coordinate', 'paragraph_coordinate'):
        reference = next(reference for reference in data['evidence_provenance'] if reference['output_section_id'] is None)
        key = 'body_block_index' if tamper == 'body_coordinate' else 'paragraph_index'
        reference['source_location'][key] = 999
    elif tamper == 'nested_coordinate':
        reference = next(reference for reference in data['evidence_provenance'] if reference['source_location']['nested_table_path'])
        reference['source_location']['nested_table_path'][-1] = 1
    elif tamper == 'cell_coordinate':
        reference = next(reference for reference in data['evidence_provenance'] if reference['cell_sources'])
        reference['cell_sources'][0]['cell_index'] = 99
    elif tamper == 'source_reference':
        reference = next(reference for reference in data['evidence_provenance'] if reference['output_section_id'] is None and reference['source_section_reference'] is not None)
        reference['source_section_reference'] = 'technical-skills-database'
    elif tamper == 'eligible_flag':
        reference = next(reference for reference in data['evidence_provenance'] if reference['output_section_id'] is None and reference['source_section_reference'] == 'technical-profile')
        reference['content_eligible'] = True
        next(item for item in data['excluded_evidence'] if item['evidence_id'] == reference['evidence_id'])['reason'] = 'content_budget_exhausted'
    elif tamper == 'identity_claim':
        statement = data['professional_summary_blueprint']['identity_statements'][0]
        statement['statement_text'] = 'Principal engineer with twenty years of unsupported leadership'
        _reference(data, statement['evidence_ids'][0])['content_sha256'] = content_sha256(statement['statement_text'])
    elif tamper == 'component_claim':
        old, new = 'SQL', 'Unsupported Oracle leadership'
        for coverage in data['requirement_coverage']:
            for component in coverage['components']:
                if component['text'] == old:
                    component['text'] = new
        for skill in data['prioritized_skills'] + data['professional_summary_blueprint']['technical_capabilities']:
            if skill['skill_text'] == old:
                skill['skill_text'] = new
        data['professional_summary_blueprint']['technical_capability_skill_texts'] = [skill['skill_text'] for skill in data['prioritized_skills']]
    elif tamper == 'job_context':
        data['target_job_context']['seniority_level'] = 'Unsupported Principal level'
    else:
        data['planning_metadata']['manifest_available'] = True
    forged = _validate(data)
    with pytest.raises(SectionPlannerError) as error:
        planner.validate_result(forged, job, matched, catalog, snapshot)
    assert MARKER not in str(error.value)
    assert 'twenty years' not in str(error.value)


def test_stage_d_source_validation_reuses_matcher_boundary_without_assess(case, monkeypatch):
    planner, result, job, matched, catalog, snapshot = case
    monkeypatch.setattr(CandidateJobMatcher, 'assess', lambda *args, **kwargs: pytest.fail('A second matching operation is not authorized.'))
    assert planner.validate_result(result, job, matched, catalog, snapshot) == result


def test_stage_d_structural_contract_rejects_weakened_parent_priority(case):
    data = _payload(case[1])
    for skills in (data['prioritized_skills'], data['professional_summary_blueprint']['technical_capabilities']):
        python = next(skill for skill in skills if skill['skill_text'] == 'Python')
        python['requirement_associations'].reverse()
        python['requirement_id'] = python['requirement_associations'][0]['requirement_id']
        python['priority_tier'] = 'preferred'
        skills.sort(key=lambda skill: ('required', 'preferred', 'other').index(skill['priority_tier']))
    data['professional_summary_blueprint']['technical_capability_skill_texts'] = [skill['skill_text'] for skill in data['prioritized_skills']]
    with pytest.raises(ValidationError):
        _validate(data)


def test_stage_d_source_boundary_rejects_self_consistent_unknown_catalog_evidence(case):
    planner, result, job, matched, catalog, snapshot = case
    data = _payload(result)
    old_id = data['excluded_evidence'][0]['evidence_id']
    reference = _reference(data, old_id)
    reference.update(
        evidence_id='invented-source-evidence', evidence_type='unclassified',
        source_section_reference=None, content_eligible=False, atomic_skill_text=None,
    )
    reference['source_location']['body_block_index'] = 1000
    data['excluded_evidence'][0] = {
        'evidence_id': 'invented-source-evidence', 'reason': 'unclassified_section_no_output_mapping',
    }
    forged = _validate(data)
    with pytest.raises(SectionPlannerError, match='original evidence catalog'):
        planner.validate_result(forged, job, matched, catalog, snapshot)


def test_stage_d_source_validation_rejects_wrong_source_aware_entry_grouping(case):
    planner, result, job, matched, catalog, snapshot = case
    data = _payload(result)
    section = _section(data, 'professional-experience')
    ids = section['selected_evidence_ids']
    entry = section['entries'][0]
    entry.update(entry_id=ids[0], identity_evidence_id=None, grouping_basis='unresolved_source_span')
    section['entry_grouping_complete'] = False
    for identifier in ids:
        _reference(data, identifier)['resume_entry_id'] = ids[0]
    data['professional_summary_blueprint']['experience_bindings'][0]['entry_ids'] = [ids[0]]
    forged = _validate(data)
    with pytest.raises(SectionPlannerError, match='source decisions'):
        planner.validate_result(forged, job, matched, catalog, snapshot)


def test_stage_d_source_boundary_rejects_self_consistent_background_displacing_required_evidence(case):
    _planner, _result, job, matched, catalog, snapshot = case
    planner = SectionPlanner(PlanBudgets(**{**DEFAULT_BUDGETS, 'max_output_sections': 1, 'max_skills': 1}))
    result = planner.plan(job, matched, catalog, snapshot)
    data = _payload(result)
    technical = _section(data, 'technical-skills')
    required = technical['selected_evidence_ids'][0]
    background = next(reference for reference in data['evidence_provenance'] if reference['atomic_skill_text'] == 'Rust')
    background_id = background['evidence_id']
    _reference(data, required)['output_section_id'] = None
    background['output_section_id'] = 'technical-skills'
    technical['selected_evidence_ids'] = [background_id]
    data['selected_evidence_ids'] = [background_id]
    data['excluded_evidence'] = [item for item in data['excluded_evidence'] if item['evidence_id'] != background_id]
    data['excluded_evidence'].append({'evidence_id': required, 'reason': 'content_budget_exhausted'})
    skill = {
        'skill_text': 'Rust', 'priority_tier': 'other', 'matched_evidence_ids': [background_id],
        'source_section_reference': 'technical-profile', 'requirement_id': None,
        'requirement_associations': [],
    }
    data['prioritized_skills'] = [skill]
    data['professional_summary_blueprint']['technical_capabilities'] = [skill.copy()]
    data['professional_summary_blueprint']['technical_capability_skill_texts'] = ['Rust']
    forged = _validate(data)
    with pytest.raises(SectionPlannerError, match='source decisions'):
        planner.validate_result(forged, job, matched, catalog, snapshot)


def test_stage_d_legacy_reader_is_explicit_and_replanning_requires_original_sources(case):
    planner, result, job, matched, catalog, snapshot = case
    data = legacy_plan_payload(result)
    for skill in data['prioritized_skills']:
        skill.pop('requirement_associations')
    legacy = LegacyResumeSectionPlan.model_validate_json(json.dumps(data))
    assert legacy.prioritized_skills[0].requirement_associations == []
    with pytest.raises(ValidationError):
        _validate(data)
    with pytest.raises(SectionPlannerError, match='schema 2.0.0'):
        planner.validate_result(legacy, job, matched, catalog, snapshot)
    assert planner.replan_legacy(legacy, job, matched, catalog, snapshot) == result
    wrong = legacy.model_copy(update={'planning_metadata': legacy.planning_metadata.model_copy(update={'source_candidate_source_sha256': 'f' * 64})})
    with pytest.raises(SectionPlannerError, match='validated sources'):
        planner.replan_legacy(wrong, job, matched, catalog, snapshot)


def _managed(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, 'PROJECT_ROOT', tmp_path)
    source = builders.save(_document(), tmp_path / 'stage-d-managed.docx')
    processed = tmp_path / 'data/private/processed'
    candidate = processed / 'candidate.json'
    cli.run_extract_master(source, candidate, processed / 'snapshot-selector.json')
    snapshot, catalog, manifest, _ = load_committed_pair(tmp_path, candidate)
    job = _stage_b_analysis(required=['Python', 'SQL'], preferred=['Python and GitHub'])
    analysis = processed / 'analysis.json'
    analysis.write_text(job.model_dump_json(), encoding='utf-8')
    matched = processed / 'match.json'
    cli.run_match_job(analysis, candidate, matched)
    output = processed / 'plan.json'
    output.write_bytes(b'SYNTHETIC_PREVIOUS_PLAN')
    generation = cli.publication_store.generation_dir_path(tmp_path, manifest.bundle_key, manifest.generation_id)
    protected = [source, candidate, analysis, matched, generation / 'snapshot.json', generation / 'catalog.json']
    return [
        'plan-sections', '--analysis', str(analysis), '--match', str(matched),
        '--candidate-evidence', str(candidate), '--output', str(output),
    ], output, protected, snapshot, catalog, manifest, job


def test_stage_d_managed_pipeline_validates_before_publication_and_keeps_generation_identity(tmp_path, monkeypatch, capsys):
    argv, output, protected, snapshot, catalog, manifest, job = _managed(tmp_path, monkeypatch)
    before = {path: path.read_bytes() for path in protected}
    events = []
    real_validate = SectionPlanner.validate_result
    real_write = cli.PrivateOutput.write_json
    def validate(*args, **kwargs):
        events.append('validate')
        return real_validate(*args, **kwargs)
    def write(*args, **kwargs):
        events.append('publish')
        return real_write(*args, **kwargs)
    capsys.readouterr()
    with patch.object(SectionPlanner, 'validate_result', validate), patch.object(cli.PrivateOutput, 'write_json', write):
        assert cli.main(argv) == 0
    assert events == ['validate', 'publish']
    captured = capsys.readouterr()
    assert 'Software engineer' not in captured.out + captured.err
    assert 'Python' not in captured.out + captured.err
    result = ResumeSectionPlan.model_validate_json(output.read_bytes())
    generation = result.planning_metadata.extraction_generation
    assert generation == ExtractionGenerationIdentity.from_manifest(manifest)
    assert generation.generation_id == manifest.generation_id
    assert generation.catalog_sha256 == manifest.catalog.sha256
    assert stat.S_IMODE(output.stat().st_mode) == 0o600
    assert {path: path.read_bytes() for path in protected} == before
    direct = SectionPlanner().plan(job, _match(job, catalog), catalog, snapshot, manifest_available=True, extraction_generation=generation)
    assert direct == result


def test_stage_d_forged_plan_is_rejected_before_private_write_with_sanitized_error(tmp_path, monkeypatch, capsys):
    argv, output, protected, *_ = _managed(tmp_path, monkeypatch)
    before = {path: path.read_bytes() for path in [*protected, output]}
    real_plan = SectionPlanner.plan
    def forge(*args, **kwargs):
        data = _payload(real_plan(*args, **kwargs))
        data['planning_metadata']['source_candidate_filename'] = MARKER + '.docx'
        data['planning_metadata']['extraction_generation']['source_filename'] = MARKER + '.docx'
        for reference in data['evidence_provenance']:
            reference['source_document'] = MARKER + '.docx'
        return _validate(data)
    capsys.readouterr()
    with patch.object(SectionPlanner, 'plan', forge), patch.object(cli.PrivateOutput, 'write_json') as writer:
        assert cli.main(argv) == 2
    writer.assert_not_called()
    captured = capsys.readouterr()
    assert MARKER not in captured.out + captured.err
    assert 'validated sources' in captured.out
    assert {path: path.read_bytes() for path in [*protected, output]} == before
    assert not list(output.parent.glob('.resume-assistant-*.tmp'))


@pytest.mark.parametrize('field', ['generation_id', 'bundle_key', 'catalog_sha256', 'snapshot_sha256'])
def test_stage_d_managed_generation_references_must_match_validated_manifest(tmp_path, monkeypatch, capsys, field):
    _argv, _output, _protected, snapshot, catalog, manifest, job = _managed(tmp_path, monkeypatch)
    generation = ExtractionGenerationIdentity.from_manifest(manifest)
    planner = SectionPlanner()
    matched = _match(job, catalog)
    result = planner.plan(job, matched, catalog, snapshot, manifest_available=True, extraction_generation=generation)
    data = _payload(result)
    data['planning_metadata']['extraction_generation'][field] = 'f' * 64 if field.endswith('sha256') else 'forged-generation-reference'
    forged = _validate(data)
    with pytest.raises(SectionPlannerError, match='validated sources'):
        planner.validate_result(forged, job, matched, catalog, snapshot, manifest_available=True, extraction_generation=generation)


def test_stage_d_serialization_is_identical_across_runs_and_hash_seeds(case, tmp_path):
    planner, result, job, matched, catalog, snapshot = case
    baseline = result.model_dump_json(indent=2, warnings='error')
    for _ in range(5):
        assert planner.plan(job, matched, catalog, snapshot).model_dump_json(indent=2, warnings='error') == baseline
    paths = [tmp_path / name for name in ('candidate.json', 'snapshot.json', 'analysis.json', 'match.json')]
    for path, item in zip(paths, (catalog, snapshot, job, matched)):
        path.write_text(item.model_dump_json(warnings='error'), encoding='utf-8')
    script = (
        'import sys; from pathlib import Path; '
        'from resume_assistant.models import CandidateEvidenceCatalog,DocumentSnapshot; '
        'from resume_assistant.job_models import JobDescriptionAnalysis; '
        'from resume_assistant.matching_models import CandidateJobMatch; '
        'from resume_assistant.section_planner import SectionPlanner; '
        'catalog=CandidateEvidenceCatalog.model_validate_json(Path(sys.argv[1]).read_text()); '
        'snapshot=DocumentSnapshot.model_validate_json(Path(sys.argv[2]).read_text()); '
        'job=JobDescriptionAnalysis.model_validate_json(Path(sys.argv[3]).read_text()); '
        'match=CandidateJobMatch.model_validate_json(Path(sys.argv[4]).read_text()); '
        'print(SectionPlanner().plan(job,match,catalog,snapshot).model_dump_json(indent=2,warnings="error"))'
    )
    for seed in ('1', '17', '103'):
        completed = subprocess.run(
            [sys.executable, '-B', '-c', script, *map(str, paths)],
            cwd=Path(__file__).resolve().parents[1],
            env={**os.environ, 'PYTHONHASHSEED': seed, 'PYTHONDONTWRITEBYTECODE': '1'},
            text=True, capture_output=True, check=True,
        )
        assert completed.stdout.rstrip('\n') == baseline
