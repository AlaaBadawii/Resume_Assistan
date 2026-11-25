# Job Description Analysis — Missions 3.1 / 3.1.1

Missions 3.1 and 3.1.1 are **APPROVED** after remediation of the original nine
review findings and explicit owner approval. Their rejection/remediation history
is preserved in `PROJECT_STATUS.md`. This analyzer produces the schema-2 input
for local matching and deterministic section planning; see [planning.md](planning.md).

## Pipeline and responsibilities

```text
analyze-job --input job.txt
    -> normalize output lexically; reject input/output aliases
    -> pin the real private-output directory chain before processing
    -> read a regular UTF-8 input file
    -> LLMConfig (environment only) -> AgentRouterClient (stdlib urllib)
    -> trusted versioned instructions, with untrusted JD supplied separately
    -> completed Responses envelope -> strict JSON object
    -> JobDescriptionPayload: required keys + own-evidence validation
    -> check every quotation and skill context against the original JD
    -> JobDescriptionAnalysis: application-owned canonical metadata
    -> independent JSON round-trip validation
    -> exclusive temporary file + descriptor-relative atomic replacement
    -> data/private/processed/job_description_analysis.json
```

`job_analyzer.py` has no extraction/evidence imports or file-reading logic.
The shared CLI/package imports extraction code for `extract-master`, but
`analyze-job` does not invoke it or inject candidate information. This is a
data-flow boundary, not an operating-system sandbox or content-type classifier.
Tests use synthetic JDs; no private inputs were read in Mission 3.1.1.

## Source-phrase grounding, with explicit uncertainty

Schema **2.0.0** introduces evidence-linked values. The deterministic checks are:

1. Every non-null scalar has `{value, quotation}`.
2. Each value/text/name/language/non-null language level must be an exact,
   case-sensitive substring of **its own** quotation.
3. Each quotation must be an exact contiguous substring of the original JD.
4. Each skill quotation must occur within its `classification_context`, and
   that entire context must itself be an exact source substring.
5. Text must be nonempty and contain at least one alphanumeric character.
   Values, quotations, spacing, case, and newlines are preserved verbatim.

Unrelated copied quotations no longer justify invented values. Paraphrases,
translations, reconstructed spans, and normalized labels are rejected unless
the claimed phrase appears literally in its own evidence. Unknown scalars are
`null`, and empty lists mean no items were extracted.

**These checks are not semantic entailment or completeness verification.** A
literal phrase can be assigned to the wrong semantic field, or can occur inside
instruction-like source text. Every artifact therefore carries:

```json
"grounding_method": "exact_substrings_only",
"interpretation_status": "ai_generated_unverified"
```

Skill items additionally carry `classification_status: "ai_generated_unverified"`.
Required/preferred classification remains an AI interpretation, with exact
context available for inspection. `uncertain_skills` preserves unclear or
conflicting source interpretations. The same normalized comparison name
(`name.strip().casefold()`, for comparison only) cannot appear in different
classification lists; such responses fail with `JobAnalysisConflictError`.
Distinct contexts for the same name in one list are retained.

Identical entries are deduplicated within each list by equality of **all
validated fields**, including original quotation/context. One quotation can
support multiple distinct copied phrases; deduplication is not quotation-only.
No keyword-based semantic inference or second LLM entailment check is used.

## Strict schema

`JobDescriptionPayload` requires all 15 analysis keys. Pydantic uses
`extra='forbid'` and strict types at every model level. Missing required keys,
unrelated keys, invalid item shapes, and blank values fail closed. Model-supplied
top-level application metadata is forbidden rather than trusted or overwritten.

| Analysis field | Required representation |
|---|---|
| `role_title`, `company_name`, `seniority_level`, `employment_type`, `work_arrangement` | `{value, quotation}` or explicit `null` |
| `responsibilities`, `education_requirements`, `experience_requirements`, `ambiguities` | list of `{text, quotation}`; text is an original phrase |
| `required_skills`, `preferred_skills`, `uncertain_skills` | list of `{name, quotation, classification_context}` |
| `languages` | list of `{language, level, quotation}`; `level` is required but nullable |
| `certifications` | list of `{name, quotation}` |
| `missing_information` | identifiers of all five scalar fields whose extracted value is `null` |

