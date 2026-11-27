'''Local-only naming for recruiter-facing resume downloads.'''

import re
import unicodedata

_BOILERPLATE_HEADINGS = frozenset({
    'about', 'about the job', 'about the role', 'about us', 'company overview',
    'description', 'job description', 'job objective', 'job overview', 'job summary',
    'objective', 'overview', 'position objective', 'position overview', 'position summary',
    'role description', 'role objective', 'role overview', 'role summary', 'summary',
    'the opportunity', 'the role', 'what you will do', 'what you ll do', 'what youll do', 'who we are',
})
_UNKNOWN = {'', 'unknown', 'n a', 'na', 'none', 'not specified'}
_MAX_STEM = 80


def _words(text: str | None) -> list[str]:
    decomposed = unicodedata.normalize('NFKD', text or '')
    unaccented = ''.join(ch for ch in decomposed if not unicodedata.combining(ch))
    return re.findall(r'[A-Za-z0-9]+', unaccented)


def usable_role(text: str | None) -> bool:
    key = ' '.join(_words(text)).lower()
    return key not in _UNKNOWN and key not in _BOILERPLATE_HEADINGS


def known_company(text: str | None) -> bool:
    return ' '.join(_words(text)).lower() not in _UNKNOWN


def _pascal_word(word: str) -> str:
    return word.capitalize() if word.isupper() else word[:1].upper() + word[1:]


def _title_word(word: str) -> str:
    if word.lower() in {'ai', 'ml', 'it', 'qa', 'ui', 'ux'} or (word.isupper() and len(word) <= 4):
        return word.upper()
    return _pascal_word(word)


def _fit_parts(parts: list[str], max_stem: int) -> str:
    # Give short parts their full length first; share the rest and prefer
    # whole words. Even a very long title leaves room for the company.
    remaining = max_stem - (len(parts) - 1)
    for n, index in enumerate(sorted(range(len(parts)), key=lambda i: len(parts[i]))):
        limit = remaining // (len(parts) - n)
        part = parts[index]
        if len(part) > limit:
            prefix = part[:limit]
            if part[limit] != '_' and '_' in prefix:
                prefix = prefix.rsplit('_', 1)[0]
            parts[index] = prefix.rstrip('_')
        remaining -= len(parts[index])
    return '_'.join(parts)


def build_resume_stem(candidate_name: str | None, role: str | None, company: str | None = None) -> str:
    '''Build CandidateName_Role[_Company] from the local header and analyzed JD.'''
    name = ''.join(map(_pascal_word, _words(candidate_name)))
    title = '_'.join(map(_title_word, _words(role))) if usable_role(role) else 'Tailored_Resume'
    parts = [name or 'Resume', title]
    if known_company(company):
        parts.append('_'.join(map(_title_word, _words(company))))
    return _fit_parts(parts, _MAX_STEM)


def build_cover_letter_stem(
    candidate_name: str | None,
    role: str | None,
    company: str | None = None,
) -> str:
    '''Build CandidateName_Role[_Company]_Cover_Letter within the filename cap.'''
    name = ''.join(map(_pascal_word, _words(candidate_name)))
    parts = [name or 'Candidate']
    if usable_role(role):
        parts.append('_'.join(map(_title_word, _words(role))))
    if known_company(company):
        parts.append('_'.join(map(_title_word, _words(company))))
    suffix = 'Cover_Letter'
    base = _fit_parts(parts, _MAX_STEM - len(suffix) - 1)
    return f'{base}_{suffix}'
