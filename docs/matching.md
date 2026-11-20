# Local Candidate Evidence Matching — Missions 3.2 / 3.2.1

The matcher consumes validated job analysis and candidate evidence locally. It
reports traceable **textual evidence**, with explicit extraction-coverage limits
and human-review indicators. Mission 3.2 was rejected by independent review;
Mission 3.2.1 addresses findings M32-01–M32-11. Review/approval status is recorded
in `docs/PROJECT_STATUS.md`.

## Architecture and input compatibility

```text
match-job
  -> pinned no-follow regular-file reads (private_input.py)
  -> unambiguous JSON + explicit version checks + input Pydantic validation
  -> CandidateJobMatcher.assess(validated_analysis)
  -> CandidateJobMatch schema 2.0.0
  -> JSON reconstruction + validate_result(restored, validated_analysis)
  -> mode-0600, descriptor-relative atomic private output
```

| Input | Actual contract |
|---|---|
| `JobDescriptionAnalysis` | Schema `2.0.0`, prompt `2.0.0`, `grounding_method=exact_substrings_only`, `interpretation_status=ai_generated_unverified`; copied source phrases with their own quotations and skill classification contexts |
| `CandidateEvidenceCatalog` | Extraction schema `3.0.0`: `source_document`, `evidence_records`, `section_references`, and validated `extraction_completeness` |

`matching.py` receives in-memory models, snapshots/revalidates them, and imports
only standard-library code and the data models. It does not import extraction
runtime, HTTP clients, providers, embeddings, or filesystem helpers. The CLI
owns artifact I/O. No candidate data enters the job analyzer or a provider.

Input artifacts keep their original schemas and are not regenerated or migrated.
Both JSON inputs must explicitly declare the supported schema version. Match
output changes to **schema `2.0.0`**, because per-component assessments, unique
source references, and compact provenance are a new contract. Old match schema
`1.0.0` is not accepted by the corrected output model; existing files are not
automatically rewritten.

## Exact matching rule: `exact_normalized_phrase_v2`

1. Normalize case using Unicode `casefold()`; collapse each whitespace run to one
   ASCII space and trim the ends. No synonyms, stemming, fuzzy matching, keyword
   expansion, confidence percentages, or semantic similarity are used.
2. For the **search phrase only**, remove a terminal run of `.`, `,`, and `;` and
   any resulting trailing whitespace. These sentence-ending marks do not make a
   qualification more specific. Original requirement text and quotations stay
   unchanged in the artifact.
3. Require the entire normalized phrase to occur literally, with a technical-token
   boundary on both ends. Unicode alphanumeric characters, `_`, `+`, `#`, `/`,
   `-`, and `&` continue a token. A dot continues a token when followed by an
   alphanumeric character, `_`, `+`, or `#`; a sentence-final dot is a delimiter.
   Whitespace and ordinary wrapping/list punctuation are delimiters.
4. A phrase/component must contain an alphanumeric character. Standalone
   connector words `and`, `or`, and `and/or` cannot supply positive evidence.

This is a boundary check, not a naive regex `\b`. It preserves `C++`, `C#`,
`.NET`, `Node.js`, `CI/CD`, `scikit-learn`, and version punctuation.

| Requirement | Candidate text | Textual result |
|---|---|---|
| `Java` | `Java development` | Supported |
| `Java` | `JavaScript development` | No match |
| `Go` | `Google Cloud` | No match |
| `R` | `Resume writing` | No match |
| `SQL` | `MySQL databases` | No match |
| `SQL` | `SQL databases` | Supported |
| `C++` / `C#` / `.NET` | `C++ development` / `C# development` / `.NET applications` | Supported |
| `Node.js` | `NodeJS development` | No match |
| `Design and maintain Python services.` | `Design and maintain Python services for production` | Supported |

Internal punctuation is not removed: `Python, SQL` is not the same phrase as
`Python SQL`. Joined forms such as `Java-based`, `C++11`, or `Python/SQL` are
conservatively whole tokens rather than expanded into base terms.

