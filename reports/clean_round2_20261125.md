# Clean round 2 (2026-11-25, chạy song song với OCR Qwen CPO_20): kiểm tra và cải thiện bước làm sạch

Nhánh `fix/clean-round2` (tách từ `feat/ocr-complete` @ `9f9251f`), một commit, **chưa push**. Mọi lần chạy đều trên
**bản sao trong scratchpad**. `data/` thật và `configs/` thật không bị ghi; hàng đợi Qwen không bị ngắt; không chạy
GPU. Hành động bị chặn: **không có** (không cần `reports/blocked_actions.md`).

## 0. Bộ dữ liệu thử và cách đo

| Bộ | CPO_18 | CPO_19 | CPO_20 |
|---|---|---|---|
| `s20` (mẫu 20 trang của lượt đêm) | 205–212 | 15–34 | 5–24 |
| `full` (quy mô lớn) | 205–212 | 15–121 | 5–415 (chỉ chép `raw/` các trang ≤ 415, không đọc file đang được Qwen ghi) |

Mỗi bộ có data root và config **bản sao** riêng: `segmentation.ocr_streams = han_vie`, `common.pages` = khoảng của
bộ. Chạy `src.pipeline --steps layers,segment,clean,sentence,validate,align,export --force`, rồi
`scripts/audit_clean.py` (đã mở rộng, mục 1). Script trong scratchpad: `c2/setup_sets.py`, `run_sets.sh`,
`metrics.py`, `probe.py`.

"Bead hợp lệ" = bead `han-am` không gắn cờ `unaligned`/`low_score` (cấu hình Unihan mặc định).

## 1. Nền (code `9f9251f`, chưa sửa) và sau khi sửa

`audit_clean.py` thêm các phép đo:
- `letter_markers`: chỉ mục `(a)`;
- `punct`: dấu câu full-width trong lớp Việt, khoảng trắng trước dấu câu, ngoặc đơn/ngoặc kép lệch;
- `numbers`: số có ở lớp gốc mà mất ở bản sạch, không tính số trong chỉ mục và số trang;
- `page_loss`: trang mất ký tự ở bước clean > max(3 × trung vị, 20%).

Bảng dưới lấy số câu đã làm sạch theo từng lớp.

| Bộ | Work | | câu han | am | nghia | tho | bead han-am hợp lệ | cặp han-am | review | cảnh báo log | chỉ mục (a) | ngoặc lệch | số mất | vấn đề audit |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| s20 | CPO_18 | nền | 26 | 33 | 28 | 0 | 22/31 | 25 | 21 | 8 | 0 | 4 | 7 | 4 |
| s20 | CPO_18 | **sau** | 26 | 33 | 28 | 0 | 22/31 | 25 | 21 | 8 | 0 | 4 | 7 | 4 |
| s20 | CPO_19 | nền | 100 | 100 | 65 | 57 | 91/105 | 94 | 114 | 32 | **11** | 0 | 3 | 2 |
| s20 | CPO_19 | **sau** | 100 | 100 | 65 | 57 | **92/106** | 94 | **103** | 32 | **0** | 0 | 3 | 1 |
| s20 | CPO_20 | nền | 87 | 85 | 49 | 67 | 62/92 | 70 | 47 | 27 | 0 | 0 | 0 | 0 |
| s20 | CPO_20 | **sau** | 87 | 85 | 49 | 67 | 62/92 | 70 | 47 | 27 | 0 | 0 | 0 | 0 |
| full | CPO_19 | nền | 530 | 510 | 460 | 244 | 464/561 | 474 | 573 | 103 | **45** | 2 | 12 | 7 |
| full | CPO_19 | **sau** | 530 | 510 | 460 | 244 | **467/562** | 474 | **528** | 103 | **0** | 2 | 12 | 5 |
| full | CPO_20 | nền | 1724 | 1833 | 1509 | 669 | 1134/1895 | 1425 | 501 | 258 | 0 | 31 | 9 | 11 |
| full | CPO_20 | **sau** | **1717** | 1833 | 1509 | 669 | 1130/1893 | 1425 | 501 | 257 | 0 | 31 | 9 | 9 |

