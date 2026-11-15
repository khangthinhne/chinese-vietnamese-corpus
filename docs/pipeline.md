# Pipeline dóng hàng Hán-Việt

```
PDF ─extract─▶ ảnh trang ─ocr─▶ 02_extraction/<layer>.xml ─clean─▶ 03_cleaned/<layer>.jsonl ─align─▶ 04_alignment/<pair>.jsonl ─export─▶ .tsv + .xlsx + report
```

| Bước | Lệnh | Vào | Ra |
|---|---|---|---|
| extract | `python -m src.extraction.pdf_extraction` | PDF | `02_extraction/01_page_image/<layer>/page_205.png` |
| ocr | `python -m src.ocr.ocr` | thư mục ảnh | `02_ocr_raw/*.json` (cache) + `02_extraction/<layer>.xml` |
| clean | `python -m src.preprocessing.clean` | `<layer>.xml` | `03_cleaned/<layer>.{jsonl,txt,dropped.tsv}` + audit |
| align | `python -m src.alignment.align` | 2 lớp đã clean | `04_alignment/<pair>.jsonl` |
| export | `python -m src.alignment.export` | `<pair>.jsonl` | `<name>.tsv`, `<name>.xlsx`, `<name>_report.{json,md}` |

Chạy cả chuỗi: `python -m src.pipeline --config configs/CPO_18_demo.json` (thêm `--dry-run` để xem lệnh).

## Quy ước
- Mỗi bước không ghi đè output cũ nếu không có `--force`.
- Mọi dòng bị loại ở bước clean nằm trong `03_cleaned/<layer>.dropped.tsv` kèm lý do: người phải duyệt.
- Câu nằm trên nhiều trang có `pages: [205, 206]` và cờ `cross_page` (ưu tiên rà soát khi QA).
- Điểm `score` chỉ để xếp hạng rà soát; tỉ lệ lỗi thật đo bằng mẫu kiểm tra thủ công.
- Sheet `REVIEW` trong xlsx cùng thứ tự hàng với `DONG_HANG`; xoá hoặc bỏ qua khi nộp nếu không cần.

## Cắt đoạn theo ranh giới tiêu đề (clean)
Khi một trang sách chứa nhiều mục (PHIÊN ÂM / DỊCH NGHĨA / CHÚ THÍCH...) mà mỗi lớp OCR
chỉ cần một mục, cấu hình `clean.<layer>` hỗ trợ hai regex (tùy chọn, `pipeline` tự truyền xuống):

```json
"clean": {
  "am":    {"start_after": "^Phiên âm",  "end_before": "^Dịch nghĩa"},
  "nghia": {"start_after": "^Dịch nghĩa", "end_before": "^Chú thích"}
}
```

- `start_after`: bỏ mọi dòng TRƯỚC dòng khớp (kể cả dòng khớp); lấy từ dòng kế tiếp.
- `end_before`: ngừng lấy trước dòng khớp; dòng khớp và phần sau bị loại.
- Chạy trên TOÀN BỘ danh sách dòng theo thứ tự, nên nội dung trải qua nhiều trang vẫn được
  nối liền (không reset trạng thái khi đổi trang).
- Khớp không phân biệt hoa/thường, bỏ khoảng trắng hai đầu dòng trước khi so regex.
- Dòng bị loại vì ranh giới vẫn vào `<layer>.dropped.tsv` với lý do
  `section_start_heading` / `section_end_heading` / `section_before_start` / `section_after_end`.
- Kết quả cắt đoạn ghi vào `02_extraction/metadata.json` → `layers["<layer>_cleaned"].section`
  (`status`: `unbounded` | `ok` | `incomplete_end`, kèm pattern, `first_page`/`last_page`,
  `n_selected`, `n_excluded`).
- Thiếu tiêu đề start hoặc đoạn cắt rỗng hoặc regex sai → lỗi dừng pipeline (không ra corpus
  "có vẻ ổn" nhưng sai). Thiếu tiêu đề end → cảnh báo lớn + `status: incomplete_end` (thường do
  mục kế tiếp nằm ngoài phạm vi trang OCR đã chọn).
- CLI tương đương: `--start-after REGEX`, `--end-before REGEX`. Không cấu hình gì = hành vi cũ.

## Bộ làm sạch module-hoá (src/cleaning/)
`src/preprocessing/clean.py` giờ chỉ là wrapper tương thích (re-export từ `src/cleaning`).
Chuỗi 10 bước, mỗi bước nhận `list[RawLine]` + `CleanConfig` + `Audit` và trả về
(dòng giữ, bản ghi) — mọi biến đổi truy vết được theo ID/trang nguồn:

1. **unicode** — NFC, bỏ zero-width, collapse khoảng trắng/xuống dòng/tab.
2. **page_filter** — loại trang trong `excluded_pages` (lý do `irrelevant_page`), trang
   rỗng (`blank_page`); tự phát hiện và bỏ dòng đầu/cuối lặp lại ≥3 trang
   (`page_header`/`page_footer`, tắt bằng `--no-headers/--no-footers`,
   ngưỡng `--header-footer-min-pages`; trang 1 dòng không bao giờ bị coi là header).
