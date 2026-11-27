'''Synthetic acceptance tests for source grouping, relevance budgets and outcomes.'''

import hashlib
import json
import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest
from docx import Document
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from pydantic import ValidationError

from resume_assistant import cli
from resume_assistant.evidence import build_evidence_catalog
from resume_assistant.extractor import MasterResumeExtractor
from resume_assistant.legacy_section_plan import LegacyResumeSectionPlan
from resume_assistant.models import EvidenceType, SourceLocation
from resume_assistant.publication import PublicationError
from resume_assistant.publication_integrity import (
    validate_catalog_structure,
    validate_publication_pair,
    validate_snapshot_structure,
)
from resume_assistant.publication_reader import load_committed_pair
from resume_assistant.section_planner import (
    DEFAULT_BUDGETS,
    ExtractionGenerationIdentity,
    PlanBudgets,
    PlannedResumeEntry,
    ResumeSectionPlan,
    SectionPlanner,
    SectionPlannerError,
)
from tests.fixtures import builders
from tests.fixtures.section_plans import legacy_plan_payload
from tests.fixtures.jobs import empty_job_payload
from tests.test_section_planner import (
    _analysis,
    _catalog,
    _evidence_record,
    _match,
    _stage_b_analysis,
)


EXPERIENCE = 'professional-experience-and-engineering-training'
PROJECTS = 'independent-software-and-ai-projects'
OTHER_PROJECTS = 'additional-ai-and-multimodal-applications'
CERTIFICATIONS = 'certifications-and-professional-development'


def _record(identifier, kind, text, section, block, *, style='Normal', bold=False):
    record = _evidence_record(
        identifier, kind, text, section_reference=section, block_index=block,
    )
    record.source_location.paragraph_index = block
    record.formatting = {'style': style, 'bold': bold}
    return record


def _entry(identifier, kind, section, block, *, support=3, text=None):
    records = [_record(
        identifier + '-title', kind, text or identifier + ' original title',
        section, block, style='Heading 2', bold=True,
    )]
    records.extend(
        _record(
            f'{identifier}-support-{index}', kind,
            f'{identifier} documented supporting record {index}',
            section, block + index + 1,
        )
        for index in range(support)
    )
    return records


def _table_record(identifier, kind, section, *, row, table=5, path=None, cell=None, text=None):
    record = _evidence_record(
        identifier, kind, text or identifier, section_reference=section, block_index=5,
    )
    record.source_location = SourceLocation(
        body_block_index=5, table_index=table, row_index=row,
        cell_index=cell, nested_table_path=path,
    )
    record.formatting = {'source': 'table', 'table': True}
    return record


def _plan(records, *, required=(), preferred=(), job=None, snapshot=None, **budgets):
    catalog = _catalog(records)
    job = job or _stage_b_analysis(required=required, preferred=preferred)
    planner = SectionPlanner(PlanBudgets(**{**DEFAULT_BUDGETS, **budgets}))
    return planner.plan(job, _match(job, catalog), catalog, snapshot)


def _section(plan, identifier):
    return next(section for section in plan.ordered_sections if section.section_id == identifier)


def _assert_accounting(plan, records):
    by_id = {record.evidence_id: record for record in records}
    selected = plan.selected_evidence_ids
    excluded = [item.evidence_id for item in plan.excluded_evidence]
    assert len(selected) == len(set(selected))
    assert len(excluded) == len(set(excluded))
    assert set(selected).isdisjoint(excluded)
    assert set(selected) | set(excluded) == {record.evidence_id for record in records}
    assert selected == [
        identifier for section in plan.ordered_sections
        for identifier in section.selected_evidence_ids
    ]
    for section in plan.ordered_sections:
        assert section.item_count == len(section.selected_evidence_ids) > 0
        assert section.dropped_empty is False
        assert set(section.source_section_references) == {
            by_id[identifier].section_reference for identifier in section.selected_evidence_ids
        }
        if section.section_id in ('professional-experience', 'projects', 'education', 'certifications'):
            members = [
                identifier for entry in section.entries
                for identifier in entry.selected_evidence_ids
            ]
            assert len(members) == len(set(members))
            assert set(members) == set(section.selected_evidence_ids)
            for entry in section.entries:
                if entry.identity_evidence_id is not None:
                    assert entry.identity_evidence_id in entry.selected_evidence_ids
                assert all(
                    by_id[identifier].section_reference == entry.source_section_reference
                    for identifier in entry.selected_evidence_ids
                )


def test_stage_c_multiple_roles_keep_original_identity_and_supporting_records():
    first = _entry('role-alpha', EvidenceType.EXPERIENCE, EXPERIENCE, 1, support=5)
    second = _entry('role-beta', EvidenceType.EXPERIENCE, EXPERIENCE, 10, support=5)
    records = [*reversed(second), *reversed(first)]
    plan = _plan(records, max_experience_entries=2)
    experience = _section(plan, 'professional-experience')
    assert experience.selected_evidence_ids == [record.evidence_id for record in first + second]
    assert experience.budget == 2
    assert len(experience.entries) == 2
    assert experience.entry_grouping_complete is True
    assert [entry.identity_evidence_id for entry in experience.entries] == [
        'role-alpha-title', 'role-beta-title',
    ]
    _assert_accounting(plan, records)


@pytest.mark.parametrize('kind,source,destination,field', [
    (EvidenceType.EXPERIENCE, EXPERIENCE, 'professional-experience', 'max_experience_entries'),
    (EvidenceType.PROJECT, PROJECTS, 'projects', 'max_projects'),
    (EvidenceType.EDUCATION, 'education', 'education', 'max_education_entries'),
    (EvidenceType.CERTIFICATION, CERTIFICATIONS, 'certifications', 'max_certifications'),
])
def test_stage_c_entry_budgets_never_split_supporting_records(kind, source, destination, field):
    first = _entry('alpha', kind, source, 1, support=6)
    second = _entry('beta', kind, source, 10, support=6)
    records = first + second
    plan = _plan(records, **{field: 1})
    section = _section(plan, destination)
    assert section.selected_evidence_ids == [record.evidence_id for record in first]
    assert section.entries[0].selected_evidence_ids == section.selected_evidence_ids
    assert section.budget == len(section.entries) == 1
    assert {item.evidence_id: item.reason for item in plan.excluded_evidence} == {
        record.evidence_id: 'content_budget_exhausted' for record in second
    }
    _assert_accounting(plan, records)


def test_stage_c_required_support_selects_the_whole_later_role():
    background = _entry('background-role', EvidenceType.EXPERIENCE, EXPERIENCE, 1)
    relevant = _entry('required-role', EvidenceType.EXPERIENCE, EXPERIENCE, 10)
    relevant[-1].original_content = 'Built documented Python services'
    records = background + relevant
    plan = _plan(records, required=['Python'], max_experience_entries=1)
    assert _section(plan, 'professional-experience').selected_evidence_ids == [
        record.evidence_id for record in relevant
    ]
    assert [skill.skill_text for skill in plan.prioritized_skills] == ['Python']
    _assert_accounting(plan, records)


