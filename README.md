# Resume_Assistant

Local Python project that will eventually take a Job Description and generate
a customized, ATS-friendly PDF resume using verified information from a
private Master Resume.

**Status: Missions 1, 1.1, 1.2, 2, and 1.3 implemented — inspection,
foundations, A4-configurable template with semantic headings, isolated
Python 3.12 environment, deterministic extraction layer, and Git/handoff
infrastructure. Mission 2.1.2's integrity/safety remediation is APPROVED.
Missions 3.1 and 3.1.1 are APPROVED after remediation and human closeout approval.
Missions 3.2 and 3.2.1 are APPROVED after remediation, independent review, and
explicit human confirmation. Mission 2.2.1's privacy/source-binding/coverage
remediation is IMPLEMENTED — AWAITING REVIEW.
Mission 2 is NOT APPROVED; parent Mission 3 remains in progress and not approved.
Resume generation does not exist yet. See `docs/PROJECT_STATUS.md`.**

## Project structure

```text
Resume_Assistant/
├── data/
│   └── private/            # PRIVATE — never committed (see .gitignore)
│       ├── Master_Resume.docx   # authoritative candidate information
│       └── processed/       # PRIVATE — generated artifacts
│           ├── candidate_evidence.json
│           ├── document_snapshot.json
│           ├── job_description_analysis.json
│           └── job_candidate_match.json
├── templates/
│   └── resume_template.docx # blank, ATS-friendly template (generated)
├── prompts/                 # reserved for future prompt assets
├── src/
│   ├── build_template.py    # regenerates templates/resume_template.docx
│   └── resume_assistant/    # extraction + analysis + local matching
│       ├── __init__.py
│       ├── models.py        # Pydantic v2 extraction models (schema 3.0.0)
│       ├── extractor.py     # deterministic DOCX extraction
│       ├── document_coverage.py # located structures + actual part/reference inventory
│       ├── section_detector.py
│       ├── evidence.py      # evidence catalog builder
│       ├── llm_client.py    # AgentRouter Responses API client (stdlib HTTP)
│       ├── job_models.py    # strict evidence-linked job models (schema 2.0.0)
│       ├── prompts.py       # versioned job-analysis prompt
│       ├── job_analyzer.py  # structured analyzer + quotation verification
│       ├── private_output.py # pinned POSIX directory-relative artifact writes
│       ├── private_input.py # no-follow local artifact reads
│       ├── matching.py      # deterministic boundary-aware phrase matching
│       ├── matching_models.py # strict match schema 2.0.0 + compact provenance
│       ├── cli.py           # extract-master | analyze-job | match-job
│       └── __main__.py
├── tests/
│   ├── test_template.py     # template verification suite
│   ├── test_extractor.py    # extraction tests (synthetic fixtures)
│   ├── test_source_capture.py # immutable-byte/parser hash binding
│   ├── test_extraction_privacy.py # real CLI mutable-model privacy failures
│   ├── test_inline_coverage.py # unsupported structures + located diagnostics
│   ├── test_header_footer_integrity.py # actual parts, variants, ownership/linkage
│   ├── test_extraction_matching_compatibility.py # legacy schema + approved consumer
│   ├── test_evidence.py
│   ├── test_cli.py          # extract-master + analyze-job CLI tests
│   ├── test_completeness.py
│   ├── test_nested_integrity.py
│   ├── test_section_detector.py
│   ├── test_build_template.py
│   ├── test_render_preview.py
│   ├── test_llm_client.py   # offline client tests (mocked transport)
│   ├── test_job_analyzer.py # offline analyzer tests (stub client)
│   ├── test_job_grounding.py # own-evidence/strict-schema/uncertainty regressions
│   ├── test_private_output.py # symlink/race guards for both CLI commands
│   ├── test_urllib_transport.py # real loopback HTTP/TLS redirect tests
│   ├── test_matching.py
│   ├── test_matching_remediation.py # accuracy, coverage, and integrity matrix
│   ├── test_matching_cli.py # offline CLI, input guards, validated writes
│   ├── render_template_pdf.py  # DOCX → PDF preview for visual QA
│   └── fixtures/           # synthetic DOCX/JD/evidence fixtures
├── output/                  # generated resumes/previews — never committed
├── docs/
│   ├── template_design.md   # template design decisions & conventions
│   ├── extraction.md        # extraction architecture & limitations
│   ├── job_analysis.md      # JD analysis architecture & limitations
│   ├── matching.md          # local matching rules, provenance, and coverage
│   └── PROJECT_STATUS.md    # persistent handoff / progress tracking
├── pyproject.toml           # metadata + dependencies (see Setup)
├── .env.example             # AGENTROUTER_* template (no secrets; whitelisted)
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
python -m resume_assistant match-job         # offline; uses existing private artifacts
set -a; source .env; set +a                 # load AGENTROUTER_* settings
python -m resume_assistant analyze-job --input path/to/job.txt
```

