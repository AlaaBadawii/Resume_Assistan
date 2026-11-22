'''Strict source-phrase contract for JD analysis, separate from extraction.

Values must be copied from their own quotations. The analyzer additionally
checks quotations against the source. These are textual invariants, not
semantic entailment: field assignments and skill classifications remain
explicitly AI-generated/unverified in every artifact.
'''

from typing import Annotated, Iterator, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, field_validator, model_validator
from pydantic_core import PydanticCustomError

from resume_assistant.prompts import JOB_ANALYSIS_PROMPT_VERSION

JOB_ANALYSIS_SCHEMA_VERSION = '2.0.0'
SCALAR_FIELDS = ('role_title', 'company_name', 'seniority_level', 'employment_type', 'work_arrangement')
SKILL_FIELDS = ('required_skills', 'preferred_skills', 'uncertain_skills')
CITED_LIST_FIELDS = (
    'responsibilities', *SKILL_FIELDS, 'education_requirements',
    'experience_requirements', 'languages', 'certifications', 'ambiguities',
)
MissingScalarField = Literal[
    'role_title', 'company_name', 'seniority_level', 'employment_type', 'work_arrangement',
]


def _meaningful_text(value: str) -> str:
    if not value.strip() or not any(char.isalnum() for char in value):
        raise ValueError('Text must contain meaningful non-whitespace characters.')
    return value  # neither values nor quotations are trimmed or rewritten


MeaningfulText = Annotated[str, Field(min_length=1), AfterValidator(_meaningful_text)]


def _require_own_evidence(value: str, quotation: str) -> None:
    if value not in quotation:
        raise PydanticCustomError(
            'claim_evidence_mismatch', 'A source phrase must be an exact substring of its own quotation.',
        )


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True, frozen=True)


class CitedValue(_StrictModel):
    '''An original scalar phrase with its own original source quotation.'''

    value: MeaningfulText
    quotation: MeaningfulText

    @model_validator(mode='after')
    def validate_evidence(self) -> 'CitedValue':
        _require_own_evidence(self.value, self.quotation)
        return self


class CitedItem(_StrictModel):
    '''A copied responsibility, requirement, or ambiguous phrase, not a paraphrase.'''

    text: MeaningfulText
    quotation: MeaningfulText

    @model_validator(mode='after')
    def validate_evidence(self) -> 'CitedItem':
        _require_own_evidence(self.text, self.quotation)
        return self


class CertificationItem(_StrictModel):
    name: MeaningfulText
    quotation: MeaningfulText

    @model_validator(mode='after')
    def validate_evidence(self) -> 'CertificationItem':
        _require_own_evidence(self.name, self.quotation)
        return self


class SkillItem(CertificationItem):
    '''A copied skill phrase with exact context for an unverified AI classification.'''

    classification_context: MeaningfulText
    classification_status: Literal['ai_generated_unverified'] = 'ai_generated_unverified'

    @model_validator(mode='after')
    def validate_classification_context(self) -> 'SkillItem':
        if self.quotation not in self.classification_context:
            raise PydanticCustomError(
                'claim_evidence_mismatch', 'Classification context must contain the associated quotation verbatim.',
            )
        return self


class LanguageItem(_StrictModel):
    language: MeaningfulText
    level: MeaningfulText | None
    quotation: MeaningfulText

    @model_validator(mode='after')
    def validate_evidence(self) -> 'LanguageItem':
        _require_own_evidence(self.language, self.quotation)
        if self.level is not None:
            _require_own_evidence(self.level, self.quotation)
        return self


class JobDescriptionPayload(_StrictModel):
    '''LLM output contract: every key is required; unknown scalars are explicit nulls.

    missing_information is the set of scalar fields not extracted (null),
    not a claim that those facts are absent from the source document.
    '''

    role_title: CitedValue | None
    company_name: CitedValue | None
    seniority_level: CitedValue | None
    employment_type: CitedValue | None
    work_arrangement: CitedValue | None
    responsibilities: list[CitedItem]
    required_skills: list[SkillItem]
    preferred_skills: list[SkillItem]
    uncertain_skills: list[SkillItem]
    education_requirements: list[CitedItem]
    experience_requirements: list[CitedItem]
    languages: list[LanguageItem]
    certifications: list[CertificationItem]
    ambiguities: list[CitedItem]
    missing_information: list[MissingScalarField]

    @field_validator(*CITED_LIST_FIELDS)
    @classmethod
    def deduplicate_identical_items(cls, items):
        # Compare all validated fields exactly, including each quotation/context.
        # Distinct claims supported by one quotation are deliberately retained.
        unique, seen = [], set()
        for item in items:
            identity = item.model_dump_json()
            if identity not in seen:
                seen.add(identity)
                unique.append(item)
        return unique

    @field_validator('missing_information')
    @classmethod
    def deduplicate_missing_fields(cls, fields):
        return list(dict.fromkeys(fields))

    @model_validator(mode='after')
    def validate_classifications_and_unknowns(self) -> 'JobDescriptionPayload':
        classifications = {}
        for field in SKILL_FIELDS:
            for item in getattr(self, field):
                comparison_name = item.name.strip().casefold()
                previous = classifications.get(comparison_name)
                if previous is not None and previous != field:
                    raise PydanticCustomError(
                        'skill_classification_conflict',
                        'A skill has conflicting classifications; represent uncertainty in uncertain_skills.',
                    )
                classifications[comparison_name] = field
        unknown = {field for field in SCALAR_FIELDS if getattr(self, field) is None}
        if set(self.missing_information) != unknown:
            raise PydanticCustomError(
                'unknown_scalar_inconsistent', 'Missing-information fields must match the unextracted null scalars.',
            )
        return self

    def quoted_items(self) -> Iterator[tuple[str, str]]:
        '''Yield content-free field locations and all source quotations/contexts.'''
        for field in SCALAR_FIELDS:
            item = getattr(self, field)
            if item is not None:
                yield f'{field}.quotation', item.quotation
        for field in CITED_LIST_FIELDS:
            for index, item in enumerate(getattr(self, field)):
                yield f'{field}[{index}]', item.quotation
                if field in SKILL_FIELDS:
                    yield f'{field}[{index}].classification_context', item.classification_context


class JobDescriptionAnalysis(JobDescriptionPayload):
    '''Application artifact with canonical metadata, never LLM-supplied metadata.'''

    schema_version: Literal['2.0.0'] = JOB_ANALYSIS_SCHEMA_VERSION
    prompt_version: str
    grounding_method: Literal['exact_substrings_only'] = 'exact_substrings_only'
    interpretation_status: Literal['ai_generated_unverified'] = 'ai_generated_unverified'

    @field_validator('prompt_version')
    @classmethod
    def require_current_prompt_version(cls, value):
        # Prompt revisions stay in their versioned module, not a second literal
        # embedded in the business schema. Raw LLM payloads cannot set this field.
        if value != JOB_ANALYSIS_PROMPT_VERSION:
            raise ValueError('Artifact prompt version must match the application prompt version.')
        return value
