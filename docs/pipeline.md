# Pipeline dóng hàng Hán-Việt

Chế độ mới (khuyến nghị): OCR MỘT lần cho mỗi HỆ CHỮ, phân đoạn 3 cấp độc lập:

```
PDF ─extract─▶ 02_extraction/pages ─ocr(chi_tra|vie)─▶ 03_ocr/raw + manifests
    ─segment─▶ 04_segmentation/{documents,layers}.xml
    ─clean─▶ 05_cleaning/structural/<layer>.xml
    ─sentence─▶ 04_segmentation/sentences/<layer>.xml ─▶ 05_cleaning/<layer>.xml
    ─validate─▶ 05_cleaning/validation_report.json
    ─align─▶ 06_alignment/<pair>.xml ─export─▶ 07_export/*.tsv + *.xlsx + report
```

Chế độ cũ (vẫn chạy, đọc/ghi tương thích): OCR 3 lần theo lớp `han/am/nghia` ->
`02_extraction/<layer>.xml` -> `03_cleaned` -> `04_alignment`. Dữ liệu cũ không bị xoá;
các bước đọc tự lùi về thư mục cũ nếu thư mục mới chưa có (`find_cleaned_dir`,
`find_alignment_dir`).

| Bước | Lệnh | Vào | Ra |
|---|---|---|---|
| extract | `python -m src.extraction.pdf_extraction` | PDF | `02_extraction/pages/page_205.png` (mới) hoặc `02_extraction/01_page_image/<layer>/` (cũ) |
| ocr | `python -m src.ocr.ocr --writing-system chi_tra\|vie` | `02_extraction/pages` | `03_ocr/raw/<ws>/page_XXXX.json` + `03_ocr/manifests/<ws>.json` |
| segment | `python -m src.segmentation.pipeline --config ...` | `03_ocr` | `04_segmentation/{documents,layers/<layer>}.xml` |
| clean | `python -m src.cleaning.pipeline --layer L --structural-only` | `04_segmentation/layers/<L>.xml` | `05_cleaning/structural/<L>.xml` + audit files |
| sentence | `python -m src.segmentation.sentences --layer L` | `05_cleaning/structural/<L>.xml` | `04_segmentation/sentences/<L>.xml` + `05_cleaning/<L>.xml` |
| validate | `python -m src.validation.validate --config ...` | segmentation & cleaning | `05_cleaning/validation_report.json` |
| align | `python -m src.alignment.align` | 2 lớp câu đã clean | `06_alignment/<pair>.xml` |
| export | `python -m src.alignment.export` | `<pair>.xml` | `07_export/<name>.{tsv,xlsx}`, report |

Chạy cả chuỗi: `python -m src.pipeline --config configs/CPO_18.json` 
Nếu muốn chạy riêng từng step thì thêm `--steps <step>` với <step> là `"extract", "ocr", "segment", "clean", "sentence", "validate", "align", "export"`

## Kiến trúc mới: OCR theo hệ chữ + phân đoạn
- **Config**: có khối `ocr` và/hoặc `segmentation` -> chế độ mới; còn `layers` -> chế độ cũ.
  `common.source/pages` dùng chung cho extract/OCR; `ocr.<chi_tra|vie>` khai báo engine,
  region, psm, dpi, lang, keep, workers, low_conf.
- **OCR**: mỗi hệ chữ chạy đúng MỘT lần (thay vì 3 lần cho han/am/nghia). Kho thô append-only
  `03_ocr/raw/<ws>/page_XXXX.json` lưu từng `run` kèm bbox/confidence/payload engine; manifest
  giữ engine/ tham số/ trang lỗi. Chạy lại dùng cache theo băm tham số; `--force` chỉ ghi lại
  manifest, KHÔNG xoá run cũ (không có tuỳ chọn xoá cache trong quy trình thường).