Matching searches individual `original_content` records, excluding `identity`
and `contact` evidence. It does not search `normalized_content`, hyperlinks,
formatting metadata, or a concatenation of unrelated records. For each component
it selects the first matching record in source order. Repeated occurrences do not
duplicate a reference.

## Requirement categories and explicit components

The matcher covers required/preferred/uncertain skills, responsibilities,
experience requirements, education requirements, languages, and certifications.
Descriptive metadata and ambiguity notes are not candidate qualifications.

Skill names split only on standalone `and` (case-insensitive), `&`, `/`, or `|`
with whitespace on **both** sides. `Python / SQL` becomes two components;
`Python/SQL` and `R and/or Python` stay whole. If a split would create an empty,
punctuation-only, or connector-only component, the phrase stays whole. Identical
normalized components are assessed once. This is not a natural-language parser.
Free-text responsibilities, experience, education, and certifications are assessed
as whole phrases.

Skills retain their original required/preferred/uncertain category, exact
classification context, and `ai_generated_unverified` status. Other input lists
do not carry a required/preferred classification: their output `classification`
is null, rather than an invented required classification. Their interpretation
status remains AI-unverified.

For a language with a requested level, both the language and level must occur
in the **same** qualification-bearing record. The matcher first searches for
such a record; if none exists, language-only evidence yields partial support and
the level remains unmatched. `English (native)` plus an unrelated `Fluent public
speaker` cannot jointly support `English (fluent)`. No proficiency equivalence
(for example, native meaning fluent) is inferred.

## Status semantics

| Stored status | Meaning |
|---|---|
| `supported` | Every explicit component has boundary-aware textual evidence |
| `partially_supported` | Some components have evidence; remaining components have no match and retain their own assessability status |
| `no_evidence_found` | No positive match, with assessed, nonempty extraction coverage and no blocking warning |
| `not_assessable` | No positive match and insufficient candidate coverage for a reliable negative search conclusion |

Positive matches remain supported or partially supported despite other coverage
gaps. In a partial result with insufficient coverage, missing components are
`not_assessable`. Neither negative status asserts that the candidate lacks a
qualification. **Every result requires human review**, including all-supported
results: textual occurrence and AI classifications are not verified possession.

## Coverage mapping from extraction schema 3.0.0

Warnings accumulate; a first coverage gap does not hide other limitations. Codes
are application-owned and content-free. Private diagnostic text is not copied.

| Actual field / condition | Warning code | Negative-result effect |
|---|---|---|
| Actual `evidence_records` list is empty | `empty_evidence_catalog` | `not_assessable`, regardless of counters/flags |
| `coverage_assessed` is false | `coverage_not_assessed` | `not_assessable` |
| `source_preservation_complete` is false | `source_preservation_incomplete` | Incomplete coverage |
| `unsupported_content_count > 0` | `unsupported_content_present` | Incomplete coverage |
| `unresolved_extraction_details` is nonempty | `unresolved_extraction_details_present` | `not_assessable` |
| `section_classification_complete` is false | `section_classification_incomplete` | Incomplete coverage, including zero detected sections |
| `unclassified_block_indexes` is nonempty | `unclassified_blocks_present` | Incomplete coverage |
| Extraction `warnings` is nonempty | `extraction_warnings_present` | Incomplete coverage |
| Completeness `evidence_records` counter differs from actual list length | `evidence_record_count_mismatch` | Incomplete coverage |
| An evidence record lacks alphanumeric text | `evidence_text_missing` | Incomplete coverage |

Coverage is `not_assessable` for empty/unassessed/unresolved evidence, otherwise
`incomplete` if any warning remains, otherwise `assessed`. Both incomplete states
make negative components `not_assessable`. Contradictory source flags are rejected
by revalidation of the existing `ExtractionCompleteness` model. The catalog stores
an unsupported-content **count**, not the snapshot's detailed unsupported-content
objects; the matcher does not recover or invent unavailable diagnostics.

