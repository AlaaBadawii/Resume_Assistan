'''Strict schema-2 planning contract with compact source and factual bindings.'''

import hashlib
import json
import re
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from resume_assistant.job_models import MeaningfulText
from resume_assistant.matching_models import ComponentMatch, MatchStatus, RequirementCategory
from resume_assistant.models import EvidenceType, SourceLocation, TableCellSource
from resume_assistant.publication import PublicationManifest

PLAN_SCHEMA_VERSION = '2.0.0'
PLANNER_VERSION = '2.0.0'
OutputSectionId = Literal[
    'professional-summary', 'technical-skills', 'professional-experience',
    'projects', 'education', 'certifications',
]
OUTPUT_SECTION_ORDER = (
    'professional-summary', 'technical-skills', 'professional-experience',
    'projects', 'education', 'certifications',
)
SECTION_HEADING_TEXT = {
    'professional-summary': 'Professional Summary',
    'technical-skills': 'Technical Skills',
    'professional-experience': 'Professional Experience',
    'projects': 'Projects',
    'education': 'Education',
    'certifications': 'Certifications',
}
SOURCE_SECTION_TO_OUTPUT_SECTION = {
    'professional-identity': 'professional-summary',
    'technical-profile': 'technical-skills',
    'technical-skills-database': 'technical-skills',
    'professional-experience-and-engineering-training': 'professional-experience',
    'independent-software-and-ai-projects': 'projects',
    'additional-ai-and-multimodal-applications': 'projects',
    'alx-software-engineering-project-archive': 'projects',
    'education': 'education',
    'certifications-and-professional-development': 'certifications',
}
BACKGROUND_SECTION_REFERENCES = frozenset(SOURCE_SECTION_TO_OUTPUT_SECTION)
SECTION_EVIDENCE_TYPES = {
    'professional-summary': EvidenceType.PROFESSIONAL_SUMMARY,
    'technical-skills': EvidenceType.TECHNICAL_SKILL,
    'professional-experience': EvidenceType.EXPERIENCE,
    'projects': EvidenceType.PROJECT,
    'education': EvidenceType.EDUCATION,
    'certifications': EvidenceType.CERTIFICATION,
}
DEFAULT_BUDGETS = {
    'max_output_sections': len(OUTPUT_SECTION_ORDER), 'max_skills': 15,
    'max_experience_entries': 5, 'max_projects': 5, 'max_education_entries': 5,
    'max_certifications': 5, 'target_pages': 2,
}
ENTRY_GROUPED_SECTIONS = frozenset({'professional-experience', 'projects', 'education', 'certifications'})
SUPPORTED_STATUSES = frozenset({'supported', 'partially_supported'})
SKILL_TIER_ORDER = ('required', 'preferred', 'other')
Sha256 = Annotated[str, Field(pattern=r'^[0-9a-fA-F]{64}$')]
RequirementId = Annotated[str, Field(pattern=r'^[0-9a-f]{64}$')]
ExclusionReason = Literal[
    'unclassified_section_no_output_mapping', 'source_section_not_mapped_to_output_section',
    'content_not_eligible_for_output_section', 'content_budget_exhausted',
    'output_section_budget_exhausted',
]


def skill_identity(text: str) -> str:
    return ' '.join(text.split()).casefold()


def content_sha256(text: str) -> str:
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def source_order_key(record) -> tuple:
    '''Approved body/paragraph/row ancestry ordering, including negative preorder IDs.'''
    location = record.source_location
    if location is None:
        return (-1, -1, (), -1, -1, '', record.evidence_id)
    body = -1 if location.body_block_index is None else location.body_block_index
    paragraph = -1 if location.paragraph_index is None else location.paragraph_index
    cell = -1 if location.cell_index is None else location.cell_index
    position_in_cell = -1 if location.paragraph_position_in_cell is None else location.paragraph_position_in_cell
    path = tuple(abs(value) if index % 3 == 0 else value for index, value in enumerate(location.nested_table_path or ()))
    table = -1 if location.table_index is None else abs(location.table_index)
    row = -1 if location.row_index is None else location.row_index
    return (body, paragraph, (*path, table, row), cell, position_in_cell, location.document_part, record.evidence_id)


