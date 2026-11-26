# Audit segmentation (tài liệu → lớp → câu), 2026-11-26

Nhánh `audit/segmentation`, tạo từ `feat/merge-member-ocr`.
- `feat/ocr-complete` và `fix/clean-round2` đã nằm sẵn trong lịch sử, không cần gộp.
- Các sửa đổi của lượt trước (gộp luồng, pinyin, `[MASK]`, validate) vẫn **chưa commit**: commit bị chặn, xem
  `reports/blocked_actions.md`. Audit đo trên cây làm việc gồm các sửa đổi đó.

Mọi phép đo chạy trên **bản sao** trong scratchpad (`scratchpad/audit/`), không dùng GPU.
- Config bản sao bật `ocr_streams = han_vie` cùng các thiết lập đã duyệt: CPO_20 bật note_entries và bỏ 321–621;
  CPO_19 có translator_names, bỏ 5–13 và 258–266; CPO_18 bỏ 29 trang ảnh chụp thủ bản.
- `data/` thật không bị ghi: số dòng `git status data` trước và sau đều là 216.

Script đo: `scripts/audit_segmentation.py`, test `tests/test_audit_segmentation.py` (11 test). Chạy được cho mọi work:
`python scripts/audit_segmentation.py --work-id CPO_20 [--data-root …] [--config …] [--json out.json]`.

## 0. Độ phủ và thời gian

| | Trang đã OCR (Hán + Việt) | Dòng han / vie | Ghi chú |
|---|---|---|---|
| CPO_18 | 307 trang trong 205–544 | 1351 / 3537 | gói tv1; bỏ 29 trang ảnh chụp thủ bản |
| CPO_19 | 820 trang trong 5–833 | 5189 / 11438 | gói tv2 (5–623) + gói cpo19_2 (623–833); bỏ 258–266; 834–850 chưa OCR |
| CPO_20 | 501 trang (5–320, 622–806) | 3070 / 8353 | đầy đủ theo phạm vi đã chốt |

Thời gian từng bước trên bản sao (giây):

| | build_layers | segment | clean | sentence | align (proxy) |
|---|---|---|---|---|---|
| CPO_18 | 11,1 | 2,0 | 6,3 | 1,3 | khoảng 3 phút |
| CPO_19 | 33,1 | 3,3 | 19,1 | 2,0 | khoảng 25 phút (máy bận) |
| CPO_20 | 9,7 | 2,4 | 18,4 | 1,5 | khoảng 4 phút |

## 1. Chỉ số theo work (mục 2–4)

### Ranh giới tài liệu

| | Tài liệu | Dòng/tài liệu (trung vị) | Quá dài (> 5× trung vị) | Quá ngắn (≤ 2 dòng) | Hán không âm | Âm không Hán | Không nghia | Lớp xuất hiện 2 lần |
|---|---|---|---|---|---|---|---|---|
| CPO_18 | 78 | 29 | 3 (.009, .013, .016) | 0 | 0 | 0 | 17 | 0 |
| CPO_19 | 271 | 35 | 4 (.119, .251, .260, .265) | 0 | 0 | 0 | 8 | 12 (khối Hán thứ hai ở cuối) |
| CPO_20 | 222 | 34 | 7 (.053, .123, .145, .153, .164, .185, .186) | 0 | 1 (.154, đúng: chỉ có nghĩa) | 0 | 25 (optional) | 4 |

- Chỉ số "Hán không âm = 0" của CPO_18 **che giấu một lỗi**: âm có mặt, nhưng là âm của bài bên cạnh (xem lỗi dây
  chuyền ở mục 3).
- **Mục lục:** không có trang mục lục nào trong phần đã OCR. Mục lục của cả ba sách nằm ở cuối sách: CPO_18 sau
  544, CPO_19 sau 833, CPO_20 sau 806. Vì vậy chưa đối chiếu được. Script đã có bước đối chiếu (có test) và sẽ chạy
  khi OCR thêm các trang đó.
  - Lưu ý: số trang in lệch số trang PDF **không phải hằng số**. Ở CPO_18, tr.234 in là 236 (lệch −2), còn tr.331,
    444, 499 in là 305, 412, 467 (lệch +26/+32/+32), vì có các trang ảnh và trang chèn. Khi đối chiếu phải ước
    lượng độ lệch theo từng đoạn.

### Kế toán dòng (mỗi dòng OCR đi tới đúng 1 nơi)

| | Lớp | Tiêu đề mục | Metadata | Unassigned (có lý do) | **Mất dấu** (han/vie) | Hai nơi |
|---|---|---|---|---|---|---|
| CPO_18 | 3655 | 167 | 146 | 723 | **196** (24/172) | 1 |
| CPO_19 | 12845 | 817 | 1013 | 1694 | **258** (23/235) | 0 |
| CPO_20 | 10196 | 348 | 659 | 179 | **38** (14/24) | 3 |

