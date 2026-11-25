# PROJECT_STATUS.md — Resume_Assistant Development Handoff

Single source of truth for project handoff across coding agents (OpenCode,
Google Antigravity) and human reviewers. Update this file at the end of every
mission. **This is a navigation aid — always verify claims against source
code (see `AGENTS.md` §A).**

Last updated: 2026-11-24 (Stages A–D APPROVED, including conditional owner-authorized Stage D acceptance after independent review; Stage E final integration/documentation IMPLEMENTED — AWAITING INDEPENDENT REVIEW; latest authorized regression 1,419 collected / 1,412 passed / 7 documented private-source exclusions / 11 existing warnings; Mission 3.3 and parent Mission 3 remain unapproved; Phase 4 generation is not implemented; historical approvals and accepted limitations are preserved).

---

## A. Project Overview

**Objective.** A local Master Resume-driven application that will later generate
job-tailored, ATS-friendly resumes from traceable candidate evidence. The current
workflow is implemented through **schema-2 resume section planning**: pinned
DOCX extraction, quotation-checked JD analysis, local component matching,
source-derived evidence selection/entry grouping, relevance budgets, complete
accounting, explicit summary bindings, source-aware validation, and private
atomic publication. Only JD analysis is provider-enabled; candidate information
does not enter that request. **Phase 4 AI resume/DOCX/PDF generation is not
implemented.** Stages A–D are owner-approved; Stage E awaits independent review.

**Current architecture.**

```text
Master_Resume.docx (private)
    -> pinned regular-file capture (immutable bytes + filename + SHA-256)
    -> src/resume_assistant/ extractor (same-byte DOCX parsing + ordered body scan)
    -> DocumentSnapshot (located gaps, actual part ownership + unresolved details)
    -> section detector (heuristic top-level headings)
    -> evidence builder (Pydantic catalog, deterministic IDs)
    -> immutable schema-3.0.0 snapshot/catalog envelopes
    -> atomic manifest: data/private/processed/candidate_evidence.json
       (--snapshot-output is compatibility selector metadata, not a separate file)

job_description.txt (private, Mission 3.1)
    -> AgentRouterClient (OpenAI-compatible Responses API, stdlib HTTP)
    -> JobDescriptionAnalyzer (JSON parse -> Pydantic -> quotation checks)
    -> data/private/processed/job_description_analysis.json (private artifact)

job_description_analysis.json + candidate_evidence.json (validated, private)
    -> CandidateJobMatcher (boundary-aware local phrases + coverage/provenance)
    -> CandidateJobMatch (match schema 2.0.0, human review required)
    -> data/private/processed/job_candidate_match.json (private artifact)

catalog/snapshot + job analysis + candidate/job match (validated, private)
    -> approved component selection and coherent source-entry grouping/budgets
    -> ResumeSectionPlan schema 2.0.0 (provenance + evidence-bound blueprint)
    -> JSON restoration + SectionPlanner.validate_result(original sources)
    -> private atomic data/private/processed/resume_section_plan.json

src/build_template.py -> templates/resume_template.docx (A4 blank template)
tests/render_template_pdf.py -> output/resume_template_preview.pdf (approximate preview)
```

**Technology stack.** Python 3.12.14 · python-docx ≥1.2,<2 · pydantic ≥2,<3 ·
pytest ≥9 (dev) · weasyprint ≥70 (optional preview) · no agent frameworks,
RAG, or vector databases.

**Local execution environment.** Project-local `.venv/` at the repository
root. Tests: `.venv/bin/python -m pytest`. Dependency check:
`.venv/bin/python -m pip check`. Never install into system Python.

**Important project directories.**

| Directory | Purpose |
|---|---|
| `data/private/` | **NEVER COMMIT** — Master Resume + processed evidence/analysis JSON |
| `src/resume_assistant/` | extraction + analysis + local matching + source-validated planning; strict/legacy contracts, secure artifact I/O, four-command CLI |
| `src/build_template.py` | template generator (A4 default, `--page-size`) |
| `tests/` | Latest Stage E authorized run: **1,419 collected, 1,412 passed, 7 documented private-source exclusions, 0 failed, 11 existing warnings**; 99 added Stage E acceptance/integration cases. Historical counts remain in §F/§K–§M. |
| `templates/` | blank ATS template artifact |
| `docs/` | extraction/job-analysis/matching/planning contracts, operational README links, this status file |
| `output/` | generated previews/resumes — ignored except `.gitkeep` |
| `prompts/` | reserved for future prompt assets (`.gitkeep` only); live prompts live in `src/resume_assistant/prompts.py` |
| `.env.example` | `AGENTROUTER_*` configuration template — whitelisted in `.gitignore`, contains no secrets |

---

## B. Mission History

Statuses per `AGENTS.md` §E. **Only an explicit human approval makes a
mission `APPROVED`; none of the approvals below are fabricated.**

| Mission | Scope | Status | Provenance / notes |
|---|---|---|---|
| 1 | Foundation, inspection, blank template | `IMPLEMENTED — AWAITING REVIEW` | Agent-reported complete with tests. Human stated at the Mission 1.2 kickoff that Missions 1 and 1.1 were "completed and reviewed"; outstanding issues from that review became Mission 1.2. No explicit `APPROVED` statement recorded. |
| 1.1 | Python 3.12 virtual environment | `IMPLEMENTED — AWAITING REVIEW` | Same provenance as Mission 1. Independently re-verified in Mission 1.3 (`.venv` present, Python 3.12.14, `pip check` clean). |
| 1.2 | Template & QA refinements (A4, semantic headings, preview, real git-check tests) | `IMPLEMENTED — AWAITING REVIEW` | Implemented according to its engineering report; 37-test suite green (agent-executed). Independent review pending. |
| 2 | Candidate intelligence & evidence extraction (Phase 2 parent) | `APPROVED` (aggregate Phase 2 deliverable) | Initial implementation identified 10 defects (see §D), remediated across child missions. Independent Phase 2 Comprehensive Engineering Review returned APPROVE WITH NON-BLOCKING OBSERVATIONS. Explicit aggregate human approval granted by the project owner (2026-11-20). Historical sub-mission review states are preserved below. |
| 1.3 | Git, AGENTS.md, handoff infrastructure | `IMPLEMENTED — AWAITING REVIEW` | Local Git repository initialized on `main`; private GitHub repository created by the **user**; `origin` configured to `https://github.com/AlaaBadawii/Resume_Assistan.git`. Initial baseline commit successfully created (**`fad3e90`** — `chore: establish resume assistant foundation`) and initial push successfully completed; branch `main` tracks `origin/main`. Repository visibility is **user-reported** and remains unconfirmed. |
| 1.3.1 | Status-doc sync + staging finalization | `IMPLEMENTED — AWAITING REVIEW` | Status documentation synchronized with the repository state; the initial staging approval gate was subsequently **completed by the user**, which authorized the baseline commit and push. |
| 1.3.2 | Post-push status synchronization | `IMPLEMENTED — AWAITING REVIEW` | Handoff documentation updated to reflect the successful initial commit and push (`fad3e90`, `origin/main`); this update **awaits human review** and is deliberately not staged or committed. |
| 2.1 | Candidate evidence extraction remediation | `IMPLEMENTED — AWAITING REVIEW` | Remediation implemented per its engineering report: table evidence persistence, hyperlink relationship resolution, numbering resolution, source locations, statistics, completeness semantics, dual-artifact CLI persistence. Independent review pending; **Mission 2 remains not approved**. |
| 2.1.1 | Critical evidence integrity & extraction remediation | `IMPLEMENTED — AWAITING REVIEW` | Focused remediation of the independent Mission 2.1 code-review findings: sanitized Pydantic error handling (F-01), honest derived completeness (F-02/F-07), effective source-location regression tests (F-03), corrected numbering overrides (F-04), recursive nested-table evidence (F-05), typed cell-source provenance (F-08), table hyperlink preservation (F-09), collision-free nested identifiers (F-13), and missing data-integrity CLI tests (F-16). Implementation complete; independent human review pending. |
| 2.1.2 | Integrity and safety blockers | `APPROVED` | R-01–R-05 implemented and verified with synthetic data: complete CLI sanitization, genuine validation-failure tests, enforceable assessed completeness, deterministic document-local nested identities, and normalized alias rejection before source reads/writes. The independent Mission 2.1.1 review rejected that mission's claimed completeness/verification; this follow-up addresses the five assigned HIGH blockers. Independent review verdict: APPROVE (all five findings VERIFIED FIXED; 111 passed / 7 deselected / 11 legacy warnings). Recorded `APPROVED` per the Mission 3.1 assignment instruction ("Confirm Mission 2.1.2 is recorded as approved"). Mission 2 remains **NOT APPROVED**. |
| 2.2.1 | Privacy, source binding & honest coverage | `IMPLEMENTED — AWAITING REVIEW` | Assigned M2-A01, F-01, F-02/F-07, F-06, F-10, and F-11 implemented and synthetically verified (§J). Immutable captured bytes feed hashing, DOCX parsing, and the existing numbering reader; fresh model-field validation and warning-error serialization protect both channels; located inline gaps and actual default/first/even reference/part ownership are persisted. No phantom/shared-part duplication. Optional schema-3.0.0 additions were documented before implementation; approved matcher implementation and pinned versions remain unchanged. **812 collected, 805 passed, 7 deselected, 0 failed, 0 skipped, 11 unchanged FutureWarnings**; 107 new cases. Dependency/diff checks pass. No real private contents or external requests; ten existing loopback connections were expressly authorized. Audited in Phase 2 comprehensive review; subsumed under Phase 2 aggregate human approval. |
| 2.2.2 | Publication store, manifest commit point & pair integrity | `IMPLEMENTED — AWAITING REVIEW` | Resolved F-17 dual-artifact publication inconsistency; implemented immutable generation store (`data/private/processed/.extractions/<bundle_key>/generations/<gen_id>/`), single-manifest atomic commit point at `--output` (`PublicationManifest` 1.0.0), committed-pair reader with SHA-256 verification, and strict cross-artifact snapshot/catalog pair integrity validation (`validate_publication_pair`). 26 regressions in `tests/test_publication.py`. Audited in Phase 2 comprehensive review; subsumed under Phase 2 aggregate human approval. |
| 2.2.3 | Numbering & rich-text structural fidelity | `IMPLEMENTED — AWAITING REVIEW` | Resolved F-04 (startOverride precedence, raw numbering_num_id/abstract_num_id/start preserved, numId=0 treated as no numbering, sanitized parser diagnostics), F-09 (w:anchor retained as w:anchor, per-link table/cell/paragraph source_location on row and body hyperlinks), F-12 (missing and unreadable numbering part diagnostics without raw values), F-19 (no invented 'page' default, field_instructions preserves all instructions in document order, incomplete begin/separate/end diagnosis), F-21 (style-basedOn inheritance resolved through the chain, standard List Bullet resolves without false unresolved diagnostics), F-24 (bare vMerge treated as continue and merged, grid_span/v_merge/content_order retained on TableCell). Schema retained at 3.0.0 with additive optional fields only; the approved matcher's Literal['3.0.0'] candidate_evidence_schema_version contract and matching.py/matching_models.py are unchanged. Full privacy-safe suite: **874 collected, 874 passed, 7 deselected, 0 failed, 0 skipped, 11 unchanged FutureWarnings**; 46 new structural-fidelity regressions in tests/test_structural_fidelity.py plus completeness/table-cell expectation updates. `pip check` and `git diff --check` pass. Audited in Phase 2 comprehensive review; subsumed under Phase 2 aggregate human approval. |
| 2.2.4 | Rich-text fidelity, recursive statistics, section SOT & row contact typing | `IMPLEMENTED — REVIEW APPROVED` | Resolved N-4 (`hMerge` support in `TableCell`, parser default/explicit state, and tests), N-5 regression gaps (cyclic style inheritance detection and diagnostics, unavailable numbering part diagnosis, fields in table cells, legacy paragraph properties), WP-1 (recursive all-depth table statistics, media counters `image_count`/`drawing_count`/`embedded_object_count`/`textbox_count`, empty header/footer handling), WP-2 (F-22 section single source of truth in evidence builder, publication pair invariants for section references and unclassified block indexes), WP-3 (F-23 scope-explicit CLI summary labels and tests), WP-4 (F-24 contact typing for unclassified table rows, ragged-row publication pair validation), WP-6 (bounded F-18 unused-import and dead-code cleanup). Schema 3.0.0 retained; approved matcher contract unchanged. Full privacy-safe suite: **897 collected, 889 passed, 1 skipped, 7 deselected, 0 failed, 11 unchanged FutureWarnings**; 16 new automated cases. `pip check` and `git diff --check` pass. Independent review completed with verdict APPROVE WITH NON-BLOCKING OBSERVATIONS; subsumed under Phase 2 aggregate human approval. |
| 3.1 | Job Description Analyzer | `APPROVED` | Original independent review verdict: **DO NOT APPROVE**. M31-01–M31-09 identify redirect credential exposure, insufficient grounding, symlink confinement escapes, incomplete-response acceptance, permissive schema/entries/configuration, inaccurate verification provenance, and directory-input error classification. Original implementation-agent result: 189 passed, 11 warnings; this unrestricted run is **not a verified synthetic-only result**. Independent privacy-safe reproduction: 189 collected, 182 passed, 0 failed, 7 private-source cases deselected, 11 warnings. Remediation implemented via Mission 3.1.1; independent review of that remediation: **APPROVE WITH NON-BLOCKING OBSERVATIONS** (all nine findings verified fixed; independent safeguard-removal checks detected 17/17). Human approval explicitly granted for Mission 3.1 at the Mission 3.1 closeout (2026-11-17); Mission 3.1 is recorded `APPROVED`. The parent Mission 3 remains in progress and was not approved. |
| 3.2 | Local Candidate Evidence Matcher | `APPROVED` | Original implementation used match schema 1.0.0; its independent read-only review returned **DO NOT APPROVE** (M32-01–M32-11, preserved in §I). Mission 3.2.1 remediated those findings with match schema 2.0.0. Independent review of the corrected implementation returned **APPROVE WITH NON-BLOCKING OBSERVATIONS**, scoped to Missions 3.2/3.2.1; all eleven findings VERIFIED FIXED. Human explicitly confirmed Mission 3.2 as already APPROVED in the Mission 2 finalization-audit assignment (2026-11-18). Observations O-1–O-4 are preserved in §I. Parent Mission 3 remains in progress/not approved. |
| 3.2.1 | Matching accuracy & evidence integrity remediation | `APPROVED` | M32-01–M32-11 independently VERIFIED FIXED (§I). Method `exact_normalized_phrase_v2`, match output schema 2.0.0; existing input schemas 2.0.0/3.0.0 unchanged. Implementation suite: **705 collected, 698 passed, 7 deselected, 0 failed, 0 skipped, 11 unchanged warnings**; 173 additional cases beyond Mission 3.2. Independent read-only review reproduced those totals (13.00s), confirmed 189 matcher cases and 183 external synthetic probes, and returned **APPROVE WITH NON-BLOCKING OBSERVATIONS**. Guard audited; no real candidate reads or external requests; ten existing synthetic loopback connections in that historical full-suite review. Review observations O-1–O-4 require no correction before approval and remain recorded in §I. Human explicitly confirmed Mission 3.2.1 as already APPROVED in the Mission 2 finalization-audit assignment (2026-11-18); Missions 3.1/3.1.1 approvals remain in force. No parent Mission 2/3 approval is implied. |
| 3.1.1 | Security, grounding & validation remediation | `APPROVED` | All nine M31 findings remediated: rejected authenticated redirects (real loopback HTTP/TLS tests), source-phrase schema/prompt 2.0.0 with own-evidence checks and explicit AI-unverified classifications, strict completed Responses envelopes, no-follow pinned-directory writes for both commands, strict structure/nonblank/deduplication/conflict/configuration rules, corrected provenance, and controlled regular-file input errors. Verification: **516 collected, 509 passed, 0 failed, 7 private-source cases deselected, 11 legacy warnings**; pip check and git diff --check pass. Eleven additional in-memory removal checks detected safeguard loss. Synthetic data and temporary fixtures only; local loopback requests, no live provider requests, no private contents read. Nothing staged/committed/pushed. Independent review of the remediation: **APPROVE WITH NON-BLOCKING OBSERVATIONS** (all nine findings verified fixed; independent safeguard-removal checks detected 17/17); human approval explicitly granted at the Mission 3.1 closeout (2026-11-17); Mission 3.1.1 is recorded `APPROVED`; Mission 3.1 is `APPROVED`. Live AgentRouter smoke test at closeout: **NOT EXECUTED — runtime credentials unavailable**. |
| 3.3 | Deterministic Resume Section Planning & Evidence Strategy Planner | `IMPLEMENTED — AWAITING REVIEW` | New `src/resume_assistant/section_planner.py` (schema `1.0.0`) plus `plan-sections` CLI subcommand. Consumes candidate evidence (schema 3.0.0), job description analysis (schema 2.0.0), and candidate job match (schema 2.0.0); produces `resume_section_plan.json`. Selects evidence from existing matcher decisions, organizes ordered output sections, prioritizes required/preferred/other skills, accounts for requirement coverage (reusing matcher statuses), emits an evidence-referenced professional-summary blueprint, and allocates deterministic content budgets. Fully deterministic: no LLM, no RAG, no vector database, no agent framework. Reuses `SectionDetector`, `CandidateEvidenceCatalog`, `JobDescriptionAnalysis`, `CandidateJobMatch`, `load_committed_pair`, `read_artifact_text`, `normalize_output_path`, `PrivateOutput`, and atomic `write_json`. New tests: `tests/test_section_planner.py`, `tests/test_planner_cli.py` (25 cases). Full authorized regression suite: **915 collected, 915 passed, 7 deselected, 0 failed, 11 warnings**; `pip check` and `git diff --check` pass. No private candidate data inspected; synthetic fixtures only. Not human-approved; parent Mission 3 remains in progress. |
| 3.3 — Stage A | Input trust boundary, provenance, sanitized errors, strict parsing, and output alias protection (A-01 through A-05), including SA-R01 exit-code correction | `APPROVED` | Explicit project-owner approval stated in the Stage B implementation authorization. Final independent re-review verdict: APPROVE WITH NON-BLOCKING OBSERVATIONS; Stage A ready for human approval. Latest implemented baseline after SA-R01: **1,087 collected, 1,080 passed, 7 documented private-source cases deselected, 0 failed, 11 existing warnings**. Missing explicit/generation inputs return 2; unexpected planner, output-serialization, and publication FileNotFoundError failures return 3. Prior non-blocking smoke-test isolation and minimum-coordinate-validation observations remain recorded in the review. This approval applies only to Stage A; Mission 3.3 itself remains in progress and unapproved. |
| 3.3 — Stage B | Supported-component skills, conservative deduplication/evidence aggregation, and type-safe background selection (B-01 through B-03) | `APPROVED` | Independent review verdict after F-1/F-2 remediation: **APPROVE WITH NON-BLOCKING OBSERVATIONS**. F-1 (contact-metadata filtering: extended/bare labels and profile URLs excluded; GitHub and GitHub Actions preserved) and F-2 (canonical-heading exclusion with boundaryless/partial snapshots; factual employment/project titles preserved) verified fixed with failing-first regressions. B-01/B-02 verified intact: supported-component skill emission, partial requirement handling, case/whitespace-only deduplication with evidence aggregation, owner-authorized additive `PlannedSkill.requirement_associations`, legacy plan loading, deterministic serialization across hash seeds 1/17/103. Approved by the project owner via explicit conditional authorization in the Stage B final review + Stage C implementation assignment ("This assignment explicitly authorizes Stage B acceptance and Stage C implementation, conditional on the review verdict"). Verification after remediation: planner/CLI 243 passed; SA-R01-focused 70 passed; publication/matcher 220 passed; Stage A/B external acceptance 50 passed; full authorized regression **1,145 collected, 1,138 passed, 7 documented private-source cases deselected, 0 failed, 11 existing warnings**; `pip check` and `git diff --check` pass. Non-blocking observations preserved: **O-1** plan schema stays `1.0.0` (additive `requirement_associations` owner-authorized, unreleased artifact); **O-2** heading-styled factual technical content may be excluded (documented limitation, no heading-policy redesign). This approval applies only to Stage B; Mission 3.3 itself remains in progress and unapproved. |
| 3.3 — Stage C | Source-derived entry grouping, relevance budgets, source ordering, and complete evidence outcomes (C-01 through C-04) | `APPROVED` | Independent review initially returned **REQUEST CHANGES** for SC-R1 (discarded vertically merged identity rows), SC-R2 (numbering contamination across repeated source sections), and SC-R3 (skill truncation before evidence survival). Focused remediation binds merge-connected rows and nested support using snapshot metadata, resolves numbering within the enclosing section instance, and applies the skill cap after final evidence survival. Ambiguous spans retain no asserted identity and `entry_grouping_complete=False`. Pre-approval implementation status and verification are preserved in §L: 18 added DOCX-backed regressions, 65 Stage C cases, 243 planner/CLI cases, 220 publication/matcher cases, 70 safeguards; authorized regression **1,210 collected, 1,203 passed, 7 documented exclusions, 0 failed, 11 existing warnings**. Final independent review verdict: **APPROVE WITH NON-BLOCKING OBSERVATIONS**; no new blockers, and accepted unmarked/merge-metadata limits, unreleased schema-1 history, and heading-styled technical-content observations were preserved. The project owner explicitly approved Stages A, B, and C and authorized Stage D in the Stage D assignment; Stage C is recorded APPROVED on that authority. Stages A/B historical approvals remain in force. Mission 3.3 itself remains unapproved. |
| 3.3 — Stage D | Strict output integrity, source-aware result validation, compact provenance, and evidence-bound blueprint (D-01 through D-04) | `APPROVED` | Output contract schema **2.0.0** has mandatory complete provenance and factual bindings; source-aware `SectionPlanner.validate_result` runs before private publication. Strict types, unambiguous JSON, section/entry/selection/accounting/budget relationships, supported component/parent associations, and blueprint projections are validated. Compact source coordinates/cell provenance and extraction generation hashes are retained without duplicating the resume body. Explicit schema-1 inspection plus validated-source re-planning preserves intentional historical compatibility. Pre-approval results are preserved in §M: 110 new cases; prior planner/CLI/Stage C 308 passed; publication/matcher 220 passed; safeguards 70 passed; all 14 external schema/provenance challenges passed; full external suite 72 passed / 4 accepted assertion failures; authorized regression **1,320 collected, 1,313 passed, 7 documented exclusions, 0 failed, 11 existing warnings**. Independent Stage D review verdict: **APPROVE WITH NON-BLOCKING OBSERVATIONS**; 418 focused cases and six independent source/compatibility challenges passed, with no new external failure class. Approval is recorded under the owner's explicit conditional authorization in the Stage D independent review + Stage E assignment. Existing lexical-support, unavailable-coordinate/generation, unmarked-entry and legacy-inspection observations are preserved. Stage E is authorized; Mission 3.3 remains unapproved. |
| 3.3 — Stage E | Managed end-to-end integration, portable acceptance, contract compatibility, operational documentation and final verification (E-01 through E-05) | `IMPLEMENTED — AWAITING REVIEW` | Conditional Stage D review passed and owner-authorized approval was recorded before Stage E work. Real four-command synthetic DOCX/JD workflow now has 23 managed integration cases with Responses-envelope/analyzer validation through an offline transport, plus the portable 76-case independent acceptance matrix. The matching CLI protects consumed generation catalog/snapshot aliases before matching and immediately before publication, preserving existing errors and schemas. README and extraction/analysis/matching/planning docs describe the actual workflow and Phase 4 boundary. All 99 added cases pass; prior planner/CLI/A–D 418 pass; publication/matcher 220 pass; safeguards 70 pass. Final authorized regression: **1,419 collected, 1,412 passed, 7 documented private-source cases deselected, 0 failed, 11 existing warnings**. Stage E is IMPLEMENTED — AWAITING INDEPENDENT REVIEW, not owner-approved; Mission 3.3 remains unapproved. |