(Bộ full CPO_18 trùng s20 vì chỉ có 8 trang.)

Ký tự bỏ ở bước clean (layers → structural), mọi phần bỏ đều có bản ghi (`dropped.tsv` / `cleaning_audit.xml`), không
có cảnh báo "mất không ghi nhận > 10%":
- CPO_18: han 13,7%, nghia 34,8%. Chú thích nguồn `(Hoằng minh tập…)` và tiêu đề; đã đối chiếu ở lượt đêm.
- CPO_19 full: han 1,0%, am 4,7%, nghia 3,0%, tho 6,8%.
- CPO_20 full: han 0,7%, am 0,8%, nghia 0,4%, tho 5,0%.

**Thời gian:** CPO_20 full mất 82–253 giây tuỳ lần chạy, nhưng chênh lệch này không do code: bước `align han-am`, phần không
đổi code, đã mất 60–187 giây vì CPU bị Qwen tranh. Phần tính fingerprint mới tốn khoảng 1,1 giây cho `raw/` 15 MB.

## 2. Lỗi đã biết, đã sửa

### 2.1 Chỉ mục khảo dị dạng chữ cái `(a)`, `(b)` (lớp am/nghia/tho)

Đếm trước khi sửa, ở `04_segmentation/layers`, bộ full:
- **44 lần, tất cả ở CPO_19** (CPO_18 và CPO_20: 0). Lớp am 42, nghia 2.
- Dạng: 24 dính chữ (`tòng(b) lĩnh`, `triện^{(b)} ấn`: Qwen viết chỉ số trên kiểu LaTeX); 20 cách chữ
  (`Tiên hoàng (a) uy đức`). Ở 10 lần, tài liệu có mục khảo dị tương ứng (`a. Bản A.404 …`).
- Xem ngữ cảnh từng lần: **44/44 là chỉ mục thật**. Không có `(a)` nào là ngoặc nội dung.

Sửa: `LETTER_MARKER_RE` (`src/cleaning/model.py`), áp ở `clean_ocr_artifacts` cho lớp am/nghia/tho. Có ghi audit
`remove_marker` (luật `LETTER_MARKER_RE`). Tắt được bằng `--keep-letter-markers` hoặc
`clean.<lớp>.strip_letter_markers: false`. Chỉ xoá khi chỉ mục đứng **sau** một chữ và **trước** khoảng trắng, dấu câu
hoặc cuối dòng. Một lần bổ sung sau khi chạy: dạng `xuất(^a) hống` (CPO_19 tr.74).

Kết quả: 45 câu → **0**. Bead hợp lệ CPO_19 s20 91/105 → 92/106; full 464/561 → 467/562.

**Không xoá** (có test):
- `(a)` ở đầu dòng: mục liệt kê khảo dị.
- `(ab)`, `(1a)`: không phải đúng một chữ cái.
- `(x)y`: sau ngoặc là chữ.
- Lớp han.

Còn sót 1 chỗ không xoá: `Thanh miếu thường y a) mộng` (CPO_19 am.00217). OCR mất ngoặc mở, nên không còn khớp mẫu.
Để rà tay.

### 2.2 Chạy lại không `--force`: bỏ qua êm, mã 0

- `deps.check_fresh` giờ phân biệt hai trường hợp:
  - đầu vào không đổi và output chính còn → ném `deps.UpToDate`; `main()` của mọi bước bắt nó, log "đã có, đầu vào
    không đổi: bỏ qua", trả 0;
  - đầu vào đổi, hoặc output có mà không có bản ghi phụ thuộc → **vẫn chặn như cũ**.
- `UpToDate` là lớp con của `FileExistsError`, nên code gọi thẳng hàm vẫn thấy "không ghi đè".
- Áp cho: ocr (unified, writing-system), segmentation, clean structural, clean, sentence, clean_sentence, align,
  export (theo cặp và han-viet).
- Hai bước trước đây luôn ghi lại, giờ cũng ghi fingerprint và bỏ qua: `build_layers` (đầu vào: `raw/` + manifest OCR
  của luồng + tham số lọc) và `validate` (đầu vào: `04_segmentation/`, `05_cleaning/`, config; không gồm
  `06_alignment/`, vì align chạy sau validate).

