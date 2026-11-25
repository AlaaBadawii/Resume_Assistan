'''Deterministic section planner tests (Mission 3.3) using synthetic fixtures.'''

import hashlib
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest
from docx import Document
from pydantic import TypeAdapter

from resume_assistant.evidence import build_evidence_catalog
from resume_assistant.extractor import MasterResumeExtractor
from resume_assistant.job_models import JobDescriptionAnalysis
from resume_assistant.legacy_section_plan import LegacyResumeSectionPlan
from resume_assistant.matching import CandidateJobIntegrityError, CandidateJobMatcher
from resume_assistant.matching_models import (
    CandidateJobMatch,
    ComponentMatch,
    RequirementMatch,
)
from resume_assistant.models import (
    CandidateEvidenceCatalog,
    DocumentSection,
    DocumentSnapshot,
    DocumentStatistics,
    EvidenceRecord,
    EvidenceType,
    SourceLocation,
)
from resume_assistant.section_planner import (
    PLAN_SCHEMA_VERSION,
    ResumeSectionPlan,
    SectionPlanner,
    SectionPlannerError,
)
from tests.fixtures import builders
from tests.fixtures.section_plans import legacy_plan_payload
from tests.fixtures.jobs import (
    empty_job_payload,
    grounded_job_payload,
    synthetic_candidate_catalog,
)

STATISTICS = DocumentStatistics()


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def _analysis(payload=None) -> JobDescriptionAnalysis:
    document = dict(grounded_job_payload() if payload is None else payload)
    document['prompt_version'] = '2.0.0'
    return JobDescriptionAnalysis.model_validate(document)


def _catalog(records) -> CandidateEvidenceCatalog:
    section_refs = sorted({r.section_reference for r in records if r.section_reference})
    cat = synthetic_candidate_catalog(records)
    return cat.model_copy(update={'section_references': section_refs})


def _match(analysis: JobDescriptionAnalysis, catalog: CandidateEvidenceCatalog) -> CandidateJobMatch:
    return CandidateJobMatcher(catalog).assess(analysis)


def _plan(
    analysis, match, catalog, snapshot=None, manifest_available: bool = False,
):
    return SectionPlanner().plan(
        analysis, match, catalog, snapshot, manifest_available=manifest_available
    )


def _evidence_record(
    evidence_id: str,
    evidence_type: EvidenceType,
    content: str,
    *,
    section_reference: str | None = None,
    block_index: int | None = 0,
) -> EvidenceRecord:
    return EvidenceRecord(
        evidence_id=evidence_id,
        evidence_type=evidence_type,
        original_content=content,
        normalized_content=None,
        section_reference=section_reference,
        source_document='synthetic-master-resume.docx',
        source_sha256='a' * 64,
        source_location=SourceLocation(body_block_index=block_index)
        if block_index is not None
        else None,
        formatting=None,
        hyperlinks=[],
        table_context=None,
    )


def _snapshot_with_sections(section_identifiers) -> DocumentSnapshot:
    sections = [
        DocumentSection(
            identifier=identifier,
            heading_text=identifier.replace('-', ' ').title(),
            numbering=None,
            start_block_index=index,
            end_block_index=index,
            confidence=1.0,
        )
        for index, identifier in enumerate(section_identifiers)
    ]
    return DocumentSnapshot(
        source_filename='synthetic-master-resume.docx',
        source_sha256='a' * 64,
        extraction_timestamp=datetime(2026, 11, 22, tzinfo=timezone.utc),
        statistics=STATISTICS,
        blocks=[],
        sections=sections,
    )


def _snapshot_with_section_specs(section_specs) -> DocumentSnapshot:
    '''Build a snapshot with independently controlled section boundaries.'''
    sections = [
        DocumentSection(
            identifier=identifier,
            heading_text=heading_text,
            numbering=None,
            start_block_index=start_block_index,
            end_block_index=start_block_index,
            confidence=1.0,
        )
        for identifier, start_block_index, heading_text in section_specs
    ]
    return DocumentSnapshot(
        source_filename='synthetic-master-resume.docx',
        source_sha256='a' * 64,
        extraction_timestamp=datetime(2026, 11, 22, tzinfo=timezone.utc),
        statistics=STATISTICS,
        blocks=[],
        sections=sections,
    )


# ---------------------------------------------------------------- functional


def test_complete_match_creates_planned_sections():
    catalog = _catalog([
        _evidence_record('ev-py', EvidenceType.TECHNICAL_SKILL, 'Python', section_reference='technical-profile', block_index=1),
        _evidence_record('ev-sql', EvidenceType.TECHNICAL_SKILL, 'SQL', section_reference='technical-profile', block_index=2),
    ])
    analysis = _analysis()
    match = _match(analysis, catalog)
    plan = _plan(analysis, match, catalog)

    assert plan.schema_version == PLAN_SCHEMA_VERSION
    assert plan.human_review_required is True
    assert len(plan.selected_evidence_ids) >= 1
    assert any(section.section_id == 'technical-skills' for section in plan.ordered_sections)
    assert plan.professional_summary_blueprint is not None


def test_partial_match_keeps_partially_supported_evidence():
    payload = dict(grounded_job_payload())
    payload['required_skills'] = [{
        'name': 'Python and SQL',
        'quotation': 'Required skills: Python and SQL.',
        'classification_context': 'Required skills: Python and SQL.',
    }]
    payload['preferred_skills'] = []
    analysis = _analysis(payload)
    catalog = _catalog([
        _evidence_record('ev-py', EvidenceType.TECHNICAL_SKILL, 'Python', section_reference='technical-profile', block_index=1),
    ])
    match = _match(analysis, catalog)
    plan = _plan(analysis, match, catalog)

    python_coverage = [
        c for c in plan.requirement_coverage
        if c.category == 'required_skills' and c.matched_evidence_ids == ['ev-py']
    ]
    assert python_coverage, 'requirement coverage should be present'
    item = python_coverage[0]
    assert item.status == 'partially_supported'
    assert item.matched_evidence_ids == ['ev-py']
    assert item.unmatched_components == ['SQL']