- **Segment** (`src/segmentation/`): cắt tài liệu theo `segmentation.document.start_patterns`
  trên system chính (chi_tra nếu có). Bật `numbered_heading: true` để dùng quy ước
  `^\s*(\d{1,3})\s*[.．﹒·。]\s+` (bắt buộc có khoảng trắng sau dấu); có thể truyền pattern
  riêng dạng chuỗi hoặc `{enabled, pattern}`. System còn lại ghép theo TRANG; nếu một trang
  có nhiều tài liệu thì chia theo tỉ lệ số dòng của system chính. Gán lớp bằng state
  machine `segmentation.layers.<layer>.start_patterns/end_patterns` (heading không vào dòng
  chính, lưu `section_headings.jsonl`). Bật `heading_mode: true` cho lớp có heading dạng
  nhãn: pattern phải khớp ĐẦU dòng (sau tiền tố OCR mặc định `[lI|] `), phần đuôi chỉ được
  có khoảng trắng/dấu câu/ký hiệu; nhờ vậy câu văn chứa “phiên âm”/“dịch nghĩa” không bị
  nhận nhầm. Đổi tiền tố bằng `heading_prefix_pattern`, đặt `null` để tắt; không bật
  `heading_mode` thì regex giữ nguyên hành vi `search` cũ. Không đủ tin cậy ->
  `review_required.tsv` với lý do `document_boundary_assumed`, `document_heading_not_found`,
  `document_match_ambiguous`, `document_match_estimated`, `layer_overlap`, `missing_heading`,
  `missing_layer`, `missing_source`, `unassigned_line`, `metadata_uncertain`.
- **Metadata** chuyển sang cấu hình lớp của segmentation (`title_patterns`, `author_patterns`,
  `attribution_patterns`, `author_max_len`...) và chạy trên dòng đầu ĐÚNG tài liệu + lớp;
  `metadata_extraction` chỉ còn bật/tắt và `exclude_from_main_text`. Cleaning nhận
  `--no-metadata` để không trích lại/second-guess.
- **ID ổn định**: `<work>.<doc:03d>.P{page:03d}.{S|L}{seq:03d}`. `Sentence.document_id` được
  giữ qua clean; align chỉ dóng TRONG từng tài liệu, không bắc cầu sang tài liệu khác.
- **Output**: `04_segmentation/{documents.jsonl,document_metadata.jsonl,section_headings.jsonl,
  review_required.tsv,layers/<layer>.jsonl,<layer>.metadata.jsonl}`;
  clean ghi `05_cleaning/` và bỏ qua gộp metadata (đã do segmentation sở hữu);
  export ghi `07_export/`, sheet REVIEW có thêm `document_id`.

## Quy ước
- Mỗi bước không ghi đè output cũ nếu không có `--force`.
- Mọi dòng bị loại ở bước clean nằm trong `<cleaned>/<layer>.dropped.tsv` kèm lý do: người phải duyệt.
- Câu nằm trên nhiều trang có `pages: [205, 206]` và cờ `cross_page` (ưu tiên rà soát khi QA).
- Điểm `score` chỉ để xếp hạng rà soát; tỉ lệ lỗi thật đo bằng mẫu kiểm tra thủ công.
- Sheet `REVIEW` trong xlsx cùng thứ tự hàng với `DONG_HANG`; xoá hoặc bỏ qua khi nộp nếu không cần.

## Cắt đoạn theo ranh giới tiêu đề (clean — chế độ cũ)
> Chế độ mới: việc cắt tài liệu/lớp nằm ở `src/segmentation`; cleaning nhận
> `04_segmentation/layers/<layer>.jsonl` và chạy với `--no-metadata`, bỏ qua
> `start_after/end_before`. Phần dưới giữ cho config/dữ liệu cũ.

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

## Trích xuất metadata cấp tài liệu (src/cleaning/metadata.py)
Chạy NGAY SAU lọc trang, TRƯỚC mọi bước làm sạch nội dung, để tiêu đề/tác giả/quy trách
không lọt vào corpus dóng hàng:

```text
front_matter / ocr_noise -> [section_start marker] -> title | author | attribution -> body
```

- Bật qua config `metadata_extraction` (src/pipeline.py) hoặc CLI `--meta-*` của clean.
  KHÔNG cấu hình gì = **no-op hoàn toàn** (không bao giờ xoá metadata phá huỷ).
- Lớp `han`: thường không có marker; tiêu đề qua `title_patterns` (vd đánh số `1﹒`),
  quy trách qua `attribution_patterns` (vd `^.{0,3}\*`), thân bài = dòng có dấu ngắt câu.
