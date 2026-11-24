# Hướng Dẫn OCR: Single-Stream Qwen VLM & Tesseract Legacy

Tài liệu này mô tả chi tiết hệ thống OCR trong dự án ngữ liệu song song Hán - Việt.

---

## 1. Triết Lý Thiết Kế: Single-Stream OCR (Khuyến nghị)

> **"OCR nhận dạng ký tự. Segmentation hiểu cấu trúc tài liệu."**

Trong kiến trúc mới, quá trình OCR được thực hiện **đúng 1 lần duy nhất cho mỗi trang** thông qua mô hình thị giác ngôn ngữ lớn **Qwen VLM** (hoặc engine đa ngôn ngữ).
- Mô hình nhận dạng tất cả chữ viết trên trang: chữ Hán/Nôm, phiên âm, dịch nghĩa, dịch thơ, chú thích, số trang, tiêu đề...
- Không có bước tiền phân loại hay lọc script (`cjk` / `latin`) ở tầng OCR.
- Dòng OCR thô được bảo toàn nguyên vẹn trong một luồng thống nhất.
- Việc phân chia nội dung vào các lớp ngữ nghĩa (`han`, `am`, `nghia`, `tho`) do bước phân đoạn tiếp theo (`src.segmentation`) đảm nhiệm.

---

## 2. Quy Ước Đặt Tên & Cấu Trúc Thư Mục OCR

### 2.1. Đặt tên trang 4 chữ số (4-Digit Zero-Padding)
Tất cả các tệp theo trang đều dùng định dạng 4 chữ số: `0001`, `0015`, `0206`, `9999`.

### 2.2. Bố cục thư mục `03_ocr/`
```text
03_ocr/
    raw/
        0015.json               # Raw JSON từ Qwen VLM (immutable)
        0016.json
        manifest.json           # Manifest theo dõi các lượt OCR unified
    ocr_raw/
        raw.xml                 # XML chuẩn hoá toàn bộ dòng của tác phẩm
    ocr_raw_text/
        0015.txt                # Văn bản thô từng trang phục vụ tra cứu nhanh
        0016.txt
    metadata.json               # Metadata tổng hợp của OCR run
```

---

## 3. Lược Đồ Dữ Liệu XML Chuẩn Hóa (`03_ocr/ocr_raw/raw.xml`)

Tệp `raw.xml` đại diện cho toàn bộ luồng OCR thống nhất của tác phẩm:

```xml
<?xml version="1.0" encoding="utf-8"?>
<CORPUS WORK_ID="CPO_19">
    <PAGE NUMBER="0015">
        <LINE ID="P0015.L001" BBOX="272,887,2200,920" CONFIDENCE="0.95">夫道至虚，四大理，而有法门，妙群域。</LINE>
        <LINE ID="P0015.L002" BBOX="272,930,2200,965" CONFIDENCE="0.98">PHIÊN ÂM</LINE>
        <LINE ID="P0015.L003" BBOX="272,970,2200,1010" CONFIDENCE="0.94">Phù Đạo chí hư, tứ đại lý thường...</LINE>
    </PAGE>
</CORPUS>
```

- `ID`: Mã duy nhất cho từng dòng theo cấu trúc `P<trang:04d>.L<thứ_tự:03d>` (ví dụ: `P0015.L001`).
- `BBOX`: Tọa độ bounding box dạng `x0,y0,x1,y1` (tương đối theo kích thước ảnh hoặc pixel chuẩn hoá).
- `CONFIDENCE`: Độ tin cậy nhận dạng (từ 0.0 đến 1.0).

---

## 4. Chạy OCR Single-Stream

### 4.1. Lệnh CLI
Để chạy OCR single-stream với Qwen VLM:

```powershell
python -m src.ocr.ocr --unified --work-id CPO_19 --pages 15-20 --engine qwen --workers 4
```

Các tham số chính:
| Tham số | Ý nghĩa | Mặc định |
|---|---|---|
| `--unified` | Bật chế độ OCR single-stream | `False` |
| `--engine` / `--engines` | Tên engine nhận dạng (`qwen`, `kimhannom`, `tesseract`) | `qwen` |
| `--pages` | Danh sách trang (ví dụ: `15-20,25`) | Bắt buộc |
| `--source` | Thư mục ảnh nguồn | `data/<WORK>/02_extraction/pages` |
| `--force` | Ghi đè XML và manifest (vẫn tái sử dụng raw cache nếu hash khớp) | `False` |
| `--workers` | Số luồng xử lý song song | `4` |

### 4.2. Cấu hình qua file JSON (`configs/CPO_19.json`)
```json
{
  "ocr": {
    "engine": "qwen",
    "mode": "all",
    "prompt_type": "all",
    "keep_raw": true
  }
}
```

Hệ thống sẽ tự động phát hiện cấu hình OCR unified khi không có các trường phân hệ chữ (`chi_tra`/`vie`).

---

## 5. Cơ Chế Cache & Bảo Tồn Dữ Liệu Thô (Immutability)

- **Immutable Raw Data**: Mỗi phản hồi thô từ Qwen VLM được lưu vào `03_ocr/raw/<NNNN>.json`. Tệp này không bao giờ bị chỉnh sửa hoặc lọc bỏ.
- **Cache Key**: Cache phân biệt dựa trên hash nội dung ảnh, engine, model version, prompt và vùng crop. Nếu ảnh và cấu hình không đổi, OCR sẽ tái sử dụng cache mà không gọi lại mô hình.
- **Tạo lại XML và Text**: Khi cần cập nhật lại `raw.xml` hoặc `NNNN.txt` từ cache thô đã có, chỉ cần chạy lại lệnh kèm cờ `--force`.

---

## 6. Chế Độ Cũ (Dual-Stream & Layer-Based Tesseract - Tương Thích Ngược)

Hệ thống vẫn giữ toàn bộ code và cấu trúc hỗ trợ chế độ cũ:
- Dual-stream theo hệ chữ:
  ```powershell
  python -m src.ocr.ocr --writing-system chi_tra --work-id MY_WORK --pages 205-210
  python -m src.ocr.ocr --writing-system vie --work-id MY_WORK --pages 205-210
  ```
  Lưu tại `03_ocr/raw/chi_tra/` và `03_ocr/raw/vie/`.
- Single-layer legacy:
  ```powershell
  python -m src.ocr.ocr --layer han --work-id MY_WORK --pages 205-210
  ```
  Lưu tại `02_extraction/<layer>.xml`.

Dữ liệu lịch sử đã chạy trong các thư mục này được giữ nguyên vẹn và an toàn.