def test_high_gap_job_produces_no_evidence_found_without_fabricating_skills():
    payload = dict(grounded_job_payload())
    payload['required_skills'] = [{
        'name': 'Kubernetes',
        'quotation': 'Required skills: Kubernetes.',
        'classification_context': 'Required skills: Kubernetes.',
    }]
    payload['preferred_skills'] = []
    analysis = _analysis(payload)
    catalog = _catalog([
        _evidence_record('ev-py', EvidenceType.TECHNICAL_SKILL, 'Python', section_reference='technical-profile', block_index=1),
    ])
    match = _match(analysis, catalog)
    plan = _plan(analysis, match, catalog)

    k8s_coverage = [c for c in plan.requirement_coverage if c.category == 'required_skills']
    assert k8s_coverage
    assert k8s_coverage[0].status == 'no_evidence_found'
    assert k8s_coverage[0].matched_evidence_ids == []
    # No skill should be added for the unsupported requirement.
    assert 'Kubernetes' not in [skill.skill_text for skill in plan.prioritized_skills]


def test_missing_sections_are_dropped_empty():
    catalog = _catalog([
        _evidence_record('ev-py', EvidenceType.TECHNICAL_SKILL, 'Python', section_reference='technical-profile', block_index=1),
    ])
    analysis = _analysis()
    match = _match(analysis, catalog)
    plan = _plan(analysis, match, catalog)

    section_ids = {section.section_id for section in plan.ordered_sections}
    assert section_ids == {'technical-skills'}
    assert 'education' not in section_ids
    assert 'certifications' not in section_ids
    assert all(section.item_count > 0 and section.dropped_empty is False for section in plan.ordered_sections)


def test_skill_prioritization_required_before_preferred_before_other():
    payload = dict(grounded_job_payload())
    payload['required_skills'] = [{
        'name': 'Python',
        'quotation': 'Required skills: Python.',
        'classification_context': 'Required skills: Python.',
    }]
    payload['preferred_skills'] = [{
        'name': 'SQL',
        'quotation': 'Preferred skills: SQL.',
        'classification_context': 'Preferred skills: SQL.',
    }]
    analysis = _analysis(payload)
    catalog = _catalog([
        _evidence_record('ev-py', EvidenceType.TECHNICAL_SKILL, 'Python', section_reference='technical-profile', block_index=1),
        _evidence_record('ev-sql', EvidenceType.TECHNICAL_SKILL, 'SQL', section_reference='technical-profile', block_index=2),
        _evidence_record('ev-go', EvidenceType.TECHNICAL_SKILL, 'Go', section_reference='technical-profile', block_index=3),
    ])
    match = _match(analysis, catalog)
    plan = _plan(analysis, match, catalog)

    tiers = [skill.priority_tier for skill in plan.prioritized_skills]
    required_index = tiers.index('required')
    preferred_index = tiers.index('preferred')
    other_index = tiers.index('other')
    assert required_index < preferred_index < other_index


def test_experience_and_project_selection_preserve_source_order():
    catalog = _catalog([
        _evidence_record('ev-exp-a', EvidenceType.EXPERIENCE, 'Role A at Co A', section_reference='professional-experience-and-engineering-training', block_index=5),
        _evidence_record('ev-exp-b', EvidenceType.EXPERIENCE, 'Role B at Co B', section_reference='professional-experience-and-engineering-training', block_index=7),
        _evidence_record('ev-proj-a', EvidenceType.PROJECT, 'Project Alpha', section_reference='independent-software-and-ai-projects', block_index=9),
        _evidence_record('ev-proj-b', EvidenceType.PROJECT, 'Project Beta', section_reference='independent-software-and-ai-projects', block_index=11),
    ])
    analysis = _analysis()
    match = _match(analysis, catalog)
    plan = _plan(analysis, match, catalog)

    experience = next(s for s in plan.ordered_sections if s.section_id == 'professional-experience')
    assert experience.selected_evidence_ids == ['ev-exp-a', 'ev-exp-b']
    projects = next(s for s in plan.ordered_sections if s.section_id == 'projects')
    assert projects.selected_evidence_ids == ['ev-proj-a', 'ev-proj-b']


def test_summary_blueprint_references_evidence():
    catalog = _catalog([
        _evidence_record('ev-py', EvidenceType.TECHNICAL_SKILL, 'Python', section_reference='technical-profile', block_index=1),
        _evidence_record('ev-proj', EvidenceType.PROJECT, 'Project Alpha', section_reference='projects', block_index=2),
    ])
    analysis = _analysis()
    match = _match(analysis, catalog)
    plan = _plan(analysis, match, catalog)

    blueprint = plan.professional_summary_blueprint
    # Every project evidence id must exist in the catalog.
    catalog_ids = {record.evidence_id for record in catalog.evidence_records}
    for evidence_id in blueprint.relevant_project_evidence_ids:
        assert evidence_id in catalog_ids
    # Technical capabilities derive from prioritized skills.
    assert blueprint.technical_capability_skill_texts


def test_content_budgets_limit_items():
    catalog = _catalog([
        _evidence_record(f'ev-skill-{i}', EvidenceType.TECHNICAL_SKILL, f'Skill {i}', section_reference='technical-profile', block_index=i)
        for i in range(30)
    ])
    analysis = _analysis()
    match = _match(analysis, catalog)
    planner = SectionPlanner()
    plan = planner.plan(analysis, match, catalog)

    assert len(plan.prioritized_skills) <= planner.budgets.max_skills
    technical = next(s for s in plan.ordered_sections if s.section_id == 'technical-skills')
    assert technical.item_count <= planner.budgets.max_skills


# ---------------------------------------------------------------- integrity


def _stage_b_analysis(required=(), preferred=()):
    payload = empty_job_payload()
    for category, names in (('required_skills', required), ('preferred_skills', preferred)):
        payload[category] = [{
            'name': name,
            'quotation': f'{category}: {name}.',
            'classification_context': f'{category}: {name}.',
        } for name in names]
    return _analysis(payload)


@pytest.mark.parametrize('contents,expected,status,unmatched', [
    (['Python'], ['Python'], 'partially_supported', ['SQL']),
    (['Python', 'SQL'], ['Python', 'SQL'], 'supported', []),
    (['Rust'], [], 'no_evidence_found', ['Python', 'SQL']),
])
def test_stage_b_only_supported_requirement_components_become_skills(contents, expected, status, unmatched):
    catalog = _catalog([
        _evidence_record(f'ev-{index}', EvidenceType.TECHNICAL_SKILL, content, section_reference='technical-profile', block_index=index)
        for index, content in enumerate(contents)
    ])
    analysis = _stage_b_analysis(required=['Python and SQL'])
    match = _match(analysis, catalog)
    plan = _plan(analysis, match, catalog)
    required = [skill for skill in plan.prioritized_skills if skill.priority_tier == 'required']
    assert [skill.skill_text for skill in required] == expected
    assert 'Python and SQL' not in plan.professional_summary_blueprint.technical_capability_skill_texts
    assert plan.requirement_coverage[0].requirement_id == match.requirements[0].requirement_id
    assert plan.requirement_coverage[0].status == status
    assert plan.requirement_coverage[0].unmatched_components == unmatched
    for skill in required:
        component = next(component for component in match.requirements[0].components if component.text == skill.skill_text)
        assert skill.matched_evidence_ids == component.matched_evidence_ids
        assert skill.requirement_id == match.requirements[0].requirement_id
        assert len(skill.requirement_associations) == 1
        association = skill.requirement_associations[0]
        assert association.requirement_id == skill.requirement_id
        assert association.priority_tier == 'required'
        assert association.matched_evidence_ids == component.matched_evidence_ids


