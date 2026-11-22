# OCR Normalization Quality Review Report

**Work ID**: `CPO_18`  
**Target Pages**: `205`, `206`  
**Writing Systems**: `chi_tra` (Sino-Nom), `vie` (Vietnamese Latin)  
**Engines Reviewed**: `kimhannom`, `qwen`, `tesseract`  
**Status**: Completed — Normalization Layer Validation & Architectural Review  
**Date**: 2026-11-22  

---

## 1. Executive Summary

Following the live execution of `KimHanNomEngine` and output normalization across `CPO_18` (pages 205 and 206), a comprehensive quality review was conducted across all four normalized datasets:
1. `data/CPO_18/03_ocr/normalized/chi_tra/page_0205.json`
2. `data/CPO_18/03_ocr/normalized/chi_tra/page_0206.json`
3. `data/CPO_18/03_ocr/normalized/vie/page_0205.json`
4. `data/CPO_18/03_ocr/normalized/vie/page_0206.json`

The three active OCR engines reflect fundamentally distinct recognition paradigms, segmentation granularities, and coordinate models:

```
[Original Page Image (2480 x 3509)]
       │
       ├─► KimHanNom: Glyph / Character Level (80-140 small boxes, vertical column order)
       ├─► Qwen:      Paragraph / Block Level (14-18 wide horizontal bands, bilingual transcription)
       └─► Tesseract: Printed Line Level (25-28 standard line boxes, script-specific models)
```

This review establishes the spatial, structural, and lexical characteristics of each engine's normalized output to provide a sound empirical foundation for downstream line alignment and Weighted Voting.

---

## 2. Comparative Engine Metrics Summary

| Metric | KimHanNom (`kimhannom`) | Qwen (`qwen`) | Tesseract (`tesseract`) |
| :--- | :--- | :--- | :--- |
| **Segmentation Unit** | **Glyph / Character Token** | **Paragraph / Synthetic Block** | **Physical Printed Line** |
| **Line Count (p205 / p206)** | 122 / 141 lines | 14 / 18 lines | 25 / 28 lines |
| **Average Bounding Box (W × H)** | ~89–117 px × ~82–126 px | ~1960 px × ~156–204 px | ~1350–1394 px × ~88–93 px |
| **Average Page Area per Box** | ~0.11% – 0.14% | ~3.67% – 4.78% | ~1.48% – 1.55% |
| **Max Page Area of Single Box** | 1.11% | **15.13%** (covers multi-line block) | 5.29% |
| **Reading Order Orientation** | **Vertical Column (Right-to-Left)** | **Horizontal Line (Top-to-Bottom)** | **Horizontal Line (Top-to-Bottom)** |
| **`chi_tra` vs `vie` Output** | **Identical** (Sino-Nom only) | **Identical** (Run mode `all`) | **Genuinely Distinct** (`chi_tra` vs `vie` models) |
| **Confidence Handling** | `null` (strictly unfabricated) | Calibrated `0–100` float (avg ~95%) | Calibrated `0–100` float (avg ~55–85%) |
| **Single-Character Lines** | **66.4% – 83.0%** | 0.0% | 0.0% |
| **Placeholder / Noise Tokens** | High (`口`, `丶`, `吕`, `当`) | Minimal | Language-model mismatch noise |

---

## 3. In-Depth Quality Analysis

### 3.1 Bounding-Box Validity and Spatial Distribution
1. **Coordinate Validity**:
   - 100% of bounding boxes across all three engines are strictly within original image boundaries: `0 <= x0 < x1 <= 2480` and `0 <= y0 < y1 <= 3509`.
   - No negative coordinates, no inverted dimensions (`x0 >= x1` or `y0 >= y1`), and zero `NaN` values.
2. **Spatial Extent**:
   - All three engines successfully capture the active text body region between `y = 280` and `y = 3264`, properly respecting the vertical crop region `[0.0, 0.08, 1.0, 0.93]`.
   - **KimHanNom** bounding boxes are concentrated within the Sino-Nom column block (`x = 261` to `x = 2205`). It does not detect horizontal Vietnamese Latin preface lines.
   - **Qwen** bounding boxes span almost the entire horizontal width (`x = 0` to `x = 2480`), creating wide horizontal bands regardless of column margins.
   - **Tesseract** bounding boxes conform closely to printed margins (`x = 77` to `x = 2476` for body lines; `x = 414` for indented preface lines).

