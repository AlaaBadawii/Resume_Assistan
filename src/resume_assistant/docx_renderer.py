'''ATS-safe single-column DOCX rendering for a tailored resume artifact.

Styling mirrors ``src/build_template.py``: Calibri, named paragraph styles,
``#1F3B57`` section headings with a bottom rule, no tables, no images, and no
Word header/footer content. Rendering returns bytes; the CLI writes them
through the pinned private-output boundary with mode 0600.
'''

import io
import os
import re
import secrets
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Optional, Tuple

from docx import Document
from docx.enum.style import WD_STYLE_TYPE
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_TAB_ALIGNMENT
from docx.opc.constants import RELATIONSHIP_TYPE
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Inches, Length, Mm, Pt, RGBColor

from resume_assistant.cover_letter_models import CoverLetterArtifact
from resume_assistant.generation_models import GeneratedEntry, TailoredResumeArtifact

TailoredResume = TailoredResumeArtifact

FONT = 'Calibri'
BODY_SIZE = Pt(9)
HEADING_COLOR = RGBColor(0x1F, 0x3B, 0x57)

_COMPACT_TIERS: dict[int, dict[str, float]] = {
    0: {
        'top_margin_in': 0.38,
        'bottom_margin_in': 0.38,
        'body_pt': 9.0,
        'heading_pt': 9.5,
        'name_pt': 15.5,
        'title_pt': 10.5,
        'contact_pt': 8.5,
        'meta_pt': 8.5,
        'line_spacing': 1.06,
        'tight_line_spacing': 1.05,
        'section_before_pt': 4.0,
        'section_after_pt': 1.5,
        'entry_before_pt': 2.0,
        'entry_after_pt': 0.5,
        'body_after_pt': 1.0,
        'skill_after_pt': 0.5,
        'compact_before_pt': 1.0,
        'compact_after_pt': 0.5,
        'bullet_after_pt': 0.5,
    },
    1: {
        'top_margin_in': 0.34,
        'bottom_margin_in': 0.34,
        'body_pt': 8.75,
        'heading_pt': 9.25,
        'name_pt': 15.0,
        'title_pt': 10.0,
        'contact_pt': 8.25,
        'meta_pt': 8.25,
        'line_spacing': 1.04,
        'tight_line_spacing': 1.04,
        'section_before_pt': 3.5,
        'section_after_pt': 1.0,
        'entry_before_pt': 1.5,
        'entry_after_pt': 0.5,
        'body_after_pt': 0.75,
        'skill_after_pt': 0.35,
        'compact_before_pt': 0.75,
        'compact_after_pt': 0.35,
        'bullet_after_pt': 0.25,
    },
    2: {
        'top_margin_in': 0.31,
        'bottom_margin_in': 0.31,
        'body_pt': 8.5,
        'heading_pt': 9.0,
        'name_pt': 14.5,
        'title_pt': 9.75,
        'contact_pt': 8.0,
        'meta_pt': 8.0,
        'line_spacing': 1.02,
        'tight_line_spacing': 1.02,
        'section_before_pt': 3.0,
        'section_after_pt': 0.75,
        'entry_before_pt': 1.25,
        'entry_after_pt': 0.25,
        'body_after_pt': 0.5,
        'skill_after_pt': 0.25,
        'compact_before_pt': 0.5,
        'compact_after_pt': 0.25,
        'bullet_after_pt': 0.0,
    },
}

PAGE_SIZES: dict[str, Tuple[Length, Length]] = {
    'A4': (Mm(210), Mm(297)),
    'Letter': (Inches(8.5), Inches(11)),
}
DEFAULT_PAGE_SIZE = 'A4'
PDF_CONVERSION_TIMEOUT_SECONDS = 60

