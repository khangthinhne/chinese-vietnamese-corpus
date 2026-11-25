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
| `--engine` / `--engines` | Tên engine nhận dạng (`qwen`, `kimhannom`, `tesseract`, `paddlevl`) | `qwen` |
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
  Lưu tại `03_ocr/raw/chi_tra/` và `03_ocr/raw/vie/`; XML là `ocr_raw/chi_tra.xml` và `ocr_raw/vi.xml`.
- Single-layer legacy (`--layer han|am|nghia|tho` ghi `02_extraction/<layer>.xml`) đã bị tắt.
  `--layer han|vie` giờ là OCR theo luồng Hán/Việt, xem mục 7.

Dữ liệu lịch sử đã chạy trong các thư mục này được giữ nguyên vẹn và an toàn.
---

## 7. Hai Luồng Hán / Việt (`han.xml`, `vie.xml`)

Thay đổi có chủ đích so với nguyên tắc Single-Stream: ngoài `raw.xml`, mỗi trang có thể được OCR thêm bằng
engine chuyên cho từng hệ chữ, rồi lọc theo hệ chữ thành hai luồng riêng. `raw.xml`, `ocr_raw_text/NNNN.txt`
và segmentation hiện tại **không đổi**; việc nối `han`/`vie` vào segmentation chưa làm.

| Luồng | Engine | Môi trường | Bộ lọc (`src/ocr/script_filter.py`) |
|---|---|---|---|
| `han` | `paddlevl` (PaddleOCR-VL-1.6) | `.venv-paddle` | giữ dòng có >= 1 chữ Hán; trong dòng chỉ giữ chữ Hán + dấu câu CJK |
| `vie` | `qwen` (Qwen2.5-VL-3B) | `my_nlp` | xoá mọi chữ Hán + dấu câu CJK/full-width, dọn ngoặc rỗng/nháy mồ côi/khoảng trắng; bỏ dòng không còn chữ cái Latin |

Lớp `han` còn loại (và ghi vào `manifests/build_han.json` -> `rejected`, kèm log) dòng có chữ Hán nhưng thực
chất là dòng tiếng Việt hoặc rác OCR, xét trên dòng GỐC (chỉ đếm chữ cái Latin, không đếm số/dấu câu):
- chữ Hán / (chữ Hán + chữ Latin) < 0.3 (`--han-min-cjk-share`), vd khảo dị `a. Bản A.404: Hội khê 洄溪.`;
- có chữ Latin mà < 2 chữ Hán (`--han-min-cjk-with-latin`), vd paddlevl đọc nhầm `5. 其二` thành `J. 一`.
Trên mẫu 20 trang của CPO_18/19/20 luật loại 29 dòng, không dòng Hán thật nào. Đặt cả hai về 0 để tắt.
paddlevl ngang đôi khi xuất MỘT dòng in thành nhiều dòng một chữ (`玄
麻
...`); `build_layers` gộp lại khi
MỌI dòng của đơn vị (dải) đều đúng 1 ký tự (`merged` trong manifest).

Cả hai chuẩn hoá NFC (NFC ánh xạ chữ CJK Compatibility U+F900–FAFF về chữ Unified tương ứng); không đổi
giản thể/phồn thể. Mỗi trang dùng đúng MỘT run của engine của luồng, không bỏ phiếu theo dòng.

### 7.1. Bố cục
```text
03_ocr/
    raw/
        0015.json               # run cũ (unified), CHỈ ĐỌC: tra cache + chọn run Qwen
        paddlevl/0015.json      # run mới của paddlevl (append-only, cùng schema)
        qwen/0015.json          # run Qwen mới, chỉ khi chưa có run cùng khoá trong raw/0015.json
    manifests/
        ocr_han.json            # lần OCR luồng han gần nhất theo trang (khoá run, trạng thái, lỗi)
        ocr_vie.json
        build_han.json          # số liệu lọc theo trang + cảnh báo
        build_vie.json
    ocr_raw/
        raw.xml                 # không đổi
        han.xml                 # chỉ chữ Hán, nguồn paddlevl
        vie.xml                 # chỉ chữ Latin/Việt, nguồn qwen
    ocr_raw_text/
        0015.txt                # không đổi
        han/0015.txt            # chỉ tạo cho trang CÓ dòng Hán
        vie/0015.txt            # chỉ tạo cho trang CÓ dòng Việt
```
Run mới không bao giờ được ghi vào `raw/NNNN.json`: file đó bất biến, và `raw.xml` nhúng mọi run của nó
trong `<RUNS>`, nên thêm run paddlevl vào đó sẽ làm đổi `raw.xml` ở lần dựng sau. Khoá cache (`describe()`)
tính y hệt chế độ unified, nên run Qwen đã có được dùng lại nguyên vẹn.

