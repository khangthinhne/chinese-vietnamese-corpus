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


## 9. Đợt 2 (2026-11-27): sau khi người dùng duyệt

Người dùng duyệt A, B, C; bật D cho CPO_20 và E cho cả ba work; yêu cầu thêm 6 sửa. Mỗi sửa một commit kèm test,
số liệu đo trên bản sao bằng `scripts/audit_segmentation.py`. Không push. `data/`: `git status --short data` = 216 dòng
trước và sau (132 M, 69 D, 15 ??), không commit file nào trong `data/`.

### Commit (nhánh `audit/segmentation`)
`git log --oneline 25b6362..HEAD`:
- `a5ba17c` nền lượt trước · `8c4afea` A+B · `23e39fd` C · `c5125f1` D · `63c8b86` E · `a784380` audit script + báo cáo
- `ef4777b` config: bật D (CPO_20) và E (CPO_18/19/20)
- `011e855` tiêu đề mục so khớp mờ · `66d2c25` bỏ chữ số chỉ mục trong lớp am · `35f5f6f` dòng "X dịch" vào metadata
- `429872e` tiêu đề Hán tối đa 1 dòng + phụ đề · `a058223`, `f265907` bộ nhận diện âm/nghĩa (mặc định tắt)
- `22a445f`, `40bf94a` tiêu đề Hán đánh số 1 dòng mở bài mới (không coi là tiêu đề chạy)

Test: mỗi commit đều qua (worktree riêng); cuối: 443 passed.

### D trước khi bật (CPO_20)
Bật `note_entry_ends` làm unassigned tăng 179 → 528 (+349): 300 dòng mục `số. thuật ngữ:` và 49 dòng là phần còn lại
của chú thích. Rà 30 dòng mẫu (seed 20261127): cả 30 là chú thích, nên đã bật.

### Diff config thật (`ef4777b`)
```
configs/CPO_18.json, CPO_19.json:  + "sentence": {"am_follow_han_split": true},
configs/CPO_20.json:               + "note_entry_ends": ["am", "nghia", "tho"],   (trong segmentation)
                                   + "sentence": {"am_follow_han_split": true},
```

### Số liệu từng sửa
Bản sao dùng config thật. Cột "nền" là `ef4777b`. "(bật nhận diện âm/nghĩa)" là đo riêng, không có trong config thật.
Cột "CUỐI" có align.

### CPO_18

| Chỉ số | nền (ef4777b) | + tiêu đề mờ | + bỏ số am | + người dịch | + tiêu đề Hán | + tiêu đề số 1 dòng | (bật nhận diện âm/nghĩa) | CUỐI 40bf94a |
|---|---|---|---|---|---|---|---|---|
| tài liệu | 75 | 75 | 75 | 75 | 75 | 75 | 75 | 75 |
| lớp lặp | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| không tho | 73 | 73 | 73 | 73 | 73 | 73 | 73 | 73 |
| tiêu đề mục trong thân (04) | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| số chỉ mục am (05) | 32 | 32 | 5 | 5 | 5 | 5 | 5 | 5 |
| tên dịch tho+nghia (05) | 5 | 5 | 5 | 5 | 5 | 5 | 5 | 5 |
| metadata | 148 | 147 | 147 | 147 | 144 | 144 | 144 | 144 |
| Hán–âm ±10% | 0.667 | 0.676 | 0.676 | 0.676 | 0.676 | 0.676 | 0.706 | 0.676 |
| lệch số câu | 15 | 16 | 16 | 16 | 16 | 16 | 11 | 16 |
| câu am | 1412 | 1410 | 1410 | 1410 | 1410 | 1410 | 1318 | 1410 |
| câu nghia | 1133 | 1133 | 1133 | 1133 | 1133 | 1133 | 1224 | 1133 |
| bead | 1107/1409 | - | - | - | - | - | - | 1105/1413 |

### CPO_19