Phân loại dòng "mất dấu":
- Tiêu đề chạy và dòng nằm ngoài mọi tài liệu (CPO_18: 118/196), bị bỏ mà không ghi gì.
- Dòng bị loại "khác luồng" sau khi gán lớp. Segmentation chỉ ghi **số đếm** vào log (CPO_20: han 75, am 11, nghia
  4), không ghi từng dòng.
- **Mất nội dung thật:** 17 câu Hán của bài văn bia CPO_18 tr.286 (`黎皇靈鬱，四海攸同。…`).
- Một phần nhỏ là giới hạn của audit: tiêu đề bài con `(kỳ nhất)` vào metadata nhưng không so khớp được.

### Rò rỉ trong thân lớp

Số dòng, đếm ở `04_segmentation/layers` → sau bước clean cấu trúc (`05_cleaning/structural`):

| | am | nghia | tho | han |
|---|---|---|---|---|
| CPO_18 | 208 → 41 | 160 → 35 | 2 → 1 | 4 → 2 |
| CPO_19 | 269 → 60 | 476 → 242 | 128 → 8 | 27 → 27 |
| CPO_20 | 520 → **279** | 143 → 35 | 159 → 24 | 0 → 0 |

Theo loại, sau clean:
- **CPO_20 am:** 112 dòng là mục chú thích `1. Nguyên dẫn: …`; 144 dòng còn chữ số chỉ mục chú thích trong câu
  (`Cao sơn lưu thủy 2 hữu tri âm`, vì Qwen chép chỉ số trên thành chữ số thường).
- **CPO_19 nghia:** 157 dòng có chữ số chỉ mục, một phần là báo nhầm (`Bài 2`). 50 dòng là mục chú thích.
- **Tiêu đề mục OCR sai không được nhận ra**, nằm trong thân lớp:
  - CPO_19: `DỊCH THOẠI:` ×5 (.027, .118, .133, .155, .171), `DỊCH NHĨA:`, `CHÚ TÍCH:`;
  - CPO_20: `Dịch phú:`;
  - CPO_18: `DỊCH :`.
  - Hệ quả: thơ dịch của các bài đó nằm trong lớp nghĩa.
- **Tên người dịch, dòng nguồn trong thân lớp:**
  - "Ngô Linh Ngọc dịch", "Hoàng Tạo dịch" (CPO_20 tho/nghia);
  - "Trích Dật thi lược toản" (CPO_20 am);
  - "Lâm Giang" (CPO_19 tho, 42 dòng ở 04; clean loại gần hết).
- **Chữ Hán trong lớp Việt / Latin trong lớp Hán:** 0 (bộ lọc luồng làm tốt).

### Hán ↔ âm (kiểm tra mạnh nhất)

| | Tài liệu có cả hai | Khớp tuyệt đối | Khớp ±10% | Lệch > 10% | Thơ: khớp mọi dòng |
|---|---|---|---|---|---|
| CPO_18 | 78 | 23% | 58% | 33 | 3/3 |
| CPO_19 | 271 | 39% | **87%** | 34 | 88/125 |
| CPO_20 | 219 | 41% | 74% | 56 | 89/124 |

Sau clean cấu trúc: CPO_18 26% / 61%, CPO_19 44% / 86%, CPO_20 46% / 74%.

- Với văn xuôi, "khớp tuyệt đối" thấp là bình thường: OCR thêm/bớt vài chữ, có chú thích trong ngoặc.
- Nhóm "lệch > 10%" mới là nhóm cần xem, và gần như trùng với danh sách nghi lỗi ở mục 2.

### Thơ dịch, nghĩa

- **Thể thơ (tho):**
  - CPO_19: thất ngôn 51, ngũ ngôn 3, hỗn hợp 3. Chỉ 1 dòng lạ.
  - CPO_20: thất ngôn 65, lục bát 16, ngũ ngôn 5, hỗn hợp 24. 56 dòng lạ: chủ yếu là mục chú thích trong lớp tho,
    tiểu sử tác giả (tr.233, 52–80 âm tiết/dòng), `Số V/09728`.
  - CPO_18: chỉ 2 bài thơ dịch.
- **Nghĩa:** không có tài liệu nào có tiêu đề DỊCH NGHĨA mà lớp nghĩa rỗng.
  - Tỉ lệ âm tiết nghĩa / chữ Hán (trung vị): CPO_18 1,36, CPO_19 1,23, CPO_20 1,48.
  - Số tài liệu ngoại lai: 12 / 24 / 7.

### Tách câu

