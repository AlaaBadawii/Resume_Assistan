'''Evidence-bound cover-letter generation with one bounded repair retry.'''

from __future__ import annotations

import json
import re
from typing import Iterable, Optional

from pydantic import ValidationError

from resume_assistant.cover_letter_models import (
    COVER_LETTER_SCHEMA_VERSION,
    CoverLetterArtifact,
    CoverLetterParagraph,
    CoverLetterPayload,
)
from resume_assistant.generation_models import (
    ClaimValidationFinding,
    DeterministicHeader,
    GroundingValidationReport,
)
from resume_assistant.llm_client import AgentRouterClient
from resume_assistant.models import (
    CandidateEvidenceCatalog,
    DocumentSnapshot,
    EvidenceType,
)
from resume_assistant.prompts import (
    COVER_LETTER_GENERATION_INSTRUCTIONS,
    COVER_LETTER_PROMPT_VERSION,
)
from resume_assistant.publication import PublicationManifest
from resume_assistant.resume_generator import (
    HydratedGenerationInput,
    ResumeGenerationError,
    _FENCED_BLOCK_PATTERN,
    _numeric_tokens,
    _pii_spans,
    _reject_json_constant,
    _sanitize_repair_details,
    _schema_repair_details,
    _scrub_pii,
    _unique_json_object,
    hydrate_plan_evidence,
)
from resume_assistant.section_plan import ResumeSectionPlan

_WORD_PATTERN = re.compile(r"[A-Za-z0-9]+(?:['-][A-Za-z0-9]+)*")
_TARGET_WORDS = (220, 320)


class CoverLetterGenerationError(ValueError):
    '''Controlled, content-free cover-letter generation error.'''

    def __init__(
        self, message: str, *, repair_details: Optional[list[dict[str, str]]] = None
    ) -> None:
        super().__init__(message)
        self.repair_details = repair_details or []


def parse_cover_letter_payload(text: str) -> CoverLetterPayload:
    '''Extract one optional JSON fence and strictly parse one payload object.'''
    if not isinstance(text, str):
        raise CoverLetterGenerationError(
            'Provider output must be JSON text.',
            repair_details=[{'code': 'invalid_json', 'location': 'payload'}],
        )
    cleaned = text.strip()
    fence = _FENCED_BLOCK_PATTERN.fullmatch(cleaned)
    if fence:
        language, body = fence.groups()
        if language.strip().casefold() not in ('', 'json'):
            raise CoverLetterGenerationError(
                'Provider output fence must contain JSON.',
                repair_details=[{'code': 'invalid_json_fence', 'location': 'payload'}],
            )
        cleaned = body.strip()
    try:
        document = json.loads(
            cleaned,
            object_pairs_hook=_unique_json_object,
            parse_constant=_reject_json_constant,
        )
    except (ValueError, RecursionError):
        raise CoverLetterGenerationError(
            'Provider output must be valid, unambiguous JSON.',
            repair_details=[{'code': 'invalid_json', 'location': 'payload'}],
        ) from None
    if not isinstance(document, dict):
        raise CoverLetterGenerationError(
            'Provider output must be a JSON object.',
            repair_details=[{'code': 'invalid_root', 'location': 'payload'}],
        )
    try:
        return CoverLetterPayload.model_validate(document)
    except ValidationError as error:
        raise CoverLetterGenerationError(
            'Provider output does not match the required cover-letter schema.',
            repair_details=_schema_repair_details(error),
        ) from None


def _resolve_cover_letter_aliases(
    payload: CoverLetterPayload,
    id_by_alias: dict[str, str],
) -> CoverLetterPayload:
    data = payload.model_dump(mode='python')
    try:
        for paragraph in (data['opening_paragraph'], *data['body_paragraphs'], data['closing_paragraph']):
            paragraph['source_evidence_ids'] = [id_by_alias[alias] for alias in paragraph['source_evidence_ids']]
    except KeyError:
        raise CoverLetterGenerationError(
            'A paragraph must cite only aliases supplied for this letter.',
            repair_details=[{'code': 'unknown_evidence_alias', 'location': 'source_evidence_ids'}],
        ) from None
    return CoverLetterPayload.model_validate(data)


def _contains_name(text: str, full_name: str) -> bool:
    name = ' '.join(full_name.partition(',')[0].split())
    if not name:
        return False
    if _contains_phrase(text, name):
        return True
    for token in name.split():
        if len(token) >= 3 and re.search(
            r'(?<!\w)' + re.escape(token) + r'(?!\w)', text or ''
        ):
            return True
    return False


def _contains_phrase(text: str, phrase: str) -> bool:
    words = phrase.split()
    return bool(words and re.search(r'(?<!\w)' + r'\s+'.join(map(re.escape, words)) + r'(?!\w)', text, re.I))


def _has_contact(text: str, header: DeterministicHeader) -> bool:
    return bool(_pii_spans(text)) or any(_contains_phrase(text, line) for line in header.contact_lines)


