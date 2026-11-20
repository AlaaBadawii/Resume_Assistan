'''Strict, provenance-linked output contract for the local matcher.'''

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from resume_assistant.job_models import MeaningfulText
from resume_assistant.models import EvidenceType, SourceLocation, TableCellSource

MATCHING_SCHEMA_VERSION = '2.0.0'
MATCHING_METHOD = 'exact_normalized_phrase_v2'

MatchStatus = Literal['supported', 'partially_supported', 'no_evidence_found', 'not_assessable']
ComponentStatus = Literal['supported', 'no_evidence_found', 'not_assessable']
CoverageStatus = Literal['assessed', 'incomplete', 'not_assessable']
RequirementCategory = Literal[
    'required_skills', 'preferred_skills', 'uncertain_skills', 'responsibilities',
    'education_requirements', 'experience_requirements', 'languages', 'certifications',
]
CoverageWarning = Literal[
    'empty_evidence_catalog', 'coverage_not_assessed', 'source_preservation_incomplete',
    'unsupported_content_present', 'unresolved_extraction_details_present',
    'section_classification_incomplete', 'unclassified_blocks_present',
    'extraction_warnings_present', 'evidence_record_count_mismatch', 'evidence_text_missing',
]


class _StrictMatchModel(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True, frozen=True, revalidate_instances='always')


class MatchSourceLocation(SourceLocation):
    '''Exact schema-3.0.0 coordinates, with strict matching-output validation.'''

    model_config = _StrictMatchModel.model_config


class MatchCellSource(TableCellSource):
    '''Contributing table-cell coordinates without copying cell text.'''

    model_config = _StrictMatchModel.model_config


class MatchedEvidenceReference(_StrictMatchModel):
    evidence_id: MeaningfulText
    evidence_type: EvidenceType
    source_document: str
    source_sha256: str | None
    source_location: MatchSourceLocation | None
    section_reference: str | None
    cell_sources: list[MatchCellSource]


class ComponentMatch(_StrictMatchModel):
    text: MeaningfulText
    kind: Literal['phrase', 'language', 'language_level'] = 'phrase'
    status: ComponentStatus
    matched_evidence_ids: list[MeaningfulText]
    matching_method: Literal['exact_normalized_phrase_v2'] = MATCHING_METHOD

    @model_validator(mode='after')
    def validate_support(self) -> 'ComponentMatch':
        if len(self.matched_evidence_ids) != len(set(self.matched_evidence_ids)):
            raise ValueError('Component evidence references must be unique.')
        if (self.status == 'supported') != bool(self.matched_evidence_ids):
            raise ValueError('Component status must agree with its evidence references.')
        return self


