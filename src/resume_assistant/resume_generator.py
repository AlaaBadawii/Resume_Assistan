'''Phase 4 resume generation: evidence hydration, LLM call, and grounding checks.

The generator never invents a header and never forwards excluded evidence or
contact PII to the provider. Deterministic validation proves traceability of
every cited evidence ID and flags numeric claims that are absent from the
cited source text; it never claims full semantic entailment.
'''

import json
import re
from dataclasses import dataclass
from typing import Callable, Iterable, Mapping, Optional

from pydantic import ValidationError

from resume_assistant.evidence import CONTACT_PREFIXES
from resume_assistant.generation_models import (
    ClaimValidationFinding,
    DeterministicHeader,
    GroundingValidationReport,
    TailoredResumeArtifact,
    TailoredResumePayload,
    RESUME_GENERATION_SCHEMA_VERSION,
)
from resume_assistant.llm_client import AgentRouterClient
from resume_assistant.models import (
    CandidateEvidenceCatalog,
    DocumentSnapshot,
    EvidenceRecord,
    EvidenceType,
)
from resume_assistant.prompts import (
    RESUME_GENERATION_INSTRUCTIONS,
    RESUME_GENERATION_PROMPT_VERSION,
)
from resume_assistant.publication import PublicationManifest
from resume_assistant.section_plan import (
    ExtractionGenerationIdentity,
    ResumeSectionPlan,
    content_sha256,
    skill_identity,
)

_METRIC_PATTERN = re.compile(
    r'(?<![\w.])'
    r'(?P<currency>[$€£])?\s?'
    r'(?P<number>\d{1,3}(?:,\d{3})+(?:\.\d+)*|\d+(?:\.\d+)*)'
    r'\s?(?P<suffix>[KkMmBb%])?'
    r'(?![\w])(?!\.\d)'
)
_EMAIL_PATTERN = re.compile(
    r'(?<![\w.+-])[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}'
)
_URL_PATTERN = re.compile(
    r'(?i)(?:https?://|www\.)[^\s<>()\[\]{}]+'
    r'|(?<![@\w.-])(?:[a-z0-9-]+\.)+(?:com|io|dev|me|net|org|ai|co|app|'
    r'tech|site|info|ly)(?:/[^\s<>()\[\]{}]*)?'
)
_PHONE_PATTERN = re.compile(
    r'(?<![\w])(?:\+?\d{1,3}[\s.-]?)?(?:\(?\d{2,4}\)?[\s.-]?){2,4}\d{2,4}(?![\w])'
)
_DATE_RANGE_PATTERN = re.compile(r'\d{4}\s*[-–—]\s*\d{4}')
_FENCED_BLOCK_PATTERN = re.compile(
    r'```[ \t]*([^\r\n`]*)\r?\n(.*?)```', re.DOTALL
)
_CONTACT_LABEL_QUALIFIERS = frozenset({
    'address', 'contact', 'handle', 'id', 'link', 'mobile', 'number',
    'page', 'phone', 'profile', 'site', 'url',
})
_CONTACT_PREFIXES_BY_LENGTH = tuple(sorted(CONTACT_PREFIXES, key=len, reverse=True))
_PROFILE_LABELS = frozenset({
    'github', 'linkedin', 'portfolio', 'website', 'blog', 'gitlab', 'medium',
    'stack overflow', 'kaggle', 'hugging face', 'twitter', 'x', 'instagram',
    'facebook', 'youtube', 'scholar', 'google scholar', 'orcid', 'telegram',
    'whatsapp', 'discord', 'behance', 'dribbble',
})
_NAME_TITLE_WORDS = frozenset({
    'senior', 'junior', 'lead', 'principal', 'staff', 'engineer', 'engineering',
    'developer', 'development', 'manager', 'management', 'analyst', 'analysis',
    'scientist', 'science', 'researcher', 'research', 'consultant', 'consulting',
    'architect', 'architecture', 'designer', 'design', 'specialist', 'director',
    'officer', 'intern', 'internship', 'student', 'graduate', 'professional',
    'software', 'data', 'backend', 'frontend', 'fullstack', 'full-stack',
    'devops', 'cloud', 'security', 'product', 'project', 'program', 'technical',
    'technology', 'systems', 'system', 'administrator', 'technician',
    'technologist', 'programmer', 'coder', 'founder', 'co-founder', 'owner',
    'executive', 'strategist', 'coordinator', 'assistant', 'associate',
    'practitioner', 'expert', 'enthusiast', 'generalist', 'consultant',
    'certified', 'experienced', 'passionate', 'motivated', 'dedicated',
    'results-driven', 'results', 'driven', 'aspiring', 'seasoned',
    'accomplished', 'creative', 'innovative', 'strategic', 'dynamic', 'proven',
    'talented', 'skilled', 'versatile', 'detail-oriented', 'detail', 'oriented',
})
_NAME_ORG_WORDS = frozenset({
    'inc', 'llc', 'ltd', 'limited', 'corp', 'corporation', 'company', 'co',
    'group', 'holdings', 'technologies', 'technology', 'solutions', 'services',
    'consulting', 'labs', 'laboratory', 'university', 'college', 'institute',
    'foundation', 'organization', 'organisation', 'agency', 'studios',
    'partners', 'associates', 'plc', 'gmbh', 'pty',
})
_NAME_HEADING_WORDS = frozenset({
    'summary', 'profile', 'contact', 'experience', 'education', 'skills',
    'projects', 'certifications', 'languages', 'achievements', 'leadership',
    'objective', 'about', 'overview', 'resume', 'curriculum', 'vitae', 'cv',
    'identity', 'additional', 'independent', 'training',
})
_NAME_CREDENTIALS = frozenset({
    'phd', 'md', 'mba', 'msc', 'bsc', 'ma', 'ms', 'ba', 'bs', 'pmp', 'cpa',
    'pe', 'cfa', 'jd', 'rn', 'pa', 'dds', 'dvm', 'edd', 'psyd', 'meng',
    'beng', 'mph', 'mfa', 'cfp', 'cissp', 'aws', 'ccna', 'itil', 'six sigma',
    'jr', 'sr', 'ii', 'iii', 'iv',
})


class ResumeGenerationError(ValueError):
    '''Controlled, content-free resume generation error.'''

    def __init__(
        self, message: str, *, repair_details: Optional[list[dict[str, str]]] = None
    ) -> None:
        super().__init__(message)
        self.repair_details = repair_details or []


@dataclass(frozen=True)
class HydratedGenerationInput:
    '''Locally derived header plus the compact provider input text.'''

    header: DeterministicHeader
    evidence_by_id: dict[str, EvidenceRecord]
    llm_input_text: str
    evidence_alias_by_id: dict[str, str]
    entry_alias_by_id: dict[str, str]


def _unique_json_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('Duplicate JSON key.')
        result[key] = value
    return result


def _reject_json_constant(value):
    raise ValueError('Non-standard JSON constant.')