| Chỉ số | nền (ef4777b) | + tiêu đề mờ | + bỏ số am | + người dịch | + tiêu đề Hán | + tiêu đề số 1 dòng | (bật nhận diện âm/nghĩa) | CUỐI 40bf94a |
|---|---|---|---|---|---|---|---|---|
| tài liệu | 270 | 270 | 270 | 270 | 270 | 270 | 270 | 270 |
| lớp lặp | 12 | 12 | 12 | 12 | 12 | 12 | 12 | 0 |
| không tho | 213 | 188 | 188 | 188 | 188 | 188 | 188 | 188 |
| tiêu đề mục trong thân (04) | 11 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| số chỉ mục am (05) | 7 | 6 | 5 | 5 | 5 | 5 | 5 | 5 |
| tên dịch tho+nghia (05) | 39 | 34 | 34 | 34 | 34 | 34 | 34 | 34 |
| metadata | 1012 | 1009 | 1009 | 1009 | 953 | 953 | 953 | 963 |
| Hán–âm ±10% | 0.896 | 0.9 | 0.9 | 0.9 | 0.893 | 0.893 | 0.893 | 0.893 |
| lệch số câu | 23 | 21 | 21 | 21 | 21 | 21 | 21 | 21 |
| câu am | 3895 | 3859 | 3859 | 3859 | 3861 | 3861 | 3861 | 3861 |
| câu nghia | 3403 | 3387 | 3387 | 3387 | 3387 | 3387 | 3387 | 3387 |
| bead | 3501/3924 | - | - | - | - | - | - | 3507/3926 |

### CPO_20

| Chỉ số | nền (ef4777b) | + tiêu đề mờ | + bỏ số am | + người dịch | + tiêu đề Hán | + tiêu đề số 1 dòng | (bật nhận diện âm/nghĩa) | CUỐI 40bf94a |
|---|---|---|---|---|---|---|---|---|
| tài liệu | 222 | 222 | 222 | 222 | 222 | 222 | 222 | 222 |
| lớp lặp | 4 | 4 | 4 | 4 | 4 | 4 | 3 | 4 |
| không tho | 112 | 111 | 111 | 111 | 111 | 111 | 111 | 111 |
| tiêu đề mục trong thân (04) | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| số chỉ mục am (05) | 144 | 144 | 40 | 40 | 40 | 40 | 28 | 40 |
| tên dịch tho+nghia (05) | 8 | 8 | 8 | 7 | 7 | 7 | 11 | 7 |
| metadata | 656 | 656 | 656 | 656 | 629 | 629 | 625 | 629 |
| Hán–âm ±10% | 0.75 | 0.755 | 0.755 | 0.755 | 0.768 | 0.768 | 0.808 | 0.768 |
| lệch số câu | 30 | 29 | 29 | 29 | 28 | 28 | 21 | 28 |
| câu am | 2838 | 2843 | 2843 | 2843 | 2843 | 2843 | 2444 | 2843 |
| câu nghia | 1446 | 1378 | 1378 | 1378 | 1378 | 1378 | 1777 | 1378 |
| bead | 2022/2753 | - | - | - | - | - | - | 2031/2758 |

### Bộ nhận diện âm/nghĩa: bài bị đổi lớp khi bật (ngưỡng 0,35), CHỜ DUYỆT
Phân bố điểm: bài phiên âm thật ≥ 0,45; bài dịch nghĩa 0,00–0,27.
- **CPO_20** (12 bài, đều là văn xuôi chỉ có dịch nghĩa):
  - .047 tr.99 (0,20) · .048 tr.104–105 (0,11) · .049 tr.107–109 (0,17) · .050 tr.111–112 (0,14)
  - .051 tr.113–114 (0,18) · .052 tr.116–118 (0,12) · .053 tr.120–134 (0,15) · .124 tr.268–269 (0,13)
  - .125 tr.271 (0,27) · .126 tr.274–275 (0,21) · .133 tr.287 (0,00) · .155 tr.646–648 (0,16)
- **CPO_18** (6 bài): lớp "âm" vốn đã sai, nhưng đổi sang nghĩa cũng chưa đúng hẳn:
  - .012 tr.339: rác `[UNCLEAR]`;
  - .013 tr.342–349 và .015 tr.358–359: mục khảo dị `(6) Bản B: …`;
  - .019 tr.369: đoạn truyện do Qwen tự bịa;
  - .028 tr.414–415: tiểu sử;
  - .054 tr.492: bản dịch; dòng đầu "hĩ. Ngũ kinh…" là đuôi phiên âm của bài trước tràn sang.
- **CPO_19:** 0 bài.
- Đề xuất: bật `segmentation.am_detection: {"enabled": true}` cho CPO_20. Với CPO_18 thì chưa bật; nên sửa nguồn
  (khảo dị, rác OCR) trước.

### 12 tài liệu CPO_19 có khối Hán thứ hai ở cuối
Cả 12 bài (.032, .144, .187, .191, .195, .205, .207, .209, .211, .215, .221, .231) cùng một kiểu: khối Hán thứ hai là
**tiêu đề đánh số của bài kế tiếp** in ở cuối trang (`10. 又一道`, `52. 共一`, `28.呈福大人`).