def test_stage_c_required_and_preferred_skills_survive_earlier_background():
    background = [
        _record(f'background-{index}', EvidenceType.TECHNICAL_SKILL,
                f'Background{index}', 'technical-profile', index)
        for index in range(10)
    ]
    preferred = _record('sql', EvidenceType.TECHNICAL_SKILL, 'SQL', 'technical-profile', 20)
    required = _record('python', EvidenceType.TECHNICAL_SKILL, 'Python', 'technical-profile', 30)
    records = background + [preferred, required]
    plan = _plan(records, required=['Python'], preferred=['SQL'], max_skills=2)
    assert plan.selected_evidence_ids == ['sql', 'python']
    assert [(skill.skill_text, skill.priority_tier) for skill in plan.prioritized_skills] == [
        ('Python', 'required'), ('SQL', 'preferred'),
    ]
    required_only = _plan(records, required=['Python'], preferred=['SQL'], max_skills=1)
    assert required_only.selected_evidence_ids == ['python']
    _assert_accounting(plan, records)


def test_stage_c_other_matcher_relevance_outranks_unmatched_background():
    payload = empty_job_payload()
    payload['responsibilities'] = [{
        'text': 'Maintain local services', 'quotation': 'Maintain local services',
    }]
    records = [
        _record('background', EvidenceType.TECHNICAL_SKILL, 'Go', 'technical-profile', 1),
        _record('relevant', EvidenceType.TECHNICAL_SKILL, 'Maintain local services', 'technical-profile', 10),
    ]
    plan = _plan(records, job=_analysis(payload), max_skills=1)
    assert plan.selected_evidence_ids == ['relevant']
    assert plan.requirement_coverage[0].status == 'supported'


def test_stage_c_education_and_certification_budgets_are_independent():
    education = [
        record for index in range(4)
        for record in _entry(f'degree-{index}', EvidenceType.EDUCATION, 'education', index * 10, support=1)
    ]
    certifications = [
        record for index in range(4)
        for record in _entry(f'cert-{index}', EvidenceType.CERTIFICATION, CERTIFICATIONS, 50 + index * 10, support=1)
    ]
    records = education + certifications
    plan = _plan(records, max_education_entries=1, max_certifications=3)
    assert len(_section(plan, 'education').entries) == 1
    assert _section(plan, 'education').item_count == 2
    assert len(_section(plan, 'certifications').entries) == 3
    assert _section(plan, 'certifications').item_count == 6
    _assert_accounting(plan, records)


def test_stage_c_maximum_output_sections_is_applied_after_relevance():
    records = [
        _record('identity', EvidenceType.PROFESSIONAL_SUMMARY, 'Software engineer', 'professional-identity', 1),
        *_entry('python-project', EvidenceType.PROJECT, PROJECTS, 10, text='Python project'),
    ]
    plan = _plan(records, required=['Python'], max_output_sections=1)
    assert [section.section_id for section in plan.ordered_sections] == ['projects']
    assert {item.evidence_id: item.reason for item in plan.excluded_evidence} == {
        'identity': 'output_section_budget_exhausted',
    }
    _assert_accounting(plan, records)


def test_stage_c_empty_filtered_sections_do_not_consume_section_budget():
    records = [
        _record('contact', EvidenceType.PROFESSIONAL_SUMMARY, 'Email: synthetic@example.invalid', 'professional-identity', 1),
        _record('python', EvidenceType.TECHNICAL_SKILL, 'Python', 'technical-profile', 2),
    ]
    plan = _plan(records, required=['Python'], max_output_sections=1)
    assert [section.section_id for section in plan.ordered_sections] == ['technical-skills']
    assert plan.selected_evidence_ids == ['python']
    _assert_accounting(plan, records)


@pytest.mark.parametrize('field,destination,kind,source', [
    ('max_experience_entries', 'professional-experience', EvidenceType.EXPERIENCE, EXPERIENCE),
    ('max_projects', 'projects', EvidenceType.PROJECT, PROJECTS),
    ('max_education_entries', 'education', EvidenceType.EDUCATION, 'education'),
    ('max_certifications', 'certifications', EvidenceType.CERTIFICATION, CERTIFICATIONS),
])
def test_stage_c_target_pages_tightens_entry_budgets(field, destination, kind, source):
    records = [
        record for index in range(5)
        for record in _entry(f'entry-{index}', kind, source, index * 10, support=1)
    ]
    one = _plan(records, target_pages=1)
    two = _plan(records, target_pages=2)
    assert _section(one, destination).budget == 2
    assert len(_section(one, destination).entries) == 2
    assert _section(two, destination).budget == 5
    assert len(_section(two, destination).entries) == 5
    assert getattr(one.budgets, field) == 5
    assert one.budgets.page_guarantee is two.budgets.page_guarantee is False
    _assert_accounting(one, records)


def test_stage_c_target_pages_tightens_skills_and_preserves_zero_budgets():
    records = [
        _record(f'skill-{index}', EvidenceType.TECHNICAL_SKILL,
                f'Skill{index}', 'technical-profile', index)
        for index in range(10)
    ]
    one = _plan(records, target_pages=1)
    two = _plan(records, target_pages=2)
    assert _section(one, 'technical-skills').budget == 7
    assert len(one.prioritized_skills) == len(one.selected_evidence_ids) == 7
    assert len(two.prioritized_skills) == len(two.selected_evidence_ids) == 10
    zero = _plan(records, max_skills=0, target_pages=1)
    assert zero.ordered_sections == []
    assert zero.prioritized_skills == []
    _assert_accounting(zero, records)


def test_stage_c_page_guarantees_cannot_be_claimed_by_the_planner():
    with pytest.raises(ValidationError, match='page count'):
        budgets = PlanBudgets(**DEFAULT_BUDGETS, page_guarantee=True)
        SectionPlanner(budgets)


@pytest.mark.parametrize('records', [
    [],
    [_record('heading', EvidenceType.TECHNICAL_SKILL, '2. Technical Profile', 'technical-profile', 1)],
    [_record('contact', EvidenceType.CONTACT, 'Email: synthetic@example.invalid', 'technical-profile', 1)],
    [_record('blank-skill', EvidenceType.TECHNICAL_SKILL, '  \t', 'technical-profile', 1)],
    [_record('blank-role', EvidenceType.EXPERIENCE, '\n', EXPERIENCE, 1)],
])
def test_stage_c_empty_output_sections_are_omitted_even_after_filtering(records):
    plan = _plan(records)
    assert plan.ordered_sections == []
    _assert_accounting(plan, records)