def _contact_prefix_for_label(label: str) -> Optional[str]:
    identity = ' '.join(label.strip().casefold().replace('_', ' ').split())
    for prefix in _CONTACT_PREFIXES_BY_LENGTH:
        if identity == prefix:
            return prefix
        if identity.startswith(prefix + ' '):
            remainder = identity[len(prefix) + 1:]
            if remainder and all(
                token in _CONTACT_LABEL_QUALIFIERS for token in remainder.split()
            ):
                return prefix
    return None


def _is_contact_label(label: str, *, bare: bool) -> bool:
    prefix = _contact_prefix_for_label(label)
    if prefix is None:
        return False
    identity = ' '.join(label.strip().casefold().split())
    if prefix == 'github' and bare and identity == 'github':
        return False
    return True


def _phone_spans(text: str) -> list[tuple[int, int]]:
    spans = []
    for match in _PHONE_PATTERN.finditer(text or ''):
        value = match.group(0).strip()
        if _DATE_RANGE_PATTERN.fullmatch(value):
            continue
        digits = re.sub(r'\D', '', value)
        if 7 <= len(digits) <= 15:
            spans.append((match.start(), match.end()))
    return spans


def _is_contact_profile_value(value: str) -> bool:
    normalized = value.strip().casefold().rstrip('.,;')
    if not normalized:
        return False
    if _EMAIL_PATTERN.fullmatch(normalized) or _URL_PATTERN.fullmatch(normalized):
        return True
    return bool(_PHONE_PATTERN.fullmatch(normalized) and _phone_spans(normalized))


def _mailto_address(url: str) -> Optional[str]:
    stripped = (url or '').strip()
    if not stripped.casefold().startswith('mailto:'):
        return None
    target = stripped[7:].split('?', 1)[0].strip()
    return target or None


def _normalized_url(url: str) -> str:
    mailto = _mailto_address(url)
    if mailto is not None:
        return mailto.casefold()
    value = (url or '').strip().casefold()
    value = re.sub(r'^[a-z][a-z0-9+.-]*://', '', value)
    if value.startswith('www.'):
        value = value[4:]
    return value.rstrip('/')


def _url_visible_in_text(url: str, text: str) -> bool:
    normalized_url = _normalized_url(url)
    if not normalized_url:
        return False
    normalized_text = (text or '').casefold()
    normalized_text = re.sub(r'mailto:', '', normalized_text)
    normalized_text = re.sub(r'[a-z][a-z0-9+.-]*://', '', normalized_text)
    normalized_text = normalized_text.replace('www.', '')
    return normalized_url in normalized_text


def _pii_spans(text: str) -> list[tuple[int, int, str]]:
    spans: list[tuple[int, int, str]] = []
    for kind, pattern in (('email', _EMAIL_PATTERN), ('url', _URL_PATTERN)):
        for match in pattern.finditer(text or ''):
            spans.append((match.start(), match.end(), kind))
    for start, end in _phone_spans(text):
        if not any(existing_start < end and start < existing_end for existing_start, existing_end, _ in spans):
            spans.append((start, end, 'phone'))
    spans.sort()
    return spans


def _is_bare_contact_line(text: str) -> bool:
    identity = ' '.join((text or '').strip().casefold().strip(' ,;').split())
    if not identity:
        return False
    parts = [
        part.strip()
        for part in re.split(r'[|•·–—,;]+', identity)
        if part.strip()
    ]
    return bool(parts) and all(
        _is_contact_label(part, bare=False)
        and not (
            part.casefold() in _PROFILE_LABELS
            and not _is_contact_profile_value(part)
        )
        for part in parts
    )


def _scrub_pii(text: str) -> str:
    labels = '|'.join(
        re.escape(prefix) for prefix in _CONTACT_PREFIXES_BY_LENGTH
    )
    qualifiers = '|'.join(
        re.escape(qualifier) for qualifier in sorted(_CONTACT_LABEL_QUALIFIERS)
    )
    labeled_contact = re.compile(
        rf'(?i)(?:^|(?<=[\s|,;•·–—-]))(?:{labels})'
        rf'(?:\s+(?:{qualifiers}))?\s*[:|]\s*[^|;•·–—\n]*'
    )
    lines = []
    for raw_line in (text or '').splitlines():
        line = raw_line
        for start, end, _ in reversed(_pii_spans(line)):
            line = line[:start] + ' ' + line[end:]
        line = labeled_contact.sub(' ', line)
        line = ' '.join(line.split()).strip(' |,;•·–—-')
        if _is_bare_contact_line(line):
            line = ''
        if line:
            lines.append(line)
    return '\n'.join(lines)


def _is_pii_only(text: str, hyperlinks: Iterable[object] = ()) -> bool:
    lines = [line.strip() for line in (text or '').splitlines() if line.strip()]
    if not lines:
        return bool(list(hyperlinks))
    if _scrub_pii(text).strip():
        return False
    return bool(lines or list(hyperlinks))


def _record_is_pii_only(record: EvidenceRecord) -> bool:
    if record.evidence_type in (EvidenceType.IDENTITY, EvidenceType.CONTACT):
        return True
    if _is_pii_only(record.original_content, record.hyperlinks):
        return True
    return _is_contact_only_text(record.original_content)


def _name_from_line(line: str) -> str:
    return _scrub_pii(line)


def _name_display_text(text: str) -> str:
    parts = [part.strip() for part in (text or '').split(',')]
    if not parts:
        return text
    kept = [parts[0]]
    for part in parts[1:]:
        if not part:
            continue
        normalized = part.casefold().replace('.', '').replace(' ', '')
        if normalized in _NAME_CREDENTIALS:
            kept.append(part)
    return ', '.join(kept)


def _is_title_phrase(text: str) -> bool:
    words = [
        word.casefold().strip('.,;:')
        for word in (text or '').split()
        if word.strip('.,;:')
    ]
    return bool(words) and any(
        word in _NAME_TITLE_WORDS or word in _NAME_HEADING_WORDS
        for word in words
    )


def _looks_like_name(text: str, *, allow_mononym: bool = True) -> bool:
    '''Conservative deterministic name heuristic for the identity fallback.'''
    text = (text or '').strip().strip(' ,;')
    if not text or len(text) > 80:
        return False
    if any(character.isdigit() for character in text):
        return False
    if any(token in text for token in ('@', ':', '/', '\\', '|', '•', '·', '_')):
        return False
    if 'http' in text.casefold() or 'www.' in text.casefold():
        return False
    if _pii_spans(text):
        return False

    parts = [part.strip() for part in text.split(',')]
    name_part = parts[0]
    if not name_part:
        return False
    for credential in parts[1:]:
        if not credential:
            continue
        normalized = credential.casefold().replace('.', '').replace(' ', '')
        if normalized in _NAME_CREDENTIALS:
            continue
        if _is_title_phrase(credential):
            continue
        return False

    if name_part.endswith(('.', '!', '?', ';')):
        stripped = name_part.rstrip('.')
        last_word = stripped.split()[-1].casefold().strip('.') if stripped.split() else ''
        if not name_part.endswith('.') or last_word not in _NAME_CREDENTIALS:
            return False
        name_part = stripped

    words = name_part.split()
    if not 1 <= len(words) <= 6:
        return False
    if len(words) == 1 and not allow_mononym:
        return False
    normalized_words = [word.casefold().strip('.') for word in words]
    if any(
        word in _NAME_TITLE_WORDS
        or word in _NAME_HEADING_WORDS
        or word in _PROFILE_LABELS
        for word in normalized_words
    ):
        return False
    if any(word in _NAME_ORG_WORDS for word in normalized_words):
        return False
    return all(
        re.fullmatch(r"[^\W\d_][\w.'’\-]*", word, re.UNICODE) for word in words
    )


