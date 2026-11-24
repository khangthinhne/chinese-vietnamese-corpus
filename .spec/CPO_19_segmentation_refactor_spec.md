# Specification: CPO_19 OCR Cleaning, Script Classification, and Segmentation Refactor

**Project:** Hán--Việt Parallel Corpus\
**Target:** `CPO_19`\
**Purpose:** Implementation specification for an agent refactoring the
post-OCR pipeline.\
**Scope:** Start from the existing unified `raw.xml`. Do not change the
OCR engine or rerun OCR by default.

## 1. Objectives

The refactor must:

1.  Clean and normalize OCR text before structural segmentation.
2.  Correct known OCR variants of structural markers such as `PHIỀN ÂM:`
    to `PHIÊN ÂM:`.
3.  Classify each line by script (`han`, `vie`, `mixed`, `unknown`)
    using Unicode character statistics.
4.  Detect candidate document boundaries, especially when a CJK-dominant
    line appears after a Vietnamese section.
5.  Segment each document into Hán original, phiên âm, dịch nghĩa, dịch
    thơ, and auxiliary sections.
6.  Preserve every source line ID and allow all derived text to be
    traced back to the OCR source.
7.  Report ambiguity and incomplete structures for human review. Never
    silently discard lines.

## 2. Existing architecture and scope

The existing OCR flow remains:

``` text
PDF -> page images -> Qwen single-stream OCR
    -> raw JSON per page
    -> OCR normalization
    -> 03_ocr/ocr_raw/raw.xml
    -> 03_ocr/ocr_raw_text/NNNN.txt
```

This refactor begins after `raw.xml` is generated. `raw.xml` and
`03_ocr/raw/NNNN.json` are immutable source artifacts. All corrections
and cleaning must produce derived data.

## 3. Terminology

Keep these concepts distinct:

-   **OCR line:** A line recognized by Qwen.
-   **Cleaned line:** A derived version after safe normalization and
    approved corrections.
-   **Script:** Writing system detected in a line: `han`, `vie`,
    `mixed`, or `unknown`.
-   **Marker:** A heading that changes the active section,
    e.g. `PHIÊN ÂM:`.
-   **Section/layer:** Semantic role: `han`, `phien_am`, `dich_nghia`,
    `dich_tho`, `khao_di`, `chu_thich`, `nguyen_chu`, or `other`.
-   **Document/work:** An independently alignable work.
-   **Sub-work:** A component within a larger work, e.g. `KỲ NHẤT`,
    `KỲ NHỊ`, `其一`, `其二`. Do not automatically treat every sub-work
    as a top-level document.

`language/script` and `layer/section` are not interchangeable. `han` and
`vie` describe script; `phien_am`, `dich_nghia`, and `dich_tho` describe
semantic sections.

## 4. Target pipeline

``` mermaid
flowchart TD
    A[Unified OCR raw.xml] --> B[OCR Text Normalization]
    B --> C[Marker OCR Correction]
    C --> D[Pre-segmentation Cleaning]
    D --> E[Line Script Classification]
    E --> F[Marker Detection]
    F --> G[Document Boundary Detection]
    G --> H[Document Assignment]
    H --> I[Sequential Section Segmentation]
    I --> J[Structural Cleaning]
    J --> K[Sentence Segmentation]
    K --> L[Sentence Cleaning]
    L --> M[Validation]
    M --> N[Alignment]
    N --> O[Export]
```

Process all pages as one continuous stream, sorted by numeric page
number and then line `ORDER`. Do not reset state at page boundaries.

## 5. Raw data immutability and provenance

Never modify or overwrite:

-   `03_ocr/raw/NNNN.json`
-   `03_ocr/ocr_raw/raw.xml`

Every derived line must retain:

-   original `line_id` (e.g. `P0015.L006`)
-   page number and reading order
-   original OCR text
-   normalized/corrected text, if applicable
-   original bbox
-   OCR confidence if present
-   correction/classification/cleaning metadata

Do not invent confidence values. If lines are split or merged, record
all parent/source line IDs and the transformation.

## 6. OCR normalization and correction

### 6.1 Safe normalization

Allowed operations:

-   Normalize Unicode consistently (prefer NFC for Vietnamese).
-   Normalize line endings and repeated whitespace.
-   Trim leading/trailing whitespace.
-   Remove non-semantic control characters.
-   Preserve meaningful punctuation, diacritics, CJK characters, and
    reading order.

Do not transliterate, translate, modernize, or rewrite content.

### 6.2 Marker correction

