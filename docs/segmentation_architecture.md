# Kiến trúc Phân đoạn 3 Cấp (3-Level Segmentation Architecture)

## 1. Tổng quan kiến trúc

Kiến trúc xử lý corpus Hán - Việt được cấu trúc thành ba cấp phân đoạn độc lập, hỗ trợ cả luồng **Single-Stream Qwen VLM** (khuyến nghị) lẫn luồng Dual-Stream truyền thống:

1. **Phân đoạn Tài liệu (Document Segmentation)**
2. **Phân đoạn Lớp / Đoạn (Layer / Paragraph Segmentation)**
3. **Phân đoạn Câu (Sentence Segmentation)**

Quy trình dữ liệu chuẩn cho Single-Stream:

```
OCR Unified XML (03_ocr/ocr_raw/raw.xml)
   │
   ▼
[1] Document Segmentation (src/segmentation/documents.py)
   │ ──▶ 04_segmentation/documents.xml
   │
   ▼
[2] Layer Segmentation - State Machine (src/segmentation/layers.py)
   │ ──▶ 04_segmentation/layers/<layer>.xml (han, am, nghia, tho)
   │ ──▶ 04_segmentation/section_headings.xml (các dòng tiêu đề mục)
   │
   ▼
[3] Structural Cleaning (src/cleaning/pipeline.py --structural-only)
   │ ──▶ 05_cleaning/structural/<layer>.xml
   │ ──▶ 05_cleaning/cleaning_audit.xml, footnotes.xml...
   │
   ▼
[4] Sentence Segmentation (src/segmentation/sentences.py)
   │ ──▶ 04_segmentation/sentences/<layer>.xml (bản ghi câu độc lập)
   │
   ▼
[5] Sentence-level Cleaning (src/cleaning/sentence_cleaner.py)
   │ ──▶ 05_cleaning/<layer>.xml
   │ ──▶ 05_cleaning/<layer>.txt
   │
   ▼
[6] Validation (src/validation/validate.py)
   │ ──▶ 05_cleaning/validation_report.json
   │
   ▼
[7] Alignment (src/alignment/align.py)
   │ ──▶ 06_alignment/<pair>.xml
   │
   ▼
[8] Export (src/alignment/export.py)
     ──▶ 07_export/*.tsv, *.xlsx
```

---

## 2. Chi tiết phân đoạn Single-Stream

### Cấp 1: Phân đoạn Tài liệu Single-Stream (Document Segmentation)
- **Module**: `src/segmentation/documents.py` (`load_unified_stream`, `segment_documents_unified`)
- **Nguyên tắc**: Đọc luồng dòng duy nhất từ `03_ocr/ocr_raw/raw.xml`. Không cần phân tách primary/secondary writing system (`chi_tra` vs `vie`).
- **Nhận diện ranh giới**:
  - Dựa trên quy ước tiêu đề đánh số `numbered_heading` (ví dụ: `^\\s*(\\d{1,3})\\s*[.．﹒·。]\\s+`).
  - Hoặc danh sách regex `segmentation.document.start_patterns`.
  - Hỗ trợ tài liệu kéo dài nhiều trang và nhiều tài liệu trên cùng một trang.
  - Phân biệt chính xác giữa tiêu đề tài liệu và số thứ tự của chú thích dưới chân trang (`numbered footnotes`).
- **Đầu ra**: `04_segmentation/documents.xml` (danh sách tài liệu, `document_id` ổn định, ví dụ `CPO_19.001`).

### Cấp 2: Phân đoạn Lớp Tuần Tự (Sequential Layer Segmentation State Machine)
- **Module**: `src/segmentation/layers.py` (`assign_document_unified`)
- **Nguyên tắc**: Trong một tài liệu, các lớp nội dung xuất hiện theo thứ tự cấu trúc logic:
  1. `han`: Nội dung Hán văn nguyên bản (mở đầu tài liệu, không phụ thuộc tiêu đề).
  2. `am`: Phiên âm Hán - Việt, mở đầu bằng tiêu đề mục `PHIÊN ÂM`.
  3. `nghia`: Dịch nghĩa, mở đầu bằng tiêu đề mục `DỊCH NGHĨA`.
  4. `tho`: Dịch thơ (tùy chọn), mở đầu bằng tiêu đề mục `DỊCH THƠ`.
  5. `end_patterns`: Ví dụ `CHÚ THÍCH` đóng lớp thơ hoặc nghĩa.
- **Tách Tiêu Đề Mục (Section Headings Separation)**:
  - Khi một dòng khớp với tiêu đề của lớp có `heading_mode=True` (như `am`, `nghia`, `tho`), dòng đó được tách riêng vào `04_segmentation/section_headings.xml`.
  - Nội dung của lớp chỉ chứa các câu văn thực tế, không bị nhiễm các từ khoá tiêu đề.
  - Đối với lớp `han` (không bật `heading_mode`), các dòng chữ Hán của văn bản được giữ nguyên vẹn.