def _unique(values: list, message: str) -> None:
    if len(values) != len(set(values)):
        raise ValueError(message)


def _ancestry(path: list[int] | None) -> list[int] | None:
    if path is not None and (len(path) % 3 or any(value < 0 for index, value in enumerate(path) if index % 3)):
        raise ValueError('Nested ancestry must retain complete table/row/cell coordinates.')
    return path


def _unambiguous_object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError('Duplicate JSON fields are invalid.')
        value[key] = item
    return value


def _standard_json_constant(value):
    raise ValueError('Nonstandard JSON constants are invalid.')


class StrictPlanModel(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True, frozen=True, revalidate_instances='always', validate_default=True)

    @classmethod
    def model_validate_json(cls, json_data, **kwargs):
        try:
            json.loads(json_data, object_pairs_hook=_unambiguous_object, parse_constant=_standard_json_constant)
        except (ValueError, TypeError):
            raise ValidationError.from_exception_data(cls.__name__, [{
                'type': 'value_error', 'loc': (), 'input': None,
                'ctx': {'error': ValueError('Plan JSON must be unambiguous standard JSON.')},
            }]) from None
        return super().model_validate_json(json_data, **kwargs)


class PlanSourceLocation(SourceLocation):
    model_config = StrictPlanModel.model_config
    document_part: MeaningfulText = 'word/document.xml'
    body_block_index: int = Field(ge=0)
    paragraph_index: int | None = Field(default=None, ge=0)
    row_index: int | None = Field(default=None, ge=0)
    cell_index: int | None = Field(default=None, ge=0)
    paragraph_position_in_cell: int | None = Field(default=None, ge=0)

    _validate_ancestry = field_validator('nested_table_path')(_ancestry)


class PlanCellSource(TableCellSource):
    model_config = StrictPlanModel.model_config
    row_index: int = Field(ge=0)
    cell_index: int = Field(ge=0)
    paragraph_positions: list[Annotated[int, Field(ge=0)]] = Field(default_factory=list)
    _validate_ancestry = field_validator('nested_table_path')(_ancestry)

    @field_validator('paragraph_positions')
    @classmethod
    def unique_positions(cls, values):
        _unique(values, 'Contributing paragraph positions must be unique.')
        return values


class ExtractionGenerationIdentity(StrictPlanModel):
    publication_format_version: Literal['1.0.0'] = '1.0.0'
    bundle_key: str = Field(pattern=r'^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$')
    generation_id: str = Field(pattern=r'^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$')
    source_filename: MeaningfulText
    source_sha256: Sha256
    snapshot_sha256: Sha256
    catalog_sha256: Sha256

    @classmethod
    def from_manifest(cls, manifest: PublicationManifest) -> 'ExtractionGenerationIdentity':
        return cls(
            publication_format_version=manifest.format_version,
            bundle_key=manifest.bundle_key, generation_id=manifest.generation_id,
            source_filename=manifest.source_filename, source_sha256=manifest.source_sha256,
            snapshot_sha256=manifest.snapshot.sha256, catalog_sha256=manifest.catalog.sha256,
        )


class PlanningMetadata(StrictPlanModel):
    schema_version: Literal['2.0.0'] = PLAN_SCHEMA_VERSION
    planner_version: Literal['2.0.0'] = PLANNER_VERSION
    source_candidate_evidence_schema_version: Literal['3.0.0']
    source_job_analysis_schema_version: Literal['2.0.0']
    source_job_match_schema_version: Literal['2.0.0']
    source_candidate_source_sha256: Sha256
    source_candidate_filename: MeaningfulText
    source_candidate_evidence_record_count: int = Field(ge=0)
    snapshot_available: bool
    manifest_available: bool
    extraction_generation: ExtractionGenerationIdentity | None = None

    @model_validator(mode='after')
    def validate_generation(self) -> 'PlanningMetadata':
        generation = self.extraction_generation
        if generation is not None and (
            not self.manifest_available or not self.snapshot_available
            or generation.source_filename != self.source_candidate_filename
            or generation.source_sha256 != self.source_candidate_source_sha256
        ):
            raise ValueError('Extraction generation must belong to the declared candidate source.')
        return self