Dependencies: `python-docx` and `pydantic` (runtime), `pytest` (dev/test),
`weasyprint` (optional preview). No other third-party packages are required.

## Current implementation (Mission 2)

- `src/resume_assistant/` package: models, extractor, section detector, evidence
  builder, CLI.
- `python -m resume_assistant extract-master` writes
  `data/private/processed/candidate_evidence.json` and `document_snapshot.json`
  under the same private directory and prints aggregate extraction statistics.
- Architecture and limitations: `docs/extraction.md`.
- Mission 2 is **not approved** — known defects are tracked in
  `docs/PROJECT_STATUS.md` §D.
- Mission 2.1 (extraction remediation) added the persisted document snapshot
  (`data/private/processed/document_snapshot.json`) alongside the candidate
  evidence catalog (`data/private/processed/candidate_evidence.json`).
- Mission 2.1.1 hardened the extraction layer: honest completeness semantics,
  corrected Word numbering overrides, recursive nested-table evidence, sanitized
  Pydantic validation errors, and effective source-location regression tests.
- Mission 2.1.2 corrected the independently reviewed integrity/safety blockers:
  sanitized error paths, validation-failure preservation tests, assessed coverage
  and model invariants, deterministic nested identities, and output-alias rejection.
  Schema 3.0.0 persists unresolved details with snapshots. Unsupported extraction
  capabilities remain explicitly OPEN; see `docs/extraction.md`. Recorded
  `APPROVED` after independent review (R-01–R-05 verified fixed).
- Mission 2.2.1 binds source provenance to one pinned, immutable byte capture:
  hashing, python-docx parsing, and the existing numbering reader use the same
  bytes. Catalog/snapshot models are strictly revalidated before warning-error
  JSON serialization. Actual header/footer references replace phantom/duplicate
  convenience-part discovery; owning-part locations and conservative inline
  diagnostics persist in both artifacts. Schema 3.0.0 has optional legacy-safe
  additions; approved matcher versions/implementation remain compatible.
  Header/footer tables and non-default-only content remain explicitly unsupported
  with located gaps. Status: **IMPLEMENTED — AWAITING REVIEW**; Mission 2 is not approved.

## Current implementation (Missions 3.1 / 3.1.1 — Job Description analysis)

- `src/resume_assistant/llm_client.py`: minimal stdlib-HTTP client for the
  AgentRouter OpenAI-compatible Responses API, with injectable transport for
  offline testing, structural URL/timeout validation, rejected redirects,
  completed-envelope/refusal checks, and sanitized, typed errors.
- `src/resume_assistant/job_models.py` + `prompts.py`: structured
  strict `JobDescriptionPayload` / `JobDescriptionAnalysis` models (schema
  2.0.0) and versioned trusted instructions (prompt 2.0.0). Factual scalars
  carry `{value, quotation}`; every claim must occur in its own quotation,
  which must occur in the JD. Unsupported paraphrases/normalized values fail.
- `src/resume_assistant/job_analyzer.py`: validates input, parses the model's
  JSON, validates with Pydantic, and rejects any supporting quotation that is
  not an exact substring of the source JD. Required/preferred/uncertain skills
  retain exact classification contexts. Cross-classification conflicts fail
  closed; identical entries are deduplicated by all fields. Semantic field
  assignments and skill classifications are explicitly **AI-generated and
  unverified**; substring checks do not prove semantic entailment or recall.
- `python -m resume_assistant analyze-job --input job.txt` writes
  `data/private/processed/job_description_analysis.json` after round-trip
  validation and prints counts/booleans plus source/model/output metadata,
  including `interpretations_verified: False`. Shared output protection pins
  no-follow directory descriptors before processing and atomically replaces
  relative to those handles. Linux is verified; unsupported platforms,
  including Windows, fail closed. See `docs/job_analysis.md` for precise limits.
- Configuration: `AGENTROUTER_API_KEY` (required) plus optional
  `AGENTROUTER_BASE_URL`, `AGENTROUTER_MODEL`, `AGENTROUTER_TIMEOUT_SECONDS`;
  see `.env.example` and `docs/job_analysis.md`.
- Missions 3.1 and 3.1.1 are **APPROVED** following independent remediation
  review and explicit human approval at closeout. Live provider compatibility
  remains unverified; the closeout smoke test could not run without credentials.

## Local candidate evidence matching (Missions 3.2 / 3.2.1)