def test_stage_b_preferred_partial_components_keep_their_parent_and_priority():
    catalog = _catalog([
        _evidence_record('ev-go', EvidenceType.TECHNICAL_SKILL, 'Go', section_reference='technical-profile'),
    ])
    analysis = _stage_b_analysis(preferred=['Go and Rust'])
    match = _match(analysis, catalog)
    plan = _plan(analysis, match, catalog)
    assert [skill.skill_text for skill in plan.prioritized_skills] == ['Go']
    skill = plan.prioritized_skills[0]
    assert skill.priority_tier == 'preferred'
    assert skill.requirement_id == match.requirements[0].requirement_id
    assert skill.requirement_associations[0].priority_tier == 'preferred'
    assert plan.requirement_coverage[0].status == 'partially_supported'
    assert plan.professional_summary_blueprint.technical_capability_skill_texts == ['Go']


def test_stage_b_deduplication_keeps_all_parents_and_duplicate_evidence():
    payload = empty_job_payload()
    payload['required_skills'] = [{
        'name': name, 'quotation': quotation, 'classification_context': quotation,
    } for name, quotation in [
        ('Python', 'Required: Python for services.'),
        ('PYTHON', 'Required: PYTHON for data.'),
    ]]
    analysis = _analysis(payload)
    catalog = _catalog([
        _evidence_record('ev-a', EvidenceType.TECHNICAL_SKILL, 'Python', section_reference='technical-profile', block_index=1),
        _evidence_record('ev-b', EvidenceType.TECHNICAL_SKILL, ' python ', section_reference='technical-profile', block_index=2),
    ])
    match = _match(analysis, catalog)
    before = (catalog.model_dump_json(), analysis.model_dump_json(), match.model_dump_json())
    plan = _plan(analysis, match, catalog)
    assert len(plan.prioritized_skills) == 1
    skill = plan.prioritized_skills[0]
    assert skill.skill_text == 'Python'
    assert skill.priority_tier == 'required'
    assert skill.matched_evidence_ids == ['ev-a', 'ev-b']
    parents = [requirement.requirement_id for requirement in match.requirements]
    assert skill.requirement_id == parents[0]
    assert [item.requirement_id for item in skill.requirement_associations] == parents
    assert all(item.matched_evidence_ids == ['ev-a'] for item in skill.requirement_associations)
    assert [item.requirement_id for item in plan.requirement_coverage] == parents
    assert plan.professional_summary_blueprint.technical_capability_skill_texts == ['Python']
    assert (catalog.model_dump_json(), analysis.model_dump_json(), match.model_dump_json()) == before


def test_stage_b_deduplication_keeps_required_and_preferred_associations():
    analysis = _stage_b_analysis(required=['Python and SQL'], preferred=['Python'])
    catalog = _catalog([
        _evidence_record('ev-py', EvidenceType.TECHNICAL_SKILL, 'Python', section_reference='technical-profile', block_index=1),
        _evidence_record('ev-sql', EvidenceType.TECHNICAL_SKILL, 'SQL', section_reference='technical-profile', block_index=2),
    ])
    match = _match(analysis, catalog)
    plan = _plan(analysis, match, catalog)
    assert [skill.skill_text for skill in plan.prioritized_skills] == ['Python', 'SQL']
    skill = plan.prioritized_skills[0]
    assert skill.priority_tier == 'required'
    assert skill.requirement_id == match.requirements[0].requirement_id
    assert [(item.requirement_id, item.priority_tier) for item in skill.requirement_associations] == [
        (match.requirements[0].requirement_id, 'required'),
        (match.requirements[1].requirement_id, 'preferred'),
    ]
    assert all(item.matched_evidence_ids == ['ev-py'] for item in skill.requirement_associations)


def test_stage_b_associations_do_not_confuse_skills_sharing_one_evidence_record():
    payload = empty_job_payload()
    payload['required_skills'] = [{
        'name': name, 'quotation': quotation, 'classification_context': quotation,
    } for name, quotation in [
        ('Python', 'Required: Python for services.'),
        ('Python', 'Required: Python for data.'),
        ('SQL', 'Required: SQL for storage.'),
        ('Go', 'Required: Go for workers.'),
    ]]
    analysis = _analysis(payload)
    catalog = _catalog([
        _evidence_record('ev-shared', EvidenceType.TECHNICAL_SKILL, 'Python SQL Go', section_reference='technical-profile'),
    ])
    match = _match(analysis, catalog)
    plan = _plan(analysis, match, catalog)
    skill = next(skill for skill in plan.prioritized_skills if skill.skill_text == 'Python')
    assert [item.requirement_id for item in skill.requirement_associations] == [
        match.requirements[0].requirement_id, match.requirements[1].requirement_id,
    ]
    assert len(plan.requirement_coverage) == 4


def test_stage_b_conservative_deduplication_preserves_distinct_technologies():
    names = ['Java', 'JavaScript', 'C', 'C++', 'C#', 'SQL', 'MySQL', '.NET', 'NET', 'Go', 'Google', 'Node.js', 'Nodejs']
    analysis = _stage_b_analysis(required=names)
    catalog = _catalog([
        _evidence_record(f'ev-{index}', EvidenceType.TECHNICAL_SKILL, name, section_reference='technical-profile', block_index=index)
        for index, name in enumerate(names)
    ])
    plan = _plan(analysis, _match(analysis, catalog), catalog)
    assert [skill.skill_text for skill in plan.prioritized_skills] == names
    assert all(len(skill.requirement_associations) == 1 for skill in plan.prioritized_skills)


