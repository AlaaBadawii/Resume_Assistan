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
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.opc.constants import RELATIONSHIP_TYPE
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Inches, Length, Mm, Pt, RGBColor

from resume_assistant.generation_models import GeneratedEntry, TailoredResumeArtifact

TailoredResume = TailoredResumeArtifact

FONT = 'Calibri'
BODY_SIZE = Pt(10.5)
HEADING_COLOR = RGBColor(0x1F, 0x3B, 0x57)

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


def build_styles(doc) -> None:
    normal = doc.styles['Normal']
    normal.font.name = FONT
    normal.font.size = BODY_SIZE

    def paragraph_format(style):
        return style.paragraph_format

    name = _style(doc, 'Resume Name', Pt(20), bold=True)
    paragraph_format(name).alignment = WD_ALIGN_PARAGRAPH.CENTER
    paragraph_format(name).space_after = Pt(0)

    title = _style(doc, 'Resume Title', Pt(12.5), bold=True)
    paragraph_format(title).alignment = WD_ALIGN_PARAGRAPH.CENTER
    paragraph_format(title).space_before = Pt(0)
    paragraph_format(title).space_after = Pt(1)

    tagline = _style(doc, 'Resume Tagline', Pt(10), italic=True)
    paragraph_format(tagline).alignment = WD_ALIGN_PARAGRAPH.CENTER
    paragraph_format(tagline).space_after = Pt(1)

    contact = _style(doc, 'Resume Contact', Pt(9.5))
    paragraph_format(contact).alignment = WD_ALIGN_PARAGRAPH.CENTER
    paragraph_format(contact).space_after = Pt(2)

    heading = _style(doc, 'Resume Section Heading', Pt(12), bold=True, base='Heading 1')
    heading.font.color.rgb = HEADING_COLOR
    paragraph_format(heading).space_before = Pt(10)
    paragraph_format(heading).space_after = Pt(3)
    paragraph_format(heading).keep_with_next = True
    _bottom_border(heading)

    sub = _style(doc, 'Resume Subheading', Pt(10.5), bold=True)
    paragraph_format(sub).space_before = Pt(4)
    paragraph_format(sub).space_after = Pt(1)
    paragraph_format(sub).keep_with_next = True

    project = _style(doc, 'Resume Project Title', Pt(10.5), bold=True)
    paragraph_format(project).space_before = Pt(4)
    paragraph_format(project).space_after = Pt(0)
    paragraph_format(project).keep_with_next = True

    job = _style(doc, 'Resume Job Title', Pt(10.5), bold=True)
    paragraph_format(job).space_before = Pt(4)
    paragraph_format(job).space_after = Pt(0)
    paragraph_format(job).keep_with_next = True

    meta = _style(doc, 'Resume Entry Meta', Pt(9.5), italic=True)
    paragraph_format(meta).space_after = Pt(1)
    paragraph_format(meta).keep_with_next = True

    body = _style(doc, 'Resume Body', BODY_SIZE)
    paragraph_format(body).space_after = Pt(2)

    bullet = doc.styles['List Bullet']
    bullet.font.name = FONT
    bullet.font.size = BODY_SIZE
    paragraph_format(bullet).space_after = Pt(1)


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
    sz.set(qn('w:val'), '19')
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
        entry.tech_stack or '',
        entry.meta or '',
    )
    return any(value.strip() for value in fields) or any(
        bullet.text.strip() for bullet in entry.bullets
    )


