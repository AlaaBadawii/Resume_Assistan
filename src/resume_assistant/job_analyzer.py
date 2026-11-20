'''Job Description Analyzer service (Mission 3.1).

Accepts job-description text, requests a structured analysis from an
injected LLM client, validates the response with Pydantic, and verifies
every supporting quotation is an exact substring of the original input.

This module deliberately does not import candidate-evidence, extraction,
or resume-generation code: job-description analysis must never receive
candidate data.
'''

import json
from typing import Any, Dict

from pydantic import ValidationError

from resume_assistant.job_models import JOB_ANALYSIS_SCHEMA_VERSION, JobDescriptionAnalysis, JobDescriptionPayload
from resume_assistant.prompts import JOB_ANALYSIS_INSTRUCTIONS, JOB_ANALYSIS_PROMPT_VERSION


class JobAnalysisError(Exception):
    '''Base class for sanitized analyzer failures.'''


class JobAnalysisInputError(JobAnalysisError):
    '''Controlled, content-free input validation error.'''


class JobAnalysisResponseError(JobAnalysisError):
    '''Structured response was invalid; contents are never included.'''


class JobAnalysisQuotationError(JobAnalysisError):
    '''A supporting quotation was not an exact substring of the source.'''


class JobAnalysisGroundingError(JobAnalysisError):
    '''A copied claim was not supported by its own quotation/context.'''


class JobAnalysisConflictError(JobAnalysisError):
    '''The model supplied contradictory skill classifications.'''


def _unique_json_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('Duplicate JSON key.')
        result[key] = value
    return result


def _reject_json_constant(value):
    raise ValueError('Non-standard JSON constant.')


def _load_json_object(response_text: str) -> Dict[str, Any]:
    text = response_text.strip()
    if text.startswith('```'):
        lines = text.splitlines()
        if len(lines) < 3 or lines[0] not in ('```', '```json') or lines[-1] != '```':
            raise JobAnalysisResponseError('Analysis response contained an invalid JSON fence.')
        text = '\n'.join(lines[1:-1]).strip()
    try:
        document = json.loads(text, object_pairs_hook=_unique_json_object, parse_constant=_reject_json_constant)
    except (ValueError, RecursionError):
        raise JobAnalysisResponseError('Analysis response was not valid JSON.') from None
    if not isinstance(document, dict):
        raise JobAnalysisResponseError('Analysis response was not a JSON object.')
    return document


class JobDescriptionAnalyzer:
    '''Converts job-description text into a validated JobDescriptionAnalysis.'''

    def __init__(self, client) -> None:
        self._client = client

    def analyze(self, job_description: str) -> JobDescriptionAnalysis:
        if not isinstance(job_description, str):
            raise JobAnalysisInputError('Job description input must be text.')
        if not job_description.strip():
            raise JobAnalysisInputError('Job description input must not be empty.')

        response_text = self._client.generate(
            instructions=JOB_ANALYSIS_INSTRUCTIONS,
            input_text=job_description,
        )
        if not isinstance(response_text, str) or not response_text.strip():
            raise JobAnalysisResponseError('Analysis response was empty.')

        payload = _load_json_object(response_text)
        try:
            extracted = JobDescriptionPayload.model_validate(payload)
        except ValidationError as error:
            # Inspect only error codes, never Pydantic input values/context.
            types = {item['type'] for item in error.errors(include_input=False, include_context=False, include_url=False)}
            if 'claim_evidence_mismatch' in types:
                raise JobAnalysisGroundingError(
                    'Analysis claim is not an exact substring of its own supporting quotation/context.'
                ) from None
            if 'skill_classification_conflict' in types:
                raise JobAnalysisConflictError(
                    'Analysis contains conflicting skill classifications; use uncertain_skills.'
                ) from None
            raise JobAnalysisResponseError(
                'Analysis response did not match the expected structure.'
            ) from None

        for location, quotation in extracted.quoted_items():
            if quotation not in job_description:
                raise JobAnalysisQuotationError(
                    f'Supporting quotation at {location} is not an exact substring '
                    'of the source job description.'
                )

        # Raw LLM output forbids application metadata. The artifact's literal
        # metadata cannot promote textual matches or AI classifications to facts.
        return JobDescriptionAnalysis(
            **extracted.model_dump(),
            schema_version=JOB_ANALYSIS_SCHEMA_VERSION,
            prompt_version=JOB_ANALYSIS_PROMPT_VERSION,
        )
