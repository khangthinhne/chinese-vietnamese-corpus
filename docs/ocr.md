# OCR và weighted voting

## Weighted voting độc lập (v1)

Khi đã có kết quả từ nhiều engine, dùng module voting mà không cần cài/chạy engine OCR:

```powershell
python -m src.ocr.voting --input input.json --output voted.json
```

Thêm `--force` để ghi đè output. Input và output phải là hai file khác nhau. CLI đọc/ghi
UTF-8, xuất chữ Hán trực tiếp, báo lỗi trên stderr và trả mã 1 khi dữ liệu/I/O không hợp lệ.
V1 chưa nối vào `--select` của pipeline OCR hiện tại.

Ví dụ `input.json` (mỗi file chứa một trang):

```json
{
  "page_id": "CPO_19_page_001",
  "engine_weights": {"paddleocr": 0.25, "qwen": 0.50, "tesseract": 0.25},
  "engine_results": [
    {"engine": "paddleocr", "lines": [
      {"line_id": "line_001", "text": "天地玄黃", "bbox": [100, 120, 400, 160], "confidence": 0.96}
    ]},
    {"engine": "qwen", "lines": [
      {"line_id": "line_001", "text": "天地玄黄", "bbox": [102, 121, 402, 161], "confidence": null}
    ]},
    {"engine": "tesseract", "lines": [
      {"line_id": "line_001", "text": "天地玄黃", "bbox": [99, 119, 401, 160], "confidence": 0.89}
    ]}
  ]
}
```

API Python thuần, không sửa input và không đọc/ghi file:

```python
from src.ocr.voting import vote_page

result = vote_page(payload)  # dict -> dict; dữ liệu sai gây ValueError kèm đường dẫn trường
```

Hợp đồng đầu vào:

- `line_id` giống nhau phải trỏ tới cùng một dòng ảnh; bước ghép dòng thuộc phía xuất
  dữ liệu. Voting không đối sánh bbox. ID đầu ra được sắp theo chuỗi; dùng số có đệm 0
  như `line_001` để giữ thứ tự đọc. Bbox phải dùng cùng hệ tọa độ ảnh gốc.
- `page_id`, tên engine, `line_id` là chuỗi không rỗng; `text` là chuỗi, được phép rỗng.
  `bbox` và `confidence` có thể thiếu/null. Bbox có dạng `[x0,y0,x1,y1]`, tọa độ hữu hạn,
  không âm, `x0<x1`, `y0<y1`.
- Trọng số hữu hạn, không âm, tổng lớn hơn 0; không cần tổng bằng 1. Mỗi engine có kết quả
  phải có trọng số. Không chấp nhận engine trùng tên hoặc ID dòng trùng trong một engine.
  Engine mới không cần đăng ký trong registry OCR của repo.
- Confidence chỉ là số hữu hạn hoặc null, giữ nguyên thang riêng của nguồn (0..1 hoặc
  0..100), không nhân vào phiếu và không so sánh giữa engine.
- Engine thiếu kết quả/dòng không bỏ phiếu cho dòng đó. `text=""` là một phiếu rỗng rõ
  ràng, tham gia bằng GAP. Engine trọng số 0 không tạo dòng/cột hoặc ảnh hưởng kết quả.

Thuật toán chuẩn hóa NFC rồi căn chỉnh ký tự Unicode bằng progressive global alignment
với chi phí weighted sum-of-pairs. Engine được thêm theo trọng số giảm dần, rồi tên tăng
dần. Đường alignment bằng điểm ưu tiên ghép chéo, tiêu thụ cột cũ, rồi chèn ký tự mới.
Do là progressive alignment, đây không phải bảo đảm tối ưu toàn cục cho mọi chuỗi; các
đoạn ký tự lặp hoặc OCR khác nhau nhiều vẫn cần đối chiếu ảnh. Không chuyển giản/phồn,
không bỏ khoảng trắng/dấu câu và không dùng mô hình ngôn ngữ để sửa chữ.

Mỗi cột cộng trọng số cho các ký tự, kể cả GAP (`null`). Hòa tổng điểm thì ưu tiên ứng
viên được engine có trọng số riêng cao nhất ủng hộ; nếu vẫn hòa thì theo tên engine tăng
dần. GAP thắng sẽ không sinh ký tự. Cộng điểm bằng Decimal để tránh sai lệch float khi
xét hòa; các tỷ lệ đầu ra là số JSON.

Đầu ra có `schema_version`, `algorithm_version`, `page_id`, `method`, `engine_weights`,
`lines`, `needs_review`, `review_reasons`. Mỗi dòng gồm:

