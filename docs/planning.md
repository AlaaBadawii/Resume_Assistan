# Deterministic resume planning — Mission 3.3

The planning pipeline consumes the user's existing Master Resume evidence and a
supplied job description. It publishes a validated **instruction contract** for
Phase 4. The Phase 4 core generator now consumes that contract plus the committed
candidate evidence to produce a validated tailored-resume JSON and ATS-safe DOCX;
it is **IMPLEMENTED — AWAITING REVIEW**. Stages A–E are owner-approved and
Mission 3.3 is APPROVED (owner closeout 2026-11-25). Approval provenance and
exact verification results live in [PROJECT_STATUS.md](PROJECT_STATUS.md).

## Artifact relationships and commands

| Producer | Input | Output |
|---|---|---|
| `extract-master` | Regular local DOCX; same immutable bytes feed hashing/parsing | Atomic publication manifest 1.0.0 selecting snapshot/catalog payloads 3.0.0 |
| `analyze-job` | Regular UTF-8 job description; provider configuration | Quotation-checked analysis 2.0.0 / prompt 2.0.0 |
| `match-job` | Validated analysis plus manifest-aware catalog input | Candidate-job match 2.0.0, checked against both inputs |
| `plan-sections` | Analysis, match, and manifest-aware catalog/snapshot | Plan 2.0.0, JSON-restored and source-aware validated before private publication |
| `generate-resume` | Plan 2.0.0 plus manifest-aware candidate evidence | Generation 1.0.0 JSON and ATS-safe DOCX, evidence-scope validated before private publication |

```bash
.venv/bin/python -m resume_assistant extract-master \
  --master data/private/Master_Resume.docx \
  --output data/private/processed/candidate_evidence.json \
  --snapshot-output data/private/processed/document_snapshot.json

# Load your own provider settings only for JD analysis:
set -a; source .env; set +a
.venv/bin/python -m resume_assistant analyze-job \
  --input data/private/job_description.txt \
  --output data/private/processed/job_description_analysis.json

.venv/bin/python -m resume_assistant match-job \
  --analysis data/private/processed/job_description_analysis.json \
  --candidate-evidence data/private/processed/candidate_evidence.json \
  --output data/private/processed/job_candidate_match.json

.venv/bin/python -m resume_assistant plan-sections \
  --analysis data/private/processed/job_description_analysis.json \
  --match data/private/processed/job_candidate_match.json \
  --candidate-evidence data/private/processed/candidate_evidence.json \
  --output data/private/processed/resume_section_plan.json

# Provider settings are also required for generation:
.venv/bin/python -m resume_assistant generate-resume \
  --plan data/private/processed/resume_section_plan.json \
  --candidate-evidence data/private/processed/candidate_evidence.json \
  --output-json data/private/processed/tailored_resume.json \
  --output-docx data/private/processed/tailored_resume.docx
```

The candidate default path is now the **manifest address**, not a catalog payload.
Its current generation contains `catalog.json` and `snapshot.json` envelopes
under `.extractions/<bundle_key>/generations/<generation_id>/`. The reader loads
the generation specified by the manifest, verifies envelope hashes and pair
integrity, and never guesses the newest directory or falls back after corruption.
`--snapshot-output` records a compatibility selector; no standalone snapshot file
is written. Old selector files are not current source inputs.

JD analysis sees only JD text. Candidate evidence is never injected into the
Responses request. Matching and planning reuse the validated analysis and run
offline, even after API settings are removed from the process environment.

## Approved selection policy

- Emit only matcher-supported components for required/preferred skill labels.
  A partial `Python and SQL` requirement cannot imply unsupported SQL.
- Deduplicate labels by case/whitespace only, preserving aggregated source IDs,
  primary parent requirements and the complete association list.
- Exclude contact metadata and structural headings; preserve eligible factual
  role/project titles in their classified source destination.
- Build entries from reliable recorded heading spans and table relationships.
  Vertically merged identity/support rows and nested-table evidence stay atomic.
  Repeated source sections bind to their enclosing numbered instance.