class TargetJobContext(StrictPlanModel):
    role_title: MeaningfulText | None
    company_name: MeaningfulText | None
    seniority_level: MeaningfulText | None
    employment_type: MeaningfulText | None
    work_arrangement: MeaningfulText | None
    required_skill_count: int = Field(ge=0)
    preferred_skill_count: int = Field(ge=0)
    uncertain_skill_count: int = Field(ge=0)
    responsibility_count: int = Field(ge=0)
    interpretation_status: Literal['ai_generated_unverified']


class SkillRequirementAssociation(StrictPlanModel):
    requirement_id: RequirementId
    priority_tier: Literal['required', 'preferred']
    matched_evidence_ids: list[MeaningfulText] = Field(min_length=1)

    @model_validator(mode='after')
    def validate_ids(self) -> 'SkillRequirementAssociation':
        _unique(self.matched_evidence_ids, 'Association evidence must be unique.')
        return self


class PlannedSkill(StrictPlanModel):
    skill_text: MeaningfulText
    priority_tier: Literal['required', 'preferred', 'other']
    matched_evidence_ids: list[MeaningfulText] = Field(min_length=1)
    source_section_reference: MeaningfulText | None = None
    requirement_id: RequirementId | None = None
    requirement_associations: list[SkillRequirementAssociation] = Field(default_factory=list)

    @model_validator(mode='after')
    def validate_support(self) -> 'PlannedSkill':
        _unique(self.matched_evidence_ids, 'Skill evidence must be unique.')
        _unique([item.requirement_id for item in self.requirement_associations], 'Skill parent requirements must be unique.')
        if self.priority_tier == 'other':
            if self.requirement_id is not None or self.requirement_associations:
                raise ValueError('Documented background skills cannot invent parent requirements.')
        elif (
            not self.requirement_associations
            or self.requirement_id != self.requirement_associations[0].requirement_id
            or self.priority_tier != self.requirement_associations[0].priority_tier
        ):
            raise ValueError('Prioritized skills must retain their strongest primary parent.')
        ranks = [SKILL_TIER_ORDER.index(item.priority_tier) for item in self.requirement_associations]
        if ranks != sorted(ranks):
            raise ValueError('Skill associations must retain strongest-parent priority order.')
        for item in self.requirement_associations:
            if not set(item.matched_evidence_ids).issubset(self.matched_evidence_ids):
                raise ValueError('Association support must belong to the skill evidence.')
        return self


class PlannedRequirementCoverage(StrictPlanModel):
    requirement_id: RequirementId
    category: RequirementCategory
    status: MatchStatus
    matched_evidence_ids: list[MeaningfulText] = Field(default_factory=list)
    unmatched_components: list[MeaningfulText] = Field(default_factory=list)
    components: list[ComponentMatch] = Field(min_length=1)

    @model_validator(mode='after')
    def validate_components(self) -> 'PlannedRequirementCoverage':
        _unique([(component.kind, component.text) for component in self.components], 'Requirement components must be unique.')
        ids = list(dict.fromkeys(identifier for component in self.components for identifier in component.matched_evidence_ids))
        missing = [component.text for component in self.components if component.status != 'supported']
        if self.matched_evidence_ids != ids or self.unmatched_components != missing:
            raise ValueError('Requirement coverage must preserve its explicit component support.')
        expected = 'supported' if not missing else ('partially_supported' if ids else self.components[0].status)
        if self.status != expected or (not ids and len({component.status for component in self.components}) != 1):
            raise ValueError('Requirement status must agree with its matcher components.')
        return self