def _public_text(text: str | None, header: DeterministicHeader) -> str | None:
    if not text or _contains_name(text, header.full_name) or _has_contact(text, header):
        return None
    return text


def _letter_input(hydrated: HydratedGenerationInput) -> tuple[str, dict[str, str]]:
    '''Project the verified, scrubbed plan onto just the evidence this letter can cite.'''
    bundle = json.loads(hydrated.llm_input_text)
    sections = []
    visible = set()
    for section in bundle['ordered_sections']:
        items = [*section['evidence'], *(ev for entry in section['entries'] for ev in entry['evidence'])]
        evidence = [item for item in items if _public_text(item['original_content'], hydrated.header)]
        if evidence:
            sections.append({'section_id': section['section_id'], 'evidence': evidence})
            visible.update(item['evidence_id'] for item in evidence)
    if not visible:
        raise CoverLetterGenerationError('The plan contains no shareable selected evidence for a letter.')
    skills = []
    for skill in bundle['prioritized_skills']:
        cited = [alias for alias in skill['matched_evidence_ids'] if alias in visible]
        if cited and _public_text(skill['skill_text'], hydrated.header):
            skills.append({**skill, 'matched_evidence_ids': cited})
    letter = {
        'target_job_context': {
            key: _public_text(bundle['target_job_context'][key], hydrated.header)
            for key in ('role_title', 'company_name')
        },
        'ordered_sections': sections,
        'prioritized_skills': skills,
    }
    return json.dumps(letter, ensure_ascii=False), {
        alias: identifier for identifier, alias in hydrated.evidence_alias_by_id.items() if alias in visible
    }


def _word_count(payload: CoverLetterPayload) -> int:
    paragraphs = [
        payload.opening_paragraph.text,
        *(paragraph.text for paragraph in payload.body_paragraphs),
        payload.closing_paragraph.text,
    ]
    return sum(len(_WORD_PATTERN.findall(text)) for text in paragraphs)


def cover_letter_word_count(artifact: CoverLetterArtifact) -> int:
    '''Count words in the generated paragraphs, excluding header and signature.'''
    return _word_count(artifact.content)


def validate_cover_letter(
    payload: CoverLetterPayload,
    plan: ResumeSectionPlan,
    catalog: CandidateEvidenceCatalog,
    header: DeterministicHeader,
    *,
    raise_on_error: bool = True,
) -> GroundingValidationReport:
    '''Deterministically check evidence scope, numeric grounding, and privacy.'''
    records = {record.evidence_id: record for record in catalog.evidence_records}
    selected = set(plan.selected_evidence_ids)
    findings: list[ClaimValidationFinding] = []

    def add(
        severity: str,
        code: str,
        location: str,
        message: str,
        evidence_ids: Iterable[str] = (),
    ) -> None:
        findings.append(ClaimValidationFinding(
            severity=severity,
            code=code,
            location=location,
            message=message,
            evidence_ids=list(dict.fromkeys(evidence_ids)),
        ))

    def check_paragraph(paragraph: CoverLetterParagraph, location: str) -> None:
        grounded = set()
        for identifier in paragraph.source_evidence_ids:
            if identifier not in selected:
                add(
                    'error',
                    'evidence_not_selected',
                    location,
                    'Cited evidence is not part of the selected plan evidence.',
                    (identifier,),
                )
                continue
            record = records.get(identifier)
            if record is None:
                add(
                    'error',
                    'evidence_integrity',
                    location,
                    'Cited evidence is not present in the candidate catalog.',
                    (identifier,),
                )
                continue
            if record.evidence_type in (EvidenceType.IDENTITY, EvidenceType.CONTACT):
                add(
                    'error',
                    'identity_evidence',
                    location,
                    'Cover-letter claims cannot cite identity or contact records.',
                    (identifier,),
                )
            else:
                grounded.update(_numeric_tokens(_scrub_pii(record.original_content)))
        if _has_contact(paragraph.text, header):
            add(
                'error',
                'contact_pii',
                location,
                'Generated text must not contain contact details.',
            )
        if _contains_name(paragraph.text, header.full_name):
            add(
                'error',
                'candidate_name',
                location,
                'Generated text must not contain the candidate name.',
            )
        if _numeric_tokens(paragraph.text) - grounded:
            add(
                'error',
                'ungrounded_metric',
                location,
                'Generated text contains a numeric claim that is not present in the cited evidence.',
            )

    check_paragraph(payload.opening_paragraph, 'opening_paragraph')
    for index, paragraph in enumerate(payload.body_paragraphs):
        check_paragraph(paragraph, f'body_paragraphs[{index}]')
    check_paragraph(payload.closing_paragraph, 'closing_paragraph')

    if _has_contact(payload.salutation, header):
        add('error', 'contact_pii', 'salutation', 'Generated text must not contain contact details.')
    if _contains_name(payload.salutation, header.full_name):
        add('error', 'candidate_name', 'salutation', 'Generated text must not contain the candidate name.')
    company = _public_text(plan.target_job_context.company_name, header)
    expected = f'Dear {company.strip()} Hiring Team,' if company else 'Dear Hiring Team,'
    if payload.salutation.strip() != expected:
        add(
            'error',
            'salutation_company_mismatch',
            'salutation',
            'Use the hiring-team salutation for the supplied company, or the generic greeting if absent.',
        )

    if _has_contact(payload.sign_off, header):
        add('error', 'contact_pii', 'sign_off', 'Generated text must not contain contact details.')
    if _contains_name(payload.sign_off, header.full_name):
        add('error', 'candidate_name', 'sign_off', 'Generated text must not contain the candidate name.')
    if payload.sign_off.strip() not in ('Sincerely,', 'Best regards,'):
        add(
            'error',
            'sign_off_format',
            'sign_off',
            'Use Sincerely, or Best regards, without any other text.',
        )

    count = _word_count(payload)
    if count < _TARGET_WORDS[0] or count > _TARGET_WORDS[1]:
        add(
            'warning',
            'cover_letter_length',
            'paragraphs',
            f'Cover letter has {count} words; target is {_TARGET_WORDS[0]} to {_TARGET_WORDS[1]}.',
        )

    traceability_verified = not any(finding.severity == 'error' for finding in findings)
    report = GroundingValidationReport(
        traceability_verified=traceability_verified,
        findings=findings,
    )
    if not traceability_verified and raise_on_error:
        raise CoverLetterGenerationError(
            'Generated cover letter failed evidence traceability validation.'
        )
    return report


