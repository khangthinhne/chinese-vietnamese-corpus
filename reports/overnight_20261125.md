# Báo cáo chạy đêm 2026-11-25: sửa clean, nối han.xml/vie.xml, kiểm thử 3 work, tool dóng Hán–Âm của Lab

Nhánh: `fix/clean-han-vie-streams` (tách từ `main` @ `787c72f`), **chưa push**. Mọi lần chạy thử dùng bản sao trong
scratchpad; `data/` thật không bị ghi (dry-run của CPO_18/19/20 vẫn giống hệt từng byte).

## 1. Việc đã làm / bỏ / chặn

Tất cả 5 bước đã xong, không phải dừng sớm. Hai work CPO_18 và CPO_19 qua cổng chất lượng; CPO_20 chưa qua
(nguyên nhân gốc ở mục 2), nên phần chạy thật của tool Lab chỉ chạy cho CPO_18 và CPO_19.

| Commit | Nội dung |
|---|---|
| `fd2bcca` | (nền, việc của lượt trước) tách câu Hán `auto`, LengthAligner không dừng khi một phía rỗng, OCR không bị cache chặn |
| `cf777ee` | **Bước 1**: file kiểm toán dùng chung không còn chặn lớp khác ở bước clean |
| `68f148c` | **Bước 2**: loại dòng không phải Hán khỏi han.xml; gộp dòng paddlevl bị tách từng chữ |
| `1b05982` | **Bước 3**: chế độ `ocr_streams = han_vie` (bật theo config) cho segmentation |
| `1c45ffc` | **Bước 2**: xoá chỉ mục chú thích dạng chữ số mũ; thêm `scripts/audit_clean.py` |
| `512da10` | Bước 3: thêm bước `layers` (build_layers) vào `src.pipeline` khi bật luồng |
| `e445a3a` | Bước 4: thơ Hán không chấm câu là thân bài, không bị gom thành "tiêu đề" metadata |
| `007f4db` | Bước 4: tách câu `am` mặc định `auto` (văn vần theo dòng, văn xuôi theo dấu câu) |
| `2fcde03` | Bước 4: chế độ luồng: thứ tự trộn, ranh giới tài liệu, chuyển lớp ngầm khi thiếu tiêu đề mục |
| `e13ddbe` | Tài liệu; `configs/CPO_20.json` thêm `notes_after_attribution` (chỉ có tác dụng khi bật luồng) |
| `916a14f` | **Bước 5, giai đoạn 1**: wrapper `src/alignment/lab_han_am.py` cho tool Lab |
| `9541e89` | **Bước 5, giai đoạn 2**: từ điển Lab cho aligner `levenshtein` (mặc định không đổi) |

Không commit: `third_party/` (code tool của Lab, chưa rõ giấy phép; `dict/` và `outputs/` đã vào `.gitignore`),
`spec_han_am_lab_tool.md`, `.env`, mọi file trong `data/`.

## 2. Bước 4: bảng trước/sau và cổng chất lượng

Mẫu: 20 trang đầu có ảnh. CPO_18 205–212 (8 trang), CPO_19 15–34, CPO_20 5–24. Chạy end-to-end, không `--force`.
"Bead hợp lệ" là bead không gắn cờ `unaligned`/`low_score`. "Cặp xuất" là số dòng trong `07_export/*_parallel.tsv`.

Các bảng:
- **(A) TRƯỚC (số trong đề, 10 trang, chế độ raw.xml):** CPO_19 41 câu han / 37 câu am / 19 trên 50 bead hợp lệ / 22 cặp han-am;
  CPO_20 7/26/1 trên 29/2; CPO_18 4/30/3 trên 30/4.
- **(B) TRƯỚC (20 trang, chế độ raw.xml, code đầu đêm):** cùng bộ trang với bảng SAU.
- **(C) SAU (20 trang, chế độ luồng `han_vie`):** kết quả cuối.

