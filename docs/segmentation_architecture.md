# Kiến trúc Phân đoạn 3 Cấp (3-Level Segmentation Architecture)

## 1. Tổng quan kiến trúc

Kiến trúc xử lý corpus Hán - Việt được tái cấu trúc để tách biệt hoàn toàn ba cấp phân đoạn:
1. **Phân đoạn Tài liệu (Document Segmentation)**
2. **Phân đoạn Lớp / Đoạn (Layer / Paragraph Segmentation)**
3. **Phân đoạn Câu (Sentence Segmentation)**

Quy trình dữ liệu chuẩn:

```
OCR Raw (03_ocr/raw + manifests)
   │
   ▼
[1] Document Segmentation (src/segmentation/documents.py)
   │
   ▼
[2] Layer / Paragraph Segmentation (src/segmentation/layers.py)
   │ ──▶ 04_segmentation/documents.xml
   │ ──▶ 04_segmentation/layers/<layer>.xml (Paragraph/Layer segments)
   │
   ▼
[3] Structural Cleaning (src/cleaning/pipeline.py --structural-only)
   │ ──▶ 05_cleaning/structural/<layer>.xml
   │ ──▶ 05_cleaning/cleaning_audit.xml, review_required.xml, footnotes.xml...
   │
   ▼
[4] Sentence Segmentation (src/segmentation/sentences.py)
   │ ──▶ 04_segmentation/sentences/<layer>.xml (Sentence records độc lập)
   │
   ▼
[5] Sentence-level Cleaning (src/cleaning/sentence_cleaner.py)
   │ ──▶ 05_cleaning/<layer>.xml (Cleaned sentences cho aligner)
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

## 2. Chi tiết từng cấp phân đoạn & làm sạch

### Cấp 1: Phân đoạn Tài liệu (Document Segmentation)
- **Module**: `src/segmentation/documents.py`
- **Nhiệm vụ**: Xác định ranh giới tài liệu độc lập trong tập trang PDF/OCR dựa trên hệ chữ chính (`chi_tra` nếu có, ngược lại `vie`).
- **Nguyên tắc**: Sử dụng `segmentation.document.start_patterns` hoặc quy ước đánh số `numbered_heading`. Ghép trang system phụ với tài liệu.
- **Đầu ra**: `04_segmentation/documents.xml` (danh sách tài liệu, `document_id` ổn định, vd `CPO_18.001`, `CPO_18.002`).

### Cấp 2: Phân đoạn Lớp / Đoạn (Layer / Paragraph Segmentation)
- **Module**: `src/segmentation/layers.py`, `src/segmentation/pipeline.py`
- **Nhiệm vụ**: Phân bổ dòng OCR vào các lớp văn bản (`han`, `am`, `nghia`, `tho`) theo cấu hình state machine và pattern mở/đóng section (`start_patterns`, `end_patterns`).
- **Nguyên tắc**: Bảo tồn ranh giới đoạn/dòng OCR gốc, bounding box, độ tin cậy và `source_ids`.
- **Đầu ra**: `04_segmentation/layers/<layer>.xml` với thẻ `<SEGMENT>`. **Không bị ghi đè bởi bước tách câu.**

### Cấp 3: Làm sạch Cấu trúc (Structural Cleaning)
- **Module**: `src/cleaning/pipeline.py` (`run_structural_cleaning`, CLI `--structural-only`)
- **Nhiệm vụ**: Thực hiện các phép làm sạch phụ thuộc ngữ cảnh trang, khối hoặc đoạn:
  - Chuẩn hoá Unicode NFC
  - Lọc trang (trang trắng, trang loại thủ công, header/footer lặp)
  - Trích xuất metadata cấp tài liệu (tiêu đề, tác giả, quy trách)
  - Phát hiện và loại bỏ dòng trùng lặp OCR
  - Phát hiện và xử lý chú thích (đơn dòng, nhiều dòng, trích dẫn thư mục, qua trang)
  - Khử nhiễu ký tự OCR và chỉ mục chú thích trong dòng
  - Tái tạo ranh giới trang (nối từ đứt gãy `dehyphenated`, đánh cờ `sentence_continuation`)
  - Lọc dòng theo pattern
- **Nguyên tắc**: **Không tách câu ở giai đoạn này.**
- **Đầu ra**: `05_cleaning/structural/<layer>.xml`, nhật ký kiểm toán `cleaning_audit.xml`, `review_required.xml`, `footnotes.xml`, `duplicates.xml`, `reconstruction.xml`.

### Cấp 4: Phân đoạn Câu (Sentence Segmentation)
- **Module**: `src/segmentation/sentences.py`
- **Nhiệm vụ**:
  - Dựng dòng chảy ký tự (`build_stream`) theo từng tài liệu độc lập (`document_id`).
  - Tách câu chữ Hán (`split_han`): dựa trên dấu chấm ngắt câu CJK (`。！？；﹖﹕﹒﹤`) và dấu đóng ngoặc (`」』”’）》`).
  - Tách câu tiếng Việt / âm (`split_vi`): dựa trên dấu câu `.?!…;:` kết hợp từ điển viết tắt (`VI_ABBREV`), số thứ tự, tên riêng.
  - Hỗ trợ câu trải dài nhiều dòng và nhiều trang (`cross_page`).
  - Giữ nguyên truy vết: `document_id`, `segment_ids`, `source_ids`, `pages`, `raw_text`.
  - Đánh cờ cảnh báo (`no_terminal_punct`, `long`, `uncertain`, `cross_page`) thay vì âm thầm sửa.
- **Đầu ra**: `04_segmentation/sentences/<layer>.xml`.

### Cấp 5: Làm sạch Cấp câu (Sentence-level Cleaning)
- **Module**: `src/cleaning/sentence_cleaner.py`
- **Nhiệm vụ**:
  - Chuẩn hoá dấu câu, khoảng cách, khoảng trắng CJK ở cấp câu đơn lẻ mà không làm mất truy vết nguồn.
  - Kiểm tra điều kiện tối thiểu của câu (tỉ lệ CJK cho chữ Hán, ký tự chữ-số cho tiếng Việt).
  - Gắn cờ cảnh báo câu thiếu dấu kết thúc hoặc quá dài.
- **Đầu ra**: `05_cleaning/<layer>.xml` (bản câu chuẩn cho dóng hàng) và `05_cleaning/<layer>.txt`.

### Cấp 6: Kiểm tra Hợp lệ (Validation)
- **Module**: `src/validation/validate.py`
- **Nhiệm vụ**: Kiểm tra tính toàn vẹn của documents, paragraph segments, sentence records, cleaned sentences và tính sẵn sàng của các cặp dóng hàng.
- **Đầu ra**: `05_cleaning/validation_report.json`.

---

## 3. Lược đồ XML & Ví dụ

### 3.1. Phân đoạn Đoạn/Lớp (`04_segmentation/layers/<layer>.xml`)

```xml
<?xml version='1.0' encoding='utf-8'?>
<root>
    <FILE ID="CPO_18">
        <LAYER NAME="han">
            <SEGMENT ID="CPO_18.001.P205.S002" DOCUMENT_ID="CPO_18.001" PAGE="205" WRITING_SYSTEM="chi_tra" BBOX="272,887,2200,1368" CONFIDENCE="67.5" SOURCE_IDS="chi_tra.P205.L004">夫道至虚，四大理，而有法门，妙群域。 若其巧能利物，度脱无量，何吕不见真形耶？</SEGMENT>
            <SEGMENT ID="CPO_18.001.P205.S003" DOCUMENT_ID="CPO_18.001" PAGE="205" WRITING_SYSTEM="chi_tra" BBOX="9,1441,2202,1712" CONFIDENCE="81.99" SOURCE_IDS="chi_tra.P205.L005">今就尋西方根源，伏願大和尚垂懷，允納下心，無惜神誥。</SEGMENT>
        </LAYER>
    </FILE>
