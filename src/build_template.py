"""Generate templates/resume_template.docx — the blank resume template.

Run:  python3 src/build_template.py [--page-size A4|Letter]

Design rules (see docs/template_design.md):
- Single column, no tables/text boxes/images, no header/footer content.
- All structure expressed through named Word paragraph styles.
- Placeholders are UPPER_SNAKE_CASE tokens in square brackets: [TOKEN_NAME].
- Default page size is A4 (210 x 297 mm); Letter is also supported.
"""

from pathlib import Path
from typing import Optional, Tuple

from docx import Document
from docx.enum.style import WD_STYLE_TYPE
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Inches, Mm, Pt, RGBColor, Length

ROOT = Path(__file__).resolve().parent.parent
OUTPUT = ROOT / "templates" / "resume_template.docx"

FONT = "Calibri"
BODY_SIZE = Pt(10.5)
HEADING_COLOR = RGBColor(0x1F, 0x3B, 0x57)

# Supported page sizes: A4 (210 x 297 mm, default — primary target is Egypt)
# and Letter (8.5 x 11 in).
PAGE_SIZES: dict[str, Tuple[Length, Length]] = {
    "A4": (Mm(210), Mm(297)),
    "Letter": (Inches(8.5), Inches(11)),
}
DEFAULT_PAGE_SIZE = "A4"


def resolve_page_size(page_size: str) -> Tuple[Length, Length]:
    """Return (width, height) for a supported page size, or raise ValueError."""
    key = str(page_size).strip().capitalize()
    if key not in PAGE_SIZES:
        supported = ", ".join(PAGE_SIZES)
        raise ValueError(
            f"Unsupported page size {page_size!r}. Supported values: {supported}."
        )
    return PAGE_SIZES[key]


def _style(doc, name, size, bold=False, italic=False, base="Normal"):
    """Create (or fetch) a paragraph style derived from `base`."""
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


def _bottom_border(style, color="1F3B57", sz="8"):
    """Thin bottom rule under section headings (paragraph border, ATS-safe)."""
    pPr = style.element.get_or_add_pPr()
    pBdr = OxmlElement("w:pBdr")
    bottom = OxmlElement("w:bottom")
    bottom.set(qn("w:val"), "single")
    bottom.set(qn("w:sz"), sz)
    bottom.set(qn("w:space"), "2")
    bottom.set(qn("w:color"), color)
    pBdr.append(bottom)
    pPr.append(pBdr)


def build_styles(doc):
    normal = doc.styles["Normal"]
    normal.font.name = FONT
    normal.font.size = BODY_SIZE

    pf = lambda s: s.paragraph_format  # noqa: E731

    name = _style(doc, "Resume Name", Pt(20), bold=True)
    pf(name).alignment = WD_ALIGN_PARAGRAPH.CENTER
    pf(name).space_after = Pt(0)

    title = _style(doc, "Resume Title", Pt(12.5), bold=True)
    pf(title).alignment = WD_ALIGN_PARAGRAPH.CENTER
    pf(title).space_before = Pt(0)
    pf(title).space_after = Pt(1)

    tagline = _style(doc, "Resume Tagline", Pt(10), italic=True)
    pf(tagline).alignment = WD_ALIGN_PARAGRAPH.CENTER
    pf(tagline).space_after = Pt(1)

    contact = _style(doc, "Resume Contact", Pt(9.5))
    pf(contact).alignment = WD_ALIGN_PARAGRAPH.CENTER
    pf(contact).space_after = Pt(2)

    heading = _style(doc, "Resume Section Heading", Pt(12), bold=True, base="Heading 1")
    heading.font.color.rgb = HEADING_COLOR
    pf(heading).space_before = Pt(10)
    pf(heading).space_after = Pt(3)
    pf(heading).keep_with_next = True
    _bottom_border(heading)

    sub = _style(doc, "Resume Subheading", Pt(10.5), bold=True)
    pf(sub).space_before = Pt(4)
    pf(sub).space_after = Pt(1)
    pf(sub).keep_with_next = True

    project = _style(doc, "Resume Project Title", Pt(10.5), bold=True)
    pf(project).space_before = Pt(4)
    pf(project).space_after = Pt(0)
    pf(project).keep_with_next = True

    job = _style(doc, "Resume Job Title", Pt(10.5), bold=True)
    pf(job).space_before = Pt(4)
    pf(job).space_after = Pt(0)
    pf(job).keep_with_next = True

    meta = _style(doc, "Resume Entry Meta", Pt(9.5), italic=True)
    pf(meta).space_after = Pt(1)
    pf(meta).keep_with_next = True

    body = _style(doc, "Resume Body", BODY_SIZE)
    pf(body).space_after = Pt(2)

    bullet = doc.styles["List Bullet"]
    bullet.font.name = FONT
    bullet.font.size = BODY_SIZE
    pf(bullet).space_after = Pt(1)


def add_p(doc, text, style):
    return doc.add_paragraph(text, style=style)