def _header_relevant_record(record: EvidenceRecord) -> bool:
    return (
        record.section_reference in (None, 'professional-identity')
        or record.evidence_type in (EvidenceType.IDENTITY, EvidenceType.CONTACT)
    )


def _source_order(record: EvidenceRecord) -> tuple[int, int]:
    location = record.source_location
    block_index = (
        location.body_block_index
        if location is not None and location.body_block_index is not None
        else 10**9
    )
    paragraph_index = (
        location.paragraph_index
        if location is not None and location.paragraph_index is not None
        else 0
    )
    return block_index, paragraph_index


def _strip_contact_label(segment: str) -> Optional[str]:
    text = (segment or '').strip()
    if not text:
        return None
    for separator in (':', '|', '–', '—', '-'):
        left, found, right = text.partition(separator)
        if found and _is_contact_label(left.strip(), bare=False) and right.strip():
            return right.strip()
    for prefix in _CONTACT_PREFIXES_BY_LENGTH:
        if text.casefold().startswith(prefix + ' '):
            remainder = text[len(prefix) + 1:].strip()
            if _is_contact_profile_value(remainder):
                return remainder
    return None


def _starts_with_contact_label(line: str) -> bool:
    return _strip_contact_label(line) is not None


def _contact_fragment_key(fragment: str) -> str:
    stripped = (fragment or '').strip()
    if _EMAIL_PATTERN.fullmatch(stripped):
        return stripped.casefold()
    if stripped.casefold().startswith(('http://', 'https://', 'www.', 'mailto:')) or _URL_PATTERN.fullmatch(stripped):
        return _normalized_url(stripped)
    return ' '.join(stripped.casefold().split())


def _is_contact_label_text(text: str) -> bool:
    lowered = (text or '').strip().casefold()
    return (
        not lowered
        or lowered in {'link', 'here', 'url', 'profile'}
        or lowered in _PROFILE_LABELS
        or _is_contact_label(lowered, bare=False)
    )


def _contact_fragments(
    line: str,
    hyperlinks: Iterable[object] = (),
    visible_text: Optional[str] = None,
) -> list[str]:
    line = (line or '').strip()
    visible = line if visible_text is None else visible_text
    fragments: list[str] = []
    seen_keys: set[str] = set()

    def add_fragment(value: str) -> None:
        cleaned = (value or '').strip()
        if not cleaned:
            return
        key = _contact_fragment_key(cleaned)
        if key not in seen_keys:
            seen_keys.add(key)
            fragments.append(cleaned)

    links = list(hyperlinks)
    if line:
        segments = [seg.strip() for seg in re.split(r'[|•·]', line) if seg.strip()]
        if len(segments) > 1 and any(':' in seg or _pii_spans(seg) for seg in segments):
            target_segments = segments
        else:
            target_segments = [line]
        for segment in target_segments:
            stripped_value = _strip_contact_label(segment)
            if stripped_value is not None:
                add_fragment(stripped_value)
            else:
                spans = _pii_spans(segment)
                if spans:
                    for start, end, _ in spans:
                        add_fragment(segment[start:end])
                elif not links:
                    for part in re.split(r'[|•·–—]| {2,}', segment):
                        part = part.strip(' ,;')
                        if (
                            part
                            and part.casefold() in _PROFILE_LABELS
                            and _is_contact_label(part, bare=False)
                            and _is_contact_profile_value(part)
                        ):
                            add_fragment(part)
    for link in links:
        url = (getattr(link, 'url', None) or '').strip()
        if not url:
            continue
        mailto = _mailto_address(url)
        if mailto is not None:
            if not _url_visible_in_text(url, visible) and _EMAIL_PATTERN.fullmatch(mailto):
                add_fragment(mailto)
            continue
        if _url_visible_in_text(url, visible):
            continue
        text = (getattr(link, 'text', None) or '').strip()
        if _is_contact_label_text(text) or _url_visible_in_text(url, text):
            add_fragment(url)
        else:
            add_fragment(f'{text}: {url}')
    return fragments


def _is_contact_only_text(text: str) -> bool:
    lines = [line.strip() for line in (text or '').splitlines() if line.strip()]
    if not lines:
        return False
    return all(
        not _scrub_pii(line).strip(' ,;|-') for line in lines
    )


def _verify_sources(
    plan: ResumeSectionPlan,
    catalog: CandidateEvidenceCatalog,
    manifest: Optional[PublicationManifest],
) -> None:
    metadata = plan.planning_metadata
    if (
        metadata.source_candidate_source_sha256 != catalog.source_document.sha256
        or metadata.source_candidate_filename != catalog.source_document.filename
    ):
        raise ResumeGenerationError(
            'Plan source identity does not match the candidate evidence catalog.'
        )
    if manifest is not None:
        if (
            catalog.source_document.sha256 != manifest.source_sha256
            or catalog.source_document.filename != manifest.source_filename
        ):
            raise ResumeGenerationError(
                'Candidate evidence does not match the committed publication manifest.'
            )
        generation = metadata.extraction_generation
        if generation is not None and generation != ExtractionGenerationIdentity.from_manifest(manifest):
            raise ResumeGenerationError(
                'Plan extraction generation does not match the committed candidate evidence generation.'
            )


def _verify_provenance(
    plan: ResumeSectionPlan, catalog: CandidateEvidenceCatalog
) -> dict[str, EvidenceRecord]:
    records: dict[str, EvidenceRecord] = {}
    for record in catalog.evidence_records:
        if record.evidence_id in records:
            raise ResumeGenerationError(
                'Candidate evidence catalog contains duplicate evidence IDs.'
            )
        records[record.evidence_id] = record

    provenance = {reference.evidence_id: reference for reference in plan.evidence_provenance}
    if len(provenance) != len(plan.evidence_provenance) or set(provenance) != set(records):
        raise ResumeGenerationError(
            'Plan evidence provenance does not match the candidate evidence catalog.'
        )

    source_sha256 = catalog.source_document.sha256
    source_filename = catalog.source_document.filename
    for identifier, reference in provenance.items():
        record = records[identifier]
        if content_sha256(record.original_content) != reference.content_sha256:
            raise ResumeGenerationError(
                'Candidate evidence content does not match the planned provenance.'
            )
        if reference.source_sha256 != source_sha256 or reference.source_document != source_filename:
            raise ResumeGenerationError(
                'Candidate evidence source identity does not match the plan.'
            )
    return records


def _iter_table_paragraphs(table):
    for row in getattr(table, 'rows', ()) or ():
        for cell in row:
            for paragraph in getattr(cell, 'paragraphs', ()) or ():
                yield paragraph
            for nested in getattr(cell, 'nested_tables', ()) or ():
                yield from _iter_table_paragraphs(nested)


