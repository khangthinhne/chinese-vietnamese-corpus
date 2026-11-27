# Segment + clean cấu trúc từ hai luồng han/vie (2026-11-26)

Nhánh `feat/merge-member-ocr`. OCR thô của cả 3 work đã đủ (CPO_18 205–544, đã bỏ 29 trang ảnh chụp thủ bản;
CPO_19 5–833; CPO_20 5–320,622–806). Thứ tự chạy: segment (04) → clean cấu trúc (05). Bước tách câu chưa chạy.

## Config
- CPO_18, CPO_19: bật `segmentation.ocr_streams = "han_vie"` (CPO_20 đã bật từ trước).
- `common.pages` (phạm vi của segmentation) trước đây còn là giá trị demo: CPO_18 `205-206`, CPO_19 `15-16`. Nay
  CPO_18 dùng cùng phạm vi với `ocr.streams.pages` (không gồm trang ảnh chụp), CPO_19 dùng `5-833`.
- Bản sao lưu của 04_segmentation/05_cleaning trước khi ghi đè: `dist/backup_seg_20261126/`.

## Segmentation

| | Tài liệu | Dòng han | am | nghia | tho | Review |
|---|---|---|---|---|---|---|
| CPO_18 | 83 | 1288 | 1480 | 869 | 20 | 795 (unassigned 719, implicit 46, missing_heading 22) |
| CPO_19 | 278 | 4872 | 3474 | 3933 | 667 | 1937 (unassigned 1697, missing_start_heading 104, implicit 89) |
| CPO_20 | 221 | 2846 | 3133 | 2731 | 1456 | 361 (implicit 187, unassigned 166) |

- `unassigned_line` là dòng không thuộc 4 lớp: lời dẫn, tiêu đề trang, chú thích (vd "Phần II: Nguyên văn…",
  "(1) Chí thánh : đây chỉ Phật"). Đây là bình thường.
- Các tài liệu cần soát:
  - Có Hán nhưng không có âm: CPO_18.009 (tr.283–285); CPO_19.087, .101, .127, .198; CPO_20.150, .154.
  - Có âm nhưng không có Hán: CPO_19.120 (tr.258–266).
- CPO_19 có 278 tài liệu so với 225 tiêu đề Hán đánh số. Phần chênh là các bài con `其一/其二…` và tài liệu mở
  theo khối Hán.

## Clean cấu trúc (`layers → structural`)

| | han | am | nghia | tho |
|---|---|---|---|---|
| CPO_18 | 1288 → 1267 | 1480 → 1145 | 869 → 712 | 20 → 17 |
| CPO_19 | 4872 → 4819 | 3474 → 3262 | 3933 → 3684 | 667 → 588 |
| CPO_20 | 2846 → 2841 | 3133 → 2900 | 2731 → 2622 | 1456 → 1320 |

Vấn đề `audit_clean` báo ở chặng clean:
- Tiêu đề chạy còn sót:
  - CPO_19 han: `又一道`, `呈福大人`, `欽惟大皇帝陛下`;
  - CPO_19 am, nghia: tương tự;
  - CPO_20 am: `quân bất kiến`, …
  - Nhiều khả năng đây là tiêu đề bài lặp ở vài trang liền nhau, cần soát lại trước khi thêm luật loại bỏ.
- CPO_19 còn chỉ mục chữ cái `(a)/(b)`: am 37 câu, nghia 2, tho 3.

Các báo "structural→sentences mất ~100%", "ID không tồn tại", "số bị mất" đều do bước `sentence` chưa chạy lại,
nên audit đang so với output cũ hoặc rỗng. Chạy `--steps sentence` xong thì audit lại.

## Bước tiếp
`python -m src.pipeline --config configs/CPO_XX.json --steps sentence,validate --force`, rồi `audit_clean`, rồi align/export.


## Cập nhật tối 26/11: sửa theo góp ý + chạy hết pipeline

Sửa (chưa commit, `pytest` 413 passed):
- Bỏ CPO_19 tr.258–266 (toàn tiếng Việt): `common.pages`, `ocr.streams.pages`, `clean.common.excluded_pages`.
- Chữ khuyết `□`:
  - giữ lại trong `han.xml` (trước đây bị xoá; dòng `□□□□，` mất hẳn);
  - đổi thành `[MASK]` ở bước làm sạch câu Hán;
  - các bộ lọc "đủ chữ Hán" tính `[MASK]` là 1 chữ.
- Tài liệu có Hán mà thiếu âm:
  - tiêu đề chạy OCR sai dấu (`ĐÔ`/`ĐỒ`, `NHÂN`/`NHẬM`) nay vẫn được nhận là tiêu đề chạy, không cắt đôi bài sang trang
    (CPO_19.087/.127/.198);
  - dòng Việt có mép trên lọt vào kẽ một khối Hán liền được đặt sau khối đó (CPO_20.150 "Phiên âm:");
  - dòng Việt 0–1 chữ cái (rác "E") không làm đứt khối Hán;
  - dòng pinyin của Qwen bị loại ở `vie.xml` (CPO_19.101: tr.200/218/242/334).
  - Còn CPO_20.154: đúng là chỉ có dịch nghĩa.
- Lời tự nói tiếng Việt của Qwen bị loại ("Phía trên của ảnh…", "Dựa trên thông tin được cung cấp…"): CPO_18 tr.341,
  CPO_20 tr.25/47.
- `validate`: đọc ID dòng của han.xml/vie.xml. Trước đây gần 60.000 cảnh báo "source_id không tồn tại" là báo nhầm.

### Kết quả export (`07_export`)

| | Tài liệu | han-am | han-nghia | han-tho | gộp |
|---|---|---|---|---|---|
| CPO_18 | 78 | 1035 | 939 | 7 | 1275 |
| CPO_19 | 271 | 3495 | 3035 | 571 | 3781 |
| CPO_20 | 222 | 2235 | 1347 | 1047 | 2507 |

- `.tsv` và `.xlsx` khớp nhau. xlsx có thêm dòng tiêu đề trong sheet `DONG_HANG`; file gộp 4 cột dùng quote kiểu CSV.
- `validate`: 0 lỗi. Cảnh báo chỉ còn câu trùng lặp trong cùng tài liệu.

### Tool Lab dóng Hán–Âm (`07_export/han-am/`)
- CPO_19: khớp 92,6% vị trí chữ.
- CPO_20: khớp 54,7%.
  - Trung vị theo dòng là 0,86, nhưng 230/2235 dòng dưới 0,3.
  - Các dòng tệ dồn vào các bài văn xuôi (chiếu, biểu, văn tế) của tài liệu 047–053 (tr.97–119), 124–128 (tr.268–279),
    146, 155. Lớp "âm" của các bài này thực ra là dịch nghĩa hoặc chú thích: không có tiêu đề PHIÊN ÂM nên phần Việt
    đứng sau Hán bị gán ngầm là âm. Cần người xác nhận các bài này có phiên âm hay không.
- CPO_18: tool dừng vì 461/1496 cặp (31%) chưa dóng được, vượt ngưỡng 30%. Phần lớn ở các bài văn bia liệt kê tên thí
  chủ (CPO_18.017, .010, .009, .015).

### Còn cần soát tay
- Rác OCR:
  - CPO_20 tr.5 `脳瑳浬汎呾祣 / 暗倭倖仅共臂`;
  - CPO_18 tr.360 `n=4 a=0/16…`.
- Tiêu đề chạy còn sót: CPO_19 `又一道`, `欽惟大皇帝陛下`; CPO_20 `quân bất kiến`.
- Ngoặc lệch ở CPO_18 am/nghia.