def build(output_path: Optional[Path] = None, page_size: str = DEFAULT_PAGE_SIZE) -> Path:
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

    # --- Header block (body text, not Word header — ATS rule) ---
    add_p(doc, "[CANDIDATE_NAME]", "Resume Name")
    add_p(doc, "[TARGET_PROFESSIONAL_TITLE]", "Resume Title")
    add_p(doc, "[SPECIALTY_FOCUS]  |  [KEY_TECH_1]  |  [KEY_TECH_2]  |  [KEY_TECH_3]", "Resume Tagline")
    add_p(
        doc,
        "[PHONE]  |  [EMAIL]  |  [PORTFOLIO_URL]  |  [LINKEDIN_URL]  |  [GITHUB_URL]  |  [LOCATION]",
        "Resume Contact",
    )

    # --- 1. Summary ---
    add_p(doc, "SUMMARY", "Resume Section Heading")
    add_p(doc, "[SUMMARY_PARAGRAPH]", "Resume Body")

    # --- 2. Technical Skills ---
    add_p(doc, "TECHNICAL SKILLS", "Resume Section Heading")
    add_p(doc, "[SKILL_CATEGORY_1]", "Resume Subheading")
    add_p(doc, "[SKILL_LIST_1]", "Resume Body")
    add_p(doc, "[SKILL_CATEGORY_2]", "Resume Subheading")
    add_p(doc, "[SKILL_LIST_2]", "Resume Body")
    add_p(doc, "[SKILL_CATEGORY_3]", "Resume Subheading")
    add_p(doc, "[SKILL_LIST_3]", "Resume Body")

    # --- 3. Selected Projects ---
    add_p(doc, "SELECTED PROJECTS", "Resume Section Heading")
    add_p(doc, "[PROJECT_TITLE_1]", "Resume Project Title")
    add_p(doc, "[PROJECT_META_1]", "Resume Entry Meta")
    add_p(doc, "[PROJECT_BULLET_1]", "List Bullet")
    add_p(doc, "[PROJECT_BULLET_2]", "List Bullet")
    add_p(doc, "[PROJECT_TITLE_2]", "Resume Project Title")
    add_p(doc, "[PROJECT_META_2]", "Resume Entry Meta")
    add_p(doc, "[PROJECT_BULLET_3]", "List Bullet")
    add_p(doc, "[PROJECT_BULLET_4]", "List Bullet")

    # --- 4. Experience ---
    add_p(doc, "EXPERIENCE", "Resume Section Heading")
    add_p(doc, "[JOB_TITLE_1]", "Resume Job Title")
    add_p(doc, "[ORGANIZATION_1]  |  [EXPERIENCE_META_1]", "Resume Entry Meta")
    add_p(doc, "[EXPERIENCE_BULLET_1]", "List Bullet")
    add_p(doc, "[EXPERIENCE_BULLET_2]", "List Bullet")
    add_p(doc, "[JOB_TITLE_2]", "Resume Job Title")
    add_p(doc, "[ORGANIZATION_2]  |  [EXPERIENCE_META_2]", "Resume Entry Meta")
    add_p(doc, "[EXPERIENCE_BULLET_3]", "List Bullet")
    add_p(doc, "[EXPERIENCE_BULLET_4]", "List Bullet")

    # --- 5. Education ---
    add_p(doc, "EDUCATION", "Resume Section Heading")
    add_p(doc, "[DEGREE_AND_FIELD]", "Resume Job Title")
    add_p(doc, "[INSTITUTION]  |  [EDUCATION_META]", "Resume Entry Meta")
    add_p(doc, "[EDUCATION_DETAIL]", "List Bullet")

    # --- 6. Certifications & Professional Development ---
    add_p(doc, "CERTIFICATIONS & PROFESSIONAL DEVELOPMENT", "Resume Section Heading")
    add_p(doc, "[CERTIFICATION_TITLE_1] — [ISSUER_1] ([DATE_OR_STATUS_1])", "Resume Body")
    add_p(doc, "[CERTIFICATION_TITLE_2] — [ISSUER_2] ([DATE_OR_STATUS_2])", "Resume Body")

    # --- 7. Languages ---
    add_p(doc, "LANGUAGES", "Resume Section Heading")
    add_p(doc, "[LANGUAGE_1] — [LEVEL_1]", "Resume Body")
    add_p(doc, "[LANGUAGE_2] — [LEVEL_2]", "Resume Body")

    props = doc.core_properties
    props.title = "Resume Template"
    props.author = "Resume_Assistant"
    props.comments = "Blank ATS-friendly resume template. Placeholders use [UPPER_SNAKE_CASE]."

    target = Path(output_path) if output_path is not None else OUTPUT
    target.parent.mkdir(parents=True, exist_ok=True)
    doc.save(target)
    print(f"wrote {target}")
    return target


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Generate the blank resume template.")
    parser.add_argument(
        "--page-size",
        default=DEFAULT_PAGE_SIZE,
        choices=sorted(PAGE_SIZES),
        help=f"page size (default: {DEFAULT_PAGE_SIZE})",
    )
    args = parser.parse_args()
    build(page_size=args.page_size)