def _iter_block_paragraphs(block):
    paragraph = getattr(block, 'paragraph', None)
    if paragraph is not None:
        yield paragraph
    table = getattr(block, 'table', None)
    if table is not None:
        yield from _iter_table_paragraphs(table)


def _build_header(
    catalog: CandidateEvidenceCatalog,
    snapshot: Optional[DocumentSnapshot] = None,
) -> DeterministicHeader:
    header_records = sorted(
        (
            record for record in catalog.evidence_records
            if _header_relevant_record(record)
        ),
        key=_source_order,
    )

    full_name = ''
    source_ids: list[str] = []

    def consider_name(
        candidate: str, record: Optional[EvidenceRecord], allow_mononym: bool
    ) -> bool:
        nonlocal full_name
        cleaned = _name_from_line(candidate)
        first_segment = re.split(r'[|•·–—]', cleaned, maxsplit=1)[0].strip(' ,;')
        if first_segment and first_segment != cleaned:
            cleaned = first_segment
        if not _looks_like_name(cleaned, allow_mononym=allow_mononym):
            return False
        full_name = _name_display_text(cleaned)
        if record is not None:
            source_ids.append(record.evidence_id)
        return True

    for record in header_records:
        allow_mononym = (
            record.section_reference in (None, 'professional-identity')
            or record.evidence_type in (EvidenceType.CONTACT, EvidenceType.IDENTITY)
        )
        for line in record.original_content.splitlines():
            if consider_name(line, record, allow_mononym):
                break
        if full_name:
            break

    snapshot_blocks = []
    if snapshot is not None:
        snapshot_blocks = list(snapshot.headers) + list(snapshot.footers)
    if not full_name:
        for block in snapshot_blocks:
            for paragraph in _iter_block_paragraphs(block):
                for line in paragraph.text.splitlines():
                    if consider_name(line, None, True):
                        break
                if full_name:
                    break
            if full_name:
                break

    contact_lines: list[str] = []
    seen_contact_keys: set[str] = set()

    def add_contact_line(fragment: str) -> bool:
        key = _contact_fragment_key(fragment)
        if key in seen_contact_keys:
            return False
        seen_contact_keys.add(key)
        contact_lines.append(fragment)
        return True

    for record in header_records:
        added = False
        for line in record.original_content.splitlines() or ['']:
            for fragment in _contact_fragments(
                line, record.hyperlinks, record.original_content
            ):
                if add_contact_line(fragment):
                    added = True
        if added:
            source_ids.append(record.evidence_id)

    for block in snapshot_blocks:
        for paragraph in _iter_block_paragraphs(block):
            for line in paragraph.text.splitlines() or ['']:
                for fragment in _contact_fragments(
                    line, paragraph.hyperlinks, paragraph.text
                ):
                    add_contact_line(fragment)

    return DeterministicHeader(
        full_name=full_name,
        contact_lines=contact_lines,
        source_evidence_ids=list(dict.fromkeys(source_ids)),
    )


def _is_technical_skill_catalog_record(record: EvidenceRecord) -> bool:
    if record.evidence_type != EvidenceType.TECHNICAL_SKILL:
        return False
    if _record_is_pii_only(record):
        return False
    style = (record.formatting or {}).get('style')
    if isinstance(style, str) and style.casefold().startswith('heading'):
        return False
    return bool(_scrub_pii(record.original_content).strip())