</root>
```

### 3.2. Phân đoạn Câu (`04_segmentation/sentences/<layer>.xml`)

```xml
<?xml version='1.0' encoding='utf-8'?>
<root>
    <FILE ID="CPO_18">
        <LAYER NAME="han">
            <SENTENCE ID="CPO_18.han.00001" DOCUMENT_ID="CPO_18.001" PAGES="205" SEGMENT_IDS="CPO_18.001.P205.S002" SOURCE_IDS="chi_tra.P205.L004" FLAGS="">夫道至虚，四大理，而有法门，妙群域。</SENTENCE>
            <SENTENCE ID="CPO_18.han.00002" DOCUMENT_ID="CPO_18.001" PAGES="205" SEGMENT_IDS="CPO_18.001.P205.S002" SOURCE_IDS="chi_tra.P205.L004" FLAGS="">若其巧能利物，度脱无量，何吕不见真形耶？</SENTENCE>
            <SENTENCE ID="CPO_18.han.00003" DOCUMENT_ID="CPO_18.001" PAGES="205" SEGMENT_IDS="CPO_18.001.P205.S003" SOURCE_IDS="chi_tra.P205.L005" FLAGS="">今就尋西方根源，伏願大和尚垂懷，允納下心，無惜神誥。</SENTENCE>
        </LAYER>
    </FILE>