Kiểm chứng (`c2/idem.py`): chạy `python -m src.pipeline --steps layers,…,export` hai lần liên tiếp trên bản sao
sạch. **CPO_19 và CPO_18: cả hai lần mã 0; lần hai đổi 0/140 và 0/105 file.**

### 2.3 Hai test cũ trong `tests/test_pipeline.py`

Do **test lỗi thời**, không phải bug. Luật "dòng Hán ngắn không dấu câu là tiêu đề" đã được cố ý chuyển thành tuỳ chọn
(`han_heading_heuristic`, mặc định tắt vì phá thơ), nhưng chưa có cờ CLI.
- Đã thêm `--han-heading-heuristic`; hai test bật nó một cách tường minh.
- `test_han_am_levenshtein_then_export` kiểm tra hành vi mới của 2.2: chạy lại thì mã 0 và file không bị ghi lại; đầu
  vào đổi mà không `--force` thì vẫn mã 1.
- Ba test khác khẳng định hành vi cũ "chạy lại → mã 1" đã được cập nhật theo 2.2: `test_clean_shared_outputs`,
  `test_ocr_store`, `test_pipeline_fixes`.

## 3. Kiểm tra quy mô lớn (CPO_19 15–121, CPO_20 5–415)

| # | Loại lỗi | Số lần | Ví dụ (ID, trang) | Nguyên nhân | Xử lý |
|---|---|---|---|---|---|
| 1 | Dòng Hán do OCR tự sinh trên trang chỉ có chữ Việt | CPO_20: 7 × `欽定四庫全書`, 4 × `也` | `CPO_20.146.P0347.S013` tr.347–411 (đã mở ảnh tr.347: văn xuôi Việt, không có chữ Hán) | paddlevl sinh dòng trên trang không có chữ Hán. Bộ lọc tiêu đề chạy bỏ qua trang chỉ 1 dòng | **Đã sửa**: luật `recurring_single_line` (`page_filter.py`): trang mà lớp chỉ có 1 dòng, dòng đó lặp y hệt trên ≥ `header_footer_min_pages` trang thì bỏ, có audit. Tắt bằng `--keep-single-line-pages`. Trên 3 bộ: bỏ **21 dòng, 0 sai** (10 tiêu đề chạy `NGÔ THÌ NHẬM TOÀN TẬP - TẬP III` / `HOÀNG HOA ĐỒ PHẢ` của CPO_19, 11 dòng tự sinh của CPO_20). 6 câu trùng liên tiếp của CPO_20 còn 0 |
| 2 | **Khối khảo dị / nguyên chú / chú thích nhập vào chính văn lớp `nghia`** | CPO_19: 21 câu có tiêu đề + phần thân | `KHẢO DỊC: a. Bản A.404: Xanh không chương.` | OCR đọc `KHẢO DỊ` thành `KHẢO DỊC:`, tiêu đề dính nội dung cùng dòng. Mẫu `end_patterns` của CPO_19 có `D[ỊI]\b`, thiếu `NGUYÊN CHÚ`/`CHÚ THÍCH` | **Đề xuất config** (không áp vào config thật): mục 5.1. Đo trên bản sao: nghia 460 → 386 câu, **0** tiêu đề chú thích còn trong nghia, bead han-am giữ 467 |
| 3 | Mục chú thích đánh số `1. Côn Lôn: Tức dãy…` lọt vào lớp am/tho | CPO_20 am **65**/1833, tho 6; CPO_19 am 2 | `CPO_20.am.00124` tr.34; `CPO_20.tho.00258` tr.137 | Thiếu tiêu đề `CHÚ THÍCH` trong OCR. Mục chú thích đánh số chỉ nhận `(1)`, `[1]` | **Tuỳ chọn mới, mặc định tắt**: `clean.<lớp>.footnotes.note_entries: true` (`--note-entries`), mẫu `NOTE_ENTRY_PATTERNS`. Kết quả đo ở mục 3a |
| 4 | Ngoặc kép lệch | CPO_20 31, CPO_18 4, CPO_19 2 | `CPO_20.nghia.00116` / `.00117` | Phần lớn là trích dẫn nhiều câu bị tách câu ở giữa (mở ở câu này, đóng ở câu sau): đúng theo tách câu, không mất chữ | Không sửa. Đề xuất: không tách câu bên trong ngoặc kép chưa đóng (cần cờ, ảnh hưởng dóng hàng) |
| 5 | Số bị mất | CPO_20 9, CPO_19 12, CPO_18 7 | `(Làm mùa thu năm Bính Thìn 1796)` (tr.111) bị loại là `footnote`; `全宋文卷六十三，2782頁）` là `bibliographic_note` | Mọi số mất đều nằm trong dòng bị loại **có ghi nhận** (chú thích / trích dẫn nguồn). Trong lớp han, chữ số Latin bị loại theo luật hệ chữ | Không bước nào đổi số trong câu giữ lại. **Cần quyết**: dòng ghi niên đại sáng tác `(Làm … năm 1796)` là metadata của bài, nên chuyển sang metadata thay vì bỏ |
| 6 | Trang mất ký tự bất thường | CPO_19 tho tr.53, 63 (29%, 22%) | `Lâm Giang` (cuối trang), tiêu đề chạy | Đúng: tên người dịch + tiêu đề chạy | Không sửa. Đề xuất đưa người dịch vào metadata |
| 7 | "Tiêu đề chạy" theo audit | CPO_19 nghia `Bài N`/`KHẢO DỊC`/`NGUYÊN CHÚ`; CPO_20 am `Than ôi`, tho `Ngô Linh Ngọc dịch` (5 câu) | | `Bài N` và `Than ôi` là nội dung lặp hợp lệ (audit báo nhầm). `KHẢO DỊC` thuộc lỗi 2. `… dịch` là dòng người dịch | Đề xuất: `attribution_patterns` của lớp tho CPO_20 nhận `^.{2,30}\s+dịch$` |
| 8 | Chú thích Hán trong ngoặc | CPO_20 han 2 | `海口千家逐波開（奇華海口門）` | Chú giải địa danh in liền câu thơ | Chưa sửa (2 lần; xoá chữ Hán khỏi chính văn có rủi ro). Đề xuất cờ `han_strip_paren_notes` |
| 9 | Dấu câu full-width trong lớp Việt / khoảng trắng trước dấu | 0 / 1 (CPO_18 `quyển lục thập tam )`) | | OCR | Không cần sửa |
| 10 | Gạch nối chưa ghép, câu trùng do Qwen lặp, số trang trơ trọi | 0 | | | Đạt |

