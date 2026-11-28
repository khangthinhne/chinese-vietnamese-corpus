# Tóm tắt Guideline đồ án (dùng cho script đóng gói và báo cáo)

Nguồn: `Guideline.pdf` ("Hướng dẫn thực hiện đồ án dóng hàng Hán-Việt"), "Hướng dẫn Đồ án Dóng hàng Hán-Việt" (notes.xlsx, [MASK]), "Hướng dẫn Dóng hàng – Dịch âm Nôm-QN hay Hán-ÂmHV". Đoạn trích nguyên văn đặt trong ngoặc kép; phần **[QUYẾT ĐỊNH NHÓM]** là điểm nhóm chọn khác/bổ sung so với Guideline.

## 1. Số lượng tối thiểu
- Tài liệu dạng ảnh/PDF scan cần OCR: "Mỗi thành viên phải hoàn thành tối thiểu 2000 cặp dóng hàng hợp lệ, tương đương tối thiểu 8.000 cặp hợp lệ cho một nhóm".
- Tài liệu text/trích xuất được trực tiếp: 3000 cặp/thành viên, 12.000/nhóm. Nhóm có cả hai loại: tính theo loại chiếm đa số. Vượt mức có điểm cộng.

## 2. Cấu trúc gói nộp (khuyến nghị)
```
<MA_DE_TAI>_<MA_TAC_PHAM>/
├── 01_nguon/
│   ├── han/
│   └── viet/
├── 02_ocr_tho/
│   ├── han/
│   └── viet/
├── 03_text_sach/
│   ├── han.txt
│   └── viet.txt
├── 04_dong_hang/
│   ├── <MA_TAC_PHAM>_parallel.tsv
│   └── <MA_TAC_PHAM>_parallel.xlsx
```
"Tối thiểu phải có:
1. File kết quả dóng hàng dạng TSV.
2. File kết quả dóng hàng dạng Excel (`.xlsx`).
3. File văn bản Hán và Việt đã làm sạch.
4. Báo cáo số lượng cặp, số dòng Hán, số dòng Việt và kết quả tự kiểm tra.
5. Thông tin nguồn tài liệu và công cụ xử lý."

Ánh xạ từ repo (đề xuất):
- `01_nguon/` ← `data/<W>/01_sources/` (PDF gốc; Hán và Việt chung một PDF thì ghi chú trong báo cáo, không tách file nguồn).
- `02_ocr_tho/han/`, `02_ocr_tho/viet/` ← `data/<W>/03_ocr/ocr_raw_text/han/`, `.../vie/` (TXT từng trang `NNNN.txt`); kèm `han.xml`, `vie.xml` nếu muốn.
- `03_text_sach/` ← `data/<W>/05_cleaning/text/` (`han.txt`, `viet_am.txt`, `viet_nghia.txt`, `viet_tho.txt`, `mapping.tsv`). **[QUYẾT ĐỊNH NHÓM]** Guideline chỉ ghi `viet.txt`; nhóm tách thành ba file theo loại dịch. Có thể thêm `viet.txt` gộp nếu cần đúng tên.
- `04_dong_hang/` ← xuất từ `export --pair han-viet`.
- Báo cáo + thông tin nguồn/công cụ: file `BAO_CAO.md`/`.pdf` ở gốc gói (số cặp, số dòng Hán, số dòng Việt theo từng loại, kết quả tự kiểm tra, bảng kiểm kê nguồn, công cụ và phiên bản).
- Dịch âm: kèm minh chứng tool Lab (`_color.xlsx`, `statistic.json`) trong thư mục phụ, ví dụ `04_dong_hang/dich_am_lab/`.
- `notes.xlsx` (nếu có): ở gốc gói hoặc `04_dong_hang/`.

## 3. Định dạng file dóng hàng
Guideline: "Mỗi dòng chứa đúng một cặp Hán – Việt, phân cách bằng một ký tự tab"; Excel "có một sheet tên DONG_HANG, gồm đúng hai cột" `Hán`, `Việt`.

**[QUYẾT ĐỊNH NHÓM]** Một file 4 cột `Hán | Dịch âm | Dịch nghĩa | Dịch thơ` (theo yêu cầu báo cáo của nhóm). Ghi rõ lý do trong báo cáo. Giữ mọi quy tắc kỹ thuật còn lại:
- "Mỗi cặp nằm trên một dòng vật lý."
- "Không dùng nhiều dấu cách thay cho tab."
- "Không để tab hoặc xuống dòng bên trong một vế."
- "Bắt buộc nộp đồng thời `.tsv` và `.xlsx`; nội dung hai file phải giống nhau."
- Tên file: `<MA_TAC_PHAM>_parallel.tsv`, `<MA_TAC_PHAM>_parallel.xlsx`.
- Excel: "Hàng đầu tiên là tiêu đề"; "Từ hàng thứ hai, mỗi hàng tương ứng đúng một dòng trong TSV"; "Không gộp ô, không chèn dòng trống và không đặt công thức trong hai cột dữ liệu"; "Không thêm số thứ tự vào giữa hai cột dóng hàng"; "Có thể thêm sheet thống kê riêng, nhưng không thay đổi cấu trúc sheet `DONG_HANG`."

