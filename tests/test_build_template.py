"""Tests for the resume template generator (src/build_template.py).

Covers: fresh generation in a temp directory, required sections/order,
semantic heading style relationship, ATS formatting constraints, page sizes.
"""

import importlib.util
from pathlib import Path

import docx
import pytest

ROOT = Path(__file__).resolve().parent.parent
REQUIRED_SECTIONS = [
    "SUMMARY",
    "TECHNICAL SKILLS",
    "SELECTED PROJECTS",
    "EXPERIENCE",
    "EDUCATION",
    "CERTIFICATIONS & PROFESSIONAL DEVELOPMENT",
    "LANGUAGES",
]

A4_WIDTH_MM = 210.0
A4_HEIGHT_MM = 297.0
LETTER_WIDTH_IN = 8.5
LETTER_HEIGHT_IN = 11.0
MM_TOLERANCE = 0.2
IN_TOLERANCE = 0.02


def _load_builder_module():
    spec = importlib.util.spec_from_file_location(
        "build_template_under_test", ROOT / "src" / "build_template.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _assert_ats_constraints(document):
    assert len(document.tables) == 0, "Template must not contain tables"
    xml = document.element.xml
    assert "w:txbxContent" not in xml, "Template must not contain text boxes"
    assert "<w:drawing" not in xml and "<w:pict" not in xml, (
        "Template must not contain drawings/pictures"
    )
    section = document.sections[0]
    assert not any(p.text.strip() for p in section.header.paragraphs), (
        "Header must be empty"
    )
    assert not any(p.text.strip() for p in section.footer.paragraphs), (
        "Footer must be empty"
    )


def test_build_template_generates_fresh_docx_at_supplied_path(tmp_path):
    builder = _load_builder_module()
    output = tmp_path / "resume_template_generated.docx"
    returned = builder.build(output)
    assert returned == output
    assert output.exists()
    document = docx.Document(str(output))
    headings = [
        paragraph.text.strip()
        for paragraph in document.paragraphs
        if paragraph.style.name == "Resume Section Heading"
    ]
    assert headings == REQUIRED_SECTIONS

    # Semantic style relationship: custom heading derives from built-in Heading 1.
    heading_style = document.styles["Resume Section Heading"]
    assert heading_style.base_style is not None
    assert heading_style.base_style.name == "Heading 1"

    # ATS formatting constraints on the freshly generated document.
    _assert_ats_constraints(document)


def test_build_default_page_size_is_a4(tmp_path):
    builder = _load_builder_module()
    output = tmp_path / "resume_template_a4.docx"
    builder.build(output)
    document = docx.Document(str(output))
    section = document.sections[0]
    assert abs(section.page_width.mm - A4_WIDTH_MM) <= MM_TOLERANCE
    assert abs(section.page_height.mm - A4_HEIGHT_MM) <= MM_TOLERANCE


def test_build_letter_page_size(tmp_path):
    builder = _load_builder_module()
    output = tmp_path / "resume_template_letter.docx"
    builder.build(output, page_size="Letter")
    document = docx.Document(str(output))
    section = document.sections[0]
    assert abs(section.page_width.inches - LETTER_WIDTH_IN) <= IN_TOLERANCE
    assert abs(section.page_height.inches - LETTER_HEIGHT_IN) <= IN_TOLERANCE


def test_build_rejects_unsupported_page_size(tmp_path):
    builder = _load_builder_module()
    output = tmp_path / "resume_template_bad.docx"
    with pytest.raises(ValueError) as excinfo:
        builder.build(output, page_size="A5")
    message = str(excinfo.value)
    assert "A5" in message
    assert "A4" in message and "Letter" in message
    assert not output.exists()