---

## C. Current Implementation

**Historical count clarification (M33-13):** The original Mission 3.3 row above
preserves the original agent claim of 915 collected / 915 passed / 7 deselected.
The independent baseline reproduction in the remediation plan was **922
collected / 915 passed / 7 deselected**; the original counting claim was
inaccurate. Current authoritative totals are the Stage E results in §N. Historical
approval/rejection states and their provenance have not been rewritten.

| Component | Location | Responsibility |
|---|---|---|
| Template generator | `src/build_template.py` | Builds the blank A4 (default) / Letter template; `Resume Section Heading` derives from built-in `Heading 1`; CLI: `--page-size {A4,Letter}`; rejects unsupported sizes. |
| Template artifact | `templates/resume_template.docx` | Regenerated by the generator; placeholder-only, ATS constraints enforced by tests. |
| DOCX extractor | `src/resume_assistant/extractor.py` | Immutable same-byte DOCX parsing/hash binding, existing ordered body/nested extraction, unique actual default-part capture and ownership; scoped statistics/metadata limitations remain. |
| Section detector | `src/resume_assistant/section_detector.py` | Heuristic top-level section detection (bold/size/numbering signals); 14 sections detected on the Master Resume. |
| Evidence builder | `src/resume_assistant/evidence.py` | Maps blocks → `EvidenceRecord`s with deterministic SHA-256 IDs and provenance; builds `CandidateEvidenceCatalog`. **Known defects — see §D.** |
| Data models | `src/resume_assistant/models.py` | Schema 3.0.0 retained; optional located gaps, detailed completeness diagnostics, block locations, and header/footer part/reference metadata; existing fields and completeness formulas preserved. |
| CLI | `src/resume_assistant/cli.py`, `__main__.py` | `extract-master`, `analyze-job`, offline `match-job`, and `plan-sections`; validated private artifacts, consumed-input alias protection, aggregate-only summaries. |
| LLM client | `src/resume_assistant/llm_client.py` | Stdlib Responses transport with structural URL/finite-timeout validation, rejected redirects, required completed envelope, refusal/malformed-content rejection, and sanitized errors. HTTP is limited to numeric loopback endpoints; live provider contract remains unverified. |
| Job analysis models & prompt | `src/resume_assistant/job_models.py`, `src/resume_assistant/prompts.py` | Strict `JobDescriptionPayload` / `JobDescriptionAnalysis`, schema/prompt 2.0.0: cited scalars and source phrases, exact classification context, explicit uncertain skills, identical-item deduplication, conflict rejection, and application-owned AI-unverified metadata. |
| Job analyzer | `src/resume_assistant/job_analyzer.py` | Strict JSON parse → Pydantic required-key/own-evidence validation → check every quotation/context against the original JD → canonical artifact. Textual checks are not semantic entailment or complete extraction; no candidate data is injected. |
| Private output | `src/resume_assistant/private_output.py` | All CLI artifact outputs pin no-follow directory handles before processing; exclusive mode-0600 temporary creation, identity checks, and descriptor-relative atomic replacement/cleanup. Facility implementation unchanged in Mission 3.2.1. |
| Local matcher and output models | `src/resume_assistant/matching.py`, `matching_models.py` | Boundary-aware phrases, standalone skill compounds, same-record language levels, actual-record coverage, source-derived unique requirement IDs, compact location/cell provenance, strict statuses, and source-validated JSON. Match schema 2.0.0; human review always required. |
| Artifact input reader | `src/resume_assistant/private_input.py` | Shared pinned no-follow regular-file byte capture for DOCX and matching artifacts; immutable source filename/bytes/digest; traversal/symlink/change rejection and sanitized input errors. |
| Structural coverage | `src/resume_assistant/document_coverage.py` | Parsed-document-only inventory: actual default/first/even relationships, linked ownership, unique part URIs, located unsupported inline/table/part diagnostics, conservative unavailable inventory state. |
| Preview renderer | `tests/render_template_pdf.py` | Approximate WeasyPrint preview; reads page size/margins from the DOCX; writes `output/resume_template_preview.pdf`. Not Word-faithful. |
| Resume planning/output contract | `section_planner.py`, `section_plan.py`, `legacy_section_plan.py` | Approved deterministic selection and schema-2 strict/source-aware output, compact provenance, evidence-bound blueprint, explicit legacy inspection/re-planning. |
| Test suite | `tests/` + `fixtures/` | Latest authorized Stage E result: **1,419 collected / 1,412 passed / 7 private-source exclusions / 0 failed / 11 existing warnings**; preserved prior cases and 99 new end-to-end/independent acceptance cases. |
| Engineering instructions | `AGENTS.md` | Shared agent rules (onboarding, env, privacy, standards, missions, Git). |
| Design/operations docs | `README.md`, `docs/template_design.md`, `docs/extraction.md`, `docs/job_analysis.md`, `docs/matching.md`, `docs/planning.md` | Owner workflow, artifact roles/versions, private use, inspection/migration, implemented limitations and later Phase 4 boundary. |

Default private locations (never committed; real contents not inspected in Mission 3.2.1):
`data/private/Master_Resume.docx`, `data/private/processed/candidate_evidence.json`,
`data/private/processed/document_snapshot.json`,
`data/private/processed/job_description_analysis.json`,
`data/private/processed/job_candidate_match.json`.

---

## D. Known Issues and Technical Debt

**Historical Mission 2 code-review findings — preserved for provenance.
The descriptions below refer to the earlier implementation; current disposition
and supporting evidence are in the remediation matrices.** (Locations verified
against source during Mission 1.3 inspection.)

1. **Evidence builder skips table blocks** — `evidence.py`
   (`build_evidence_catalog`) continues whenever `block.paragraph is None`,
   so table blocks never produce evidence records; table-based evidence does
   not reach the persisted candidate evidence catalog. *(Data loss.)*
2. **Hyperlink relationship ID read from the wrong element** —
   `extractor.py::_paragraph_hyperlinks` reads `w:id` from `w:r` run
   elements; the relationship ID (`r:id`) lives on the `w:hyperlink`
   element itself, so URL resolution fails and `url` stays `None`.