def _repair_input(original_input: str, details: Iterable[dict[str, str]]) -> str:
    feedback = json.dumps(
        {
            'errors': _sanitize_repair_details(details),
            'repair_guidance': (
                'Return the complete cover-letter JSON object again. Cite ONLY selected '
                'E... aliases from ordered_sections, '
                'remove unsupported numbers and contact details, keep the grounded '
                'salutation and sign-off format, and do not include the candidate name.'
            ),
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(',', ':'),
    )
    return (
        original_input
        + '\n\nAPPLICATION_REPAIR_FEEDBACK (trusted; not source data):\n'
        + feedback
    )


class CoverLetterGenerator:
    '''One provider call plus at most one bounded repair retry.'''

    def __init__(self, client: AgentRouterClient) -> None:
        self._client = client

    def _attempt(
        self,
        input_text: str,
        plan: ResumeSectionPlan,
        catalog: CandidateEvidenceCatalog,
        header: DeterministicHeader,
        id_by_alias: dict[str, str],
    ) -> tuple[CoverLetterPayload, GroundingValidationReport]:
        output_text = self._client.generate(
            instructions=COVER_LETTER_GENERATION_INSTRUCTIONS,
            input_text=input_text,
        )
        payload = parse_cover_letter_payload(output_text)
        payload = _resolve_cover_letter_aliases(payload, id_by_alias)
        report = validate_cover_letter(payload, plan, catalog, header, raise_on_error=False)
        if not report.traceability_verified:
            raise CoverLetterGenerationError(
                'Generated cover letter requires repair.',
                repair_details=[{'code': f.code, 'location': f.location, 'message': f.message}
                                for f in report.findings if f.severity == 'error'],
            )
        return payload, report

    def generate(
        self,
        plan: ResumeSectionPlan,
        catalog: CandidateEvidenceCatalog,
        snapshot: Optional[DocumentSnapshot] = None,
        manifest: Optional[PublicationManifest] = None,
    ) -> CoverLetterArtifact:
        try:
            hydrated = hydrate_plan_evidence(plan, catalog, snapshot, manifest)
        except ResumeGenerationError as error:
            raise CoverLetterGenerationError(str(error)) from None
        input_text, id_by_alias = _letter_input(hydrated)
        for attempt in range(2):
            try:
                payload, report = self._attempt(
                    input_text, plan, catalog, hydrated.header, id_by_alias,
                )
                break
            except CoverLetterGenerationError as error:
                if attempt:
                    raise CoverLetterGenerationError(
                        'Provider output failed validation after one repair retry.'
                    ) from None
                input_text = _repair_input(input_text, error.repair_details)
        return CoverLetterArtifact(
            schema_version=COVER_LETTER_SCHEMA_VERSION,
            prompt_version=COVER_LETTER_PROMPT_VERSION,
            plan_schema_version=plan.schema_version,
            model=self._client.model,
            header=hydrated.header,
            content=payload,
            validation=report,
        )


def generate_cover_letter(
    plan: ResumeSectionPlan,
    catalog: CandidateEvidenceCatalog,
    snapshot: Optional[DocumentSnapshot],
    manifest: Optional[PublicationManifest],
    client: AgentRouterClient,
) -> CoverLetterArtifact:
    '''Generate a validated cover letter from a local planning handoff.'''
    return CoverLetterGenerator(client).generate(plan, catalog, snapshot, manifest)