</root>
```

### 3.3. Quy ước Lược đồ Thuộc tính XML Chuẩn (Canonical XML Schema)

| Thuộc tính | Ý nghĩa | Phạm vi áp dụng | Ví dụ |
|---|---|---|---|
| `ID` | Định danh duy nhất của bản ghi hiện tại | Mọi bản ghi (segment, sentence, alignment) | `ID="CPO_18.han.00001"` |
| `DOCUMENT_ID` | Mã tài liệu cha sở hữu bản ghi | Segment, Sentence | `DOCUMENT_ID="CPO_18.001"` |
| `SEGMENT_IDS` | Danh sách ID phân đoạn đoạn/lớp đóng góp vào câu | Bản ghi câu (`SENTENCE`) | `SEGMENT_IDS="CPO_18.001.P205.S002"` |
| `SOURCE_IDS` | Danh sách ID dòng OCR thô ban đầu | Segment, Sentence | `SOURCE_IDS="chi_tra.P205.L004"` |
| `PAGES` | Danh sách trang PDF/ảnh nguồn | Bản ghi câu (`SENTENCE`) | `PAGES="205"` |
| `SRC_IDS` | Danh sách ID câu nguồn của cặp dóng | Cặp dóng hàng (`ALIGNMENT`) | `SRC_IDS="CPO_18.han.00001"` |
| `TGT_IDS` | Danh sách ID câu đích của cặp dóng | Cặp dóng hàng (`ALIGNMENT`) | `TGT_IDS="CPO_18.nghia.00001"` |

> [!NOTE]
> Thuộc tính `SRC_IDS` **tuyệt đối không** được dùng trong bản ghi `<SENTENCE>` (đã thay bằng `SEGMENT_IDS` và `SOURCE_IDS`). `SRC_IDS` và `TGT_IDS` chỉ dành riêng cho thẻ `<ALIGNMENT>` trong file dóng hàng parallel.

### 3.4. Câu đã Làm sạch cho Dóng hàng (`05_cleaning/<layer>.xml`)

```xml
<?xml version='1.0' encoding='utf-8'?>
<root>
    <FILE ID="CPO_18">
        <LAYER NAME="am">
            <SENTENCE ID="CPO_18.am.00001" DOCUMENT_ID="CPO_18.001" PAGES="205" SEGMENT_IDS="CPO_18.001.P205.L005" SOURCE_IDS="vie.P205.L013">Phù Đạo xứ thanh hư, tứ đại lý thường, nhi hữu pháp môn, diệu xuất quần vực.</SENTENCE>
            <SENTENCE ID="CPO_18.am.00002" DOCUMENT_ID="CPO_18.001" PAGES="205" SEGMENT_IDS="CPO_18.001.P205.L005" SOURCE_IDS="vie.P205.L013">Nhược xưng kỳ xảo năng lợi vật, độ thoát vô lượng vì giáo, hà dĩ bất kiến chân hình ư thế?</SENTENCE>
        </LAYER>
    </FILE>
</root>
```

---

## 4. Lệnh CLI Điều phối

Chạy toàn bộ pipeline tự động:
```bash
python -m src.pipeline --config configs/CPO_18.json
```

Chạy từng bước độc lập:
```bash
# 1. Phân đoạn tài liệu và lớp
python -m src.pipeline --config configs/CPO_18.json --steps segment

# 2. Làm sạch cấu trúc
python -m src.pipeline --config configs/CPO_18.json --steps clean

# 3. Phân đoạn câu và làm sạch câu
python -m src.pipeline --config configs/CPO_18.json --steps sentence

# 4. Kiểm tra tính hợp lệ
python -m src.pipeline --config configs/CPO_18.json --steps validate

# 5. Dóng hàng
python -m src.pipeline --config configs/CPO_18.json --steps align

# 6. Xuất bản bàn giao
python -m src.pipeline --config configs/CPO_18.json --steps export
```

Lọc theo lớp hoặc cặp:
```bash
python -m src.pipeline --config configs/CPO_18.json --steps clean,sentence --layers han
python -m src.pipeline --config configs/CPO_18.json --steps align,export --pairs han-am
```