def test_stage_b_association_extension_loads_legacy_plan_shape():
    analysis = _stage_b_analysis(required=['Python'])
    catalog = _catalog([
        _evidence_record('ev-py', EvidenceType.TECHNICAL_SKILL, 'Python', section_reference='technical-profile'),
    ])
    plan = _plan(analysis, _match(analysis, catalog), catalog)
    restored = ResumeSectionPlan.model_validate_json(plan.model_dump_json())
    assert restored == plan
    legacy = legacy_plan_payload(plan)
    for skill in legacy['prioritized_skills']:
        skill.pop('requirement_associations')
    restored_legacy = LegacyResumeSectionPlan.model_validate_json(json.dumps(legacy))
    assert restored_legacy.schema_version == '1.0.0'
    assert restored_legacy.prioritized_skills[0].requirement_id == plan.prioritized_skills[0].requirement_id
    assert restored_legacy.prioritized_skills[0].requirement_associations == []
    assert SectionPlanner().replan_legacy(restored_legacy, analysis, _match(analysis, catalog), catalog) == plan


@pytest.mark.parametrize('evidence_type', [kind for kind in EvidenceType if kind != EvidenceType.TECHNICAL_SKILL])
def test_stage_b_nontechnical_types_cannot_populate_technical_sections(evidence_type):
    catalog = _catalog([
        _evidence_record('ev-wrong-type', evidence_type, 'Python', section_reference='technical-profile'),
    ])
    analysis = _stage_b_analysis(required=['Python'])
    match = _match(analysis, catalog)
    plan = _plan(analysis, match, catalog)
    assert plan.selected_evidence_ids == []
    assert plan.prioritized_skills == []
    assert plan.professional_summary_blueprint.technical_capability_skill_texts == []
    assert plan.requirement_coverage[0].status == match.requirements[0].status


@pytest.mark.parametrize('kind,section,destination,content', [
    (EvidenceType.PROFESSIONAL_SUMMARY, 'professional-identity', 'professional-summary', 'Software engineer'),
    (EvidenceType.TECHNICAL_SKILL, 'technical-profile', 'technical-skills', 'Go'),
    (EvidenceType.EXPERIENCE, 'professional-experience-and-engineering-training', 'professional-experience', 'Engineer at Synthetic Co'),
    (EvidenceType.PROJECT, 'independent-software-and-ai-projects', 'projects', 'Synthetic project'),
    (EvidenceType.EDUCATION, 'education', 'education', 'Computer Science degree'),
    (EvidenceType.CERTIFICATION, 'certifications-and-professional-development', 'certifications', 'Synthetic certification'),
])
def test_stage_b_valid_content_stays_in_its_classified_destination(kind, section, destination, content):
    record = _evidence_record('ev-content', kind, content, section_reference=section, block_index=1)
    if kind != EvidenceType.TECHNICAL_SKILL:
        record.formatting = {'style': 'Heading 2'}
    catalog = _catalog([record])
    analysis = _stage_b_analysis()
    plan = _plan(analysis, _match(analysis, catalog), catalog)
    assert plan.selected_evidence_ids == ['ev-content']
    selected = next(item for item in plan.ordered_sections if item.selected_evidence_ids)
    assert selected.section_id == destination


@pytest.mark.parametrize('with_snapshot', [False, True], ids=['legacy', 'snapshot'])
def test_stage_b_section_headings_are_not_skills_even_when_the_matcher_supports_them(with_snapshot):
    catalog = _catalog([
        _evidence_record('ev-heading', EvidenceType.TECHNICAL_SKILL, '2. Technical Profile', section_reference='technical-profile'),
    ])
    analysis = _stage_b_analysis(required=['Technical Profile'])
    match = _match(analysis, catalog)
    assert match.requirements[0].status == 'supported'
    snapshot = _snapshot_with_sections(['technical-profile']) if with_snapshot else None
    plan = _plan(analysis, match, catalog, snapshot)
    assert plan.selected_evidence_ids == []
    assert plan.prioritized_skills == []
    assert plan.professional_summary_blueprint.technical_capability_skill_texts == []
    assert plan.requirement_coverage[0].status == 'supported'


def test_stage_b_recorded_technical_subheadings_are_structural():
    record = _evidence_record('ev-heading', EvidenceType.TECHNICAL_SKILL, 'Databases', section_reference='technical-profile')
    record.formatting = {'style': 'Heading 2'}
    catalog = _catalog([record])
    analysis = _stage_b_analysis(required=['Databases'])
    plan = _plan(analysis, _match(analysis, catalog), catalog)
    assert plan.selected_evidence_ids == []
    assert plan.prioritized_skills == []


def test_stage_b_boundaryless_snapshot_excludes_canonical_technical_heading():
    catalog = _catalog([
        _evidence_record('ev-heading', EvidenceType.TECHNICAL_SKILL, '2. Technical Profile', section_reference='technical-profile'),
    ])
    analysis = _stage_b_analysis(required=['Technical Profile'])
    match = _match(analysis, catalog)
    assert match.requirements[0].status == 'supported'
    snapshot = _snapshot_with_section_specs([('technical-profile', None, 'Technical Profile')])
    plan = _plan(analysis, match, catalog, snapshot)
    assert plan.selected_evidence_ids == []
    assert plan.prioritized_skills == []
    assert plan.professional_summary_blueprint.technical_capability_skill_texts == []
    assert plan.requirement_coverage[0].status == 'supported'


def test_stage_b_partial_snapshot_boundaries_preserve_factual_titles():
    catalog = _catalog([
        _evidence_record('ev-heading', EvidenceType.TECHNICAL_SKILL, '2. Technical Profile', section_reference='technical-profile', block_index=0),
        _evidence_record('ev-exp', EvidenceType.EXPERIENCE, 'Senior Engineer at Synthetic Co', section_reference='professional-experience-and-engineering-training', block_index=1),
        _evidence_record('ev-proj', EvidenceType.PROJECT, 'Synthetic Project', section_reference='independent-software-and-ai-projects', block_index=2),
    ])
    analysis = _stage_b_analysis()
    snapshot = _snapshot_with_section_specs([
        ('technical-profile', None, 'Technical Profile'),
        ('professional-experience-and-engineering-training', 1, 'Professional Experience And Engineering Training'),
        ('independent-software-and-ai-projects', 2, 'Independent Software And Ai Projects'),
    ])
    plan = _plan(analysis, _match(analysis, catalog), catalog, snapshot)
    assert 'ev-heading' not in plan.selected_evidence_ids
    experience = next(section for section in plan.ordered_sections if section.section_id == 'professional-experience')
    projects = next(section for section in plan.ordered_sections if section.section_id == 'projects')
    assert experience.selected_evidence_ids == ['ev-exp']
    assert projects.selected_evidence_ids == ['ev-proj']
    assert plan.professional_summary_blueprint.technical_capability_skill_texts == []