3. **Numbering format inferred, not resolved** — `extractor.py` infers
   `decimal`/`bullet` from the mere presence of a numbering ID instead of
   resolving the actual Word numbering definition in `numbering.xml`.
4. **Source-location mapping for table-cell evidence incomplete** —
   `evidence.py::_source_location_for_block` sets only `table_index`; row,
   cell, and paragraph-in-cell coordinates are missing.
5. **`paragraph_position_in_cell` wrongly assigned to body paragraphs** —
   `evidence.py` sets `paragraph_position_in_cell = paragraph.index` for
   ordinary body paragraphs, not only paragraphs inside table cells.
6. **Unsupported-content scanning not comprehensive** —
   `extractor.py::_scan_unsupported` walks only `document.element.body`;
   headers, footers, footnotes, and other parts are not scanned.
7. **Header/footer extraction incomplete** — only `header/footer.paragraphs`
   are read (tables ignored); fields are captured only for `w:fldSimple`,
   not complex field runs (`fldChar` begin/separate/end sequences).
8. **Statistics undercount** — word count and hyperlink count are computed
   from body paragraphs only; table cell content and header/footer parts are
   excluded.
9. **Nested table counting unverified** — `_table_counts` recursion needs
   verification against documents containing multiple nested tables.
10. **Extraction completeness conflates two concepts** —
    `ExtractionCompleteness.complete` currently equates "no unclassified
    blocks" with overall success; it needs a clearer distinction between
    preserved source content and successful section classification.

**Original ten Mission 2 defects — status after Mission 2.1.2.**

| # | Defect | Status | Supporting verification |
|---|---|---|---|
| 1 | Evidence builder skips table blocks | FIXED | Supported body tables/nested tables emit row records; `test_table_evidence_persisted_to_json` and `test_deep_provenance_survives_both_artifacts_and_snapshot_reconstruction`. Unsupported structures are inventoried separately. |
| 2 | Hyperlink relationship ID read from wrong element | FIXED | `test_hyperlink_relationship_resolution` + `test_unresolved_hyperlinks_are_recorded`. |
| 3 | Numbering format inferred, not resolved | PARTIALLY FIXED | Basic direct-numbering format/override tests pass; source-keyed cache tested. Combined overrides, raw-ID persistence, and style resolution remain OPEN. Unsupported definition combinations are gaps; inherited numbering produces unresolved diagnostics. |
| 4 | Source-location mapping for table-cell evidence incomplete | FIXED | Row-level granularity with typed contributing-cell references. Nine exact row/cell/paragraph/path cases in `test_exact_nested_row_and_contributing_cell_coordinates`, including two parents and two nesting levels. |
| 5 | `paragraph_position_in_cell` wrongly assigned to body paragraphs | FIXED | Rewritten regression tests assert body-paragraph locations have `paragraph_position_in_cell is None`. |
| 6 | Unsupported-content scanning not comprehensive | PARTIALLY FIXED | `test_known_unextracted_content_is_a_coverage_gap`: SDTs, header/footer tables, alternate content, unknown containers, non-default headers, comments/footnotes/endnotes. Actual extraction of those structures remains OPEN. |
| 7 | Header/footer extraction incomplete | OPEN | Default paragraphs retained; tables/non-default parts now make preservation incomplete. Phantom/duplicate parts and full field reconstruction remain OPEN. |
| 8 | Statistics undercount | PARTIALLY FIXED | Body, table cells, first nested level, and default header/footer paragraphs contribute. Deeper paragraph/word/hyperlink statistics and zero image counters remain OPEN (F-14). |
| 9 | Nested table counting unverified | FIXED | `_table_counts` source inspection confirms recursive row/cell accumulation. Multi-parent/sibling/deep hierarchy and unique row records verified by `test_table_ids_row_ids_and_full_locations_are_unique`; paragraph/word/hyperlink scopes remain a separate OPEN issue. |
| 10 | Extraction completeness conflates two concepts | FIXED | `test_completeness_components_are_independent_and_survive_reconstruction`, unknown-coverage test, and nine constructor/JSON invariant-rejection cases; see R-03 scope below. |

### Mission 2.1.2 — assigned HIGH-blocker remediation matrix

| Finding | Status | Implementation and actual verification |
|---|---|---|
| R-01: CLI input leakage | FIXED | Dedicated validation/serialization handlers, sanitized generic document ValueErrors/I/O failures; raw text only for controlled content-free configuration/integrity errors. Catalog/snapshot serialization tests, real malformed XML, and failed-replacement sanitization tests. |
| R-02: False-positive validation tests | FIXED | Temporary `PROJECT_ROOT` configured. Real Pydantic validation fails after corrupting the generated payload; each validator is asserted called. Catalog/snapshot failures independently preserve both previously validated artifacts and synthetic source bytes. |
| R-03: Dishonest completeness | FIXED | Snapshot persists `coverage_assessed`, coverage gaps, and unresolved details; unknown coverage defaults false. Model invariants reject contradictory flags. Recognized-heading cases isolate preservation, classification, and resolution failures; snapshot-only reconstruction preserves results. Deferred anchors/numbering limitations are diagnosed rather than declared extracted. |
| R-04: Nondeterministic nested identity | FIXED | Body IDs are nonnegative body block indexes; nested IDs are negative document-local preorder IDs reset each extraction. Numbering cache is keyed to source SHA-256. Same-instance/fresh-instance, sibling/multi-parent/deep-coordinate, uniqueness, persistence, and source-change/restore tests pass. |
| R-05: Destructive output aliases | FIXED | Source and both destinations are resolved before extraction; equal destinations/source aliases rejected before hashing, extraction, or writing. Ten relative/`..`/symlink cases preserve existing bytes. Exclusive unique temporary files prevent following an old `.tmp` symlink; replacement-failure test preserves prior targets. |

### Findings ledger — current disposition, with historical issues preserved

| Finding | Status | Actual remaining limitation |
|---|---|---|
| F-01 | FIXED — Mission 2.2.1 assigned warning path | Existing sanitized exception handlers retained. Extraction catalog/snapshot now undergo strict raw-field revalidation before serialization and use `warnings='error'`; independent mutable-value CLI tests verify both stdout/stderr and prior bytes. No global warning suppression. |
| F-02 / F-07 | FIXED — declared coverage/invariant scope | Existing formulas remain enforced. Paragraph/run/hyperlink allowlists and eleven newly diagnosed Word content kinds close assigned silent-loss cases; located diagnostics survive both artifacts. Unresolved/conflicting parts and unavailable roots are conservative. This is not a promise to interpret every future Word extension. |
| F-04 | OPEN | Combined `startOverride` + `lvl` precedence and raw numbering IDs are not fully represented. Missing format values can still appear as a legacy `"None"` format. The inventory flags these unsupported definition cases; it does not fix their interpretation. |
| F-06 | FIXED — Mission 2.2.1 | Actual section/package relationships replace convenience-property discovery. Absent parts remain absent; shared physical parts are represented once; owning URI, filename/hash, within-part locations/order, and linked source sections persist. Header/footer content remains outside body evidence. |
| F-09 / internal anchors | OPEN | Row hyperlinks are flattened without per-link cell/paragraph references. `w:anchor` targets are not retained correctly; their presence is now a coverage gap. |
| F-10 | FIXED — assigned accounting/diagnostic scope; interpretation unsupported | Default/first/even references, missing/external/wrong-kind/type targets, duplicate physical parts, unavailable roots, and empty parts are accounted for. Header/footer tables and non-default-only content remain explicitly unsupported with owning-part/available-location diagnostics, rather than claimed extracted. |
| F-11 | PARTIALLY FIXED — assigned detection scope implemented | moveTo/moveFrom/dir/bdo and ruby/sym/softHyphen/delText/delInstrText/contentPart/subDoc, plus unknown direct inline extensions, now get located persisted gaps. Existing SDT/alternate/notes/comments diagnostics retained. Full semantic interpretation remains intentionally unsupported/later work. |
| F-12 | OPEN | Source-keyed numbering caching is implemented; selected parser failures still collapse to partial/default definitions rather than dedicated parser-failure diagnostics. |
| F-14 | FIXED — Mission 2.2.4 | Table statistics recurse through all nested levels for table, row, cell, and paragraph counts, word counts, empty paragraphs, and hyperlinks. Media counters (`drawing_count`, `textbox_count`, `embedded_object_count`, `image_count`) are accurately populated from body and header/footer roots. Empty header/footer paragraphs are recorded in `empty_paragraph_count`. |
| F-15 | PARTIALLY FIXED | Original historical description restored from the human-provided Mission 2 review history: **stale documentation**. Current extraction documentation, README extraction/status notes, and this status file are reconciled with Mission 2.2.4. Broader documentation review remains F-25; no additional historical details are inferred. |
| F-18 | FIXED — Mission 2.2.4 | Bounded cleanup of unused imports and dead code in `evidence.py`, `extractor.py`, and `publication_integrity.py`. All public and tested interfaces remain intact. |
| F-19 | OPEN | Field capture keeps only the first instruction fragment, does not reconstruct complex begin/separate/end sequences, and does not evaluate fields. Field structures are coverage gaps. |
| F-20 | FIXED — existing behavior independently regression-verified | Original historical description restored from the human-provided review history: **unresolved lists not reset**. `extract()` resets both lists, nested sequence, capture, and inventory state. `test_unresolved_lists_are_reset_between_changed_and_clean_source_calls` and `test_failed_capture_also_clears_old_unresolved_lists` verify isolation. No additional historical details are inferred. |
| F-21 | OPEN | Style-inherited numbering is diagnosed as unresolved, including custom/base styles. Full format inheritance/resolution is deferred; legacy list-style format labels remain inferred. |
| F-22 | FIXED — Mission 2.2.4 | `snapshot.sections` is established as the single source of truth for section assignment and unclassified detection in evidence extraction. Publication pair validation strictly enforces that catalog section references and unclassified block indexes agree with snapshot sections. |
| F-23 | FIXED — Mission 2.2.4 | CLI `extract-master` output explicitly labels all paragraph counts by scope (`body_paragraphs`, `table_paragraphs`, `header_paragraphs`, `footer_paragraphs`, `total_paragraphs`) and prints all media and structure counters. Verified by dedicated CLI summary tests. |
| F-24 | FIXED — Mission 2.2.4 | Unclassified table rows containing contact details in any cell/line are typed as `EvidenceType.CONTACT`. Ragged rows with fewer cells than `column_count` are supported and verified in publication pair integrity checks. |
| F-25 | OPEN | Documentation outside this assigned remediation and historical agent claims still need independent review. Current guarantees/limitations are scoped explicitly in `docs/extraction.md`. |
| F-17 | PARTIALLY FIXED | Reader-visible two-artifact inconsistency resolved via manifest commit point (PublicationManifest 1.0.0 and load_committed_pair); operating-system fsync/power-loss durability remains unpromised. |
| M2-A01 | FIXED — Mission 2.2.1 | Capture filename/bytes/digest through the shared safe input reader; python-docx and numbering read streams over those bytes. CLI has no independent pathname hash/parse reads; both artifact/provenance hashes match the immutable capture. A→B→A/after-capture changes and exact parser-input hashes are tested. |

---

## E. Current Stopping Point

- **Mission 3.3 remediation — Stage E is `IMPLEMENTED — AWAITING INDEPENDENT REVIEW`.**
  Stages A–D are APPROVED. The final integrated workflow, 76-case portable
  independent acceptance matrix, 23 full managed pipeline cases and current
  documentation are complete (§N). Final authorized run: 1,419 collected /
  1,412 passed / 7 documented private-source exclusions / 0 failed / 11 existing
  warnings. No prior test cases were modified or weakened during Stage E.
  Stage E and Mission 3.3 are not approved; Phase 4 is not implemented.

- **Mission 3.3 remediation — Stage A is `APPROVED`** by explicit project-owner decision in the Stage B authorization, following the final SA-R01 independent re-review. The original Mission 3.3 implementation entry above is retained as historical evidence, including its historical test-count claim; the current verified Stage A baseline is 1,087 collected / 1,080 passed / 7 private-source exclusions / 11 warnings. Mission 3.3 and parent Mission 3 remain in progress and unapproved.
- **Mission 3.3 remediation — Stage B is `APPROVED`** by the project owner via
  explicit conditional authorization in the Stage B final review + Stage C
  implementation assignment, recorded after an independent review verdict of
  **APPROVE WITH NON-BLOCKING OBSERVATIONS** (F-1/F-2 verified fixed; B-01/B-02
  and Stage A guarantees verified intact; full authorized regression 1,145
  collected / 1,138 passed / 7 exclusions / 11 warnings). Non-blocking
  observations preserved: plan schema remains `1.0.0` (owner-authorized
  additive `requirement_associations`), and heading-styled factual technical
  content may be excluded (documented limitation). The approved Stage B
  selection semantics are preserved by Stage C's verified implementation.
  Broader output-schema and per-evidence plan-provenance work remains deferred.
  No Stage C or Mission 3.3 approval is implied.
- **Mission 3.3 remediation — Stage C is `APPROVED`** by explicit owner approval
  in the Stage D assignment, following the final independent verdict
  **APPROVE WITH NON-BLOCKING OBSERVATIONS** (§L). The SC-R1/SC-R2/SC-R3
  corrections and their pre-approval verification remain preserved below.
  Merge-connected identity/supporting rows are retained together, repeated
  sections use their enclosing numbering, and surviving skill candidates fill
  available budget slots. Unmarked/ambiguous sources remain explicitly
  unresolved. Latest authorized regression: 1,210 collected / 1,203 passed /
  7 exclusions / 11 existing warnings. Three remediation files changed:
  `section_planner.py`, `test_section_planner_stage_c.py`, and this handoff.
  No existing tests were weakened or removed; approved Stage A/B files are
  preserved. Stage D is implemented and awaiting independent review;
  Mission 3.3 remains unapproved.
- **Mission 3.3 remediation — Stage D is `APPROVED`** under the owner's explicit
  conditional authorization in the Stage D review + Stage E assignment, after
  an independent **APPROVE WITH NON-BLOCKING OBSERVATIONS** verdict.
  The schema-2 contract and source-aware publication boundary are complete (§M).
  Latest full regression: 1,320 collected / 1,313 passed / 7 documented exclusions /
  0 failed / 11 existing warnings. All 14 assigned external schema/provenance
  failures are resolved. Earlier A/B/C approvals and selection behavior are
  preserved. Stage E is implemented and awaiting independent review;
  Mission 3.3 remains unapproved.
- **Stage B selection limitations preserved:** only conservative single-token technical background labels become standalone "other" skills; multiword/compound/prose records remain source evidence and become prioritized labels only through supported matcher components. Association evidence IDs retain matcher decisions, whose first-witness lexical behavior is unchanged; eligible atomic background duplicates contribute additional overall skill evidence IDs. Heading filtering uses snapshot source boundaries or recorded heading metadata/canonical legacy section identifiers, not a new upstream classification. Stage C now supplies source grouping, relevance budgets, ordering, accounting, and identity binding; unmarked-entry inference and broader output validation remain documented limitations.
- **Mission 1.2** has been implemented according to its engineering report;
  independent review is pending.