Hai nguyên nhân chồng nhau:
1. Khối Hán 1 dòng không được mở bài.
2. `_norm` bỏ chữ số, nên `又一道` lặp ở nhiều trang và bị coi là tiêu đề chạy.

Ảnh hưởng tới cặp dóng:
- 9 cặp han-nghia có câu Hán cuối bị nối thêm tiêu đề (`…者也。又一道`, `…之大願也。呈福大人`).
- Bài sau mất tiêu đề.
- CPO_19.207: tiêu đề âm "LẠI MỘT ĐẠO" của bài sau cũng dính vào bài trước.

Sau `22a445f` và `40bf94a`: còn 0 tài liệu có lớp lặp; bead hợp lệ 3501 → 3507.

### Ảnh đã trích để kiểm tra
- CPO_18: 340 trang (205–544). CPO_19: 829 trang (5–833). Ảnh nằm trong `data/*/02_extraction/pages`, thư mục này đã
  có trong gitignore.
- `check_ocr_complete` khi đã đủ ảnh:
  - CPO_19: **ĐỦ, 0 lỗi**;
  - CPO_18: còn 3 lỗi `vie_degenerate` đã biết (208, 286, 523), hết `missing_image`.

## 10. Vòng 3 (2026-11-27): duyệt đổi lớp, ghi đè CPO_18, quét ảo giác Qwen, file bị xoá

### 10.1 Commit (nhánh `audit/segmentation`, không push)
| Commit | Nội dung | Test |
|---|---|---|
| `272cd68` | Kiểm tra chéo Qwen (vie.xml) với dòng Latin paddlevl, cảnh báo trong `check_ocr_complete` | `tests/test_ocr_cross_check.py` (7) |
| `0b027cd` | Bộ nhận diện âm/nghĩa chỉ chấm điểm và chuyển phần am MỞ NGẦM | 1 test mới |
| `c7e249c` | Bước dòng Hán dùng trung vị dưới (sửa ranh giới CPO_18.054) | 1 test mới |
| `f895c61` | Cơ chế `segmentation.document_overrides` | `tests/test_segmentation_overrides.py` (4) |
| `3358fc0` | Config CPO_20: bật `am_detection`; docs | - |
| `480960c` | Config CPO_18: 4 ghi đè có lý do | - |

Toàn bộ test: 455 passed (nền 443). `git status --short data`: 216 dòng trước và sau, không commit gì trong `data/`.

### 10.2 Xác nhận ảnh CPO_20 trước khi bật bộ nhận diện
- **.155** (Hán tr.644–645, Việt tr.646–648): ảnh tr.646 có "Dịch nghĩa:" và tiêu đề "BÀI KÝ: NGƯỜI NÓI CHUYỆN VỚI
  BÓNG" nhưng Qwen bỏ sót cả hai dòng, nên phần Việt bị mở ngầm thành am. Đây là văn xuôi chỉ có dịch nghĩa: **đúng**.
- **.053** (tr.120–134) thực ra gồm 3 phần:
  - tr.120–122: "TỰA TẬP NGÔ GIA VĂN PHÁI", văn xuôi dịch nghĩa, Hán ở tr.118–119;
  - tr.123–125: THƠ NÔM PHAN HUY ÍCH (thơ Nôm, không in chữ Hán);
  - tr.126–134: "Phiên âm:" bài DỤ NHỊ SÚY QUỐC ÂM CHIẾU VĂN (chiếu Nôm, không in chữ Nôm).

  Bộ nhận diện cũ chuyển cả phần "Phiên âm:" tường minh sang nghia, nên đã sửa (`0b027cd`): giờ chỉ chuyển
  tr.120–122. Phần tr.123–134 không có chữ Hán đối ứng, nên cặp dóng của .053 vẫn lẫn. **Cần quyết:** đưa tr.123–134
  ra khỏi lớp dóng bằng `document_overrides`, hoặc tách thành bài riêng.
- Danh sách 12 bài đổi am→nghia không đổi so với danh sách đã duyệt.

| CPO_20 | nền 40bf94a | sau (bật) |
|---|---|---|
| câu am / câu nghia | 2843 / 1378 | 2525 / 1698 |
| Hán–âm ±10% | 0,768 | 0,804 |
| lệch số câu han/am | 28 | 22 |
| chữ số chỉ mục trong am (05) | 40 | 30 |
| bead han-am hợp lệ | 2031/2758 | 2031/2783 |