_ENTRY_STYLE = {
    'professional-experience': 'Resume Job Title',
    'projects': 'Resume Project Title',
    'education': 'Resume Subheading',
    'certifications': 'Resume Subheading',
}
_COMPACT_SECTIONS = frozenset({'education', 'certifications'})
_NON_SECTION_OUTPUTS = frozenset({'professional-summary', 'technical-skills'})
_LINK_CONTACT_PATTERN = re.compile(
    r'(?i)(?:^https?://|^www\.|(?:[a-z0-9-]+\.)+(?:com|io|dev|me|net|org|ai|co|app|tech|site|info|ly)(?:/|$))'
)
_MONTH_ABBREVIATIONS = {
    'january': 'Jan',
    'february': 'Feb',
    'march': 'Mar',
    'april': 'Apr',
    'may': 'May',
    'june': 'Jun',
    'july': 'Jul',
    'august': 'Aug',
    'september': 'Sep',
    'october': 'Oct',
    'november': 'Nov',
    'december': 'Dec',
}
_FULL_MONTH_PATTERN = re.compile(
    r'\b(' + '|'.join(_MONTH_ABBREVIATIONS) + r')\b',
    re.IGNORECASE,
)


class DocxRenderError(ValueError):
    '''Controlled, content-free DOCX rendering error.'''


class PdfRenderError(DocxRenderError, RuntimeError):
    '''Controlled, content-free PDF conversion error.'''


def find_pdf_converter(converter_bin: Optional[str] = None) -> str:
    binary = converter_bin or shutil.which('soffice') or shutil.which('libreoffice')
    if not binary:
        raise PdfRenderError(
            'LibreOffice (libreoffice-writer) is required for --output-pdf.'
        )
    return binary


def resolve_compact_tier(compact_tier: int) -> dict[str, float]:
    if (
        isinstance(compact_tier, bool)
        or not isinstance(compact_tier, int)
        or compact_tier not in _COMPACT_TIERS
    ):
        raise DocxRenderError('Unsupported compact tier. Supported values: 0, 1, 2.')
    return _COMPACT_TIERS[compact_tier]


def resolve_page_size(page_size: str) -> Tuple[Length, Length]:
    key = str(page_size).strip().capitalize()
    if key not in PAGE_SIZES:
        supported = ', '.join(PAGE_SIZES)
        raise DocxRenderError(f'Unsupported page size. Supported values: {supported}.')
    return PAGE_SIZES[key]


def _style(doc, name, size, bold=False, italic=False, base='Normal'):
    try:
        style = doc.styles[name]
    except KeyError:
        style = doc.styles.add_style(name, WD_STYLE_TYPE.PARAGRAPH)
        style.base_style = doc.styles[base]
    style.font.name = FONT
    style.font.size = size
    style.font.bold = bold
    style.font.italic = italic
    style.font.color.rgb = RGBColor(0, 0, 0)
    return style


def _bottom_border(style, color='1F3B57', sz='8'):
    pPr = style.element.get_or_add_pPr()
    pBdr = OxmlElement('w:pBdr')
    bottom = OxmlElement('w:bottom')
    bottom.set(qn('w:val'), 'single')
    bottom.set(qn('w:sz'), sz)
    bottom.set(qn('w:space'), '2')
    bottom.set(qn('w:color'), color)
    pBdr.append(bottom)
    pPr.append(pBdr)