**Mục 3a. Đo `note_entries` (bộ `full_ne`, bật cho mọi lớp Việt):** xem phụ lục A.

**Tên riêng, niên hiệu, số, đơn vị:**
- Chuẩn hoá chỉ gồm NFC và dấu câu Hán. Bước làm sạch không thay chữ trong câu giữ lại.
- Mọi số mất đều nằm trong dòng bị loại có bản ghi (lỗi 5).
- Không chuẩn hoá giản thể → phồn thể ở bước clean.

**Lệch hệ chữ:** 0 câu lệch mà không gắn cờ, trên cả 3 work.

## 4. Đối chiếu checklist "2 – Làm sạch text" của hướng dẫn

| Yêu cầu | Module | Test | Kết quả đo (mục 1/3) | Trạng thái |
|---|---|---|---|---|
| Bỏ đầu/chân trang | `cleaning/page_filter.py` (dòng đầu/cuối lặp ≥ 3 trang; **mới**: dòng duy nhất lặp) | `test_clean_round2::test_recurring_single_line_pages_dropped`, test page_filter cũ | 21 tiêu đề chạy/dòng tự sinh bị bỏ, 0 sai; audit chỉ còn báo nhầm (`Bài N`, `Than ôi`) | đạt |
| Bỏ trang không thuộc nội dung chính | `excluded_pages` (config, thủ công) | test cleaning cũ | Chưa cấu hình cho phạm vi mới (lời giới thiệu, phụ lục) | một phần → mục 5 |
| Ghép từ/câu ngắt do chuyển trang | `reconstruct_page_boundaries`, cờ `cross_page` | `test_pipeline::test_clean_han_cross_page_and_noise` | 0 gạch nối chưa ghép | đạt |
| Loại dòng trùng | `detect_duplicates` + luật dòng duy nhất | test duplicates cũ | CPO_20: 6 câu trùng liên tiếp → 0 | đạt |
| Loại chú thích chân trang, chỉ mục `[1]`, `(1)` | `footnotes.py`, `MARKER_RE`, **`LETTER_MARKER_RE`**, `note_entries` (tuỳ chọn) | `test_clean_markers`, `test_clean_round2` | Chỉ mục: 0 còn sót (trừ 1 do OCR mất ngoặc). **Khối chú thích còn lọt vào nghia (CPO_19) và am (CPO_20)** | một phần (lỗi 2, 3) |
| Sửa ký tự rác / khoảng trắng | `artifacts.py` (NOISE_CHARS), `normalization.py` | test artifacts cũ | full-width 0, khoảng trắng trước dấu 1 | đạt |
| Chuẩn hoá dấu câu không đổi nghĩa | `normalization.py` (Hán: `,` → `，`…) | `test_pipeline` | Ngoặc kép lệch do tách câu (lỗi 4) | một phần |
| Giữ tên riêng / niên hiệu / số / đơn vị | Không bước nào thay chữ; audit `numbers` | `test_clean_round2::test_audit_number_loss_and_page_loss` | 0 số đổi trong câu giữ lại | đạt (lưu ý lỗi 5) |
| Bỏ qua bảng biểu, tranh/ảnh | Không có bộ phát hiện | — | CPO_19 tr.13 có bảng (thuộc lời giới thiệu) | thiếu → loại theo trang (mục 5) |