@pytest.mark.parametrize('content', [
    'Email address: synthetic@example.invalid',
    'Phone number: 000-000-0000',
    'LinkedIn profile: synthetic',
    'Mobile phone: 000-000-0000',
    'Email',
    'LinkedIn',
    'Portfolio',
    'Location',
    'linkedin.com/in/synthetic',
    'github.com/synthetic',
])
def test_stage_b_contact_metadata_variants_are_not_technical_content(content):
    catalog = _catalog([
        _evidence_record('ev-contact-variant', EvidenceType.TECHNICAL_SKILL, content, section_reference='technical-profile'),
    ])
    analysis = _stage_b_analysis()
    plan = _plan(analysis, _match(analysis, catalog), catalog)
    assert plan.selected_evidence_ids == []
    assert plan.prioritized_skills == []
    assert plan.professional_summary_blueprint.technical_capability_skill_texts == []


def test_stage_b_github_capabilities_remain_eligible():
    catalog = _catalog([
        _evidence_record('ev-github', EvidenceType.TECHNICAL_SKILL, 'GitHub', section_reference='technical-profile', block_index=1),
        _evidence_record('ev-actions', EvidenceType.TECHNICAL_SKILL, 'GitHub Actions', section_reference='technical-profile', block_index=2),
    ])
    analysis = _stage_b_analysis(required=['GitHub Actions'])
    plan = _plan(analysis, _match(analysis, catalog), catalog)
    assert plan.selected_evidence_ids == ['ev-github', 'ev-actions']
    assert [skill.skill_text for skill in plan.prioritized_skills] == ['GitHub Actions', 'GitHub']
    assert plan.professional_summary_blueprint.technical_capability_skill_texts == ['GitHub Actions', 'GitHub']


@pytest.mark.parametrize('content', ['Email: synthetic@example.invalid', 'GitHub: https://example.invalid/profile'])
def test_stage_b_contact_label_metadata_is_not_technical_content(content):
    catalog = _catalog([
        _evidence_record('ev-contact-label', EvidenceType.TECHNICAL_SKILL, content, section_reference='technical-profile'),
    ])
    analysis = _stage_b_analysis()
    plan = _plan(analysis, _match(analysis, catalog), catalog)
    assert plan.selected_evidence_ids == []
    assert plan.prioritized_skills == []


@pytest.mark.parametrize('content', ['Built Python systems with SQL', 'Python, SQL', 'Python and SQL', 'Machine Learning'])
def test_stage_b_coarse_technical_records_are_not_assumed_to_be_atomic_skills(content):
    catalog = _catalog([
        _evidence_record('ev-technical-content', EvidenceType.TECHNICAL_SKILL, content, section_reference='technical-profile'),
    ])
    analysis = _stage_b_analysis()
    plan = _plan(analysis, _match(analysis, catalog), catalog)
    assert plan.selected_evidence_ids == ['ev-technical-content']
    assert plan.prioritized_skills == []
    assert plan.professional_summary_blueprint.technical_capability_skill_texts == []


def test_stage_b_supported_multiword_skills_can_reference_experience_without_moving_it():
    catalog = _catalog([
        _evidence_record('ev-experience', EvidenceType.EXPERIENCE, 'Built systems using Machine Learning', section_reference='professional-experience-and-engineering-training'),
    ])
    analysis = _stage_b_analysis(required=['Machine Learning'])
    match = _match(analysis, catalog)
    plan = _plan(analysis, match, catalog)
    assert plan.prioritized_skills[0].skill_text == 'Machine Learning'
    assert plan.prioritized_skills[0].matched_evidence_ids == ['ev-experience']
    assert all(section.section_id != 'technical-skills' for section in plan.ordered_sections)
    experience = next(section for section in plan.ordered_sections if section.section_id == 'professional-experience')
    assert experience.selected_evidence_ids == ['ev-experience']


def test_stage_b_plain_technology_names_are_not_contact_metadata():
    catalog = _catalog([
        _evidence_record('ev-github', EvidenceType.TECHNICAL_SKILL, 'GitHub', section_reference='technical-profile'),
    ])
    analysis = _stage_b_analysis()
    plan = _plan(analysis, _match(analysis, catalog), catalog)
    assert [skill.skill_text for skill in plan.prioritized_skills] == ['GitHub']


def test_stage_b_technology_prose_is_not_mistaken_for_a_contact_label():
    catalog = _catalog([
        _evidence_record('ev-ci', EvidenceType.TECHNICAL_SKILL, 'GitHub Actions: built CI pipelines', section_reference='technical-profile'),
    ])
    analysis = _stage_b_analysis(required=['GitHub Actions'])
    plan = _plan(analysis, _match(analysis, catalog), catalog)
    assert plan.selected_evidence_ids == ['ev-ci']
    assert [skill.skill_text for skill in plan.prioritized_skills] == ['GitHub Actions']


@pytest.mark.parametrize('section,kind,reason', [
    (None, EvidenceType.UNCLASSIFIED, 'unclassified_section_no_output_mapping'),
    ('languages', EvidenceType.LANGUAGE, 'source_section_not_mapped_to_output_section'),
])
def test_stage_b_preserves_existing_exclusions_for_matched_unmapped_evidence(section, kind, reason):
    catalog = _catalog([
        _evidence_record('ev-unmapped', kind, 'Python', section_reference=section),
    ])
    analysis = _stage_b_analysis(required=['Python'])
    match = _match(analysis, catalog)
    plan = _plan(analysis, match, catalog)
    assert plan.selected_evidence_ids == []
    assert [(item.evidence_id, item.reason) for item in plan.excluded_evidence] == [('ev-unmapped', reason)]
    assert plan.requirement_coverage[0].status == match.requirements[0].status


def _stage_b_determinism_plan():
    catalog = _catalog([
        _evidence_record('ev-later', EvidenceType.TECHNICAL_SKILL, 'PYTHON', section_reference='technical-profile', block_index=2),
        _evidence_record('ev-earlier', EvidenceType.TECHNICAL_SKILL, 'Python', section_reference='technical-profile', block_index=1),
        _evidence_record('ev-sql', EvidenceType.TECHNICAL_SKILL, 'SQL', section_reference='technical-profile', block_index=3),
    ])
    analysis = _stage_b_analysis(required=['Python and SQL'], preferred=['Python'])
    return _plan(analysis, _match(analysis, catalog), catalog)