def build_styles(doc, *, compact_tier: int = 0) -> None:
    spec = resolve_compact_tier(compact_tier)
    body_size = Pt(spec['body_pt'])
    normal = doc.styles['Normal']
    normal.font.name = FONT
    normal.font.size = body_size
    normal.paragraph_format.line_spacing = spec['line_spacing']

    def paragraph_format(style):
        return style.paragraph_format

    name = _style(doc, 'Resume Name', Pt(spec['name_pt']), bold=True)
    paragraph_format(name).alignment = WD_ALIGN_PARAGRAPH.CENTER
    paragraph_format(name).space_after = Pt(0)

    title = _style(doc, 'Resume Title', Pt(spec['title_pt']), bold=True)
    paragraph_format(title).alignment = WD_ALIGN_PARAGRAPH.CENTER
    paragraph_format(title).space_before = Pt(0)
    paragraph_format(title).space_after = Pt(0.5)

    tagline = _style(doc, 'Resume Tagline', body_size, italic=True)
    paragraph_format(tagline).alignment = WD_ALIGN_PARAGRAPH.CENTER
    paragraph_format(tagline).space_after = Pt(0.5)

    contact = _style(doc, 'Resume Contact', Pt(spec['contact_pt']))
    paragraph_format(contact).alignment = WD_ALIGN_PARAGRAPH.CENTER
    paragraph_format(contact).space_after = Pt(1)
    paragraph_format(contact).line_spacing = spec['tight_line_spacing']

    heading = _style(doc, 'Resume Section Heading', Pt(spec['heading_pt']), bold=True, base='Heading 1')
    heading.font.color.rgb = HEADING_COLOR
    paragraph_format(heading).space_before = Pt(spec['section_before_pt'])
    paragraph_format(heading).space_after = Pt(spec['section_after_pt'])
    paragraph_format(heading).keep_with_next = True
    _bottom_border(heading, sz='6')

    sub = _style(doc, 'Resume Subheading', body_size, bold=True)
    paragraph_format(sub).space_before = Pt(spec['entry_before_pt'])
    paragraph_format(sub).space_after = Pt(spec['entry_after_pt'])
    paragraph_format(sub).line_spacing = spec['tight_line_spacing']
    paragraph_format(sub).keep_with_next = True

    project = _style(doc, 'Resume Project Title', body_size, bold=True)
    paragraph_format(project).space_before = Pt(spec['entry_before_pt'])
    paragraph_format(project).space_after = Pt(spec['entry_after_pt'])
    paragraph_format(project).line_spacing = spec['tight_line_spacing']
    paragraph_format(project).keep_with_next = True

    job = _style(doc, 'Resume Job Title', body_size, bold=True)
    paragraph_format(job).space_before = Pt(spec['entry_before_pt'])
    paragraph_format(job).space_after = Pt(spec['entry_after_pt'])
    paragraph_format(job).line_spacing = spec['tight_line_spacing']
    paragraph_format(job).keep_with_next = True

    meta = _style(doc, 'Resume Entry Meta', Pt(spec['meta_pt']), italic=True)
    paragraph_format(meta).space_after = Pt(spec['entry_after_pt'])
    paragraph_format(meta).line_spacing = spec['tight_line_spacing']
    paragraph_format(meta).keep_with_next = True

    body = _style(doc, 'Resume Body', body_size)
    paragraph_format(body).space_after = Pt(spec['body_after_pt'])
    paragraph_format(body).line_spacing = spec['line_spacing']

    bullet = doc.styles['List Bullet']
    bullet.font.name = FONT
    bullet.font.size = body_size
    paragraph_format(bullet).space_before = Pt(0)
    paragraph_format(bullet).space_after = Pt(spec['bullet_after_pt'])
    paragraph_format(bullet).line_spacing = spec['tight_line_spacing']


def _add(doc, text: str, style: str):
    return doc.add_paragraph(text, style=style)


def _is_link_contact(item: str) -> bool:
    stripped = item.strip()
    if '@' in stripped and not stripped.casefold().startswith(('http://', 'https://')):
        return False
    return bool(_LINK_CONTACT_PATTERN.search(stripped))


def _compact_url_display(url: str) -> str:
    cleaned = url.strip()
    cleaned = re.sub(r'^https?://(?:www\.)?', '', cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r'^www\.', '', cleaned, flags=re.IGNORECASE)
    return cleaned.rstrip('/')


def _hyperlink_target(item: str) -> Optional[str]:
    stripped = item.strip()
    if not stripped or not _is_link_contact(stripped):
        return None
    if stripped.casefold().startswith(('http://', 'https://')):
        return stripped
    return f'https://{stripped.lstrip("/")}'