- **Phase 2 (Candidate Intelligence & Evidence Extraction)** is **`APPROVED`**
  as an aggregate deliverable by explicit human decision (2026-11-20), following the
  completed Phase 2 Comprehensive Independent Engineering Review and
  Approval-Readiness Reconciliation Report. Missions 2, 2.1, 2.1.1, 2.2.1, 2.2.2,
  2.2.3, and 2.2.4 are technically complete, fully verified, and subsumed under
  the approved Phase 2 deliverable, while retaining their historical review and
  remediation records in §B and §D. Mission 2.1.2 retains its earlier human approval.
- **Mission 2.2.4 — Rich-Text Fidelity, Recursive Statistics, Section SOT & Row Contact Typing**
  is `IMPLEMENTED — REVIEW APPROVED`. Resolved N-4, N-5, WP-1 (F-14), WP-2 (F-22), WP-3 (F-23),
  WP-4 (F-24), WP-6 (F-18), and WP-5. 897 collected, 889 passed, 1 skipped, 7 deselected,
  0 failed, 11 unchanged warnings; 16 new automated cases. Dependency and diff checks pass.
  Independent review completed with verdict APPROVE WITH NON-BLOCKING OBSERVATIONS;
  subsumed under Phase 2 aggregate human approval.
- **Mission 2.2.1 — Privacy, Source Binding & Honest Coverage** is
  `IMPLEMENTED — AWAITING REVIEW`. The assigned seven corrections and
  compatibility regressions passed the full privacy-safe suite: 812 collected,
  805 passed, 7 deselected, 0 failed, 0 skipped, 11 unchanged warnings.
  Audited in Phase 2 comprehensive review; subsumed under Phase 2 aggregate human approval.
- **Mission 2.1 — Extraction Remediation** has been **implemented**
  per its engineering report (table evidence persistence, hyperlink relationship
  resolution, numbering resolution, source locations, statistics, completeness
  semantics, dual-artifact CLI persistence); subsumed under Phase 2 aggregate human approval.
- **Mission 2.1.1 — Critical Evidence Integrity & Extraction Remediation** has been
  **implemented** per its engineering report: sanitized Pydantic error handling,
  honest derived completeness, effective source-location tests, corrected numbering
  overrides, recursive nested-table evidence, typed cell-source provenance, table
  hyperlink preservation, collision-free nested identifiers, and missing data-integrity
  CLI tests.
- **Mission 2.1.1's independent review rejected its completeness/privacy/test
  claims.** The five assigned HIGH blockers have been remediated in Mission 2.1.2;
  the matrices distinguish these fixes from remaining OPEN extraction capabilities.
- **Mission 2.1.2 — Integrity and Safety Blockers** is **`APPROVED`** —
  independent review verdict APPROVE (R-01–R-05 VERIFIED FIXED); recorded per
  the Mission 3.1 assignment instruction. Verification used synthetic data
  exclusively; no private input/artifact contents were read, regenerated, or
  modified. Phase 2 as a whole is now **APPROVED** by explicit human decision.
- **Mission 3.1 — Job Description Analyzer** is `APPROVED` — the independent
  **DO NOT APPROVE** verdict (M31-01–M31-09) was remediated in Mission 3.1.1;
  the independent Mission 3.1.1 review returned **APPROVE WITH NON-BLOCKING
  OBSERVATIONS** (all nine findings verified fixed); explicit human approval was
  granted for Mission 3.1 at the Mission 3.1 closeout (2026-11-17); Mission 3.1
  is recorded `APPROVED`.
- **Mission 3.1.1 — Security, Grounding & Validation Remediation** is
  **`APPROVED`** — recorded at the Mission 3.1 closeout (2026-11-17) after the
  independent review returned **APPROVE WITH NON-BLOCKING OBSERVATIONS** and the
  human explicitly approved. All nine findings were remediated, including shared
  CLI filesystem protection and privacy-safe verification/provenance. The final
  suite collected 516 cases: 509 passed, 0 failed, 7 private-source cases
  deselected, 11 unsuppressed legacy warnings. No private contents were read, no
  live provider requests were made, and nothing was staged or committed. The live
  AgentRouter smoke test at closeout was **not executed** (runtime credentials
  unavailable). This describes the historical Mission 3.1 closeout.
- **Mission 3 (parent)** remains **in progress** and was **not approved**;
  the approved child missions are 3.1, 3.1.1, 3.2, and 3.2.1.
- **Mission 3.2 — Local Candidate Evidence Matcher** is **`APPROVED`**.
  Its original **DO NOT APPROVE** review was remediated in Mission 3.2.1;
  independent review of the corrected implementation returned **APPROVE WITH
  NON-BLOCKING OBSERVATIONS**. Human approval explicitly confirmed in the
  Mission 2 finalization-audit assignment (2026-11-18).
- **Mission 3.2.1 — Matching Accuracy & Evidence Integrity Remediation** is
  **`APPROVED`**, with all M32-01–M32-11 independently VERIFIED FIXED (§I).
  Independent review reproduced 705 collected / 698 passed / 7 deselected /
  0 failed / 0 skipped / 11 unchanged warnings and returned **APPROVE WITH
  NON-BLOCKING OBSERVATIONS**. O-1–O-4 are preserved in §I; human approval
  explicitly confirmed in the Mission 2 finalization-audit assignment
  (2026-11-18). Mission 2.1.2 and Missions 3.1/3.1.1 remain `APPROVED`;
  Mission 2 remains NOT APPROVED and parent Mission 3 remains in progress/not approved.
- **Mission 1.3 / 1.3.1 infrastructure work is committed and pushed:**
  - The **initial baseline is committed and pushed** — commit
    **`fad3e90`** (`chore: establish resume assistant foundation`) on
    `main`, pushed to `origin/main`.
  - The **GitHub remote is configured**: `origin` →
    `https://github.com/AlaaBadawii/Resume_Assistan.git`.
  - The **initial working tree was clean after the push** (re-verified at
    the start of Mission 1.3.2).
  - **Future implementation missions must preserve the existing baseline**
    (no history rewriting, no force pushes — `AGENTS.md` §F).
  - Repository visibility (private) remains **user-reported and
    unconfirmed** — not independently verified.

---

## F. Verification History

Environment: Python **3.12.14**, project `.venv/`, pytest **9.2.0**, Linux.
Historical implementation runs: 2026-11-15; Mission 2.1.2 verification: 2026-11-16.

| Result | Provenance |
|---|---|
| `pytest` → **37 passed**, 3 warnings (pre-existing `FutureWarning` from python-oxml) | **Agent-executed** (Mission 1.2 end and Mission 1.3 baseline) |
| `pip check` → "No broken requirements found" | **Agent-executed** (Mission 1.3 baseline) |
| `.gitignore` real-Git verification: 10 sensitive paths ignored, 12 legitimate paths trackable, 0 files tracked pre-staging | **Agent-executed** (Mission 1.3, `git check-ignore` in initialized repo) |
| Master Resume SHA-256 unchanged by extraction (`a2796902…8f42a5`) | **Agent-executed** (test `test_master_resume_untouched`, Mission 1.3 run) |
| 10 Mission 2 defects confirmed by line-level source inspection (`evidence.py`, `extractor.py`) | **Independently inspected** (Mission 1.3 code review) |
| GitHub repository creation (`AlaaBadawii/Resume_Assistan`) | **User-reported** — not independently verified |
| `origin` remote configuration = `https://github.com/AlaaBadawii/Resume_Assistan.git` | **User-executed; independently confirmed** through supplied `git remote -v` output (Mission 1.3.2) |
| Initial commit creation (`fad3e90`) and push to `origin/main` | **User-executed**, based on supplied terminal output (Mission 1.3.2) |
| Branch `main` tracking `origin/main` | **User-executed; confirmed** through supplied terminal output (Mission 1.3.2: `git status -sb`, `git branch -vv`) |
| Post-push repository state (`fad3e90`, clean working tree) | **Independently confirmed** by agent via `git status -sb`, `git log -1`, `git remote -v`, `git branch -vv` (Mission 1.3.2) |
| Repository visibility (private) | **Not independently verified — explicitly unconfirmed** |
| Full test suite re-run by the human user | **Not yet verified** |
| Rendering fidelity in Microsoft Word / LibreOffice | **Not verified** — no Word/LibreOffice in this environment (preview is approximate by design) |
| Table evidence persistence + persisted-JSON integration | **Agent-executed** (Mission 2.1) — synthetic table fixture through CLI → persisted JSON contains `table_context` records |
| Hyperlink resolution (relationship + URL) | **Agent-executed** (Mission 2.1) — Master Resume: 10/10 hyperlinks resolved; relationship test green |
| Numbering resolution (numbering.xml) | **Agent-executed** (Mission 2.1) — numbering definitions loaded; per-numId/level resolution verified |
| Master Resume SHA-256 unchanged by extraction | **Agent-executed** (Mission 2.1 CLI runs) |
| Pydantic ValidationError sanitized; synthetic marker not printed | **Agent-executed** (Mission 2.1.1 regression test) |
| Numbering overrides (lvlOverride/w:lvl/w:numFmt, startOverride fallback) resolved at paragraph level | **Agent-executed** (Mission 2.1.1 regression test) |
| Nested table evidence survives snapshot -> catalog -> JSON -> reload | **Agent-executed** (Mission 2.1.1 regression test) |
| Full test suite | **Agent-executed** — **56 passed**, 10 warnings (python-oxml FutureWarnings) (Mission 2.1.1) |
| Mission 2.1.1 independent review | **Rejected by independent review**; its privacy/preservation tests did not reach validation. Historical implementation reports above are claims, not substitute verification. |
| Mission 2.1.2 regression run | **Agent-executed** — **118 collected, 111 passed, 7 private-source cases deselected, 11 FutureWarnings**. All enabled pre-existing tests and new regression cases pass. |
| Mission 2.1.2 dependencies | `.venv/bin/python -m pip check` — **No broken requirements found**. No dependency installation/change. |
| Synthetic source/artifact integrity | Successful CLI, validation/serialization failures, alias rejection, repeated extraction, and replacement-failure tests preserve the expected source/previous artifact bytes. |
| Real Master Resume/private JSON integration in Mission 2.1.2 | **NOT EXECUTED / NOT INSPECTED** — prohibited by the assigned privacy scope. No real-source integrity claim is inferred from synthetic tests. |
| Mission 3.1 implementation-agent full-suite report | **Agent-reported unrestricted run** — **189 collected, 189 passed, 0 failed, 0 deselected, 11 warnings**. The shown invocation omitted the seven private-source exclusions; those cases read the real Master Resume. This result is not synthetic-only/privacy-safe and was not repeated during remediation. |
| Mission 3.1 independent read-only review | **Independently reproduced privacy-safe run** — **189 collected, 182 passed, 0 failed, 7 private-source cases deselected, 11 warnings**. Review verdict **DO NOT APPROVE**; M31-01–M31-09 recorded. No private contents or live provider requests were used in that review. |
| Mission 3.1 dependencies | `.venv/bin/python -m pip check` — **No broken requirements found**. No new dependencies added (stdlib `urllib` HTTP transport). |
| Live AgentRouter API in Mission 3.1 | **Reported not executed** — client tests used fake transports. Live provider compatibility is unverified. |
| Original Mission 3.1 private-read claim | **WITHDRAWN / CORRECTED** — the unrestricted 189-pass invocation included seven private-source tests. The earlier blanket statement that no private contents were read during implementation/testing is unsupported by that invocation and must not be repeated. No historical private contents were inspected to make this documentation correction. |
| Mission 3.1.1 final privacy-safe suite | **Agent-executed** — **516 collected, 509 passed, 0 failed, 7 deselected, 0 skipped, 11 legacy FutureWarnings**. All enabled prior regressions and remediation cases pass; warnings were not suppressed. |
| Mission 3.1.1 checks | `.venv/bin/python -m pip check`: **No broken requirements found**. `git diff --check`: **pass**. No dependency installation/change. |
| Mission 3.1.1 transport/security probes | Real stdlib transport exercised only against synthetic loopback HTTP/TLS servers (including cross-host redirects and HTTPS downgrade rejection). Eleven separate in-memory safeguard-removal checks were all detected by existing/new tests; not counted as additional pytest cases. No live provider requests. |
| Mission 3.1.1 privacy | Synthetic JDs and temporary fixtures under `/tmp/opencode` only. `.env`, real Master Resume, and real candidate artifact contents were not read or regenerated. Private-source integration remains explicitly excluded. |
| Mission 3.1 closeout approvals (2026-11-17) | Human explicitly approved Mission 3.1 and Mission 3.1.1. Independent review verdict: APPROVE WITH NON-BLOCKING OBSERVATIONS. All nine M31 findings VERIFIED FIXED; independent safeguard-removal checks detected 17/17. Mission 2.1.2 remains APPROVED; Mission 2 remains NOT APPROVED; Mission 3 remains in progress and not approved. |
| Mission 3.1 closeout live smoke test (2026-11-17) | NOT EXECUTED - STOPPED AT CREDENTIAL GATE; runtime AGENTROUTER credentials unavailable; no synthetic job text was sent and no implementation change was made. |
| Mission 3.2 final privacy-safe suite and independent reproduction (2026-11-17) | **532 collected, 525 passed, 7 deselected, 0 failed, 11 existing warnings**; **16 new Mission 3.2 cases**, distinct from total collection. Independently reproduced in the read-only review. The earlier 15-new/524-collected/509-passed entry was inaccurate and is corrected by M32-05. `pip check` and `git diff --check` passed. No real candidate contents or live provider requests. |
| Mission 3.2.1 full privacy-safe suite (2026-11-17) | **Agent-executed — 705 collected, 698 passed, 7 deselected, 0 failed, 0 skipped, 11 existing FutureWarnings**, 13.53s. **173 additional automated cases** beyond Mission 3.2 (132 in `test_matching_remediation.py`, 41 additional CLI cases); 189 matcher acceptance cases in total. Existing 16 Mission 3.2 cases were retained/adapted to the corrected contract. Manual probes are not counted as tests. |
| Mission 3.2.1 dependencies and diff check | `.venv/bin/python -m pip check`: **No broken requirements found**; `git diff --check`: **pass**. No dependency changes/installations; original extraction, analyzer, input models, and private-output implementation preserved. |
| Mission 3.2.1 privacy and network verification | All inputs/fixtures synthetic; test artifacts under `/tmp/opencode`. Temporary audit guards reported **external_network_attempts=0, real_private_reads=0, loopback_connections=10**. Matching itself is offline; these ten connections belong to the existing local HTTP/TLS transport tests. No `.env`, real Master Resume, or real candidate artifact contents read; no external provider requests or candidate transmission. |
| Mission 3.2.1 independent read-only review | **APPROVE WITH NON-BLOCKING OBSERVATIONS**, scoped only to Mission 3.2.1 and the remediated Mission 3.2 implementation. M32-01–M32-11 independently VERIFIED FIXED; **705 collected, 698 passed, 7 deselected, 0 failed, 0 skipped, 11 unsuppressed existing FutureWarnings**, 13.00s. Collection confirmed 189 matcher cases (11/132/46); 183 external synthetic probes passed. The single external `sitecustomize.py` was read/audited before execution; no application/test/import/discovery patches were found. Reported counters: external network 0, real private reads 0, existing loopback connections 10. `pip check`/`git diff --check` passed; working-file/index hashes, HEAD, and staging unchanged. O-1–O-4 are preserved in §I. The reviewer did not record human approval. |
| Mission 3 approval confirmation (2026-11-18) | The human explicitly identified **Missions 3.1, 3.1.1, 3.2, and 3.2.1 as already APPROVED** in the Mission 2 finalization-audit assignment. Recorded accordingly; the earlier M31/M32 rejection history and subsequent **APPROVE WITH NON-BLOCKING OBSERVATIONS** outcomes remain intact. M31 limits/observations remain in §H; M32 observations remain in §I. Mission 2.1.2 remains APPROVED; Mission 2 remains NOT APPROVED; parent Mission 3 remains in progress/not approved. |
| Mission 2 finalization audit scoped reproduction | Historical audit: **159 collected, 152 passed, 7 deselected, 0 failed, 0 skipped, 11 existing FutureWarnings**, 11.31s; 48 external synthetic scenarios inspected. Private accesses/network attempts 0, including loopback. An earlier guard rejected 41 legitimate directory-relative temporary writes; the corrected external-CWD run passed and repository hashes confirmed no code/test modifications. These historical scoped totals are distinct from the later full-repository run below. |
| Mission 2.2.1 full privacy-safe regression (2026-11-18) | **Agent-executed — 812 collected, 805 passed, 7 deselected, 0 failed, 0 skipped, 11 unchanged FutureWarnings**, 22.84s. **107 new automated cases**: 17 source capture, 8 extraction privacy, 29 inline/diagnostics, 44 header/footer integrity, 9 approved-consumer compatibility. Existing 189 matcher and all other enabled regressions pass. This records implementation verification, not human approval. |
| Mission 2.2.1 focused runs | Captured-input/privacy + existing CLI/matcher CLI/nested regressions: **121 passed, 1 existing warning**, 5.30s. Coverage/parts + existing extraction/completeness/evidence: **140 collected, 136 passed, 4 private tests deselected, 10 existing warnings**, 6.57s. Intermediate new compatibility/capture/privacy/coverage/parts matrix: **100 passed, 0 warnings**, 5.06s. Later added edge cases are included in the final 107-new/812-total full run, not added to historical focused counts. |
| Mission 2.2.1 dependency/diff checks | `.venv/bin/python -m pip check`: **No broken requirements found**. `git diff --check`: **pass**. No dependency changes/installations. Private-output, section detector, matcher/models, job analyzer/models, prompt, client, AGENTS.md, and pyproject.toml hashes unchanged from mission start. Index/HEAD unchanged; nothing staged/committed/pushed. |
| Mission 2.2.1 verification-guard audit | The external `/tmp/opencode/mission221-verification/guard/` contains one fully read/audited `sitecustomize.py`: stdlib audit hook only, no application/test/import/discovery patches or warning filters. It resolves existing immediate-call-site directory FDs and actual fdopen targets instead of assuming repository CWD for relative temporary names. Full-suite report: **private_access_attempts=0, external_network_attempts=0, loopback_connections=10, repository_write_attempts=0, directory_relative_opens=3057**. The human expressly authorized the existing synthetic loopback HTTP/TLS tests; no external requests or real candidate reads/regeneration. All fixtures/artifacts under `/tmp/opencode`; bytecode/cache writes disabled. |

