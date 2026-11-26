# Resume Assistant

A local, **Master Resume-driven** Python application that extracts candidate
evidence, analyzes a supplied job description, matches requirements locally,
publishes a deterministic, evidence-bound **resume section plan**, and generates
a validated, evidence-bound **tailored resume JSON + ATS-safe DOCX**.

**Current milestone:** Mission 3.3 planning Stages A–E are owner-approved and
Mission 3.3 is APPROVED (owner closeout 2026-11-25). **Phase 4 core JSON/DOCX
generation is IMPLEMENTED — AWAITING REVIEW; PDF/template-filling generation is
not implemented.**
See [PROJECT_STATUS.md](docs/PROJECT_STATUS.md) for approval provenance and
actual verification results.

## What the workflow produces

```text
Private Master_Resume.docx
    → pinned byte capture → ordered DOCX extraction
    → immutable snapshot/catalog generation + atomic extraction manifest

Supplied job description (UTF-8 text)
    → configured Responses client → strict quotation-checked job analysis

Validated candidate evidence + job analysis
    → local CandidateJobMatcher → component-level candidate/job match

Validated candidate evidence + analysis + match
    → deterministic source-entry grouping and relevance budgets
    → strict schema-2 plan + compact provenance + evidence-bound blueprint
    → JSON restoration → source-aware result validation
    → mode-0600 private atomic publication

Validated section plan + committed candidate evidence
    → deterministic local header (identity/contact stay local)
    → provider generation over selected non-contact evidence only
    → strict payload parse + evidence-scope/numeric-grounding validation
    → mode-0600 tailored_resume.json + tailored_resume.docx
```

The Master Resume is the candidate source. The planner does not infer missing
qualifications. Analysis sends only the **job description**; generation sends
only **selected non-contact evidence** from the approved plan. Matching and
planning work offline; analysis and generation require API configuration.
Contact/identity records and excluded evidence never enter the generation
request.

## Requirements and setup

Python **3.12 or later** is required; the project is verified with Python
3.12.14 on Linux. Always use the project virtual environment.

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install -e ".[dev]"
.venv/bin/python -m pip check
```

Runtime dependencies are `python-docx` and `pydantic`; tests use `pytest`.
The optional `preview` dependency group adds WeasyPrint for an approximate
blank-template preview. No RAG, vector database, agent framework, or additional
planning dependency is used.

## Private inputs and artifacts

| Path / artifact | Purpose and contract |
|---|---|
| `data/private/Master_Resume.docx` | Authoritative candidate source; never modified by extraction |
| `data/private/job_description.txt` | Example location for the job description you supply |
| `data/private/processed/candidate_evidence.json` | Current extraction **manifest**, referencing one immutable generation |
| Committed `catalog.json` / `snapshot.json` | Extraction payload schema **3.0.0** inside publication envelopes **1.0.0** |
| `data/private/processed/job_description_analysis.json` | Job analysis schema **2.0.0**, prompt **2.0.0**, AI-unverified interpretations |
| `data/private/processed/job_candidate_match.json` | Match schema **2.0.0**, explicit component support and human-review requirement |
| `data/private/processed/resume_section_plan.json` | Plan schema **2.0.0**, complete provenance and explicit summary bindings |
| `data/private/processed/tailored_resume.json` | Generation schema **1.0.0**, deterministic local header, grounded payload, and validation report |
| `data/private/processed/tailored_resume.docx` | ATS-safe single-column DOCX rendered from the validated generation artifact |

The committed generation files live below
`data/private/processed/.extractions/<bundle_key>/generations/<generation_id>/`.
Use the manifest-aware reader; do not select a generation by filename order or
read an old standalone snapshot as if it were the current extraction.

## Run the complete workflow

Run these commands from the repository root. They intentionally show distinct
input and output roles. The examples are for local owner operation; coding-agent
verification uses synthetic temporary inputs instead of your real resume.

### 1. Extract the Master Resume

```bash
.venv/bin/python -m resume_assistant extract-master \
  --master data/private/Master_Resume.docx \
  --output data/private/processed/candidate_evidence.json \
  --snapshot-output data/private/processed/document_snapshot.json