def test_stage_c_every_record_has_one_outcome_and_distinct_exclusion_reasons():
    records = [
        _record('python', EvidenceType.TECHNICAL_SKILL, 'Python', 'technical-profile', 0),
        _record('background', EvidenceType.TECHNICAL_SKILL, 'Go', 'technical-profile', 1),
        _record('contact', EvidenceType.TECHNICAL_SKILL, 'Email: synthetic@example.invalid', 'technical-profile', 2),
        _record('unclassified', EvidenceType.UNCLASSIFIED, 'Unclassified record', None, 3),
        _record('unmapped', EvidenceType.LEADERSHIP, 'Documented leadership', 'leadership-and-demonstrated-competencies', 4),
        _record('wrong-type', EvidenceType.CONTACT, 'Python', 'technical-profile', 5),
    ]
    plan = _plan(records, required=['Python'], max_skills=1)
    assert {item.evidence_id: item.reason for item in plan.excluded_evidence} == {
        'background': 'content_budget_exhausted',
        'contact': 'content_not_eligible_for_output_section',
        'unclassified': 'unclassified_section_no_output_mapping',
        'unmapped': 'source_section_not_mapped_to_output_section',
        'wrong-type': 'content_not_eligible_for_output_section',
    }
    _assert_accounting(plan, records)


def test_stage_c_source_references_are_recomputed_after_budget_selection():
    first = _entry('first-project', EvidenceType.PROJECT, PROJECTS, 1)
    second = _entry('second-project', EvidenceType.PROJECT, OTHER_PROJECTS, 10)
    records = first + second
    plan = _plan(records, max_projects=1)
    assert _section(plan, 'projects').source_section_references == [PROJECTS]
    assert _section(plan, 'projects').entries[0].source_section_reference == PROJECTS
    _assert_accounting(plan, records)


def test_stage_c_source_sections_never_merge_unmarked_spans():
    records = [
        _record('first', EvidenceType.PROJECT, 'Unmarked source content', PROJECTS, 1),
        _record('second', EvidenceType.PROJECT, 'Another unmarked source', OTHER_PROJECTS, 2),
    ]
    section = _section(_plan(records), 'projects')
    assert [entry.source_section_reference for entry in section.entries] == [PROJECTS, OTHER_PROJECTS]


def test_stage_c_unmarked_and_bold_only_records_do_not_invent_role_boundaries():
    records = [
        _record('first', EvidenceType.EXPERIENCE, 'First source text', EXPERIENCE, 1, bold=True),
        _record('support', EvidenceType.EXPERIENCE, 'Documented support', EXPERIENCE, 2),
        _record('second', EvidenceType.EXPERIENCE, 'Different source text', EXPERIENCE, 3, bold=True),
    ]
    plan = _plan(records, max_experience_entries=1)
    section = _section(plan, 'professional-experience')
    assert section.selected_evidence_ids == ['first', 'support', 'second']
    assert section.entry_grouping_complete is False
    assert len(section.entries) == 1
    assert section.entries[0].grouping_basis == 'unresolved_source_span'
    assert section.entries[0].identity_evidence_id is None
    _assert_accounting(plan, records)


def test_stage_c_legacy_records_without_boundaries_remain_an_indivisible_unresolved_span():
    records = [
        _evidence_record(f'legacy-{index}', EvidenceType.EXPERIENCE, f'Source paragraph {index}',
                         section_reference=EXPERIENCE, block_index=index)
        for index in range(5)
    ]
    plan = _plan(records, max_experience_entries=1)
    section = _section(plan, 'professional-experience')
    assert section.selected_evidence_ids == [record.evidence_id for record in records]
    assert section.entries[0].grouping_basis == 'unresolved_source_span'
    assert section.entry_grouping_complete is False
    _assert_accounting(plan, records)


def test_stage_c_numbered_bold_catalog_flags_without_source_runs_do_not_invent_entries():
    records = [
        _record('section-heading', EvidenceType.EXPERIENCE,
                '1. Professional Experience And Engineering Training', EXPERIENCE, 0, style='Heading 1'),
        _record('first', EvidenceType.EXPERIENCE, '1.1. First source title', EXPERIENCE, 1, bold=True),
        _record('support', EvidenceType.EXPERIENCE, 'Source support', EXPERIENCE, 2),
        _record('second', EvidenceType.EXPERIENCE, '1.2. Second source title', EXPERIENCE, 3, bold=True),
    ]
    plan = _plan(records, max_experience_entries=1)
    section = _section(plan, 'professional-experience')
    assert section.selected_evidence_ids == ['first', 'support', 'second']
    assert section.entry_grouping_complete is False
    assert section.entries[0].identity_evidence_id is None
    _assert_accounting(plan, records)


def test_stage_c_partial_bold_numbered_paragraphs_stop_affected_grouping(tmp_path):
    document = Document()
    document.add_heading('1. Professional Experience And Engineering Training', level=1)
    title = document.add_paragraph('1.1. First source title')
    title.runs[0].bold = True
    document.add_paragraph('First source support')
    uncertain = document.add_paragraph()
    uncertain.add_run('1.2. ').bold = True
    uncertain.add_run('Partially bold source content').bold = False
    document.add_paragraph('Further source support')
    source = builders.save(document, tmp_path / 'partial-bold-synthetic.docx')
    snapshot = MasterResumeExtractor(source).extract()
    catalog = build_evidence_catalog(snapshot)
    paragraph = next(block.paragraph for block in snapshot.blocks if block.paragraph and 'Partially bold' in block.paragraph.text)
    assert paragraph.bold is True
    assert any(run.bold is False for run in paragraph.runs)
    job = _stage_b_analysis()
    plan = SectionPlanner(PlanBudgets(**{**DEFAULT_BUDGETS, 'max_experience_entries': 1})).plan(
        job, _match(job, catalog), catalog, snapshot,
    )
    section = _section(plan, 'professional-experience')
    assert section.item_count == 4
    assert section.entry_grouping_complete is False
    assert section.entries[0].grouping_basis == 'unresolved_source_span'
    assert section.entries[0].identity_evidence_id is None
    _assert_accounting(plan, catalog.evidence_records)