def test_stage_b_aggregation_serialization_is_stable_across_runs_and_hash_seeds():
    baseline = _stage_b_determinism_plan().model_dump_json(indent=2, warnings='error').encode('utf-8')
    for _ in range(10):
        assert _stage_b_determinism_plan().model_dump_json(indent=2, warnings='error').encode('utf-8') == baseline
    expected = hashlib.sha256(baseline).hexdigest()
    script = (
        'import hashlib; from tests.test_section_planner import _stage_b_determinism_plan; '
        'print(hashlib.sha256(_stage_b_determinism_plan().model_dump_json(indent=2, warnings="error").encode("utf-8")).hexdigest())'
    )
    for seed in ('1', '17', '103'):
        result = subprocess.run(
            [sys.executable, '-B', '-c', script],
            cwd=Path(__file__).resolve().parents[1],
            env={**os.environ, 'PYTHONHASHSEED': seed, 'PYTHONDONTWRITEBYTECODE': '1'},
            capture_output=True, text=True, check=True,
        )
        assert result.stdout.strip() == expected


def _cross_artifact_inputs(tamper):
    '''Individually valid artifacts that disagree at the matcher boundary.'''
    def job(skill):
        payload = empty_job_payload()
        payload['required_skills'] = [{
            'name': skill,
            'quotation': f'Required skills: {skill}.',
            'classification_context': f'Required skills: {skill}.',
        }]
        return _analysis(payload)

    catalog = _catalog([
        _evidence_record('ev-py', EvidenceType.TECHNICAL_SKILL, 'Python', section_reference='technical-profile', block_index=1),
        _evidence_record('ev-k8s', EvidenceType.TECHNICAL_SKILL, 'Kubernetes', section_reference='technical-profile', block_index=2),
    ])
    analysis = job('Python')
    data = _match(analysis, catalog).model_dump(mode='json')
    if tamper == 'foreign_job':
        data = _match(job('Kubernetes'), catalog).model_dump(mode='json')
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
        reference['source_document'] = 'another-synthetic-source.docx'
        reference['source_location']['body_block_index'] = 999
    elif tamper == 'missing_requirements':
        data['requirements'] = []
    elif tamper == 'forged_skill_support':
        analysis = job('Kubernetes')
        catalog = _catalog([catalog.evidence_records[0]])
        forged = catalog.evidence_records[0].model_copy(update={'original_content': 'Kubernetes'})
        data = _match(analysis, _catalog([forged])).model_dump(mode='json')
    else:
        raise AssertionError('Unknown synthetic mismatch case.')
    return analysis, CandidateJobMatch.model_validate_json(json.dumps(data)), catalog


@pytest.mark.parametrize('tamper', [
    'foreign_job', 'wrong_candidate_hash', 'wrong_record_count', 'fake_requirement_id',
    'wrong_source_index', 'wrong_provenance', 'missing_requirements', 'forged_skill_support',
])
def test_cross_artifact_mismatch_is_rejected(tamper):
    analysis, match, catalog = _cross_artifact_inputs(tamper)
    with pytest.raises(CandidateJobIntegrityError):
        CandidateJobMatcher(catalog).validate_result(match, analysis)
    with pytest.raises(SectionPlannerError, match='not mutually consistent'):
        _plan(analysis, match, catalog)


def test_planner_consumes_validated_match_without_calling_assess(monkeypatch):
    catalog = _catalog([
        _evidence_record('ev-py', EvidenceType.TECHNICAL_SKILL, 'Python', section_reference='technical-profile', block_index=1),
    ])
    analysis = _analysis()
    match = _match(analysis, catalog)
    real_validate = CandidateJobMatcher.validate_result
    validated = []

    def validate(matcher, result, job):
        restored = real_validate(matcher, result, job)
        validated.append(restored)
        # Invalidate only the supplied instance after validation. The planner
        # must use the returned instance, including its intact requirements.
        result.requirements.clear()
        return restored

    def forbidden_assess(*args, **kwargs):
        raise AssertionError('Section planning must not call assess().')

    monkeypatch.setattr(CandidateJobMatcher, 'validate_result', validate)
    monkeypatch.setattr(CandidateJobMatcher, 'assess', forbidden_assess)
    plan = _plan(analysis, match, catalog)
    assert len(validated) == 1
    assert not match.requirements
    assert [item.requirement_id for item in plan.requirement_coverage] == [
        item.requirement_id for item in validated[0].requirements
    ]
    assert plan.requirement_coverage[0].matched_evidence_ids == ['ev-py']


def test_planner_rejects_unknown_evidence_id():
    analysis = _analysis()
    catalog = _catalog([
        _evidence_record('ev-py', EvidenceType.TECHNICAL_SKILL, 'Python', section_reference='technical-profile', block_index=1),
    ])
    # Build a match whose requirement references an evidence id absent from the
    # catalog, bypassing the match model's own provenance validation.
    requirement = RequirementMatch.model_construct(
        requirement_id='b' * 64,
        category='required_skills',
        source_index=0,
        requirement_text='Python',
        source_quotation='Required skills: Python.',
        classification='required',
        classification_status='ai_generated_unverified',
        classification_context='Required skills: Python.',
        language_level=None,
        status='supported',
        matched_evidence_ids=['unknown-id'],
        matched_evidence=[],
        components=[ComponentMatch.model_construct(
            text='Python', kind='phrase', status='supported',
            matched_evidence_ids=['unknown-id'],
            matching_method='exact_normalized_phrase_v2',
        )],
        unmatched_components=[],
        matching_method='exact_normalized_phrase_v2',
    )
    match = CandidateJobMatch.model_construct(
        schema_version='2.0.0',
        source_job_schema_version='2.0.0',
        source_job_prompt_version='2.0.0',
        source_job_grounding_method='exact_substrings_only',
        source_job_interpretation_status='ai_generated_unverified',
        candidate_evidence_schema_version='3.0.0',
        candidate_source_sha256='a' * 64,
        candidate_evidence_record_count=1,
        coverage_status='assessed',
        coverage_warnings=[],
        requirements=[requirement],
        limitations=['synthetic'],
    )
    with pytest.raises(SectionPlannerError, match='unknown candidate evidence identifier'):
        _plan(analysis, match, catalog)


def test_planner_rejects_invalid_section_reference():
    # Declare a different section reference in the catalog than the record uses,
    # so the record's reference is unknown to the planner.
    catalog = synthetic_candidate_catalog([
        _evidence_record('ev-py', EvidenceType.TECHNICAL_SKILL, 'Python', section_reference='nonexistent-section', block_index=1),
    ]).model_copy(update={'section_references': ['technical-profile']})
    analysis = _analysis()
    match = _match(analysis, catalog)
    with pytest.raises(SectionPlannerError, match='unknown source section'):
        _plan(analysis, match, catalog)