class RequirementMatch(_StrictMatchModel):
    '''One source requirement with individually traceable explicit components.'''

    requirement_id: Annotated[str, Field(pattern=r'^[0-9a-f]{64}$')]
    category: RequirementCategory
    source_index: Annotated[int, Field(ge=0)]
    requirement_text: MeaningfulText
    source_quotation: MeaningfulText
    classification: Literal['required', 'preferred', 'uncertain'] | None
    classification_status: Literal['ai_generated_unverified']
    classification_context: MeaningfulText | None = None
    language_level: MeaningfulText | None = None
    status: MatchStatus
    matched_evidence_ids: list[MeaningfulText]
    matched_evidence: list[MatchedEvidenceReference]
    components: Annotated[list[ComponentMatch], Field(min_length=1)]
    unmatched_components: list[MeaningfulText]
    matching_method: Literal['exact_normalized_phrase_v2'] = MATCHING_METHOD

    @model_validator(mode='after')
    def validate_requirement(self) -> 'RequirementMatch':
        if self.requirement_text not in self.source_quotation:
            raise ValueError('Requirement wording must occur in its original quotation.')
        skill_classifications = {
            'required_skills': 'required', 'preferred_skills': 'preferred', 'uncertain_skills': 'uncertain',
        }
        if self.classification != skill_classifications.get(self.category):
            raise ValueError('Classification must preserve the source requirement category.')
        if self.category in skill_classifications:
            if self.classification_context is None or self.source_quotation not in self.classification_context:
                raise ValueError('Skill classification context must retain the original quotation.')
        elif self.classification_context is not None:
            raise ValueError('Unclassified requirement categories cannot invent a skill context.')

        component_keys = [(component.kind, component.text) for component in self.components]
        if len(component_keys) != len(set(component_keys)):
            raise ValueError('Requirement components must be unique.')
        if self.category == 'languages':
            expected = [('language', self.requirement_text)]
            if self.language_level is not None:
                if self.language_level not in self.source_quotation:
                    raise ValueError('Language level must occur in its original quotation.')
                expected.append(('language_level', self.language_level))
            if component_keys != expected:
                raise ValueError('Language components must preserve the language and any requested level.')
            if self.language_level is not None and not set(self.components[1].matched_evidence_ids).issubset(
                self.components[0].matched_evidence_ids,
            ):
                raise ValueError('Language and proficiency level must reference the same evidence.')
        elif self.language_level is not None or any(component.kind != 'phrase' for component in self.components):
            raise ValueError('Only language requirements can contain language-level components.')

        evidence_ids = list(dict.fromkeys(
            evidence_id for component in self.components for evidence_id in component.matched_evidence_ids
        ))
        if self.matched_evidence_ids != evidence_ids:
            raise ValueError('Requirement evidence IDs must be the unique union of component references.')
        if [reference.evidence_id for reference in self.matched_evidence] != evidence_ids:
            raise ValueError('Evidence provenance must correspond exactly to the referenced IDs.')
        missing = [component.text for component in self.components if component.status != 'supported']
        if self.unmatched_components != missing:
            raise ValueError('Unmatched components must agree with the component assessments.')
        if not missing:
            expected_status = 'supported'
        elif evidence_ids:
            expected_status = 'partially_supported'
        else:
            negative_statuses = {component.status for component in self.components}
            if len(negative_statuses) != 1:
                raise ValueError('Unmatched components must agree on negative assessability.')
            expected_status = self.components[0].status
        if self.status != expected_status:
            raise ValueError('Requirement status must agree with its explicit components.')
        return self


class CandidateJobMatch(_StrictMatchModel):
    '''Schema 2.0.0; input artifacts and their original schemas stay unchanged.'''

    schema_version: Literal['2.0.0']
    source_job_schema_version: Literal['2.0.0']
    source_job_prompt_version: Literal['2.0.0']
    source_job_grounding_method: Literal['exact_substrings_only']
    source_job_interpretation_status: Literal['ai_generated_unverified']
    candidate_evidence_schema_version: Literal['3.0.0']
    candidate_source_sha256: str | None
    candidate_evidence_record_count: Annotated[int, Field(ge=0)]
    coverage_status: CoverageStatus
    coverage_warnings: list[CoverageWarning]
    requirements: list[RequirementMatch]
    limitations: Annotated[list[MeaningfulText], Field(min_length=1)]
    human_review_required: Literal[True] = True

    @model_validator(mode='after')
    def validate_integrity(self) -> 'CandidateJobMatch':
        identifiers = [requirement.requirement_id for requirement in self.requirements]
        source_items = [(requirement.category, requirement.source_index) for requirement in self.requirements]
        if len(identifiers) != len(set(identifiers)) or len(source_items) != len(set(source_items)):
            raise ValueError('Requirement identifiers and source-item references must be unique.')
        if len(self.coverage_warnings) != len(set(self.coverage_warnings)):
            raise ValueError('Coverage warnings must be unique.')
        unassessable = {'empty_evidence_catalog', 'coverage_not_assessed', 'unresolved_extraction_details_present'}
        expected_coverage = (
            'not_assessable' if unassessable.intersection(self.coverage_warnings)
            else 'incomplete' if self.coverage_warnings else 'assessed'
        )
        if self.coverage_status != expected_coverage:
            raise ValueError('Coverage status must agree with the coverage warnings.')
        if (self.candidate_evidence_record_count == 0) != ('empty_evidence_catalog' in self.coverage_warnings):
            raise ValueError('Empty-catalog warnings must agree with the actual record count.')
        negative_status = 'no_evidence_found' if self.coverage_status == 'assessed' else 'not_assessable'
        for requirement in self.requirements:
            for component in requirement.components:
                if component.status != 'supported' and component.status != negative_status:
                    raise ValueError('Negative component results must respect candidate coverage.')
        return self