```

`--master` and `--output` have the displayed private defaults. `--output` is the
atomic manifest commit point. `--snapshot-output` is compatibility selector
metadata: it does **not** write an independent snapshot file. The current
snapshot/catalog pair must be loaded through `load_committed_pair`.

Extraction preserves original content, source filename/SHA-256, paragraph and
table locations, nested ancestry, and explicit coverage gaps. Unsupported Word
structures are diagnosed rather than treated as extracted facts.

### 2. Analyze the supplied job description

Select the provider via optional `LLM_PROVIDER` (`responses` [default],
`chat_completions`, or `gemini`):
- `responses`: `AGENTROUTER_API_KEY` and optional `AGENTROUTER_BASE_URL`,
  `AGENTROUTER_MODEL` (fallback `MODEL`), and `AGENTROUTER_TIMEOUT_SECONDS`.
- `chat_completions`: `LLM_API_KEY` (fallback `OPENAI_API_KEY` /
  `AGENTROUTER_API_KEY`) and optional `LLM_BASE_URL` (fallback
  `AGENTROUTER_BASE_URL`), `LLM_MODEL` (fallback `MODEL`), and
  `LLM_TIMEOUT_SECONDS`.
- `gemini`: `GEMINI_API_KEY` (fallback `LLM_API_KEY`) and optional
  `GEMINI_BASE_URL` (`https://generativelanguage.googleapis.com/v1beta`),
  `GEMINI_MODEL` (fallback `LLM_MODEL` / `MODEL`, default `gemini-2.5-flash`),
  and `LLM_TIMEOUT_SECONDS`.

The `responses` defaults are documented in `.env.example`. If you maintain a local
`.env`, explicitly load your own file in the shell; the application has no
automatic dotenv loader:

```bash
set -a; source .env; set +a
.venv/bin/python -m resume_assistant analyze-job \
  --input data/private/job_description.txt \
  --output data/private/processed/job_description_analysis.json
```

Analysis retains original phrases, quotations, and classification contexts.
Missing scalars remain null; unsupported quotations or invalid provider responses
are rejected before writing. Required/preferred classification is explicitly
AI-generated and unverified. Live-provider compatibility has not been established
by the offline integration tests.

### 3. Match candidate evidence locally

```bash
.venv/bin/python -m resume_assistant match-job \
  --analysis data/private/processed/job_description_analysis.json \
  --candidate-evidence data/private/processed/candidate_evidence.json \
  --output data/private/processed/job_candidate_match.json
```

The displayed paths are the defaults. Matching uses boundary-aware normalized
phrases, supported compound components, and explicit coverage semantics. It has
no synonyms, fuzzy scores, external retrieval, or provider calls. Results retain
`supported`, `partially_supported`, `no_evidence_found`, or `not_assessable`;
none of these proves semantic qualification possession.

### 4. Publish a source-validated section plan

```bash
.venv/bin/python -m resume_assistant plan-sections \
  --analysis data/private/processed/job_description_analysis.json \
  --match data/private/processed/job_candidate_match.json \
  --candidate-evidence data/private/processed/candidate_evidence.json \
  --output data/private/processed/resume_section_plan.json
```

The first three flags are required; `--output` defaults to the displayed plan
path. Inputs must agree with the approved matcher decisions. Supported components
are prioritized required → preferred → documented background; deduplication
retains evidence and parent requirements. Source-derived entries, merged rows,
and nested support stay intact under relevance-aware budgets. Every evidence
record has one selected or excluded outcome, and empty sections are omitted.

The serialized plan is restored and source-aware validated before atomic
publication. Candidate/match/analysis aliases and consumed managed generation
artifacts cannot be used as the match or plan destination.

### 5. Generate a tailored resume

```bash
set -a; source .env; set +a
.venv/bin/python -m resume_assistant generate-resume \
  --plan data/private/processed/resume_section_plan.json \
  --candidate-evidence data/private/processed/candidate_evidence.json \
  --output-json data/private/processed/tailored_resume.json \
  --output-docx data/private/processed/tailored_resume.docx \
  --output-pdf data/private/processed/tailored_resume.pdf
```

Both inputs are required, and at least one of `--output-docx` or `--output-pdf`
must be provided (`--output-json` defaults to
`data/private/processed/tailored_resume.json`). Using `--output-pdf` converts
the rendered ATS-safe DOCX via headless LibreOffice (`soffice` or `libreoffice`
from the optional `libreoffice-writer` system package) without adding Python PDF
dependencies; all outputs remain confined under `data/private/processed/` with
mode `0600`. The deterministic header is built locally from identity/contact
records. Only selected non-contact evidence from the approved plan (plus
non-PII `technical-skills` catalog records for `skill_groups`) is sent to the
provider. Every generated claim must cite selected evidence in its permitted
section/entry/skill scope; heading and ordering must match the plan. Numeric
claims that are absent from the cited text produce warnings for human review;
add `--strict-metrics` to make them hard failures. `--page-size {A4,Letter}`
selects the DOCX/PDF page size (default A4). Semantic entailment is never
claimed, and `human_review_required` is always true.

## Inspect the plan

The CLI prints aggregate counts, versions and review indicators, not candidate
resume text. You may inspect the private JSON locally in your editor. For local
programmatic inspection, use `ResumeSectionPlan.model_validate_json` and then
`SectionPlanner.validate_result` with the original validated sources; structural
JSON loading alone is not proof that a plan belongs to those sources.