### 3.2 Reading Order Consistency
- **Qwen & Tesseract (Horizontal Top-to-Bottom)**:
  - Both engines exhibit **100% strict monotonic downward vertical progression** ($dy > 0$, 0 upward transitions).
  - Lines follow visual Western reading order: Preface (Vietnamese) $\rightarrow$ Heading $\rightarrow$ Text Body $\rightarrow$ Footnotes.
- **KimHanNom (Vertical Column Right-to-Left with Jitter)**:
  - KimHanNom detects text arranged in traditional Sino-Nom vertical columns. The primary progression moves downward within columns ($dy > 0$, 93 transitions on p205) and right-to-left across columns ($dx < 0$, 70 transitions).
  - **Defect Identified (Reading Order Jitter)**: On page 205, there are 28 upward vertical jumps ($dy < 0$) and 46 rightward steps ($dx > 0$). For example:
    - `L001`: `[2104, 1020, 2205, 1253]` (`y = 1020`)
    - `L002`: `[2114, 2821, 2186, 2891]` (`y = 2821` — bottom of column 1)
    - `L003`: `[2112, 2751, 2143, 2793]` (`y = 2751` — jump *upward* in same column!)
    - `L004`: `[1999, 1017, 2110, 1255]` (`x = 1999` — shift left to column 2)
  - *Root Cause*: The cloud API returns detection polygons in the order they were segmented by the detector, without a post-detection topological sort into canonical vertical reading order.

### 3.3 Overlapping or Unusually Large Bounding Boxes
- **Pairwise Overlaps**: Zero significant intra-engine line overlaps (IoU > 0.3 is 0 across all pages). Bounding boxes within each engine partition the space cleanly without duplicate detection.
- **Unusually Large Bounding Boxes (Qwen)**:
  - Qwen line `qwen.L004` on page 205 covers `[272, 887, 2200, 1368]`, spanning **10.66%** of the page area (width 1928 px, height 481 px).
  - Qwen line `qwen.L005` on page 206 covers `15.13%` of the page area.
  - *Nature*: This is not a detector defect; Qwen's VLM adapter groups an entire multi-line block into a single text output with a composite bounding box.

### 3.4 Differences in Line Segmentation Granularity
The three engines exhibit a 10:1 granularity disparity:
1. **KimHanNom (Character Level)**:
   - Average width: ~100 px (~1 character).
   - 81 of 122 lines (66.4%) on p205 and 117 of 141 lines (83.0%) on p206 are single characters (`'吕'`, `'一'`, `'洲'`, `'上'`).
   - KimHanNom decomposes multi-character columns into discrete glyph bounding boxes.
2. **Qwen (Block Level)**:
   - 14 lines on p205, 18 lines on p206.
   - Combines multiple printed lines into a single synthetic transcription band.
3. **Tesseract (Physical Line Level)**:
   - 25 lines on p205, 28 lines on p206.
   - Accurately tracks physical typographic line breaks of the printed book.

### 3.5 Empty or Suspicious OCR Lines
1. **KimHanNom CJK Noise / Placeholder Characters**:
   - Page 205 contains 22 lines, and Page 206 contains **57 lines** with CJK placeholder / surrogate tokens: `'口'`, `'丶'`, `'吕'`, `'当'`.
   - *Cause*: In weathered or low-contrast Sino-Nom woodblock/lithograph impressions, the character classifier outputs generic radical proxies (e.g. `'口'` for unresolvable enclosed components).
2. **Tesseract Language Model Mismatch in `chi_tra`**:
   - In `chi_tra/page_0205.json`, Tesseract's `chi_tra.traineddata` attempts to parse the Vietnamese Latin preface:
     - Output: `'3au day la toan van S/w*/ Ze 仍 / e6 kem theo phgn'`
     - Actual text: `'Sau đây là toàn văn Sáu bức thư, có kèm theo phần'`
   - *Cause*: A CJK language model forced to decode Latin typography creates severe character substitution errors.