| | Câu han / am / nghia / tho | Han rất dài (> max(40, 3× trung vị)) | Việt rất ngắn (≤ 2 âm tiết) | Kết thúc ở chữ viết tắt | Ngoặc kép lệch |
|---|---|---|---|---|---|
| CPO_18 | 1274 / 1401 / 1193 / 17 | 31 | 21 | 12 | 135 |
| CPO_19 | 3780 / 3970 / 3426 / 587 | 289 (phần lớn là văn xuôi dài thật) | 80 | 16 | 56 |
| CPO_20 | 2506 / 3018 / 1469 / 1320 | 57 | 34 | 11 | 25 |

Chế độ tách (dựng lại bằng chính `choose_han_split` / `choose_am_split`):
- CPO_20: han line 184 / punct 38, am line 193 / punct 26.
- CPO_19: hai lớp trùng nhau (line 165 / punct 106).
- CPO_18: chủ yếu punct.

Số câu han và am lệch > 30%:

| | Tài liệu | do khác chế độ tách | lệch 2:1 | lệch nội dung |
|---|---|---|---|---|
| CPO_18 | 29 | 11 | 5 | 13 |
| CPO_19 | 28 | 14 | 5 | 9 |
| CPO_20 | 52 | **31** | 8 | 13 |

- Câu vượt ranh giới tài liệu: 0.
- Câu ghép xuyên trang: 126 / 244 / 29. Lấy mẫu 8 câu/work trong JSON; các câu đã xem nối đúng (văn xuôi qua trang).

### Proxy hạ nguồn: bead han-am hợp lệ

Bead hợp lệ là bead không phải 0-1 / 1-0 và có score ≥ 0,5.

| | Bead hợp lệ | Trung vị theo tài liệu |
|---|---|---|
| CPO_18 | 1002/1496 (67%) | 0,91 |
| CPO_19 | 3433/3920 (88%) | 1,00 |
| CPO_20 | 1968/2781 (71%) | 0,89 |

## 2. Tài liệu nghi lỗi (top 20 mỗi work)

Điểm = thiếu lớp 3 + thứ tự lớp lạ 2 + lệch Hán–âm (tối đa 3) + lệch số câu 1 + (1 − tỉ lệ bead) × 3 + rò rỉ (tối
đa 2). Cột trang là trang PDF, dùng để mở ảnh trong `02_extraction/pages`.

### CPO_18
| Tài liệu | Điểm | Trang | Dấu hiệu |
|---|---|---|---|
| CPO_18.010 | 8.94 | 286–293 | han_am_diff=0.979, sentence_count, bead_rate=0.021, leak=36 |
| CPO_18.005 | 8.5 | 224–234 | han_am_diff=0.996, sentence_count, bead_rate=0.0, leak=6 |
| CPO_18.007 | 7.87 | 235–241 | han_am_diff=0.959, sentence_count, bead_rate=0.043, leak=4 |
| CPO_18.028 | 7.75 | 388–396 | han_am_diff=0.997, sentence_count, bead_rate=0.0, leak=3 |
| CPO_18.073 | 7.75 | 529–531 | han_am_diff=0.976, sentence_count, bead_rate=0.0, leak=3 |
| CPO_18.009 | 7.5 | 283–286 | han_am_diff=0.665, sentence_count, bead_rate=0.0, leak=2 |
| CPO_18.074 | 7.5 | 531–533 | han_am_diff=0.727, sentence_count, bead_rate=0.0, leak=2 |
| CPO_18.045 | 7.1 | 454–459 | han_am_diff=0.779, sentence_count, bead_rate=0.465, leak=6 |
| CPO_18.069 | 7.03 | 518–521 | han_am_diff=0.698, sentence_count, bead_rate=0.407, leak=5 |
| CPO_18.002 | 7.0 | 208 | han_am_diff=0.983, sentence_count, bead_rate=0.0 |
| CPO_18.006 | 7.0 | 234–235 | han_am_diff=0.996, sentence_count, bead_rate=0.0 |
| CPO_18.015 | 7.0 | 337–339 | han_am_diff=0.976, sentence_count, bead_rate=0.0 |
| CPO_18.022 | 7.0 | 368–369 | han_am_diff=0.833, sentence_count, bead_rate=0.0 |
| CPO_18.016 | 6.98 | 339–357 | han_am_diff=0.352, sentence_count, bead_rate=0.378, leak=41 |
| CPO_18.023 | 6.93 | 369–372 | han_am_diff=0.889, sentence_count, bead_rate=0.19, leak=2 |
| CPO_18.039 | 6.84 | 434–436 | han_am_diff=0.642, sentence_count, bead_rate=0.385, leak=4 |
| CPO_18.051 | 6.57 | 475–479 | han_am_diff=0.776, sentence_count, bead_rate=0.476, leak=4 |
| CPO_18.053 | 6.53 | 482–485 | han_am_diff=0.62, sentence_count, bead_rate=0.489, leak=4 |
| CPO_18.065 | 6.25 | 508–510 | han_am_diff=0.654, sentence_count, bead_rate=0.5, leak=3 |
| CPO_18.049 | 6.1 | 469–472 | han_am_diff=0.601, sentence_count, bead_rate=0.465, leak=2 |