Warnings were not suppressed. The warning originates in python-docx/lxml's
`makeelement`, triggered by legacy fixture calls passing an XML element as
`OxmlElement`'s second argument (`attrs`), not a supported parent argument.
New coverage/deep-table fixtures use valid construction APIs. The source-change
cache test reuses the legacy numbering fixture and contributes one warning.

Mission 2.1.2 verification, the independent Mission 3.1 review, and the final
Mission 3.1.1 run explicitly excluded these seven real-source tests. The original
unrestricted Mission 3.1 implementation-agent invocation did **not** use them:

- `tests/test_evidence.py::test_evidence_ids_are_deterministic`
- `tests/test_evidence.py::test_catalog_pydantic_round_trip`
- `tests/test_extractor.py::test_extraction_does_not_modify_source`
- `tests/test_extractor.py::test_numbering_resolution_from_numbering_xml`
- `tests/test_section_detector.py::test_master_resume_top_level_section_count`
- `tests/test_section_detector.py::test_unclassified_blocks_for_preamble`
- `tests/test_template.py::test_master_resume_untouched`

**Exact Mission 3.1.1 subprocess invocation (2026-11-17):**

```bash
.venv/bin/python -m pytest -p no:cacheprovider \
  --basetemp=/tmp/opencode/mission311-private-safe-suite-g4t5ttar/fixtures \
  --deselect=tests/test_evidence.py::test_evidence_ids_are_deterministic \
  --deselect=tests/test_evidence.py::test_catalog_pydantic_round_trip \
  --deselect=tests/test_extractor.py::test_extraction_does_not_modify_source \
  --deselect=tests/test_extractor.py::test_numbering_resolution_from_numbering_xml \
  --deselect=tests/test_section_detector.py::test_master_resume_top_level_section_count \
  --deselect=tests/test_section_detector.py::test_unclassified_blocks_for_preamble \
  --deselect=tests/test_template.py::test_master_resume_untouched
```

The wrapper created and subsequently cleaned the external temporary root.
Subprocess environment: `PYTHONDONTWRITEBYTECODE=1`,
`PYTEST_DISABLE_PLUGIN_AUTOLOAD=1`, `PYTEST_ADDOPTS=''`, `GIT_OPTIONAL_LOCKS=0`,
and `TMPDIR=/tmp/opencode/mission311-private-safe-suite-g4t5ttar`. Inherited
`AGENTROUTER_*` settings were removed; tests provided synthetic settings.
No blanket warning filter or private-source monkeypatch was used to turn an
unrestricted run into a claimed privacy-safe result.

**Exact Mission 3.2.1 full-suite invocation (2026-11-17):**

```bash
env -u AGENTROUTER_API_KEY -u AGENTROUTER_BASE_URL \
  -u AGENTROUTER_MODEL -u AGENTROUTER_TIMEOUT_SECONDS \
  PYTHONDONTWRITEBYTECODE=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
  PYTEST_ADDOPTS='' GIT_OPTIONAL_LOCKS=0 TMPDIR=/tmp/opencode \
  PYTHONPATH=/tmp/opencode/m321-verification \
  .venv/bin/python -m pytest -p no:cacheprovider \
  --basetemp=/tmp/opencode/m321-full-suite \
  --deselect=tests/test_evidence.py::test_evidence_ids_are_deterministic \
  --deselect=tests/test_evidence.py::test_catalog_pydantic_round_trip \
  --deselect=tests/test_extractor.py::test_extraction_does_not_modify_source \
  --deselect=tests/test_extractor.py::test_numbering_resolution_from_numbering_xml \
  --deselect=tests/test_section_detector.py::test_master_resume_top_level_section_count \
  --deselect=tests/test_section_detector.py::test_unclassified_blocks_for_preamble \
  --deselect=tests/test_template.py::test_master_resume_untouched
```

The external `sitecustomize.py` in `/tmp/opencode/m321-verification` is a
verification aid, not an application dependency. It guards real private-artifact
file opens and external DNS/connections, allowing synthetic loopback transport
tests. No warnings were filtered, and exactly the seven listed tests were
excluded. Matcher-only runs used the same privacy/cache/temporary environment
and collected 189 passing cases across the three matching test modules.

---

## G. Next Recommended Actions

1. **Independent review of Mission 3.1.1 remediation** — completed
   (2026-11-17); verdict **APPROVE WITH NON-BLOCKING OBSERVATIONS**;
   Missions 3.1 and 3.1.1 are now recorded `APPROVED` (human approval granted).
2. **Live AgentRouter smoke test** — pending runtime credentials; not
   executed at the Mission 3.1 closeout (credential gate).
3. **Independent review of Mission 3.2.1** — completed; verdict **APPROVE WITH
   NON-BLOCKING OBSERVATIONS**, all M32-01–M32-11 VERIFIED FIXED. Missions 3.2
   and 3.2.1 are now recorded `APPROVED` per explicit human confirmation
   (2026-11-18). Non-blocking observations O-1–O-4 remain in §I.
4. Human review of Mission 1.2 output (template/preview) → possible
   `APPROVED` status.
5. Confirm repository visibility (private) on GitHub — currently
   user-reported only.
6. Review the OPEN findings (§D) before assigning further extraction work.
   Evidence selection, section ordering, and resume rendering remain
   unimplemented and outside the completed Mission 3.1 scope.
7. First live AgentRouter call (when explicitly authorized) may be used to
   validate the researched provider contract; it was deliberately not
   attempted during Mission 3.1.

Roadmap items 2–3 are **not implemented** and must not be treated as done
(Mission 3.1 completed only the JD *analysis* portion of roadmap item 2).

---

## H. Mission 3.1.1 — remediation matrix and limits

Statuses below describe implemented corrections, not human approval.

| Finding | Remediation | Actual verification |
|---|---|---|
| M31-01 HIGH: authenticated redirects | Custom urllib opener rejects all redirects; no redirected GET or credential-forwarding request is constructed. | Real loopback HTTP 301/302/303/307/308, same-origin redirect, TLS cross-host and HTTPS-to-HTTP tests; destination request logs remain empty. |
| M31-02 HIGH: factual grounding | Schema 2.0.0 scalar `{value, quotation}` and source-phrase list entries; own-evidence matching plus source quotation/context checks. All semantic field assignments/classifications carry AI-unverified status. | Every scalar/list value and language level, unrelated quotes despite values occurring elsewhere, unsupported paraphrases, and adversarial unsupported model responses tested. No live-model injection immunity claimed. |
| M31-03 HIGH: symlink confinement | Trusted project anchor, no-follow component opens, pinned handles across processing, identity checks, exclusive mode-0600 temporaries, descriptor-relative replace/cleanup for both commands. | Private-root/intermediate/destination symlinks, generation-time swaps, temporary-creation swap, final-syscall swap, hardlink aliases, source/prior-artifact preservation, valid writes, unsupported-platform failure. |
| M31-04 MEDIUM: incomplete envelopes | Required envelope fields; completed top/message status; null error/incomplete details; one assistant message; malformed/refusal/tool/partial content rejected. | Completed success plus all noncompleted statuses, falsy non-null details, invalid content, reasoning shape, duplicate JSON keys and nonstandard constants. |
| M31-05 MEDIUM: permissive schema | All 15 raw keys required; explicit nullable scalars; strict Pydantic types/extra-forbid; model-supplied application metadata rejected. | Every missing top key, empty/unrelated/partial objects, nested extras, canonical metadata, and independent artifact round-trip failures. |
| M31-06 MEDIUM: blank/duplicate/conflict entries | Nonblank alphanumeric-containing verbatim text; full-field identical-item deduplication; cross-classification strip/casefold name conflicts rejected. | Empty/whitespace/punctuation-only entries, case-sensitive own matching, shared quote/distinct claims, distinct contexts, same-name cross-list conflicts, explicit uncertain bucket. |
| M31-07 MEDIUM: invalid configuration | Structural hostname/IP/port/base-path validation; HTTPS except numeric loopback HTTP; URL credentials/query/fragments rejected; finite positive timeouts in both entry points. | Malformed URL matrix, endpoint construction, NaN/infinity/overflow, missing/blank/control-character settings, injectable transport retained. |
| M31-08 MEDIUM: verification provenance | README, job-analysis doc and this status file separate original unrestricted report, independent private-safe review, and current remediation results. | Counts/exclusions reconciled with the actual invocation; original no-private-read claim withdrawn; actual final result recorded above. |
| M31-09 LOW: directory input | Regular-file stat plus opened-descriptor check, controlled missing/read/identity/UTF-8 errors; expected errors 2, unexpected implementation/transport errors 3. | Directory, missing, permission-denied, FIFO, valid/default input/output, real client error propagation, and preserved aliases/artifacts. |

**Remaining limits:** substring chains are not semantic entailment or extraction
recall verification; even literal source instructions can pass textual checks,
so interpretations remain AI-unverified. No live provider compatibility or
live-model injection resistance has been established. Linux descriptor semantics
are verified; unsupported platforms (including Windows) fail closed. Pinned
handles prevent symlink redirection, not directory renaming or privileged/direct
file modification; no fsync, pathname-visibility lock, or two-file transaction is
claimed. See `docs/job_analysis.md` for the full contract/platform policy.

---

## I. Mission 3.2 independent review and Mission 3.2.1 remediation checklist

Original independent verdict: **DO NOT APPROVE**, scoped to Mission 3.2.
That original review was read-only; its eleven findings are preserved below.
Independent read-only review of Mission 3.2.1 subsequently confirmed all eleven
corrections and returned **APPROVE WITH NON-BLOCKING OBSERVATIONS**, scoped to
Mission 3.2.1 and the remediated Mission 3.2 implementation. Human explicitly
confirmed both missions as already **APPROVED** in the Mission 2 finalization-audit
assignment (2026-11-18). Only that human instruction records approval; neither
parent Mission 2 nor parent Mission 3 is approved by this entry.

The implementation evidence below was independently checked against source,
synthetic challenges, and the regression results in §F. All M32-01–M32-11 are
**VERIFIED FIXED**. Their original findings and correction details remain intact.

| Finding | Original severity / issue | Remediation status |
|---|---|---|
| M32-01 | HIGH: substring fragments incorrectly support Java/JavaScript, Go/Google, R/Resume, SQL/MySQL | IMPLEMENTED — `exact_normalized_phrase_v2`, Unicode case/whitespace normalization, technical-token boundaries; all four negatives and C++/C#/.NET/Node.js positives automated |
| M32-02 | MEDIUM: distinct same-name requirements share an ID | IMPLEMENTED — full SHA-256 over category + complete source item/quotation/context; source indices and final uniqueness checks; same-name/distinct-quote/context reproducibility tests |
| M32-03 | MEDIUM: match records omit source-location provenance | IMPLEMENTED — compact ID/type/file/hash/location/section/contributing-cell references; component-to-ID links; exact source validation and nested-table JSON round-trip tests |
| M32-04 | MEDIUM: mandatory acceptance tests missing | IMPLEMENTED — automated compound/full/partial/category/coverage/duplicate/conflict/unknown-ID/privacy/symlink/alias/race/write-failure matrix; 189 matcher cases total |
| M32-05 | MEDIUM: verification counts inaccurate | IMPLEMENTED — historical Mission 3.2 corrected to 16 new cases, 532 collected / 525 passed / 7 deselected / 0 failed / 11 warnings; current remediation totals separately recorded in §F |
| M32-06 | LOW: statuses unconstrained; unused constants | IMPLEMENTED — Literal requirement/component/coverage statuses and versions/methods; unused status constants removed; valid/invalid state tests |
| M32-07 | LOW: assessed-but-empty evidence produces a negative search result | IMPLEMENTED — actual empty record lists force `empty_evidence_catalog` and NOT_ASSESSABLE, even with otherwise complete flags or contradictory summary counts |
| M32-08 | LOW: proficiency levels combined across unrelated records | IMPLEMENTED — language/level must share a supporting record; language-only partial result; same-record, cross-record, later-full-record, and level-only regressions |
| M32-09 | LOW: and/or splitting creates meaningless components | IMPLEMENTED — only standalone whitespace-delimited and/&/slash/pipe split; and/or and attached slashes stay whole; empty/punctuation/connector components do not independently match |
| M32-10 | LOW: sentence-final punctuation prevents phrase matches | IMPLEMENTED — only terminal phrase periods/commas/semicolons trimmed for searching; original text and internal technical punctuation preserved; direct responsibility/experience and punctuation tests |
| M32-11 | LOW: unused test import and misleading artifact errors | IMPLEMENTED — unused json import removed; matching readers use artifact-specific sanitized errors; no raw candidate fields, filenames, hashes, JSON, or exception values in summaries/errors |

