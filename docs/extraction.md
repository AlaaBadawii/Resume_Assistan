# Extraction Architecture — Mission 2 through Mission 2.2.1

Mission 2.2.1 is **IMPLEMENTED — AWAITING REVIEW**. Mission 2 remains **NOT
APPROVED**. Mission 2.1.2 and Missions 3.1, 3.1.1, 3.2, and 3.2.1 remain
APPROVED. Verification history and the independent-review gate are recorded in
`docs/PROJECT_STATUS.md`.

## Pipeline and responsibility boundaries

```text
local DOCX path
    -> private_input.capture_docx_source()
       pinned no-follow regular-file read -> immutable bytes + filename + SHA-256
    -> MasterResumeExtractor.extract()
       python-docx Document(BytesIO(the_same_bytes))
    -> DocumentCoverage
       actual header/footer references + owning parts + located structural gaps
    -> DocumentSnapshot (schema 3.0.0)
       body, default-part projections, ownership, coverage, unresolved details
    -> SectionDetector.detect() (existing heuristic)
    -> build_evidence_catalog() (existing body/recursive table-row projection)
    -> CandidateEvidenceCatalog (schema 3.0.0)
    -> strict raw-field revalidation of both mutable models
    -> JSON serialization with warnings='error' + reconstruction
    -> captured-source hash checks on both artifacts and generated provenance
    -> existing pinned, mode-0600, atomic-per-file private writes
```

The extractor never saves the source DOCX. `document_coverage.py` examines an
already-parsed document; it has no source-file, provider, or network I/O. The
CLI owns artifact validation and persistence. The matcher remains a separate
offline consumer of the existing schema-3.0.0 candidate catalog.

## Captured-byte source binding

`private_input.py` now shares its established safe byte-read implementation
between matching artifacts and DOCX capture. It:

- Rejects invalid/local-path inputs, traversal components, and symlinks.
- Opens directory ancestry with `O_DIRECTORY | O_NOFOLLOW`, retaining the
  descriptors through the read and checking each directory identity.
- Rejects nonregular inputs before reading; opens the final file with
  `O_NOFOLLOW | O_NONBLOCK` and checks the opened descriptor.
- Compares device, inode, size, modification time, and change time before,
  during, and after the read, including the current directory-relative entry.
- Closes all file/directory descriptors and exposes only content-free errors.

`CapturedSource` is frozen and excludes private data from its repr. Its digest
is computed from its immutable `data` bytes. ZIP checks, python-docx parsing,
and the existing numbering reader all consume streams backed by those same
bytes. The extraction path does not independently reopen the pathname for
hashing or parsing. The standalone `compute_sha256` utility remains available
for explicitly authorized caller/test comparisons.

The structural invariant is:

```text
SHA256(bytes supplied to python-docx) == snapshot.source_sha256
                                    == catalog.source_document.sha256
                                    == generated evidence source_sha256
                                    == header/footer part source_sha256
```

The original captured filename is retained. A pathname replacement or source
edit after capture cannot substitute a second logical input: extraction uses
the captured version. This does not promise that an external editor cannot
change the pathname afterward. Changes noticed during capture are rejected;
ordinary valid extraction leaves source bytes unchanged.

Invalid/truncated ZIPs, missing required parts/body, malformed XML, and
unreadable inputs produce sanitized errors and no newly written artifacts.
Unexpected implementation errors remain distinguishable by type internally.

## Serialization privacy

The extraction CLI reads model fields into a fresh Python validation payload
without invoking Pydantic serializers. `model_validate(..., strict=True)`
therefore rechecks nested mutable data rather than trusting an already-created
model instance. Both catalog and snapshot then serialize using
`model_dump_json(..., warnings='error')` and undergo independent JSON loading
and equality checks before writing.

Invalid values fail before JSON serialization; residual serialization warnings
become serialization errors rather than stderr messages containing values.
Validation/serialization/document/I/O failure handlers expose fixed messages
or exception type names, not candidate text, paths, warning payloads, or raw
exception messages. Catalog and snapshot leak tests independently inject a
unique synthetic private marker into mutable model values and reach the real
CLI preparation path. They assert absence from both stdout and stderr and
preservation of existing artifact/source bytes.

No global warning filter or blanket suppression is installed. The eleven
existing python-docx FutureWarnings remain visible in the full regression run.

## Structural coverage and available locations

`DocumentCoverage` inventories supported structural contexts and known
unhandled content. Body/table/row/cell/paragraph/run/hyperlink child allowlists
make unrecognized inline children conservative gaps rather than letting their
text disappear inside otherwise recognized paragraphs.

Newly diagnosed WordprocessingML content includes:

- `w:moveTo`, `w:moveFrom`, `w:dir`, and `w:bdo`;
- `w:ruby`, `w:sym`, `w:softHyphen`, `w:delText`, and `w:delInstrText`;
- `w:contentPart` and `w:subDoc` references.

Existing diagnostics remain for SDTs, `mc:AlternateContent`, `altChunk`,
custom-XML/smart-tag containers, drawings/pictures/objects/textboxes,
insertions/deletions, fields, note/comment references and parts, internal
anchors, and the previously declared numbering-definition gaps. This mission
does not add semantic interpretation for those structures. Supported run text
controls, including ordinary tabs/breaks, positioned-tab text equivalents,
and no-break hyphens, do not create spurious gaps.

Each new gap preserves its existing `tag`, `parent_path`, `description`, and
`extracted` fields plus optional `source_location` and `reason`. The indexed
XML path identifies the actual container within its owning package part.
Available body/paragraph/table/row/cell/nesting coordinates are copied from
source structure; unavailable coordinates remain null. Body/nested table IDs
retain their established namespaces. Unsupported header/footer table indexes
are part-local XML-preorder ordinals used for diagnostics, not body table IDs.

Reason codes distinguish:

| Reason | Meaning |
|---|---|
| `unsupported_structure` | Located content/structure was detected but is not fully extracted |
| `unresolved_reference` | A reference cannot be resolved safely within its declared kind/variant, or references conflict |
| `inventory_unavailable` | The owning content root cannot be inventoried reliably |

Unresolved header/footer references/conflicts and unavailable roots make
`coverage_assessed=False`. A successful declared inventory containing known
unsupported content may be assessed, but it still cannot establish complete
preservation. Package properties, themes/settings/font/style metadata, and
non-visible custom-XML metadata are outside candidate-content copying scope.

This is a bounded structural assessment, not recognition of every possible
current or future WordprocessingML extension, byte-for-byte package copying,
or Word rendering fidelity.

## Actual header/footer relationships and ownership

The extractor no longer accesses `section.header`/`section.footer` convenience
properties to discover source content. Actual section references and package
relationships determine ownership, so absent parts remain absent.

All default, first-page, and even-page references are accounted for. Relationship
type, content type, owning-part name, and XML root must agree with the declared
header/footer kind. External relationships are not fetched. Broken/missing IDs,
unknown variants, wrong kinds/types, and conflicting targets are diagnosed.
Packages whose referenced parts cannot be loaded fail closed with sanitized
input errors.

Linked-to-previous uses retain their originating section and XML reference
address. Actual references, effective inherited uses, relationship IDs, and
resolution state are persisted in `header_footer_references`. Shared parts
are keyed by actual package part URI and extracted/inventoried once, even when
multiple sections or relationship IDs reference them.

`header_footer_parts` retains each actual owning part's name, kind, source
filename/hash, extraction status, and supported blocks. Its collection order
follows first resolved reference discovery, then remaining package parts in
canonical URI order; it implies no page/body-stream interleaving. Paragraph
order within each extracted part follows its XML source order.

| Part status | Current support |
|---|---|
| `extracted` | Referenced default-part paragraphs were captured with no structural gap in that part |
| `partial` | Default paragraphs were captured, but other part content has located gaps |
| `unsupported` | Non-default-only or unreferenced part is accounted for but not interpreted |
| `inventory_unavailable` | Part content cannot be reliably inventoried |

Legacy `headers`/`footers` remain default-part paragraph projections. Each actual
owning part contributes once; aggregate projection indexes are retained while
new locations use owning-part paragraph coordinates. Referenced empty parts
remain explicit; missing parts are not replaced with fabricated empty ones.
First/even content shared with a captured default part is not copied twice.

Header/footer tables remain unsupported and receive explicit owning-part/table
diagnostics. Non-default-only content remains unsupported with located
diagnostics. Default header/footer content is retained in the snapshot, outside
the existing body-evidence and section streams; it is not new searchable
candidate evidence.

## Schema compatibility decision

The compatibility decision was recorded before implementation in
`docs/PROJECT_STATUS.md` §J and the earlier version of this document.
Snapshot and candidate evidence remain **schema 3.0.0**. Additions are optional
and legacy-defaulted:

| Model | Added data / legacy default |
|---|---|
| `DocumentBlock` | `source_location=None` |
| `UnsupportedContent` | `source_location=None`, `reason=None` |
| `DocumentSnapshot` | `header_footer_parts=[]`, `header_footer_references=[]` |
| `ExtractionCompleteness` | Detailed `unsupported_content=[]` |

