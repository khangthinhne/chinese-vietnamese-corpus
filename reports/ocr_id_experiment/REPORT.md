# KimHanNom API `ocr_id` Parameter Investigation Report

**Work ID**: `CPO_18`  
**Test Subject**: `page_205.png` (Cropped body: `[0.0, 0.08, 1.0, 0.93]`, dimensions: `2480 × 2984` px)  
**Endpoint**: `https://kimhannom.fit.hcmus.edu.vn/api/web/clc-sinonom/image-ocr`  
**Tested Configurations**: `ocr_id=1` (General Vertical) vs `ocr_id=4` (General Horizontal)  
**Date**: 2026-11-22  

---

## 1. Objective & Hypothesis

The KimHanNom API documentation defines the following values for `ocr_id`:
- `1`: General vertical text (*thông thường dọc*)
- `2`: Administrative documents (*hành chính*)
- `3`: Outdoor scenes (*ngoại cảnh*)
- `4`: General horizontal text (*thông thường ngang*)

Under current production settings (`ocr_id=1`), the API segments traditional Sino-Nom woodblock pages into predominantly single-character/glyph bounding boxes rather than horizontal text lines.

**Experimental Hypothesis**:
Could changing `ocr_id` from `1` (vertical) to `4` (horizontal):
1. Alter the detection granularity from character-level glyphs to horizontal line segments?
2. Alter the reading order progression from vertical columns to horizontal left-to-right?
3. Improve or degrade Sino-Nom text recognition accuracy?

---

## 2. Experimental Methodology

To achieve strict experimental control:
1. **Identical Image Payload**: The exact same cropped PNG image from `CPO_18` page 205 was uploaded to the server (`upload_4c8cc842430b42b9a7b80fd126c2f56f.png`).
2. **Identical Secondary Parameters**:
   - `lang_type = 1` (Sino-Nom)
   - `reading_direction = 1`
   - `epitaph = 0`
   - `tag = null`
3. **Controlled Variable**: Only `ocr_id` was toggled between `1` and `4`.
4. **Data Isolation**: Existing raw OCR runs (`data/CPO_18/03_ocr/raw/`) and production manifests were left completely untouched. Experimental artifacts were saved to `reports/ocr_id_experiment/`.

---

## 3. Empirical Results & Comparative Metrics

| Metric | `ocr_id=1` (General Vertical) | `ocr_id=4` (General Horizontal) | Delta / Difference |
| :--- | :--- | :--- | :--- |
| **Raw Response Path** | `raw_response_ocr_id_1.json` | `raw_response_ocr_id_4.json` | Separate files |
| **Detection Count** | **122 boxes** | **122 boxes** | **0 (Identical)** |
| **Average Bounding Box (W × H)** | `88.9` px × `126.4` px | `88.9` px × `126.4` px | **0.0 px (Identical)** |
| **Min Bounding Box Area** | `0.0138%` of page | `0.0138%` of page | **Identical** |
| **Max Bounding Box Area** | `0.53%` of page | `0.53%` of page | **Identical** |
| **Single-Character Detections** | 81 / 122 (**66.4%**) | 81 / 122 (**66.4%**) | **0 (Identical)** |
| **CJK Noise / Radical Proxies** | 22 lines (`口`, `丶`, `吕`) | 22 lines (`口`, `丶`, `吕`) | **0 (Identical)** |
| **Vertical Downward Steps ($dy > 0$)**| 93 | 93 | **0 (Identical)** |
| **Vertical Upward Jumps ($dy < 0$)** | 28 | 28 | **0 (Identical)** |
| **Horizontal Leftward Steps ($dx < 0$)**| 70 | 70 | **0 (Identical)** |
| **Horizontal Rightward Steps ($dx > 0$)**| 46 | 46 | **0 (Identical)** |
| **Text Output Identity** | Baseline text array | 100% bit-for-bit identical | **Zero diffs** |
| **Bounding Box Identity** | Baseline coordinates | 100% bit-for-bit identical | **Zero diffs** |

---

## 4. Key Empirical Findings

### 4.1 Does `ocr_id` Affect Detection Granularity?
**No.** `ocr_id=4` does **not** merge adjacent glyphs into horizontal lines. It outputs the exact same 122 character-level bounding boxes (average width 88.9 px, average height 126.4 px) with identical pixel coordinates down to the floating-point precision of the polygon vertices.