Correct known marker OCR variants before marker detection. Examples:

  OCR output      Canonical marker
  --------------- ------------------
  `PHIỀN ÂM:`     `PHIÊN ÂM:`
  `PHIEN AM:`     `PHIÊN ÂM:`
  `DỊCH THO:`     `DỊCH THƠ:`
  `DICH THO:`     `DỊCH THƠ:`
  `KHÁO DỊ:`      `KHẢO DỊ:`
  `DỊCH NGHĨA:`   `DỊCH NGHĨA:`

Requirements:

-   Keep a configurable alias dictionary per work.
-   Apply exact alias matching first.
-   Fuzzy matching is only for short heading-like lines, not arbitrary
    content.
-   Initial fuzzy threshold may be 80/100, configurable and evaluated
    against CPO_19.
-   Preserve `original_text`, `corrected_text`, canonical marker,
    method, and status.
-   If multiple markers are plausible, send the line to review.
-   Do not fuzzy-rewrite ordinary prose or poetry.

### 6.3 Content correction

Do not automatically rewrite arbitrary Hán or Vietnamese content based
on a dictionary or language model alone.

For content lines, detect suspicious text and optionally suggest
corrections, but require visual/manual verification before accepting
them. Preserve original and corrected forms. Rare Han characters,
variants, names, place names, reign titles, and classical spellings must
not be treated as errors merely because they are uncommon.

## 7. Pre-segmentation cleaning

This stage prepares OCR lines for script and structure detection. It
must be conservative.

### 7.1 Allowed operations

-   Remove empty/whitespace-only lines from the processing stream while
    recording them.
-   Normalize whitespace.
-   Remove clearly non-text control artifacts.
-   Detect likely duplicate OCR lines, but do not deduplicate repeated
    text unless duplication is supported by evidence.
-   Detect likely running headers, footers, page numbers, and OCR noise.
-   Flag uncertain candidates instead of deleting them.

### 7.2 Header/footer and page-number handling

Known CPO_19 running-header examples:

-   `HOÀNG HOA ĐỒ PHẢ`
-   `NGÔ THÌ NHẬM TOÀN TẬP`

A numeric-only line such as `18` may be a page number. Do not remove it
based only on short length or numeric content: numbers can be dates,
reign years, poem numbering, or actual content.

Use repetition across pages, consistent page position, configured
aliases, and surrounding context. Record every excluded line with
`line_id`, original text, reason, rule, and status. No silent deletion.

### 7.3 Output

Write a derived artifact such as:

``` text
04_segmentation/preprocessing/preprocessed.xml
04_segmentation/preprocessing/cleaning_log.json
```

Adapt the exact path to repository conventions, but keep it separate
from `raw.xml`.

## 8. Line-level script classification

### 8.1 Labels

-   `han`: predominantly CJK ideographs.
-   `vie`: predominantly Latin-script text, including Vietnamese
    diacritics.
-   `mixed`: substantial CJK and Latin content.
-   `unknown`: insufficient or ambiguous script evidence.

### 8.2 Character counting

Count at least:

-   `cjk_count`
-   `latin_count`
-   `other_letter_count`
-   `script_character_count`
-   `cjk_ratio`
-   `latin_ratio`

The initial CJK detection should support CJK Unified Ideographs
(`U+4E00–U+9FFF`) and relevant extension/compatibility ranges supported
by the runtime. Do not assume the basic range covers every character in
the corpus.

Punctuation, whitespace, digits, and symbols must not inflate script
ratios.

### 8.3 Initial configurable thresholds

Suggested starting values, to be evaluated on manually inspected CPO_19
lines:

-   `han` when `cjk_ratio >= 0.50`.
-   `vie` when `latin_ratio >= 0.70` and the line is not substantially
    mixed.
-   `mixed` when both script groups are substantial.
-   `unknown` when too few script characters exist or no rule
    confidently applies.

These are initial heuristics, not universal truths. Keep them
configurable. A line containing one Han character is not automatically a
Han line. Short titles, names, numbers, and mixed annotations may need
review.

Store the label, counts, ratios, method, and review status.

## 9. Marker detection

### 9.1 Canonical markers

  Canonical marker   Section state
  ------------------ ---------------
  `PHIÊN ÂM:`        `phien_am`
  `DỊCH NGHĨA:`      `dich_nghia`
  `DỊCH THƠ:`        `dich_tho`
  `KHẢO DỊ:`         `khao_di`
  `CHÚ THÍCH:`       `chu_thich`
  `NGUYÊN CHÚ:`      `nguyen_chu`

The marker list must be configurable because books may use different
conventions.

### 9.2 Recognition

Use two steps:

1.  Detect marker candidates using heading-like features: short line,
    uppercase text, colon suffix, and/or configured aliases.