Existing provenance fields, body/row evidence shapes, body/nested identifier
namespaces, and completeness formulas retain their meanings. Correcting phantom
and duplicate source parts repairs an existing bug rather than changing the
default-only projection into a rendered-page or all-variant content stream.

Old schema-3.0.0 JSON remains loadable through defaults. Detailed gaps now
survive both artifacts, including snapshot-based catalog reconstruction; old
catalogs retain their counts/unresolved indicators even when detail objects
are unavailable. The approved matcher consumes the same version/types and
existing coverage signals without source changes. Match schema 2.0.0 and job
schema/prompt 2.0.0 are unchanged.

Synthetic compatibility tests cover legacy field omission, new catalog loading,
unchanged lexical results/coverage, and `extract-master` -> `match-job` CLI
execution. No old private artifacts are automatically migrated or regenerated.

## Completeness semantics — unchanged formulas, stronger evidence

- `coverage_assessed`: the declared structural/reference inventory completed
  reliably. Legacy snapshots without an assessment default to False.
- `source_preservation_complete`: assessed coverage AND zero coverage gaps.
- `section_classification_complete`: at least one detected section AND no
  unclassified supported body blocks; not proof of evidence-type semantics.
- `complete`: preservation AND section classification AND zero unresolved details.

`ExtractionCompleteness` continues to reject contradictory component/overall
flags. Known unsupported content prevents complete preservation. Unavailable
inventories/unresolved parts are conservative, independent of section detection.
Diagnostics and unresolved details persist and reconstruct without relying on
an extractor-side list. Matching preserves positive lexical evidence despite
unrelated coverage gaps, as in its approved contract.

## Body/table behavior and remaining scope

Supported body paragraphs/tables retain their existing ordered stream. Body
table IDs are their nonnegative supported body-block indexes; nested table IDs
are negative document-local preorder IDs reset on each extraction. Paragraph
and row evidence IDs remain SHA-256 over source hash plus those established
identities. Cell paragraph positions and contributing-cell sources remain typed.

Unchanged limitations remain for later finalization missions:

- Numbering format/override resolution still uses the existing algorithm,
  now reading captured ZIP bytes. Combined starts, raw IDs, style inheritance,
  parser-partial maps, and owning-numbering-part selection remain later work.
- Hyperlink internal-anchor/per-link mapping and full field structure/evaluation
  remain later work; existing coverage gaps make no new support claim.
- Cell paragraph/nested-table interleaving, merge geometry/contact typing,
  classification-source consistency, and CLI count labels remain later work.
- Statistics remain incompletely scoped at deeper table levels and for media
  and empty header/footer paragraphs. Shared default parts are now counted once;
  this is not a complete F-14 statistics remediation.
- Heading/evidence classification remains heuristic, not semantic proof.

## Private persistence and failure guarantees

Both validated, serialized, reconstructed artifacts pass captured-input hash
checks before either write. Validation or serialization failure preserves
both previous artifact bytes and the source. Output paths remain lexically
confined beneath `data/private/processed/`; alias checks precede extraction.

The existing `PrivateOutput` facility is unchanged: pinned no-follow ancestry,
exclusive mode-0600 temporaries, identity checks, and descriptor-relative atomic
replacement/cleanup prevent symlink redirection. Linux is verified; unsupported
platforms fail closed. Pinned handles do not prevent every directory-renaming
window or privileged/direct modification.

Atomicity is still **per file**, not transactional across the pair. A second
replacement failure may leave the first artifact new and the second old.
Two-artifact publication belongs to Mission 2.2.2. No fsync/power-loss durability
or fully transactional read/write guarantee is claimed.

## Historical descriptions and verification

The human-provided original review descriptions are restored without added
historical speculation:

- **F-15: stale documentation** — scoped extraction/status descriptions are
  corrected; broader documentation review remains F-25.
- **F-20: unresolved lists not reset** — existing `extract()` resets hyperlink
  and numbering lists; new changed/clean/failed-call regressions verify isolation.

Mission 2.2.1 adds **107 synthetic tests**: 17 capture tests, 8 privacy tests,
29 inline/diagnostic tests, 44 part-integrity tests, and 9 matcher compatibility
tests. Full privacy-safe regression: **812 collected, 805 passed, 7 deselected,
0 failed, 0 skipped, 11 unchanged FutureWarnings**, 22.84s. `pip check` and
`git diff --check` pass. Exact commands and exclusions are in
`docs/PROJECT_STATUS.md` §F/§J.

All new fixtures/artifacts are synthetic and temporary, outside the repository.
No `.env`, real Master Resume, or real candidate artifact contents were read or
regenerated. Existing loopback transport tests were explicitly authorized;
external network/API activity remained prohibited and was not attempted.
Implementation and verification do not record human approval.
