# Resume_Assistant

Local Python project that will eventually take a Job Description and generate
a customized, ATS-friendly PDF resume using verified information from a
private Master Resume.

**Status: Missions 1, 1.1, 1.2, 2, and 1.3 implemented — inspection,
foundations, A4-configurable template with semantic headings, isolated
Python 3.12 environment, deterministic extraction layer, and Git/handoff
infrastructure. Mission 2 is NOT approved (extraction defects found in
code review; Mission 2.1 remediation is next). No Job Description analysis
or resume generation exists yet. See `docs/PROJECT_STATUS.md`.**

## Project structure

```text
Resume_Assistant/
├── data/
│   └── private/            # PRIVATE — never committed (see .gitignore)
│       ├── Master_Resume.docx   # authoritative candidate information
│       └── processed/       # PRIVATE — generated extraction artifact
│           └── candidate_evidence.json
├── templates/
│   └── resume_template.docx # blank, ATS-friendly template (generated)
├── prompts/                 # reserved for future prompt assets
├── src/
│   ├── build_template.py    # regenerates templates/resume_template.docx
│   └── resume_assistant/    # extraction package
│       ├── __init__.py
│       ├── models.py        # Pydantic v2 data models
│       ├── extractor.py     # deterministic DOCX extraction
│       ├── section_detector.py
│       ├── evidence.py      # evidence catalog builder
│       ├── cli.py           # python -m resume_assistant extract-master
│       └── __main__.py
├── tests/
│   ├── test_template.py     # template verification suite
│   ├── test_extractor.py    # extraction tests (synthetic fixtures)
│   ├── test_evidence.py
│   ├── test_section_detector.py
│   ├── test_build_template.py
│   ├── test_render_preview.py
│   ├── render_template_pdf.py  # DOCX → PDF preview for visual QA
│   └── fixtures/           # synthetic DOCX builders
├── output/                  # generated resumes/previews — never committed
├── docs/
│   ├── template_design.md   # template design decisions & conventions
│   ├── extraction.md        # extraction architecture & limitations
│   └── PROJECT_STATUS.md    # persistent handoff / progress tracking
├── pyproject.toml           # metadata + dependencies (see Setup)
├── .venv/                   # local virtual environment — never committed
├── .gitignore
├── AGENTS.md                # shared engineering instructions for all agents
└── README.md
```

## Requirements

- **Python 3.12** (`requires-python = ">=3.12"`; verified with 3.12.14).
  Python 3.10 is end-of-life and is not a supported target.

## Setup (one-time)

```bash
python3.12 -m venv .venv
source .venv/bin/activate                 # Windows: .venv\Scripts\activate
python -m pip install -e ".[dev]"         # runtime + test dependencies
```

Dependency groups (declared in `pyproject.toml`):

| Install command | Installs |
|---|---|
| `python -m pip install -e .` | runtime: `python-docx`, `pydantic` |
| `python -m pip install -e ".[dev]"` | + `pytest` (test suite) |
| `python -m pip install -e ".[dev,preview]"` | + `weasyprint` (PDF preview) |

Never install project dependencies into the system Python; `.venv/` is
excluded from Git by `.gitignore`.

## Commands (run inside the virtual environment)

```bash
source .venv/bin/activate

python -m pytest -v                        # run the test suite
python src/build_template.py               # regenerate the template (A4 default)
python src/build_template.py --page-size Letter   # regenerate as US Letter
python tests/render_template_pdf.py        # optional: output/resume_template_preview.pdf
python -m pip check                        # verify dependency consistency
python -m resume_assistant extract-master    # extract Master Resume evidence
```

Dependencies: `python-docx` and `pydantic` (runtime), `pytest` (dev/test),
`weasyprint` (optional preview). No other third-party packages are required.

## Current implementation (Mission 2)

- `src/resume_assistant/` package: models, extractor, section detector, evidence
  builder, CLI.
- `python -m resume_assistant extract-master` writes
  `data/private/processed/candidate_evidence.json` and prints aggregate extraction
  statistics only.
- Architecture and limitations: `docs/extraction.md`.
- Mission 2 is **not approved** — known defects are tracked in
  `docs/PROJECT_STATUS.md` §D.

## Development environments and repository workflow

Multiple coding environments and AI agents (OpenCode, Google Antigravity)
work on this same repository. The shared rules live in **`AGENTS.md`** at the
project root; progress and the current stopping point live in
**`docs/PROJECT_STATUS.md`**. Read both before starting any task.

**Supported environments**

- **OpenCode / CLI agents:** run from the repository root; use `.venv/` for
  all Python commands.
- **Google Antigravity:** open the *existing* `Resume_Assistant` directory
  as the project folder — do **not** create a second copy of the project.
  This project assumes no Antigravity-specific settings; agents must not
  modify personal Antigravity or Google account settings and must not
  request or handle Google account credentials.

**Working-tree rules**

- Before starting work, every agent must inspect `git status` and read
  `AGENTS.md` + `docs/PROJECT_STATUS.md`.
- Only one agent should modify the working tree at a time — avoid
  concurrent modifications of the same checkout.
- If parallel implementation is ever required, use separate Git worktrees
  (e.g. `git worktree add ../Resume_Assistant-<task> <branch>`) rather than
  duplicating the project directory.
- Never commit or push without explicit human approval (`AGENTS.md` §F).

## Privacy rules

- `data/private/` (including the Master Resume) is ignored by git — never
  commit or quote its full contents.
- Everything under `output/` (generated personal resumes) is ignored.
- `.env` and virtual environments are ignored.
- The template contains only `[PLACEHOLDER]` tokens, never real candidate
  data.

## Conventions

- Placeholders: `[UPPER_SNAKE_CASE]` tokens (see `docs/template_design.md`).
- Template structure is defined by named Word paragraph styles
  (`Resume Section Heading`, `Resume Project Title`, `List Bullet`, …).
- Template page size defaults to A4 (210 × 297 mm); Letter is selectable via
  `--page-size`.
- `Resume Section Heading` derives from Word's built-in `Heading 1`, so
  headings are semantically real headings, not just visually similar text.
- Sections are ordered blocks delimited by `Resume Section Heading`
  paragraphs, so future output can reorder them per target role.

## Roadmap (not yet implemented)

1. ~~Parse and index the Master Resume into structured evidence.~~
   (Mission 2 implemented — **not approved**; remediation Mission 2.1 next,
   see `docs/PROJECT_STATUS.md`).
2. Ingest a Job Description and decide section order / evidence selection.
3. Fill `resume_template.docx` programmatically → DOCX → ATS-friendly PDF.
