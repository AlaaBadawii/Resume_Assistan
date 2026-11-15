"""Render templates/resume_template.docx to output/resume_template_preview.pdf.

Approximate visual QA only — NOT a Word-faithful renderer: paragraph styles are
mapped to CSS and rendered with WeasyPrint. Page size and margins are read from
the DOCX section so the preview matches the document's page settings, but
WeasyPrint does not verify actual Word or LibreOffice rendering. Consecutive
`List Bullet` paragraphs are grouped into <ul> lists.

Run:  python3 tests/render_template_pdf.py
"""

import html
from pathlib import Path

from docx import Document

ROOT = Path(__file__).resolve().parent.parent
TEMPLATE = ROOT / "templates" / "resume_template.docx"
OUT = ROOT / "output" / "resume_template_preview.pdf"

BODY_CSS = """
body { font-family: Calibri, Carlito, sans-serif; font-size: 10.5pt; color: #000; }
p { margin: 0 0 2pt 0; }
.Resume-Name { font-size: 20pt; font-weight: bold; text-align: center; margin: 0; }
.Resume-Title { font-size: 12.5pt; font-weight: bold; text-align: center; margin: 0 0 1pt; }
.Resume-Tagline { font-size: 10pt; font-style: italic; text-align: center; margin: 0 0 1pt; }
.Resume-Contact { font-size: 9.5pt; text-align: center; margin: 0 0 2pt; }
.Resume-Section-Heading {
  font-size: 12pt; font-weight: bold; color: #1f3b57;
  border-bottom: 0.75pt solid #1f3b57;
  margin: 10pt 0 3pt; padding-bottom: 1pt;
}
.Resume-Subheading { font-size: 10.5pt; font-weight: bold; margin: 4pt 0 1pt; }
.Resume-Project-Title, .Resume-Job-Title { font-size: 10.5pt; font-weight: bold; margin: 4pt 0 0; }
.Resume-Entry-Meta { font-size: 9.5pt; font-style: italic; margin: 0 0 1pt; }
.Resume-Body { font-size: 10.5pt; }
ul { margin: 0 0 2pt 0; padding-left: 18pt; }
li { font-size: 10.5pt; margin: 0 0 1pt 0; }
"""


def build_page_css(section) -> str:
    """Build the @page rule from the DOCX section's actual page settings."""
    width_mm = section.page_width.mm
    height_mm = section.page_height.mm
    top_mm = section.top_margin.mm
    right_mm = section.right_margin.mm
    bottom_mm = section.bottom_margin.mm
    left_mm = section.left_margin.mm
    return (
        f"@page {{ size: {width_mm:.2f}mm {height_mm:.2f}mm; "
        f"margin: {top_mm:.2f}mm {right_mm:.2f}mm {bottom_mm:.2f}mm {left_mm:.2f}mm; }}"
    )


def build_document_css(doc) -> str:
    return build_page_css(doc.sections[0]) + "\n" + BODY_CSS


def docx_to_html(doc) -> str:
    out, in_list = [], False
    for p in doc.paragraphs:
        cls = p.style.name.replace(" ", "-")
        text = html.escape(p.text)
        if p.style.name == "List Bullet":
            if not in_list:
                out.append("<ul>")
                in_list = True
            out.append(f"<li>{text}</li>")
            continue
        if in_list:
            out.append("</ul>")
            in_list = False
        out.append(f'<p class="{cls}">{text}</p>')
    if in_list:
        out.append("</ul>")
    body = "\n".join(out)
    css = build_document_css(doc)
    return f"<!DOCTYPE html><html><head><meta charset='utf-8'><style>{css}</style></head><body>{body}</body></html>"


def main(output: Path = OUT) -> Path:
    doc = Document(TEMPLATE)
    page_html = docx_to_html(doc)
    import weasyprint

    target = Path(output)
    target.parent.mkdir(parents=True, exist_ok=True)
    weasyprint.HTML(string=page_html).write_pdf(target)
    print(f"wrote {target}")
    return target


if __name__ == "__main__":
    main()