### CPO_19
| Tài liệu | Điểm | Trang | Dấu hiệu |
|---|---|---|---|
| CPO_19.205 | 8.93 | 515–519 | layer_order, han_am_diff=0.518, sentence_count, bead_rate=0.441, leak=5 |
| CPO_19.024 | 7.91 | 63–65 | han_am_diff=0.603, sentence_count, bead_rate=0.364, leak=8 |
| CPO_19.271 | 7.82 | 831–833 | han_am_diff=0.862, sentence_count, bead_rate=0.143, leak=5 |
| CPO_19.045 | 7.75 | 102–103 | han_am_diff=0.5, sentence_count, bead_rate=0.0, leak=3 |
| CPO_19.050 | 7.75 | 110–117 | han_am_diff=0.946, sentence_count, bead_rate=0.0, leak=3 |
| CPO_19.171 | 7.73 | 371–372 | han_am_diff=0.624, sentence_count, bead_rate=0.258, leak=6 |
| CPO_19.189 | 7.68 | 437–445 | han_am_diff=0.527, sentence_count, bead_rate=0.441, leak=11 |
| CPO_19.270 | 7.5 | 830–831 | han_am_diff=0.997, sentence_count, bead_rate=0.0, leak=2 |
| CPO_19.033 | 7.36 | 79–80 | han_am_diff=0.624, sentence_count, bead_rate=0.381, leak=6 |
| CPO_19.117 | 7.28 | 267–270 | han_am_diff=0.837, sentence_count, bead_rate=0.24, leak=4 |
| CPO_19.109 | 7.25 | 242–244 | han_am_diff=0.857, sentence_count, bead_rate=0.0, leak=1 |
| CPO_19.148 | 7.11 | 329–330 | han_am_diff=0.597, sentence_count, bead_rate=0.381, leak=5 |
| CPO_19.162 | 7.07 | 349–350 | han_am_diff=0.429, sentence_count, bead_rate=0.0, leak=2 |
| CPO_19.130 | 6.69 | 296–298 | han_am_diff=0.488, sentence_count, bead_rate=0.412, leak=4 |
| CPO_19.114 | 6.47 | 251–253 | han_am_diff=0.286, sentence_count, bead_rate=0.0, leak=3 |
| CPO_19.055 | 6.22 | 124–126 | han_am_diff=0.286, sentence_count, bead_rate=0.0, leak=2 |
| CPO_19.236 | 6.0 | 649–651 | han_am_diff=0.534, sentence_count, bead_rate=0.5, leak=2 |
| CPO_19.177 | 5.71 | 386–389 | han_am_diff=0.208, sentence_count, bead_rate=0.429, leak=7 |
| CPO_19.168 | 5.55 | 362–365 | han_am_diff=0.467, sentence_count, bead_rate=0.5, leak=1 |
| CPO_19.123 | 4.75 | 283–285 | sentence_count, bead_rate=0.167, leak=5 |

### CPO_20
| Tài liệu | Điểm | Trang | Dấu hiệu |
|---|---|---|---|
| CPO_20.053 | 11.0 | 118–135 | layer_order, han_am_diff=0.691, sentence_count, bead_rate=0.0, leak=157 |
| CPO_20.187 | 8.17 | 735–740 | han_am_diff=0.571, sentence_count, bead_rate=0.192, leak=7 |
| CPO_20.181 | 7.79 | 702–703 | han_am_diff=0.758, sentence_count, bead_rate=0.154, leak=5 |
| CPO_20.049 | 7.59 | 106–109 | han_am_diff=0.432, bead_rate=0.0, leak=12 |
| CPO_20.050 | 7.51 | 110–112 | han_am_diff=0.251, sentence_count, bead_rate=0.0, leak=8 |
| CPO_20.054 | 7.5 | 136–137 | han_am_diff=0.639, sentence_count, bead_rate=0.5, leak=11 |
| CPO_20.134 | 7.5 | 287–288 | han_am_diff=0.898, sentence_count, bead_rate=0.0, leak=2 |
| CPO_20.139 | 7.47 | 296–297 | han_am_diff=0.286, sentence_count, bead_rate=0.0, leak=7 |
| CPO_20.127 | 7.25 | 277–279 | han_am_diff=0.967, sentence_count, bead_rate=0.0, leak=1 |
| CPO_20.169 | 7.18 | 678–679 | han_am_diff=0.636, sentence_count, bead_rate=0.357, leak=5 |
| CPO_20.047 | 7.0 | 97–102 | han_am_diff=0.917, sentence_count, bead_rate=0.0 |
| CPO_20.205 | 7.0 | 772 | han_am_diff=0.905, sentence_count, bead_rate=0.0 |
| CPO_20.095 | 6.74 | 210–211 | han_am_diff=0.694, sentence_count, bead_rate=0.421, leak=4 |
| CPO_20.216 | 6.59 | 792–793 | han_am_diff=0.616, sentence_count, bead_rate=0.471, leak=4 |
| CPO_20.110 | 6.49 | 235–236 | han_am_diff=0.659, sentence_count, bead_rate=0.421, leak=3 |
| CPO_20.074 | 6.33 | 170–171 | han_am_diff=0.72, sentence_count, bead_rate=0.308, leak=1 |
| CPO_20.124 | 6.31 | 268–269 | han_am_diff=0.343, sentence_count, bead_rate=0.0, leak=1 |
| CPO_20.221 | 6.17 | 801–802 | han_am_diff=0.614, sentence_count, bead_rate=0.444, leak=2 |
| CPO_20.155 | 6.03 | 644–649 | han_am_diff=0.255, sentence_count, bead_rate=0.0, leak=2 |
| CPO_20.048 | 6.0 | 103–105 | sentence_count, bead_rate=0.0, leak=21 |

