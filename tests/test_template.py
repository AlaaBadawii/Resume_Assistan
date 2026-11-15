"""Verification for Mission 1 (refined in Mission 1.2).

Run:  python3 -m pytest tests/ -v   (or)  python3 tests/test_template.py

Checks:
1. Master Resume is byte-identical to its inspected hash (untouched).
2. Template opens; all required sections exist in order.
3. Formatting constraints: no tables, text boxes, images, header/footer content.
4. No candidate-specific content leaked into the template.
5. Placeholder conventions and named styles are consistent.
6. Section heading style derives from built-in Heading 1 (semantic heading).
7. .gitignore behavior verified with real `git check-ignore` in a temp repo.
"""

import hashlib
import os
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

import docx
import pytest
from docx.oxml.ns import qn

ROOT = Path(__file__).resolve().parent.parent
MASTER = ROOT / "data" / "private" / "Master_Resume.docx"
TEMPLATE = ROOT / "templates" / "resume_template.docx"
GITIGNORE = ROOT / ".gitignore"

# Historical sha256 observed during Mission 1 inspection (2026-11-15).
# NOT asserted any more: the Master Resume is expected to evolve legitimately.
# Retained only as historical documentation.
HISTORICAL_MASTER_SHA256 = "a27969026f578ac5badefdad029a8d6984919e43e069ba3a50cb6f99268f42a5"


def _source_sha256() -> str:
    return hashlib.sha256(MASTER.read_bytes()).hexdigest()

REQUIRED_SECTIONS = [
    "SUMMARY",
    "TECHNICAL SKILLS",
    "SELECTED PROJECTS",
    "EXPERIENCE",
    "EDUCATION",
    "CERTIFICATIONS & PROFESSIONAL DEVELOPMENT",
    "LANGUAGES",
]

REQUIRED_STYLES = [
    "Resume Name",
    "Resume Title",
    "Resume Tagline",
    "Resume Contact",
    "Resume Section Heading",
    "Resume Subheading",
    "Resume Project Title",
    "Resume Job Title",
    "Resume Entry Meta",
    "Resume Body",
    "List Bullet",
]

# Markers that must never appear in the generated template.
CANDIDATE_MARKERS = [
    "alaa", "badawii", "alaabadawii", "@gmail", "+20", "cairo", "egypt",
    "quizey", "alx", "creativa", "modern academy", "alignerr",
    "linkedin.com/in/", "github.com/", "nice ng217", "ng217",
]

PLACEHOLDER_RE = re.compile(r"\[[A-Z][A-Z0-9_]*\]")
STRAY_TOKEN_RE = re.compile(r"\[[^\]]*\]")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_master_resume_untouched():
    from resume_assistant import extractor as extractor_module

    assert MASTER.exists(), "Master Resume missing"
    extractor = extractor_module.MasterResumeExtractor(source=MASTER)
    before = _sha256(MASTER)
    snapshot = extractor.extract()
    after = _sha256(MASTER)
    assert before == after
    assert snapshot.source_sha256 == before


def test_template_opens():
    assert TEMPLATE.exists(), "Template missing — run src/build_template.py"
    doc = docx.Document(TEMPLATE)
    assert len(doc.paragraphs) > 0


def test_required_sections_present_and_ordered():
    doc = docx.Document(TEMPLATE)
    headings = [
        p.text.strip()
        for p in doc.paragraphs
        if p.style.name == "Resume Section Heading"
    ]
    assert headings == REQUIRED_SECTIONS, f"Section headings mismatch: {headings}"


def test_required_styles_exist():
    doc = docx.Document(TEMPLATE)
    names = {s.name for s in doc.styles}
    missing = [s for s in REQUIRED_STYLES if s not in names]
    assert not missing, f"Missing styles: {missing}"


def test_section_heading_semantic_style():
    """`Resume Section Heading` must derive from built-in Heading 1 so the
    document has a real semantic heading hierarchy (outline level), not just
    a visually heading-like custom style based on Normal."""
    doc = docx.Document(TEMPLATE)
    style = doc.styles["Resume Section Heading"]
    assert style.base_style is not None, "Section heading must have a base style"
    assert style.base_style.name == "Heading 1", (
        f"Section heading must derive from Heading 1, got {style.base_style.name!r}"
    )
    # Built-in Heading 1 carries outline level 0 (top-level heading semantics).
    heading_1_pPr = style.base_style.element.find(qn("w:pPr"))
    outline = (
        heading_1_pPr.find(qn("w:outlineLvl")) if heading_1_pPr is not None else None
    )
    assert outline is not None, "Heading 1 must declare an outline level"
    assert outline.get(qn("w:val")) == "0", "Heading 1 must be outline level 0"