def add_hyperlink(paragraph, url: str, text: str) -> None:
    '''Append an external hyperlink run to ``paragraph`` with matching contact styling.'''
    rel_id = paragraph.part.relate_to(
        url, RELATIONSHIP_TYPE.HYPERLINK, is_external=True
    )
    hyperlink = OxmlElement('w:hyperlink')
    hyperlink.set(qn('r:id'), rel_id)

    run = OxmlElement('w:r')
    r_pr = OxmlElement('w:rPr')
    r_fonts = OxmlElement('w:rFonts')
    r_fonts.set(qn('w:ascii'), FONT)
    r_fonts.set(qn('w:hAnsi'), FONT)
    r_pr.append(r_fonts)

    color = OxmlElement('w:color')
    color.set(qn('w:val'), '1F3B57')
    r_pr.append(color)

    sz = OxmlElement('w:sz')
    sz.set(qn('w:val'), '18')
    r_pr.append(sz)

    underline = OxmlElement('w:u')
    underline.set(qn('w:val'), 'single')
    r_pr.append(underline)

    run.append(r_pr)
    text_el = OxmlElement('w:t')
    text_el.text = text
    run.append(text_el)
    hyperlink.append(run)
    paragraph._p.append(hyperlink)


def _render_contact_header(doc, contact_lines: list[str]) -> None:
    items = [line.strip() for line in contact_lines if line.strip()]
    if not items:
        return
    primary = [item for item in items if not _is_link_contact(item)]
    links = [item for item in items if _is_link_contact(item)]
    if primary and links:
        groups = [primary, links]
    elif len(items) <= 3:
        groups = [items]
    else:
        midpoint = (len(items) + 1) // 2
        groups = [items[:midpoint], items[midpoint:]]
    for index, group in enumerate(groups):
        paragraph = doc.add_paragraph(style='Resume Contact')
        if index < len(groups) - 1:
            paragraph.paragraph_format.space_after = Pt(0)
        for item_index, item in enumerate(group):
            if item_index > 0:
                paragraph.add_run('  |  ')
            target_url = _hyperlink_target(item)
            if target_url is not None:
                add_hyperlink(paragraph, target_url, _compact_url_display(item))
            else:
                paragraph.add_run(item)


def _append_unique_part(parts: list[str], value: str, existing: str) -> None:
    cleaned = value.strip()
    if not cleaned:
        return
    lowered = cleaned.casefold()
    if lowered in existing.casefold():
        return
    if any(lowered == part.casefold() for part in parts):
        return
    parts.append(cleaned)


def _has_entry_content(entry: GeneratedEntry) -> bool:
    fields = (
        entry.title,
        entry.organization or '',
        entry.dates or '',
        entry.location or '',
        entry.meta or '',
    )
    return any(value.strip() for value in fields) or any(
        bullet.text.strip() for bullet in entry.bullets
    )


def _abbreviate_month_names(dates: str) -> str:
    return _FULL_MONTH_PATTERN.sub(
        lambda match: _MONTH_ABBREVIATIONS[match.group(1).casefold()],
        dates,
    )


def _estimate_header_width_pt(
    left_text: str,
    right_dates: str,
    left_pt: float,
    right_pt: float,
) -> float:
    # Conservative advance factors keep single-line headers from wrapping in
    # Calibri or wider Linux fallback fonts (Carlito / Liberation Sans).
    left_width = len(left_text) * left_pt * 0.58
    right_width = len(right_dates) * right_pt * 0.54
    gap = 18.0 if (left_text and right_dates) else 0.0
    return left_width + gap + right_width


def _fit_entry_header_line(
    left_text: str,
    right_dates: str,
    right_tab_pos: Length,
    *,
    base_pt: float = 9.0,
) -> tuple[str, Optional[Pt], Optional[Pt]]:
    if not left_text or not right_dates:
        return right_dates, None, None
    available_pt = (
        right_tab_pos.pt
        if hasattr(right_tab_pos, 'pt')
        else float(right_tab_pos) / float(Pt(1))
    )
    fitted_dates = right_dates
    if _estimate_header_width_pt(left_text, fitted_dates, base_pt, base_pt) <= available_pt * 0.94:
        return fitted_dates, None, None
    fitted_dates = _abbreviate_month_names(fitted_dates)
    if _estimate_header_width_pt(left_text, fitted_dates, base_pt, base_pt) <= available_pt * 0.94:
        return fitted_dates, None, None
    for left_pt, right_pt in ((min(base_pt, 8.5), min(base_pt, 8.5)), (min(base_pt, 8.0), min(base_pt, 8.0))):
        if _estimate_header_width_pt(left_text, fitted_dates, left_pt, right_pt) <= available_pt * 0.94:
            return fitted_dates, Pt(left_pt), Pt(right_pt)
    return fitted_dates, Pt(min(base_pt, 8.0)), Pt(min(base_pt, 8.0))