2.  Canonicalize using exact aliases first, then constrained fuzzy
    matching.

Do not assume every uppercase line ending in `:` is a known marker.
Preserve unknown headings and put them in review or `other_heading`.

### 9.3 Marker behavior

-   `PHIÊN ÂM:` starts `phien_am`.
-   `DỊCH NGHĨA:` starts `dich_nghia`.
-   `DỊCH THƠ:` starts `dich_tho`.
-   `KHẢO DỊ:`, `CHÚ THÍCH:`, and `NGUYÊN CHÚ:` start auxiliary sections
    and close the active main section.
-   Auxiliary content must not be appended to the preceding main
    section.
-   Define explicit return behavior after an auxiliary section. Do not
    blindly resume the previous section without marker/script/context
    evidence. Ambiguous transitions require review.
-   Store marker lines separately in `section_headings.xml`, preserving
    source line IDs and original text.

## 10. Document boundary detection

Use the ordered, cleaned stream. Script transitions are a useful signal
but must not be the only signal.

### 10.1 Main transition rule

When a CJK-dominant line appears after the stream has entered a
Vietnamese section (`phien_am`, `dich_nghia`, or `dich_tho`), create a
**candidate** new Hán work/document boundary.

This is not automatically a confirmed boundary.

### 10.2 Additional evidence

Boundary confidence is stronger when accompanied by:

-   A poem/work heading such as `其一`, `其二`.
-   A new numbering sequence or title pattern.
-   A Vietnamese title immediately after `PHIÊN ÂM:`,
    e.g. `LẠNG SƠN ĐẠO TRUNG (NHỊ THỦ)`.
-   A clear page/layout break or large vertical gap, if geometry is
    available.
-   Evidence that the previous work completed its expected sections.
-   A following coherent Hán block and subsequent section markers.

A Vietnamese title after `PHIÊN ÂM:` should be captured as a title
candidate, not automatically treated as transliteration content.

### 10.3 Same-script boundaries and sub-works

Adjacent Hán blocks may represent different works even without an
intervening Vietnamese block. Detect these using headings, numbering,
and context.

`KỲ NHẤT`, `KỲ NHỊ`, `其一`, and `其二` may be sub-works within a larger
work. Preserve this distinction. Do not automatically split each into a
top-level document without configured rules or review.

### 10.4 Candidate record

For every proposed boundary, record:

-   boundary ID
-   preceding and following line IDs
-   title candidate, if any
-   evidence/reasons (e.g. `script_transition_vie_to_han`,
    `numbered_heading`, `title_pattern`)
-   confidence category/score only if a defined scoring method exists
-   status: `pending_review`, `accepted`, `rejected`, or `auto_accepted`
-   decision source: rule, config, or human reviewer

Ambiguous boundaries must enter a review queue. Never drop lines between
documents.

## 11. Sequential section segmentation

Run the state machine independently within each confirmed document.

### 11.1 Initial state

Default to `han`, unless work-specific configuration says otherwise.

### 11.2 Transitions

-   `PHIÊN ÂM:` -\> `phien_am`
-   `DỊCH NGHĨA:` -\> `dich_nghia`
-   `DỊCH THƠ:` -\> `dich_tho`
-   Auxiliary markers -\> their auxiliary state
-   A CJK-dominant line after a Vietnamese section is handled by
    Document Boundary Detection before layer assignment. Do not let the
    layer state machine independently create conflicting document
    boundaries.
-   Ordinary lines are assigned to the active state, with script
    mismatch/ambiguity flags as appropriate.

### 11.3 Title extraction

The first line after `PHIÊN ÂM:` may be a Vietnamese title, for example
`LẠNG SƠN ĐẠO TRUNG (NHỊ THỦ)`. Detect title candidates using
configurable patterns and context. Do not blindly treat every first line
after the marker as a title.

### 11.4 Unexpected and mixed lines

-   CJK-dominant lines in the Hán section are assigned to `han`.
-   Vietnamese lines inside the Hán section are retained and flagged;
    they may be titles, annotations, OCR mixing, or genuine content.
-   `mixed` and `unknown` lines are never forced into a section without
    evidence.
-   Keep script classification separate from semantic section
    assignment.

## 12. Output data model

Each document should contain at least:

``` json
{
  "document_id": "CPO_19_DOC_001",
  "title": "LẠNG SƠN ĐẠO TRUNG (NHỊ THỦ)",
  "subworks": [],
  "sections": {
    "han": [],
    "phien_am": [],
    "dich_nghia": [],
    "dich_tho": [],
    "khao_di": [],
    "chu_thich": [],
    "nguyen_chu": [],
    "other": []
  },
  "source_line_ids": [],
  "review_status": "pending_review"
}
```

