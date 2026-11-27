'''Strict schema-1.0.0 cover-letter generation contract.

The provider returns only evidence-bound prose paragraphs. Identity, contact
details, and the candidate signature are injected locally during rendering so
private header data never reaches the model.
'''

from typing import Literal

from pydantic import Field, field_validator

from resume_assistant.generation_models import (
    DeterministicHeader,
    GroundingValidationReport,
    StrictGenerationModel,
)

COVER_LETTER_SCHEMA_VERSION = '1.0.0'


def _nonblank(value: str) -> str:
    if not value.strip():
        raise ValueError('Generated text fields must not be blank.')
    return value


def _single_line(value: str) -> str:
    if '\n' in value or '\r' in value:
        raise ValueError('Salutation and sign-off must be single lines.')
    return value


def _unique(values: list[str], message: str) -> list[str]:
    if len(values) != len(set(values)):
        raise ValueError(message)
    return values


class CoverLetterParagraph(StrictGenerationModel):
    '''One generated paragraph bound to selected candidate evidence.'''

    text: str = Field(min_length=1)
    source_evidence_ids: list[str] = Field(min_length=1)

    _validate_text = field_validator('text')(_nonblank)

    @field_validator('source_evidence_ids')
    @classmethod
    def validate_evidence_ids(cls, values: list[str]) -> list[str]:
        return _unique(values, 'Paragraph evidence IDs must be unique.')


class CoverLetterPayload(StrictGenerationModel):
    '''The complete JSON object returned by the cover-letter model.'''

    salutation: str = Field(min_length=1)
    opening_paragraph: CoverLetterParagraph
    body_paragraphs: list[CoverLetterParagraph] = Field(min_length=1, max_length=2)
    closing_paragraph: CoverLetterParagraph
    sign_off: str = Field(min_length=1)

    _validate_salutation = field_validator('salutation')(_nonblank)
    _validate_sign_off = field_validator('sign_off')(_nonblank)

    @field_validator('salutation', 'sign_off')
    @classmethod
    def validate_single_line(cls, value: str) -> str:
        return _single_line(value)


class CoverLetterArtifact(StrictGenerationModel):
    schema_version: Literal['1.0.0'] = COVER_LETTER_SCHEMA_VERSION
    prompt_version: str = Field(min_length=1)
    plan_schema_version: Literal['2.0.0']
    model: str = Field(min_length=1)
    header: DeterministicHeader
    content: CoverLetterPayload
    validation: GroundingValidationReport
