# OCR Normalization Layer — Progress Checkpoint

**Date:** 2026-11-22  
**Branch:** `fead/add-api-kim-han-nom`  
**Latest Commit:** `8762f9c remove large-size files`  
**Status:** In Progress — Ready for Resumption  

---

## 1. Task Objective

Implement a Unified OCR Output Normalization Layer that standardizes the raw outputs of all OCR engines into a single, engine-independent JSON schema (v1.0) prior to Weighted Voting.

### Scope Constraints
- **Scope is strictly Output Normalization**: Text cleaning, line matching, Weighted Voting, and segmentation are deferred to subsequent pipeline stages.
- **Active OCR Engines (Exactly 3)**:
  - `kimhannom`
  - `qwen`
  - `tesseract`
- `PaddleOCR` is **not** an active engine and is excluded from the default registry and normalization workflow.
- **Append-only Raw Storage**: Existing raw OCR runs (`03_ocr/raw/`) and manifests (`03_ocr/manifests/`) remain unmodified.

---

## 2. Git Working Tree State

### 2.1 Branch & Commit
- **Current Branch:** `fead/add-api-kim-han-nom`
- **HEAD Commit:** `8762f9c remove large-size files`

### 2.2 Modified Files
- `.env.example`: Added `KIMHANNOM_TOKEN` placeholder.
- `src/common/corpus.py`: Added properties `ocr_normalized_root` (`03_ocr/normalized`) and `ocr_normalized_path(writing_system, page)`.
- `src/ocr/engines/__init__.py`:
  - Updated `OcrLine` dataclass to allow `bbox: tuple[int, int, int, int] | None`.
  - Registered `kimhannom`, `qwen`, and `tesseract` in `ENGINES`.
- `src/ocr/ocr.py`:
  - Updated `validate_result` and `process_writing_system_page` to safely handle lines where `bbox` is `None`.

### 2.3 New / Untracked Files
- `src/ocr/engines/kimhannom.py`: Added `KimHanNomEngine(OcrEngine)` adapter class using existing `KimHanNomOCR` client.
- `src/ocr/normalization/__init__.py`: Package export file.
- `src/ocr/normalization/schema.py`: Canonical JSON schema v1.0 dataclasses with `EngineStatus` supporting `("success", "empty", "failed", "not_run")`.
- `src/ocr/normalization/validators.py`: Strict schema validator.
- `src/ocr/normalization/normalizer.py`: Core normalization engine, bbox math, confidence scaling, multi-run selection, atomic persistence, and CLI entry point.
- `tests/test_ocr_normalization.py`: Comprehensive test suite (16 tests, 100% pass).
- `data/CPO_18/03_ocr/normalized/`: Real normalized outputs for CPO_18 pages 205–206 (`chi_tra` and `vie`).
- `docs/OCR_NORMALIZATION_CHECKPOINT.md`: This checkpoint documentation.

### 2.4 Deleted Files
- None.

---

## 3. Implementation Summary & Verification Status

### 3.1 What Has Been Implemented
1. **Engine Registry & KimHanNom Adapter**:
   - `KimHanNomEngine` inherits from `OcrEngine`, implements static `describe(lang)` without network calls, implements `recognize(image, ...)` by writing to a temporary file, calls `predict()`, preserves `raw_response`, preserves missing confidence as `None`, preserves missing bbox as `None`, and handles API errors explicitly.
   - `ENGINES` registry in `src/ocr/engines` contains exactly `['kimhannom', 'qwen', 'tesseract']`.
2. **Schema & EngineStatus**:
   - Updated `EngineStatus` to `Literal["success", "empty", "failed", "not_run"]` across `schema.py` and `validators.py`.
3. **Coordinate Handling Contract**:
   - Contract established: Raw store line bboxes (`run["lines"]`) are already in original-page pixel coordinates (as guaranteed by `src/ocr/ocr.py`).
   - `normalize_bbox` does not apply crop offsets to `run["lines"]` (avoids double-shifting).
   - Crop offsets are only added when normalizing crop-relative raw payloads directly.
   - Bboxes are clamped to image boundaries `[0, width]` and `[0, height]`.
   - Missing bboxes are preserved as `None` without fabricating fake boxes.
4. **Image Path Portability**:
   - Created `make_relative_image_path(layout, path)` to ensure only workspace/work_dir-relative paths (e.g. `02_extraction/pages/page_0205.png`) are stored in normalized JSON, preventing local absolute paths (e.g. `E:\Projects\...`) from leaking into data files.
5. **Multi-Run Selection**:
   - Run disambiguation supports explicit run keys and manifest entries.
   - A manifest entry selecting `qwen` does NOT resolve or disambiguate `tesseract` or `kimhannom`.
   - Unresolvable runs with distinct keys raise `AmbiguousRunError` rather than silently picking the latest.
6. **Real CPO_18 Page Generation**:
   - Normalized pages 205 and 206 for `chi_tra` and `vie`.
   - Results accurately reflect all 3 active engines:
     - `qwen`: `status: "success"` (14 lines in page 205 chi_tra)
     - `tesseract`: `status: "success"` (25 lines in page 205 chi_tra)
     - `kimhannom`: `status: "not_run"` (0 lines, unrun on CPO_18, not fabricated)