## 3. Rà bằng mắt

Mẫu theo seed 20261126 (`--sample 5`): mỗi work 5 tài liệu ngẫu nhiên và 5 tài liệu nghi vấn nhất. Danh sách trong
JSON của audit.

Phạm vi đã làm:
- Đã mở ảnh ít nhất một trang của 4 tài liệu ngẫu nhiên CPO_18 (.006, .014, .042, .061) và 4 tài liệu ngẫu nhiên
  CPO_20 (.009, .116/117, .124, .162), cùng các trang lỗi của 5 tài liệu nghi vấn (CPO_18 .005, .006, .009/.010;
  CPO_19 .101, .205; CPO_20 .124).
- Với mỗi trang, đối chiếu từng dòng với kết quả gán lớp (`scratchpad/audit/view.py`).
- Mẫu ngẫu nhiên CPO_19 chưa mở ảnh. Ảnh trang 122–622 không có ở máy này; với tài liệu .205, phần audit nêu dưới
  đây dựa trên văn bản các lớp, không dựa trên ảnh.
- Cỡ mẫu nhỏ, tỉ lệ chỉ để định hướng.

| Tài liệu (trang) | Ngẫu nhiên / nghi vấn | Lỗi thấy | Loại | Nguồn gốc |
|---|---|---|---|---|
| CPO_18.006 (234–235) | ngẫu nhiên | Hai dòng Hán cuối thư (chữ ký `弟子李淼謹呈`, nguồn `（弘明集卷十一…）`) sang tr.235 và bị dòng rác "Điều" ngăn khỏi khối Hán trước → mở tài liệu mới .007. **Phiên âm của .006 gắn sang .007**; lớp âm của .006 chỉ còn chữ "Điều". | ranh giới bài sai | segmentation (rác OCR kích hoạt) |
| CPO_18.014 (331) | ngẫu nhiên | Đúng. Dòng paddlevl tự sinh `2023年1月1日星期一` nằm ở unassigned. | — | OCR |
| CPO_18.042 (444) | ngẫu nhiên | Đúng. Số mục "11" không được OCR. | — | OCR |
| CPO_18.061 (499) | ngẫu nhiên | Đúng; tiêu đề chạy "Phụ lục" lọt vào lớp nghĩa của .060. | rò rỉ tiêu đề chạy | segmentation |
| CPO_18.005 (224–226) | nghi vấn | Dòng rác "of contradiction!" ngầm mở am. Tiêu đề chạy tính là nội dung. Tiêu đề "PHIÊN ÂM:" thật không đặt lại trạng thái, nên tiêu đề bài viết hoa ngay sau đó mở **nghia**. **Toàn bộ phiên âm vào lớp nghĩa.** | gán lớp sai | segmentation |
| CPO_18.009/.010 (285–287) | nghi vấn | Dòng rác "**N**" ngầm mở am. 17 câu Hán văn bia tr.286 bị gán vào am rồi bị loại "khác luồng": **mất 17 câu Hán**, không có dấu vết. Lớp am chứa 30 dòng rác 1–3 chữ cái do Qwen đọc trang chữ Hán. | mất dòng | segmentation + OCR |
| CPO_20.009 (19) | ngẫu nhiên | Đúng; `Ban vũ 2` (chữ số chỉ mục) và "Trích Dật thi lược toản" còn trong am. | rò rỉ chú thích | OCR + clean |
| CPO_20.117 (246) | ngẫu nhiên | Đúng. | — | — |
| CPO_20.124 (268) | ngẫu nhiên | Bài văn xuôi chỉ có **dịch nghĩa** ("KHÍ HẬU NGHỆ AN – Nghệ An khí hậu ôn hòa…") nhưng bị gán ngầm vào **am**. Metadata "tiêu đề Hán" nuốt **3 dòng thân văn đầu** (`又安天氣和暢…`), vì tiêu đề chữ to `天氣` không được OCR. | gán lớp sai, mất dòng | segmentation + OCR |
| CPO_20.162 (659) | ngẫu nhiên | Chú thích cuối trang `1. Nguyên chủ: …` (3 mục) vào lớp am, vì bài không có dòng "Trích …" để kết thúc am. | rò rỉ chú thích | segmentation |
| CPO_19.101 (216–218) | nghi vấn (lượt trước) | Đã sửa ở lượt trước (dòng rác "E", pinyin). | — | — |
| CPO_19.205 (515–519) | nghi vấn | Metadata tiêu đề Hán là `广一伯` (rác OCR của tiêu đề chữ to) cộng thêm dòng phụ đề `（呈告國書…）` bị nuốt. | mất dòng | OCR + segmentation |