- Rank required, preferred, other matcher relevance, then eligible background
  before content/entry/section caps. Restore source order after choosing units.
  Apply the final skill cap after evidence survival so eligible candidates refill
  slots vacated by excluded roles.
- Use distinct skill, experience, project, education and certification caps.
  One-page targets tighten positive content caps; zero caps remain zero.
  No page-count guarantee is claimed.
- Give every catalog record exactly one selected/excluded outcome. Omit empty
  sections and recalculate source references from surviving records.

Unmarked/ambiguous spans remain indivisible and explicitly unresolved, with no
asserted entry identity. Their cost is one conservative source unit; the number
of underlying roles cannot be guaranteed. Heading-styled technical factual
content may be excluded under the documented conservative filter.

## Schema-2 trust boundary

`ResumeSectionPlan` enforces strict types, known section IDs, noncoercive flags,
unique evidence/requirements, exact ordered selection unions, consistent entry
membership/counts/caps, and complete selected/excluded accounting. The artifact
reader rejects duplicate JSON fields and nonstandard constants.

Every original record has compact provenance: source filename/document SHA-256,
evidence ID/type/content digest, exact available source coordinates, contributing
cell sources and actual section/entry associations. Null means unavailable;
coordinates are never filled in. Managed identity includes the bundle/generation
and snapshot/catalog hashes. Large original resume text remains in the catalog.

Summary instructions bind verbatim `PROFESSIONAL_SUMMARY` identity statements,
supported capabilities and parent requirements, experience category entry IDs,
and selected project entries to genuine source evidence. Display projections
must equal the structured bindings. No free-form years, achievements, seniority,
metrics or qualifications are added by the planner.

`SectionPlanner.validate_result` revalidates source models through the existing
matcher boundary, checks exact original provenance and component decisions, and
verifies approved selection/grouping policy. Structural validity alone cannot
establish authenticity: self-consistent fabricated IDs, hashes, coordinates or
claims are rejected against the actual inputs. The CLI runs this check on the
JSON-restored plan immediately before its final alias check and atomic write.

## Local inspection and source-aware verification

For local owner inspection, read the private artifact only on your trusted
machine. This example verifies source relationships and prints aggregate counts:

```python
from pathlib import Path
from resume_assistant.job_models import JobDescriptionAnalysis
from resume_assistant.matching_models import CandidateJobMatch
from resume_assistant.publication_reader import load_committed_pair
from resume_assistant.section_planner import (
    ExtractionGenerationIdentity, ResumeSectionPlan, SectionPlanner,
)

root = Path.cwd()
processed = root / "data/private/processed"
snapshot, catalog, manifest, _ = load_committed_pair(
    root, processed / "candidate_evidence.json",
)
analysis = JobDescriptionAnalysis.model_validate_json(
    (processed / "job_description_analysis.json").read_bytes(),
)
match = CandidateJobMatch.model_validate_json(
    (processed / "job_candidate_match.json").read_bytes(),
)
plan = ResumeSectionPlan.model_validate_json(
    (processed / "resume_section_plan.json").read_bytes(),
)
generation = ExtractionGenerationIdentity.from_manifest(manifest) if manifest else None
verified = SectionPlanner(plan.budgets).validate_result(
    plan, analysis, match, catalog, snapshot,
    manifest_available=manifest is not None, extraction_generation=generation,
)
print(verified.schema_version, len(verified.ordered_sections),
      len(verified.selected_evidence_ids), len(verified.excluded_evidence))
```

This is local owner code, not a provider request. The application CLI uses the
documented no-follow readers/private writers; an editor or caller's explicit
`Path.read_bytes` is outside that CLI I/O boundary.

## Compatibility and migration

Input versions remain extraction 3.0.0, analysis/prompt 2.0.0, and match 2.0.0.
Plan schema 2.0.0 is an intentional breaking trust contract: complete provenance
and explicit factual bindings are mandatory even though schema 1 was unreleased.

`LegacyResumeSectionPlan.model_validate_json` supports historical schema-1
inspection/defaults only. It cannot be passed to the current trusted result
validator. `SectionPlanner.replan_legacy(legacy, analysis, match, catalog,
snapshot, ...)` requires matching validated original sources and creates current
instructions from them and the caller's configured budgets. Historical factual
strings are not silently promoted. There is no automatic private-file migration,
no source recovery from a legacy plan alone, and no acceptance of match schema 1.

