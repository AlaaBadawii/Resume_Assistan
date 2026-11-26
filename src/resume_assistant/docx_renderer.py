'''ATS-safe single-column DOCX rendering for a tailored resume artifact.

Styling mirrors ``src/build_template.py``: Calibri, named paragraph styles,
``#1F3B57`` section headings with a bottom rule, no tables, no images, and no
Word header/footer content. Rendering returns bytes; the CLI writes them
through the pinned private-output boundary with mode 0600.
'''

import io
from typing import Tuple

from docx import Document
from docx.enum.style import WD_STYLE_TYPE
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Inches, Length, Mm, Pt, RGBColor

from resume_assistant.generation_models import TailoredResumeArtifact

FONT = 'Calibri'
BODY_SIZE = Pt(10.5)
HEADING_COLOR = RGBColor(0x1F, 0x3B, 0x57)

PAGE_SIZES: dict[str, Tuple[Length, Length]] = {
    'A4': (Mm(210), Mm(297)),
    'Letter': (Inches(8.5), Inches(11)),
}
DEFAULT_PAGE_SIZE = 'A4'

_ENTRY_STYLE = {
    'professional-experience': 'Resume Job Title',
    'projects': 'Resume Project Title',
    'education': 'Resume Subheading',
    'certifications': 'Resume Subheading',
}
_NON_SECTION_OUTPUTS = frozenset({'professional-summary', 'technical-skills'})


class DocxRenderError(ValueError):
    '''Controlled, content-free DOCX rendering error.'''


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
    contact_lines = [line.strip() for line in header.contact_lines if line.strip()]
    if contact_lines:
        _add(doc, '  |  '.join(contact_lines), 'Resume Contact')

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
        entries = []
        for entry in generated.entries:
            title = entry.title.strip()
            meta = entry.meta.strip() if entry.meta is not None else ''
            bullets = [bullet.text.strip() for bullet in entry.bullets if bullet.text.strip()]
            if title or meta or bullets:
                entries.append((title, meta, bullets))
        if not entries:
            continue
        _add(doc, generated.heading_text.strip(), 'Resume Section Heading')
        entry_style = _ENTRY_STYLE.get(generated.section_id, 'Resume Subheading')
        for title, meta, bullets in entries:
            if title:
                _add(doc, title, entry_style)
            if meta:
                _add(doc, meta, 'Resume Entry Meta')
            for bullet in bullets:
                _add(doc, bullet, 'List Bullet')

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