Tỉ lệ ước lượng trên mẫu ngẫu nhiên (8 tài liệu):
- 3/8 có lỗi ảnh hưởng tới cặp dóng: ranh giới bài (1), gán lớp nghĩa → âm (1), chú thích lọt vào am (1).
- 3/8 chỉ có rò rỉ nhẹ (tiêu đề chạy, chữ số chỉ mục).
- 2/8 sạch.

## 4. Bảng phân loại lỗi

| Loại | Số lượng đo được | Work chính | Nguồn gốc |
|---|---|---|---|
| Ranh giới bài sai do rác Việt / dòng Hán đuôi bài sang trang (lỗi dây chuyền: âm gắn sang bài kế) | ước lượng 10–20 tài liệu CPO_18, nằm trong top nghi vấn với bead ≈ 0 | CPO_18 | segmentation |
| Rác Việt ngầm mở lớp am (`of contradiction!`, `**N**`, `Vandana =`, `Stone`, `E`…) | 8 / 4 / 2 lần ngầm mở am | CPO_18, CPO_19 | OCR + segmentation |
| Tiêu đề mục tường minh không đặt lại trạng thái → am thành nghia | 4 / 2 / 3 tài liệu (CPO_18 .005 .028 .073 .074; CPO_19 .055 .114; CPO_20 .058 .134 .160) | cả ba | segmentation |
| Bài chỉ có dịch nghĩa bị gán ngầm vào am | khoảng 25 tài liệu CPO_20 (047–053, 124–128, 146, 155…), 8 CPO_18 | CPO_20 | segmentation (thiếu tiêu đề mục) |
| Mục chú thích `1. Thuật ngữ: …` lọt vào lớp Việt | CPO_20 am 194 / nghia 72 / tho 34 dòng ở 04; còn 112 am sau clean | CPO_20 | segmentation |
| Chữ số chỉ mục chú thích trong câu (`thủy 2 hữu`) | CPO_20 am 144, CPO_18 am 32 sau clean | CPO_20, CPO_18 | OCR (Qwen) + clean |
| Tiêu đề mục OCR sai (`DỊCH THOẠI:`…) → thơ dịch vào lớp nghĩa | 9 dòng / 8 tài liệu | CPO_19 | config (mẫu tiêu đề) |
| Metadata tiêu đề Hán nuốt dòng thân hoặc là rác OCR | tiêu đề nhiều dòng 5 / 59 / 24; > 12 chữ 7 / 42 / 20 | CPO_19, CPO_20 | OCR (tiêu đề chữ to) + segmentation |
| Dòng bị bỏ không dấu vết (khác luồng, tiêu đề chạy) | 196 / 258 / 38 | cả ba | segmentation (thiếu log) |
| Han tách theo dòng, am theo dấu câu (hoặc ngược lại) | 11 / 14 / 31 tài liệu | CPO_20 | sentence |
| Tên người dịch, dòng nguồn trong lớp | tho CPO_19 42 (clean loại hết), CPO_20 "X dịch" ~100 | CPO_20 | segmentation + clean |

## 5. Nguyên nhân gốc và đề xuất sửa

Xếp theo tác động lên số cặp dóng hợp lệ.

1. **Rác Việt quyết định cấu trúc.** Dòng rác 1–4 chữ cái, hoặc tiếng Anh ngắn không dấu, đang:
   (a) mở tài liệu mới giữa hai khối Hán;
   (b) ngầm mở lớp am.
   Hai việc này gây lỗi dây chuyền ở CPO_18 và làm mất văn bia tr.286.
   → **Sửa:** dùng một vị từ "dòng Việt nhiễu" chung (≤ 4 chữ cái, hoặc không có chữ cái riêng tiếng Việt và ≤ 3
   từ) để bỏ qua cả khi xét ranh giới tài liệu lẫn khi ngầm chuyển lớp. Rủi ro thấp.
