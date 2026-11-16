# Xây dựng ngữ liệu song song Hán - Việt

## 1. Giới thiệu

Dự án hướng đến xây dựng ngữ liệu song song Hán - Việt ở cấp câu/đoạn, phục vụ nghiên cứu và ứng dụng trong lĩnh vực Xử lý ngôn ngữ tự nhiên (NLP).

## 2. Quy trình xử lý

Dự án được chia thành 7 giai đoạn:

| Thư mục           | Chức năng                                            |
| ----------------- | ---------------------------------------------------- |
| `01_sources`      | Lưu trữ tài liệu nguồn (`.pdf`, `.html`, ...)        |
| `02_extraction`   | Trích xuất PDF thành hình ảnh                        |
| `03_ocr`          | Nhận dạng ký tự, lưu kết quả OCR thô dưới dạng XML   |
| `04_segmentation` | Phân đoạn tài liệu, văn bản và câu                   |
| `05_cleaning`     | Làm sạch, loại bỏ nội dung thừa và lỗi OCR           |
| `06_alignment`    | Dóng hàng Hán - phiên âm, dịch nghĩa và dịch thơ     |
| `07_export`       | Xuất dữ liệu dóng hàng thành TSV và XLSX             |

Các lớp dữ liệu:

- `han`: Hán văn nguyên bản.
- `am`: Phiên âm Hán - Việt.
- `nghia`: Dịch nghĩa.
- `tho`: Dịch thơ.

## 3. So sánh với Guidelines

| Guidelines     | Cấu trúc của nhóm                 |
| -------------- | --------------------------------- |
| `01_nguon`     | `01_sources` + `02_extraction`    |
| `02_ocr_tho`   | `03_ocr`                          |
| `03_text_sach` | `04_segmentation` + `05_cleaning` |
| `04_dong_hang` | `06_alignment` + `07_export`      |

Nhóm chủ động chia nhỏ quy trình để dễ quản lý, kiểm tra lỗi và mở rộng từng giai đoạn.

## 4. Các quyết định thiết kế

- Sử dụng **XML** cho dữ liệu trung gian nhằm tổ chức cấu trúc rõ ràng, bảo toàn metadata và hỗ trợ truy vết.
- Chỉ giữ nội dung chính của tác phẩm trong dữ liệu dóng hàng; header, footer và tiêu đề được tách riêng hoặc loại bỏ.
- Dữ liệu dóng hàng không bắt buộc hiển thị ID tác phẩm. Thông tin truy vết vẫn được lưu trong dữ liệu trung gian.
- Sử dụng TSV và XLSX làm định dạng xuất dữ liệu cuối cùng.

## 5. Công cụ và định hướng phát triển

**Hiện tại:**

- OCR: Tesseract.

**Dự định:**

- Tích hợp thêm PaddleOCR và CLC API của Lab.
- Mở rộng thử nghiệm OCR trên khoảng 5-10 bài/tài liệu cho mỗi tác phẩm.

## Note

AI được sử dụng để hỗ trợ biên soạn README. Nhóm đã kiểm tra và rà soát lại nội dung.