def _add_entry_header_line(
    doc,
    style: str,
    left_text: str,
    right_dates: str,
    right_tab_pos: Length,
    *,
    keep_with_next: bool = True,
    base_pt: float = 9.0,
):
    fitted_dates, left_size, right_size = _fit_entry_header_line(
        left_text, right_dates, right_tab_pos, base_pt=base_pt
    )
    paragraph = doc.add_paragraph(style=style)
    paragraph.paragraph_format.keep_with_next = keep_with_next
    if left_text:
        left_run = paragraph.add_run(left_text)
        left_run.bold = True
        if left_size is not None:
            left_run.font.size = left_size
    if fitted_dates:
        paragraph.paragraph_format.tab_stops.add_tab_stop(
            right_tab_pos, WD_TAB_ALIGNMENT.RIGHT
        )
        tab_run = paragraph.add_run(f'\t{fitted_dates}' if left_text else fitted_dates)
        tab_run.bold = False
        if right_size is not None:
            tab_run.font.size = right_size
    return paragraph


def _render_entry(
    doc,
    section_id: str,
    entry: GeneratedEntry,
    right_tab_pos: Length,
    *,
    spec: Optional[dict[str, float]] = None,
) -> None:
    active_spec = spec or _COMPACT_TIERS[0]
    base_pt = active_spec['body_pt']
    title = entry.title.strip()
    organization = (entry.organization or '').strip()
    dates = (entry.dates or '').strip()
    location = (entry.location or '').strip()
    meta = (entry.meta or '').strip()
    bullets = [bullet.text.strip() for bullet in entry.bullets if bullet.text.strip()]

    head = title
    if organization and organization.casefold() not in head.casefold():
        head = f'{head} — {organization}' if head else organization

    right_dates = dates if dates and dates.casefold() not in head.casefold() else ''
    meta_parts: list[str] = []
    for value in (location, meta):
        _append_unique_part(meta_parts, value, f'{head} {right_dates}')

    if section_id in _COMPACT_SECTIONS and not bullets:
        if head or right_dates or meta_parts:
            left_text = head
            if meta_parts:
                suffix = ' | '.join(meta_parts)
                left_text = f'{left_text} | {suffix}' if left_text else suffix
            paragraph = _add_entry_header_line(
                doc,
                'Resume Subheading',
                left_text,
                right_dates,
                right_tab_pos,
                keep_with_next=False,
                base_pt=base_pt,
            )
            paragraph.paragraph_format.space_before = Pt(active_spec['compact_before_pt'])
            paragraph.paragraph_format.space_after = Pt(active_spec['compact_after_pt'])
        return

    entry_style = _ENTRY_STYLE.get(section_id, 'Resume Subheading')
    if head or right_dates:
        _add_entry_header_line(
            doc,
            entry_style,
            head,
            right_dates,
            right_tab_pos,
            keep_with_next=bool(meta_parts or bullets),
            base_pt=base_pt,
        )
    if meta_parts:
        meta_paragraph = _add(doc, ' | '.join(meta_parts), 'Resume Entry Meta')
        if not bullets:
            meta_paragraph.paragraph_format.keep_with_next = False
    for bullet in bullets:
        _add(doc, bullet, 'List Bullet')


def _format_language_entry(entry: GeneratedEntry) -> str:
    title = entry.title.strip()
    detail_parts: list[str] = []
    for value in (entry.meta, entry.organization, entry.dates, entry.location):
        cleaned = (value or '').strip()
        if cleaned and cleaned.casefold() not in title.casefold():
            detail_parts.append(cleaned)
    bullets = [bullet.text.strip() for bullet in entry.bullets if bullet.text.strip()]
    if title.casefold() in ('languages', 'language', 'spoken languages') and bullets:
        return ' · '.join(dict.fromkeys(bullets))
    for bullet in bullets:
        if bullet.casefold() not in title.casefold():
            detail_parts.append(bullet)
    if not title:
        return ' · '.join(dict.fromkeys(detail_parts))
    if detail_parts:
        return f"{title} ({', '.join(dict.fromkeys(detail_parts))})"
    return title