| Work | Bảng | câu han | câu am | bead han-am hợp lệ | cặp han-am | han-nghia | han-tho | cảnh báo (log) |
|---|---|---|---|---|---|---|---|---|
| CPO_18 | B | 4 | 30 | 3/30 (10%) | 4 | 2 | 0 | 7 |
| CPO_18 | **C** | **26** | **30** | **24/28 (86%)** | **25** | **24** | 0 | 15 |
| CPO_19 | B | 84 | 52 | 32/88 (36%) | 37 | 26 | 43 | 30 |
| CPO_19 | **C** | **100** | **100** | **91/105 (87%)** | **94** | **63** | **54** | 19 |
| CPO_20 | B | 8 | 42 | 1/46 (2%) | 2 | 5 | 7 | 27 |
| CPO_20 | **C** | **87** | **85** | **62/92 (67%)** | **70** | **46** | **53** | 27 |

**Cổng chất lượng** (≥ 70% bead han-am hợp lệ, và mọi tài liệu lệch số câu han/am ≤ 30%):
- **CPO_18: ĐẠT** (86%; 2 tài liệu, không tài liệu nào lệch). Phạm vi trang trong mẫu KHÔNG khớp hẳn: câu Hán ở
  trang 205, 208, 209; câu âm ở 205, 206, 209, 210. Lý do: CPO_18 là văn xuôi (6 bức thư), Hán và âm của một bức
  thư in lệch trang nhau; trang 206, 210–212 không có chữ Hán (đã mở ảnh kiểm tra trước đó).
- **CPO_19: ĐẠT** (87%; 9 tài liệu, không tài liệu nào lệch quá 30%).
- **CPO_20: KHÔNG ĐẠT** (67%, 4/12 tài liệu lệch). Nguyên nhân gốc, không chỉnh ngưỡng:
  1. Tài liệu 001–003 (trang 5–9): bản in đặt dấu `。` sau mỗi cặp câu Hán, paddlevl đọc được, nên
     `--split auto` tách Hán theo dấu câu (mỗi câu là một cặp câu thơ), còn phiên âm (văn vần) tách theo dòng.
     Số câu lệch đúng 2:1 (4 vs 8). Theo yêu cầu, tôi không hạ ngưỡng `auto` của lớp Hán.
  2. Tài liệu 012: mẫu cắt ở trang 24, phần phiên âm của bài nằm ở trang 25 (ngoài mẫu).
  3. Gợi ý (chưa làm): cho `auto` của lớp `am` đi theo lựa chọn của lớp `han` trong cùng tài liệu. Hoặc nếu chấp
     nhận, dùng từ điển Lab cho bước ghép cặp: số bead hợp lệ của CPO_20 lên 67/92 = 72,8% (mục 5, giai đoạn 2),
     nhưng tiêu chí lệch số câu vẫn trượt.

Chế độ cũ (chưa bật luồng): CPO_18 (chi_tra.xml/vi.xml) cho đầu ra **giống từng byte** so với trước. CPO_19/20 ở chế
độ raw.xml có thay đổi đúng dự kiến (luật câu thơ trong metadata, tách âm `auto`, xoá chữ số mũ). Kết quả raw.xml sau
khi sửa: CPO_19 53/112 bead hợp lệ, 55 cặp han-am (trước 32/88, 37 cặp); CPO_20 không đổi đáng kể (1/57), vì lỗi
nằm ở việc gán lớp trong stream unified, đây chính là lý do cần chế độ luồng.

## 3. Lỗi tìm thấy ở bước clean / segmentation

Đã sửa (kèm test):