3. **Punctuation-Only or Empty Lines**:
   - Zero empty lines (`len == 0`) across all normalized outputs (empty lines are filtered during normalization).
   - Zero punctuation-only lines.

### 3.6 Are `chi_tra` and `vie` Outputs Distinct or Identical?
An architectural finding from this review:

| Engine | `chi_tra` vs `vie` Output Comparison | Structural Cause |
| :--- | :--- | :--- |
| **`kimhannom`** | **100% Identical** (`lines_ct == lines_vi`) | The KimHanNom cloud API is strictly a Sino-Nom recognition model (`lang_type=1`). It has no Vietnamese Latin mode. |
| **`qwen`** | **100% Identical** (`lines_ct == lines_vi`) | Qwen was executed in full-page transcription mode (`mode: all`). The exact same run was selected for both `chi_tra` and `vie` manifests. |
| **`tesseract`** | **Genuinely Distinct** | Tesseract was run with distinct trained models: `chi_tra.traineddata` for `chi_tra` and `vie.traineddata` for `vie`. |

---

## 4. Distinguishing Actual Defects vs Engine-Specific Segmentation

To maintain architectural integrity, the review strictly distinguishes actual defects from expected engine characteristics:

### A. Engine-Specific Segmentation Differences (Expected / Valid)
1. **Granularity Disparity**:
   - KimHanNom outputting glyph boxes vs Qwen outputting paragraph blocks vs Tesseract outputting printed lines is **not a bug**. Each engine's native model operates at that specific level.
2. **Missing Confidence in KimHanNom**:
   - Preserving `confidence: null` for KimHanNom is correct. Fabricating a synthetic score would violate the provenance contract.
3. **Qwen Wide Horizontal Bounding Boxes**:
   - Qwen's bounding boxes represent attention-receptive fields of generated paragraph spans, not tight word-level contours.

### B. Actual Defects (To Be Handled Downstream)
1. **KimHanNom Intra-Column Ordering Jitter**:
   - Non-monotonic vertical steps within the same column break sequential reading order.
2. **KimHanNom CJK Noise Tokens**:
   - Isolated glyph detections of `'口'`, `'丶'`, `'吕'` represent low-confidence character classification failures.
3. **Tesseract Script Mismatch in `chi_tra`**:
   - Running Tesseract Chinese OCR on the Latin preface yields severe garbling.
4. **Redundant Duplicate Runs across `chi_tra` and `vie` for KimHanNom and Qwen**:
   - Storing identical Qwen and KimHanNom outputs in both writing systems duplicates storage without script separation.

---

## 5. Visual Review Artifacts

All visualization overlays and comparison panels were generated by `src/ocr/normalization/visualizer.py` and saved under `reports/ocr_normalization_review/`:

| Page | Dataset | Single-Engine Overlays | Combined Overlay | Side-by-Side Comparison Panel |
| :--- | :--- | :--- | :--- | :--- |
| **205** | `chi_tra` | [KimHanNom](file:///E:/Projects/han-viet-corpus/reports/ocr_normalization_review/CPO_18_chi_tra_p0205_kimhannom.png)<br>[Qwen](file:///E:/Projects/han-viet-corpus/reports/ocr_normalization_review/CPO_18_chi_tra_p0205_qwen.png)<br>[Tesseract](file:///E:/Projects/han-viet-corpus/reports/ocr_normalization_review/CPO_18_chi_tra_p0205_tesseract.png) | [Overlay All](file:///E:/Projects/han-viet-corpus/reports/ocr_normalization_review/CPO_18_chi_tra_p0205_overlay_all.png) | [Side-by-Side Panel](file:///E:/Projects/han-viet-corpus/reports/ocr_normalization_review/CPO_18_chi_tra_p0205_side_by_side.png) |
| **206** | `chi_tra` | [KimHanNom](file:///E:/Projects/han-viet-corpus/reports/ocr_normalization_review/CPO_18_chi_tra_p0206_kimhannom.png)<br>[Qwen](file:///E:/Projects/han-viet-corpus/reports/ocr_normalization_review/CPO_18_chi_tra_p0206_qwen.png)<br>[Tesseract](file:///E:/Projects/han-viet-corpus/reports/ocr_normalization_review/CPO_18_chi_tra_p0206_tesseract.png) | [Overlay All](file:///E:/Projects/han-viet-corpus/reports/ocr_normalization_review/CPO_18_chi_tra_p0206_overlay_all.png) | [Side-by-Side Panel](file:///E:/Projects/han-viet-corpus/reports/ocr_normalization_review/CPO_18_chi_tra_p0206_side_by_side.png) |
| **205** | `vie` | [KimHanNom](file:///E:/Projects/han-viet-corpus/reports/ocr_normalization_review/CPO_18_vie_p0205_kimhannom.png)<br>[Qwen](file:///E:/Projects/han-viet-corpus/reports/ocr_normalization_review/CPO_18_vie_p0205_qwen.png)<br>[Tesseract](file:///E:/Projects/han-viet-corpus/reports/ocr_normalization_review/CPO_18_vie_p0205_tesseract.png) | [Overlay All](file:///E:/Projects/han-viet-corpus/reports/ocr_normalization_review/CPO_18_vie_p0205_overlay_all.png) | [Side-by-Side Panel](file:///E:/Projects/han-viet-corpus/reports/ocr_normalization_review/CPO_18_vie_p0205_side_by_side.png) |
| **206** | `vie` | [KimHanNom](file:///E:/Projects/han-viet-corpus/reports/ocr_normalization_review/CPO_18_vie_p0206_kimhannom.png)<br>[Qwen](file:///E:/Projects/han-viet-corpus/reports/ocr_normalization_review/CPO_18_vie_p0206_qwen.png)<br>[Tesseract](file:///E:/Projects/han-viet-corpus/reports/ocr_normalization_review/CPO_18_vie_p0206_tesseract.png) | [Overlay All](file:///E:/Projects/han-viet-corpus/reports/ocr_normalization_review/CPO_18_vie_p0206_overlay_all.png) | [Side-by-Side Panel](file:///E:/Projects/han-viet-corpus/reports/ocr_normalization_review/CPO_18_vie_p0206_side_by_side.png) |

---

## 6. Actionable Recommendations for Weighted Voting & Next Pipeline Stages

### 6.1 Do Not Use Direct Bounding-Box IoU for Multi-Engine Line Matching
Because KimHanNom boxes are ~100px square glyphs and Qwen boxes are ~2000px wide paragraph bands, direct intersection-over-union (IoU) between engines will be near zero ($IoU < 0.05$).
- **Recommendation**: In Weighted Voting, use **Spatial Containment & Intersection Projections** (e.g. checking whether a KimHanNom glyph center falls inside a Qwen/Tesseract line band) combined with **Text Sequence Alignment** (Needleman-Wunsch / edit-distance alignment) rather than direct bounding box IoU.

### 6.2 Implement Script-Aware Engine Routing
The bilingual nature of CPO_18 (Vietnamese Latin introduction + Sino-Nom woodblock columns) requires script routing:
- **Latin Sections (Vietnamese preface/notes)**:
  - Weight `tesseract` (`vie`) and `qwen` heavily.
  - Zero-weight or exclude `kimhannom` (which detects noise) and `tesseract` (`chi_tra`) (which produces garbled Latin).
- **Sino-Nom Sections (Chinese character columns)**:
  - Weight `kimhannom` and `qwen` heavily.
  - Zero-weight `tesseract` (`vie`) on Chinese characters.

### 6.3 Topological Reading-Order Sorting for KimHanNom
Before passing KimHanNom lines to sequence alignment or voting:
- Implement a 2D column clustering step:
  1. Cluster glyph boxes into vertical columns by $x$-coordinate.
  2. Order columns from right to left ($x$ descending).
  3. Sort glyphs within each column strictly from top to bottom ($y$ ascending).
- This will eliminate the 28–31 upward jitter transitions identified in this audit.

---

*Report prepared by Antigravity Review Framework — han-viet-corpus.*