Kiểu dóng chấp nhận: `1–1`, `1–n` (gộp các câu Việt vào cùng ô), `n–1` (gộp các câu Hán vào cùng ô), `n–m` ("chỉ dùng khi bản dịch tái cấu trúc mạnh"). "Ưu tiên đơn vị nhỏ nhất có ý nghĩa đầy đủ. Không chia cơ học theo dấu câu nếu việc chia làm mất quan hệ dịch."

## 4. Quy trình bắt buộc liên quan
- Bước 2: bảng kiểm kê nguồn tối thiểu: Tên tài liệu, Ngôn ngữ, Link nguồn, Tên tệp, Số trang (tổng và trang nội dung), Định dạng, Phạm vi, Ghi chú. Hán và Việt chung một quyển: xác định bố cục, "Giữ lại mã trang và thứ tự xuất hiện để có thể đối chiếu lại với PDF."
- Bước 3: giữ nguyên bản nguồn; tên trang cố định chiều dài (`0001.png`); "Không dùng tên kiểu `final`, `final2`, `final_new`"; phiên bản rõ ràng (`v01`, ngày, mã commit); "Không đưa mật khẩu, cookie hay dữ liệu tài khoản vào thư mục bàn giao" (không đóng gói `.env`).
- Bước 4: "Lưu riêng OCR thô và text đã sửa để có thể truy vết."
- Bước 5 (làm sạch) và Bước 6 (tách câu): như checklist đã dùng trong `reports/clean_full_*.md`.
- "OCR tự động chỉ tạo bản nháp; mọi kết quả cần được con người kiểm tra."

## 5. Quy định bổ sung (Hướng dẫn đồ án)
- Bản dịch sai khác bản Hán: ghi `notes.xlsx` với các cột "tên tác phẩm, câu Hán, câu Việt, lỗi (mô tả ngắn gọn lỗi), chỉnh sửa; không được chỉnh sửa nội dung bản dịch."
- Chữ Hán hư hỏng/mờ không đọc được: "đánh dấu chữ khuyết là [MASK] vào dữ liệu dóng hàng". Bản khác rõ hơn thì được bổ sung và ghi nguồn.
- Dịch âm: "dùng Levenshtein bằng code của Lab". Dịch nghĩa: Guideline gợi ý API CLC, VecAlign, BERTAlign. **[QUYẾT ĐỊNH NHÓM]** Không được cấp API dóng hàng; dùng BERTAlign (bge-m3 + cầu nối âm), đánh giá bằng bộ dóng tay `eval/gold/`.
- Mục 9: đề xuất phương pháp khác phải nêu lý do, dữ liệu vào/ra, cách kiểm chứng, rủi ro và phương án dự phòng, khả năng tái lập.

## 6. Tiêu chí và đánh giá chất lượng
Một cặp đúng khi: "1. Hai vế nói về cùng nội dung. 2. Không bỏ mất ý quan trọng. 3. Không thêm nội dung thuộc câu/đoạn khác. 4. Tên riêng, chức danh, địa danh cơ bản tương ứng. 5. Thời gian, niên hiệu và con số tương ứng. 6. Ranh giới câu/đoạn hợp lý. 7. Không phải dòng tiêu đề, số trang hoặc OCR rác ghép nhầm."

Nhãn lỗi: `OMISSION` (thiếu nội dung), `ADDITION` (thừa nội dung), `MISSING` (thiếu một vế hoặc thiếu cặp).

"Tỷ lệ lỗi = Số cặp lỗi / Số cặp được kiểm tra hợp lệ". "Tỷ lệ lỗi ≤ 30%: đạt ngưỡng nghiệm thu"; "Mục tiêu nội bộ nên là ≤ 20%". "Việc đạt ngưỡng không có nghĩa được phép giữ lỗi đã phát hiện; mọi lỗi cụ thể trong mẫu phải được sửa và rà soát các lỗi tương tự trên toàn file."

**[QUYẾT ĐỊNH NHÓM]** Với file 4 cột: tính tỉ lệ lỗi riêng cho từng cột dịch; ô trống vì sách không có loại dịch đó là `KHONG_CO`, không tính lỗi.