| # | Lỗi | Bằng chứng | Sửa |
|---|---|---|---|
| 1 | Clean lớp am/nghia/tho bị chặn sau khi chạy lớp han, thông báo sai "structural/am.xml đã tồn tại"; tách câu âm thầm dùng bản chưa làm sạch | lượt chạy trước, cây scratchpad mới | `cf777ee`: chỉ output riêng của lớp mới chặn |
| 2 | Chỉ mục chú thích dạng chữ số mũ (`Sài Sơn ² đệ nhất phong`) còn trong lớp am | `audit_clean` CPO_20: 4 câu | `1c45ffc` |
| 3 | han.xml lẫn dòng khảo dị tiếng Việt có trích chữ Hán (`a. Bản A.404: Hội khê 洄溪.`) và dòng paddlevl đọc sai (`J. 一`, `山 [r]`, một dòng tự sinh tiếng Anh) | 41 dòng ngắn (< 4 chữ CJK) trong mẫu, đối chiếu ảnh trang | `68f148c`: luật loại có cấu hình (`--han-min-cjk-share 0.3`, `--han-min-cjk-with-latin 2`), loại 29 dòng, không dòng Hán thật nào; dòng bị loại ghi vào `build_han.json` + log |
| 4 | paddlevl xuất một dòng in thành 7 dòng một chữ (CPO_20 tr.14 `玄麻蛋副璽書來`) | xem ảnh trang | `68f148c`: gộp khi mọi dòng của một dải đều đúng 1 ký tự |
| 5 | Cả bài thơ không chấm câu bị gom thành "tiêu đề" metadata và mất khỏi lớp Hán (CPO_20: 喜雨作 + 8 câu; CPO_19 bài 5) | `han.metadata.xml` | `e445a3a` |
| 6 | Phiên âm thơ tách theo dấu câu thành từng cặp câu, lệch 2:1 với Hán | CPO_20 8 vs 4 | `007f4db` |
| 7 | (chế độ luồng) dòng Qwen cao chen sai thứ tự; mẫu tiêu đề vỡ vì lỗi OCR; thiếu tiêu đề "PHIÊN ÂM"/"DỊCH NGHĨA" trong OCR; tiêu đề chạy `HOÀNG HOA ĐỒ PHẢ` làm mở nhầm lớp; chữ Hán trích trong chú thích mở tài liệu mới | xem các vòng chạy Bước 4 | `2fcde03` |

Kết quả kiểm toán khác (`scripts/audit_clean.py`, 3 work):
- Idempotent: chạy lại không `--force` thì mọi bước bị chặn đúng, 0 file thay đổi trong `04_segmentation/` và
  `05_cleaning/`. Lưu ý: chạy lại như vậy trả mã khác 0 (bị chặn) chứ không "bỏ qua êm".
- Truy vết: 0 câu thiếu `SOURCE_IDS`, 0 ID không tồn tại (raw.xml, han/vie.xml, chi_tra/vi.xml).
- Mất nội dung: lớp han ở chế độ raw.xml mất 86–94% ký tự ở bước clean, nhưng đều có bản ghi (`not_han`). Đó là
  dòng Việt bị gán nhầm vào lớp Hán, không có chữ Hán thật nào bị bỏ (đã kiểm tra `han.dropped.tsv`).
- Không còn số trang trơ trọi, tiêu đề chạy, câu trùng liên tiếp, hay gạch nối chưa ghép trong mẫu.

Chưa sửa (gợi ý):
- Chỉ mục khảo dị dạng chữ cái `(a)`, `(b)` trong lớp am (`Điệp trĩ (a) mạn truyền…`) vẫn còn. Khi đưa vào tool
  Lab, nó thành một "âm tiết" thừa `a`. Gợi ý: thêm `\([a-zđ]\)` vào `MARKER_RE` sau khi xác nhận sách không dùng
  dạng này trong chính văn.
- paddlevl đọc sai một số tiêu đề nhỏ (`其二` → `六一`, `J. 一`). Ở chế độ luồng không còn ảnh hưởng ranh giới tài
  liệu, nhưng chữ sai vẫn nằm trong metadata tiêu đề.
- Bản thân 2 test fail có từ trước trong `tests/test_pipeline.py` (đường đi `cleaning/pipeline.py` cũ) vẫn còn.

## 4. Test

Nền: 214 passed, 7 fail + 4 error có từ trước. Cuối đêm: **285 passed, 7 fail + 4 error y như trước**
(`test_paddle_engine.py` viết cho API paddle cũ; 2 test trong `test_pipeline.py`). Không thêm test fail mới.
71 test mới: `test_clean_shared_outputs`, `test_clean_markers`, `test_metadata_verse`, `test_segmentation_streams`,
`test_lab_han_am`, `test_levenshtein_lab`, và phần mở rộng của `test_script_filter`/`test_build_layers`/
`test_pipeline_fixes`.

## 5. Bước 5: tool dóng Hán–Âm của Lab

Tìm thấy `spec_han_am_lab_tool.md`; tool và 2 từ điển có đủ trong `third_party/SN-QN-alignment/`.