- Lớp `am`/`nghia`: `section_start` (`PHIÊN ÂM` / `DỊCH NGHĨA`) mở khối metadata; dòng
  TRƯỚC marker = `front_matter` (lời dẫn) hoặc `ocr_noise` (rác OCR); tiêu đề/tác giả =
  các dòng CHỮ HOA trước thân bài (dòng cuối khối đúng "dạng tên người" = tác giả,
  còn lại = tiêu đề); `attribution_patterns` (`^\*`) bắt dòng quy trách; `section_end`
  (`DỊCH NGHĨA` / `CHÚ THÍCH`) cắt phần sau (`after_section_end`).
- Tiêu đề nhiều dòng được GHỘP MỘT giá trị nhưng giữ nguyên từng `source_ids`/`pages`.
- Dòng không xếp loại được trong khối metadata → `metadata_uncertain`: bị loại khỏi corpus
  chính nhưng vào review_required.tsv + file metadata (không xoá lặng lẽ).
- Nhiều `<SECT>` = nhiều tài liệu: metadata tách theo `document_id` (tiền tố ID dòng),
  không bao giờ trộn lẫn.
- Khi metadata trích được, các ranh giới do metadata lo (`section_start`/`section_end`);
  KHÔNG dùng đồng thời `clean.<layer>.start_after/end_before` cho cùng marker (sẽ báo lỗi
  vì marker đã bị metadata stage loại trước).

Output trong `03_cleaned/` (chế độ cũ) hoặc `04_segmentation/` + `05_cleaning/` (chế độ mới):

| Tệp | Nội dung |
|---|---|
| `<layer>.metadata.jsonl` | metadata theo từng tài liệu của lớp: fields + uncertain + status |
| `document_metadata.jsonl` | GỘP MỌI LỚP theo `document_id`: title/author/attribution của han/am/nghia + source_pages |
| `file_metadata.json` | `<FILE><meta>` của mọi lớp (file-level: TITLE, LANGUAGE, OCR...) |

Lý do mới trong `dropped.tsv`: `front_matter`, `ocr_noise`, `section_marker`,
`metadata_title`, `metadata_author`, `metadata_attribution`, `metadata_uncertain`,
`after_section_end`.


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

Cờ thêm ở vòng clean dữ liệu đầy đủ (`reports/clean_full_20261127.md`), **mặc định tắt**, đặt trong `clean.common`:
- `stream_running_headers` (`--stream-running-headers`, chế độ hai luồng): bỏ tiêu đề chạy tính trên han.xml+vie.xml
  của cả work (khớp mờ, chỉ dòng ở 2 dòng đầu/cuối trang, `streams.edge_running_header_ids`).
- `footnotes.note_entry_markers` (`--note-entry-markers`): dòng "N. ..." là mục chú thích khi N trùng một chữ số chỉ
  mục trong thân trên cùng trang, hoặc liền sau mục N-1 đã nhận.
- `stream_note_markers` (`--stream-note-markers`): số mục chú thích / chỉ mục theo trang lấy từ cả vie.xml; lớp
  nghia/tho xoá chỉ mục trùng số một mục chú thích cùng trang ("Đảo Bồng 3 khói tỏa").
- `am_markers_extended` (`--am-markers-extended`): lớp am xoá thêm chỉ mục sau dấu câu và ở cuối dòng, trừ số bài
  ("BÀI 2", "Kỳ 3").
- `capture_source_lines` (`--capture-source-lines`): dòng nguồn "Trích <Tên sách>" (không dấu kết câu) -> metadata
  `source`.

Text sạch mỗi câu một dòng: `python scripts/export_clean_text.py --work-id W` ghi `data/W/05_cleaning/text/`
(`han.txt`, `viet_<lớp>.txt`, `mapping.tsv`); khi đóng gói nộp bài: `--package-root DIR --topic MA_DE_TAI` chép sang
`DIR/<MA_DE_TAI>_<W>/03_text_sach/`. Danh sách rà soát OCR cho người: `python scripts/export_ocr_review.py --work-id W`.