`missing_information` describes **unextracted scalar fields**, not proof that
information is absent from the source. Arbitrary missing-information prose is
rejected. Its identifier set must exactly match the null scalar fields.

A structurally empty raw analysis is still a complete object:

```json
{
  "role_title": null,
  "company_name": null,
  "seniority_level": null,
  "employment_type": null,
  "work_arrangement": null,
  "responsibilities": [],
  "required_skills": [],
  "preferred_skills": [],
  "uncertain_skills": [],
  "education_requirements": [],
  "experience_requirements": [],
  "languages": [],
  "certifications": [],
  "ambiguities": [],
  "missing_information": [
    "role_title", "company_name", "seniority_level", "employment_type", "work_arrangement"
  ]
}
```

The artifact adds application-owned `schema_version: "2.0.0"`, current
`prompt_version: "2.0.0"`, and the grounding/interpretation markers above.
These fields and skill classification markers cannot be changed to claim
semantic verification. Old schema 1.0.0 artifacts are incompatible and are
not automatically migrated or regenerated. Extraction schema **3.0.0** is
separate and unchanged.

## Prompt and JSON handling

`prompts.py` contains trusted instructions and prompt version **2.0.0**.
The JD is supplied separately as `input` and is explicitly described as
untrusted data. Unsupported claims in simulated instruction-following responses
are rejected deterministically; this does **not** establish live-model prompt
injection immunity. A prompt alone cannot guarantee that immunity.

Only one JSON object is accepted. Complete `json`/unlabelled code fences are
tolerated; incomplete/other fences and commentary are rejected. Duplicate JSON
keys and non-standard `NaN`/infinity constants are rejected in both provider
envelopes and analysis parsing. No partial result is persisted after rejection.

## Configuration and provider contract

No `.env` loader or new dependency is bundled; configuration comes from the
process environment. Both direct `LLMConfig` construction and `from_env()`
enforce the same invariants. Credential values are excluded from config reprs.

| Variable | Required | Default |
|---|---|---|
| `AGENTROUTER_API_KEY` | yes | — |
| `AGENTROUTER_BASE_URL` | no | `https://agentrouter.org/v1` |
| `AGENTROUTER_MODEL` | no | `gpt-6.1-sol` |
| `AGENTROUTER_TIMEOUT_SECONDS` | no | `60` |

- HTTPS is required except for **numeric loopback HTTP** endpoints used by
  local development/integration tests. HTTP hostname endpoints, including
  nonnumeric `localhost`, are not permitted.
- Hostnames/IP addresses and ports (1–65535) are validated structurally.
  URL credentials, queries/fragments, whitespace/control characters, and
  invalid hosts/ports are rejected. API base paths use unreserved ASCII
  components; empty/dot/parent components, encoded paths, and a trailing
  `responses` endpoint are rejected. A normal trailing slash is normalized.
- Timeouts must be finite positive numbers; NaN, infinity, and overflow are rejected.
- The endpoint is `{normalized_base_url}/responses`. Requests contain
  `{model, instructions, input, stream: false}` with bearer/JSON headers.
- **All redirects are rejected**, including same-origin redirects. The client
  does not construct a redirected GET or forward authorization to a destination.
- The required envelope fields are `status`, `error`, `incomplete_details`,
  and `output`. Status must be `completed`, with both detail fields `null`.
  There must be one completed assistant message containing only nonblank
  `output_text` fragments. Well-formed reasoning summaries/content may be
  ignored; refusal, unknown tool/output types, malformed items, additional
  assistant messages, and partial content fail closed. Ancillary provider
  metadata is not part of the analysis contract.

The live AgentRouter contract, current model availability/Responses-only
behavior, tuning-parameter compatibility, and reported User-Agent requirements
remain **unverified**. The client sends an honest `resume-assistant/0.1` UA and
omits tuning parameters; their rejection by the live provider is not claimed.
No live provider call was made in Mission 3.1.1. Real transport testing used
only synthetic loopback HTTP/TLS servers and temporary test certificates.
There are no retries, streaming, token accounting, or tool execution.

