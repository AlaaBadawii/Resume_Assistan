'''Deterministic canonicalization for pasted job-description text.

Job boards often copy Unicode bullets, smart quotes, and invisible spacing
into the clipboard. Stage 2 must send the model and the exact-substring
verifier the same canonical text, so this module performs only lossless
layout cleanup: it never rewrites words or drops substantive content.
'''

import re

_ZERO_WIDTH = dict.fromkeys(map(ord, '\u200b\u200c\u200d\ufeff'))
_QUOTES_AND_MINUS = {
    ord('\u2018'): "'",
    ord('\u2019'): "'",
    ord('\u201a'): "'",
    ord('\u201b'): "'",
    ord('\u201c'): '"',
    ord('\u201d'): '"',
    ord('\u201e'): '"',
    ord('\u201f'): '"',
    ord('\u2212'): '-',
}
_INLINE_MARKER = re.compile(r'(?<=\S)[\u2713\u2714\u2022\u25aa\u25cf\u2212](?=[ \t])')
_LINE_MARKER = re.compile(r'(?m)^[ \t]*[\u2713\u2714\u2022\u25aa\u25cf][ \t]+')
_HORIZONTAL = re.compile(r'[ \t\f\v]+')
_TRAILING = re.compile(r'[ \t]+$', re.MULTILINE)
_BLANK_LINES = re.compile(r'\n{3,}')


def sanitize_job_description_text(text: str) -> str:
    '''Return canonical JD text without changing words or paragraph structure.

    Glued inline list markers become newline-separated '- ' items. Unicode
    minus signs that are not list markers become ASCII hyphens, so ranges such
    as '3-4 years' and terms such as 'data-driven' stay intact.
    '''
    if not isinstance(text, str):
        raise TypeError('Job description text must be a string.')

    normalized = text.replace('\r\n', '\n').replace('\r', '\n')
    normalized = normalized.translate(_ZERO_WIDTH).replace('\u00a0', ' ')
    normalized = _INLINE_MARKER.sub('\n- ', normalized)
    normalized = _LINE_MARKER.sub('- ', normalized)
    normalized = normalized.translate(_QUOTES_AND_MINUS)
    normalized = _HORIZONTAL.sub(' ', normalized)
    normalized = _TRAILING.sub('', normalized)
    normalized = _BLANK_LINES.sub('\n\n', normalized)
    return normalized