## Provenance, identity, and validation

Each requirement includes:

- Full SHA-256 `requirement_id` over the category and canonical JSON of the
  complete source item: original wording, quotation, context/status, and any
  language level. Same-name items with distinct quotes/contexts get distinct IDs.
- `category` and zero-based `source_index` into the validated job-analysis list.
- Original wording, quotation, classification/context/status, and language level.
- `components`, each with its own status, method, and `matched_evidence_ids`.
- The existing requirement-level `matched_evidence_ids`, as the unique union of
  component IDs, plus compact `matched_evidence` provenance references.

Each provenance reference copies the actual evidence ID/type, source filename,
source SHA-256, section reference, `SourceLocation` fields, and contributing
`TableCellSource` coordinates. Nested table paths and negative document-local
table IDs are preserved. `cell_texts`, original/normalized candidate text,
formatting, and hyperlinks are not copied. Optional locations/hashes remain null
when unavailable; the matcher never invents them or verifies the source DOCX.

Fully identical records sharing an evidence ID are collapsed for lookup, while
the actual source-list count is preserved. Conflicting records sharing an ID
are rejected. Distinct IDs with repeated text remain distinct records; source
order determines the selected reference.

Pydantic enforces strict types, extra-forbid fields (including nested provenance),
literal statuses/versions/methods, requirement-ID and source-item uniqueness,
component/evidence unions, language-level association, and coverage/status
consistency. Standalone JSON reconstruction verifies structure;
`matcher.validate_result(result, analysis)` additionally checks against **both
actual inputs**, including evidence existence, exact provenance, requirement
identity, coverage metadata, and a replay of the deterministic assessments. The
CLI applies this check after serialization/reconstruction and before writing.

Identical inputs produce identical result IDs and JSON. No timestamps, random
match IDs, or arbitrary confidence values affect the result.

## CLI and private file handling

```bash
.venv/bin/python -m resume_assistant match-job \
  --analysis data/private/processed/job_description_analysis.json \
  --candidate-evidence data/private/processed/candidate_evidence.json \
  --output data/private/processed/job_candidate_match.json
```

The displayed paths are the defaults for all three flags. Explicit inputs may be
other local regular UTF-8 files; every directory and final file is opened without
following symlinks. URL paths, NULs, and `..` traversal components are rejected.
Directory/file identity and file size/timestamps are checked before/after reads.
Duplicate JSON keys, nonstandard constants, incompatible versions, and invalid
model data fail with sanitized artifact-specific errors.

Output is confined to `data/private/processed/`. Its no-follow directory chain
is pinned before reading/matching; aliases to inputs are rejected. Validated JSON
is written through the existing exclusive mode-0600 temporary and descriptor-
relative atomic replacement facility. No API configuration is needed or used.
CLI output is aggregate counts, schema labels, a source-hash-presence boolean,
coverage and review indicators, and the output path. No candidate names, document
hash values, evidence text, provenance records, or full matches are printed.

Expected input/validation/integrity errors exit `2`; unexpected failures exit `3`.
Existing `extract-master` and `analyze-job` behavior is preserved.

## Remaining limits and review

Lexical matches can occur in negated, hypothetical, interest-only, or otherwise
non-qualifying prose. Even same-record language/level occurrence is textual
association, not grammatical or semantic proof. No semantic entailment,
qualification possession, proficiency equivalence, extraction recall, or complete
extraction is claimed. Human review is always required.

Secure filesystem operations are verified on Linux; unsupported platforms fail
closed. Pinned handles prevent symlink redirection; they do not prevent privileged
direct modification, all directory-renaming windows, or power-loss failures.
Atomicity is per output file; no fsync or multi-file transaction is added.

Automated acceptance tests are in `tests/test_matching.py`,
`tests/test_matching_remediation.py`, and `tests/test_matching_cli.py`. All matcher
fixtures are synthetic. Exact full-suite commands, exclusions, and measured
results are recorded in `docs/PROJECT_STATUS.md` §F.