class PlanBudgets(StrictPlanModel):
    max_output_sections: int = Field(ge=1)
    max_skills: int = Field(ge=0)
    max_experience_entries: int = Field(ge=0)
    max_projects: int = Field(ge=0)
    max_education_entries: int = Field(default=DEFAULT_BUDGETS['max_education_entries'], ge=0)
    max_certifications: int = Field(ge=0)
    target_pages: Literal[1, 2]
    page_guarantee: Literal[False] = False

    @field_validator('target_pages', mode='before')
    @classmethod
    def exact_page_integer(cls, value):
        if type(value) is not int:
            raise ValueError('Target pages must use an exact integer.')
        return value

    @field_validator('page_guarantee', mode='before')
    @classmethod
    def never_guarantee_pages(cls, value):
        if type(value) is not bool or value is not False:
            raise ValueError('Content budgets cannot guarantee a page count.')
        return value

    def section_caps(self) -> dict[str, int]:
        caps = {
            'professional-summary': 1, 'technical-skills': self.max_skills,
            'professional-experience': self.max_experience_entries, 'projects': self.max_projects,
            'education': self.max_education_entries, 'certifications': self.max_certifications,
        }
        return caps if self.target_pages == 2 else {key: (max(value // 2, 1) if value else 0) for key, value in caps.items()}


class ExcludedEvidence(StrictPlanModel):
    evidence_id: MeaningfulText
    reason: ExclusionReason


class PlannedResumeEntry(StrictPlanModel):
    entry_id: MeaningfulText
    source_section_reference: MeaningfulText
    identity_evidence_id: MeaningfulText | None = None
    grouping_basis: Literal['source_heading', 'table_row', 'unresolved_source_span']
    selected_evidence_ids: list[MeaningfulText] = Field(min_length=1)

    @model_validator(mode='after')
    def validate_membership(self) -> 'PlannedResumeEntry':
        _unique(self.selected_evidence_ids, 'Resume entry evidence membership must be unique.')
        if self.identity_evidence_id is not None and self.identity_evidence_id not in self.selected_evidence_ids:
            raise ValueError('Resume entry identity must belong to the selected entry.')
        if (self.grouping_basis == 'unresolved_source_span') != (self.identity_evidence_id is None):
            raise ValueError('Unresolved source spans cannot assert an entry identity.')
        if self.entry_id != (self.identity_evidence_id or self.selected_evidence_ids[0]):
            raise ValueError('Resume entry identifiers must retain their source anchor.')
        return self


class PlannedSection(StrictPlanModel):
    section_id: OutputSectionId
    heading_text: MeaningfulText
    source_section_references: list[MeaningfulText] = Field(default_factory=list)
    selected_evidence_ids: list[MeaningfulText] = Field(min_length=1)
    item_count: int = Field(ge=1)
    budget: int = Field(ge=0)
    dropped_empty: Literal[False] = False
    entries: list[PlannedResumeEntry] = Field(default_factory=list)
    entry_grouping_complete: bool = True

    @field_validator('dropped_empty', mode='before')
    @classmethod
    def exact_emitted_section_flag(cls, value):
        if type(value) is not bool or value is not False:
            raise ValueError('Emitted sections must use an exact false empty-section flag.')
        return value

    @model_validator(mode='after')
    def validate_section(self) -> 'PlannedSection':
        _unique(self.selected_evidence_ids, 'Section evidence must be unique.')
        _unique(self.source_section_references, 'Section source references must be unique.')
        _unique([entry.entry_id for entry in self.entries], 'Resume entry IDs must be unique within a section.')
        if self.item_count != len(self.selected_evidence_ids) or self.heading_text != SECTION_HEADING_TEXT[self.section_id]:
            raise ValueError('Section heading and item count must agree with its content.')
        if self.section_id in ENTRY_GROUPED_SECTIONS:
            members = [identifier for entry in self.entries for identifier in entry.selected_evidence_ids]
            _unique(members, 'Resume entries cannot share selected evidence.')
            if set(members) != set(self.selected_evidence_ids) or len(self.entries) > self.budget:
                raise ValueError('Section selections and entry budgets must agree with entry membership.')
        elif self.entries or self.item_count > self.budget:
            raise ValueError('Record sections cannot invent resume entries or exceed their record cap.')
        if self.entry_grouping_complete != all(entry.grouping_basis != 'unresolved_source_span' for entry in self.entries):
            raise ValueError('Entry grouping completeness must describe the selected source groups.')
        return self


class EvidenceProvenance(StrictPlanModel):
    '''All catalog record identities; large source text remains in the original catalog.'''
    evidence_id: MeaningfulText
    evidence_type: EvidenceType
    source_document: MeaningfulText
    source_sha256: Sha256
    content_sha256: Sha256
    source_location: PlanSourceLocation
    cell_sources: list[PlanCellSource] | None
    source_section_reference: MeaningfulText | None
    output_section_id: OutputSectionId | None
    resume_entry_id: MeaningfulText | None
    content_eligible: bool
    atomic_skill_text: MeaningfulText | None = None

    @model_validator(mode='after')
    def validate_atomic_source(self) -> 'EvidenceProvenance':
        if self.atomic_skill_text is not None and (
            not self.content_eligible or self.evidence_type != EvidenceType.TECHNICAL_SKILL
            or SOURCE_SECTION_TO_OUTPUT_SECTION.get(self.source_section_reference) != 'technical-skills'
            or not re.fullmatch(r'[\w.+#/-]+', self.atomic_skill_text.strip())
            or content_sha256(self.atomic_skill_text) != self.content_sha256
        ):
            raise ValueError('Atomic background labels must retain their exact source content binding.')
        return self


class IdentityStatement(StrictPlanModel):
    '''One verbatim professional-identity source paragraph, not generated prose.'''
    statement_text: MeaningfulText
    evidence_ids: list[MeaningfulText] = Field(min_length=1, max_length=1)


class ExperienceCategoryBinding(StrictPlanModel):
    category: Literal['professional-experience']
    entry_ids: list[MeaningfulText] = Field(min_length=1)
    evidence_ids: list[MeaningfulText] = Field(min_length=1)

    @model_validator(mode='after')
    def validate_ids(self) -> 'ExperienceCategoryBinding':
        _unique(self.entry_ids, 'Experience entry bindings must be unique.')
        _unique(self.evidence_ids, 'Experience evidence bindings must be unique.')
        return self


class ProfessionalSummaryBlueprint(StrictPlanModel):
    professional_identity_evidence_ids: list[MeaningfulText] = Field(default_factory=list)
    experience_categories: list[Literal['professional-experience']] = Field(default_factory=list)
    technical_capability_skill_texts: list[MeaningfulText] = Field(default_factory=list)
    relevant_project_evidence_ids: list[MeaningfulText] = Field(default_factory=list)
    identity_statements: list[IdentityStatement]
    technical_capabilities: list[PlannedSkill]
    experience_bindings: list[ExperienceCategoryBinding]
    project_highlights: list[PlannedResumeEntry]


class ResumeSectionPlan(StrictPlanModel):
    schema_version: Literal['2.0.0'] = PLAN_SCHEMA_VERSION
    planning_metadata: PlanningMetadata
    target_job_context: TargetJobContext
    ordered_sections: list[PlannedSection] = Field(default_factory=list)
    selected_evidence_ids: list[MeaningfulText] = Field(default_factory=list)
    prioritized_skills: list[PlannedSkill] = Field(default_factory=list)
    requirement_coverage: list[PlannedRequirementCoverage] = Field(default_factory=list)
    professional_summary_blueprint: ProfessionalSummaryBlueprint
    budgets: PlanBudgets
    excluded_evidence: list[ExcludedEvidence] = Field(default_factory=list)
    evidence_provenance: list[EvidenceProvenance]
    human_review_required: Literal[True] = True

    @field_validator('human_review_required', mode='before')
    @classmethod
    def exact_review_flag(cls, value):
        if type(value) is not bool or value is not True:
            raise ValueError('Planning instructions always require human review.')
        return value

    @model_validator(mode='after')
    def validate_integrity(self) -> 'ResumeSectionPlan':
        provenance = {reference.evidence_id: reference for reference in self.evidence_provenance}
        _unique([reference.evidence_id for reference in self.evidence_provenance], 'Provenance record IDs must be unique.')
        _unique(self.selected_evidence_ids, 'Flat selected evidence must be unique.')
        excluded = {item.evidence_id: item.reason for item in self.excluded_evidence}
        _unique([item.evidence_id for item in self.excluded_evidence], 'Excluded evidence must be unique.')
        selected = set(self.selected_evidence_ids)
        if selected.intersection(excluded) or selected | set(excluded) != set(provenance):
            raise ValueError('Every provenance record must have exactly one selected or excluded outcome.')
        if self.planning_metadata.source_candidate_evidence_record_count != len(provenance):
            raise ValueError('Candidate record count must agree with complete provenance accounting.')
        sections = {section.section_id: section for section in self.ordered_sections}
        _unique([section.section_id for section in self.ordered_sections], 'Output sections must be unique.')
        if len(sections) > self.budgets.max_output_sections or list(sections) != [identifier for identifier in OUTPUT_SECTION_ORDER if identifier in sections]:
            raise ValueError('Output section count and presentation order must respect the contract.')
        flat = [identifier for section in self.ordered_sections for identifier in section.selected_evidence_ids]
        if flat != self.selected_evidence_ids:
            raise ValueError('Flat selected evidence must equal ordered section selections.')
        caps = self.budgets.section_caps()
        assignments = {}
        entries = {}
        for section in self.ordered_sections:
            if section.budget != caps[section.section_id]:
                raise ValueError('Sections must report the effective configured content cap.')
            if any(identifier not in provenance for identifier in section.selected_evidence_ids):
                raise ValueError('Section selections must reference known provenance records.')
            if section.selected_evidence_ids != sorted(section.selected_evidence_ids, key=lambda identifier: source_order_key(provenance[identifier])):
                raise ValueError('Selected section records must retain available source ordering.')
            references = list(dict.fromkeys(provenance[identifier].source_section_reference for identifier in section.selected_evidence_ids))
            if section.source_section_references != references:
                raise ValueError('Section source references must describe surviving selections.')
            member_entries = {identifier: entry.entry_id for entry in section.entries for identifier in entry.selected_evidence_ids}
            for entry in section.entries:
                if entry.entry_id in entries:
                    raise ValueError('Resume entry IDs cannot be reused across sections.')
                entries[entry.entry_id] = entry
                if any(provenance[identifier].source_section_reference != entry.source_section_reference for identifier in entry.selected_evidence_ids):
                    raise ValueError('Resume entry members must retain their source section.')
            for identifier in section.selected_evidence_ids:
                assignments[identifier] = (section.section_id, member_entries.get(identifier))
        for identifier, reference in provenance.items():
            if reference.source_document != self.planning_metadata.source_candidate_filename or reference.source_sha256 != self.planning_metadata.source_candidate_source_sha256:
                raise ValueError('Per-evidence source identity must agree with the candidate source.')
            expected_assignment = assignments.get(identifier, (None, None))
            if (reference.output_section_id, reference.resume_entry_id) != expected_assignment:
                raise ValueError('Provenance section and entry associations must equal actual selections.')
            destination = SOURCE_SECTION_TO_OUTPUT_SECTION.get(reference.source_section_reference)
            if identifier in selected:
                if not reference.content_eligible or destination != reference.output_section_id or reference.evidence_type != SECTION_EVIDENCE_TYPES[destination]:
                    raise ValueError('Selected evidence must be eligible for its source-derived output section.')
            else:
                reason = excluded[identifier]
                if reference.source_section_reference is None:
                    expected = {'unclassified_section_no_output_mapping'}
                elif destination is None:
                    expected = {'source_section_not_mapped_to_output_section'}
                elif not reference.content_eligible:
                    expected = {'content_not_eligible_for_output_section'}
                else:
                    expected = {'content_budget_exhausted', 'output_section_budget_exhausted'}
                if reason not in expected:
                    raise ValueError('Exclusion reason must describe the evidence outcome.')
        self._validate_skill_contract(provenance, selected)
        self._validate_blueprint_contract(provenance, sections)
        return self

    def _validate_skill_contract(self, provenance, selected) -> None:
        requirements = {item.requirement_id: item for item in self.requirement_coverage}
        _unique([item.requirement_id for item in self.requirement_coverage], 'Requirement coverage IDs must be unique.')
        for item in self.requirement_coverage:
            if not set(item.matched_evidence_ids).issubset(provenance):
                raise ValueError('Requirement coverage must reference original catalog evidence.')
        for category, count in (
            ('required_skills', self.target_job_context.required_skill_count),
            ('preferred_skills', self.target_job_context.preferred_skill_count),
            ('uncertain_skills', self.target_job_context.uncertain_skill_count),
            ('responsibilities', self.target_job_context.responsibility_count),
        ):
            if sum(item.category == category for item in self.requirement_coverage) != count:
                raise ValueError('Requirement coverage counts must preserve target context.')
        _unique([skill_identity(skill.skill_text) for skill in self.prioritized_skills], 'Prioritized labels must be conservatively deduplicated.')
        if len(self.prioritized_skills) > self.budgets.section_caps()['technical-skills']:
            raise ValueError('Prioritized skills must respect the effective skill cap.')
        tiers = [SKILL_TIER_ORDER.index(skill.priority_tier) for skill in self.prioritized_skills]
        if tiers != sorted(tiers):
            raise ValueError('Skill priorities must retain required/preferred/background order.')
        for skill in self.prioritized_skills:
            ids = set(skill.matched_evidence_ids)
            if not ids.issubset(provenance) or not ids.intersection(selected) or any(not provenance[identifier].content_eligible for identifier in ids):
                raise ValueError('Planned skills require known eligible evidence with surviving support.')
            if skill.source_section_reference not in {provenance[identifier].source_section_reference for identifier in ids}:
                raise ValueError('Skill source reference must belong to its supporting evidence.')
            associated_ids = set()
            for association in skill.requirement_associations:
                parent = requirements.get(association.requirement_id)
                category = 'required_skills' if association.priority_tier == 'required' else 'preferred_skills'
                if parent is None or parent.category != category or parent.status not in SUPPORTED_STATUSES:
                    raise ValueError('Skill parents must retain supported matcher requirements and priority.')
                components = [component for component in parent.components if component.status == 'supported' and skill_identity(component.text) == skill_identity(skill.skill_text)]
                if not any(set(association.matched_evidence_ids).issubset(component.matched_evidence_ids) for component in components):
                    raise ValueError('Skill labels and associations must correspond to supported matcher components.')
                associated_ids.update(association.matched_evidence_ids)
            for identifier in ids - associated_ids:
                text = provenance[identifier].atomic_skill_text
                if text is None or skill_identity(text) != skill_identity(skill.skill_text):
                    raise ValueError('Additional skill evidence must be a matching documented atomic label.')

    def _validate_blueprint_contract(self, provenance, sections) -> None:
        blueprint = self.professional_summary_blueprint
        summary_ids = sections['professional-summary'].selected_evidence_ids if 'professional-summary' in sections else []
        identity_ids = [identifier for statement in blueprint.identity_statements for identifier in statement.evidence_ids]
        if identity_ids != summary_ids or blueprint.professional_identity_evidence_ids != identity_ids:
            raise ValueError('Professional identity projections must equal selected statement bindings.')
        for statement in blueprint.identity_statements:
            reference = provenance[statement.evidence_ids[0]]
            if reference.evidence_type != EvidenceType.PROFESSIONAL_SUMMARY or content_sha256(statement.statement_text) != reference.content_sha256:
                raise ValueError('Identity statements must retain their exact professional-summary source binding.')
        if blueprint.technical_capabilities != self.prioritized_skills or blueprint.technical_capability_skill_texts != [skill.skill_text for skill in self.prioritized_skills]:
            raise ValueError('Technical summary instructions must equal evidence-bound prioritized components.')
        experience = sections.get('professional-experience')
        expected_categories = ['professional-experience'] if experience is not None else []
        if blueprint.experience_categories != expected_categories or len(blueprint.experience_bindings) != len(expected_categories):
            raise ValueError('Experience categories require actual selected experience entries.')
        if experience is not None:
            binding = blueprint.experience_bindings[0]
            if binding.category != 'professional-experience' or binding.entry_ids != [entry.entry_id for entry in experience.entries] or binding.evidence_ids != experience.selected_evidence_ids:
                raise ValueError('Experience category bindings must retain selected entry and evidence membership.')
        projects = sections.get('projects')
        if blueprint.project_highlights != (projects.entries if projects is not None else []) or blueprint.relevant_project_evidence_ids != (projects.selected_evidence_ids if projects is not None else []):
            raise ValueError('Project highlights require exact selected project entry bindings.')