def _render_entry(doc, section_id: str, entry: GeneratedEntry) -> None:
    title = entry.title.strip()
    organization = (entry.organization or '').strip()
    dates = (entry.dates or '').strip()
    location = (entry.location or '').strip()
    tech_stack = (entry.tech_stack or '').strip()
    meta = (entry.meta or '').strip()
    bullets = [bullet.text.strip() for bullet in entry.bullets if bullet.text.strip()]

    if section_id == 'projects':
        head = title
        if tech_stack and tech_stack.casefold() not in head.casefold():
            head = f'{head} | {tech_stack}' if head else tech_stack
        meta_parts: list[str] = []
        for value in (organization, location, dates, meta):
            _append_unique_part(meta_parts, value, head)
        title_paragraph = _add(doc, head, 'Resume Project Title') if head else None
        if meta_parts:
            meta_paragraph = _add(doc, ' | '.join(meta_parts), 'Resume Entry Meta')
            if not bullets:
                meta_paragraph.paragraph_format.keep_with_next = False
        elif title_paragraph is not None and not bullets:
            title_paragraph.paragraph_format.keep_with_next = False
        for bullet in bullets:
            _add(doc, bullet, 'List Bullet')
        return

    head = title
    if organization and organization.casefold() not in head.casefold():
        head = f'{head} — {organization}' if head else organization
    meta_parts = []
    for value in (location, dates, tech_stack, meta):
        _append_unique_part(meta_parts, value, head)

    if section_id in _COMPACT_SECTIONS and not bullets:
        if head or meta_parts:
            paragraph = doc.add_paragraph(style='Resume Subheading')
            paragraph.paragraph_format.keep_with_next = False
            paragraph.paragraph_format.space_after = Pt(2)
            if head:
                head_run = paragraph.add_run(head)
                head_run.bold = True
            if meta_parts:
                suffix = ' | '.join(meta_parts)
                tail_run = paragraph.add_run(f' | {suffix}' if head else suffix)
                tail_run.bold = False
        return

    entry_style = _ENTRY_STYLE.get(section_id, 'Resume Subheading')
    title_paragraph = _add(doc, head, entry_style) if head else None
    if meta_parts:
        meta_paragraph = _add(doc, ' | '.join(meta_parts), 'Resume Entry Meta')
        if not bullets:
            meta_paragraph.paragraph_format.keep_with_next = False
    elif title_paragraph is not None and not bullets:
        title_paragraph.paragraph_format.keep_with_next = False
    for bullet in bullets:
        _add(doc, bullet, 'List Bullet')


def build_document(
    artifact: TailoredResumeArtifact, page_size: str = DEFAULT_PAGE_SIZE
) -> Document:
    width, height = resolve_page_size(page_size)

    doc = Document()
    section = doc.sections[0]
    section.page_width = width
    section.page_height = height
    section.top_margin = Inches(0.7)
    section.bottom_margin = Inches(0.6)
    section.left_margin = Inches(0.8)
    section.right_margin = Inches(0.8)
    section.header_distance = Inches(0.3)
    section.footer_distance = Inches(0.3)

    build_styles(doc)

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
            _add(doc, group_name, 'Resume Subheading')
            _add(doc, ', '.join(skills), 'Resume Body')

    for generated in content.sections:
        if generated.section_id in _NON_SECTION_OUTPUTS:
            continue
        entries = [entry for entry in generated.entries if _has_entry_content(entry)]
        if not entries:
            continue
        _add(doc, generated.heading_text.strip().upper(), 'Resume Section Heading')
        for entry in entries:
            _render_entry(doc, generated.section_id, entry)

    props = doc.core_properties
    props.title = 'Tailored Resume'
    props.author = 'Resume_Assistant'
    props.comments = 'Evidence-bound tailored resume. Human review required.'
    return doc


def render_docx_bytes(
    artifact: TailoredResumeArtifact, page_size: str = DEFAULT_PAGE_SIZE
) -> bytes:
    document = build_document(artifact, page_size)
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
) -> Path:
    '''Convert a tailored resume DOCX (existing or rendered) to PDF via LibreOffice.'''
    if docx_path is None:
        resolve_page_size(page_size)
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
            source_docx.write_bytes(render_docx_bytes(resume, page_size=page_size))

        command = [
            binary,
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
                capture_output=True,
                text=True,
                timeout=PDF_CONVERSION_TIMEOUT_SECONDS,
                check=False,
            )
        except (subprocess.TimeoutExpired, OSError):
            raise PdfRenderError(
                'PDF conversion with LibreOffice failed or timed out.'
            ) from None
        if completed.returncode != 0:
            raise PdfRenderError(
                f'LibreOffice PDF conversion failed (exit {completed.returncode}).'
            )

        produced_pdf = temp_outdir / f'{source_docx.stem}.pdf'
        if not produced_pdf.is_file() or produced_pdf.stat().st_size == 0:
            raise PdfRenderError('LibreOffice did not produce a valid PDF output.')

        _write_atomic_private_bytes(target_path, produced_pdf.read_bytes())
        return target_path
    finally:
        shutil.rmtree(temp_root, ignore_errors=True)