### 3.2 What Has Actually Been Tested
1. **Unit Test Suite (`tests/test_ocr_normalization.py`)**:
   - 16 tests executed via `pytest`:
     - `test_active_engines_registry`: PASSED
     - `test_kimhannom_engine_adapter_describe_and_recognize`: PASSED
     - `test_kimhannom_engine_handles_errors`: PASSED
     - `test_valid_normalized_page_schema`: PASSED
     - `test_validation_catches_invalid_fields`: PASSED
     - `test_normalize_bbox_full_page`: PASSED
     - `test_normalize_bbox_cropped_with_offsets`: PASSED
     - `test_normalize_bbox_already_page_relative`: PASSED
     - `test_normalize_bbox_missing`: PASSED
     - `test_normalize_bbox_image_boundaries`: PASSED
     - `test_normalize_bbox_polygon`: PASSED
     - `test_normalize_confidence`: PASSED
     - `test_image_path_portability`: PASSED
     - `test_select_run_multiple_qwen_models`: PASSED
     - `test_single_engine_manifest_does_not_select_other_engines`: PASSED
     - `test_normalize_page_and_save`: PASSED
   - **Result:** 16 passed in 1.18s.
2. **Real Data Normalization Run**:
   - Executed CLI on CPO_18 pages 205–206 for both `chi_tra` and `vie`.
   - Output validated: generated JSON files are valid v1.0 schema with all 3 engines represented.

---

## 4. Remaining Issues & Incomplete Tasks

1. **Live KimHanNom API Inference on CPO_18**:
   - While the `KimHanNomEngine` adapter is fully implemented and tested with mock fixtures, a real API inference run on CPO_18 pages 205–206 using a valid `KIMHANNOM_TOKEN` has not yet been executed because it requires a live network connection and remote API quota.
   - Consequently, in the CPO_18 normalized files, KimHanNom is correctly flagged as `not_run`. Once live runs are stored in `03_ocr/raw/`, re-running the normalizer will produce `success` for KimHanNom.
2. **Legacy PaddleOCR Tests in Repository**:
   - `tests/test_paddle_engine.py` contains legacy tests expecting `PaddleOcrEngine` in `src.ocr.engines`. Since PaddleOCR is deactivated per specification, those legacy tests fail if the whole test folder `pytest tests/` is invoked without filtering.
3. **End-to-End Pipeline Integration**:
   - Integrating `src.ocr.normalization` into the overall corpus orchestrator (`src/pipeline.py`) as an automated step between raw OCR and Weighted Voting.

---

## 5. Explicit Next Steps to Resume

When resuming work, proceed with the following actions:

1. **Verify Engine Registry and Import Safety**:
   Ensure `from src.ocr.engines import ENGINES` continues to return only `['kimhannom', 'qwen', 'tesseract']`.
2. **Execute Complete Normalization Test Suite**:
   ```powershell
   python -m pytest tests/test_ocr_normalization.py -p no:cacheprovider -v
   ```
3. **If Valid `KIMHANNOM_TOKEN` Is Available**:
   Run KimHanNom OCR on CPO_18 pages 205–206 via `src.ocr.ocr`:
   ```powershell
   python -m src.ocr.ocr --work-id CPO_18 --writing-system chi_tra --pages 205-206 --engines kimhannom --source data/CPO_18/02_extraction/pages
   ```
   Then re-normalize to capture live KimHanNom lines:
   ```powershell
   python -m src.ocr.normalization.normalizer --work-id CPO_18 --writing-system chi_tra --pages 205-206 --force
   ```
4. **Inspect Generated Normalized JSON**:
   Verify coordinates, bounding boxes, confidence values, and engine statuses in:
   - `data/CPO_18/03_ocr/normalized/chi_tra/page_0205.json`
   - `data/CPO_18/03_ocr/normalized/chi_tra/page_0206.json`
   - `data/CPO_18/03_ocr/normalized/vie/page_0205.json`
   - `data/CPO_18/03_ocr/normalized/vie/page_0206.json`
5. **Proceed to Weighted Voting Design**:
   Only after all normalized outputs are signed off, design the Weighted Voting module to consume the canonical normalized schema.

---

## 6. Exact Commands to Resume

```powershell
# 1. Run unit tests
python -m pytest tests/test_ocr_normalization.py -p no:cacheprovider -v

# 2. Re-run normalizer on CPO_18 (chi_tra)
python -m src.ocr.normalization.normalizer --work-id CPO_18 --writing-system chi_tra --pages 205-206 --force

# 3. Re-run normalizer on CPO_18 (vie)
python -m src.ocr.normalization.normalizer --work-id CPO_18 --writing-system vie --pages 205-206 --force

# 4. Validate output schema programmatically
python -c "from src.ocr.normalization.validators import validate_normalized_page; import json; [print(f, validate_normalized_page(json.load(open(f, encoding='utf-8')))) for f in ['data/CPO_18/03_ocr/normalized/chi_tra/page_0205.json', 'data/CPO_18/03_ocr/normalized/vie/page_0205.json']]"
```
