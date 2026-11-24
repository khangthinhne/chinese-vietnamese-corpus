# Hướng Dẫn Chuyển Đổi Sang Single-Stream Qwen VLM OCR & Segmentation Pipeline

Tài liệu này hướng dẫn chi tiết việc chuyển đổi từ kiến trúc OCR cũ (chia theo hệ chữ `chi_tra` / `vie` hoặc OCR đa lượt theo từng layer) sang kiến trúc **Single-Stream OCR sử dụng Qwen VLM** kết hợp với bộ phân đoạn tuần tự (state machine).

---

## 1. Triết Lý Thiết Kế Cốt Lõi

> **"OCR nhận dạng ký tự. Segmentation hiểu cấu trúc tài liệu."**

- **OCR một lượt duy nhất mỗi trang**: Qwen VLM nhận dạng toàn bộ văn bản xuất hiện trên trang sách (chữ Hán, phiên âm, dịch nghĩa, dịch thơ, chú thích, số trang, tiêu đề...).
- **Không phân loại theo hệ chữ ở OCR**: Tuyệt đối không dùng regex hoặc bộ lọc chữ viết để phân tách Hán / Việt tại tầng OCR. Toàn bộ chữ được bảo toàn trong một dòng chảy OCR duy nhất.
- **Phân đoạn cấu trúc đảm nhiệm phân lớp**: Tầng phân đoạn (Segmentation) căn cứ vào tiêu đề mục (`PHIÊN ÂM`, `DỊCH NGHĨA`, `DỊCH THƠ`, `CHÚ THÍCH`...) và state machine tuần tự để gán từng dòng OCR vào đúng lớp ngữ nghĩa (`han`, `am`, `nghia`, `tho`).

---

## 2. Quy Ước Đặt Tên Trang 4 Chữ Số (Four-Digit Zero-Padded Naming)

Tất cả các tệp liên quan đến trang đều tuân thủ chuẩn 4 chữ số đệm số 0 (Zero-padded 4-digit):

- Ảnh trích xuất: `02_extraction/pages/0015.png` (thay cho `page_15.png` hay `page_0015.png`)
- OCR thô từng trang: `03_ocr/raw/0015.json`
- Văn bản OCR từng trang: `03_ocr/ocr_raw_text/0015.txt`
- ID dòng OCR trong XML: `P0015.L001`, `P0015.L002`, ...

Module dùng chung: `src.common.page_naming`:
```python
from src.common.page_naming import page_stem, page_filename

stem = page_stem(15)        # "0015"
fname = page_filename(15)   # "0015.txt"
```

---

## 3. Cấu Trúc Thư Mục Mục Tiêu

```text
data/<WORK_ID>/
    01_sources/
        source.pdf
    02_extraction/
        pages/
            0015.png
            0016.png
    03_ocr/
        raw/
            0015.json
            0016.json
        ocr_raw/
            raw.xml
        ocr_raw_text/
            0015.txt
            0016.txt
        metadata.json
    04_segmentation/
        documents.xml
        layers/
            han.xml
            am.xml
            nghia.xml
            tho.xml
        section_headings.xml
        sentences/
            han.xml
            am.xml
            nghia.xml
            tho.xml
        review_required.tsv
    05_cleaning/
        structural/
            han.xml
            ...
        han.xml
        am.xml
        nghia.xml
        tho.xml
        validation_report.json
    06_alignment/
    07_export/
```

---

## 4. Chuyển Đổi Tệp Cấu Hình (Configuration Migration)

### 4.1. Cấu hình cũ (Dual-Stream OCR)
```json
{
  "ocr": {
    "chi_tra": {
      "engine": "tesseract",
      "lang": "chi_tra",
      "keep": "cjk"
    },
    "vie": {
      "engine": "tesseract",
      "lang": "vie",
      "keep": "latin"
    }
  },
  "segmentation": {
    "layers": {
      "han": {
        "source": "chi_tra"
      },
      "am": {
        "source": "vie",
        "heading_mode": true
      }
    }
  }
}
```