**Giai đoạn 1** (`916a14f`): hồi quy với `data/sample-data.csv` qua wrapper cho **139 vị trí, 134 khớp, match_rate
96,40%, mismatch_rate 3,60%**, TSV và `statistic.json` giống hệt từng byte so với chạy tay. Chạy thật (scratchpad,
20 trang, chế độ luồng):

| Work | bead han-am | vào CSV | unaligned | vị trí căn chỉnh | match_rate | mismatch_rate | dòng cần rà soát | gợi ý OCR |
|---|---|---|---|---|---|---|---|---|
| CPO_18 | 28 | 25 | 3 | 423 | 86,52% | 13,48% ⚠ | 15 | 6 |
| CPO_19 | 105 | 94 | 11 | 678 | 92,48% | 7,52% | 33 | 10 |
| CPO_20 | 92 | 70 (dry-run) | 22 | không chạy (chưa qua cổng) | | | | |

Cảnh báo: CPO_18 có mismatch toàn tài liệu > 10%, và 2 bead lệch độ dài (44 chữ Hán / 21 âm tiết, 23/17: ghép cặp
văn xuôi). CPO_19 có 2 dòng lỗi > 30%. Tiền kiểm (> 30% unaligned) đều qua (CPO_18 11%, CPO_19 10%, CPO_20 24%).
Bàn giao vào `07_export/han-am/` của bản sao: TSV và XLSX (sheet `DONG_HANG`) cùng nội dung, mỗi dòng đúng một TAB
giữa các cột. Chạy lại khi input không đổi thì bỏ qua.

**Giai đoạn 2** (`9541e89`). So sánh 3 cấu hình cho bước ghép cặp (mặc định giữ nguyên Unihan; chỉ Unihan cho
kết quả giống hệt trước trên cả 3 work):

| Cấu hình | Work | bead | unaligned | low_score | hợp lệ | điểm TB | match_rate (tool Lab) |
|---|---|---|---|---|---|---|---|
| Unihan | CPO_18 | 28 | 3 | 1 | 24 | 0,799 | 86,52% |
| Lab | CPO_18 | 28 | 3 | 1 | 24 | 0,938 | 86,52% |
| Hợp | CPO_18 | 28 | 3 | 1 | 24 | 0,938 | 86,52% |
| Unihan | CPO_19 | 105 | 11 | 3 | 91 | 0,837 | 92,48% |
| Lab | CPO_19 | 105 | 11 | 1 | 93 | 0,964 | 92,48% |
| Hợp | CPO_19 | 105 | 11 | 1 | 93 | 0,965 | 92,48% |
| Unihan | CPO_20 | 92 | 22 | 8 | 62 | 0,778 | không chạy |
| Lab | CPO_20 | 92 | 22 | 3 | 67 | 0,918 | không chạy |
| Hợp | CPO_20 | 92 | 22 | 3 | 67 | 0,918 | không chạy |

Độ phủ chữ Hán: Unihan 84,4% / 85,2% / 80,2% (CPO_18/19/20, đều dưới ngưỡng cảnh báo 90%); Lab 100% cả ba.
**Đề xuất:** cấu hình **hợp** (Unihan + Lab + tập chữ tương tự). Nó cho điểm cao nhất, ít `low_score` nhất, đưa
CPO_20 qua mức 70% bead hợp lệ, và giữ được độ phủ Unihan cũ. match_rate sau tool Lab không đổi vì cách ghép cặp
giống nhau trên mẫu này. Bật bằng: `--opt reading_dict=resources/han_viet.tsv --opt
lab_translation_dict=third_party/SN-QN-alignment/dict/QuocNgu_SinoNom_TongHop3.csv --opt
lab_similarity_dict=third_party/SN-QN-alignment/dict/SinoNom_Similar_Dic_v2.csv`. Chưa đổi mặc định; bạn quyết.

## 6. Quyết định tôi tự chọn thay vì hỏi

1. **Commit 3 sửa đổi của lượt trước** làm commit đầu nhánh (`fd2bcca`), vì các bước sau dựa trên chúng.
2. **Nối hai luồng bằng cách trộn theo vị trí và chạy máy trạng thái unified** thay vì dùng lại đường đi hai luồng
   kiểu chi_tra. Đường kia chia dòng Việt vào bài theo tỉ lệ trên trang, dễ sai khi một trang có hai bài; còn config
   CPO_19/20 viết cho chế độ unified. Chế độ luồng chỉ bật qua config, mặc định tắt.