def test_stage_c_bold_bullets_are_support_not_new_role_entries(tmp_path):
    document = Document()
    document.add_heading('1. Professional Experience And Engineering Training', level=1)
    title = document.add_paragraph('1.1. Original role title')
    title.runs[0].bold = True
    bullet = document.add_paragraph('1.2. Documented supporting task', style='List Bullet')
    bullet.runs[0].bold = True
    source = builders.save(document, tmp_path / 'bold-bullet-synthetic.docx')
    snapshot = MasterResumeExtractor(source).extract()
    catalog = build_evidence_catalog(snapshot)
    job = _stage_b_analysis()
    plan = SectionPlanner(PlanBudgets(**{**DEFAULT_BUDGETS, 'max_experience_entries': 1})).plan(
        job, _match(job, catalog), catalog, snapshot,
    )
    section = _section(plan, 'professional-experience')
    assert len(section.entries) == 1
    assert section.item_count == 2
    assert section.entry_grouping_complete is True
    _assert_accounting(plan, catalog.evidence_records)


@pytest.mark.parametrize('field,kind,source', [
    ('max_experience_entries', EvidenceType.EXPERIENCE, EXPERIENCE),
    ('max_projects', EvidenceType.PROJECT, PROJECTS),
    ('max_education_entries', EvidenceType.EDUCATION, 'education'),
    ('max_certifications', EvidenceType.CERTIFICATION, CERTIFICATIONS),
])
def test_stage_c_zero_entry_budgets_exclude_whole_entries_and_omit_sections(field, kind, source):
    records = _entry('original-entry', kind, source, 1)
    plan = _plan(records, target_pages=1, **{field: 0})
    assert plan.ordered_sections == []
    assert all(item.reason == 'content_budget_exhausted' for item in plan.excluded_evidence)
    _assert_accounting(plan, records)


@pytest.mark.parametrize('tamper', ['duplicate_membership', 'unknown_identity', 'invented_identity'])
def test_stage_c_entry_contract_rejects_inconsistent_membership(tamper):
    plan = _plan(_entry('role', EvidenceType.EXPERIENCE, EXPERIENCE, 1))
    data = _section(plan, 'professional-experience').entries[0].model_dump(mode='json')
    if tamper == 'duplicate_membership':
        data['selected_evidence_ids'].append(data['selected_evidence_ids'][0])
    elif tamper == 'unknown_identity':
        data['identity_evidence_id'] = 'unknown-identity'
    else:
        data['grouping_basis'] = 'unresolved_source_span'
    with pytest.raises(ValidationError):
        PlannedResumeEntry.model_validate_json(json.dumps(data))


def test_stage_c_lower_level_headings_remain_with_their_parent_entry():
    first = _entry('first', EvidenceType.PROJECT, PROJECTS, 1, support=1)
    subheading = _record('subheading', EvidenceType.PROJECT, 'Documented component', PROJECTS, 3, style='Heading 3')
    subcontent = _record('subcontent', EvidenceType.PROJECT, 'Component support', PROJECTS, 4)
    second = _entry('second', EvidenceType.PROJECT, PROJECTS, 10, support=1)
    records = [*first, subheading, subcontent, *second]
    plan = _plan(records, max_projects=1)
    assert _section(plan, 'projects').selected_evidence_ids == [
        'first-title', 'first-support-0', 'subheading', 'subcontent',
    ]
    _assert_accounting(plan, records)


def test_stage_c_table_rows_and_nested_support_form_whole_entries():
    first = _table_record('f' * 64, EvidenceType.EXPERIENCE, EXPERIENCE, row=0)
    second = _table_record('0' * 64, EvidenceType.EXPERIENCE, EXPERIENCE, row=1)
    nested = _table_record('9' * 64, EvidenceType.EXPERIENCE, EXPERIENCE,
                           row=0, table=-1, path=[5, 1, 0], text='Python nested support')
    records = [nested, second, first]
    plan = _plan(records, required=['Python'], max_experience_entries=1)
    section = _section(plan, 'professional-experience')
    assert section.selected_evidence_ids == [second.evidence_id, nested.evidence_id]
    assert len(section.entries) == 1
    assert section.entries[0].identity_evidence_id == second.evidence_id
    assert section.entries[0].grouping_basis == 'table_row'
    _assert_accounting(plan, records)


def test_stage_c_missing_parent_rows_are_not_given_invented_identities():
    records = [
        _table_record('nested-first', EvidenceType.EXPERIENCE, EXPERIENCE,
                      row=0, table=-1, path=[5, 0, 0]),
        _table_record('nested-second', EvidenceType.EXPERIENCE, EXPERIENCE,
                      row=1, table=-1, path=[5, 0, 0]),
    ]
    plan = _plan(records, max_experience_entries=1)
    section = _section(plan, 'professional-experience')
    assert section.item_count == 2
    assert section.entry_grouping_complete is False
    assert section.entries[0].identity_evidence_id is None
    _assert_accounting(plan, records)


def test_stage_c_table_rows_follow_source_order_before_hash_tie_breaks():
    first = _table_record('f' * 64, EvidenceType.EXPERIENCE, EXPERIENCE, row=0)
    second = _table_record('0' * 64, EvidenceType.EXPERIENCE, EXPERIENCE, row=1)
    plan = _plan([second, first])
    assert _section(plan, 'professional-experience').selected_evidence_ids == [
        first.evidence_id, second.evidence_id,
    ]


def test_stage_c_sibling_and_deep_nested_tables_keep_extractor_preorder():
    locations = [
        (5, 0, None),
        (-1, 0, [5, 0, 0]),
        (-2, 0, [5, 0, 0, -1, 0, 0]),
        (-3, 0, [5, 0, 0]),
        (5, 1, None),
    ]
    records = [
        _table_record(f'{9 - index:x}' * 64, EvidenceType.TECHNICAL_SKILL,
                      'technical-profile', table=table, row=row, path=path)
        for index, (table, row, path) in enumerate(locations)
    ]
    plan = _plan(list(reversed(records)))
    assert plan.selected_evidence_ids == [record.evidence_id for record in records]


def test_stage_c_paragraph_cell_and_position_coordinates_precede_hashes():
    later = _record('0' * 64, EvidenceType.TECHNICAL_SKILL, 'SQL', 'technical-profile', 1)
    earlier = _record('f' * 64, EvidenceType.TECHNICAL_SKILL, 'Python', 'technical-profile', 1)
    earlier.source_location.paragraph_index = 0
    later.source_location.paragraph_index = 1
    first_cell = _table_record('e' * 64, EvidenceType.TECHNICAL_SKILL, 'technical-profile', row=0, cell=0, text='Go')
    later_cell = _table_record('1' * 64, EvidenceType.TECHNICAL_SKILL, 'technical-profile', row=0, cell=1, text='Rust')
    first_position = later_cell.model_copy(deep=True)
    first_position.evidence_id = 'd' * 64
    first_position.source_location.paragraph_position_in_cell = 0
    later_cell.source_location.paragraph_position_in_cell = 1
    records = [later, earlier, later_cell, first_position, first_cell]
    plan = _plan(records)
    assert plan.selected_evidence_ids == [
        earlier.evidence_id, later.evidence_id, first_cell.evidence_id,
        first_position.evidence_id, later_cell.evidence_id,
    ]


