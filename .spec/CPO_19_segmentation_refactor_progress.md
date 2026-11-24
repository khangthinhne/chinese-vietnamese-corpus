# CPO_19 Segmentation Refactor — Progress / Handoff

**Source of truth:** `.spec/CPO_19_segmentation_refactor_spec.md`
**Work id:** `CPO_19`
**Status:** implementation in progress (audit complete).

## 1. Project overview

Refactor the post-OCR pipeline for CPO_19 into clearly separated stages:

```
raw.xml
 -> OCR text normalization
 -> marker OCR correction
 -> pre-segmentation cleaning (headers/footers/page numbers/empty)
 -> line script classification
 -> marker detection/canonicalization
 -> document boundary detection (script + heading evidence)
 -> document assignment
 -> sequential section segmentation (han/phien_am/dich_nghia/dich_tho/aux)
 -> structural cleaning -> sentence segmentation -> validation
```

Constraints preserved: raw OCR immutable, no OCR rerun, no silent line loss,
no automatic rewriting of content, work-specific aliases/thresholds configurable,
four-digit page naming preserved.

## 2. Repository audit

### Inspected

- `src/pipeline.py`, `src/segmentation/{pipeline,documents,layers,patterns,serialization,model}.py`
- `src/cleaning/{pipeline,filtering,normalization,artifacts,metadata,model}.py`
- `src/validation/validate.py`, `src/common/corpus.py`, `src/ocr/raw_xml.py`
- `configs/CPO_19.json`, `configs/CPO_19_unified.json`
- `data/CPO_19/03_ocr/ocr_raw/raw.xml`, `data/CPO_19/03_ocr/raw/NNNN.json`
- `data/CPO_19/04_segmentation/*`, `data/CPO_19/05_cleaning/*`
- `tests/test_segmentation.py`, `tests/test_unified_pipeline.py`, `tests/test_cleaning.py`
- `docs/segmentation_architecture.md`

### Confirmed root causes of “zero cleaned Hán lines”

1. **Hán heading heuristic in `src/cleaning/filtering.py::drop_reason`.**
   A Hán line with `cjk_ratio >= 0.5` that is short, punctuation-free, not last on page,
   and follows a complete line is dropped as `heading`. CPO_19 Hán poetry lines are all
   short and punctuation-free, so 75/124 Hán layer lines were dropped as `heading`.
   `05_cleaning/han.xml` is therefore empty.
2. **Script-mismatch drops.** 46/124 Hán layer lines were dropped as `not_han`
   (Vietnamese lines accidentally placed in the Hán layer). The spec requires retaining
   and flagging such lines, not silently dropping them.
3. **Marker OCR variants / missing markers.** The old state machine only knew a few
   regexes, so `PHIỀN ÂM:`, `DỊCH THOẠI:` and missing `PHIÊN ÂM:` markers at pages 19
   and 30 caused sections to be mis-assigned or missed.
4. **Boundary over-splitting.** Numbered sub-works `2. 其二` / `5. 其二` were treated
   as top-level document starts, producing 9 documents instead of the intended 6 works
   plus front matter.

### Baseline tests

`python -m pytest tests -q` before changes:
- 157 passed, 6 failed, 4 errors.
- Pre-existing failures: `tests/test_paddle_engine.py` (missing
  `src.ocr.engines.parse_paddle_output` / `PaddleOcrEngine`) and
  `tests/test_ocr_normalization.py::test_active_engines_registry`. Unrelated to this task.

### Key CPO_19 facts

- `raw.xml` has 16 `PAGE` elements, pages 15–30, 382 selected `PAGE/LINE` lines.
  `.//LINE` = 1148 because `PAGE/RUNS/RUN/LINE` duplicates exist; use
  `src.ocr.raw_xml.load_unified` (PAGE/LINE only).
- Config says pages 15–121 but raw OCR only contains 15–30.
- Intended structure: Vietnamese front matter (15.1–15.5) + 6 works:
  1. `LẠNG SƠN ĐẠO TRUNG (NHỊ THỦ)` (2 sub-works, pages 15–19)
  2. `3. 登母子山` (page 19)
  3. `4. 過關留贈潘御史` (2 sub-works, pages 20–24)
  4. `6. 受降城` (page 24)
  5. `7. 寧明江記見` (page 26)
  6. `8. 長短句吟` (page 28)