def build_document(
    artifact: TailoredResumeArtifact,
    page_size: str = DEFAULT_PAGE_SIZE,
    *,
    compact_tier: int = 0,
) -> Document:
    width, height = resolve_page_size(page_size)
    spec = resolve_compact_tier(compact_tier)

    doc = Document()
    section = doc.sections[0]
    section.page_width = width
    section.page_height = height
    section.top_margin = Inches(spec['top_margin_in'])
    section.bottom_margin = Inches(spec['bottom_margin_in'])
    section.left_margin = Inches(0.5)
    section.right_margin = Inches(0.5)
    section.header_distance = Inches(0.25)
    section.footer_distance = Inches(0.25)
    right_tab_pos = section.page_width - section.left_margin - section.right_margin

    build_styles(doc, compact_tier=compact_tier)

    header = artifact.header
    content = artifact.content

    if header.full_name.strip():
        _add(doc, header.full_name.strip(), 'Resume Name')
    if content.headline_title.strip():
        _add(doc, content.headline_title.strip(), 'Resume Title')
    if content.tagline is not None and content.tagline.strip():
        _add(doc, content.tagline.strip(), 'Resume Tagline')
    _render_contact_header(doc, header.contact_lines)

    summary = [sentence.text.strip() for sentence in content.summary_sentences if sentence.text.strip()]
    if summary:
        _add(doc, 'SUMMARY', 'Resume Section Heading')
        _add(doc, ' '.join(summary), 'Resume Body')

    groups = []
    for group in content.skill_groups:
        skills = [item.skill_text.strip() for item in group.skills if item.skill_text.strip()]
        if skills:
            groups.append((group.group_name.strip(), skills))
    if groups:
        _add(doc, 'TECHNICAL SKILLS', 'Resume Section Heading')
        for group_name, skills in groups:
            paragraph = doc.add_paragraph(style='Resume Body')
            paragraph.paragraph_format.space_after = Pt(spec['skill_after_pt'])
            paragraph.paragraph_format.line_spacing = spec['tight_line_spacing']
            label_run = paragraph.add_run(f'{group_name}: ')
            label_run.bold = True
            paragraph.add_run(' · '.join(skills))

    for generated in content.sections:
        if generated.section_id in _NON_SECTION_OUTPUTS:
            continue
        entries = [entry for entry in generated.entries if _has_entry_content(entry)]
        if not entries:
            continue
        _add(doc, generated.heading_text.strip().upper(), 'Resume Section Heading')
        if generated.section_id == 'languages':
            items = [
                item for item in (_format_language_entry(entry) for entry in entries)
                if item
            ]
            if items:
                paragraph = _add(doc, ' · '.join(dict.fromkeys(items)), 'Resume Body')
                paragraph.paragraph_format.space_after = Pt(spec['body_after_pt'])
            continue
        for entry in entries:
            _render_entry(doc, generated.section_id, entry, right_tab_pos, spec=spec)

    props = doc.core_properties
    props.title = 'Tailored Resume'
    props.author = 'Resume_Assistant'
    props.comments = 'Evidence-bound tailored resume. Human review required.'
    return doc


def render_docx_bytes(
    artifact: TailoredResumeArtifact,
    page_size: str = DEFAULT_PAGE_SIZE,
    *,
    compact_tier: int = 0,
) -> bytes:
    document = build_document(artifact, page_size, compact_tier=compact_tier)
    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()


def _cover_letter_paragraphs(artifact: CoverLetterArtifact) -> list[str]:
    content = artifact.content
    return [
        content.opening_paragraph.text.strip(),
        *(paragraph.text.strip() for paragraph in content.body_paragraphs),
        content.closing_paragraph.text.strip(),
    ]