def test_stage_c_table_support_belongs_to_its_explicit_paragraph_entry():
    title = _entry('role', EvidenceType.EXPERIENCE, EXPERIENCE, 1, support=1)
    row = _table_record('table-support', EvidenceType.EXPERIENCE, EXPERIENCE, row=0)
    nested = _table_record('nested-support', EvidenceType.EXPERIENCE, EXPERIENCE,
                           row=0, table=-1, path=[5, 0, 0])
    next_role = _entry('next-role', EvidenceType.EXPERIENCE, EXPERIENCE, 10, support=1)
    records = [*title, row, nested, *next_role]
    plan = _plan(records, max_experience_entries=1)
    assert _section(plan, 'professional-experience').entries[0].selected_evidence_ids == [
        'role-title', 'role-support-0', 'table-support', 'nested-support',
    ]
    _assert_accounting(plan, records)


def test_stage_c_legacy_plan_shape_loads_with_defaulted_entry_and_education_fields():
    plan = _plan([_record('python', EvidenceType.TECHNICAL_SKILL, 'Python', 'technical-profile', 1)])
    legacy = legacy_plan_payload(plan)
    legacy['budgets'].pop('max_education_entries')
    for section in legacy['ordered_sections']:
        section.pop('entries')
        section.pop('entry_grouping_complete')
    restored = LegacyResumeSectionPlan.model_validate_json(json.dumps(legacy))
    assert restored.budgets.max_education_entries == DEFAULT_BUDGETS['max_education_entries']
    assert restored.ordered_sections[0].entries == []


def test_stage_c_managed_extraction_and_cli_preserve_numbered_roles_and_source_bytes(tmp_path, monkeypatch, capsys):
    document = Document()
    document.add_heading('1. Professional Experience And Engineering Training', level=1)
    for index in (1, 2):
        title = document.add_paragraph(f'1.{index} SYNTHETIC_STAGE_C_ROLE_{index}')
        title.runs[0].bold = True
        document.add_paragraph(f'Original synthetic date text for role {index}')
        for task in range(3):
            document.add_paragraph(f'Python task {task} for synthetic role {index}')
    document.add_heading('2. Technical Profile', level=1)
    document.add_paragraph('Python')
    source = builders.save(document, tmp_path / 'stage-c-synthetic.docx')
    source_before = source.read_bytes()
    processed = tmp_path / 'data/private/processed'
    candidate = processed / 'candidate.json'
    monkeypatch.setattr(cli, 'PROJECT_ROOT', tmp_path)
    cli.run_extract_master(source, candidate, processed / 'snapshot-selector.json')
    snapshot, catalog, manifest, _ = load_committed_pair(tmp_path, candidate)
    assert manifest is not None
    job = _stage_b_analysis(required=['Python'])
    analysis = processed / 'analysis.json'
    analysis.write_text(job.model_dump_json(), encoding='utf-8')
    matched = processed / 'match.json'
    cli.run_match_job(analysis, candidate, matched)
    generation = cli.publication_store.generation_dir_path(tmp_path, manifest.bundle_key, manifest.generation_id)
    protected = [source, candidate, analysis, matched, generation / 'catalog.json', generation / 'snapshot.json']
    before = {path: path.read_bytes() for path in protected}
    capsys.readouterr()
    output = processed / 'plan.json'
    assert cli.main([
        'plan-sections', '--analysis', str(analysis), '--match', str(matched),
        '--candidate-evidence', str(candidate), '--output', str(output),
    ]) == 0
    captured = capsys.readouterr()
    assert 'SYNTHETIC_STAGE_C_ROLE_' not in captured.out + captured.err
    assert 'Python' not in captured.out + captured.err
    plan = ResumeSectionPlan.model_validate_json(output.read_bytes())
    experience = _section(plan, 'professional-experience')
    assert len(experience.entries) == 2
    assert experience.item_count == 10
    assert experience.entry_grouping_complete is True
    by_id = {record.evidence_id: record for record in catalog.evidence_records}
    assert [by_id[entry.identity_evidence_id].original_content for entry in experience.entries] == [
        '1.1 SYNTHETIC_STAGE_C_ROLE_1', '1.2 SYNTHETIC_STAGE_C_ROLE_2',
    ]
    assert source.read_bytes() == source_before
    assert {path: path.read_bytes() for path in protected} == before
    assert stat.S_IMODE(output.stat().st_mode) == 0o600
    assert plan.planning_metadata.snapshot_available is True
    _assert_accounting(plan, catalog.evidence_records)
    direct = SectionPlanner().plan(
        job, _match(job, catalog), catalog, snapshot, manifest_available=True,
        extraction_generation=ExtractionGenerationIdentity.from_manifest(manifest),
    )
    assert direct == plan


def _stage_c_determinism_plan():
    records = [
        *_entry('early', EvidenceType.EXPERIENCE, EXPERIENCE, 1, support=2),
        *_entry('late', EvidenceType.EXPERIENCE, EXPERIENCE, 10, support=2, text='Python original role'),
        _record('contact', EvidenceType.TECHNICAL_SKILL, 'LinkedIn profile: synthetic', 'technical-profile', 20),
        _record('python', EvidenceType.TECHNICAL_SKILL, 'Python', 'technical-profile', 21),
        _record('sql', EvidenceType.TECHNICAL_SKILL, 'SQL', 'technical-profile', 22),
    ]
    return _plan(list(reversed(records)), required=['Python'], preferred=['SQL'], max_experience_entries=1)


def test_stage_c_output_is_identical_across_repeated_runs_and_hash_seeds():
    baseline = _stage_c_determinism_plan().model_dump_json(indent=2, warnings='error').encode('utf-8')
    for _ in range(10):
        assert _stage_c_determinism_plan().model_dump_json(indent=2, warnings='error').encode('utf-8') == baseline
    expected = hashlib.sha256(baseline).hexdigest()
    script = (
        'import hashlib; from tests.test_section_planner_stage_c import _stage_c_determinism_plan; '
        'print(hashlib.sha256(_stage_c_determinism_plan().model_dump_json(indent=2, warnings="error").encode()).hexdigest())'
    )
    for seed in ('1', '17', '103'):
        result = subprocess.run(
            [sys.executable, '-B', '-c', script], cwd=Path(__file__).resolve().parents[1],
            env={**os.environ, 'PYTHONHASHSEED': seed, 'PYTHONDONTWRITEBYTECODE': '1'},
            capture_output=True, text=True, check=True,
        )
        assert result.stdout.strip() == expected


