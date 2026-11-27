# han-viet-corpus

Đồ án dóng hàng Hán–Việt (CPO_18, CPO_19, CPO_20) theo `Guideline.pdf`: OCR/trích text, tách lớp,
làm sạch, tách câu, dóng hàng, xuất TSV + XLSX. **Định dạng nộp đã chốt:** mỗi tác phẩm một file 4 cột
`Hán | Dịch âm | Dịch nghĩa | Dịch thơ` (`python -m src.alignment.export --pair han-viet`); `.tsv` và `.xlsx` phải
giống hệt nhau (sheet `DONG_HANG`). README.md có sơ đồ pipeline đầy đủ.

**Đầu phiên: đọc `docs/STATUS.md`** (nhánh, quyết định đã chốt, task đang dở, việc chờ người dùng quyết).

## Pipeline (Single-Stream)
PDF -> `02_extraction/pages/NNNN.png` -> OCR 1 lần/trang -> `03_ocr/raw/NNNN.json` (bất biến)
-> `03_ocr/ocr_raw/raw.xml` (ID dòng `PNNNN.LNNN`) -> `04_segmentation/` (lớp `han`, `am`, `nghia`, `tho`,
rồi câu) -> `05_cleaning/` -> `06_alignment/` -> `07_export/`.

- Tên file theo trang luôn 4 chữ số (`0015`), dùng `src/common/page_naming.py`.
- OCR nhận dạng ký tự, segmentation mới hiểu cấu trúc. Không lọc theo hệ chữ ở bước OCR (`--unified`).
- Không bao giờ sửa hoặc xoá `03_ocr/raw/NNNN.json`. Bản đã sửa lưu riêng.
- Ngoại lệ có chủ đích: hai luồng `03_ocr/ocr_raw/han.xml` (paddlevl, chỉ chữ Hán) và `vie.xml` (qwen, chỉ
  chữ Latin/Việt) + `ocr_raw_text/{han,vie}/NNNN.txt`, sinh SONG SONG với `raw.xml` (không thay thế).
  Run mới nằm ở `03_ocr/raw/<engine>/NNNN.json`. Segmentation đọc hai luồng này khi bật
  `segmentation.ocr_streams = "han_vie"` (mặc định tắt). Xem `docs/ocr.md` mục 7 và
  `docs/segmentation_architecture.md` mục "Hai luồng".
- Chế độ hệ chữ cũ (`--writing-system`, CPO_18) dùng `ocr_raw/chi_tra.xml` + `vi.xml`, KHÔNG dùng `han.xml`.
- Đường dẫn do `DataLayout` (`src/common/corpus.py`) quản lý. Đừng ghép đường dẫn bằng tay.

## Môi trường
Có hai môi trường Python riêng, đừng trộn:
- `my_nlp`: Qwen, Tesseract, các bước còn lại (transformers 4.x).
- `.venv-paddle`: engine `paddlevl` (transformers >= 5, torch >= 2.6, CUDA 12.6+).
  Không nâng transformers trong `my_nlp`; chưa kiểm chứng Qwen chạy được trên transformers 5.

Mọi môi trường đều cần `python-dotenv` (gói tên `python-dotenv`, KHÔNG phải `loadenv`), vì
`engines/__init__.py` import `kimhannom.py` lúc nạp package. `KIMHANNOM_TOKEN` nằm trong `.env`:
không commit, không đọc, không in ra.

GPU local: RTX 3050 6GB. Colab dự phòng: T4 (compute capability 7.5, không có bf16 thật).

