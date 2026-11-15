# Pipeline dóng hàng Hán-Việt

```
PDF ─extract─▶ ảnh trang ─ocr─▶ 02_extraction/<layer>.xml ─clean─▶ 03_cleaned/<layer>.jsonl ─align─▶ 04_alignment/<pair>.jsonl ─export─▶ .tsv + .xlsx + report
```

| Bước | Lệnh | Vào | Ra |
|---|---|---|---|
| extract | `python -m src.extraction.pdf_extraction` | PDF | `02_extraction/01_page_image/<layer>/page_205.png` |
| ocr | `python -m src.ocr.ocr` | thư mục ảnh | `02_ocr_raw/*.json` (cache) + `02_extraction/<layer>.xml` |
| clean | `python -m src.preprocessing.clean` | `<layer>.xml` | `03_cleaned/<layer>.{jsonl,txt,dropped.tsv}` |
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

## Thêm phương pháp dóng hàng
Viết class kế thừa `Aligner` trong `src/alignment/aligners.py`, gắn `@register_aligner`, trả về `list[Bead]`
phủ đủ hai phía theo thứ tự (câu thừa = Bead một phía rỗng + cờ `unaligned`). Sau đó dùng `--method <tên>`.
Dự kiến: `clc` (API lab), `bertalign`/`vecalign`, `llm` (dịch thơ).

## Thêm engine OCR
Class kế thừa `OcrEngine` trong `src/ocr/engines.py` + `@register_engine`, rồi `--engines <tên>`
(nhiều engine + `--select best_conf` để so sánh/voting).