| Trường | Ý nghĩa |
|---|---|
| `text`, `line_id` | Văn bản hợp nhất và ID nguồn chung |
| `bbox`, `bbox_engine` | Bbox từ nguồn có trọng số cao nhất có bbox hợp lệ; null nếu thiếu |
| `sources` | Bản sao các dòng gốc kèm tên engine, giữ text/bbox/confidence để truy vết |
| `columns` | Từng cột alignment: `selected`, `support`, `tie`, các `candidates` có token/trọng số/engine ủng hộ |
| `agreement` | Trung bình tỷ lệ phiếu thắng trên mọi cột, kể cả GAP thắng; null nếu không có cột |
| `coverage` | Trọng số engine có dòng / tổng trọng số cấu hình, tính cả engine vắng trong mẫu số |
| `needs_review`, `review_reasons` | Cờ và lý do cần đối chiếu ảnh |

`alignment_index` và `output_index` trong mỗi cột bắt đầu từ 0; `output_index=null` khi
GAP thắng. `support` là điểm thắng chia tổng trọng số tham gia dòng. Không sinh
`confidence` tổng hợp: mức đồng thuận không phải xác suất nhận dạng đúng.

Lý do review gồm `disagreement`, `tie`, `incomplete_coverage`, `single_engine`,
`empty_text`; trang không có dòng từ engine trọng số dương có lý do `empty_page`.
Không tự loại các dòng cần review khỏi kết quả.

Ví dụ trên xuất `天地玄黄`, `agreement=0.875`, `coverage=1`, bbox của Qwen và
`review_reasons=["disagreement", "tie"]`: chữ cuối hòa 0.50–0.50, Qwen có trọng số
riêng lớn nhất. Nếu Qwen vắng, hai engine còn lại xuất `天地玄黃`, `agreement=1` nhưng
`coverage=0.50` và vẫn cần review.

Chạy kiểm thử voting bằng `python -m pytest tests/test_ocr_voting.py`. Trọng số hiện do
người dùng cấu hình; cần ground truth và đánh giá CER riêng để kiểm chứng cải thiện OCR.

## Chạy OCR bằng Tesseract

Chạy lệnh từ thư mục gốc repo. Module nhận thư mục ảnh, lưu OCR thô dạng JSON; chế độ mới
xuất kho thô theo hệ chữ (`chi_tra`, `vie`) cho bước phân đoạn, chế độ cũ xuất XML cho từng
lớp `han`, `am`, `nghia`, `tho`.

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

## 2b. Chế độ mới: OCR theo HỆ CHỮ (khuyến nghị)

Chế độ mới chạy OCR đúng MỘT lần cho mỗi hệ chữ viết (`chi_tra`, `vie`) thay vì 3 lần cho
`han/am/nghia`, rồi để bước phân đoạn cắt tài liệu/lớp:

```powershell
.venv-ocr/Scripts/python.exe -m src.ocr.ocr --writing-system chi_tra --work-id MY_WORK `
  --source data/MY_WORK/02_extraction/pages --pages 205-210 --workers 4
.venv-ocr/Scripts/python.exe -m src.ocr.ocr --writing-system vie --work-id MY_WORK `
  --source data/MY_WORK/02_extraction/pages --pages 205-210 --workers 4
```

- `--writing-system` thay cho `--layer`; không tạo `<layer>.xml`.
- Mặc định `lang` = hệ chữ, `keep` = `cjk` (chi_tra) / `latin` (vie); có thể ghi đè.
- Output: `03_ocr/raw/<writing_system>/page_XXXX.json` (append-only, giữ bbox/confidence/payload
  engine) và `03_ocr/manifests/<writing_system>.json` (engine, tham số, trang lỗi/rỗng).
- Vì ảnh nằm trong `02_extraction/pages` của chính work, chế độ mới KHÔNG copy lại vào
  `01_sources`; nguồn PDF/ảnh gốc đã được extract đăng ký trước đó.
- Cache dùng băm tham số (ảnh, hệ chữ, engine/model, lang, crop, psm, dpi). Chạy lại cùng tham
  số chỉ đọc cache. `--force` ghi lại manifest nhưng KHÔNG xoá run cũ; muốn nhận dạng lại với
  cùng tham số phải xoá thủ công tệp raw tương ứng (có chủ đích, tránh mất dữ liệu).
- Manifest ghi đè cần `--force`. Nếu chưa có `04_segmentation`, chạy
  `python -m src.segmentation.pipeline --config <config> --work-id MY_WORK` tiếp theo.

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