def test_ats_formatting_constraints():
    doc = docx.Document(TEMPLATE)

    assert len(doc.tables) == 0, "Template must not contain tables"

    xml = doc.element.xml
    assert "w:txbxContent" not in xml, "Template must not contain text boxes"
    assert "<w:drawing" not in xml and "<w:pict" not in xml, (
        "Template must not contain drawings/pictures"
    )

    section = doc.sections[0]
    assert not any(p.text.strip() for p in section.header.paragraphs), (
        "Header must be empty"
    )
    assert not any(p.text.strip() for p in section.footer.paragraphs), (
        "Footer must be empty"
    )

    with zipfile.ZipFile(TEMPLATE) as zf:
        media = [n for n in zf.namelist() if n.startswith("word/media/")]
    assert not media, f"Embedded media found: {media}"


def test_no_candidate_specific_content():
    doc = docx.Document(TEMPLATE)
    text = "\n".join(p.text for p in doc.paragraphs).lower()
    leaks = [m for m in CANDIDATE_MARKERS if m in text]
    assert not leaks, f"Candidate-specific markers found: {leaks}"


def test_placeholder_convention():
    doc = docx.Document(TEMPLATE)
    placeholders = []
    for p in doc.paragraphs:
        for token in STRAY_TOKEN_RE.findall(p.text):
            assert PLACEHOLDER_RE.fullmatch(token), (
                f"Malformed placeholder {token!r} in paragraph: {p.text!r}"
            )
            placeholders.append(token)
    assert len(placeholders) >= 20, "Too few placeholders for a usable template"


def test_no_tables_or_textboxes_in_package():
    with zipfile.ZipFile(TEMPLATE) as zf:
        doc_xml = zf.read("word/document.xml").decode()
    assert "<w:tbl>" not in doc_xml, "Raw package check: table element found"


def test_gitignore_patterns_present():
    content = GITIGNORE.read_text()
    required_patterns = [
        "data/private/",
        "output/*",
        ".env",
        ".venv/",
        "__pycache__/",
    ]
    for pat in required_patterns:
        assert pat in content, f".gitignore missing pattern: {pat}"


def _git_check_ignore(repo: Path, rel_path: str, env: dict) -> bool:
    """Return True if `rel_path` is excluded per real Git semantics."""
    result = subprocess.run(
        ["git", "check-ignore", "-q", "--", rel_path],
        cwd=repo,
        capture_output=True,
        text=True,
        env=env,
    )
    if result.returncode not in (0, 1):
        raise AssertionError(
            f"git check-ignore failed for {rel_path!r}: {result.stderr.strip()}"
        )
    return result.returncode == 0


def test_gitignore_real_git_semantics():
    """Verify .gitignore behavior with the real `git check-ignore` command in
    an isolated temporary repository (Git is never initialized in the project)."""
    if shutil.which("git") is None:
        pytest.skip("git executable is not available")

    ignored_paths = [
        "data/private/Master_Resume.docx",
        "data/private/processed/candidate_evidence.json",
        "output/Resume_JobX.pdf",
        "output/resume_template_preview.pdf",
        ".env",
        ".venv/lib/python3.12/site.py",
        "src/__pycache__/build_template.cpython-312.pyc",
    ]
    trackable_paths = [
        "src/build_template.py",
        "src/resume_assistant/extractor.py",
        "templates/resume_template.docx",
        "README.md",
        "output/.gitkeep",
    ]

    env = {
        **os.environ,
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_SYSTEM": os.devnull,
        "GIT_TERMINAL_PROMPT": "0",
    }

    with tempfile.TemporaryDirectory(prefix="gitignore_check_") as tmp:
        repo = Path(tmp)
        shutil.copy(GITIGNORE, repo / ".gitignore")
        subprocess.run(
            ["git", "init", "-q"], cwd=repo, check=True, capture_output=True, env=env
        )

        for rel in ignored_paths + trackable_paths:
            target = repo / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("")

        for rel in ignored_paths:
            assert _git_check_ignore(repo, rel, env), f"{rel} should be ignored"
        for rel in trackable_paths:
            assert not _git_check_ignore(repo, rel, env), (
                f"{rel} should remain trackable"
            )


def main():
    tests = [(k, v) for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for name, fn in tests:
        try:
            fn()
            print(f"PASS  {name}")
        except pytest.skip.Exception as exc:
            print(f"SKIP  {name}: {exc}")
        except AssertionError as exc:
            failed += 1
            print(f"FAIL  {name}: {exc}")
    print(f"\n{len(tests) - failed}/{len(tests)} checks passed")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