Each derived line should retain fields equivalent to:

``` json
{
  "line_id": "P0015.L006",
  "page": 15,
  "order": 6,
  "original_text": "original OCR text",
  "corrected_text": "normalized/corrected text",
  "script": {
    "label": "han",
    "cjk_count": 8,
    "latin_count": 0,
    "cjk_ratio": 1.0,
    "latin_ratio": 0.0,
    "method": "unicode_script_ratio"
  },
  "section": "han",
  "document_id": "CPO_19_DOC_001",
  "bbox": [120, 240, 850, 290],
  "ocr_confidence": null,
  "correction_status": "unchanged",
  "review_status": "not_required"
}
```

These are conceptual schemas. Reuse the repository's XML/data model if
it can represent the same information without destructive migration.

## 13. Suggested output artifacts

``` text
04_segmentation/
├── preprocessing/
│   ├── preprocessed.xml
│   ├── cleaning_log.json
│   └── correction_log.json
├── classification/
│   └── line_classification.xml
├── boundary_detection/
│   ├── boundary_candidates.xml
│   └── boundary_review.json
├── documents.xml
├── layers/
│   ├── han.xml
│   ├── am.xml
│   ├── nghia.xml
│   └── tho.xml
├── section_headings.xml
└── review/
    └── segmentation_review.json
```

If repository conventions place cleaning outputs under `05_cleaning/`,
preserve consistency. Logical separation and provenance matter more than
the exact directory name.

All page-specific files must use four-digit zero-padded page numbers
(`0001`, `0015`, `0206`, `9999`). Do not introduce unpadded names or
`page_0015` variants.

## 14. Validation

### 14.1 Line accounting

Every input OCR line must be accounted for as exactly one of:

-   assigned to a document and section;
-   excluded as header/footer/page number/noise with an explicit
    cleaning record;
-   assigned to preface/auxiliary content;
-   placed in a review queue;
-   explicitly excluded with a reason.

No silent line loss.

Report counts for raw lines, empty/noise lines, corrected lines,
headers/footers, page-number candidates, script classes, boundary
candidates by status, lines per document/section, and unassigned lines.

### 14.2 Section completeness

For each document:

-   Check expected sections.
-   Warn when a marker exists but its section is empty.
-   Warn when section script distribution is unexpected.
-   Warn when Hán is absent but Vietnamese sections exist.
-   Do not require `dich_tho` for works that have no verse translation.

### 14.3 Hán/transliteration consistency

Compare Hán and phiên âm line counts as a diagnostic, not a hard
equality constraint. Titles, footnotes, line wrapping, OCR omissions,
and editorial formatting can legitimately cause different counts. Large
differences should produce warnings with affected line IDs.

### 14.4 Marker validation

Report recognized markers, corrected aliases, unknown uppercase-colon
headings, fuzzy candidates needing review, marker-order anomalies, and
missing/repeated markers.

### 14.5 OCR confidence

If confidence is present, flag lines below a configurable `low_conf`
threshold. Preserve the source scale. Do not assume every OCR line has
confidence.

### 14.6 Diagnose the existing CPO_19 failure

A previous run reported zero cleaned Hán lines while Vietnamese sections
contained many lines. Explicitly trace representative lines through:

``` text
raw.xml
 -> preprocessed.xml
 -> script classification
 -> boundary detection
 -> document assignment
 -> section assignment
 -> structural cleaning
 -> sentence segmentation
```

Do not assume the cause before inspecting actual code and artifacts.

## 15. Configuration

Work-specific aliases, headers, thresholds, and boundary rules must be
configurable.

Illustrative configuration:

``` json
{
  "segmentation": {
    "script_classification": {
      "han_cjk_ratio_min": 0.50,
      "vie_latin_ratio_min": 0.70,
      "min_script_characters": 2
    },
    "markers": {
      "PHIEN_AM": ["PHIÊN ÂM:", "PHIỀN ÂM:", "PHIEN AM:"],
      "DICH_NGHIA": ["DỊCH NGHĨA:", "DICH NGHIA:"],
      "DICH_THO": ["DỊCH THƠ:", "DỊCH THO:"],
      "KHAO_DI": ["KHẢO DỊ:", "KHÁO DỊ:"],
      "CHU_THICH": ["CHÚ THÍCH:"],
      "NGUYEN_CHU": ["NGUYÊN CHÚ:"]
    },
    "document_boundary": {
      "split_on_vie_to_han_after_vietnamese_section": true,
      "require_heading_evidence_for_auto_accept": true,
      "review_ambiguous_boundaries": true
    },
    "cleaning": {
      "running_headers": [
        "HOÀNG HOA ĐỒ PHẢ",
        "NGÔ THÌ NHẬM TOÀN TẬP"
      ],
      "remove_numeric_line_only_when_confirmed_page_number": true
    }
  }
}
```