def build_cover_letter_plain_text(artifact: CoverLetterArtifact) -> str:
    '''Assemble copy-ready plain text with the local header and signature.'''
    header = artifact.header
    content = artifact.content
    lines: list[str] = []
    if header.full_name.strip():
        lines.append(header.full_name.strip())
    contacts = [line.strip() for line in header.contact_lines if line.strip()]
    if contacts:
        lines.append(' | '.join(contacts))
    if lines:
        lines.append('')

    lines.append(content.salutation.strip())
    for paragraph in _cover_letter_paragraphs(artifact):
        if not paragraph:
            continue
        lines.extend(['', paragraph])
    lines.extend(['', content.sign_off.strip()])
    if header.full_name.strip():
        lines.append(header.full_name.strip())
    return '\n'.join(lines).strip() + '\n'


def _add_cover_letter_body(doc, text: str, *, after_pt: float = 6.0):
    paragraph = doc.add_paragraph(text, style='Resume Body')
    paragraph.paragraph_format.space_after = Pt(after_pt)
    return paragraph


def build_cover_letter_document(
    artifact: CoverLetterArtifact,
    page_size: str = DEFAULT_PAGE_SIZE,
) -> Document:
    width, height = resolve_page_size(page_size)
    spec = resolve_compact_tier(0)

    doc = Document()
    section = doc.sections[0]
    section.page_width = width
    section.page_height = height
    section.top_margin = Inches(spec['top_margin_in'])
    section.bottom_margin = Inches(spec['bottom_margin_in'])
    section.left_margin = Inches(0.5)
    section.right_margin = Inches(0.5)
    section.header_distance = Inches(0.25)
    section.footer_distance = Inches(0.25)

    build_styles(doc, compact_tier=0)

    header = artifact.header
    content = artifact.content
    if header.full_name.strip():
        _add(doc, header.full_name.strip(), 'Resume Name')
    _render_contact_header(doc, header.contact_lines)
    doc.add_paragraph('', style='Resume Body')
    _add_cover_letter_body(doc, content.salutation.strip(), after_pt=10.0)
    for paragraph in _cover_letter_paragraphs(artifact):
        if paragraph:
            _add_cover_letter_body(doc, paragraph)
    _add_cover_letter_body(doc, content.sign_off.strip(), after_pt=0.0)
    if header.full_name.strip():
        _add(doc, header.full_name.strip(), 'Resume Body')

    props = doc.core_properties
    props.title = 'Cover Letter'
    props.author = 'Resume_Assistant'
    props.comments = 'Evidence-bound cover letter. Human review required.'
    return doc


def render_cover_letter_docx_bytes(
    artifact: CoverLetterArtifact,
    page_size: str = DEFAULT_PAGE_SIZE,
) -> bytes:
    document = build_cover_letter_document(artifact, page_size)
    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()


def _write_atomic_private_bytes(target_path: Path, payload: bytes) -> None:
    tmp_name = None
    fd = None
    parent = target_path.parent
    try:
        for _ in range(32):
            candidate = parent / f'.resume-pdf-{secrets.token_hex(16)}.tmp'
            try:
                fd = os.open(
                    candidate,
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | getattr(os, 'O_CLOEXEC', 0),
                    0o600,
                )
            except FileExistsError:
                continue
            tmp_name = candidate
            break
        if fd is None or tmp_name is None:
            raise OSError('Could not create a temporary PDF output file.')
        handle = os.fdopen(fd, 'wb')
        fd = None
        with handle:
            handle.write(payload)
        os.chmod(tmp_name, 0o600)
        os.replace(tmp_name, target_path)
        tmp_name = None
        os.chmod(target_path, 0o600)
    finally:
        if fd is not None:
            os.close(fd)
        if tmp_name is not None:
            try:
                tmp_name.unlink()
            except FileNotFoundError:
                pass


