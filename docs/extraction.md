# Extraction Architecture — Mission 2

## Overview

The extraction layer converts the private Master Resume (`data/private/Master_Resume.docx`)
into a deterministic, typed, source-faithful evidence catalog.

Pipeline (CLI: `python -m resume_assistant extract-master`):

```text
Master_Resume.docx
    -> MasterResumeExtractor.extract()      # ordered body stream + parts
    -> SectionDetector.detect()             # top-level section boundaries
    -> build_evidence_catalog()            # block -> EvidenceRecord mapping
    -> CandidateEvidenceCatalog            # Pydantic validated
    -> data/private/processed/candidate_evidence.json
```

## Design decisions

1. **Ordered body stream.** Extraction iterates the WordprocessingML body element
   children in document order and dispatches on tag (`w:p`, `w:tbl`). Paragraphs and
   tables therefore never lose their original interleaved sequence, unlike independent
   `doc.paragraphs` / `doc.tables` collections.

2. **Source faithfulness.** Evidence records store the original text plus formatting
   metadata. Normalized values are only introduced when unambiguous; otherwise the
   original is preserved and the normalized value is left unresolved.

3. **Deterministic evidence IDs.** `evidence_id = sha256(f"{source_sha256}|{block_index}|{paragraph_index}")`.
   Derived from source hash + source location, not random UUIDs.

4. **Confidence-based section detection.** Signals considered: built-in Heading styles,
   bold + font size (strong headings are bold at >= 14 pt; sub-headings are typically bold
   at >= 12 but below that), leading numbering patterns (top-level single number group;
   sub-sections use two-group numbering such as `4.1`). Sub-headings never start new
   top-level sections.

5. **Unclassified content.** Blocks before the first detected section and blocks that do
   not meet heading confidence thresholds are reported via
   `extraction_completeness.unclassified_block_indexes` rather than being force-assigned.

6. **Unsupported content.** The scanner flags embedded content containers it does not
   process (drawing, pict, object, txbxContent, mc). Nothing is silently discarded; the
   inventory is exposed on the snapshot and in the extraction summary.

7. **Private output.** The JSON artifact is written under `data/private/processed/` and
   is excluded from version control. Terminal output prints only aggregate statistics,
   warnings, and the output path — never document contents.

## Limitations

- Heading detection is heuristic and confidence-scored; very weak headings may be skipped.
- `Master Resume` contains manually formatted headings (bold + direct font size) with no
  Heading styles; the detector compensates via bold/size signals.
- The footer of the Master Resume contains candidate information and a page-number field;
  it is captured during extraction but is not part of the section stream.
- Date normalization is intentionally not performed; original date text is preserved.