Adapt to the repository's actual config schema. Do not duplicate
existing settings or hardcode CPO_19-specific patterns into generic
algorithms.

## 16. Implementation workflow and constraints

Before editing:

1.  Inspect repository structure and current pipeline entry points.
2.  Inspect actual CPO_19 `raw.xml`, current segmentation outputs,
    review files, cleaning outputs, and config.
3.  Trace current behavior and identify why Hán lines are lost or
    misassigned.
4.  Map responsibilities to existing modules; reuse code where
    appropriate.
5.  Record the implementation plan and any genuine blockers.

Then implement. Do not stop after a plan unless a blocking ambiguity
requires user input.

Constraints:

-   Do not rerun Qwen OCR by default.
-   Do not overwrite raw JSON or `raw.xml`.
-   Do not delete legacy OCR data.
-   Do not disable validation to make the pipeline pass.
-   Do not silently drop unknown/ambiguous lines.
-   Do not auto-correct arbitrary Hán/Vietnamese content.
-   Do not treat every CJK line after Vietnamese as a definite new work.
-   Do not treat every numbered sub-work as a top-level document.
-   Preserve backward compatibility where practical and document
    required migration.

## 17. Testing

### 17.1 Unit tests

Test Unicode normalization, CJK/Latin counting, all script classes,
exact marker aliases, fuzzy marker candidate detection, false-positive
rejection, header/footer detection, page-number exceptions, boundary
candidates, same-script boundaries, title extraction, all state
transitions, and provenance after
correction/filtering/splitting/merging.

### 17.2 Integration fixtures

Include fixtures for:

1.  Complete Hán -\> Phiên âm -\> Dịch nghĩa -\> Dịch thơ sequence.
2.  A work spanning multiple pages.
3.  New Hán work after a Vietnamese section.
4.  Adjacent Hán works without a Vietnamese block between them.
5.  Title immediately after `PHIÊN ÂM:`.
6.  `KỲ NHẤT`, `KỲ NHỊ`, `其一`, `其二`.
7.  Misspelled markers such as `PHIỀN ÂM:` and `DỊCH THO:`.
8.  `KHẢO DỊ:`, `CHÚ THÍCH:`, `NGUYÊN CHÚ:`.
9.  Running headers and page numbers interleaved with content.
10. Mixed-script and low-confidence lines.
11. Missing markers and incomplete sections.
12. Repeated text that is legitimate and must not be deduplicated.

### 17.3 Regression and end-to-end

-   Run all existing tests.
-   Run CPO_19 using existing OCR artifacts, without forcing OCR.
-   Compare before/after line accounting.
-   Verify Hán lines appear in the correct outputs when supported by the
    source.
-   Inspect representative pages/documents manually.
-   Verify source line IDs survive every stage.
-   Verify deterministic reruns from the same raw OCR and config.

## 18. Acceptance criteria

Complete only when:

-   Raw OCR artifacts remain unchanged.
-   Marker variants are normalized or queued before state-machine
    segmentation.
-   Every line has script classification or an explicit review status.
-   Boundary detection combines script transitions with heading/context
    evidence.
-   Multiple pages are processed as one ordered stream.
-   Hán, phiên âm, dịch nghĩa, dịch thơ, and auxiliary sections are
    assigned without silent line loss.
-   Titles and sub-work labels are preserved distinctly.
-   Every output line traces back to source OCR IDs.
-   Uncertain corrections and boundaries are reviewable.
-   Line accounting reconciles exactly.
-   Existing tests pass or regressions are resolved/documented.
-   CPO_19 report includes counts, warnings, unresolved review items,
    and representative examples.
-   Documentation describes marker aliases, thresholds, and
    work-specific patterns.

## 19. Required final report from the coding agent

Provide:

1.  Root causes found in the existing implementation.
2.  Changed files/modules and their responsibilities.
3.  Updated pipeline diagram.
4.  Configuration changes.
5.  Test commands and exact pass/fail counts.
6.  CPO_19 before/after line and section statistics.
7.  Examples of corrected markers and detected boundaries.
8.  Remaining review items and limitations.
9.  Confirmation raw OCR artifacts were not modified.
10. Confirmation no full OCR rerun was performed unless explicitly
    requested.

------------------------------------------------------------------------

**End of specification.**
