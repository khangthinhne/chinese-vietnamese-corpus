# CPO_20: sửa paddlevl thừa dòng + Qwen trang thưa mực, check đến 0 lỗi (2026-11-26)

Phạm vi `5-320,622-806` (501 trang). Code trên nhánh `fix/clean-round2` (worktree), **chưa commit**.
OCR lại trên `data/` thật bằng `--force-reocr`: chỉ thêm run mới, raw cũ giữ nguyên
(sao lưu manifest + raw trước khi chạy ở scratchpad `c5/backup_20261126_110209`, `c5/backup2_20261126_111104`).
GPU tuần tự (paddlevl rồi Qwen), không chạy hai mô hình cùng lúc.

## Kết quả check_ocr_complete

| | Trước | Sau |
|---|---|---|
| Lỗi | 2 (`han_degenerate` P0631, `no_text` P0692) | **0 → ĐỦ** |
| Cảnh báo | 38: han_many_lines 28, han_long_line 2, vie_low_conf 6, vie_run_no_lines 1, outside_range 1 | 34: han_many_lines 27, vie_low_conf 6, outside_range 1 |
| Dòng Hán trong han.xml | 3299 | 3070 |
| Trang có dòng Việt | 462 | 463 (thêm P0692) |

27 cảnh báo `han_many_lines` còn lại đã soát: ở cả 27 trang, số dòng Hán = số hàng chữ đo trên ảnh (±1, phần lệch là
dòng Việt/tiêu đề), không còn đơn vị nào ra nhiều dòng. Đó là văn xuôi Hán dày thật (23–33 hàng); cảnh báo chỉ vì
heuristic so với trung vị 9 dòng/trang của cả work. `outside_range` là 321–621 đã loại khỏi phạm vi.

## 1. Nguyên nhân thừa dòng (6 trang được hỏi)

Hàng thật đếm bằng hình chiếu mực theo y (sau khi chẻ theo bước hàng + chỉnh nghiêng), so với run cũ:

| Trang | Hàng thật | Đơn vị cũ | Dòng cũ | Nguyên nhân |
|---|---|---|---|---|
| 663 | 31 | 13 | 124 | nhiều hàng dính một đơn vị, trang nghiêng −1.4° |
| 645 | 33 | 18 | 71 | như trên, nghiêng −1.5° |
| 097 | 22 | 8 | 63 | như trên, nghiêng 1.3° |
| 270 | 28 | 3 | 57 | như trên (đơn vị 1 cao 469px = 8 hàng) |
| 631 | 16 Hán | 36 | 38 | không thừa dòng: 1 lát ảnh 23px giữa 2 hàng đọc ra `口 + — — —` (2 hàng thật đọc đủ ở đơn vị riêng) |
| 679 | 0 Hán | 29 | 29 | không thừa dòng: trang tiếng Việt, dòng Hán là lời tự nói "作为一个人工智能语言模型…" |

Cơ chế (663, 645, 097, 270): văn xuôi in lưới dày có dấu câu/hạt nhiễu nằm vắt qua kẽ hàng, nên kẽ hàng còn
10–13% mực, cao hơn ngưỡng "trắng" của `split_rows`. Chẻ dải cao theo trung vị chiều cao dải (adapter 5) không chạy,
vì phần lớn dải đã dính 3 hàng (trung vị ~190px). Model nhận một LƯỚI nhiều hàng và **đọc theo cột**: tr.270 đơn vị 1
ra `宮之偏場化入一` = cột phải ngoài cùng, trong khi đọc ngang trái→phải thì có nghĩa
("乂安土音正濁…", "宮為土。土無正位，無成性。四行皆可寄旺…"). Chế độ `horizontal` giữ nguyên.

Không phải dấu câu/chữ chú thành dòng riêng. Có thêm nguyên nhân thứ hai, nhỏ hơn: **model xuống dòng sau từng
chữ** trong một hàng in thưa (tr.268 đơn vị 3 `熟\n然\n冬\n夏…` đúng thứ tự nhưng mỗi chữ một dòng; tr.14 đơn vị 7
câu thất ngôn `玄/麻/蛋/副/璽/書/來` thành 7 dòng).

## 2. Sửa (paddlevl `ADAPTER_VERSION` 5 → 7, `describe()` có tham số mới)

- `row_pitch`: bước hàng = đỉnh tự tương quan của hình chiếu mực (58–67px ở văn xuôi 300 dpi).
- `split_by_pitch`: dải cao > 1.6 × bước hàng được chẻ tại khe ít mực nhất gần vị trí kỳ vọng, chỉ khi khe đủ sâu
  (≤ 0.5 × trung vị mực của dải) nên chữ tiêu đề cỡ lớn (mực liền) không bị chém.
- `estimate_skew` + xoay trang: CHỈ khi còn dải nhiều hàng sau bước trên (trang nghiêng 1.3–1.7° làm kẽ hàng mờ). Hộp
  ghi vào raw được ánh xạ ngược về toạ độ trang gốc (`rotate_box_back`); góc ghi ở `raw.deskew_deg`.
- `char_per_line`: đơn vị ngang có ≥ 3 dòng, mỗi dòng 1–2 chữ Hán, không có chữ Latin → nối thành một dòng, cờ
  `char_lines_joined`.
- `plan_units`: tách phần chọn vùng thành hàm thuần (test và script so sánh dùng đúng logic của adapter).