### 10.3 CPO_18: ghi đè theo bài và sửa ranh giới
| Bài | Thực tế (đã xem ảnh/văn bản) | Xử lý | Dòng gỡ ra |
|---|---|---|---|
| .012 tr.337–339 | Nửa đầu văn bia chuông Thanh Mai; am chỉ có 1 dòng `[UNCLEAR]` | `exclude` | 29 |
| .013 tr.339–357 | Tiêu đề "KHAO DI" mất dấu ở tr.342, nên khảo dị (1)–(47) mở ngầm am trước "PHIÊN ÂM:" tr.344 | `notes`, chỉ phần am mở ngầm | 45 |
| .015 tr.358–359 | Khảo dị Bản B/C; 2 dòng Hán là chữ trích trong khảo dị | `notes` cả bài | 12 |
| .028 tr.413–415 | am là tiểu sử/chú thích, không phải phiên âm | `exclude` | 35 |
| .054 tr.492 | Dòng rác "一" (conf 19) đầu trang làm bước dòng Hán = 2806 px, nên 2 dòng Hán cuối trang bị "dán" lên trên đuôi phiên âm .053 | Sửa chung `c7e249c`, không cần ghi đè | - |
| .019 tr.368–369 | Qwen bịa dòng "Một ngày, một người đến gặp một thầy…" ở tr.369 (ảnh không có), cắt khối Hán | Đưa tr.369 vào danh sách OCR lại | - |

Mọi dòng bị gỡ đều có review `override_excluded` (64) hoặc `override_note` (57), kèm 1 dòng `document_override`
mang lý do.

Ghi chú thêm:
- **Nguyên nhân gốc của .013:** tiêu đề khảo dị chỉ kết thúc lớp nghia. Nếu nó cũng chặn việc mở ngầm am ngay sau
  khối Hán, có thể bỏ ghi đè này. Đề xuất làm sau, có cờ.
- **.012 có thể giữ lại thay vì loại:** phiên âm của .012 nằm trong .013, nên gộp .012 vào .013 thì giữ được 28 dòng
  Hán văn bia. Cần bạn quyết.
- **Tác dụng phụ của sửa ranh giới:** ở CPO_18 tr.524 có 3 dòng Hán là tên bài thơ trích trong văn xuôi. Trước khi
  sửa, chúng mở bài giả ở dòng 1; sau khi sửa, mở bài giả ở dòng 2, còn dòng 1 dính vào .066 thành "lớp lặp". Cả
  trước lẫn sau đều sai.

| CPO_18 | nền 40bf94a | +bước dòng Hán | +ghi đè |
|---|---|---|---|
| tài liệu | 75 | 74 | 74 |
| bead han-am hợp lệ | 1105/1413 | 1108/1411 | 1108/1350 |
| Hán–âm ±10% | 0,676 | 0,699 | 0,714 |
| lệch số câu | 16 | 15 | 13 |

CPO_19 không đổi ở mọi chỉ số (bead 3507/3926).

### 10.4 Quét ảo giác/lặp/bỏ sót của Qwen (CPU, `src/ocr/cross_check.py`)
**Cách đo.** So vie.xml với các dòng Latin mà paddlevl đã OCR trên cùng trang (lấy từ JSON thô của luồng Hán). Dòng lẫn
chữ Hán thì giữ phần Latin. Văn bản được đưa về chữ thường, bỏ dấu, bỏ khoảng trắng/dấu câu/số, rồi so tập 4-gram ký tự.
- precision = phần chữ của Qwen mà paddlevl cũng thấy;
- recall = phần chữ của paddlevl mà Qwen cũng có;
- dòng Qwen ≥ 30 chữ cái có precision < 0,35 là "không xác nhận".

**Phạm vi.** So được 284/311 trang (CPO_18), 718/820 (CPO_19), 463/501 (CPO_20); các trang còn lại có quá ít chữ
Latin.

**Phân vị.** precision p5: 0,93 / 0,96 / 0,94. recall p5: 0,90 / 0,91 / 0,77; CPO_20 hay bỏ sót chú thích chữ nhỏ.

**Tích hợp.** Đã thêm vào `check_ocr_complete` dưới dạng **cảnh báo** (không chặn):
- `vie_cross_low_precision` (< 0,75);
- `vie_cross_low_recall` (< 0,6);
- `vie_unconfirmed_line`.

Trên dữ liệu thật:
- CPO_18: 2 / 2 / 5 cảnh báo;
- CPO_19: 2 / 0 / 1;
- CPO_20: 1 / 6 / 1.