## Thêm phương pháp dóng hàng
Viết class kế thừa `Aligner` trong `src/alignment/aligners.py`, gắn `@register_aligner`, trả về `list[Bead]`
phủ đủ hai phía theo thứ tự (câu thừa = Bead một phía rỗng + cờ `unaligned`). Sau đó dùng `--method <tên>`.
Dự kiến: `llm` (dịch thơ). API CLC không được cấp nên không cài. Aligner cần thống kê toàn work thì định nghĩa
`prepare(layout, src_layer, tgt_layer, src, tgt)`; `align.py` gọi một lần trước khi dóng từng tài liệu.

### `bertalign` (han-nghia)
Cài lại thuật toán Bertalign trong `src/alignment/bertalign.py`, không dùng gói `bertalign`. Embedding nằm ở
`src/alignment/embeddings.py` (transformers thuần, chạy được trong `my_nlp`; không cần sentence-transformers).
```powershell
$env:HAN_VIET_MODEL_DIR="E:\models"          # thư mục chứa LaBSE\, bge-m3\ đã tải (không có thì tải từ HF)
python -m src.alignment.align --pair han-nghia --work-id CPO_19 --method bertalign --opt model=bge-m3 --force
python -m src.alignment.align --pair han-nghia --work-id CPO_19 --method bertalign --opt model=bge-m3 --opt bridge_weight=3 --force
python scripts/eval_han_nghia.py --work-id CPO_19 --run a=<x.xml> --run b=<y.xml> [--review 30 --prefer b --review-out r.tsv]
# chấm theo bộ dóng tay eval/gold/{work}_*.xlsx, gộp 3 work ('{work}' được thay; bỏ file khoá ~$ của Excel)
python scripts/eval_han_nghia.py --work-id CPO_18,CPO_19,CPO_20 --run a=<root>/{work}/06_alignment/han-nghia.a.xml `
    --gold --tune
```
- Chấm theo chuẩn: bản dóng tay đã sửa OCR và tách câu lại, nên được chiếu lên ID câu pipeline. Việc chiếu dùng dóng
  token, rồi gộp các hàng chuẩn dính chung một câu pipeline thành bead chuẩn (`build_gold`). Hàng có Dịch nghĩa mà
  lớp nghĩa pipeline không có thì bị loại khỏi phần chấm và được liệt kê trong `rows_nghia_unreachable`.
- Bead a-b với 1 <= a,b <= `max_side` (4) và a+b <= `max_total` (8). Bead 1-0 và 0-1 có cờ `unaligned`.
- Điểm bead = cosine(đoạn Hán, đoạn nghĩa) + `bridge_weight` × trùng từ âm + `len_weight` × (log2(1+min/max) - 1).
  Tỉ lệ độ dài nghĩa/Hán được tính theo từng work trong `prepare`. Bead một phía nhận điểm `skip`.
- Cầu nối âm (`bridge_weight` > 0): đọc `06_alignment/han-am.xml` (hoặc `bridge_file`). Trùng từ là Dice của n-gram
  âm tiết 1–3 (bỏ dấu, chữ thường), trọng số `ngram_weights` × IDF.
- Cache embedding: SQLite theo sha1 câu, đặt ở `cache_dir` (mặc định `$HAN_VIET_EMB_CACHE` hoặc
  `~/.cache/han_viet_corpus/embeddings`). Mô hình chỉ được nạp khi có câu chưa có trong cache.
- GPU: dùng fp16. Lệnh từ chối chạy nếu tiến trình khác đang chiếm > `gpu_busy_mb` MiB (1024).
- `configs/CPO_18.json`, `CPO_19.json`, `CPO_20.json` dùng `bertalign` cho `han-nghia`, với `opt` là `model=bge-m3`
  và `bridge_weight=3` (đổi ngày 2026-11-28). Mặc định trong mã (`default_method`) vẫn là `length`.
  Số liệu: `reports/han_nghia_20261127.md`, mục 4a (bộ chuẩn 50 trang; strict F1 0,62 với `length`, 0,91 với cấu hình này).
- Mô hình không tải được qua Python trên máy local (lỗi SSL). Đặt `HAN_VIET_MODEL_DIR` trỏ vào thư mục chứa `bge-m3\`
  trước khi chạy pipeline.

## Thêm engine OCR
Class kế thừa `OcrEngine` trong `src/ocr/engines.py` + `@register_engine`, rồi `--engines <tên>`
(nhiều engine + `--select best_conf` để so sánh/voting).