## Lệnh thường dùng
Luôn chạy từ gốc repo, dạng module (`python src\...py` sẽ lỗi `No module named 'src'`):
```powershell
python -m src.pipeline --config configs/CPO_19.json --dry-run     # xem lệnh tương đương
python -m src.ocr.ocr --config configs/CPO_19.json --unified      # OCR
python -m src.ocr.ocr --help                                       # --engines, --select, --pages, --force-reocr
# Hai luồng Hán/Việt: OCR ở đúng môi trường, dựng XML/TXT ở môi trường nào cũng được
$env:PADDLEVL_ORIENTATION="horizontal"                             # Hán in ngang ở CPO_18/19/20
python -m src.ocr.ocr --config configs/CPO_20.json --layer han --engines paddlevl --pages 1-280   # .venv-paddle
python -m src.ocr.ocr --config configs/CPO_20.json --layer vie --engines qwen --pages 1-280       # my_nlp
python -m src.pipeline --config configs/CPO_20.json --steps layers --force   # dựng han.xml/vie.xml ĐÚNG phạm vi trang
# CHÚ Ý: build_layers --config không kèm --pages dựng MỌI trang có raw, kể cả trang đã loại (tạo lại TXT trang bỏ)
python -m src.ocr.build_layers --config configs/CPO_20.json        # JSON thô -> han.xml, vie.xml, TXT + kiểm tra
python -m src.ocr.build_layers --config configs/CPO_20.json --report-only   # chỉ kiểm tra + bảng tổng hợp
powershell -File scripts\ocr_work.ps1 -Work CPO_19                # OCR cả work một mạch (resume được), docs/ocr_runbook.md
python scripts/check_ocr_complete.py --work-id CPO_19 [--sample 5] # OCR thô đã đủ chưa (0 error = đủ)
python scripts/pack_ocr.py --work-id CPO_19                        # dist/CPO_19_ocr_<ngày>.zip; unpack_ocr.py để giải
python -m src.segmentation.pipeline --config configs/CPO_20.json --ocr-streams han_vie   # segmentation từ han/vie.xml
python scripts/audit_clean.py --work-id CPO_20 [--data-root ...]   # kiểm toán bước clean (đọc lại được)
python -m src.alignment.lab_han_am --work-id CPO_19 [--dry-run|--quick N|--force]   # dóng Hán-âm bằng tool Lab
python -m src.segmentation.pipeline --config configs/CPO_19.json
python -m src.cleaning.pipeline --work-id CPO_19 --structural-only
python -m src.validation.validate --config configs/CPO_19.json
python -m src.alignment.align --pair han-am --work-id CPO_19
python -m src.alignment.export --pair han-viet --work-id CPO_19
python -m src.ocr.engines.smoke_paddlevl <ảnh> <raw.json>          # thử một trang với paddlevl
```
`--pages` là bắt buộc (không tự OCR cả thư mục).

## OCR engine
Đăng ký trong `src/ocr/engines/__init__.py` (`register_engine`): `tesseract`, `qwen`, `kimhannom` (API của lab,
có giới hạn tốc độ), `paddlevl` (file `paddle_ocr.py`, PaddleOCR-VL-1.6).

Mỗi engine được import riêng: thiếu phụ thuộc của một engine chỉ ghi cảnh báo và đưa engine đó vào
`UNAVAILABLE_ENGINES`; `require_engine(name)` báo lỗi rõ khi người dùng chọn đúng engine đó.

Luồng Hán/Việt: `han` <- `paddlevl`, `vie` <- `qwen` (`LAYER_ENGINES` trong `src/ocr/build_layers.py`);
`--layer` từ chối engine khác. Lọc theo hệ chữ nằm ở `src/ocr/script_filter.py` (hàm thuần), chạy ở bước
`build_layers`, KHÔNG ở bước OCR. JSON thô của engine không bao giờ bị lọc.

Quy ước bắt buộc khi sửa một engine:
- Mọi tham số ảnh hưởng kết quả phải nằm trong `describe()` (đó là khoá cache), và phải tăng `ADAPTER_VERSION`.
  Quên là cache cũ vẫn được dùng lại.
- `confidence` thang 0..100 là trung bình nhân xác suất token, KHÔNG phải độ chính xác và không so được giữa engine.
- Chế độ unified chọn MỘT engine cho cả trang (`--select first|best_conf`), không bỏ phiếu theo dòng.
  Lấy Hán từ paddlevl và Việt từ qwen đã làm bằng hai luồng `han.xml`/`vie.xml` (mỗi file một engine);
  segmentation vẫn đọc `raw.xml`.

### Bài học từ OCR thực tế (đừng lặp lại)
- Chữ Hán in ngang theo lưới (thơ thất ngôn, 7 chữ/dòng): phải tách TỪNG DÒNG rồi mới OCR.
  Cắt dải nhiều dòng làm PaddleOCR-VL đọc lưới theo cột và ra rác. `paddlevl` horizontal làm đúng việc này.