**Remaining matcher limits:** lexical occurrence is not semantic entailment,
skill possession, proficiency equivalence, or complete extraction. Negated,
interest-only, hypothetical, or unrelated prose can contain exact phrases;
human review is always required. Input source hashes/optional locations are
copied rather than independently verified against a DOCX. Same-record language
association is textual, not grammatical proof. No extraction expansion,
embeddings, semantic similarity, resume generation, fsync, or multi-file
transaction is implemented. See `docs/matching.md` for the full contract.

**Non-blocking independent-review observations — preserved at approval:**

- **O-1 (LOW):** Unicode full case folding maps `İ` to `i` plus U+0307;
  `İstanbul` does not match ASCII `istanbul`. This is a conservative edge of the
  documented casefold-only rule, not a semantic/normalization guarantee.
- **O-2 (LOW):** `or`-joined skills remain whole phrases; only standalone
  `and`, `&`, `/`, and `|` split. Conservative false negatives are possible under
  the documented compound rule.
- **O-3 (LOW):** Explicit matching inputs may be other local regular UTF-8
  files; output alone is confined to the private processed directory. No-follow,
  pinned, identity-checked reads remain the documented input policy.
- **O-4 (LOW):** Job analyses with empty requirement lists may produce empty
  match requirement lists; limitations and mandatory human review remain present.

The independent review required no correction for O-1–O-4 before approval.
Missions 3.2/3.2.1 are approved within that documented scope. Existing M31
non-blocking limits (textual grounding, unverified live-provider behavior, POSIX
filesystem/renaming limits, and per-file durability/atomicity) remain preserved in §H.

---

## J. Mission 2.2.1 — scope, compatibility decision, and acceptance gates

**Status: IMPLEMENTED — AWAITING REVIEW.** Assigned scope: F-01, F-02/F-07, F-06, F-10,
F-11, and M2-A01. The finalization audit independently reproduced serialization
warnings leaking synthetic values, hash-A/parse-B attribution, undiagnosed
move/directional containers, phantom/duplicated header/footer paragraphs, and
unresolved non-default references incorrectly permitting complete preservation.

**Compatibility decision — recorded before implementation (2026-11-18):**

- Keep `DocumentSnapshot` and `CandidateEvidenceCatalog` at **schema 3.0.0**.
  Add only optional, legacy-defaulted ownership/diagnostic fields: block and gap
  locations, gap reason codes, detailed completeness gaps, and snapshot
  header/footer part/reference collections.
- Existing field meanings, body evidence shape, source-provenance fields,
  body/nested identifier namespaces, and completeness formulas are retained.
  Legacy `headers`/`footers` remain default-part paragraph projections; actual
  shared parts are represented once, correcting a duplication bug. First/even
  variants are explicitly accounted for, with located unsupported diagnostics
  when not extracted. Header/footer tables remain explicitly unsupported.
- Existing schema-3.0.0 JSON remains loadable through defaults. New candidate
  catalogs keep the approved matcher's pinned version, types, and coverage
  signals. Match schema 2.0.0, job/prompt schema 2.0.0, and matcher code are
  retained. Compatibility must be verified with service and CLI tests.
- No private artifact migration or regeneration is performed. Any unexpected
  need for a breaking schema change or matcher redesign must be reported as a
  dependency rather than silently introduced.

**Acceptance gates:**

1. Capture unchanged regular-file bytes through pinned no-follow input handles;
   SHA-256 and python-docx parsing consume that same immutable byte sequence.
   Pathname replacement/changes after capture cannot change the logical input.
2. Validate mutable catalog/snapshot data before JSON serialization; fail on
   Pydantic serialization warnings without printing values to stdout/stderr.
   Both failure paths preserve existing artifacts and unrelated FutureWarnings.
3. Each newly identified unsupported inline container gets its own synthetic
   regression and located, persisted diagnostic. Unavailable inventories and
   unresolved references cannot imply complete source preservation.
4. Discover actual header/footer relationships and default/first/even references
   without creating parts; preserve linkage/ownership, deduplicate shared parts,
   and keep within-part source order independent of the body stream.
5. Run the full privacy-safe suite with exactly the seven existing private-source
   exclusions, `pip check`, and `git diff --check`; report actual totals.
   The human authorized existing synthetic loopback transport tests; all external
   network/API activity and real candidate reads remain prohibited.

Manifest-committed two-artifact publication (Mission 2.2.2) and rich-text structural fidelity (Missions 2.2.3–2.2.4) have been completed and verified; full field evaluation, live model verification, and resume generation belong to later missions. Phase 2 as an aggregate deliverable is APPROVED by explicit human decision (2026-11-20); Mission 2.1.2 and Missions 3.1, 3.1.1, 3.2, and 3.2.1 remain APPROVED.

**Implemented corrections and synthetic evidence:**

| Assigned finding | Implementation | Principal new tests |
|---|---|---|
| M2-A01 | Shared pinned regular-file capture -> frozen filename/bytes/digest -> BytesIO ZIP/DOCX/numbering inputs; captured hash checked on snapshot, catalog, records, and part provenance | `test_snapshot_catalog_and_parser_share_one_captured_byte_input`, `test_aba_path_replacement_cannot_substitute_parser_input`, `test_source_changed_after_capture_does_not_change_logical_input`, `test_numbering_reader_uses_the_same_capture_when_path_changes_after_capture`, invalid/truncated/XML/permission/FIFO/preservation cases |
| F-01 | Fresh raw model-field projection + strict recursive validation before JSON; warning-error serialization and safe handlers for both artifacts | `test_invalid_mutable_values_are_revalidated_without_serialization_warning_leaks` (catalog/snapshot), `test_both_real_json_serializers_use_warning_errors` (both), `test_hash_tampering_cannot_publish_under_a_different_input_hash` (four targets) |
| F-02 | Located unsupported inline/part inventory with persistent detailed gap objects; existing coverage formulas retained | `test_each_known_unsupported_container_is_located_and_prevents_complete_preservation`, `test_located_coverage_details_persist_in_both_real_cli_artifacts` |
| F-07 | Unresolved/conflicting parts and unavailable roots prevent reliable complete preservation, independently of section classification | `test_broken_references_are_not_confused_with_absent_parts`, `test_unavailable_owning_part_inventory_is_distinct_from_known_unsupported_content`, existing constructor/JSON invariants |
| F-06 | Actual section/package references, no convenience-part creation; one extraction per physical URI; persisted original/effective linked ownership and per-part order | `test_absent_header_footer_references_do_not_create_phantom_parts`, `test_linked_parts_across_three_sections_are_extracted_once_with_effective_references`, duplicate/empty/ownership cases |
| F-10 | Default/first/even accounting, external/wrong-kind/type/missing targets diagnosed; unloaded packages fail closed; tables explicitly unsupported and located | Non-default/linkage variants, `test_header_footer_tables_have_explicit_owning_part_table_diagnostics`, `test_unloadable_referenced_package_parts_fail_closed_with_sanitized_cli_errors` |
| F-11 | moveTo/moveFrom/dir/bdo plus seven other known content kinds diagnosed; paragraph/run/hyperlink unknown children conservatively reported; no semantic interpretation added | Per-kind body/cell/header regressions, `test_unknown_inline_extensions_with_text_are_conservatively_reported`, run/reference cases and supported-control negative control |

**Compatibility and gate outcome:** all 107 new cases and all enabled prior
regressions passed in the **812-collected / 805-passed / 7-deselected / 0-failed /
0-skipped / 11-warning** full run. `pip check` and `git diff --check` passed.
Legacy optional-field omission, new catalog loading, coverage/lexical equality,
and actual `extract-master` -> `match-job` CLI tests verify the approved consumer.
No matcher, job analyzer/client/prompt, private-output, section-detector,
dependency, or AGENTS.md source changes were made. F-15/F-20 descriptions were
restored from the human-provided historical wording; their dispositions are in §D.

**Exact full-suite invocation (repository working directory):**

```bash
env -u AGENTROUTER_API_KEY -u AGENTROUTER_BASE_URL \
  -u AGENTROUTER_MODEL -u AGENTROUTER_TIMEOUT_SECONDS \
  PYTHONDONTWRITEBYTECODE=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 PYTEST_ADDOPTS='' \
  GIT_OPTIONAL_LOCKS=0 TMPDIR=/tmp/opencode \
  PYTHONPATH=/tmp/opencode/mission221-verification/guard \
  .venv/bin/python -m pytest -p no:cacheprovider \
  --basetemp=/tmp/opencode/mission221-verification/full-suite tests \
  --deselect=tests/test_evidence.py::test_evidence_ids_are_deterministic \
  --deselect=tests/test_evidence.py::test_catalog_pydantic_round_trip \
  --deselect=tests/test_extractor.py::test_extraction_does_not_modify_source \
  --deselect=tests/test_extractor.py::test_numbering_resolution_from_numbering_xml \
  --deselect=tests/test_section_detector.py::test_master_resume_top_level_section_count \
  --deselect=tests/test_section_detector.py::test_unclassified_blocks_for_preamble \
  --deselect=tests/test_template.py::test_master_resume_untouched
```

**Exact focused invocations:** same `env` assignments as the full run, followed by:

```bash
.venv/bin/python -m pytest -p no:cacheprovider \
  --basetemp=/tmp/opencode/mission221-verification/capture-privacy-tests \
  tests/test_source_capture.py tests/test_extraction_privacy.py tests/test_cli.py \
  tests/test_matching_cli.py tests/test_nested_integrity.py

.venv/bin/python -m pytest -p no:cacheprovider \
  --basetemp=/tmp/opencode/mission221-verification/coverage-parts-tests \
  tests/test_inline_coverage.py tests/test_header_footer_integrity.py \
  tests/test_completeness.py tests/test_extractor.py tests/test_evidence.py \
  tests/test_source_capture.py tests/test_extraction_privacy.py \
  --deselect=tests/test_evidence.py::test_evidence_ids_are_deterministic \
  --deselect=tests/test_evidence.py::test_catalog_pydantic_round_trip \
  --deselect=tests/test_extractor.py::test_extraction_does_not_modify_source \
  --deselect=tests/test_extractor.py::test_numbering_resolution_from_numbering_xml

.venv/bin/python -m pytest -p no:cacheprovider \
  --basetemp=/tmp/opencode/mission221-verification/compatibility-tests \
  tests/test_extraction_matching_compatibility.py tests/test_source_capture.py \
  tests/test_extraction_privacy.py tests/test_inline_coverage.py tests/test_header_footer_integrity.py
```

These focused runs describe their historical test-file contents at execution;
the final full-suite totals include the later added cases. Dependency check:
same no-bytecode/temporary/guard environment with `.venv/bin/python -m pip check`.
Diff check: `GIT_OPTIONAL_LOCKS=0 git diff --check`.

Historical stopping point: stopped for independent read-only review; subsequently audited in the Phase 2 Comprehensive Engineering Review and subsumed under Phase 2 aggregate human approval.

---

## K. Mission 2.2.4 — Scope, implementation details, test results, and stopping point

### Scope completed
- **N-4:** `hMerge` in `TableCell`, parser default/explicit state resolution (`'continue'` vs `'restart'`), and tests.
- **N-5 regression gaps:**
  - Cyclic style inheritance safely diagnosed (`'Numbering style inheritance is cyclic'`).
  - Unavailable numbering part diagnosis (`'word/numbering.xml could not be read'`).
  - Fields inside table cell paragraphs captured with instructions.
  - Legacy paragraph properties and run formatting preserved.
- **WP-1 (F-14):**
  - Table statistics recurse through all nested depths (`table_count`, `row_count`, `cell_count`, `table_paragraph_count`, `word_count`, `empty_paragraph_count`, `hyperlink_count`).
  - Media elements (`w:drawing`, `w:txbxContent`, `w:object`, `a:blip`, `v:imagedata`) counted in body and headers/footers into `drawing_count`, `textbox_count`, `embedded_object_count`, `image_count`.
  - Empty header/footer paragraphs tracked in `empty_paragraph_count`.
- **WP-2 (F-22):**
  - `snapshot.sections` is established as the single source of truth for section assignment and unclassified detection.
  - `publication_integrity.py` validates that `catalog.section_references` are present in `snapshot.sections` and that `completeness.unclassified_block_indexes` agrees with snapshot sections.
- **WP-3 (F-23):**
  - Scope-explicit CLI summary labels: `body_paragraphs`, `table_paragraphs`, `header_paragraphs`, `footer_paragraphs`, `total_paragraphs`.
  - Regression tests verify all CLI summary labels and counts.
- **WP-4 (F-24):**
  - Contact typing for table rows in unclassified sections when any cell or line matches contact info.
  - Publication pair integrity cleanly validates ragged table rows (fewer physical cells than `column_count`).
- **WP-6 (F-18):**
  - Bounded cleanup of unused imports and dead code in `evidence.py`, `extractor.py`, and `publication_integrity.py`.
- **WP-5:**
  - Documentation reconciled across `README.md`, `docs/extraction.md`, `docs/matching.md`, and `docs/PROJECT_STATUS.md`.

### Verification outcome
- Full suite: **897 collected, 889 passed, 1 skipped, 0 failed, 7 deselected, 11 unchanged FutureWarnings** (26.53s).
- 16 new automated cases added across `test_structural_fidelity.py`, `test_evidence.py`, `test_publication.py`, and `test_cli.py`.
- Dependency check: `.venv/bin/python -m pip check` -> `No broken requirements found`.
- Diff check: `git diff --check` -> pass.
- No real candidate data read or generated; no external network requests.
- Nothing staged, committed, or pushed.

Independent reviews for Mission 2.2.4 and the Phase 2 Comprehensive Engineering Review both completed with verdicts of APPROVE WITH NON-BLOCKING OBSERVATIONS. Phase 2 as an aggregate deliverable was granted explicit human approval by the project owner (2026-11-20). Mission 2.1.2 and Missions 3.1, 3.1.1, 3.2, and 3.2.1 remain APPROVED; parent Mission 3 remains in progress. Ready for Phase 3 remaining-scope planning.

---

## L. Mission 3.3 — Stage C implementation and independent-review handoff

**Status: IMPLEMENTED — AWAITING INDEPENDENT REVIEW.** Stage B was accepted
under the assignment's explicit project-owner conditional authorization after
an **APPROVE WITH NON-BLOCKING OBSERVATIONS** verdict. O-1 (unreleased plan
schema `1.0.0`) and O-2 (heading-styled technical factual content may be
excluded) are preserved. Stage A remains approved. Stage C, Mission 3.3,
and parent Mission 3 have not been approved by this implementation.

