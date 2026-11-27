'''Historical schema-1 data for inspection, never a trusted generation contract.

Schema 1 lacked complete provenance and factual bindings. Its original defaults
are retained here; current output requires source-backed re-planning.
'''

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from resume_assistant.job_models import MeaningfulText
from resume_assistant.matching_models import MatchStatus, RequirementCategory


class _LegacyModel(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True, revalidate_instances='always')


class LegacyPlanningMetadata(_LegacyModel):
    schema_version: Literal['1.0.0'] = '1.0.0'
    planner_version: str = '1.0.0'
    source_candidate_evidence_schema_version: Literal['3.0.0']
    source_job_analysis_schema_version: Literal['2.0.0']
    source_job_match_schema_version: Literal['2.0.0']
    source_candidate_source_sha256: str
    source_candidate_evidence_record_count: int
    snapshot_available: bool
    manifest_available: bool


class LegacyTargetJobContext(_LegacyModel):
    role_title: str | None
    company_name: str | None
    seniority_level: str | None
    employment_type: str | None
    work_arrangement: str | None
    required_skill_count: int = Field(ge=0)
    preferred_skill_count: int = Field(ge=0)
    uncertain_skill_count: int = Field(ge=0)
    responsibility_count: int = Field(ge=0)
    interpretation_status: Literal['ai_generated_unverified']


class LegacySkillAssociation(_LegacyModel):
    requirement_id: str = Field(pattern=r'^[0-9a-f]{64}$')
    priority_tier: Literal['required', 'preferred']
    matched_evidence_ids: list[str] = Field(default_factory=list)


class LegacyPlannedSkill(_LegacyModel):
    skill_text: MeaningfulText
    priority_tier: Literal['required', 'preferred', 'other']
    matched_evidence_ids: list[str] = Field(default_factory=list)
    source_section_reference: str | None = None
    requirement_id: str | None = None
    requirement_associations: list[LegacySkillAssociation] = Field(default_factory=list)


class LegacyRequirementCoverage(_LegacyModel):
    requirement_id: str = Field(pattern=r'^[0-9a-f]{64}$')
    category: RequirementCategory
    status: MatchStatus
    matched_evidence_ids: list[str] = Field(default_factory=list)
    unmatched_components: list[MeaningfulText] = Field(default_factory=list)


class LegacySummaryBlueprint(_LegacyModel):
    professional_identity_evidence_ids: list[str] = Field(default_factory=list)
    experience_categories: list[str] = Field(default_factory=list)
    technical_capability_skill_texts: list[str] = Field(default_factory=list)
    relevant_project_evidence_ids: list[str] = Field(default_factory=list)


class LegacyBudgets(_LegacyModel):
    max_output_sections: int = Field(ge=1)
    max_skills: int = Field(ge=0)
    max_experience_entries: int = Field(ge=0)
    max_projects: int = Field(ge=0)
    max_education_entries: int = Field(default=5, ge=0)
    max_certifications: int = Field(ge=0)
    target_pages: Literal[1, 2]
    page_guarantee: bool = False


class LegacyExcludedEvidence(_LegacyModel):
    evidence_id: str
    reason: str


class LegacyResumeEntry(_LegacyModel):
    entry_id: str
    source_section_reference: str
    identity_evidence_id: str | None = None
    grouping_basis: Literal['source_heading', 'table_row', 'unresolved_source_span']
    selected_evidence_ids: list[str] = Field(min_length=1)


class LegacyPlannedSection(_LegacyModel):
    section_id: str
    heading_text: str
    source_section_references: list[str] = Field(default_factory=list)
    selected_evidence_ids: list[str] = Field(default_factory=list)
    item_count: int = Field(ge=0)
    budget: int = Field(ge=0)
    dropped_empty: bool = False
    entries: list[LegacyResumeEntry] = Field(default_factory=list)
    entry_grouping_complete: bool = False


class LegacyResumeSectionPlan(_LegacyModel):
    '''Unverified historical data; cannot be passed as a schema-2 result.'''
    schema_version: Literal['1.0.0'] = '1.0.0'
    planning_metadata: LegacyPlanningMetadata
    target_job_context: LegacyTargetJobContext
    ordered_sections: list[LegacyPlannedSection] = Field(default_factory=list)
    selected_evidence_ids: list[str] = Field(default_factory=list)
    prioritized_skills: list[LegacyPlannedSkill] = Field(default_factory=list)
    requirement_coverage: list[LegacyRequirementCoverage] = Field(default_factory=list)
    professional_summary_blueprint: LegacySummaryBlueprint
    budgets: LegacyBudgets
    excluded_evidence: list[LegacyExcludedEvidence] = Field(default_factory=list)
    human_review_required: Literal[True] = True