def render_resume_pdf(
    resume: Optional[TailoredResumeArtifact],
    output_path: Path,
    *,
    page_size: str = 'LETTER',
    docx_path: Optional[Path] = None,
    converter_bin: Optional[str] = None,
    compact_tier: int = 0,
) -> Path:
    '''Convert a tailored resume DOCX (existing or rendered) to PDF via LibreOffice.'''
    if docx_path is None:
        resolve_page_size(page_size)
        resolve_compact_tier(compact_tier)
    binary = find_pdf_converter(converter_bin)
    target_path = Path(output_path)
    target_path.parent.mkdir(parents=True, exist_ok=True)

    if docx_path is not None:
        existing_docx = Path(docx_path)
        if not existing_docx.is_file():
            raise PdfRenderError(
                'Source DOCX file for PDF conversion could not be found.'
            )
    elif resume is None:
        raise PdfRenderError(
            'TailoredResumeArtifact is required when docx_path is omitted.'
        )

    temp_root = Path(tempfile.mkdtemp(dir=target_path.parent, prefix='.resume-pdf-'))
    try:
        temp_outdir = temp_root / 'out'
        temp_outdir.mkdir()
        if docx_path is not None:
            source_docx = Path(docx_path)
        else:
            source_docx = temp_root / 'resume.docx'
            _write_atomic_private_bytes(
                source_docx,
                render_docx_bytes(resume, page_size=page_size, compact_tier=compact_tier)
            )

        # Writer is single-instance per user profile. Sharing the desktop
        # profile can hand the request to a starting/exiting process instead
        # of completing this export. The private temp tree owns both the
        # profile and the conversion, including on the very first invocation.
        command = [
            binary,
            f'-env:UserInstallation={(temp_root / "profile").as_uri()}',
            '--headless',
            '--nologo',
            '--nofirststartwizard',
            '--convert-to',
            'pdf',
            '--outdir',
            str(temp_outdir),
            str(source_docx),
        ]
        try:
            completed = subprocess.run(
                command,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                umask=0o077,
                timeout=PDF_CONVERSION_TIMEOUT_SECONDS,
                check=False,
            )
        except subprocess.TimeoutExpired:
            raise PdfRenderError('LibreOffice PDF conversion timed out.') from None
        except OSError:
            raise PdfRenderError('LibreOffice could not start the PDF conversion.') from None
        if completed.returncode != 0:
            raise PdfRenderError(
                f'LibreOffice PDF conversion failed (exit {completed.returncode}).'
            )

        produced_pdf = temp_outdir / f'{source_docx.stem}.pdf'
        if not produced_pdf.is_file():
            raise PdfRenderError('LibreOffice finished without producing a PDF.')
        pdf_bytes = produced_pdf.read_bytes()
        if not pdf_bytes.startswith(b'%PDF-'):
            raise PdfRenderError('LibreOffice did not produce a valid PDF output.')

        _write_atomic_private_bytes(target_path, pdf_bytes)
        return target_path
    finally:
        shutil.rmtree(temp_root, ignore_errors=True)


def render_cover_letter_pdf(
    cover_letter: Optional[CoverLetterArtifact],
    output_path: Path,
    *,
    page_size: str = DEFAULT_PAGE_SIZE,
    docx_path: Optional[Path] = None,
    converter_bin: Optional[str] = None,
) -> Path:
    '''Convert a cover-letter DOCX (existing or rendered) to PDF via LibreOffice.'''
    if docx_path is not None:
        return render_resume_pdf(
            None,
            output_path,
            page_size=page_size,
            docx_path=docx_path,
            converter_bin=converter_bin,
        )
    if cover_letter is None:
        raise PdfRenderError(
            'CoverLetterArtifact is required when docx_path is omitted.'
        )
    target_path = Path(output_path)
    target_path.parent.mkdir(parents=True, exist_ok=True)
    temp_root = Path(tempfile.mkdtemp(dir=target_path.parent, prefix='.cover-letter-pdf-'))
    try:
        source_docx = temp_root / 'cover_letter.docx'
        _write_atomic_private_bytes(
            source_docx,
            render_cover_letter_docx_bytes(cover_letter, page_size=page_size),
        )
        return render_resume_pdf(
            None,
            target_path,
            page_size=page_size,
            docx_path=source_docx,
            converter_bin=converter_bin,
        )
    finally:
        shutil.rmtree(temp_root, ignore_errors=True)