def test_planner_rejects_wrong_catalog_schema():
    catalog = _catalog([
        _evidence_record('ev-py', EvidenceType.TECHNICAL_SKILL, 'Python', section_reference='technical-profile', block_index=1),
    ]).model_copy(update={'schema_version': '2.0.0'})
    analysis = _analysis()
    match = _match(analysis, catalog.model_copy(update={'schema_version': '3.0.0'}))
    with pytest.raises(SectionPlannerError, match='schema 3.0.0'):
        _plan(analysis, match, catalog)


def test_planner_rejects_wrong_match_schema():
    catalog = _catalog([
        _evidence_record('ev-py', EvidenceType.TECHNICAL_SKILL, 'Python', section_reference='technical-profile', block_index=1),
    ])
    analysis = _analysis()
    match = _match(analysis, catalog).model_copy(update={'schema_version': '1.0.0'})
    with pytest.raises(SectionPlannerError, match='schema 2.0.0'):
        _plan(analysis, match, catalog)


def test_planner_rejects_wrong_analysis_schema():
    catalog = _catalog([
        _evidence_record('ev-py', EvidenceType.TECHNICAL_SKILL, 'Python', section_reference='technical-profile', block_index=1),
    ])
    analysis = _analysis().model_copy(update={'schema_version': '1.0.0'})
    match = _match(analysis.model_copy(update={'schema_version': '2.0.0'}), catalog)
    with pytest.raises(SectionPlannerError, match='schema 2.0.0'):
        _plan(analysis, match, catalog)


def test_planner_handles_legacy_catalog_without_snapshot():
    catalog = _catalog([
        _evidence_record('ev-py', EvidenceType.TECHNICAL_SKILL, 'Python', section_reference='technical-profile', block_index=1),
    ])
    analysis = _analysis()
    match = _match(analysis, catalog)
    plan = _plan(analysis, match, catalog, snapshot=None)
    assert plan.planning_metadata.snapshot_available is False
    assert plan.planning_metadata.manifest_available is False
    assert plan.schema_version == PLAN_SCHEMA_VERSION


@pytest.mark.parametrize('tamper', [
    'missing_location', 'missing_hash', 'wrong_hash', 'wrong_filename', 'missing_body_index',
])
@pytest.mark.parametrize('with_snapshot', [False, True], ids=['legacy', 'snapshot'])
def test_planner_requires_sufficient_record_provenance(tamper, with_snapshot):
    item = _evidence_record('ev-py', EvidenceType.TECHNICAL_SKILL, 'Python', section_reference='technical-profile')
    if tamper == 'missing_location':
        item.source_location = None
    elif tamper == 'missing_hash':
        item.source_sha256 = None
    elif tamper == 'wrong_hash':
        item.source_sha256 = 'b' * 64
    elif tamper == 'wrong_filename':
        item.source_document = 'another-synthetic-source.docx'
    else:
        item.source_location = SourceLocation()
    catalog = _catalog([item])
    analysis = _analysis()
    match = _match(analysis, catalog)
    snapshot = _snapshot_with_sections(['technical-profile']) if with_snapshot else None
    with pytest.raises(SectionPlannerError, match='provenance is insufficient'):
        _plan(analysis, match, catalog, snapshot)


@pytest.mark.parametrize('tamper', ['missing_source', 'blank_filename', 'blank_hash', 'invalid_hash'])
def test_catalog_source_information_is_required(tamper):
    catalog = _catalog([
        _evidence_record('ev-py', EvidenceType.TECHNICAL_SKILL, 'Python', section_reference='technical-profile'),
    ])
    analysis = _analysis()
    match = _match(analysis, catalog)
    if tamper == 'missing_source':
        catalog.source_document = None
    elif tamper == 'blank_filename':
        catalog.source_document.filename = ' '
    elif tamper == 'blank_hash':
        catalog.source_document.sha256 = ''
    else:
        catalog.source_document.sha256 = 'not-a-sha256'
    with pytest.raises(SectionPlannerError, match='provenance is insufficient'):
        _plan(analysis, match, catalog)


def test_unselected_records_also_require_provenance():
    catalog = _catalog([
        _evidence_record('ev-py', EvidenceType.TECHNICAL_SKILL, 'Python', section_reference='technical-profile'),
        _evidence_record('ev-contact', EvidenceType.CONTACT, 'Synthetic contact', block_index=None),
    ])
    analysis = _analysis()
    with pytest.raises(SectionPlannerError, match='provenance is insufficient'):
        _plan(analysis, _match(analysis, catalog), catalog)


@pytest.mark.parametrize('field,value', [
    ('source_filename', 'another-synthetic-source.docx'),
    ('source_sha256', 'b' * 64),
])
def test_service_rejects_a_mismatched_snapshot(field, value):
    catalog = _catalog([
        _evidence_record('ev-py', EvidenceType.TECHNICAL_SKILL, 'Python', section_reference='technical-profile'),
    ])
    analysis = _analysis()
    snapshot = _snapshot_with_sections(['technical-profile']).model_copy(update={field: value})
    with pytest.raises(SectionPlannerError, match='Snapshot and catalog source'):
        _plan(analysis, _match(analysis, catalog), catalog, snapshot)


def test_snapshot_schema_is_checked_against_supported_extraction_version():
    catalog = _catalog([
        _evidence_record('ev-py', EvidenceType.TECHNICAL_SKILL, 'Python', section_reference='technical-profile'),
    ])
    analysis = _analysis()
    snapshot = _snapshot_with_sections(['technical-profile']).model_copy(update={'schema_version': '2.0.0'})
    with pytest.raises(SectionPlannerError, match='snapshot schema version is not supported'):
        _plan(analysis, _match(analysis, catalog), catalog, snapshot)


@pytest.mark.parametrize('target', ['catalog', 'record'])
def test_snapshot_sections_must_cover_relevant_catalog_references(target):
    catalog = _catalog([
        _evidence_record('ev-py', EvidenceType.TECHNICAL_SKILL, 'Python', section_reference='technical-profile'),
    ])
    if target == 'catalog':
        catalog.section_references.append('another-section')
    else:
        catalog.evidence_records[0].section_reference = 'another-section'
    analysis = _analysis()
    snapshot = _snapshot_with_sections(['technical-profile'])
    with pytest.raises(SectionPlannerError, match='unknown.*section'):
        _plan(analysis, _match(analysis, catalog), catalog, snapshot)