### 7.2. Lệnh
Cả một work (trích ảnh -> han -> vie -> build -> check, resume được): `scripts/ocr_work.ps1` / `scripts/ocr_work.sh`,
hướng dẫn đầy đủ cho thành viên nhóm ở `docs/ocr_runbook.md`. Từng bước:
OCR và dựng XML tách riêng. Dựng XML/TXT chỉ đọc JSON thô, không import engine/torch, chạy ở môi trường nào cũng được.
```powershell
# .venv-paddle (Hán in ngang cả ở CPO_18/19/20 -> horizontal)
$env:PADDLEVL_ORIENTATION = "horizontal"
python -m src.ocr.ocr --config configs/CPO_20.json --layer han --engines paddlevl --pages 1-280

# my_nlp
python -m src.ocr.ocr --config configs/CPO_20.json --layer vie --engines qwen --pages 1-280

# môi trường bất kỳ
python -m src.ocr.build_layers --config configs/CPO_20.json               # JSON thô -> han.xml, vie.xml, TXT
python -m src.ocr.build_layers --config configs/CPO_20.json --layers vie --pages 5-60
python -m src.ocr.build_layers --config configs/CPO_20.json --report-only # chỉ kiểm tra + bảng tổng hợp
```
- `--layer han` chỉ nhận `--engines paddlevl`, `--layer vie` chỉ nhận `--engines qwen`.
- `--pages auto` = `ocr.streams.pages` của config (thiếu thì mọi ảnh đã trích). Hướng chữ paddlevl lấy từ
  `ocr.streams.paddlevl_orientation` nếu chưa đặt `PADDLEVL_ORIENTATION` (biến môi trường luôn thắng).
- `--skip-done`: bỏ qua trang đã có run của engine ở BẤT KỲ khoá/phiên bản adapter nào (có >= 1 đơn vị ảnh), để
  resume sau khi tăng `ADAPTER_VERSION` mà không OCR lại cả work. Trạng thái `done` trong manifest.
- `--log-file`: ghi thêm log ra file. Mỗi dòng tiến độ có s/trang và thời gian còn lại ước tính.
- Ảnh lấy từ `--source` nếu là thư mục, ngược lại từ `02_extraction/pages` (config trỏ `source` tới PDF).
  Trang chưa có ảnh được bỏ qua và ghi vào `manifests/ocr_<layer>.json` (`missing_images`).
- Resume: trang đã có run cùng khoá (cùng `describe()`/`ADAPTER_VERSION`) thì bỏ qua; `--force-reocr`
  chạy lại và APPEND run mới. Trang lỗi được ghi log + `error_pages`, batch chạy tiếp; lệnh trả mã 1 ở cuối.
- `build_layers` mặc định dựng mọi trang có JSON thô; `--pages` của config không giới hạn bước này.
- Chế độ hệ chữ cũ (`--writing-system chi_tra`) nay ghi `ocr_raw/chi_tra.xml` + `ocr_raw_text/chi_tra/NNN.txt`
  (trước đây là `han.xml` + `ocr_raw_text/han/`, trùng tên với luồng Hán). CPO_18 đã được chuyển: file giữ
  nguyên từng byte, `02_extraction/metadata.json` cập nhật đường dẫn + fingerprint của bước segmentation.
- Với dữ liệu cũ chưa chuyển, `build_layers` không ghi đè `han.xml` không phải của nó nếu không có `--force`.

### 7.3. Chọn run cho mỗi trang
1. Khoá ghi trong `manifests/ocr_<layer>.json` (lần OCR luồng gần nhất).
2. Khoá `selected_key` trong manifest unified nếu `selected_engine` trùng engine (Qwen của `raw.xml`).
3. Nếu chỉ có một khoá: run đó; nhiều khoá khác nhau: trong các run HỢP LỆ cho luồng (`src/ocr/run_quality.py`:
   không có đơn vị `truncated`/`loop_stopped` chứa chữ của luồng đó; Qwen lặp trên dải toàn chữ Hán không làm hỏng
   luồng vie), lấy run mới nhất (`created_at`); không run nào hợp lệ thì lấy run mới nhất.

Kho hệ chữ cũ chỉ được đọc theo đúng luồng: `han` đọc `raw/chi_tra/`, `vie` đọc `raw/vie/`.

### 7.4. Schema XML
Giống `raw.xml` (`root/FILE/OCR` rồi `PAGE/LINE`), `OCR` có `MODE="layer" LAYER="han|vie" ENGINE=...`.
`LINE` có `ID="PNNNN.LNNN"` đánh lại tuần tự SAU khi lọc, `ORDER`, `NO`, `SRC_LINE` (số thứ tự dòng gốc của
engine, 1-based, trước khi lọc; một dòng engine chứa ký tự xuống dòng, như run Qwen cũ của CPO_18, được tách
thành nhiều LINE cùng `SRC_LINE`), `BBOX`, `CONFIDENCE` (của dòng gốc). `PAGE` thêm `N_SRC_LINES`, `N_DROPPED`,
`CJK_REMOVED` (vie). `RUNS/RUN` chỉ giữ provenance (khoá, tham số, nguồn, `SELECTION`), không chép lại dòng
hay payload chưa lọc, nên `vie.xml` không chứa ký tự CJK nào. Chỉ trang có ít nhất một dòng mới có `PAGE`.

### 7.5. Kiểm tra và cảnh báo
Sau mỗi lần dựng (và với `--report-only`): `vie.xml` 0 ký tự CJK; mọi dòng `han.xml` có chữ Hán; ID đúng định
dạng, đúng trang/thứ tự, không trùng; TXT tồn tại đúng cho các trang có `PAGE` và trùng nội dung; tổng dòng XML
= tổng dòng TXT; không trộn engine. Lỗi -> mã thoát 1. Cảnh báo (không chặn):
- trang có run nhưng không ra dòng Hán lẫn dòng Việt;
- trang có run paddlevl nhưng không ra dòng Hán nào;
- trang vie mất > 30% dòng KHÔNG chứa chữ Hán (tối thiểu 2 dòng). Dòng Hán thuần bị bỏ khỏi vie là đúng thiết
  kế nên không tính; dòng có chữ Latin không bao giờ bị bỏ.

