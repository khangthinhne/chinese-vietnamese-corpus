# Hướng dẫn chạy OCR bằng Tesseract

Chạy lệnh từ thư mục gốc repo. Module nhận thư mục ảnh, lưu OCR thô dạng JSON và xuất XML cho các lớp `han`, `am`, `nghia`, `tho`.

## 1. Cài môi trường

Dùng Python 3.11 trở lên; ví dụ dưới đây dùng Python 3.13:

```powershell
py -3.13 -m venv .venv-ocr
.venv-ocr/Scripts/python.exe -m pip install -r requirements-ocr.txt
```

Cài [Tesseract 5](https://tesseract-ocr.github.io/tessdoc/Installation.html) nếu máy chưa có.

Khai báo đường dẫn và tải model `tessdata_best` vào môi trường local:

```powershell
$env:TESSERACT_CMD = 'C:/Program Files/Tesseract-OCR/tesseract.exe'
$ocrTessdata = Join-Path (Resolve-Path .venv-ocr).Path 'tessdata'
New-Item -ItemType Directory -Path $ocrTessdata -Force | Out-Null
Invoke-WebRequest -UseBasicParsing -Uri 'https://raw.githubusercontent.com/tesseract-ocr/tessdata_best/main/chi_tra.traineddata' -OutFile (Join-Path $ocrTessdata 'chi_tra.traineddata')
Invoke-WebRequest -UseBasicParsing -Uri 'https://raw.githubusercontent.com/tesseract-ocr/tessdata_best/main/vie.traineddata' -OutFile (Join-Path $ocrTessdata 'vie.traineddata')
$env:TESSDATA_PREFIX = $ocrTessdata
& $env:TESSERACT_CMD --list-langs
```

Chỉ cần tải model một lần. Mỗi khi mở terminal mới, khai báo lại `TESSERACT_CMD` và `TESSDATA_PREFIX`; ví dụ:

```powershell
$env:TESSERACT_CMD = 'C:/Program Files/Tesseract-OCR/tesseract.exe'
$env:TESSDATA_PREFIX = (Resolve-Path .venv-ocr/tessdata).Path
```

Module chỉ dùng Tesseract, không cần cài PaddleOCR. Adapter dùng LSTM (`--oem 1`), ghi hash model để truy vết và đặt timeout 180 giây cho mỗi lần nhận dạng.

## 2. Chuẩn bị ảnh và chạy

Mỗi file là một trang, định dạng PNG/JPG/JPEG/TIF/TIFF/BMP. Số cuối trong tên file là số trang nguồn: `page_0205.png` tương ứng trang 205. Không dùng số in trên sách để tự đổi ID. Ảnh không có số bị bỏ qua kèm cảnh báo; thiếu trang, trùng số trang hoặc ảnh nhiều frame sẽ báo lỗi.

Thay `MY_WORK`, đường dẫn ảnh và phạm vi trang bằng dữ liệu của bạn:

```powershell
.venv-ocr/Scripts/python.exe -m src.ocr.ocr --layer han --work-id MY_WORK --source data/MY_WORK/01_sources/pages --pages 205-210 --workers 1
```

Các tham số thường dùng:

| Tham số | Ý nghĩa |
|---|---|
| `--layer` | `han`, `am`, `nghia` hoặc `tho` |
| `--pages` | Bắt buộc, ví dụ `205-210,212` |
| `--region` | Vùng crop `x0,y0,x1,y1`, theo tỉ lệ 0..1; mặc định toàn trang |
| `--lang` | Mặc định `chi_tra` cho Hán, `vie` cho ba lớp còn lại |
| `--keep` | `cjk`, `latin`, `none`; mặc định theo lớp |
| `--psm` | Mặc định 6 cho khối văn bản; có thể thử 3 khi bố cục phức tạp |
| `--dpi` | Mặc định 300; chỉ khai báo độ phân giải, không phóng ảnh |
| `--workers` | Mặc định 4; dùng 1 nếu muốn hạn chế tài nguyên |
| `--low-conf` | Ngưỡng đưa trang vào danh sách rà soát, mặc định 60 |
| `--force` | Cho phép xuất lại XML và metadata, vẫn dùng cache OCR hợp lệ |

Ví dụ OCR nửa trên trang:

```powershell
.venv-ocr/Scripts/python.exe -m src.ocr.ocr --layer han --work-id MY_WORK --source data/MY_WORK/01_sources/pages --pages 205 --region 0,0,1,0.5 --workers 1
```

Nếu cần `chi_tra_vert` hoặc kết hợp ngôn ngữ như `chi_tra+vie`, cài đủ các model tương ứng. Tách Hán/Việt theo vùng ảnh hoặc bộ lọc chữ viết. Phân biệt âm/nghĩa/thơ cần chọn đúng nội dung nguồn; bộ lọc Latin không tự phân biệt ba lớp này.

## 3. Dùng config

Có thể lưu cấu hình riêng theo cấu trúc sau, dùng dấu `/` trong đường dẫn JSON:

```json
{
  "common": {
    "work_id": "MY_WORK",
    "source": "data/MY_WORK/01_sources/pages",
    "pages": "205-210",
    "workers": 1
  },
  "layers": {
    "han": { "lang": "chi_tra", "region": "0,0,1,0.5" },
    "am": { "lang": "vie", "region": "0,0.5,1,1" }
  }
}
```

Vùng crop trên chỉ là ví dụ; cần xác định theo bố cục thật. Sau khi lưu config:

```powershell
.venv-ocr/Scripts/python.exe -m src.ocr.ocr --config configs/my_ocr.json --layer han
```

Ưu tiên cấu hình: mặc định < `common` < `layers.<layer>` < CLI. `configs/CPO_18.json` là ví dụ nguồn PDF dùng chung của repo; khi OCR phải ghi đè `--source` thành thư mục ảnh hoặc dùng config riêng.

## 4. Output và chạy lại

Trong `data/<WORK_ID>/02_extraction/`:

- `02_ocr_raw/page_0205.json`: các lần nhận dạng, từng dòng, bbox theo pixel ảnh gốc, confidence và payload gốc của engine.
- `<layer>.xml`: XML thô do schema chung trong `corpus.py` tạo.
- `metadata.json`: nguồn, hash ảnh/model, tham số, thống kê, cache và các trang cần kiểm tra.

Nguồn nằm ngoài `01_sources` được sao lưu bằng helper dùng chung. Nếu thư mục đã sao lưu cùng tên có nội dung khác, dùng tên nguồn mới để tránh ghi đè nguồn cũ.

Cache phân biệt theo ảnh, lớp, phiên bản engine/model, ngôn ngữ, crop, PSM và DPI. Cùng cấu hình thì tái sử dụng; thay đổi tham số nhận dạng thì thêm kết quả mới, giữ kết quả cũ. Đổi bộ lọc, selector hoặc ngưỡng confidence không chạy OCR lại. Chạy lại khi XML đã tồn tại cần `--force`.

Khi một trang lỗi, module dừng xuất XML và giữ raw JSON đã hoàn thành để tiếp tục lần sau. Trang không có dòng sau lọc được liệt kê trong `empty_pages`; confidence thấp hoặc không xác định được liệt kê trong `low_conf_pages`. Confidence không phải độ chính xác đã kiểm chứng.

Chạy lần lượt từng lớp trong cùng tác phẩm để tránh ghi đè metadata giữa các tiến trình. OCR chỉ tạo bản nháp: chưa sửa lỗi chữ, xóa chú thích, tách câu hay dóng hàng; cần đối chiếu ảnh trước bước làm sạch.