## 5. Đề xuất cấu hình phạm vi mới (CHƯA áp vào config thật)

Đã render PDF bằng CPU ở 36 dpi (`c2/contact.py`, ảnh `c2/sheet_cpo1{8,9}.png`, `sheet_cpo20.png`).

**5.1 CPO_19 (5–833):**
- Trang 5–13 là **lời giới thiệu** (văn xuôi Việt, tr.13 có bảng đối chiếu và CHÚ THÍCH). Trang 14 (`小引` + TIỂU DẪN)
  là nội dung (Hoàng Hoa đồ phả). Từ khoảng tr.400 là phần Bang giao hảo thoại (văn xuôi Hán + phiên âm + dịch).
  Tr.836 trở đi là MỤC LỤC, nên điểm cuối 833 hợp lý; cần xem tr.834–835.
- Đề xuất:
  ```json
  "clean": {"common": {"excluded_pages": [5,6,7,8,9,10,11,12,13]}},
  "segmentation": {"layers": {"nghia": {"end_patterns": ["^\\s*DỊCH\\s+TH[ƠO]\\b", "^\\s*KH[ẢAÁÃẠ]O\\s+D[ỊI]C?\\b",
                                                          "^\\s*NGUY[ÊEỀ]N\\s+CH[ÚU]\\b", "^\\s*CH[ÚU]\\s+TH[ÍI]CH\\b"]}}}
  ```
  Mẫu `end_patterns` đã đo ở lỗi 2. Có thể thay danh sách `excluded_pages` bằng việc cho thành viên 2 OCR từ trang 14.

**5.2 CPO_18 (205–544):**
- Tr.205 mở đầu "Phần II: nguyên văn tác phẩm dịch và chú thích". Có các trang CHÚ THÍCH dài (vd 213–215), phải loại ở
  bước clean.
- Tr.543–545 mang tiêu đề chạy "Phụ lục": **cần quyết** có lấy tr.543–544 không.
- Một số trang có watermark `giangvien.net` và chữ viết tay (tr.500): OCR có thể sinh rác. Đề xuất thêm
  `clean.common.ocr.remove_patterns: ["giangvien\\.net"]`.

**5.3 CPO_20 (5–806):**
- Tr.3–4 là tiểu sử (đã nằm ngoài khoảng); tr.807 sang tác giả Hoàng Quang. Khoảng **đúng**.
- **Tr.346–khoảng 424 là văn xuôi chỉ có tiếng Việt** (truyện dịch, không in nguyên văn Hán). Đây là nguồn của dòng Hán
  tự sinh (lỗi 1) và các tài liệu "chỉ có Việt".