3. **section** — cắt `start_after`/`end_before` (chạy sớm để tiêu đề rác OCR không bị
   nuốt làm "chú thích"; dòng loại có lý do `section_*`).
4. **duplicates** — OCR quét chồng: bỏ dòng trùng y hệt hoặc `difflib` ratio ≥
   `--dup-threshold` (mặc định 0.9) trong CÙNG trang hoặc vùng biên trang kề
   (ngữ cảnh `--dup-context` dòng; cụm < `--dup-min-len` ký tự, mặc định 20, không bao giờ bị bỏ).
   Chi tiết: `duplicates.jsonl` (kèm `kept_id`, `removed_id`, similarity, page).
5. **footnotes** — gom chú thích nhiều dòng / qua trang thành MỘT ghi chú:
   - ngoặc trọn `( ... )`/`（ ... ）`/`[ ... ]` mở đầu dòng, đóng cuối dòng, trải nhiều dòng
     hoặc qua trang → loại nguyên cụm (`footnote`; giống trích nguồn → `bibliographic_note`);
   - mục đánh số `(1) `/`（2） `/`[1] ` sau dòng chấm dứt câu → gom tới dấu kết câu
     (`footnote`, confidence thấp nếu cuối dữ liệu);
   - ngoặc chưa đóng hoặc đóng giữa dòng với phần đuôi → KHÔNG xoá, trình review
     (`uncertain_note`, reason `uncertain`);
   - ngoặc trong dòng ("ông (đệ tử) kính lễ") và chú thích [Phật]/[muôn]/[người] GIỮ NGUYÊN;
   - chính sách: `--footnote-policy exclude|preserve|review` (mặc định exclude),
     `--no-paren-notes`, `--no-bib-notes` để tắt nhánh phát hiện.
   Chi tiết: `footnotes.jsonl` (category, action, pages, line_ids, confidence).
6. **artifacts** — xoá/thay mẫu OCR (`--ocr-remove REGEX`, `--ocr-replace REGEX=>REPL`),
   bỏ chỉ mục chú thích trong dòng `[1] (2) （3） ①...` (giữ [Phật] v.v.), cờ ký tự nghi
   OCR (`®°Øø‡†§¤µ¶`^~¦`) + từ Latin lạc — KHÔNG tự xoá, đưa vào review.
7. **reconstruction** — nối qua ranh giới trang: từ bị cắt (`"tra conjunct-"` + `"ion"`,
   chỉ khi dòng sau bắt đầu chữ thường), câu chưa trọn (`sentence_continuation`),
   mơ hồ (viết hoa) chỉ GHI + review (`ambiguous_join`), không tự sửa.
   Chi tiết: `reconstruction.jsonl` (type, from_page/to_page, before/after).
8. **normalization** — chuẩn hoá theo lớp (giữ hành vi clean.py cũ; han: bỏ `-`, `*`...).
9. **filtering** — lọc dòng (pattern cấu hình, tiêu đề trọn dòng...) có ngữ cảnh,
   lý do cũ `pattern:...`/`heading`/`too_short`... giữ nguyên.
10. **segmentation** — dóng câu trên dòng chảy đã nối; câu qua trang có `pages: [...]`
    + cờ `cross_page`.

Tệp audit trong `03_cleaned/`:

| Tệp | Nội dung |
|---|---|
| `cleaning_audit.jsonl` | mọi thao tác add/remove/replace/flag theo `line_id` |
| `footnotes.jsonl` | mỗi chú thích gom được: category, action, pages, line_ids |
| `duplicates.jsonl` | mỗi dòng trùng bị bỏ: kept_id, removed_id, similarity |
| `reconstruction.jsonl` | mỗi phép nối: type, pages, text trước/sau |
| `review_required.tsv` | dòng cần người duyệt (ký tự nghi OCR, chú thích không chắc, nối mơ hồ...) |

`<layer>.dropped.tsv` vẫn gồm TẤT CẢ dòng bị loại (cả `section_*`, `footnote`,
`duplicate_line`, lý do lọc cũ) — đây là bản review chính cho người.

Cấu hình JSON `clean.<layer>.common` (áp cho mọi lớp) hỗ trợ các ngưỡng:
`excluded_pages`, `duplicate_threshold`, `duplicate_min_len`, `duplicate_context`,
`header_footer_min_pages`, `footnote_policy`, `enable_*` (tắt từng nhánh),
`ocr_remove`, `ocr_replace`; cờ CLI tương đương (xem `python -m src.preprocessing.clean --help`).


## Thêm phương pháp dóng hàng
Viết class kế thừa `Aligner` trong `src/alignment/aligners.py`, gắn `@register_aligner`, trả về `list[Bead]`
phủ đủ hai phía theo thứ tự (câu thừa = Bead một phía rỗng + cờ `unaligned`). Sau đó dùng `--method <tên>`.
Dự kiến: `clc` (API lab), `bertalign`/`vecalign`, `llm` (dịch thơ).

## Thêm engine OCR
Class kế thừa `OcrEngine` trong `src/ocr/engines.py` + `@register_engine`, rồi `--engines <tên>`
(nhiều engine + `--select best_conf` để so sánh/voting).