The subsequent independent review returned **REQUEST CHANGES** for
SC-R1/SC-R2/SC-R3. The focused corrections and their latest verification are
recorded below; the initial pre-review test results are retained as history.

### Implemented behavior and metadata boundary

- **C-01:** Grouping uses recorded heading levels, or source-numbered child
  headings whose parent number and fully bold runs are available in the
  snapshot. Same-level headings delimit entries; lower-level content,
  supporting paragraphs, tables, and their nested rows stay with the entry.
  Table-only spans use actual row coordinates, parent-row ancestry, and
  snapshot vertical-merge links. Repeated source sections are distinguished
  using their enclosing boundaries and recorded heading/numbering.
  Source-section changes never merge entries. Original identity evidence IDs
  and all selected supporting record IDs are retained in `PlannedSection.entries`.
- **C-01 remaining dependency:** Arbitrary prose, bold-only catalog flags,
  partial-bold numbered headings, and missing parent rows do not establish
  reliable role/project boundaries. Inference for those inputs is stopped;
  the affected source span is retained as one indivisible
  `unresolved_source_span`, with no asserted identity and
  `entry_grouping_complete=False`. It consumes one conservative source-group
  slot, not a claimed count of underlying roles. Accurate entry counts for
  unmarked sources require an explicitly authorized upstream boundary contract.
- **C-02:** Required skill evidence precedes preferred, then other matcher
  relevance, then eligible background. Source groups are built and ranked
  before budgets are applied; surviving records are restored to source order.
  Nonempty sections are relevance-ranked
  for `max_output_sections`, with fixed output order as the tie-break and final
  presentation order. `max_education_entries` defaults to 5 independently of
  certifications. A one-page target halves positive content caps (minimum 1);
  zero budgets stay zero. Configured budgets remain in `plan.budgets`, and each
  section reports its effective cap. `item_count` counts evidence records;
  entry budgets count indivisible source groups. The planner rejects a requested
  page guarantee and always produces `page_guarantee=False`. The final skill cap
  is applied to deduplicated, relevance-ordered candidates only after evidence
  and entry/section selection, allowing surviving candidates to fill the slots.
- **C-03:** Ordering uses body/paragraph coordinates, table ancestry/row/cell/
  paragraph-in-cell coordinates, document part, and only then evidence ID.
  The approved extractor's negative nested-table IDs are ordered in their
  recorded preorder. No dates or chronology are inferred.
- **C-04:** Every record has exactly one selected/excluded outcome. Existing
  unclassified/unmapped reason codes are retained; eligibility failures use
  `content_not_eligible_for_output_section`, content-budget drops use
  `content_budget_exhausted`, and section-cap drops use
  `output_section_budget_exhausted`. Empty sections are omitted, including
  sections emptied by filtering or zero budgets. The legacy `dropped_empty`
  field remains defaulted but is always false on emitted sections. Source
  references are recomputed from surviving records. Producer accounting checks
  and the new entry membership contract enforce consistency.
- Narrow M33-09 identity binding uses selected `PROFESSIONAL_SUMMARY` records.
  New entry fields and the education budget are legacy-defaulted under plan
  schema `1.0.0`; extraction `3.0.0`, job `2.0.0`, and match `2.0.0` are preserved.

### Initial verification results (before the independent Stage C review)

All fixtures are synthetic. New cases live in
`tests/test_section_planner_stage_c.py`; the existing 243 planner/CLI cases
are retained. Two existing assertions now require omission instead of an
empty-section placeholder. No test functions or parameter cases were removed.
An initial challenge run against the unfinished implementation failed all
11 selected cases before the fixes.

| Run | Actual result |
|---|---|
| Stage C focused module | **47 passed**, 0 failed |
| Planner + CLI regression | **243 passed**, 0 failed |
| Publication + all three matcher test modules | **220 passed**, 0 failed |
| Stage B / SA-R01 / unexpected-failure / atomic-replace safeguards | **70 passed, 173 deselected**, 0 failed |
| Unmodified external Stage A/B subset | **46 passed, 1 failed, 29 deselected**; the failure is the empty-section assumption described below |
| Complete unmodified external advisory suite | **76 collected, 58 passed, 18 failed**; all failures accounted for below |
| Authorized full repository regression | **1,192 collected, 1,185 passed, 7 deselected, 0 failed, 11 existing warnings** (37.53s) |
| Dedicated hash-seed checks (Stage B, Stage C, external control) | **3 passed, 231 deselected**; seeds **1/17/103** and repeated-run equality pass |
| Dependency / whitespace checks | `pip check`: **No broken requirements found**; tracked and untracked diff whitespace checks pass |

**External suite limitations are not reported as passing:**

1. Thirteen `test_output_schema_rejects_invalid_states` cases and
   `test_selected_evidence_keeps_compact_source_provenance` remain deferred
   output-schema/provenance work under the assignment's Stage D boundary.
2. Three `test_control_simple_numeric_record_budgets` cases supply eight
   unmarked paragraphs and demand exactly two records. That legacy flat-record
   cap cannot establish actual resume-entry boundaries. These sources remain
   explicitly unresolved and indivisible; the missing metadata dependency is
   exposed rather than inventing eight entries.
3. `test_contact_is_not_selected_as_technical_content` fails at its `next()`
   lookup because it requires an empty Technical Skills section to exist.
   C-04 requires that section to be omitted. The contact record is excluded;
   the new filtered-empty tests and the existing Stage B contact cases verify
   the required behavior. The external file was not edited or patched.

**Privacy and Git verification:** The audited external guard recorded zero
real-private accesses, external-network attempts, repository writes, and
outside-temporary writes in every run. The full suite's 10 loopback connections
belong to the existing authorized synthetic transport tests. New planner paths
make no network or LLM calls. Synthetic CLI outputs retain mode 0600 and preserve
source and input artifact bytes. No `.env`, real private contents, dependencies,
or virtual-environment files were modified. Stage A implementation-file hashes
(`cli.py`, `private_input.py`, `publication_reader.py`), evidence-builder and
publication-test hashes, and `test_planner_cli.py` match the approved starting
baseline. No staging, commits, pushes, branch switches, resets, or cleaning.

**Exact full authorized command:**

```bash
env -u AGENTROUTER_API_KEY -u AGENTROUTER_BASE_URL \
  -u AGENTROUTER_MODEL -u AGENTROUTER_TIMEOUT_SECONDS \
  PYTHONDONTWRITEBYTECODE=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
  PYTEST_ADDOPTS='' GIT_OPTIONAL_LOCKS=0 TMPDIR=/tmp/opencode \
  PYTHONPATH=/tmp/opencode/stage-a-trust-boundary \
  .venv/bin/python -B -m pytest -p no:cacheprovider \
  --basetemp=/tmp/opencode/stage-c-full-authorized tests \
  --deselect=tests/test_evidence.py::test_evidence_ids_are_deterministic \
  --deselect=tests/test_evidence.py::test_catalog_pydantic_round_trip \
  --deselect=tests/test_extractor.py::test_extraction_does_not_modify_source \
  --deselect=tests/test_extractor.py::test_numbering_resolution_from_numbering_xml \
  --deselect=tests/test_section_detector.py::test_master_resume_top_level_section_count \
  --deselect=tests/test_section_detector.py::test_unclassified_blocks_for_preamble \
  --deselect=tests/test_template.py::test_master_resume_untouched
```

The same environment and guard were used for focused, regression, external,
and determinism runs, each with its own external `--basetemp`. In that initial
implementation the only new workspace file was the Stage C test module.
Awaiting independent review;
unmarked-entry inference remains an explicit metadata dependency, and broader
output validation/provenance remains unimplemented.

### Focused remediation — SC-R1, SC-R2, SC-R3 (2026-11-24)

**Status: IMPLEMENTED — AWAITING INDEPENDENT REVIEW.** Scope is limited to
`section_planner.py`, `test_section_planner_stage_c.py`, and this Stage C handoff.
No Stage C or Mission 3.3 approval is recorded.

| Finding | Root cause and correction |
|---|---|
| SC-R1 HIGH | Row-only grouping ignored `TableCell.v_merge` dependencies. Snapshot `restart`/`continue` cells now connect rows through recorded logical cell ranges (`grid_span`), retaining the origin row and all supporting/nested rows as one budget unit. Ordinary adjacent rows remain separate. Orphan continuations, ambiguous/ragged alignments, multiple origins, and unavailable/ineligible origin evidence produce an intact unresolved span with no asserted identity and incomplete grouping. |
| SC-R2 MEDIUM | Numbering was collected from all sections with the same identifier. Runs now resolve their enclosing, bounded source-section instance and split at instance changes even when a legacy catalog omits structural heading records. Only that instance's numbering and its recorded heading contribute the parent number. Existing heading and child-boundary detectors are unchanged. |
| SC-R3 MEDIUM | Skills were capped before evidence survival, leaving vacancies after dropped roles. Relevance-ordered, deduplicated skill candidates now survive until evidence/entry/section selection is complete; candidates without surviving support are filtered first, then the final skill cap is applied. The demonstrated cross-budget case yields Python and SQL, excluding Go. Requirement associations, aggregated evidence, primary parent IDs, and matcher coverage remain intact. |

**New regressions:** 18 synthetic DOCX-backed cases were added without changing
existing cases. They cover the required merged role identity/Python support
scenario across all four entry categories; nested support; ordinary rows;
grid-spanned merges; orphan, ragged, multi-origin, and filtered-identity cases;
independently numbered sections 1 and 4; legacy structural-heading omission;
Python/Go/SQL skill refill; aggregated evidence/associations; and combined
repeated-run/hash-seed stability. All ordinary extraction inputs pass the
existing snapshot/catalog/pair validators. The two deliberately partial legacy
catalog cases explicitly verify managed-pair rejection while preserving the
approved sufficient-provenance service path. Seven selected critical cases
failed before implementation, confirming all three original defects.

| Verification | Actual result |
|---|---|
| New SC-R remediation cases | **18 passed, 47 deselected** |
| All Stage C tests | **65 passed** |
| Existing planner/CLI tests | **243 passed** |
| Stage A/B + SA-R01/exception/atomic-replace safeguards | **70 passed, 173 deselected** |
| Publication + matcher regressions | **220 passed** |
| Complete unmodified external adversarial suite | **76 collected, 58 passed, 18 failed** |
| External Stage A/B subset | **46 passed, 1 failed, 29 deselected** |
| Applicable named positive controls | **13 passed, 63 deselected**; legacy paragraph-count controls are accounted for in the full external result |
| Full authorized regression | **1,210 collected, 1,203 passed, 7 documented exclusions, 0 failed, 11 unchanged warnings** (62.86s) |
| Dedicated repeated/hash-seed checks | **4 passed, 248 deselected**; seeds **1/17/103** and repeated byte equality pass, including merged/repeated-section/refill paths |
| Dependency/whitespace checks | `pip check`: **No broken requirements found**; tracked/untracked whitespace checks pass |

**External failure investigation:** All 18 failures were reproduced and inspected;
no new failure class appeared. Thirteen invalid-output-state probes and the one
embedded per-evidence-location probe remain explicitly deferred Stage D work.
Three legacy controls demand a two-paragraph cap for records lacking any entry
boundaries; those eight records remain one explicitly unresolved source span,
not eight invented entries. The contact-only Technical Skills test still fails
its empty-section `next()` lookup because C-04 requires omission; contact
exclusion/accounting remains correct. That is also the single external Stage
A/B subset failure. No external test was edited, weakened, or patched.

**Preservation/security:** Protected extraction/model/evidence/matching,
Stage A CLI/input/publication-reader, `test_section_planner.py`,
`test_planner_cli.py`, `test_publication.py`, AGENTS.md, pyproject.toml, and
Git-index hashes match the remediation baseline.
All audit runs report zero private access, external network, repository write,
and outside-temporary write attempts; the full suite includes the same 10
authorized synthetic loopback connections. Test data and generated DOCX/JSON
fixtures remain under `/tmp/opencode`; no real private resume contents were
read. Source/input bytes, JSON determinism, and private output modes are
preserved. No dependencies, `.env`, or `.venv` modifications; no branch switch,
reset, clean, staging, commit, push, Stage D, or Phase 4 implementation.

The exact full authorized command above was repeated with the same environment,
guard, flags, and seven exclusions, using
`--basetemp=/tmp/opencode/sc-remediation-full-authorized`. Focused runs used
separate external temporary directories.

**Remaining limitations:** Merge recovery depends on available snapshot cell
metadata; unmarked entries and ambiguous/missing merge origins remain explicit
unresolved spans. Record-only legacy catalogs do not expose vertical-merge
states. Entry budgets therefore cannot promise a count of underlying roles in
unresolved spans. Broader output-schema/per-evidence-provenance work remains
deferred. Stopped for independent review; Stage A/B approvals remain in force.

### Stage C owner approval — recorded at Stage D authorization

The final independent Stage C review returned **APPROVE WITH NON-BLOCKING
OBSERVATIONS** and verified SC-R1/SC-R2/SC-R3 fixed. The project owner explicitly
approved Stages A, B, and C and authorized Stage D in the Stage D assignment.
Stage C is now **APPROVED** on that explicit owner authority. The historical
implementation/review stopping states and verification above are preserved.
Accepted unmarked-span and snapshot-merge metadata limits, unreleased schema-1
history, and heading-styled technical-content observations remain recorded.
Mission 3.3 is not approved by this child-stage approval.

## M. Mission 3.3 — Stage D output contract

**Status: IMPLEMENTED — AWAITING INDEPENDENT REVIEW.** Authorized scope:
D-01 strict structural invariants,
D-02 source-aware completed-result validation before publication, D-03 compact
per-evidence provenance, and D-04 evidence-bound summary instructions.

**Schema decision:** The current trusted output contract will be schema
**2.0.0**. Complete provenance and explicit factual bindings are mandatory;
schema-1 artifacts lack the information needed to validate them as trusted
generation instructions. An explicit historical schema-1 reader will preserve
legacy field/default inspection; producing current output from legacy data
requires re-planning against validated catalog, analysis, and match inputs.
This makes the breaking contract intentional despite its unreleased status,
preserves the Stage B association history, and avoids silently promoting old
unbound artifacts to the stricter contract. Input schemas and approved selection
policies remain unchanged. No Stage D or Mission 3.3 approval is recorded.

### Completed D-01 through D-04 behavior

- **D-01:** `section_plan.py` owns strict, extra-forbidden, revalidated output
  models. The artifact reader rejects duplicate JSON fields and nonstandard
  constants. Exact integer/boolean checks prevent numeric false/review/page
  coercions. Evidence and requirement IDs are unique where required; every
  catalog record has one selected/excluded outcome. Ordered flat/section/entry
  selections, source references, provenance associations, headings, item counts,
  record/entry/section caps, and grouping completeness are checked. Prioritized
  labels retain conservative deduplication, strongest-parent order, supported
  component references, and at least one surviving eligible source witness.
  `page_guarantee=False` and `human_review_required=True` are enforced.