- **Đầu ra**:
  - `04_segmentation/layers/han.xml`
  - `04_segmentation/layers/am.xml`
  - `04_segmentation/layers/nghia.xml`
  - `04_segmentation/layers/tho.xml` (tùy chọn)
  - `04_segmentation/section_headings.xml`
  - `04_segmentation/review_required.tsv` (các dòng nghi vấn hoặc chưa phân lớp)

---

## 3. Lược Đồ XML & Truy Vết Nguồn Dòng Gốc (Provenance)

### 3.1. Phân đoạn Lớp (`04_segmentation/layers/<layer>.xml`)
Mỗi đoạn văn bản mang định danh truy vết trực tiếp về dòng OCR thô:

```xml
<?xml version='1.0' encoding='utf-8'?>
<root>
    <FILE ID="CPO_19">
        <LAYER NAME="han">
            <SEGMENT ID="CPO_19.001.P0015.S001" DOCUMENT_ID="CPO_19.001" PAGE="15" BBOX="272,887,2200,920" CONFIDENCE="0.95" SOURCE_IDS="P0015.L001">夫道至虚，四大理，而有法门，妙群域。</SEGMENT>
            <SEGMENT ID="CPO_19.001.P0015.S002" DOCUMENT_ID="CPO_19.001" PAGE="15" BBOX="272,930,2200,965" CONFIDENCE="0.98" SOURCE_IDS="P0015.L002">若其巧能利物，度脱无量，何吕不见真形耶？</SEGMENT>
        </LAYER>
    </FILE>
</root>
```

### 3.2. Tiêu Đề Mục Được Tách Riêng (`04_segmentation/section_headings.xml`)
```xml
<?xml version='1.0' encoding='utf-8'?>
<root>
    <FILE ID="CPO_19">
        <HEADING ID="CPO_19.001.P0015.H001" DOCUMENT_ID="CPO_19.001" LAYER="am" PAGE="15" BBOX="272,1400,1000,1430" SOURCE_IDS="P0015.L005">PHIÊN ÂM</HEADING>
        <HEADING ID="CPO_19.001.P0015.H002" DOCUMENT_ID="CPO_19.001" LAYER="nghia" PAGE="15" BBOX="272,1700,1000,1730" SOURCE_IDS="P0015.L012">DỊCH NGHĨA</HEADING>
    </FILE>
</root>
```

### 3.3. Phân đoạn Câu (`04_segmentation/sentences/<layer>.xml`)
```xml
<?xml version='1.0' encoding='utf-8'?>
<root>
    <FILE ID="CPO_19">
        <LAYER NAME="am">
            <SENTENCE ID="CPO_19.am.00001" DOCUMENT_ID="CPO_19.001" PAGES="15" SEGMENT_IDS="CPO_19.001.P0015.S003" SOURCE_IDS="P0015.L006" FLAGS="">Phù Đạo chí hư, tứ đại lý thường...</SENTENCE>
        </LAYER>
    </FILE>
</root>
```

Chế độ tách câu (`--split`, mặc định theo lớp):
- `han`: `auto`. Theo từng `DOCUMENT_ID`: tài liệu có < 1 dấu kết câu (`。！？；`) mỗi 4 dòng (thơ không
  chấm câu) thì mỗi dòng là một câu; ngược lại tách theo dấu câu. Không có `auto`, thơ Hán bị gộp cả bài
  thành một câu trong khi phiên âm tách theo câu thơ, nên dóng `han-am` hỏng.
- `tho`: `line`; các lớp khác: `punct`. `punct`/`line` vẫn chọn tay được cho mọi lớp.
- Khi tách xong `han` hoặc `am` mà lớp kia đã có, tài liệu có số câu han/am lệch > 30% bị ghi cảnh báo
  (và vào `stats.han_am_mismatch_documents` trong metadata).

Bước dóng hàng tra từ điển âm Hán-Việt: chữ KHÔNG có trong từ điển (thường là giản thể OCR đọc nhầm) được
thử lại ở dạng phồn thể (OpenCC `s2t`, `pip install opencc`). Chỉ dùng khi tra; văn bản xuất ra không đổi.

### 3.4. Bảng Thuộc Tính Chuẩn

| Thuộc tính | Ý nghĩa | Ví dụ Single-Stream | Ví dụ Legacy Dual-Stream |
|---|---|---|---|
| `ID` | Định danh bản ghi | `CPO_19.am.00001` | `CPO_18.am.00001` |
| `DOCUMENT_ID` | Mã tài liệu cha | `CPO_19.001` | `CPO_18.001` |
| `SOURCE_IDS` | ID dòng OCR thô ban đầu | `P0015.L001` | `chi_tra.P205.L004`, `vie.P205.L005` |
| `SEGMENT_IDS` | ID phân đoạn đóng góp | `CPO_19.001.P0015.S001` | `CPO_18.001.P205.S002` |
| `PAGES` | Trang nguồn | `15` | `205` |

---

## 4. Cấu Hình State Machine Trong Pipeline

Cấu hình mẫu trong tệp JSON:
```json
{
  "segmentation": {
    "document": {
      "numbered_heading": {
        "enabled": true,
        "pattern": "^\\s*(\\d{1,3})\\s*[.．﹒·。]\\s+"
      }
    },
    "layers": {
      "han": {
        "description": "Nội dung Hán văn chính"
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