def _sc_docx_inputs(document, tmp_path):
    source = builders.save(document, tmp_path / 'sc-remediation-synthetic.docx')
    before = source.read_bytes()
    snapshot = MasterResumeExtractor(source).extract()
    catalog = build_evidence_catalog(snapshot)
    validate_snapshot_structure(snapshot)
    validate_catalog_structure(catalog)
    validate_publication_pair(snapshot, catalog)
    assert source.read_bytes() == before
    return snapshot, catalog


def _sc_plan(snapshot, catalog, job=None, **caps):
    job = job or _stage_b_analysis(required=['Python'])
    return SectionPlanner(PlanBudgets(**{**DEFAULT_BUDGETS, **caps})).plan(
        job, _match(job, catalog), catalog, snapshot,
    )


def _sc_vertical_document(heading='Professional Experience And Engineering Training'):
    document = Document()
    document.add_heading('1. ' + heading, level=1)
    table = document.add_table(rows=2, cols=2)
    table.cell(0, 0).text = 'Original synthetic entry identity'
    table.cell(0, 1).text = 'Original synthetic date and scope'
    table.cell(1, 1).text = 'Python supporting evidence'
    table.cell(0, 0).merge(table.cell(1, 0))
    return document, table


def _sc_mark_vertical(table, row, column, value):
    marker = OxmlElement('w:vMerge')
    if value is not None:
        marker.set(qn('w:val'), value)
    table._tbl.tr_lst[row].tc_lst[column].get_or_add_tcPr().append(marker)


@pytest.mark.parametrize('heading,destination,cap', [
    ('Professional Experience And Engineering Training', 'professional-experience', 'max_experience_entries'),
    ('Independent Software And AI Projects', 'projects', 'max_projects'),
    ('Education', 'education', 'max_education_entries'),
    ('Certifications And Professional Development', 'certifications', 'max_certifications'),
])
def test_sc_r1_vertical_merge_retains_identity_and_support(tmp_path, heading, destination, cap):
    document, _table = _sc_vertical_document(heading)
    snapshot, catalog = _sc_docx_inputs(document, tmp_path)
    source_table = next(block.table for block in snapshot.blocks if block.table is not None)
    assert [row[0].v_merge for row in source_table.rows] == ['restart', 'continue']
    records = [record for record in catalog.evidence_records if record.table_context is not None]
    plan = _sc_plan(snapshot, catalog, **{cap: 1})
    section = _section(plan, destination)
    assert section.selected_evidence_ids == [record.evidence_id for record in records]
    assert len(section.entries) == section.budget == 1
    assert section.entries[0].identity_evidence_id == records[0].evidence_id
    assert section.entries[0].selected_evidence_ids == section.selected_evidence_ids
    assert section.entry_grouping_complete is True
    assert [skill.skill_text for skill in plan.prioritized_skills] == ['Python']
    _assert_accounting(plan, catalog.evidence_records)


def test_sc_r1_vertical_merge_keeps_nested_support_with_its_identity(tmp_path):
    document, table = _sc_vertical_document()
    table.cell(1, 1).text = 'Original supporting row'
    table.cell(1, 1).add_table(rows=1, cols=1).cell(0, 0).text = 'Python nested supporting evidence'
    snapshot, catalog = _sc_docx_inputs(document, tmp_path)
    records = [record for record in catalog.evidence_records if record.table_context is not None]
    assert len(records) == 3 and records[-1].source_location.nested_table_path
    plan = _sc_plan(snapshot, catalog, max_experience_entries=1)
    section = _section(plan, 'professional-experience')
    assert section.selected_evidence_ids == [record.evidence_id for record in records]
    assert section.entries[0].identity_evidence_id == records[0].evidence_id
    assert section.entry_grouping_complete is True
    _assert_accounting(plan, catalog.evidence_records)


def test_sc_r1_ordinary_adjacent_rows_remain_independent(tmp_path):
    document = Document()
    document.add_heading('1. Professional Experience And Engineering Training', level=1)
    table = document.add_table(rows=2, cols=1)
    table.cell(0, 0).text = 'Original unrelated role'
    table.cell(1, 0).text = 'Original Python role'
    snapshot, catalog = _sc_docx_inputs(document, tmp_path)
    rows = [record for record in catalog.evidence_records if record.table_context is not None]
    plan = _sc_plan(snapshot, catalog, max_experience_entries=1)
    section = _section(plan, 'professional-experience')
    assert section.selected_evidence_ids == [rows[1].evidence_id]
    assert section.entries[0].identity_evidence_id == rows[1].evidence_id
    assert section.entry_grouping_complete is True
    assert next(item.reason for item in plan.excluded_evidence if item.evidence_id == rows[0].evidence_id) == 'content_budget_exhausted'
    _assert_accounting(plan, catalog.evidence_records)


def test_sc_r1_orphan_continuations_are_connected_but_unresolved(tmp_path):
    document = Document()
    document.add_heading('1. Professional Experience And Engineering Training', level=1)
    table = document.add_table(rows=3, cols=2)
    table.cell(0, 0).text = 'Ordinary unrelated role'
    table.cell(1, 1).text = 'Unresolved source context'
    table.cell(2, 1).text = 'Python supporting source'
    _sc_mark_vertical(table, 1, 0, None)
    _sc_mark_vertical(table, 2, 0, None)
    snapshot, catalog = _sc_docx_inputs(document, tmp_path)
    rows = [record for record in catalog.evidence_records if record.table_context is not None]
    plan = _sc_plan(snapshot, catalog, max_experience_entries=1)
    section = _section(plan, 'professional-experience')
    assert section.selected_evidence_ids == [record.evidence_id for record in rows[1:]]
    assert section.entries[0].grouping_basis == 'unresolved_source_span'
    assert section.entries[0].identity_evidence_id is None
    assert section.entry_grouping_complete is False
    assert rows[0].evidence_id not in plan.selected_evidence_ids
    _assert_accounting(plan, catalog.evidence_records)


def test_sc_r1_ragged_merge_alignment_preserves_an_unresolved_span(tmp_path):
    document = Document()
    document.add_heading('1. Professional Experience And Engineering Training', level=1)
    table = document.add_table(rows=2, cols=3)
    table.cell(0, 0).text = 'Original source identity'
    table.cell(0, 1).text = 'Original source dates'
    table.cell(1, 1).text = 'Python supporting source'
    _sc_mark_vertical(table, 0, 0, 'restart')
    _sc_mark_vertical(table, 1, 0, None)
    table._tbl.tr_lst[1].remove(table._tbl.tr_lst[1].tc_lst[-1])
    snapshot, catalog = _sc_docx_inputs(document, tmp_path)
    rows = [record for record in catalog.evidence_records if record.table_context is not None]
    plan = _sc_plan(snapshot, catalog, max_experience_entries=1)
    section = _section(plan, 'professional-experience')
    assert section.selected_evidence_ids == [record.evidence_id for record in rows]
    assert section.entries[0].grouping_basis == 'unresolved_source_span'
    assert section.entries[0].identity_evidence_id is None
    assert section.entry_grouping_complete is False
    _assert_accounting(plan, catalog.evidence_records)