Số lỗi không đổi: CPO_18 còn 3 `vie_degenerate`; CPO_19 và CPO_20 có 0 lỗi.

**Đã mở ảnh 10 trang có min(precision, recall) thấp nhất,** cùng các trang có dòng không xác nhận:
| Trang | Kết luận |
|---|---|
| CPO_18 343, 539 | Qwen đúng; chính **paddlevl bịa** ("The quick brown fox…", "(the original is…") |
| CPO_18 402 | Qwen bịa "Bài giảng: Điều tra dân số…", "HỌC THỦY QUỐC" (trang chỉ có 2 chú thích, phần còn lại là mực thấm từ trang sau) |
| CPO_18 369 | Qwen bịa 1 dòng truyện (bài .019) |
| CPO_20 90, 76, 678, 622, 298, 685, 771 | Qwen **bỏ sót chú thích cuối trang**; tr.90 mất cả tiêu đề "NHÂM TUẤT NGUYÊN NHẬT…" |

Kiểm tra theo văn bản:
- CPO_18 tr.470: dòng tiếng Anh tự bịa.
- CPO_18 tr.528: rác LaTeX.
- CPO_20 tr.215: dòng "Trên đỉnh núi cao tuyết trắng như trời." không có trong ảnh, chen vào Dịch thơ.
- CPO_20 tr.296: 25 dòng pinyin "-: zhuǎn" và lời tự nói "Phần âm của các từ trong hình là:".
- CPO_19 tr.102: 4 dòng pinyin không dấu dưới "PHIÊN ÂM:".

Ba trang cuối lọt qua `script_filter`/`chatter`. Đề xuất mở rộng luật lọc (pinyin 1 âm tiết, pinyin không dấu), có
test.

**Danh sách trang cần OCR lại Qwen (luồng vie)**
- Ưu tiên 1, rác/bịa ảnh hưởng tách lớp hoặc cặp dóng:
  - CPO_18: 208, 286, 523 (`vie_degenerate`), 369, 402, 470, 528; 353 nghi (1 dòng tên người bị đọc rối).
  - CPO_19: 102.
  - CPO_20: 215, 296.
- Ưu tiên 2, bỏ sót chú thích/tiêu đề:
  - CPO_20: 76, 90, 298, 622, 646 (mất "Dịch nghĩa:" và tiêu đề), 678, 685, 771.
  - Chú thích không vào cặp dóng; chỉ tr.90 và tr.646 ảnh hưởng metadata/tách lớp.
- Không cần OCR lại: CPO_18 343, 539 (paddlevl sai); CPO_19 599 (dòng bị che một nửa ở mép trên trang).

### 10.5 69 file bị xoá trong `data/` (chỉ báo cáo, không khôi phục hay xoá gì)
| Nhóm | File | Có trong commit | Xoá khi nào |
|---|---|---|---|
| CPO_18 `ocr_raw_text/han` | 29 trang: 261–281, 306–307, 327–329, 335, 361–362 | `8b001c3` (2026-11-26 17:09, "OCR CPO_18 raw from 205-544") | Lần `build_layers` sau khi các trang ảnh chụp thủ bản bị bỏ khỏi `ocr.streams.pages` (lần chạy cuối 2026-11-26 20:39) |
| CPO_18 `ocr_raw_text/vie` | 28 trang như trên, trừ 272 (trang này chưa từng có vie) | `8b001c3` | như trên |
| CPO_19 `ocr_raw_text/han` | 3 trang: 258, 259, 266 | `dfb8d22` (2026-11-26 17:11) | `build_layers` sau khi bỏ 258–266 (2026-11-26 20:22) |
| CPO_19 `ocr_raw_text/vie` | 9 trang: 258–266 | 12 file theo `9e0b941` (19:36), còn lại theo `dfb8d22` | như trên |

- **Cách bị xoá:** đây là hành vi có chủ đích của `build_layers.write_texts`, vốn xoá TXT của những trang không còn
  được build (vòng `entry.unlink()`). Không có thao tác xoá bằng tay.
- **Còn ở đâu:** cả 69 file vẫn có trong HEAD của `audit/segmentation`, `feat/merge-member-ocr` và
  `origin/feat/merge-member-ocr`.
- **JSON thô `03_ocr/raw/{paddlevl,qwen}/NNNN.json`** của các trang này còn nguyên.
- **Nếu commit trạng thái `data/` hiện tại**, các TXT này sẽ bị xoá khỏi lịch sử tiếp theo. Điều này đúng với quyết
  định bỏ trang, nhưng cần bạn xác nhận.