### 4.2. Cấu hình mới (Unified Single-Stream Qwen VLM)
```json
{
  "ocr": {
    "engine": "qwen",
    "mode": "all",
    "prompt_type": "all",
    "keep_raw": true
  },
  "segmentation": {
    "document": {
      "numbered_heading": {
        "enabled": true,
        "pattern": "^\\s*(\\d{1,3})\\s*[.．﹒·。]\\s+"
      }
    },
    "layers": {
      "han": {
        "description": "Nguyên văn Hán văn"
      },
      "am": {
        "heading_mode": true,
        "start_patterns": ["^[lI|]?\\s*PHIÊN ÂM"],
        "end_patterns": ["^[lI|]?\\s*DỊCH NGHĨA", "^[lI|]?\\s*DỊCH THƠ", "^[lI|]?\\s*CHÚ THÍCH"]
      },
      "nghia": {
        "heading_mode": true,
        "start_patterns": ["^[lI|]?\\s*DỊCH NGHĨA"],
        "end_patterns": ["^[lI|]?\\s*DỊCH THƠ", "^[lI|]?\\s*CHÚ THÍCH"]
      },
      "tho": {
        "optional": true,
        "heading_mode": true,
        "start_patterns": ["^[lI|]?\\s*DỊCH THƠ"],
        "end_patterns": ["^[lI|]?\\s*CHÚ THÍCH"]
      }
    }
  }
}
```

**Các điểm chính cần lưu ý:**
1. Khối `ocr` không còn lồng `chi_tra` / `vie`. Chỉ định trực tiếp `engine: "qwen"` và `mode: "all"`.
2. Khối `segmentation.layers` không còn trường `"source"`.
3. Lớp `han` không có `start_patterns` tiêu đề sẽ nhận các dòng nội dung chính mở đầu tài liệu.
4. Các lớp `am`, `nghia`, `tho` bật `heading_mode: true`: tiêu đề mục nhận dạng được sẽ tự động chuyển vào `04_segmentation/section_headings.xml`, không bị lẫn vào nội dung câu/đoạn văn bản.

---

## 5. Lệnh Thực Thi Mới

### 5.1. Chạy OCR Single-Stream
```powershell
python -m src.ocr.ocr --unified --work-id CPO_19 --pages 15-20 --engine qwen
```
Hoặc thông qua tệp cấu hình:
```powershell
python -m src.ocr.ocr --config configs/CPO_19.json --unified
```

### 5.2. Chạy Phân đoạn Tài liệu & Lớp
Pipeline tự động nhận diện nếu tồn tại `03_ocr/ocr_raw/raw.xml` để chuyển sang chế độ phân đoạn single-stream:
```powershell
python -m src.segmentation.pipeline --config configs/CPO_19.json
```

### 5.3. Chạy Toàn Bộ Quy Trình
```powershell
python -m src.pipeline --config configs/CPO_19.json
```

---

## 6. Tính Tương Thích Ngược & An Toàn Dữ Liệu Lịch Sử

1. **Bảo tồn kho dữ liệu cũ**: Hệ thống không xóa thư mục cũ `03_ocr/raw/chi_tra/` và `03_ocr/raw/vie/`. Dữ liệu lịch sử được giữ nguyên trạng vĩnh viễn.
2. **Khả năng chạy kép (Dual Support)**: Nếu tệp cấu hình chứa `ocr.chi_tra` và `ocr.vie`, hệ thống tự động chạy theo quy trình tách hệ chữ truyền thống. Khi cấu hình chỉ định `ocr.engine` hoặc chạy cờ `--unified`, hệ thống kích hoạt luồng Single-Stream.
3. **Truy vết dòng gốc (Provenance)**:
   - Dòng cũ: `SOURCE_IDS="chi_tra.P205.L001"` hoặc `SOURCE_IDS="vie.P205.L002"`.
   - Dòng mới (Unified): `SOURCE_IDS="P0015.L001"`.
   Tất cả các bước tiếp theo (Structural Cleaning, Sentence Segmentation, Sentence Cleaner, Validation, Alignment, Export) đều tương thích hoàn toàn với cả hai định dạng ID này.
