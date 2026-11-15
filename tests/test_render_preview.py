"""Tests for the approximate PDF preview renderer (tests/render_template_pdf.py).

The renderer is approximate visual QA only — it does not verify actual Word or
LibreOffice rendering. These tests verify the output filename and that the
generated @page CSS matches the DOCX section's page dimensions and margins.
"""

import importlib.util
import re
from pathlib import Path

import docx
import pytest

ROOT = Path(__file__).resolve().parent.parent
TEMPLATE = ROOT / "templates" / "resume_template.docx"
EXPECTED_PREVIEW = ROOT / "output" / "resume_template_preview.pdf"

PAGE_RULE_RE = re.compile(
    r"@page \{ size: ([\d.]+)mm ([\d.]+)mm; "
    r"margin: ([\d.]+)mm ([\d.]+)mm ([\d.]+)mm ([\d.]+)mm; \}"
)


def _load_renderer_module():
    spec = importlib.util.spec_from_file_location(
        "render_template_pdf_under_test", ROOT / "tests" / "render_template_pdf.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_preview_output_filename():
    renderer = _load_renderer_module()
    assert renderer.OUT == EXPECTED_PREVIEW
    assert renderer.OUT.name == "resume_template_preview.pdf"
    assert "\\" not in str(renderer.OUT)
    # No stray backslash-named artifacts in output/
    strays = [p.name for p in (ROOT / "output").iterdir() if "\\" in p.name]
    assert not strays, f"Unexpected filenames in output/: {strays}"


def test_renderer_writes_intended_filename(tmp_path):
    renderer = _load_renderer_module()
    pytest.importorskip("weasyprint", reason="weasyprint not installed")
    target = tmp_path / renderer.OUT.name
    written = renderer.main(output=target)
    assert written == target
    assert target.exists()
    assert target.name == "resume_template_preview.pdf"
    assert target.stat().st_size > 0


def test_page_css_matches_docx_section():
    renderer = _load_renderer_module()
    doc = docx.Document(TEMPLATE)
    section = doc.sections[0]

    css = renderer.build_page_css(section)
    match = PAGE_RULE_RE.search(css)
    assert match, f"@page rule not found in: {css}"
    width, height, top, right, bottom, left = (float(v) for v in match.groups())

    assert abs(width - section.page_width.mm) <= 0.01
    assert abs(height - section.page_height.mm) <= 0.01
    assert abs(top - section.top_margin.mm) <= 0.01
    assert abs(right - section.right_margin.mm) <= 0.01
    assert abs(bottom - section.bottom_margin.mm) <= 0.01
    assert abs(left - section.left_margin.mm) <= 0.01

    # Default template is A4.
    assert abs(width - 210.0) <= 0.05
    assert abs(height - 297.0) <= 0.05


def test_document_html_contains_page_css():
    renderer = _load_renderer_module()
    doc = docx.Document(TEMPLATE)
    page_html = renderer.docx_to_html(doc)
    match = PAGE_RULE_RE.search(page_html)
    assert match, "Rendered HTML must embed the @page rule from the DOCX"
    width = float(match.group(1))
    assert abs(width - doc.sections[0].page_width.mm) <= 0.01
