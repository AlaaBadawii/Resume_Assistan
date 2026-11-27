'''Offline tests for deterministic pasted-JD canonicalization.'''

import pytest

from resume_assistant.job_text import sanitize_job_description_text
from tests.fixtures.jobs import SYNTHETIC_JOB_TEXT


MESSY = (
    '\ufeffSenior\u00a0Backend\u200b Engineer at Acme\u2019s lab.\r\n'
    'Build data-driven APIs:\u2713 Own cross-functional delivery.\u2713 Improve latency\u22125ms.\r\n'
    'Requirements: 3\u22124 years with Python \u201cand\u201d SQL.\u200d\r\n'
    '\u2022 Mentor engineers.\r\n'
)


def test_sanitize_messy_job_board_text_preserves_words_and_hyphens():
    cleaned = sanitize_job_description_text(MESSY)

    assert cleaned == (
        "Senior Backend Engineer at Acme's lab.\n"
        'Build data-driven APIs:\n'
        '- Own cross-functional delivery.\n'
        '- Improve latency-5ms.\n'
        'Requirements: 3-4 years with Python "and" SQL.\n'
        '- Mentor engineers.\n'
    )
    assert 'data-driven' in cleaned
    assert 'cross-functional' in cleaned
    assert '3-4 years' in cleaned


def test_sanitize_removes_invisible_characters_and_normalizes_quotes():
    cleaned = sanitize_job_description_text(MESSY)

    for character in ('\u200b', '\u200c', '\u200d', '\ufeff', '\u00a0', '\u2212'):
        assert character not in cleaned
    assert '\u2019' not in cleaned and '\u201c' not in cleaned and '\u201d' not in cleaned
    assert "Acme's" in cleaned and '"and"' in cleaned


def test_sanitize_is_idempotent_and_leaves_clean_text_unchanged():
    cleaned = sanitize_job_description_text(MESSY)
    assert sanitize_job_description_text(cleaned) == cleaned
    assert sanitize_job_description_text(SYNTHETIC_JOB_TEXT) == SYNTHETIC_JOB_TEXT


def test_sanitize_collapses_horizontal_space_and_extra_blank_lines():
    cleaned = sanitize_job_description_text('Role:\t\tEngineer   \n\n\n\nBuild   things.  \n')
    assert cleaned == 'Role: Engineer\n\nBuild things.\n'


def test_sanitize_rejects_non_text_input():
    with pytest.raises(TypeError):
        sanitize_job_description_text(b'not text')