The plan contains ordered sections/entries, prioritized supported skills,
matcher coverage, complete inclusion/exclusion accounting, compact evidence
provenance, and an evidence-bound professional-summary instruction contract.
Detailed validation/inspection examples and the compatibility policy are in
[docs/planning.md](docs/planning.md).

## Schema compatibility and privacy

- Extraction payloads stay **3.0.0**; publication wrappers stay **1.0.0**.
- Job analysis/prompt, match, and current plan use **2.0.0** independently.
- Legacy standalone schema-3 catalogs remain readable with sufficient provenance
  for planning; they have no managed snapshot/generation identity to invent.
- Schema-1 plans are data-only historical inspection through
  `LegacyResumeSectionPlan`. Trusted schema-2 instructions require re-planning
  from validated original inputs; an old version label cannot bypass validation.
- Private resume, JD, evidence, matches, plans, generated outputs, `.env`, and
  virtual environments must never be committed. Candidate material is sent to a
  provider only by `generate-resume`, and only selected non-contact evidence
  from the approved plan; identity/contact records and excluded evidence stay
  local. JD analysis sends only the supplied job description.
- Application input/output paths use existing pinned no-follow/private atomic
  facilities where documented. Match/plan/generation destinations reject aliases
  to direct inputs and resolved generation files, including aliases introduced
  during processing. Outputs are confined to private processed storage and mode
  0600.
- Expected input/version/integrity errors normally exit **2**; unexpected
  implementation/publication failures exit **3**; successful commands exit **0**.
  Summaries and controlled errors are aggregate/content-free.

## Current limitations and human review

Every match and plan requires human review. Lexical support and quotation checks
are not semantic entailment, proficiency proof, completeness verification, or
independent verification of candidate facts. Heading detection is heuristic.
Unmarked/ambiguous entries and unavailable merge origins remain explicit,
indivisible unresolved spans; their underlying role count is not guessed.
Optional coordinates and unavailable generation metadata stay null. Target pages
deterministically influence content caps, but `page_guarantee` is always false.

Linux no-follow/descriptor-relative behavior is verified. Unsupported platforms
fail closed; rendering fidelity, fsync/power-loss durability, and every privileged
filesystem mutation are not guaranteed. Phase 4 core JSON/DOCX/PDF generation is
implemented and awaiting review; its numeric checks are heuristic, its DOCX is
structurally ATS-safe, `--output-pdf` relies on an optional local
`libreoffice-writer` installation (`soffice`/`libreoffice`), and template
filling remains later work.

## Synthetic verification

Seven legacy tests use the real Master Resume. To run the synthetic suite without
those private-source cases, use an external temporary directory:

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -B -m pytest -p no:cacheprovider \
  --basetemp=/tmp/resume-assistant-tests tests \
  --deselect=tests/test_evidence.py::test_evidence_ids_are_deterministic \
  --deselect=tests/test_evidence.py::test_catalog_pydantic_round_trip \
  --deselect=tests/test_extractor.py::test_extraction_does_not_modify_source \
  --deselect=tests/test_extractor.py::test_numbering_resolution_from_numbering_xml \
  --deselect=tests/test_section_detector.py::test_master_resume_top_level_section_count \
  --deselect=tests/test_section_detector.py::test_unclassified_blocks_for_preamble \
  --deselect=tests/test_template.py::test_master_resume_untouched
.venv/bin/python -m pip check
git diff --check
```

The engineering verification command and audited `/tmp/opencode` guard are
recorded separately in [PROJECT_STATUS.md](docs/PROJECT_STATUS.md). End-to-end
tests use a synthetic Responses transport and never call a live provider. The
portable independent acceptance matrix is maintained in
`tests/test_planning_acceptance.py`.

## Project guide

- `src/resume_assistant/`: extraction, analyzer/client, matcher, planner,
  strict/legacy output models, publication and private I/O, four-command CLI.
- `tests/`: synthetic unit/acceptance/integration fixtures and seven explicitly
  listed private-source legacy cases.
- `docs/`: [extraction](docs/extraction.md), [job analysis](docs/job_analysis.md),
  [matching](docs/matching.md), [planning](docs/planning.md),
  [template design](docs/template_design.md), and the engineering handoff.
- `templates/resume_template.docx`: placeholder-only blank ATS template.
- `src/build_template.py`: blank A4/Letter template creation; optional preview
  is approximate and does not generate a candidate-tailored resume.
- `AGENTS.md`: onboarding, privacy, environment, scope and Git instructions.

Work in the existing checkout, preserve all uncommitted work, and use `.venv`.
Do not stage, commit, push, reset or switch branches without explicit authority.