- Missing `PHIÊN ÂM:` markers at 19.11 (`ĐĂNG MẪU TỬ SƠN`) and 30.11
  (`TRƯỜNG ĐOÀN CÚ NGÂM`).
- OCR variants: `DỊCH THOẠI:` (26.6), lowercase `Dịch nghĩa:`, `PHIEN AM`.
- Running headers: `NGÔ THÌ NHẬM TOÀN TẬP - TẬP III` / `HOÀNG HOA ĐỒ PHẢ`.
- Numeric-only lines (18, 21, 29, 32) are page numbers with consistent offset +3.

## 3. Implementation checklist

- [x] Audit repo, spec, CPO_19 artifacts, tests, docs.
- [x] Confirm Hán cleaning root cause by tracing actual lines.
- [x] Create persistent progress file.
- [~] Add script classification module (`src/segmentation/script.py`).
- [~] Add heading/marker helpers (`src/segmentation/headings.py`, `markers.py`).
- [~] Add pre-segmentation cleaning (`src/segmentation/preprocessing.py`).
- [~] Add boundary detection (`src/segmentation/boundaries.py`).
- [~] Add sequential section segmentation (`src/segmentation/sections.py`).
- [~] Add refactor orchestrator (`src/segmentation/refactor.py`).
- [ ] Integrate new path into `src/segmentation/pipeline.py` (opt-in when new config present).
- [ ] Add serialization for preprocessing/classification/boundary/review artifacts.
- [ ] Fix `src/cleaning/filtering.py` so Hán poetry is preserved and mismatches are flagged.
- [ ] Extend `src/validation/validate.py` with line accounting/section/marker checks.
- [ ] Update `configs/CPO_19.json`.
- [ ] Add unit/integration tests.
- [ ] Run full test suite.
- [ ] Run CPO_19 segmentation + cleaning with existing OCR, verify stats/determinism.
- [ ] Update `docs/segmentation_architecture.md` and final report.

## 4. Files changed

| File | Purpose | Status | Tests |
|---|---|---|---|
| `src/segmentation/script.py` | Unicode script counting/classification | in progress | pending |
| `src/segmentation/headings.py` | heading/subwork/title helpers | in progress | pending |
| `src/segmentation/markers.py` | marker aliases/canonicalization | in progress | pending |
| `src/segmentation/preprocessing.py` | normalization + pre-seg cleaning | in progress | pending |
| `src/segmentation/boundaries.py` | boundary candidates/evidence | in progress | pending |
| `src/segmentation/sections.py` | sequential section state machine | in progress | pending |
| `src/segmentation/refactor.py` | orchestrator for new path | in progress | pending |
| `src/segmentation/model.py` | add script/marker/provenance fields | in progress | pending |
| `src/segmentation/pipeline.py` | integrate refactored path | not started | pending |
| `src/segmentation/serialization.py` | new artifact writers/readers | not started | pending |
| `src/cleaning/filtering.py` | preserve Hán poetry, flag mismatches | not started | pending |
| `src/validation/validate.py` | accounting/section/marker checks | not started | pending |
| `configs/CPO_19.json` | aliases/thresholds/boundary config | not started | pending |
| `tests/test_cpo19_refactor.py` | new unit/integration tests | not started | pending |
| `docs/segmentation_architecture.md` | architecture update | not started | pending |

## 5. Testing results

| Command | Result | Notes |
|---|---|---|
| `python -m pytest tests -q` (baseline) | 157 passed, 6 failed, 4 errors | pre-existing paddle/OCR-registry failures |
| `python -m pytest tests/test_segmentation.py tests/test_cleaning.py -q` (baseline) | passed | existing segmentation/cleaning behavior |

## 6. Current working state

- Audit complete; no source changes committed yet at time of writing.
- Scratch analysis file `scratch/cpo19_lines.txt` is untracked and temporary.
- Implementation of new segmentation stages is starting.

## 7. Next steps

1. Finish `script.py`, `headings.py`, `markers.py`, `preprocessing.py`,
   `boundaries.py`, `sections.py`, `refactor.py`.
2. Add serialization helpers and integrate into `segmentation/pipeline.py`
   (activate only when `segmentation.script_classification` / `segmentation.markers`
   / `segmentation.document_boundary` are present, preserving old behavior/tests).
3. Fix cleaning filtering.
4. Extend validation.
5. Update CPO_19 config, tests, docs.
6. Run full tests and CPO_19 pipeline, record exact counts here.