```bash
.venv/bin/python -m resume_assistant match-job \
  --analysis data/private/processed/job_description_analysis.json \
  --candidate-evidence data/private/processed/candidate_evidence.json \
  --output data/private/processed/job_candidate_match.json
```

- All three displayed paths are private defaults. The command runs offline and
  consumes existing validated job-analysis schema **2.0.0** / prompt **2.0.0**
  and candidate-evidence schema **3.0.0** artifacts. It does not regenerate inputs
  or need API configuration.
- Matching method **`exact_normalized_phrase_v2`** normalizes case/whitespace,
  tolerates terminal sentence `.`, `,`, and `;`, and enforces technical-token
  boundaries. `C++`, `C#`, `.NET`, and `Node.js` punctuation is retained.
  Java/JavaScript, Go/Google, R/Resume, and SQL/MySQL fragments do not match.
- Skill compounds split only on standalone, whitespace-delimited separators;
  `and/or` remains whole. Language and requested proficiency level must share a
  supporting record. No synonyms, fuzzy matching, or confidence percentages.
- Match schema **2.0.0** retains unique source-derived requirement IDs, exact
  quotations/classification contexts, component-level evidence IDs, and compact
  source-location/table-cell provenance. Old match schema 1.0.0 is not migrated
  automatically. Source extraction/analysis schemas remain unchanged.
- Statuses are `supported`, `partially_supported`, `no_evidence_found`, and
  `not_assessable`. Coverage gaps affect negative assessability; empty actual
  evidence is explicitly unassessable. Positive evidence survives coverage gaps.
- Evidence/requirement cross-references are checked after JSON reconstruction
  before atomic private writing. Inputs reject symlink traversal; output is pinned
  under `data/private/processed/` and written mode 0600. CLI summaries contain
  aggregate indicators, never candidate text, names, hashes, or full records.
- Textual occurrence is not semantic proof, skill possession, or complete
  extraction. Negated or interest-only mentions still need review. Every match
  artifact explicitly requires human review; AI classifications stay unverified.
- Missions 3.2 and 3.2.1 are **APPROVED**, with the independent review's
  non-blocking observations preserved in `docs/PROJECT_STATUS.md` §I.
  See `docs/matching.md` for the precise
  normalization, coverage, provenance, and filesystem contracts.

### Verification provenance

- Original Mission 3.1 implementation agent: **189 passed, 11 warnings** in
  an unrestricted run containing seven real-source tests. Its earlier
  synthetic-only/no-private-read claim is withdrawn.
- Independent privacy-safe review: **189 collected, 182 passed, 0 failed,
  7 private-source cases deselected, 11 warnings**.
- Mission 3.1.1 remediation: **516 collected, 509 passed, 0 failed,
  7 deselected, 11 unchanged legacy warnings**; `pip check` and
  `git diff --check` pass. See `docs/PROJECT_STATUS.md` §F for the exact
  invocation and exclusions; an unrestricted `pytest` run still accesses
   the real Master Resume through those legacy cases.
- Mission 3.2 independent review: **532 collected, 525 passed, 7 deselected,
  0 failed, 11 warnings**, including **16 new Mission 3.2 cases**. The matcher
  was rejected for M32-01–M32-11 despite the green suite.
- Mission 3.2.1 remediation: **705 collected, 698 passed, 7 deselected,
  0 failed, 0 skipped, 11 unchanged warnings**; **173 additional cases** beyond
  Mission 3.2. Existing extraction/analyzer regressions pass. Matching is offline;
  the complete suite also runs ten pre-existing synthetic loopback HTTP/TLS
  transport cases. External-network and real-private-read verification guards
  recorded zero attempts. Exact invocation/exclusions: `docs/PROJECT_STATUS.md` §F.
- Mission 2.2.1: **812 collected, 805 passed, 7 deselected, 0 failed, 0 skipped,
  11 unchanged FutureWarnings**; **107 new synthetic regressions**. `pip check`
  and `git diff --check` pass. The audited external guard distinguished actual
  directory-relative temporary opens and reported zero private/external-network
  attempts; ten existing synthetic loopback connections were explicitly authorized.
  Exact commands, scope, and compatibility decision: `docs/PROJECT_STATUS.md` §J.

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
   (Mission 2 implemented — **not approved**; Mission 2.1.2 approved,
   see `docs/PROJECT_STATUS.md`).
2. Ingest a Job Description and decide section order / evidence selection.
   Mission 3.1 added structured JD **analysis** (quotation-verified JSON);
   evidence selection and section ordering remain unimplemented.
3. Fill `resume_template.docx` programmatically → DOCX → ATS-friendly PDF.