Trang thơ lưới ngang đang đúng: so vùng cũ/mới trên mọi trang có raw paddlevl (CPO_18 8 trang, CPO_19 109, CPO_20 802),
**CPO_19 tr.53 và CPO_20 tr.5–24 không đổi vùng nào**. Chỉ việc nối dòng từng chữ chạm tới 1 đơn vị ở CPO_20 tr.14
(câu thơ đang bị tách 7 dòng → nay 1 dòng) và 1 đơn vị ở CPO_19 tr.53 (xem mục 3).

Test (ảnh tổng hợp nhỏ): `tests/test_paddlevl_rows.py`: lưới có dấu câu vắt kẽ → mỗi hàng một đơn vị; hàng sạch
không đổi; khối mực liền cao 2 bước hàng không bị cắt; lưới nghiêng 1.5° được xoay và đủ 8 hàng; ánh xạ hộp ngược;
không xoay khi đã tách đúng; nhận dạng xuống dòng từng chữ; khoá cache.

## 3. Trang đã OCR lại

Tìm bằng tiêu chí mục 1 trên cả 3 work (raw có đơn vị cao > 1.6 × bước hàng mà ra > 1 dòng; vùng đổi theo code mới;
đơn vị xuống dòng từng chữ):

- CPO_18: 0 trang. CPO_19: chỉ tr.53 (1 đơn vị xuống dòng từng chữ), **chưa chạy lại**: CPO_19 đã giao tv2, chạy lại ở
  máy này sẽ thêm run khác nội dung vào raw seed và gây xung đột khi gộp gói. Thành viên dùng code mới (adapter 7) thì
  tự đúng.
- CPO_20 (trong phạm vi): **29 trang paddlevl** (30 lượt; tr.268 chạy 2 lần: adapter 6 rồi 7):
  - vùng đổi (adapter 6): 85, 87, 97, 103, 105, 107, 113, 268, 270, 272, 274, 276, 645, 663;
  - xuống dòng từng chữ (adapter 7): 14, 36, 40, 68, 90, 93, 160, 175, 178, 228, 239, 256, 259, 268, 280, 294.
  - 85/87: chỉ thêm 1 vùng, là 2 dòng tiếng Việt từng dính nhau (đã xem ảnh).
  - 22 trang thuộc 321–621 cũng đổi vùng nhưng nằm ngoài phạm vi: không chạy.

| Trang | Đơn vị cũ | Dòng cũ | Đơn vị mới | Dòng mới | Nghiêng | Dòng Hán (han.xml) |
|---|---|---|---|---|---|---|
| 97 | 8 | 63 | 22 | 22 | 1.3 | 21 |
| 107 | 15 | 20 | 35 | 35 | 1.3 | 0 (trang Việt) |
| 268 | 16 | 26 | 28 | 28 | 1.7 | 25 |
| 270 | 3 | 57 | 28 | 28 | 0 | 28 |
| 272 | 9 | 20 | 30 | 30 | 0 | 16 |
| 276 | 14 | 16 | 23 | 22 | 1.6 | 0 (trang Việt) |
| 645 | 18 | 71 | 33 | 33 | −1.5 | 33 |
| 663 | 13 | 124 | 31 | 31 | −1.4 | 31 |
| 14 | 23 | 28 | 23 | 22 | 0 | 9 |

Soát 5 hàng đầu với ảnh: 663 "余從而問其事，則歷歷如雲遊之所記 / 盛晚循環，故事成淵…", 270 "又安土音正濁。然效他聲音皆可能…",
268 "又安天氣和暢雖六陰節候盛陽常自如稼穡 / 有二熟十一月立苗夏四月熟…" đều đúng thứ tự đọc ngang.

## 4. Trang 692 (Qwen rỗng)

Nguyên nhân: trang 8 dòng dịch thơ chỉ ~1.4% điểm mực; phân vị 2 = phân vị 98 = 255 nên `ink_mask` của Qwen coi là
trang trắng, không cắt dải nào (0 giây). Chạy lại hay đổi `QWEN_BAND_INCHES` không giúp. Sửa như paddlevl adapter 5
(lấy phân vị 0.1 làm mức mực), Qwen `ADAPTER_VERSION` 3 → 4. Chạy lại trong my_nlp: 4 dải, 12 dòng ("Dịch thơ:", 8 câu,
"Thạch Can dịch"). Không cần phương án dự phòng bằng dòng Latin của paddlevl. Quét cả 3 work: chỉ trang 692 dính lỗi này.

## 5. Lời tự nói của mô hình + checker

- `build_layers` loại (lớp han): 人工智能, 语言模型/語言模型, 抱歉, 作为一个, 我还没, 原文本, 识别…, và "as an AI",
  "language model"; lớp vie thêm "as an AI", "language model", "I'm sorry" (chỉ dòng không có chữ Việt có dấu).
- Checker: dòng như vậy còn trong `han.xml`/`vie.xml` → `[error] han_model_chatter`/`vie_model_chatter`.
- "In lặp" cảnh báo: một nguyên nhân bị báo nhiều dòng. Nay gộp theo (trang, mức, mã) và bỏ cảnh báo đã bị lỗi cùng
  trang bao hàm (`no_text` ⊃ `*_run_no_lines`, `han_model_chatter` ⊃ `han_long_line`).
- Lát ảnh mỏng tr.631: dòng ≤ 2 chữ Hán + ≥ 6 dấu gạch bị loại (`rule_junk`); đơn vị lặp chỉ là lỗi khi có ≥ 3 chữ Hán.

## Kiểm tra

- `pytest tests` (trừ `test_paddle_engine.py` vốn lỗi từ trước): 400 passed, 1 skipped.
- Chưa chạy lại pipeline (segment → export) trên data thật sau lần OCR lại này.
- Chưa commit, không push.