- Hạt nhiễu của bản quét lấp kẽ giữa dòng/cột: ngưỡng tách phải theo mật độ tương đối, không dùng ngưỡng tuyệt đối vài pixel.
- `PADDLEVL_ORIENTATION=auto` đoán hướng bằng heuristic. Trang lạ nên mở ảnh xem trước, rồi đặt `horizontal` hoặc `vertical`.
- Qwen2.5-VL-3B (4-bit): hay lặp vô hạn trên chữ Hán và đổi sang chữ giản thể. Chữ Việt tốt hơn (conf ~90).
- Chữ giản thể lẫn vào output Hán (ví dụ `数`): chuẩn hoá ở bước làm sạch, không ở bước OCR. Bước dóng `han-am`
  tra từ điển bằng dạng phồn thể (OpenCC) khi chữ gốc không có trong từ điển, không sửa văn bản xuất ra.
- Thơ Hán không chấm câu: tách câu `han` mặc định `--split auto` (theo dòng nếu < 1 dấu 。！？； mỗi 4 dòng).
- Bước `ocr` chỉ bị chặn bởi manifest/XML đã có; cache `raw/*.json` không bắt phải `--force`.
- Hướng chữ paddlevl và khoảng trang OCR đặt trong `ocr.streams` của config (env vẫn thắng); `--pages auto`.
  Tăng `ADAPTER_VERSION` thì resume bằng `--skip-done` để không OCR lại trang đã xong.
- Trang thưa mực (vài cột thơ) từng bị coi là trắng (`ink_mask`, sửa ở paddlevl adapter 5). Qwen còn cùng lỗi
  (chỉ ảnh hưởng trang gần như trắng); đổi thì tăng `ADAPTER_VERSION` của qwen.
- Qwen đôi khi chèn lời dẫn/bản dịch tiếng Anh: `script_filter.vie_reject_reason` loại, checker cảnh báo
  `vie_english_like`.
- Số trang và chú thích dạng `(奇華海口門)` bị OCR kèm vào; loại ở `05_cleaning`.

## Tách lớp (segmentation)
Máy trạng thái tuần tự theo tiêu đề mục (`PHIÊN ÂM`, `DỊCH NGHĨA`, `KHẢO DỊ`, `CHÚ THÍCH`, `DỊCH THƠ`).
Chế độ hai luồng (`ocr_streams: han_vie`): trộn han.xml + vie.xml theo mép trên bbox; mỗi khối Hán >= 2 dòng sau
phần Việt mở tài liệu mới; thiếu tiêu đề mục thì phần Việt ngay sau Hán là âm, tiêu đề VIẾT HOA sau thân âm mở
nghĩa (đều ghi review `implicit_layer_start`). `src.pipeline` thêm bước `layers` (build_layers) khi bật.
OCR hay sai tiêu đề (`KHÁO DỊ`, `DỊCH THOẠI`), nên so khớp mờ, không so chuỗi chính xác.
Chữ Hán trong khảo dị/chú thích không có nghĩa là bắt đầu khối Hán mới. Xem `docs/segmentation_architecture.md`.

## Quy ước mã
- Python 3.11+, type hints, `logging` thay `print`, hàm thuần dễ test, không hard-code đường dẫn Windows.
- File nguồn dùng CRLF: giữ nguyên khi sửa.
- Không dùng LLM để phân loại dòng ở các bước xử lý văn bản; dùng luật + kiểm tra.
- Cảnh báo chất lượng (thiếu lớp, lệch số dòng giữa các lớp, conf thấp) phải ghi lại, không âm thầm vá dữ liệu.

## Tool dóng Hán–Âm của Lab
`third_party/SN-QN-alignment/` (không sửa code tool) + `dict/QuocNgu_SinoNom_TongHop3.csv`, `dict/SinoNom_Similar_Dic_v2.csv`
(gitignored, không commit). Wrapper `src/alignment/lab_han_am.py` sinh config với ĐƯỜNG DẪN TUYỆT ĐỐI (tool hiểu
đường dẫn tương đối theo thư mục cha của nó). Aligner `levenshtein` chỉ ghép cặp câu; kết quả dóng chữ là của tool
Lab. Bàn giao `07_export/han-am/`. Xem `docs/han_am_lab_tool.md`.

## Tài liệu
`docs/ocr.md`, `docs/segmentation_architecture.md`, `docs/pipeline.md`, `docs/migration.md`, `docs/han_am_lab_tool.md`.