- **D-02:** `SectionPlanner.validate_result` validates fresh source-model
  projections through the approved `CandidateJobMatcher.validate_result` boundary,
  revalidates completed output, and explicitly compares every provenance record,
  original content digest/location/cell reference, requirement component, source
  context, and identity text with its source inputs. It also compares the output
  against the shared private approved selection implementation, detecting
  source-consistent but wrongly selected entries/budgets. It does not call a
  second matcher or reinterpret lexical support. The CLI invokes this boundary
  on the JSON-restored plan before `PrivateOutput.write_json`, retaining all
  no-follow/alias checks and sanitized exit-code behavior. Well-typed forged
  candidate identities, claims, witnesses, entry groupings, and generation
  references fail before publication with content-free errors.
- **D-03:** Every original evidence record has a compact provenance row:
  evidence ID/type, source filename/SHA-256, original-content SHA-256, exact
  source location, body/paragraph/table/row/cell/nested/paragraph-in-cell
  coordinates, contributing cell references where available, and actual output
  section/entry associations. Optional coordinates remain `None`; unavailable
  cell provenance is `None`, distinguishable from an available empty collection.
  Invalid coordinates/ancestry are rejected, not filled in. Managed plans retain
  validated publication bundle/generation identity and snapshot/catalog hashes.
  No large resume body is copied: original entry/support text remains traceable
  in the original catalog, with content digests and managed-generation identity
  allowing exact retrieval verification.
- **D-04:** Professional identity instructions are verbatim selected
  `PROFESSIONAL_SUMMARY` paragraphs with explicit evidence IDs and digest checks.
  Technical capability instructions contain the supported, evidence-bound
  prioritized components, including parent requirement associations and tiers;
  atomic documented background labels retain explicit source bindings.
  Experience categories bind selected entry IDs and evidence, and project
  highlights bind actual selected source entries. Existing display projections
  must exactly equal their structured bindings. Free-form additions, unsupported
  years/seniority/achievements/qualification claims, empty bindings, and
  contradictory capability/identity/entry references are rejected. The blueprint
  remains an instruction contract; no LLM or resume prose generation is added.

### Compatibility and earlier-stage preservation

Current `ResumeSectionPlan` accepts schema 2 only. Mandatory provenance and
structured bindings cannot be omitted to imitate a historical artifact.
`LegacyResumeSectionPlan` in `legacy_section_plan.py` preserves schema-1 defaults
and data-only inspection, including the Stage B association extension and
Stage C optional entry/education fields. It is never accepted as a trusted
schema-2 result. `SectionPlanner.replan_legacy` requires validated matching
source identity, target context and requirement coverage, then builds current
instructions solely from those sources and the current planner's configured
budgets; historical factual strings are not silently copied or guessed.

Version assertions, legacy-reader tests, and managed generation-context
expectations were intentionally updated for this breaking contract. No existing
test cases were removed, and functional A/B/C checks remain enabled. Input
schemas stay extraction **3.0.0**, job **2.0.0**, and match **2.0.0**. The
approved heading/contact/deduplication/component selection, merged-row and
repeated-section grouping, skill refill, ordering and accounting algorithms
are preserved. Source-aware CLI integration is the necessary earlier-stage
dependency; extraction/matching/input-reader/publication-reader algorithms and
contracts are unchanged.

### Actual verification

All new fixtures are synthetic DOCX-backed inputs under `/tmp/opencode`.
Tests cover unknown fields at 17 contract layers, strict coercion/JSON handling,
selection/entry/requirement/provenance/budget contradictions, unsupported and
self-consistent forged claims, filename/hash/coordinate/generation manipulation,
valid complete blueprints, schema-1 inspection and source-backed re-planning,
managed extraction/matching/planning, rejection before private writes, and
determinism. Two additional failing-first contract challenges exposed numeric
`0` empty flags and reordered weaker primary parents; both are now rejected.

| Run | Actual result |
|---|---|
| New Stage D module | **110 passed**, 0 failed |
| Existing planner/CLI/Stage C regression | **308 passed**, 0 failed (111 planner + 132 CLI + 65 Stage C) |
| Earlier Stage B / SA-R01 / failure / atomic-replace safeguards | **70 passed, 173 deselected**, 0 failed |
| Publication + matcher regressions | **220 passed**, 0 failed |
| Assigned external schema/provenance cases | **14 passed, 62 deselected**, 0 failed |
| Complete unmodified external advisory suite | **76 collected, 72 passed, 4 failed** |
| Full authorized repository regression | **1,320 collected, 1,313 passed, 7 documented private-source exclusions, 0 failed, 11 unchanged warnings** (70.34s) |
| Dedicated repeated/hash-seed checks | **5 passed, 357 deselected**; seeds **1/17/103** pass |
| Dependency / diff checks | `pip check`: **No broken requirements found**; tracked/untracked whitespace checks pass |

The remaining four external failures are the accepted Stage C assertions:
three paragraph-count controls for records with no entry boundaries, and one
contact-only Technical Skills `next()` lookup requiring an empty section.
The current unresolved-span and empty-section omission contracts are preserved;
no external test was edited or patched. No new failure class appeared, and
all previously deferred 14 schema/provenance challenges now pass.

**Privacy/security:** All guards report zero real-private access, external
network, repository-write, and outside-temporary write attempts. The full run's
10 loopback connections are the existing authorized synthetic transport tests.
Managed source/input bytes and mode-0600 private publication are preserved;
forged valid-typed output is rejected before writes without exposing candidate
markers. No real private contents, `.env`, or credentials were inspected;
`.venv` and dependencies were not modified. Protected extraction/model/evidence/matcher,
input-reader/publication-reader, publication-test, AGENTS.md, pyproject.toml,
and Git-index hashes match the Stage D baseline. No staging, commits, pushes,
branch changes, resets, cleaning, Stage E implementation, or Phase 4 generation.

**Exact full-suite invocation:** Same guarded environment and seven exclusions
as the earlier exact authorized command in §L, using
`--basetemp=/tmp/opencode/stage-d-full-authorized`. Focused runs used independent
external temporary directories. Tests ran through the project `.venv`.

### Remaining limitations and stopping point

Bindings prove exact source trace and matcher-declared lexical support, not
semantic entailment or independent verification of candidate qualifications.
Human review remains mandatory. Accepted unmarked/ambiguous entry and missing
snapshot merge-metadata limits remain explicit; missing coordinates and
unavailable generation identity are not fabricated. A future renderer must use
the supported artifact reader and source-aware boundary and retrieve original
catalog text through the retained evidence/generation/content bindings. Legacy
schema-1 data requires original validated inputs for trusted re-planning.
Stopped at **IMPLEMENTED — AWAITING INDEPENDENT REVIEW**; Stage D and Mission
3.3 are not owner-approved by this implementation.

### Stage D independent review and conditional owner approval

The independent Stage D review returned **APPROVE WITH NON-BLOCKING
OBSERVATIONS**. It inspected the strict contract, source-aware boundary,
provenance, blueprint and schema-1 compatibility, and ran **418 passing focused
cases**. Six additional independent synthetic checks rejected self-consistent
evidence/requirement/identity forgeries, rejected boolean count coercion,
verified exact available/unavailable coordinates, and confirmed unsupported
historical factual strings are not promoted by re-planning. The external suite
remained **72 passed / 4 accepted assertion failures**, with no new class.
Source/test/status/index hashes and Git state were unchanged during the review.

The assignment explicitly authorized **Stage D approval and Stage E
implementation conditional on a satisfactory independent review**. Stage D is
now recorded **APPROVED** on that owner authority. Historical A/B/C approvals,
pre-approval Stage D reports, lexical-support and incomplete-source metadata
limits, mandatory human review, and explicit legacy inspection are preserved.
Stage E is in progress; neither Stage E nor Mission 3.3 is approved here.

## N. Mission 3.3 — Stage E final integration and documentation

**Status: IMPLEMENTED — AWAITING INDEPENDENT REVIEW.** Stage D was independently
reviewed and approved under the owner's explicit conditional authorization before
Stage E implementation began. Stages A–D remain APPROVED. Stage E and Mission
3.3 are not approved by this implementation; Phase 4 has not begun.

### E-01 through E-05 delivery

- **E-01 — Complete integration:** The four existing CLI commands implement
  pinned Master Resume extraction/publication, JD-only Responses analysis and
  quotation checks, local matching, approved source selection/grouping/budgets,
  strict schema-2 restoration, source-aware validation, and private atomic
  publication. A managed-input alias challenge demonstrated that `match-job`
  protected direct inputs but could target the generation catalog/snapshot it
  had consumed. The driver now protects resolved generation paths and repeats
  alias checks after matching/serialization and before publication. The existing
  error text and 0/2/3 conventions are preserved; matching/extraction algorithms
  and data contracts were not changed.
- **E-02 — Managed handoff:** `tests/test_planning_pipeline.py` adds **23** full
  synthetic DOCX/JD cases using the real client, completed Responses-envelope
  parser, analyzer, matcher, planner, reader and private writer. An injectable
  offline transport produces grounded JD output without network calls. Tests
  verify all four artifact versions, immutable pair/checksum integrity,
  candidate-data isolation from the provider request, supported compound-only
  labels, merged identities/entries, complete accounting, generation provenance,
  JSON-restored validation before writes, mode 0600 and source/input preservation.
  Negative cases cover analysis grounding/provider failures, forged references,
  incompatible versions, tampered/missing generations, atomic publication
  failures and direct/late generation aliases. Matching/planning repeat byte-for-
  byte after API settings are removed, without another provider request.
- **E-03 — Compatibility:** Extraction payload 3.0.0, publication 1.0.0,
  analysis/prompt 2.0.0, match 2.0.0 and plan 2.0.0 are verified end-to-end.
  The complete managed pipeline exercises explicit schema-1 inspection and
  validated-source re-planning; unsupported historical factual strings do not
  enter current instructions. No legacy plan bypasses the trusted validator.
- **E-04 — Operations/docs:** README was reconciled with the implemented
  Master Resume-driven workflow. `docs/planning.md` documents exact commands,
  private artifact purposes/locations, manifest-aware inspection, source-aware
  verification, schema/migration policies, privacy/exit-code boundaries and
  limitations. Extraction/analysis/matching documents now reflect approved
  status, atomic manifest publication and current integration. They explicitly
  state that job-tailored AI DOCX/PDF generation remains Phase 4 work.
- **E-05 — Acceptance:** `tests/test_planning_acceptance.py` adopts the portable
  **76-case independent review matrix** without the original absolute import
  paths. The external original is untouched. Its contact control additionally
  enforces empty-section omission/accounting; its three numeric entry controls
  use actual DOCX source rows instead of inventing entries from arbitrary
  paragraphs. All other adversarial assertions retain independent source,
  schema, path, accounting, blueprint, publication and determinism checks.
  No xfail/skip hides these current-contract controls. The original external
  assertion mismatches remain separately reported.

### Exact Stage E files changed

- `src/resume_assistant/cli.py` — consumed-generation match alias protection.
- `tests/test_planning_pipeline.py` — new full managed workflow tests.
- `tests/test_planning_acceptance.py` — new portable independent acceptance.
- `README.md` — current workflow/commands/artifact roles and limitations.
- `docs/planning.md` — new detailed planning/inspection/compatibility guide.
- `docs/extraction.md`, `docs/job_analysis.md`, `docs/matching.md` — current
  publication/status/integration descriptions.
- `docs/PROJECT_STATUS.md` — conditional D approval, count reconciliation and
  final Stage E handoff. Historical approval/rejection/verification entries
  remain preserved; the original M33-13 counting claim is explicitly annotated.

No prior test files or cases were modified or weakened during Stage E. Protected
A–D planner, strict/legacy models, extraction, analysis/client, matching, input/
output/publication components and existing test hashes remain unchanged; the
only source modification is the necessary CLI integration safeguard.

### Actual final verification

| Run | Actual result |
|---|---|
| New Stage E integration + acceptance | **99 passed**, 0 failed (23 managed + 76 independent acceptance) |
| Existing full planner/CLI/A–D tests | **418 passed**, 0 failed |
| Publication + matcher regression | **220 passed**, 0 failed |
| Earlier Stage B / SA-R01 / failure / atomic-replace safeguards | **70 passed, 173 deselected**, 0 failed |
| Original unmodified external adversarial suite | **76 collected, 72 passed, 4 accepted assertion failures** |
| Full authorized repository regression | **1,419 collected, 1,412 passed, 7 documented private-source exclusions, 0 failed, 11 existing warnings** (62.37s) |
| Repeated/hash-seed checks | **6 passed, 432 deselected**; seeds **1/17/103** and same-artifact repeatability pass |
| Dependency / whitespace checks | `pip check`: **No broken requirements found**; tracked/untracked whitespace checks pass |

Every failure was investigated. The two source-generation alias challenges
failed first and exposed the driver gap; after correction all four direct/managed
alias cases and both late-alias cases pass. Three pre-existing matcher tests
detected an overly broad exception conversion during development; the original
alias error text was restored, and the untouched 220-case regression passes.
The first workflow fixture assumed a standalone snapshot selector file; it was
corrected to the actual manifest contract and now explicitly verifies no such
file is written. None of these development failures remains unresolved.

The four original external failures are unchanged: three obsolete record-count
assertions for unmarked spans and one contact-only empty-section lookup. Current
stronger repository controls pass, the source remains explicitly unresolved
where boundaries are unavailable, and contact content is excluded/accounted for.
There is no new external failure class, no original external modification, and
all 14 former Stage D schema/provenance failures remain resolved.

### Privacy, determinism and Git integrity

All instrumented runs report zero actual-private access, external networking,
repository writes and outside-temporary writes. Candidate-only markers never
enter the synthetic Responses request; only the supplied JD does. Full-suite
loopback connections are the same **10** existing authorized transport cases.
Synthetic artifacts are confined to `/tmp/opencode`; source/input bytes and
private output permissions remain intact. No live provider request, real Master
Resume/private artifact inspection, `.env`, `.venv` or dependency change occurred.

The existing branch and HEAD `e029c8f4b668f23d84feabce7494358a4cde2615` are
preserved. Git index and protected source/test hashes match the mission baseline.
All existing uncommitted work remains intact. Nothing staged, committed, pushed,
reset, cleaned, or switched. Stages A–D approvals and review observations remain
in force; Stage E and Mission 3.3 await owner review.

**Exact final full-suite command:** Same guarded environment and seven explicit
exclusions as the exact command in §L, with
`--basetemp=/tmp/opencode/stage-e-full-authorized`. Independent focused temporary
directories were used. Tests ran through the existing project `.venv` with
bytecode/cache writing disabled and existing warnings visible.

**Remaining limitations:** Source grounding and lexical support are not semantic
entailment or independent qualification verification; human review is mandatory.
Unmarked/ambiguous entries, missing merge origins and unavailable coordinates/
generation identity remain explicit and are not guessed. Live provider
compatibility, rendering fidelity and power-loss durability are not established
by this mission. The final plan is an evidence-bound input contract for Phase 4,
not a generated resume. Stopped at **IMPLEMENTED — AWAITING INDEPENDENT REVIEW**.
