# Kiểm kê OCR thô trước khi chạy bổ sung (2026-11-25, ~12:35)

Đo bằng script đọc `03_ocr/raw/**` (chưa có OCR mới nào). Ảnh xem bằng mắt: các trang nêu ở mục 3, 4.

## 1. Phạm vi trang
| work | trang PDF | ảnh đã trích (`02_extraction/pages`) | `common.pages` cũ | `ocr.streams.pages` (mới) |
|---|---|---|---|---|
| CPO_17 | – (nguồn HTML `01_sources/han.html`) | – | không có config | không áp dụng (không OCR) |
| CPO_18 | 565 | 205-212 (8) | 205-206 | 205-212 |
| CPO_19 | 850 | 15-121 (107) | 15-16 | 15-121 |
| CPO_20 | 854 | 5-806 (802) | 5-806 | 5-806 |

Đối chiếu biên (render PDF ở 30 dpi):
- **CPO_20**: tr.3-4 là phần giới thiệu Phan Huy Ích (không có Hán); tr.5 bắt đầu bài đầu tiên; tr.807 sang tác giả
  khác ("HOÀNG QUANG"). Khoảng 5-806 khớp phần của Phan Huy Ích.
- **CPO_19**: tr.14 có "小引" (lời dẫn chữ Hán, kèm dịch) nằm NGOÀI khoảng; sau tr.121 nội dung còn tiếp (bài 54...,
  tr.124). Nghi thiếu trang ở cả hai đầu. **Cần người dùng quyết.**
- **CPO_18**: tr.212 kết thúc giữa văn bản (tr.213-215 tiếp chú thích). 8 trang chỉ là mẫu thử. **Cần người dùng quyết.**

Không tự đổi khoảng trích; `ocr.streams.pages` chỉ ghi lại khoảng ảnh hiện có.

## 2. Raw JSON theo engine (trước khi chạy bổ sung)
| work | paddlevl (Hán): có / thiếu | qwen (Việt): có / thiếu | trang nhiều khoá | adapter |
|---|---|---|---|---|
| CPO_18 | 8 / 0 | 8 / 0 | qwen: 205, 206 (v1 + v3) | paddlevl v4; qwen v1 (6 run), v3 (10 run) |
| CPO_19 | 20 / 87 (thiếu 35-121) | 107 / 0 | qwen: 18 trang (15-31, 33), chỉ khác `min_pixels` | paddlevl v4; qwen v3 |
| CPO_20 | 158 / 644 (thiếu 163-806) | 411 / 391 (thiếu 416-806) | 0 | paddlevl v4; qwen v3 |

Không trang nào có `error` trong manifest. Toàn bộ run Qwen dùng transformers 4.57.6; paddlevl dùng 5.24.0.
Run có 0 đơn vị ảnh (cả trang bị coi là trắng): CPO_19 tr.41, tr.111 (cả qwen lẫn paddlevl), xem mục 4.

## 3. Qwen: cờ chất lượng theo dải
- Tổng: CPO_18 1 run lặp + cắt cụt (tr.208), CPO_20 8 run có `repetition_collapsed`, 11 run có `truncated`.
- Phân theo nội dung dải:
  - Phần lớn dải hỏng là dải TOÀN CHỮ HÁN (Qwen lặp "江江江..."). Chữ Hán bị lọc khỏi vie nên không làm hỏng luồng
    vie (CPO_20 tr.90, 93, 106, 110, 198, 208, 246, 254, 259, 292, 319).
  - Dải tiếng Việt có `repetition_collapsed` không cắt cụt (CPO_20 tr.16, 34, 223): do đường kẻ "_____" trước
    chú thích, nội dung đủ. Vô hại.
  - **CPO_18 tr.208 dải 2**: Qwen lặp "？？？" giữa đoạn tiếng Việt rồi bị cắt ở 768 token. Mất phần sau của dải
    -> phải OCR lại.
- Lời dẫn tiếng Anh lọt vào `vie.xml`: CPO_20 tr.97 ("The image contains Chinese text. I will transcribe it..."),
  tr.121 (lời dẫn + một câu Qwen tự dịch sang tiếng Anh). Đã sửa (commit 58df306).
- Confidence trung bình theo trang không dùng để kết luận (thang 0-100 là trung bình nhân xác suất token).
  Kiểm tra dùng ngưỡng cấu hình `qwen_low_conf=60`.

## 4. Trang rỗng (đã mở ảnh từng trang)
| loại | trang | kết luận |
|---|---|---|
| vie rỗng | CPO_19: 41, 111 | trang chỉ có thơ Hán (và tiêu đề chạy "NGÔ THÌ NHẬM TOÀN TẬP") -> hợp lệ |
| vie rỗng | CPO_20: 36, 40, 53, 98, 103, 106, 110, 115, 119, 178, 256-259, 270, 273, 306-308 | trang chỉ có chữ Hán (thơ/văn xuôi in ngang) -> hợp lệ |
| han rỗng | CPO_18: 206, 210-212; CPO_19: 18, 31, 33; CPO_20: các trang chẵn/lẻ chỉ có chữ Việt (vd 6, 100, 122, 130, 161) | chỉ có chữ Việt -> hợp lệ |
| han = 0 dòng nhưng có thơ Hán | CPO_19: 41 (và 111 khi chạy) | **OCR hỏng**: `ink_mask` coi trang thưa mực (~1,3% điểm mực) là trắng -> đã sửa (adapter 5), OCR lại |

Hướng chữ: CPO_20 tr.106 (văn xuôi Hán dày đặc) xem phóng to: chữ in NGANG, paddlevl horizontal đọc đúng thứ tự.

## 5. Thời gian thực đo (từ `inference_seconds` trong raw)
| engine | work | n run | trung vị s/trang | trung bình |
|---|---|---|---|---|
| paddlevl | CPO_18 / 19 / 20 | 8 / 20 / 159 | 17,6 / 14,9 / 15,8 | 31,0 / 21,0 / 21,1 |
| qwen | CPO_18 / 19 / 20 | 16 / 123 / 411 | 38,8 / 27,8 / 28,4 | 57,3 / 29,6 / 39,9 |

Ước lượng phần còn thiếu lúc kiểm kê: paddlevl 731 trang x ~21 s ≈ 4,3 giờ; Qwen 391 trang x ~35 s ≈ 3,8 giờ.