Legacy standalone catalogs may still be used if their per-record filename,
document hash and body location satisfy planning provenance checks. Their
unavailable snapshot, generation, merge metadata and coordinates stay unavailable.

## Privacy, failures and operational limits

Keep the Master Resume, JD, all processed artifacts, `.env`, environments and
generated output out of Git and external services. CLI summaries expose counts
and fixed indicators, not candidate text or full provenance. Outputs are confined
to private processed storage and mode 0600, using pinned no-follow directories,
exclusive temporaries and descriptor-relative atomic replacement.

Provider data flows are explicit: `analyze-job` sends the supplied JD only;
`generate-resume` sends only selected non-contact evidence from the approved
plan. Identity/contact records and excluded evidence never enter the generation
request. The deterministic header is assembled locally.

Match, plan and generation outputs cannot alias their direct inputs or consumed
managed generation catalog/snapshot, including aliases introduced while
processing. Validation/version/reference/checksum failures preserve previous
output and return 2. Unexpected implementation or private publication failures
return 3, including SA-R01's unexpected `FileNotFoundError` cases. Success
returns 0. The extraction manifest is the pair's commit point; orphan
uncommitted generations never become the visible current pair after a failed
publication.

Linux behavior is verified; unsupported platforms fail closed. Privileged direct
file modification, every directory-renaming window, Word rendering fidelity and
power-loss durability are not guaranteed. Lexical support/quotation/numeric
checks are not semantic entailment or independent qualification verification.
Human review is mandatory; AI JD classifications remain unverified. Phase 4 core
JSON/DOCX generation is implemented and awaiting review; template filling and
PDF generation are not implemented.

## Phase 4 generation contract

`generate-resume` re-verifies plan source identity, every provenance hash, and
the committed publication generation before any provider request. It then builds
the deterministic local header and a compact provider input containing the target
job context, hydrated ordered sections, selected prioritized skills, and a
sanitized summary blueprint. Excluded evidence and contact/identity records are
never included.

The returned payload must be one unambiguous JSON object (an optional Markdown
fence is stripped). Validation is deterministic:

- every cited evidence ID must be selected, present, integrity-matching, and
  within the permitted summary/entry/skill scope;
- skills, sections, entries, headings, and section/entry order must match the
  plan;
- numeric claims absent from the cited text are `ungrounded_metric` warnings for
  human review; `--strict-metrics` makes them hard failures.

The published artifact records the generation schema/prompt/plan versions, model,
local header, grounded payload, and validation report. `traceability_verified`
covers deterministic evidence checks only; `semantic_entailment_verified` is
always false and `human_review_required` is always true. The DOCX renderer emits
a single-column document with named styles matching the approved template, no
tables/images/Word header/footer content, and A4/Letter page sizes.

`tests/test_resume_generation.py` runs the real five-command path with synthetic
DOCX/JD data and a deterministic offline transport. It covers provider-input
privacy, artifact modes, JSON round-trip, DOCX structure/order, provenance
tampering, fabricated/out-of-scope evidence, ungrounded metrics, strict mode,
alias protection, and 0/2/3 exit codes.

## Maintained integration and independent acceptance

`tests/test_planning_pipeline.py` runs all four real commands using synthetic
DOCX/JD inputs and an injectable, completed Responses transport. It verifies
analysis data-flow isolation, managed pair/version consistency, coherent entries,
source-aware restored output, private atomic publication, repeatability and
negative handoff/publication cases.

`tests/test_planning_acceptance.py` adopts the 76-case independent review matrix
portably. The external original remains untouched. Its contact control now also
requires empty-section omission; its three numeric entry controls use actual
DOCX row relationships rather than pretending eight arbitrary paragraphs prove
eight entries. These current-contract controls are stronger, not skipped or
xfail-marked. Original external assertion mismatches remain explicitly recorded
alongside complete results in PROJECT_STATUS.md. Preserve the seven documented
private-source exclusions when running the full synthetic regression.