3. **Trộn theo mép trên bbox**, không theo tâm. Qwen gộp nhiều dòng in thành một hộp cao.
4. **Mỗi khối Hán ≥ 2 dòng sau phần Việt mở một tài liệu mới** (mặc định ở chế độ luồng), thay cho mẫu tiêu đề vốn
   vỡ vì lỗi OCR. Khối 1 dòng (trích trong chú thích) không mở tài liệu. Cả hai đều có tuỳ chọn tắt trong config.
5. **Chuyển lớp ngầm khi thiếu tiêu đề mục**: Hán → âm; âm → nghĩa khi gặp tiêu đề viết hoa. Luật "chú thích sau
   dòng nguồn" là tuỳ chọn, mặc định tắt, chỉ bật cho CPO_20, vì CPO_18 dùng `*` với nghĩa khác.
6. **`--split auto` cho lớp `am`**: đổi mặc định của lớp này, có test; ngưỡng `auto` của lớp Hán giữ nguyên.
7. **han.xml lưu thêm `RAW_TEXT`** để segmentation nhận tiêu đề có số thứ tự.
8. **Cài `XlsxWriter` vào `my_nlp` (`--no-deps`)**. Gói có trong danh sách thư viện bắt buộc của spec, thuần
   Python, không ghi đè gói nào; thiếu nó thì không chạy được tool. Đồng thời thêm vào `requirements.txt`.
9. **Đường hồi quy `--input-csv` dùng cột Fallback** nếu CSV có dữ liệu ở cột đó (như sample-data), để khớp chạy tay.
   Dữ liệu corpus luôn chạy với `fallback_column: null` như spec.
10. **XLSX bàn giao do wrapper tạo** (sheet `DONG_HANG`, cùng nội dung TSV), vì XLSX của tool dùng sheet
    "Alignment Results".
11. Không bật `ocr_streams` trong config thật. Chỉ thêm `notes_after_attribution` cho CPO_20, khoá này không có tác
    dụng khi chưa bật luồng.

## 7. Việc cần làm khi quay lại

1. **Quyết định bật chế độ luồng** cho CPO_19 (đạt cổng) và CPO_18 (đạt cổng; chế độ cũ chi_tra.xml vẫn nguyên):
   thêm `"ocr_streams": "han_vie"` vào khối `segmentation` của config, rồi chạy
   `python -m src.pipeline --config configs/CPO_19.json --steps layers,segment,clean,sentence,validate,align,export`
   (môi trường `my_nlp`). Cần han.xml phủ đủ trang: CPO_19 hiện có paddlevl 15–34; CPO_20 có khoảng 5–102; còn lại
   phải chạy `python -m src.ocr.ocr --config ... --layer han --engines paddlevl --pages ...` trong `.venv-paddle`.
2. **CPO_20**: chọn cách xử lý chênh lệch Hán có `。` theo cặp câu và âm theo dòng (gợi ý ở mục 2), hoặc chấp nhận
   1-2 bead.
3. **Chọn cấu hình từ điển cho `levenshtein`** (đề xuất: hợp; mục 5).
4. **Câu hỏi mở theo spec**: file nộp cuối cho dịch âm là output của tool Lab (`07_export/han-am/`) hay gộp vào file
   chung Hán–Việt?
5. **Giấy phép**: có được commit code `third_party/SN-QN-alignment/` không (hiện để untracked; `dict/` đã gitignore)?
6. **Ghi chú về working tree**: trước khi tôi bắt đầu, working tree đã có các file `data/CPO_18/04_segmentation`,
   `05_cleaning`, `06_alignment`, `07_export` ở trạng thái bị xoá, và `src.zip` bị xoá. Tôi không tạo ra và không đụng
   tới chúng. Hãy kiểm tra xem đó có phải ý bạn không.
7. Môi trường: chạy Qwen cần `HF_HUB_OFFLINE=1` (lỗi SSL tới huggingface.co); chạy pipeline cần `conda activate my_nlp`
   để có Poppler trong PATH.
8. (Tuỳ chọn) xoá chỉ mục khảo dị `(a)` trong lớp am (mục 3).