def test_sc_r1_filtered_merge_identity_is_not_restored_or_claimed_complete(tmp_path):
    document, table = _sc_vertical_document()
    table.cell(0, 0).text = 'Original source identity\nEmail: synthetic@example.invalid'
    snapshot, catalog = _sc_docx_inputs(document, tmp_path)
    rows = [record for record in catalog.evidence_records if record.table_context is not None]
    plan = _sc_plan(snapshot, catalog, max_experience_entries=1)
    section = _section(plan, 'professional-experience')
    assert section.selected_evidence_ids == [rows[1].evidence_id]
    assert section.entries[0].identity_evidence_id is None
    assert section.entry_grouping_complete is False
    assert next(item.reason for item in plan.excluded_evidence if item.evidence_id == rows[0].evidence_id) == 'content_not_eligible_for_output_section'
    assert [skill.skill_text for skill in plan.prioritized_skills] == ['Python']
    _assert_accounting(plan, catalog.evidence_records)


def test_sc_r1_multiple_merge_origins_preserve_a_connected_unresolved_span(tmp_path):
    document = Document()
    document.add_heading('1. Professional Experience And Engineering Training', level=1)
    table = document.add_table(rows=3, cols=2)
    table.cell(0, 0).text = 'Original first identity'
    table.cell(0, 1).text = 'Original source dates'
    table.cell(1, 1).text = 'Original second identity'
    table.cell(2, 0).text = 'Python supporting source'
    for row, column, value in ((0, 0, 'restart'), (1, 0, None), (1, 1, 'restart'), (2, 1, None)):
        _sc_mark_vertical(table, row, column, value)
    snapshot, catalog = _sc_docx_inputs(document, tmp_path)
    rows = [record for record in catalog.evidence_records if record.table_context is not None]
    plan = _sc_plan(snapshot, catalog, max_experience_entries=1)
    section = _section(plan, 'professional-experience')
    assert section.selected_evidence_ids == [record.evidence_id for record in rows]
    assert section.entries[0].identity_evidence_id is None
    assert section.entry_grouping_complete is False
    _assert_accounting(plan, catalog.evidence_records)


def test_sc_r1_grid_spanned_vertical_merges_use_logical_cell_ranges(tmp_path):
    document = Document()
    document.add_heading('1. Professional Experience And Engineering Training', level=1)
    table = document.add_table(rows=2, cols=3)
    table.cell(0, 0).text = 'Original spanning identity'
    table.cell(0, 2).text = 'Original source dates'
    table.cell(1, 2).text = 'Python supporting source'
    table.cell(0, 0).merge(table.cell(1, 1))
    snapshot, catalog = _sc_docx_inputs(document, tmp_path)
    source_table = next(block.table for block in snapshot.blocks if block.table is not None)
    assert [row[0].grid_span for row in source_table.rows] == [2, 2]
    rows = [record for record in catalog.evidence_records if record.table_context is not None]
    plan = _sc_plan(snapshot, catalog, max_experience_entries=1)
    section = _section(plan, 'professional-experience')
    assert section.selected_evidence_ids == [record.evidence_id for record in rows]
    assert section.entries[0].identity_evidence_id == rows[0].evidence_id
    assert section.entry_grouping_complete is True
    _assert_accounting(plan, catalog.evidence_records)


def _sc_repeated_sections_document():
    document = Document()
    for parent, skills in ((1, ('Python', 'Java')), (4, ('Go', 'Rust'))):
        document.add_heading(f'{parent}. Professional Experience And Engineering Training', level=1)
        for child, skill in enumerate(skills, 1):
            title = document.add_paragraph(f'{parent}.{child}. Original {skill} role')
            title.runs[0].bold = True
            document.add_paragraph(f'Original source support {parent}-{child}')
    return document


@pytest.mark.parametrize('required,expected_title,expected_support', [
    ((), '1.1. Original Python role', 'Original source support 1-1'),
    (('Go',), '4.1. Original Go role', 'Original source support 4-1'),
])
def test_sc_r2_repeated_sections_use_enclosing_numbering(tmp_path, required, expected_title, expected_support):
    snapshot, catalog = _sc_docx_inputs(_sc_repeated_sections_document(), tmp_path)
    assert [(section.numbering, section.start_block_index, section.end_block_index) for section in snapshot.sections] == [
        ('1.', 0, 4), ('4.', 5, 9),
    ]
    plan = _sc_plan(snapshot, catalog, _stage_b_analysis(required=required), max_experience_entries=1)
    section = _section(plan, 'professional-experience')
    by_id = {record.evidence_id: record for record in catalog.evidence_records}
    assert len(section.entries) == 1
    assert [by_id[identifier].original_content for identifier in section.selected_evidence_ids] == [
        expected_title, expected_support,
    ]
    assert by_id[section.entries[0].identity_evidence_id].original_content == expected_title
    assert section.entry_grouping_complete is True
    _assert_accounting(plan, catalog.evidence_records)


@pytest.mark.parametrize('numbering_available', [True, False])
def test_sc_r2_source_boundaries_split_runs_when_catalog_headings_are_omitted(tmp_path, numbering_available):
    snapshot, catalog = _sc_docx_inputs(_sc_repeated_sections_document(), tmp_path)
    starts = {section.start_block_index for section in snapshot.sections}
    records = [record for record in catalog.evidence_records if record.source_location.body_block_index not in starts]
    catalog = catalog.model_copy(update={
        'evidence_records': records,
        'extraction_completeness': catalog.extraction_completeness.model_copy(update={'evidence_records': len(records)}),
    })
    if not numbering_available:
        snapshot = snapshot.model_copy(update={
            'sections': [section.model_copy(update={'numbering': None}) for section in snapshot.sections],
        })
    validate_catalog_structure(catalog)
    with pytest.raises(PublicationError, match='Snapshot content has no matching evidence record'):
        validate_publication_pair(snapshot, catalog)
    plan = _sc_plan(snapshot, catalog, _stage_b_analysis(required=['Go']), max_experience_entries=1)
    section = _section(plan, 'professional-experience')
    by_id = {record.evidence_id: record for record in records}
    assert [by_id[identifier].original_content for identifier in section.selected_evidence_ids] == [
        '4.1. Original Go role', 'Original source support 4-1',
    ]
    assert section.entry_grouping_complete is True
    _assert_accounting(plan, records)