2. **Tiêu đề mục tường minh không đặt lại số dòng nội dung.** Ngoài ra, tiêu đề chạy hoặc dòng nhiễu vẫn được tính
   là nội dung. → **Sửa trong `layers.py`.** Rủi ro thấp.
3. **Bài văn xuôi chỉ có dịch nghĩa (CPO_20) bị gán vào am.** Chưa có quy tắc an toàn để phân biệt âm với nghĩa
   khi không có tiêu đề. → **Chỉ đề xuất:**
   - nhận diện "âm" bằng tỉ lệ từ thuần Việt (là, của, những, không…) hoặc bằng đối chiếu âm Hán–Việt với chữ Hán
     (từ điển `han_viet.tsv`);
   - hoặc người dùng liệt kê các bài chỉ có nghĩa trong config.
4. **Mục chú thích `1. Thuật ngữ: …` trong lớp Việt (CPO_20).** → **Sửa có cờ (mặc định tắt):** trong chế độ hai
   luồng, dòng khớp mẫu mục chú thích kết thúc lớp Việt hiện tại (dòng đó vào unassigned). Đề xuất bật cho CPO_20.
5. **Dòng bị bỏ không dấu vết.** → **Sửa:** ghi review `dropped_cross_stream` cho từng dòng bị loại. Chỉ thêm dòng
   vào review, không đổi output.
6. **Lệch chế độ tách han/am (CPO_20: 31 tài liệu).** → **Sửa có cờ (mặc định tắt)**
   `sentence.am_follow_han_split`: lớp am dùng chế độ tách mà lớp han đã chọn trong cùng tài liệu. Kèm số liệu.
7. **Tiêu đề mục OCR sai.** → **Chỉ đề xuất config:** thêm mẫu mờ cho `DỊCH TH(Ơ|OẠI)`, `DỊCH NG?H?ĨA`,
   `CHÚ T?H?ÍCH` (CLAUDE.md đã ghi phải so khớp mờ).
8. **Metadata tiêu đề Hán nuốt dòng.** → **Chỉ đề xuất:** giới hạn tiêu đề Hán tối đa 1 dòng (hoặc ≤ 12 chữ); nếu
   dòng đầu là rác (không khớp tiêu đề âm/nghĩa) thì để nó ở lớp han kèm review, không đưa vào metadata.
9. **Chữ số chỉ mục chú thích trong câu Việt.** → **Chỉ đề xuất:** ở lớp am (phiên âm không bao giờ có chữ số Ả
   Rập), bỏ token 1–2 chữ số đứng giữa hai chữ. Lớp nghia/tho không làm, vì có ngày tháng thật.

## 6. Sửa sau báo cáo

Xem mục 7 (được bổ sung sau khi sửa, kèm số liệu trước/sau).

## 7. Đã sửa (chưa commit) và số liệu trước/sau

Commit bị chặn (`reports/blocked_actions.md`), nên mọi sửa đổi đều nằm trong cây làm việc của nhánh
`audit/segmentation`. Dưới đây là các commit đề xuất, mỗi sửa một commit, để người dùng duyệt.

| # | Sửa | File | Mặc định | Test |
|---|---|---|---|---|
| A | Vị từ "dòng Việt nhiễu" (≤ 4 chữ cái, hoặc ≤ 3 từ ngoại ngữ không có chữ cái riêng tiếng Việt): không làm đứt khối Hán, không ngầm mở/chuyển lớp, không tính là nội dung | `streams.py` (`is_noise_vie`, `header_predicate`) | bật | `test_noise_*` |
| B | Tiêu đề mục tường minh đặt lại số dòng nội dung của lớp; dòng tiêu đề chạy hoặc nhiễu không tính là nội dung | `layers.py` | bật | `test_explicit_heading_resets_*` |
| C | Ghi review `dropped_cross_stream` cho từng dòng bị loại "khác luồng" | `layers.py` | bật (chỉ thêm review) | `test_dropped_cross_stream_lines_are_reviewed` |
| D | `segmentation.note_entry_ends`: mục chú thích `1. Thuật ngữ: …` mở vùng chú thích tới hết trang; sang trang mới thì lớp cũ chạy tiếp | `layers.py`, `segmentation/pipeline.py` | **tắt** | `test_note_entry_*` |
| E | `sentence.am_follow_han_split`: lớp am dùng chế độ tách mà lớp han đã chọn trong cùng tài liệu | `sentences.py` | **tắt** | `test_am_can_follow_han_split_mode` |
| F | `scripts/audit_segmentation.py` + test | mới | — | 11 test |

Số liệu chạy trên bản sao, cùng dữ liệu OCR:
- "trước" là code trước các sửa A–E;
- "mặc định" là có A–C;
- "có cờ" là thêm D (chỉ CPO_20) và E (cả ba work).