- Đề xuất: không loại khỏi OCR. Nếu nhóm quyết không dùng phần này cho cặp Hán–Việt thì thêm `excluded_pages` cho
  346–424 sau khi xác định ranh giới chính xác.
- Đề xuất bật `clean.am.footnotes.note_entries: true` và `clean.tho.footnotes.note_entries: true` (lỗi 3) sau khi xem
  phụ lục A.

## 6. Hai việc kiểm tra thêm

**6.1 Xuất `03_text_sach`.** Pipeline chưa có. Đã thêm `scripts/export_clean_text.py`, có test:
- Đọc `05_cleaning/<lớp>.xml`, ghi `han.txt`, `viet.txt`, `am.txt` / `nghia.txt` / `tho.txt`, mỗi câu một dòng, giữ thứ
  tự tài liệu.
- `mapping.tsv` ghi các cột file, dòng, ID câu, tài liệu, lớp, trang, `SOURCE_IDS` để truy vết về dòng OCR.
- Mặc định ghi vào `data/<W>/07_export/03_text_sach/`.
- **Cần quyết:** "viet.txt" là lớp nào. Mặc định đang gộp am → nghia → tho theo từng tài liệu; `--viet-layers nghia`
  nếu chỉ cần bản dịch nghĩa.

**6.2 Sửa tay OCR có truy vết.** Repo chưa có cơ chế áp bản sửa; chỉ có `review_required.*` để đọc. Đề xuất thiết kế
(chưa cài, vì cần sửa cả build_layers và segmentation):
- File `corrections/<WORK>/<han|vie>.tsv` (commit được, không phải `data/`) gồm các cột: `line_id` (vd `vie.P0074.L006`),
  `old_text` (bắt buộc, để phát hiện lệch), `new_text` (rỗng = xoá dòng), `reviewer`, `note`.
- Áp ở `build_layers`: sau khi chọn run, trước script_filter. Khoá cache của `raw/` không đổi; `raw/` không bao giờ bị
  sửa.
- Dòng có `old_text` khác văn bản hiện tại (OCR chạy lại) thì không áp, ghi vào `build_<layer>.json` mục
  `corrections_unmatched`, kèm cảnh báo.
- XML ghi thuộc tính `CORRECTED="1"` và `RAW_TEXT` gốc.
- Bật bằng config `ocr.streams.corrections: true`, mặc định tắt. Thêm phép đo vào checker: số bản sửa áp được / không
  khớp.

## 7. Nghiệm thu

- **Test:**
  - Nền đầu task: 332 passed, 7 fail + 4 error.
  - Sau sửa: **351 passed, 5 fail + 4 error, 1 skipped**. Phần còn hỏng chỉ có `test_paddle_engine.py` (viết cho API paddle
    cũ), có từ trước.
  - 2 test `test_pipeline.py` đã sửa (mục 2.3). Thêm 17 test mới (`tests/test_clean_round2.py`).
- **Mẫu 20 trang so với lượt đêm (bảng C):**
  - CPO_19: 100/100 câu han/am như đêm qua; bead hợp lệ 91/105 → **92/106** (tăng, do xoá chỉ mục `(a)`).
  - CPO_20: 87/85 câu, bead 62/92, **giống hệt**.
  - **CPO_18: 22/31, đêm qua 24/28.** Thay đổi này **đã có ở nền**, trước mọi sửa của lượt này: nền và sau sửa giống
    nhau. Mình đã loại trừ run Qwen mới của tr.208 (bỏ run đó trên bản sao vẫn ra 22/31). Không chạy lại được code
    đêm qua trên config hiện tại, vì code cũ không đọc khối `ocr.streams`. Nguyên nhân khả dĩ: dữ liệu OCR khác đêm qua
    (paddlevl adapter 5, quy tắc chọn run hợp lệ, lớp am 30 → 33 câu). **Chưa xác định; cần rà riêng.**
- **Bộ full CPO_20:** bead hợp lệ 1134/1895 → 1130/1893 (−4). Nguyên nhân là 11 dòng Hán tự sinh bị bỏ (lỗi 1); bead
  ghép với chúng trước đây là ghép sai.