## Filesystem security and operating-system limits

`private_output.py` is shared by the artifact-publishing CLI commands:

- Normalize paths lexically under the trusted project root; never resolve an
  output through a symlink to choose the trusted private root.
- Open the project root and every private/output-parent component with
  `O_DIRECTORY | O_NOFOLLOW`. Reject symlinked roots/intermediate directories
  and nonregular/symlink destinations. Create missing directories relative
  to already-open parents with mode 0700.
- Keep directory handles open across extraction/model generation. Check
  device/inode identities without following symlinks before writing; both
  extraction destinations are checked before either writer is called.
- Create random temporary files with `O_CREAT | O_EXCL | O_NOFOLLOW`, mode
  0600, relative to the pinned parent. Collisions do not delete or follow
  pre-existing entries. Cleanup is relative to the same handle.
- Replace atomically using `os.replace(..., src_dir_fd=fd, dst_dir_fd=fd)`.
  A parent swap at the replacement syscall cannot redirect the operation
  through an external symlink. Mutation noticed during processing/temporary
  creation is rejected with prior artifact/source bytes preserved.

**Verified platform:** Linux / Python 3.12.14. Other POSIX systems must provide
the checked no-follow/directory-relative operations; they were not executed
here. Windows and unsupported platforms fail closed with a controlled error,
without an insecure path-based fallback.

The project root and its ancestry are trusted. Directory handles prevent
symlink redirection, not directory renaming itself: a last-instant rename can
leave a committed file in the originally pinned directory under a different
pathname. No pathname-visibility lock, protection against a privileged/private-
directory owner directly changing files, fsync/power-loss durability, or
general multi-file transaction is claimed. Replacement remains atomic **per
output file**. Extraction additionally uses immutable generations and a single
manifest commit point, so readers see one committed snapshot/catalog pair;
see [extraction.md](extraction.md) for that publication contract.

## CLI and errors

```bash
source .venv/bin/activate
# Copy .env.example to .env and configure it, then explicitly load it:
set -a; source .env; set +a
python -m resume_assistant analyze-job --input path/to/job.txt
# --output may select another file below data/private/processed/.
```

Directories, missing/unreadable/nonregular inputs, and invalid UTF-8 produce
controlled input errors. Normalized equal paths and existing symlink/hardlink
source/output aliases are rejected. Only a regular file is read; the opened
descriptor is also checked before reading.

The console shows counts, presence booleans, and source/model/output metadata,
including `interpretations_verified: False`; it does not show extracted values,
JD text, response bodies, or credentials. Messages are static or contain only
HTTP status integers, exception type names, or fixed field locations.

- Exit **0**: successful analysis and persistence.
- Exit **2**: expected input/configuration/destination/network/provider/format/
  grounding/quotation/classification/validation errors.
- Exit **3**: unexpected implementation or transport failures, type name only.

## Verification provenance

| Run | Collected | Passed | Failed | Deselected | Warnings | Privacy scope |
|---|---:|---:|---:|---:|---:|---|
| Original Mission 3.1 implementation-agent report | 189 | 189 | 0 | 0 | 11 | Unrestricted invocation; includes seven private-source tests. Earlier synthetic-only/no-private-read claims are withdrawn. |
| Independent read-only Mission 3.1 review | 189 | 182 | 0 | 7 | 11 | Explicit exclusions; private contents not read during the review. |
| Mission 3.1.1 remediation | 516 | 509 | 0 | 7 | 11 | Synthetic inputs; temporary artifacts; loopback-only transport tests; no private contents or live provider calls. |

The seven exclusions and exact remediation invocation are recorded in
`docs/PROJECT_STATUS.md` §F. `pip check` and `git diff --check` pass. Eleven
additional in-memory safeguard-removal checks confirmed test sensitivity;
these are separate from the pytest case counts. Legacy warnings were not
suppressed. Those rows record historical pre-approval verification. Missions
3.1/3.1.1 were subsequently owner-approved; approval provenance is in
`PROJECT_STATUS.md`. Managed analysis-to-planning verification is described in
[planning.md](planning.md).