| Work | Chỉ số | Trước | Mặc định (A–C) | Có cờ (+D, +E) |
|---|---|---|---|---|
| CPO_18 | Bead han-am hợp lệ | 1002/1496 (**67,0%**) | 1117/1421 (**78,6%**) | 1107/1409 (78,6%) |
| | Hán–âm khớp ±10% | 57,7% (33 lệch) | 66,7% (25 lệch) | 66,7% |
| | Lệch số câu han/am > 30% | 29 (11 do khác chế độ tách) | 22 (8) | **15 (1)** |
| | Dòng mất dấu | 196 | **0** (571 có review `dropped_cross_stream`) | 0 |
| | Tài liệu | 78 | 75 (bài bị tách đôi được gộp lại) | 75 |
| CPO_19 | Bead hợp lệ | 3433/3920 (87,6%) | 3476/3920 (88,7%) | 3501/3924 (89,2%) |
| | Hán–âm ±10% | 87,5% (34 lệch) | 89,6% (28 lệch) | 89,6% |
| | Lệch số câu | 28 | 24 | 23 |
| | Dòng mất dấu | 258 | **16** | 16 |
| CPO_20 | Bead hợp lệ | 1968/2781 (70,8%) | 1988/2805 (70,9%) | 2022/2753 (**73,4%**) |
| | Hán–âm ±10% | 74,4% | 74,1% | 75,0% |
| | Lệch số câu (do khác chế độ tách) | 52 (31) | 53 (31) | **30 (8)** |
| | Mục chú thích trong am sau clean | 112 | 112 | 72 |
| | Rò rỉ sau clean (tổng) | 338 | 339 | 287 |
| | Dòng mất dấu | 38 | **1** | 1 |

Kiểm tra lại các ca cụ thể trên bản sao "mặc định":
- **Văn bia CPO_18 tr.286:** 17 câu Hán nằm trong lớp han.
- **"Trùng đáp Lý Giao Châu thư"** (trước là .005): phiên âm vào lớp âm, không còn vào nghĩa.
- **Bức thư tr.234–235** (trước là .006): chữ ký và dòng nguồn ở lớp Hán của chính bài; phiên âm gắn đúng bài.

Phiên bản đầu của D tắt lớp tới tiêu đề kế tiếp: CPO_20 tăng thêm 1074 dòng unassigned và bead giảm, vì chú thích
nằm cuối trang mà bài chạy tiếp sang trang sau. Đã đổi phạm vi thành "tới hết trang"; số liệu ở bảng trên là của
bản đã đổi.

Nghiệm thu:
- `pytest tests` (trừ `test_paddle_engine.py`, vốn đã lỗi từ trước): **432 passed**, nền trước audit là 413.
- `src/pipeline.py` không đổi, nên `--dry-run` của config thật không đổi (CPO_18 21 lệnh; CPO_19, CPO_20 mỗi work
  20 lệnh).
- `data/` thật không bị ghi: số dòng `git status --short data` trước và sau đều là 216.
- Không push.

Nhánh `audit/segmentation`. Commit: **chưa có commit nào** (bị chặn).

Thứ tự commit đề xuất:
1. Commit nền của lượt trước.
2. Sửa A + B (dòng nhiễu, đặt lại trạng thái).
3. Sửa C.
4. Sửa D.
5. Sửa E.
6. `audit_segmentation.py` + báo cáo.

## 8. Cần người dùng quyết

1. **Duyệt các commit** (nền + A–F). Commit hiện bị chặn ở chế độ tự động.
2. **Bật `segmentation.note_entry_ends: ["am","nghia","tho"]` cho CPO_20** (bead +2,5 điểm, mục chú thích trong am
   112 → 72) và **`sentence.am_follow_han_split: true`**:
   - CPO_18: lệch số câu 22 → 15;
   - CPO_20: lệch số câu 53 → 30.
   Cả hai đều cần sửa config thật.
3. **Bài chỉ có dịch nghĩa ở CPO_20** (khoảng 25 bài văn xuôi: 047–053, 124–128, 146, 155…) đang bị gán vào am.
   Bạn muốn liệt kê chúng trong config, hay để mình làm bộ nhận diện âm/nghĩa bằng từ điển Hán–Việt?
4. **Mẫu tiêu đề mục mờ** (`DỊCH THOẠI`, `DỊCH NHĨA`, `CHÚ TÍCH`, `Dịch phú`) có được thêm vào config CPO_18/19/20
   không?
5. **Metadata tiêu đề Hán:** chỉ cho phép 1 dòng (≤ 12 chữ) để không nuốt dòng thân văn (CPO_20.124 tr.268, CPO_19
   59 tiêu đề nhiều dòng)?