def _build_llm_input(
    plan: ResumeSectionPlan, records: Mapping[str, EvidenceRecord]
) -> tuple[str, dict[str, str], dict[str, str]]:
    selected = set(plan.selected_evidence_ids)
    for identifier in selected:
        record = records.get(identifier)
        if record is None:
            raise ResumeGenerationError(
                'Selected evidence is missing from the candidate evidence catalog.'
            )
        if record.evidence_type in (EvidenceType.IDENTITY, EvidenceType.CONTACT):
            raise ResumeGenerationError(
                'Selected evidence cannot include identity or contact records.'
            )

    all_technical_skill_ids = [
        record.evidence_id
        for record in records.values()
        if _is_technical_skill_catalog_record(record)
    ]

    safe_content: dict[str, str] = {}

    def content(identifier: str) -> str:
        if identifier not in safe_content:
            record = records[identifier]
            safe_content[identifier] = (
                '' if _record_is_pii_only(record) else _scrub_pii(record.original_content)
            )
        return safe_content[identifier]

    seen_skill_texts: set[str] = {
        skill_identity(content(identifier))
        for identifier in all_technical_skill_ids
        if identifier in selected and content(identifier).strip()
    }
    technical_skill_ids: list[str] = []
    for identifier in all_technical_skill_ids:
        text = content(identifier).strip()
        if not text:
            continue
        if identifier in selected:
            technical_skill_ids.append(identifier)
            continue
        identity = skill_identity(text)
        if identity not in seen_skill_texts:
            seen_skill_texts.add(identity)
            technical_skill_ids.append(identifier)
    technical_skill_set = set(technical_skill_ids)
    available_ids = selected | technical_skill_set

    def safe_ids(
        identifiers: Iterable[str], pool: Optional[set[str]] = None
    ) -> list[str]:
        allowed = selected if pool is None else pool
        return [
            identifier
            for identifier in identifiers
            if identifier in allowed and content(identifier).strip()
        ]

    evidence_alias_by_id = {
        identifier: f'E{index}'
        for index, identifier in enumerate(
            (
                identifier for identifier in plan.selected_evidence_ids
                if identifier in selected and content(identifier).strip()
            ),
            start=1,
        )
    }
    extra_skill_index = 1
    for identifier in technical_skill_ids:
        if identifier not in evidence_alias_by_id and content(identifier).strip():
            evidence_alias_by_id[identifier] = f'SK{extra_skill_index}'
            extra_skill_index += 1
    entry_alias_by_id = {
        entry.entry_id: f'ENT{index}'
        for index, entry in enumerate(
            (
                entry
                for section in plan.ordered_sections
                for entry in section.entries
                if safe_ids(entry.selected_evidence_ids)
            ),
            start=1,
        )
    }

    def alias(identifier: str) -> str:
        return evidence_alias_by_id.get(identifier, identifier)

    def entry_alias(identifier: str) -> str:
        return entry_alias_by_id.get(identifier, identifier)

    def evidence(
        identifiers: Iterable[str], pool: Optional[set[str]] = None
    ) -> list[dict[str, str]]:
        return [
            {'evidence_id': alias(identifier), 'original_content': content(identifier)}
            for identifier in safe_ids(identifiers, pool=pool)
        ]

    ordered_sections = []
    for section in plan.ordered_sections:
        section_ids = safe_ids(section.selected_evidence_ids)
        entries = []
        for entry in section.entries:
            entry_ids = safe_ids(entry.selected_evidence_ids)
            if not entry_ids:
                continue
            identity = entry.identity_evidence_id
            entries.append({
                'entry_id': entry_alias(entry.entry_id),
                'identity_evidence_id': alias(identity) if identity in entry_ids else None,
                'selected_evidence_ids': [alias(identifier) for identifier in entry_ids],
                'evidence': evidence(entry_ids),
            })
        if not section_ids and not entries:
            continue
        entry_members = {
            identifier
            for entry in section.entries
            for identifier in safe_ids(entry.selected_evidence_ids)
        }
        ordered_sections.append({
            'section_id': section.section_id,
            'heading_text': section.heading_text,
            'selected_evidence_ids': [alias(identifier) for identifier in section_ids],
            'entries': entries,
            'evidence': evidence(
                identifier for identifier in section_ids if identifier not in entry_members
            ),
        })

    prioritized_skills = []
    for skill in plan.prioritized_skills:
        matched = safe_ids(skill.matched_evidence_ids)
        if not matched:
            continue
        prioritized_skills.append({
            'skill_text': skill.skill_text,
            'priority_tier': skill.priority_tier,
            'matched_evidence_ids': [alias(identifier) for identifier in matched],
        })

    blueprint = plan.professional_summary_blueprint
    technical_capabilities = []
    for skill in blueprint.technical_capabilities:
        matched = safe_ids(skill.matched_evidence_ids)
        if not matched:
            continue
        technical_capabilities.append({
            'skill_text': skill.skill_text,
            'priority_tier': skill.priority_tier,
            'matched_evidence_ids': [alias(identifier) for identifier in matched],
        })
    remaining_capability_texts = {
        skill_identity(item['skill_text']) for item in technical_capabilities
    }
    technical_capability_skill_texts = [
        text for text in blueprint.technical_capability_skill_texts
        if skill_identity(text) in remaining_capability_texts
    ]

    identity_statements = []
    for statement in blueprint.identity_statements:
        identifiers = safe_ids(statement.evidence_ids)
        text = _scrub_pii(statement.statement_text).strip()
        if not identifiers or not text:
            continue
        identity_statements.append({
            'statement_text': text,
            'evidence_ids': [alias(identifier) for identifier in identifiers],
        })

    experience_bindings = []
    for binding in blueprint.experience_bindings:
        identifiers = safe_ids(binding.evidence_ids)
        entry_ids = [
            entry_alias(identifier) for identifier in binding.entry_ids
            if identifier in entry_alias_by_id
        ]
        if not identifiers or not entry_ids:
            continue
        experience_bindings.append({
            'category': binding.category,
            'entry_ids': entry_ids,
            'evidence_ids': [alias(identifier) for identifier in identifiers],
        })

    project_highlights = []
    for entry in blueprint.project_highlights:
        identifiers = safe_ids(entry.selected_evidence_ids)
        if not identifiers or entry.entry_id not in entry_alias_by_id:
            continue
        identity = entry.identity_evidence_id
        project_highlights.append({
            'entry_id': entry_alias(entry.entry_id),
            'source_section_reference': entry.source_section_reference,
            'identity_evidence_id': alias(identity) if identity in identifiers else None,
            'grouping_basis': entry.grouping_basis,
            'selected_evidence_ids': [alias(identifier) for identifier in identifiers],
        })

    summary_blueprint = {
        'professional_identity_evidence_ids': [
            alias(identifier) for identifier in safe_ids(
                blueprint.professional_identity_evidence_ids
            )
        ],
        'experience_categories': list(blueprint.experience_categories),
        'technical_capability_skill_texts': technical_capability_skill_texts,
        'relevant_project_evidence_ids': [
            alias(identifier) for identifier in safe_ids(
                blueprint.relevant_project_evidence_ids
            )
        ],
        'identity_statements': identity_statements,
        'technical_capabilities': technical_capabilities,
        'experience_bindings': experience_bindings,
        'project_highlights': project_highlights,
    }

    technical_skills_evidence = evidence(
        technical_skill_ids, pool=available_ids
    )

    payload = {
        'target_job_context': plan.target_job_context.model_dump(mode='json'),
        'ordered_sections': ordered_sections,
        'prioritized_skills': prioritized_skills,
        'technical_skills_evidence': technical_skills_evidence,
        'professional_summary_blueprint': summary_blueprint,
    }
    return (
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(',', ':')),
        evidence_alias_by_id,
        entry_alias_by_id,
    )


def hydrate_plan_evidence(
    plan: ResumeSectionPlan,
    catalog: CandidateEvidenceCatalog,
    snapshot: Optional[DocumentSnapshot] = None,
    manifest: Optional[PublicationManifest] = None,
) -> HydratedGenerationInput:
    '''Verify source/provenance bindings and build the compact provider input.'''
    _verify_sources(plan, catalog, manifest)
    records = _verify_provenance(plan, catalog)
    llm_input_text, evidence_aliases, entry_aliases = _build_llm_input(plan, records)
    return HydratedGenerationInput(
        header=_build_header(catalog, snapshot),
        evidence_by_id=records,
        llm_input_text=llm_input_text,
        evidence_alias_by_id=evidence_aliases,
        entry_alias_by_id=entry_aliases,
    )


def _schema_repair_details(error: ValidationError) -> list[dict[str, str]]:
    details = []
    for item in error.errors(
        include_url=False, include_context=False, include_input=False
    )[:20]:
        location = '.'.join(str(part) for part in item.get('loc', ())) or 'payload'
        detail = {
            'code': str(item.get('type', 'validation_error')),
            'location': location,
        }
        if item.get('msg'):
            detail['message'] = str(item['msg'])
        details.append(detail)
    return details or [{'code': 'validation_error', 'location': 'payload'}]