- **`--dry-run` của config thật** (CPO_18/19/20): giống hệt từng byte giữa code cũ và mới (`c2/dryrun_diff.sh`).
- **`data/` thật:** không bị ghi bởi task này. Chỉ có job Qwen đang chạy ghi `data/CPO_20/03_ocr`.

## 8. Commit, nhánh, git status

- Nhánh `fix/clean-round2`, **1 commit** (theo yêu cầu: một commit cho cả prompt), chưa push.
- `git status` main tree đầu và cuối: không đổi file nào ngoài `data/CPO_20/03_ocr/**` do job Qwen ghi. Worktree
  `fix/clean-round2` chỉ có các file code/test/script/báo cáo của commit này.
- **Ghi chú OCR (ngoài phạm vi):** Qwen ở CPO_20 tr.416+ (văn xuôi dày chữ) chạy trung vị **144 giây/trang**,
  đến khoảng 200 giây/trang khi CPU bận (tr.≤ 415: 28 giây). Không cắt cụt dải nào. Còn 339 trang, ước khoảng 14 giờ.

## 9. Việc người dùng cần quyết

1. **Bật mẫu `end_patterns` mới cho lớp nghia của CPO_19** (lỗi 2, mục 5.1). Đã đo: 74 câu chú thích ra khỏi nghia,
   bead không đổi.
2. **Bật `note_entries` cho lớp am/tho của CPO_20** (lỗi 3, phụ lục A), và các `excluded_pages` cho lời giới thiệu
   CPO_19 (tr.5–13). Cùng chỗ đó: có lấy trang "Phụ lục" 543–544 của CPO_18 không, và có dùng phần văn xuôi chỉ có
   tiếng Việt của CPO_20 (tr.346–424) không.
3. Dòng niên đại sáng tác `(Làm … năm 1796)` và tên người dịch: bỏ như hiện tại, hay chuyển sang metadata.
4. `viet.txt` của `03_text_sach` là lớp nào (mặc định gộp am → nghia → tho).
5. Điều tra riêng việc bead CPO_18 giảm từ 24/28 xuống 22/31 (đã có ở nền, không do lượt này).

## Phụ lục A. `note_entries` (bộ full_ne, bật cho am/nghia/tho của cả 3 work)

| Work | Lớp | Dòng bị loại thêm | Câu dạng `N. …` còn lại | Câu am / nghia / tho (sau) | Bead han-am hợp lệ |
|---|---|---|---|---|---|
| CPO_18 | — | 0 | 0 | 33 / 28 / 0 | 22/31 (không đổi) |
| CPO_19 | am / nghia | 2 / 40 | 0 / 0 | 508 / 446 / 244 | 467/562 → 467/560 |
| CPO_20 | am / nghia / tho | 141 / 46 / 1 | 15 / 0 / 5 | 1833 → **1742** / 1509 → 1485 / 669 → 668 | 1130/1893 (59,7%) → **1181/1822 (64,8%)** |

- Rà tay các dòng bị loại thêm (mẫu ở log `c2/ne_diff.py`): tất cả là mục chú thích thật, kiểu `2. Uất Lâm: Một quận
  thuộc Quảng Tây.`, `1. 2. Bản A.603 chép là…`, `4. Tam kinh: Ba lối cũ…`. **Không thấy câu nội dung nào bị loại
  nhầm** trong mẫu đã xem. Chưa xem hết 230 dòng.
- Vẫn sót 20 mục, vì dòng trước chưa trọn câu (luật chỉ nổ khi dòng trước đã kết câu) hoặc không có dấu `:`
  (`6. Ý câu thơ nhắc tới…`).
- Số "bị mất" tăng (CPO_20 9 → 233) vì số thứ tự chú thích và năm trong chú thích bị loại cùng dòng: đúng dự kiến,
  có bản ghi `footnote`.
- **Đề xuất:** bật cho `clean.am` và `clean.tho` của CPO_20; bật cho `clean.nghia` của CPO_19 nếu chưa áp mẫu
  `end_patterns` ở mục 5.1. Giữ mặc định tắt trong code.