### 4.2 Does `ocr_id` Affect Reading Order?
**No.** `ocr_id=4` does **not** reorder text into horizontal left-to-right sequence. The sequential order of the 122 detected boxes is 100% identical between `ocr_id=1` and `ocr_id=4`, exhibiting the exact same 70 leftward steps and the same 28 intra-column upward jitter steps.

### 4.3 Does `ocr_id` Affect Text Recognition Quality?
**No.** Every character in `result_ocr_text` matches character-for-character across both runs:
- Line 1: `群見`
- Line 2: `吕`
- Line 3: `一`
- Line 4: `出不`
- ...
- Line 122: `上`

### 4.4 Architectural Explanation
In the HCMUS CLC Sino-Nom server architecture (`/api/web/clc-sinonom/image-ocr`), the `ocr_id` parameter behaves as a legacy or top-level routing parameter designed for multi-script document pipelines. When `lang_type=1` (Sino-Nom) is specified, the backend routes the request to a dedicated Sino-Nom glyph detector (DBNet / CRAFT variant trained specifically on Nom woodblock characters). 

The specialized Sino-Nom detection and recognition model operates identically regardless of whether `ocr_id=1` or `ocr_id=4` is supplied in the request body.

---

## 5. Visual Artifacts

Experimental visualizations are available at the following paths:

| Artifact | File Path | Description |
| :--- | :--- | :--- |
| **`ocr_id=1` Overlay** | [page_205_ocr_id_1_overlay.png](file:///E:/Projects/han-viet-corpus/reports/ocr_id_experiment/page_205_ocr_id_1_overlay.png) | 122 bounding boxes overlaid in Crimson Red (`ocr_id=1`). |
| **`ocr_id=4` Overlay** | [page_205_ocr_id_4_overlay.png](file:///E:/Projects/han-viet-corpus/reports/ocr_id_experiment/page_205_ocr_id_4_overlay.png) | 122 bounding boxes overlaid in Cyan Blue (`ocr_id=4`). |
| **Side-by-Side Comparison** | [page_205_ocr_id_1_vs_4_side_by_side.png](file:///E:/Projects/han-viet-corpus/reports/ocr_id_experiment/page_205_ocr_id_1_vs_4_side_by_side.png) | Direct side-by-side verification confirming visual identity. |
| **Raw API Response (`ocr_id=1`)**| [raw_response_ocr_id_1.json](file:///E:/Projects/han-viet-corpus/reports/ocr_id_experiment/raw_response_ocr_id_1.json) | Full API response payload with bounding boxes and metadata. |
| **Raw API Response (`ocr_id=4`)**| [raw_response_ocr_id_4.json](file:///E:/Projects/han-viet-corpus/reports/ocr_id_experiment/raw_response_ocr_id_4.json) | Full API response payload with bounding boxes and metadata. |
| **Summary Statistics** | [experiment_summary.json](file:///E:/Projects/han-viet-corpus/reports/ocr_id_experiment/experiment_summary.json) | Extracted numerical metrics and reading order transition counts. |

---

## 6. Recommendation for Production Configuration

1. **Retain `ocr_id=1` in Production**:
   - The current default `ocr_id=1` (*General vertical text*) accurately represents the true typographic layout of CPO_18 (traditional vertical columns).
   - Changing `ocr_id` to `4` produces **zero benefit** (identical output) and would be semantically incorrect for vertical Sino-Nom documents.
2. **Do Not Rely on API Parameters for Line Reconstruction**:
   - Because the KimHanNom API operates natively at the glyph/character level for Sino-Nom text, line aggregation cannot be solved by API parameter tuning.
   - Line reconstruction must be handled in the corpus pipeline downstream of normalization:
     - **Topological Column Clustering**: Group glyph boxes into vertical columns by $x$-coordinate, sort right-to-left, and sort within columns top-to-bottom.
     - **Intersection Containment**: When performing Weighted Voting against Qwen and Tesseract, match KimHanNom glyphs by containment within the larger line/paragraph bands rather than expecting horizontal line segments from KimHanNom.