def test_sufficient_provenance_accepts_partial_snapshot_without_pair_revalidation():
    catalog = _catalog([
        _evidence_record('ev-py', EvidenceType.TECHNICAL_SKILL, 'Python', section_reference='technical-profile', block_index=0),
    ])
    analysis = _analysis()
    snapshot = _snapshot_with_sections(['technical-profile'])
    assert not snapshot.blocks
    plan = _plan(analysis, _match(analysis, catalog), catalog, snapshot)
    assert plan.planning_metadata.snapshot_available is True
    assert plan.selected_evidence_ids == ['ev-py']


def test_approved_extractor_paragraph_and_nested_table_output_remains_plannable(tmp_path):
    document = Document()
    document.add_heading('1. Technical Profile', level=1)
    document.add_paragraph('Python')
    table = document.add_table(rows=1, cols=1)
    table.cell(0, 0).text = 'SQL'
    nested = table.cell(0, 0).add_table(rows=1, cols=1)
    nested.cell(0, 0).text = 'Kubernetes'
    source = builders.save(document, tmp_path / 'synthetic-extractor-control.docx')
    source_before = source.read_bytes()
    snapshot = MasterResumeExtractor(source).extract()
    catalog = build_evidence_catalog(snapshot)
    analysis = _analysis()
    match = _match(analysis, catalog)
    assert any(record.table_context is not None for record in catalog.evidence_records)
    assert any(record.source_location.nested_table_path for record in catalog.evidence_records)
    for supplied_snapshot in (None, snapshot):
        plan = _plan(analysis, match, catalog, supplied_snapshot)
        assert plan.selected_evidence_ids
        assert plan.planning_metadata.source_candidate_source_sha256 == hashlib.sha256(source_before).hexdigest()
    assert source.read_bytes() == source_before


@pytest.mark.parametrize('case,expected', [
    ('unknown_id', 'Job match references an unknown candidate evidence identifier.'),
    ('unknown_section', 'Evidence record references an unknown source section.'),
    ('duplicate_id', 'Candidate evidence contains conflicting records for one identifier.'),
    ('provenance', 'Candidate evidence provenance is insufficient for section planning.'),
    ('cross_artifact', 'Candidate evidence, job analysis, and job match are not mutually consistent.'),
    ('snapshot_identity', 'Snapshot and catalog source filenames disagree.'),
])
def test_planner_integrity_errors_are_content_free_and_distinguishable(case, expected):
    marker = 'SYNTHETIC_PRIVATE_REVIEW_MARKER'
    item = _evidence_record('ev-py', EvidenceType.TECHNICAL_SKILL, 'Python', section_reference='technical-profile')
    catalog = _catalog([item])
    analysis = _analysis()
    snapshot = None
    if case == 'unknown_section':
        item.section_reference = marker
    elif case == 'duplicate_id':
        item.evidence_id = marker
        catalog = _catalog([item, item.model_copy(deep=True)])
    elif case == 'provenance':
        item.source_document = marker
    elif case == 'snapshot_identity':
        snapshot = _snapshot_with_sections(['technical-profile']).model_copy(update={'source_filename': marker})
    match = _match(analysis, catalog)
    if case in ('unknown_id', 'cross_artifact'):
        data = match.model_dump(mode='json')
        requirement = data['requirements'][0]
        if case == 'unknown_id':
            requirement['matched_evidence_ids'] = [marker]
            requirement['matched_evidence'][0]['evidence_id'] = marker
            requirement['components'][0]['matched_evidence_ids'] = [marker]
        else:
            requirement['matched_evidence'][0]['source_document'] = marker
        match = CandidateJobMatch.model_validate_json(json.dumps(data))
    with pytest.raises(SectionPlannerError) as caught:
        _plan(analysis, match, catalog, snapshot)
    assert str(caught.value) == expected
    assert marker not in str(caught.value)


# ---------------------------------------------------------------- determinism


def test_repeated_identical_inputs_produce_identical_plans():
    catalog = _catalog([
        _evidence_record('ev-py', EvidenceType.TECHNICAL_SKILL, 'Python', section_reference='technical-profile', block_index=1),
        _evidence_record('ev-sql', EvidenceType.TECHNICAL_SKILL, 'SQL', section_reference='technical-profile', block_index=2),
    ])
    analysis = _analysis()
    match = _match(analysis, catalog)
    plan_a = _plan(analysis, match, catalog)
    plan_b = _plan(analysis, match, catalog)
    assert plan_a.model_dump_json() == plan_b.model_dump_json()


def test_stable_section_ordering():
    catalog = _catalog([
        _evidence_record('ev-edu', EvidenceType.EDUCATION, 'BS CS', section_reference='education', block_index=1),
        _evidence_record('ev-exp', EvidenceType.EXPERIENCE, 'Role A', section_reference='professional-experience-and-engineering-training', block_index=2),
    ])
    analysis = _analysis()
    match = _match(analysis, catalog)
    plan = _plan(analysis, match, catalog)

    order = [section.section_id for section in plan.ordered_sections if section.item_count > 0]
    assert order == ['professional-experience', 'education']


def test_stable_skill_ordering():
    payload = dict(grounded_job_payload())
    payload['required_skills'] = [{
        'name': 'Python',
        'quotation': 'Required skills: Python.',
        'classification_context': 'Required skills: Python.',
    }]
    payload['preferred_skills'] = [{
        'name': 'SQL',
        'quotation': 'Preferred skills: SQL.',
        'classification_context': 'Preferred skills: SQL.',
    }]
    analysis = _analysis(payload)
    catalog = _catalog([
        _evidence_record('ev-py', EvidenceType.TECHNICAL_SKILL, 'Python', section_reference='technical-profile', block_index=1),
        _evidence_record('ev-sql', EvidenceType.TECHNICAL_SKILL, 'SQL', section_reference='technical-profile', block_index=2),
    ])
    match = _match(analysis, catalog)
    plan = _plan(analysis, match, catalog)

    tiers = [skill.priority_tier for skill in plan.prioritized_skills]
    assert tiers[0] == 'required'
    assert tiers[1] == 'preferred'


def test_stable_serialization_round_trip():
    catalog = _catalog([
        _evidence_record('ev-py', EvidenceType.TECHNICAL_SKILL, 'Python', section_reference='technical-profile', block_index=1),
    ])
    analysis = _analysis()
    match = _match(analysis, catalog)
    plan = _plan(analysis, match, catalog)
    restored = TypeAdapter(ResumeSectionPlan).validate_json(plan.model_dump_json())
    assert restored == plan