def parse_tailored_resume_payload(text: str) -> TailoredResumePayload:
    '''Extract one optional JSON fence and strictly parse one payload object.'''
    if not isinstance(text, str):
        raise ResumeGenerationError(
            'Provider output must be JSON text.',
            repair_details=[{'code': 'invalid_json', 'location': 'payload'}],
        )
    cleaned = text.strip()
    fenced_blocks = _FENCED_BLOCK_PATTERN.findall(cleaned)
    if len(fenced_blocks) > 1:
        raise ResumeGenerationError(
            'Provider output must contain at most one JSON fence.',
            repair_details=[{'code': 'multiple_json_fences', 'location': 'payload'}],
        )
    if len(fenced_blocks) == 1:
        language, body = fenced_blocks[0]
        if language.strip().casefold() not in ('', 'json'):
            raise ResumeGenerationError(
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
        raise ResumeGenerationError(
            'Provider output must be valid, unambiguous JSON.',
            repair_details=[{'code': 'invalid_json', 'location': 'payload'}],
        ) from None
    if not isinstance(document, dict):
        raise ResumeGenerationError(
            'Provider output must be a JSON object.',
            repair_details=[{'code': 'invalid_root', 'location': 'payload'}],
        )
    try:
        return TailoredResumePayload.model_validate(document)
    except ValidationError as error:
        raise ResumeGenerationError(
            'Provider output does not match the required resume schema.',
            repair_details=_schema_repair_details(error),
        ) from None


def _numeric_tokens(text: str) -> set[str]:
    tokens = set()
    for match in _METRIC_PATTERN.finditer(text or ''):
        number = match.group('number').replace(',', '')
        currency = match.group('currency') or ''
        suffix = (match.group('suffix') or '').lower()
        tokens.add(f'{currency}{number}{suffix}')
    return tokens


def _check_numeric_claims(
    text: str,
    ground_texts: Iterable[str],
    location: str,
    severity: str,
    add: Callable[[str, str, str, str, Iterable[str]], None],
) -> None:
    generated = _numeric_tokens(text)
    if not generated:
        return
    ground: set[str] = set()
    for source in ground_texts:
        ground |= _numeric_tokens(source)
    if generated - ground:
        add(
            severity,
            'ungrounded_metric',
            location,
            'Generated text contains a numeric claim that is not present in the cited evidence.',
            (),
        )


def _check_contact_pii(
    text: str,
    location: str,
    add: Callable[[str, str, str, str, Iterable[str]], None],
) -> None:
    if _pii_spans(text or ''):
        add(
            'error',
            'contact_pii',
            location,
            'Generated text must not contain contact details.',
            (),
        )


def _skill_grounded_in_evidence(
    skill_text: str,
    evidence_ids: Iterable[str],
    records: Mapping[str, EvidenceRecord],
    selected: set[str],
) -> bool:
    identifiers = list(evidence_ids)
    if not identifiers:
        return False
    normalized_skill = ' '.join((skill_text or '').strip().split())
    if not normalized_skill:
        return False
    pattern = re.compile(
        r'(?<![A-Za-z0-9_])' + re.escape(normalized_skill) + r'(?![A-Za-z0-9_])',
        re.IGNORECASE,
    )
    found_match = False
    for identifier in identifiers:
        if identifier not in selected:
            return False
        record = records.get(identifier)
        if record is None or _record_is_pii_only(record):
            return False
        normalized_source = ' '.join(record.original_content.split())
        if pattern.search(normalized_source):
            found_match = True
    return found_match


def validate_tailored_resume(
    payload: TailoredResumePayload,
    plan: ResumeSectionPlan,
    catalog: CandidateEvidenceCatalog,
    *,
    strict_metrics: bool = False,
    raise_on_error: bool = True,
) -> GroundingValidationReport:
    '''Deterministically check evidence scope and numeric grounding.

    Traceability errors are hard failures. Numeric-risk checks are warnings for
    human review unless ``strict_metrics`` is set, in which case they fail.
    Semantic entailment is never claimed.
    '''
    records = {record.evidence_id: record for record in catalog.evidence_records}
    provenance = {reference.evidence_id: reference for reference in plan.evidence_provenance}
    selected = set(plan.selected_evidence_ids)
    technical_skill_ids = {
        record.evidence_id
        for record in catalog.evidence_records
        if _is_technical_skill_catalog_record(record)
    }
    allowed_skill_evidence = selected | technical_skill_ids
    findings: list[ClaimValidationFinding] = []
    metric_severity = 'error' if strict_metrics else 'warning'

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

    for identifier in dict.fromkeys([*plan.selected_evidence_ids, *technical_skill_ids]):
        record = records.get(identifier)
        reference = provenance.get(identifier)
        if (
            record is None
            or reference is None
            or content_sha256(record.original_content) != reference.content_sha256
        ):
            add(
                'error',
                'evidence_integrity',
                'evidence',
                'Selected evidence does not match its planned content binding.',
                (identifier,),
            )

    def scope_check(
        identifiers: Iterable[str],
        allowed: set[str],
        location: str,
        out_of_scope_code: str,
        pool: Optional[set[str]] = None,
    ) -> None:
        effective_pool = selected if pool is None else pool
        for identifier in identifiers:
            if identifier not in effective_pool:
                add(
                    'error',
                    'evidence_not_selected',
                    location,
                    'Cited evidence is not part of the selected plan evidence.',
                    (identifier,),
                )
            elif identifier not in allowed:
                add(
                    'error',
                    out_of_scope_code,
                    location,
                    'Cited evidence is outside the permitted scope for this claim.',
                    (identifier,),
                )
            elif identifier not in records:
                add(
                    'error',
                    'evidence_integrity',
                    location,
                    'Cited evidence is not present in the candidate catalog.',
                    (identifier,),
                )

    for index, sentence in enumerate(payload.summary_sentences):
        location = f'summary_sentences[{index}]'
        scope_check(sentence.evidence_ids, selected, location, 'summary_evidence_out_of_scope')
        _check_numeric_claims(
            sentence.text,
            [records[i].original_content for i in sentence.evidence_ids if i in records],
            location,
            metric_severity,
            add,
        )
        _check_contact_pii(sentence.text, location, add)

    all_selected_content = [
        records[identifier].original_content
        for identifier in plan.selected_evidence_ids
        if identifier in records
    ]
    _check_numeric_claims(
        payload.headline_title, all_selected_content, 'headline_title', metric_severity, add
    )
    _check_contact_pii(payload.headline_title, 'headline_title', add)
    if payload.tagline is not None:
        _check_numeric_claims(
            payload.tagline, all_selected_content, 'tagline', metric_severity, add
        )
        _check_contact_pii(payload.tagline, 'tagline', add)

    planned_skills = {skill_identity(skill.skill_text): skill for skill in plan.prioritized_skills}
    for group_index, group in enumerate(payload.skill_groups):
        _check_contact_pii(group.group_name, f'skill_groups[{group_index}].group_name', add)
        for item_index, item in enumerate(group.skills):
            location = f'skill_groups[{group_index}].skills[{item_index}]'
            _check_contact_pii(item.skill_text, location, add)
            planned = planned_skills.get(skill_identity(item.skill_text))
            if planned is None:
                scope_check(
                    item.evidence_ids,
                    allowed_skill_evidence,
                    location,
                    'skill_evidence_out_of_scope',
                    pool=allowed_skill_evidence,
                )
                if not _skill_grounded_in_evidence(
                    item.skill_text, item.evidence_ids, records, allowed_skill_evidence
                ):
                    add(
                        'error',
                        'skill_not_planned',
                        location,
                        'Generated skill is not anchored in the planned skills or cited selected evidence.',
                        item.evidence_ids,
                    )
                    continue
            else:
                catalog_skill_ids = {
                    identifier
                    for identifier in item.evidence_ids
                    if identifier in (technical_skill_ids - selected)
                    and _skill_grounded_in_evidence(
                        item.skill_text, (identifier,), records, technical_skill_ids
                    )
                }
                allowed = (
                    (set(planned.matched_evidence_ids) & allowed_skill_evidence)
                    | catalog_skill_ids
                )
                scope_check(
                    item.evidence_ids,
                    allowed,
                    location,
                    'skill_evidence_out_of_scope',
                    pool=allowed_skill_evidence,
                )
            _check_numeric_claims(
                item.skill_text,
                [records[i].original_content for i in item.evidence_ids if i in records],
                location,
                metric_severity,
                add,
            )

    planned_sections = {section.section_id: section for section in plan.ordered_sections}
    plan_order = [section.section_id for section in plan.ordered_sections]
    generated_order = [section.section_id for section in payload.sections]
    known_generated = [identifier for identifier in generated_order if identifier in planned_sections]
    expected_generated = [identifier for identifier in plan_order if identifier in set(known_generated)]
    if known_generated != expected_generated:
        add(
            'error',
            'section_order_mismatch',
            'sections',
            'Generated sections must preserve plan order.',
        )

    for section_index, section in enumerate(payload.sections):
        location = f'sections[{section_index}]'
        _check_contact_pii(section.heading_text, f'{location}.heading_text', add)
        planned_section = planned_sections.get(section.section_id)
        if planned_section is None:
            add(
                'error',
                'section_not_planned',
                location,
                'Generated section is not present in the plan.',
            )
            continue
        if section.heading_text != planned_section.heading_text:
            add(
                'error',
                'section_heading_mismatch',
                location,
                'Generated section heading does not match the plan.',
            )

        planned_entries = {entry.entry_id: entry for entry in planned_section.entries}
        planned_entry_order = [entry.entry_id for entry in planned_section.entries]
        generated_entry_order = [entry.entry_id for entry in section.entries]
        known_entries = [
            identifier for identifier in generated_entry_order if identifier in planned_entries
        ]
        expected_entries = [
            identifier for identifier in planned_entry_order if identifier in set(known_entries)
        ]
        if known_entries != expected_entries:
            add(
                'error',
                'entry_order_mismatch',
                location,
                'Generated entries must preserve plan order.',
            )

        for entry_index, entry in enumerate(section.entries):
            entry_location = f'{location}.entries[{entry_index}]'
            planned_entry = planned_entries.get(entry.entry_id)
            if planned_entry is None:
                add(
                    'error',
                    'entry_not_planned',
                    entry_location,
                    'Generated entry does not belong to the planned section.',
                )
                continue
            entry_scope = set(planned_entry.selected_evidence_ids)
            _check_contact_pii(entry.title, f'{entry_location}.title', add)
            for field_name in ('organization', 'dates', 'location', 'tech_stack', 'meta'):
                field_value = getattr(entry, field_name)
                if field_value is not None:
                    _check_contact_pii(field_value, f'{entry_location}.{field_name}', add)
            for bullet_index, bullet in enumerate(entry.bullets):
                bullet_location = f'{entry_location}.bullets[{bullet_index}]'
                scope_check(
                    bullet.evidence_ids, entry_scope, bullet_location, 'entry_evidence_out_of_scope'
                )
                _check_numeric_claims(
                    bullet.text,
                    [records[i].original_content for i in bullet.evidence_ids if i in records],
                    bullet_location,
                    metric_severity,
                    add,
                )
                _check_contact_pii(bullet.text, bullet_location, add)
            entry_content = [
                records[identifier].original_content
                for identifier in planned_entry.selected_evidence_ids
                if identifier in records
            ]
            _check_numeric_claims(
                entry.title, entry_content, f'{entry_location}.title', metric_severity, add
            )
            for field_name in ('organization', 'dates', 'location', 'tech_stack', 'meta'):
                field_value = getattr(entry, field_name)
                if field_value is not None:
                    _check_numeric_claims(
                        field_value, entry_content, f'{entry_location}.{field_name}',
                        metric_severity, add,
                    )

    traceability_verified = not any(
        finding.severity == 'error' and finding.code != 'ungrounded_metric'
        for finding in findings
    )
    report = GroundingValidationReport(
        traceability_verified=traceability_verified,
        findings=findings,
    )

    if (
        strict_metrics
        and raise_on_error
        and any(finding.code == 'ungrounded_metric' for finding in findings)
    ):
        raise ResumeGenerationError(
            'Generated resume contains ungrounded numeric claims.'
        )
    if not traceability_verified and raise_on_error:
        raise ResumeGenerationError(
            'Generated resume failed evidence traceability validation.'
        )
    return report


def _resolve_payload_aliases(
    payload: TailoredResumePayload,
    evidence_alias_by_id: Mapping[str, str],
    entry_alias_by_id: Mapping[str, str],
) -> TailoredResumePayload:
    evidence_by_alias = {
        alias: identifier for identifier, alias in evidence_alias_by_id.items()
    }
    entry_by_alias = {
        alias: identifier for identifier, alias in entry_alias_by_id.items()
    }
    data = payload.model_dump(mode='python')

    def resolve_evidence(identifiers: list[str]) -> list[str]:
        return [evidence_by_alias.get(identifier, identifier) for identifier in identifiers]

    for sentence in data['summary_sentences']:
        sentence['evidence_ids'] = resolve_evidence(sentence['evidence_ids'])
    for group in data['skill_groups']:
        for item in group['skills']:
            item['evidence_ids'] = resolve_evidence(item['evidence_ids'])
    for section in data['sections']:
        for entry in section['entries']:
            entry['entry_id'] = entry_by_alias.get(entry['entry_id'], entry['entry_id'])
            for bullet in entry['bullets']:
                bullet['evidence_ids'] = resolve_evidence(bullet['evidence_ids'])

    try:
        return TailoredResumePayload.model_validate(data)
    except ValidationError as error:
        raise ResumeGenerationError(
            'Provider output does not match the required resume schema.',
            repair_details=_schema_repair_details(error),
        ) from None


def _validation_repair_details(
    report: GroundingValidationReport,
    *,
    payload: Optional[TailoredResumePayload] = None,
    plan: Optional[ResumeSectionPlan] = None,
    hydrated: Optional[HydratedGenerationInput] = None,
) -> list[dict[str, str]]:
    planned_skills = (
        {skill_identity(skill.skill_text): skill for skill in plan.prioritized_skills}
        if plan is not None else {}
    )
    planned_order = (
        [
            section.section_id
            for section in plan.ordered_sections
            if section.section_id not in ('professional-summary', 'technical-skills')
        ]
        if plan is not None else []
    )
    details = []
    for finding in report.findings:
        if finding.severity != 'error':
            continue
        message = finding.message
        if payload is not None and hydrated is not None:
            if finding.code in (
                'skill_evidence_out_of_scope',
                'evidence_not_selected',
                'skill_not_planned',
            ):
                match = re.fullmatch(
                    r'skill_groups\[(\d+)\]\.skills\[(\d+)\]', finding.location
                )
                if match:
                    gi, si = int(match.group(1)), int(match.group(2))
                    if gi < len(payload.skill_groups) and si < len(payload.skill_groups[gi].skills):
                        item = payload.skill_groups[gi].skills[si]
                        planned = planned_skills.get(skill_identity(item.skill_text))
                        if planned is not None:
                            aliases = [
                                hydrated.evidence_alias_by_id[eid]
                                for eid in planned.matched_evidence_ids
                                if eid in hydrated.evidence_alias_by_id
                            ]
                            if aliases:
                                message += f' Use matched_evidence_ids: {aliases}.'
                        elif finding.code == 'evidence_not_selected':
                            message += (
                                ' Cite an existing SK... alias from technical_skills_evidence'
                                ' or E... alias from ordered_sections whose original_content'
                                ' verbatim contains skill_text, or omit the skill.'
                            )
            elif finding.code == 'entry_evidence_out_of_scope':
                match = re.fullmatch(
                    r'sections\[(\d+)\]\.entries\[(\d+)\]\.bullets\[\d+\]',
                    finding.location,
                )
                if match:
                    si, ei = int(match.group(1)), int(match.group(2))
                    if si < len(payload.sections) and ei < len(payload.sections[si].entries):
                        entry = payload.sections[si].entries[ei]
                        entry_alias = hydrated.entry_alias_by_id.get(
                            entry.entry_id, entry.entry_id
                        )
                        message += (
                            f' Cite ONLY aliases from {entry_alias}'
                            ' selected_evidence_ids in ordered_sections.'
                        )
            elif finding.code == 'section_order_mismatch' and planned_order:
                message += f' Emit sections in exact plan order: {planned_order}.'
        details.append({
            'code': finding.code,
            'location': finding.location,
            'message': message,
        })
    return details[:20] or [{'code': 'validation_error', 'location': 'payload'}]


def _sanitize_repair_details(
    details: Iterable[dict[str, str]],
) -> list[dict[str, str]]:
    sanitized = []
    for detail in list(details)[:20]:
        item = {}
        for key in ('code', 'location', 'message'):
            value = detail.get(key)
            if value is None:
                continue
            text = str(value)
            for start, end, _ in reversed(_pii_spans(text)):
                text = text[:start] + '[redacted]' + text[end:]
            text = re.sub(r'\b[0-9a-f]{64}\b', '[evidence_id]', text, flags=re.I)
            item[key] = text
        if item:
            sanitized.append(item)
    return sanitized or [{'code': 'validation_error', 'location': 'payload'}]


def _repair_input(original_input: str, details: Iterable[dict[str, str]]) -> str:
    sanitized = _sanitize_repair_details(details)
    feedback = json.dumps(
        {
            'errors': sanitized,
            'repair_guidance': (
                'Cite ONLY aliases from the exact target entry selected_evidence_ids '
                'for entry bullets (never from any other entry), use each supplied '
                'entry_id at most once (never duplicate or split an entry_id across '
                'multiple entries), for any skill listed in prioritized_skills cite '
                'ONLY its exact matched_evidence_ids from prioritized_skills, and for '
                'supporting skills verify the exact SK... or E... alias whose '
                'original_content verbatim contains skill_text (or omit the skill).'
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


def _requires_repair(report: GroundingValidationReport, strict_metrics: bool) -> bool:
    return (
        not report.traceability_verified
        or (
            strict_metrics
            and any(finding.code == 'ungrounded_metric' for finding in report.findings)
        )
    )


def _normalize_language_sections(payload: TailoredResumePayload) -> TailoredResumePayload:
    '''Ensure languages entries carry no bullet items by folding grounded bullets into title.'''
    changed = False
    data = payload.model_dump(mode='python')
    for section in data['sections']:
        if section['section_id'] != 'languages':
            continue
        for entry in section['entries']:
            bullets = [
                bullet['text'].strip()
                for bullet in entry['bullets']
                if bullet['text'].strip()
            ]
            if not bullets:
                continue
            changed = True
            title = entry['title'].strip()
            if title.casefold() in ('languages', 'language', 'spoken languages'):
                entry['title'] = ' · '.join(dict.fromkeys(bullets))
            elif len(bullets) == 1:
                detail = bullets[0]
                if detail.casefold() not in title.casefold():
                    if title.casefold() in detail.casefold():
                        entry['title'] = detail
                    else:
                        entry['title'] = f'{title} ({detail})'
            else:
                merged = [title]
                for item in bullets:
                    if item.casefold() not in title.casefold():
                        merged.append(item)
                entry['title'] = ' · '.join(dict.fromkeys(merged))
            entry['bullets'] = []
    if not changed:
        return payload
    return TailoredResumePayload.model_validate(data)


class ResumeGenerator:
    '''One provider call plus at most one bounded repair retry.'''

    def __init__(
        self, client: AgentRouterClient, *, strict_metrics: bool = False
    ) -> None:
        self._client = client
        self._strict_metrics = strict_metrics

    def _attempt(
        self,
        input_text: str,
        plan: ResumeSectionPlan,
        catalog: CandidateEvidenceCatalog,
        hydrated: HydratedGenerationInput,
    ) -> tuple[TailoredResumePayload, GroundingValidationReport]:
        output_text = self._client.generate(
            instructions=RESUME_GENERATION_INSTRUCTIONS,
            input_text=input_text,
        )
        payload = parse_tailored_resume_payload(output_text)
        payload = _resolve_payload_aliases(
            payload,
            hydrated.evidence_alias_by_id,
            hydrated.entry_alias_by_id,
        )
        report = validate_tailored_resume(
            payload,
            plan,
            catalog,
            strict_metrics=self._strict_metrics,
            raise_on_error=False,
        )
        if _requires_repair(report, self._strict_metrics):
            raise ResumeGenerationError(
                'Generated resume requires repair.',
                repair_details=_validation_repair_details(
                    report, payload=payload, plan=plan, hydrated=hydrated
                ),
            )
        normalized = _normalize_language_sections(payload)
        if normalized != payload:
            payload = normalized
            report = validate_tailored_resume(
                payload,
                plan,
                catalog,
                strict_metrics=self._strict_metrics,
                raise_on_error=True,
            )
        return payload, report

    def generate(
        self,
        plan: ResumeSectionPlan,
        catalog: CandidateEvidenceCatalog,
        snapshot: Optional[DocumentSnapshot] = None,
        manifest: Optional[PublicationManifest] = None,
    ) -> TailoredResumeArtifact:
        '''Hydrate, call the provider, parse, validate, and build the artifact.'''
        hydrated = hydrate_plan_evidence(plan, catalog, snapshot, manifest)
        try:
            payload, report = self._attempt(
                hydrated.llm_input_text, plan, catalog, hydrated
            )
        except ResumeGenerationError as error:
            repair_text = _repair_input(
                hydrated.llm_input_text,
                error.repair_details
                or [{'code': 'validation_error', 'location': 'payload'}],
            )
            try:
                payload, report = self._attempt(
                    repair_text, plan, catalog, hydrated
                )
            except ResumeGenerationError:
                raise ResumeGenerationError(
                    'Provider output failed validation after one repair retry.'
                ) from None
        return TailoredResumeArtifact(
            schema_version=RESUME_GENERATION_SCHEMA_VERSION,
            prompt_version=RESUME_GENERATION_PROMPT_VERSION,
            plan_schema_version=plan.schema_version,
            model=self._client.model,
            header=hydrated.header,
            content=payload,
            validation=report,
        )


def generate_tailored_resume(
    plan: ResumeSectionPlan,
    catalog: CandidateEvidenceCatalog,
    snapshot: Optional[DocumentSnapshot],
    manifest: Optional[PublicationManifest],
    client: AgentRouterClient,
    *,
    strict_metrics: bool = False,
) -> TailoredResumeArtifact:
    '''Backward-compatible wrapper around :class:`ResumeGenerator`.'''
    return ResumeGenerator(client, strict_metrics=strict_metrics).generate(
        plan, catalog, snapshot, manifest
    )