def _sc_cross_budget_document(extra_python=False):
    document = Document()
    document.add_heading('1. Professional Experience And Engineering Training', level=1)
    for child, skill in ((1, 'Python'), (2, 'Go')):
        title = document.add_paragraph(f'1.{child}. Original {skill} role')
        title.runs[0].bold = True
        document.add_paragraph(f'Original supporting record for role {child}')
    document.add_heading('2. Technical Profile', level=1)
    document.add_paragraph('SQL')
    if extra_python:
        document.add_paragraph('PYTHON')
    return document


def test_sc_r3_skill_budget_is_applied_after_survival(tmp_path):
    snapshot, catalog = _sc_docx_inputs(_sc_cross_budget_document(), tmp_path)
    job = _stage_b_analysis(required=['Python', 'Go'], preferred=['SQL'])
    matched = _match(job, catalog)
    plan = _sc_plan(snapshot, catalog, job, max_experience_entries=1, max_skills=2)
    by_text = {record.original_content: record for record in catalog.evidence_records}
    assert [skill.skill_text for skill in plan.prioritized_skills] == ['Python', 'SQL']
    assert plan.professional_summary_blueprint.technical_capability_skill_texts == ['Python', 'SQL']
    assert _section(plan, 'professional-experience').selected_evidence_ids == [
        by_text['1.1. Original Python role'].evidence_id,
        by_text['Original supporting record for role 1'].evidence_id,
    ]
    go = by_text['1.2. Original Go role'].evidence_id
    assert go not in plan.selected_evidence_ids
    assert next(item.reason for item in plan.excluded_evidence if item.evidence_id == go) == 'content_budget_exhausted'
    assert by_text['SQL'].evidence_id in plan.selected_evidence_ids
    assert [item.status for item in plan.requirement_coverage] == [item.status for item in matched.requirements]
    assert all(set(skill.matched_evidence_ids).issubset(plan.selected_evidence_ids) for skill in plan.prioritized_skills)
    assert all(any(identifier in plan.selected_evidence_ids for identifier in skill.matched_evidence_ids) for skill in plan.prioritized_skills)
    _assert_accounting(plan, catalog.evidence_records)


def test_sc_r3_refill_preserves_aggregated_evidence_and_requirement_associations(tmp_path):
    snapshot, catalog = _sc_docx_inputs(_sc_cross_budget_document(extra_python=True), tmp_path)
    job = _stage_b_analysis(required=['Python', 'Go'], preferred=['Python and SQL'])
    matched = _match(job, catalog)
    plan = _sc_plan(snapshot, catalog, job, max_experience_entries=1, max_skills=2)
    by_text = {record.original_content: record for record in catalog.evidence_records}
    assert [skill.skill_text for skill in plan.prioritized_skills] == ['Python', 'SQL']
    python, sql = plan.prioritized_skills
    assert python.requirement_id == matched.requirements[0].requirement_id
    assert set(python.matched_evidence_ids) == {
        by_text['1.1. Original Python role'].evidence_id, by_text['PYTHON'].evidence_id,
    }
    assert [association.requirement_id for association in python.requirement_associations] == [
        matched.requirements[0].requirement_id, matched.requirements[2].requirement_id,
    ]
    assert [association.priority_tier for association in python.requirement_associations] == ['required', 'preferred']
    assert all(association.matched_evidence_ids == [by_text['1.1. Original Python role'].evidence_id] for association in python.requirement_associations)
    assert sql.requirement_associations[0].requirement_id == matched.requirements[2].requirement_id
    assert all(set(skill.matched_evidence_ids).issubset(plan.selected_evidence_ids) for skill in plan.prioritized_skills)
    assert plan.professional_summary_blueprint.technical_capability_skill_texts == ['Python', 'SQL']
    _assert_accounting(plan, catalog.evidence_records)


def test_sc_r_remediation_is_stable_across_repetitions_and_hash_seeds(tmp_path):
    document, _table = _sc_vertical_document()
    document.add_heading('4. Professional Experience And Engineering Training', level=1)
    for child, skill in ((1, 'Go'), (2, 'Rust')):
        title = document.add_paragraph(f'4.{child}. Original {skill} role')
        title.runs[0].bold = True
        document.add_paragraph(f'Original source support 4-{child}')
    document.add_heading('5. Technical Profile', level=1)
    document.add_paragraph('SQL')
    snapshot, catalog = _sc_docx_inputs(document, tmp_path)
    job = _stage_b_analysis(required=['Python', 'Go'], preferred=['SQL'])
    plan = _sc_plan(snapshot, catalog, job, max_experience_entries=1, max_skills=2)
    assert [skill.skill_text for skill in plan.prioritized_skills] == ['Python', 'SQL']
    payloads = (snapshot.model_dump_json(), catalog.model_dump_json(), job.model_dump_json())
    baseline = plan.model_dump_json(indent=2, warnings='error').encode()
    for _ in range(10):
        repeated = _sc_plan(snapshot, catalog, job, max_experience_entries=1, max_skills=2)
        assert repeated.model_dump_json(indent=2, warnings='error').encode() == baseline
    assert (snapshot.model_dump_json(), catalog.model_dump_json(), job.model_dump_json()) == payloads
    paths = [tmp_path / name for name in ('snapshot.json', 'catalog.json', 'job.json')]
    for path, payload in zip(paths, payloads):
        path.write_text(payload, encoding='utf-8')
    script = (
        'import hashlib, sys; from pathlib import Path; '
        'from resume_assistant.models import DocumentSnapshot, CandidateEvidenceCatalog; '
        'from resume_assistant.job_models import JobDescriptionAnalysis; '
        'from resume_assistant.matching import CandidateJobMatcher; '
        'from resume_assistant.section_planner import SectionPlanner, PlanBudgets, DEFAULT_BUDGETS; '
        'snapshot=DocumentSnapshot.model_validate_json(Path(sys.argv[1]).read_text()); '
        'catalog=CandidateEvidenceCatalog.model_validate_json(Path(sys.argv[2]).read_text()); '
        'job=JobDescriptionAnalysis.model_validate_json(Path(sys.argv[3]).read_text()); '
        'match=CandidateJobMatcher(catalog).assess(job); '
        'plan=SectionPlanner(PlanBudgets(**{**DEFAULT_BUDGETS,"max_experience_entries":1,"max_skills":2})).plan(job,match,catalog,snapshot); '
        'print(hashlib.sha256(plan.model_dump_json(indent=2,warnings="error").encode()).hexdigest())'
    )
    expected = hashlib.sha256(baseline).hexdigest()
    for seed in ('1', '17', '103'):
        result = subprocess.run(
            [sys.executable, '-B', '-c', script, *map(str, paths)],
            cwd=Path(__file__).resolve().parents[1],
            env={**os.environ, 'PYTHONHASHSEED': seed, 'PYTHONDONTWRITEBYTECODE': '1'},
            capture_output=True, text=True, check=True,
        )
        assert result.stdout.strip() == expected
