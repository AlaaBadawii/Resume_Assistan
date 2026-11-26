'''Strict schema-1.0.0 tailored-resume generation contract.

These models describe the LLM-returned payload, the deterministic grounding
validation report, and the published artifact. They never carry contact PII
from the candidate catalog; the deterministic header is populated locally.
'''

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

RESUME_GENERATION_SCHEMA_VERSION = '1.0.0'


class StrictGenerationModel(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
        strict=True,
        frozen=True,
        revalidate_instances='always',
        validate_default=True,
    )


def _nonblank(value: str) -> str:
    if not value.strip():
        raise ValueError('Generated text fields must not be blank.')
    return value


def _unique(values: list[str], message: str) -> list[str]:
    if len(values) != len(set(values)):
        raise ValueError(message)
    return values


class GroundedClaim(StrictGenerationModel):
    '''One generated sentence or bullet bound to source evidence.'''

    text: str = Field(min_length=1)
    evidence_ids: list[str] = Field(min_length=1)

    _validate_text = field_validator('text')(_nonblank)

    @field_validator('evidence_ids')
    @classmethod
    def validate_evidence_ids(cls, values: list[str]) -> list[str]:
        return _unique(values, 'Claim evidence IDs must be unique.')


class GeneratedSkillItem(StrictGenerationModel):
    skill_text: str = Field(min_length=1)
    evidence_ids: list[str] = Field(min_length=1)

    _validate_text = field_validator('skill_text')(_nonblank)

    @field_validator('evidence_ids')
    @classmethod
    def validate_evidence_ids(cls, values: list[str]) -> list[str]:
        return _unique(values, 'Skill evidence IDs must be unique.')


class GeneratedSkillGroup(StrictGenerationModel):
    group_name: str = Field(min_length=1)
    skills: list[GeneratedSkillItem] = Field(default_factory=list)

    _validate_name = field_validator('group_name')(_nonblank)


class GeneratedEntry(StrictGenerationModel):
    entry_id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    meta: str | None = None
    bullets: list[GroundedClaim] = Field(default_factory=list)

    _validate_entry_id = field_validator('entry_id')(_nonblank)
    _validate_title = field_validator('title')(_nonblank)


class GeneratedSection(StrictGenerationModel):
    section_id: str = Field(min_length=1)
    heading_text: str = Field(min_length=1)
    entries: list[GeneratedEntry] = Field(default_factory=list)

    _validate_section_id = field_validator('section_id')(_nonblank)
    _validate_heading = field_validator('heading_text')(_nonblank)


class TailoredResumePayload(StrictGenerationModel):
    '''The complete JSON object returned by the generation model.'''

    headline_title: str = Field(min_length=1)
    tagline: str | None = None
    summary_sentences: list[GroundedClaim] = Field(default_factory=list)
    skill_groups: list[GeneratedSkillGroup] = Field(default_factory=list)
    sections: list[GeneratedSection] = Field(default_factory=list)

    _validate_headline = field_validator('headline_title')(_nonblank)

    @model_validator(mode='after')
    def validate_identifiers(self) -> 'TailoredResumePayload':
        section_ids = [section.section_id for section in self.sections]
        _unique(section_ids, 'Generated section IDs must be unique.')
        entry_ids = [entry.entry_id for section in self.sections for entry in section.entries]
        _unique(entry_ids, 'Generated entry IDs must be unique across sections.')
        return self


class ClaimValidationFinding(StrictGenerationModel):
    severity: Literal['error', 'warning']
    code: str = Field(min_length=1)
    location: str = Field(min_length=1)
    message: str = Field(min_length=1)
    evidence_ids: list[str] = Field(default_factory=list)


class GroundingValidationReport(StrictGenerationModel):
    '''Deterministic checks only; never claims full semantic entailment.'''

    traceability_verified: bool
    semantic_entailment_verified: Literal[False] = False
    human_review_required: Literal[True] = True
    findings: list[ClaimValidationFinding] = Field(default_factory=list)


class DeterministicHeader(StrictGenerationModel):
    '''Header populated locally from catalog identity/contact records.'''

    full_name: str = ''
    contact_lines: list[str] = Field(default_factory=list)
    source_evidence_ids: list[str] = Field(default_factory=list)


class TailoredResumeArtifact(StrictGenerationModel):
    schema_version: Literal['1.0.0'] = RESUME_GENERATION_SCHEMA_VERSION
    prompt_version: str = Field(min_length=1)
    plan_schema_version: Literal['2.0.0']
    model: str = Field(min_length=1)
    header: DeterministicHeader
    content: TailoredResumePayload
    validation: GroundingValidationReport
