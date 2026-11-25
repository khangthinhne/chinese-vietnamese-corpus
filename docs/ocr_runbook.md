# Runbook: OCR thô hai luồng cho một CPO (mỗi thành viên một work)

Mục tiêu: từ PDF ra `data/<WORK>/03_ocr/` đầy đủ (`raw/`, `manifests/`, `ocr_raw/han.xml`, `ocr_raw/vie.xml`,
`ocr_raw_text/{han,vie}/NNNN.txt`), kiểm tra 0 lỗi, rồi đóng gói gửi về. KHÔNG làm segmentation/clean/align ở đây.

```
PDF -> 02_extraction/pages/page_NNN.png -> paddlevl (Hán) -> raw/paddlevl/NNNN.json ┐
                                         -> qwen (Việt)   -> raw/qwen/NNNN.json     ┘-> build_layers -> han.xml, vie.xml, TXT
                                                                                    -> check_ocr_complete -> ocr_check.json -> pack
```

## 1. Lệnh một mạch

`-Work` và `-Pages` là BẮT BUỘC (không có mặc định "cả work"); khoảng trang của từng người: `docs/ocr_assignment.md`.
```powershell
# Windows (PowerShell), từ gốc repo
powershell -ExecutionPolicy Bypass -File scripts\ocr_work.ps1 -Work CPO_19 -Pages 5-623
powershell -ExecutionPolicy Bypass -File scripts\ocr_work.ps1 -Work CPO_19 -Pages 5-623 -DryRun          # không GPU: trang nào sẽ OCR
powershell -ExecutionPolicy Bypass -File scripts\ocr_work.ps1 -Work CPO_19 -Pages 5-623 -Steps han,build,check
powershell -ExecutionPolicy Bypass -File scripts\ocr_work.ps1 -Work CPO_19 -Pages 5-623 -Pack -Member tv2
```
```bash
# Linux / Git Bash
bash scripts/ocr_work.sh CPO_19 5-623
DRY_RUN=1 bash scripts/ocr_work.sh CPO_19 5-623
STEPS="han build check" PACK=1 MEMBER=tv2 bash scripts/ocr_work.sh CPO_19 5-623
```
**Notebook (Colab T4 hoặc local):** `notebooks/ocr_work.ipynb`. Chỉ sửa ô 1 (`WORK`, `PAGES`, `MEMBER`, đường dẫn
PDF), rồi *Run all*. Notebook tự nhận nơi chạy.
- Colab: cài hai môi trường, tải model, lưu kết quả và gói lên Drive.
- Local (kernel `my_nlp`): bỏ qua cài đặt, dùng `.venv-paddle` + `my_nlp` có sẵn, gọi đúng `ocr_work.ps1`/`.sh` ở trên;
  kết quả ở `data/`, gói ở `dist/`.

Cả hai đều làm: kiểm tra SHA-256 của PDF, giải gói hạt giống, dry-run, extract, han, vie, build, check, pack.
Bị ngắt thì *Run all* lại.
Các bước: `extract` (trích ảnh còn thiếu) -> `han` -> `vie` -> `build` -> `check` (-> `pack`). Bị ngắt (tắt máy,
OOM, Ctrl+C) thì CHẠY LẠI ĐÚNG LỆNH ĐÓ: trang đã có run được bỏ qua (`--skip-done`), không mất gì
(`raw/*.json` chỉ được thêm run, không bao giờ bị sửa/xoá). Hai model không bao giờ chạy cùng lúc.

Log: `logs/ocr_<WORK>_<han|vie>_<thời điểm>.log`; mỗi trang một dòng
`[han] 260/802 trang 0264: ocr, 36 dòng, ~25s/trang, còn ~1h29m` (`cached`/`done` = đã có từ trước).

Script tự tìm python: `.venv-paddle\Scripts\python.exe` cho paddlevl; `my_nlp` (đang kích hoạt, hoặc qua
`conda env list`) cho Qwen và các bước còn lại; Poppler ở `<my_nlp>\Library\bin`. Khác máy thì truyền
`-PaddlePython`, `-QwenPython`, `-PopplerPath` (bash: `PADDLE_PY`, `QWEN_PY`, `POPPLER`).

## 2. Cấu hình theo work (`configs/<WORK>.json`, khối `ocr.streams`)
```json
"ocr": {
  "streams": {
    "pages": "5-806",
    "paddlevl_orientation": "horizontal",
    "engines": {"han": "paddlevl", "vie": "qwen"},
    "check": {"qwen_low_conf": 60}
  }
}
```
- `pages`: khoảng trang cần OCR (cũng là khoảng `extract` trích). Thiếu thì lấy mọi ảnh đã trích.
- `paddlevl_orientation`: `horizontal` (Hán in ngang theo lưới, CPO_18/19/20), `vertical` (in dọc), `auto` (đoán; trang
  lạ nên mở ảnh xem trước). Biến môi trường `PADDLEVL_ORIENTATION` vẫn được ưu tiên hơn config.
- `check`: ngưỡng của `scripts/check_ocr_complete.py` (xem `CHECK_DEFAULTS` trong `src/ocr/work_config.py`).
- `common.source` phải trỏ tới PDF trong `data/<WORK>/01_sources/` (đường dẫn tương đối, chạy được trên mọi máy).

## 3. Cài đặt (một lần)
Hai môi trường RIÊNG, đừng trộn (Qwen chưa kiểm chứng trên transformers 5):

| môi trường | dùng cho | phiên bản đã chạy |
|---|---|---|
| `my_nlp` (conda, Python 3.11) | Qwen, extract, build, check, pack | torch 2.5.1+cu121, transformers 4.57.6, bitsandbytes 0.50.2, accelerate 1.2.1 |
| `.venv-paddle` (venv, Python 3.11) | paddlevl | torch 2.14.1+cu126, transformers 5.24.0 |

```powershell
conda create -n my_nlp python=3.11 -y; conda activate my_nlp
conda install -c conda-forge poppler -y                  # pdftoppm/pdfinfo (Library\bin)
pip install -r req_my_nlp.txt                            # hoặc: torch (cu121) transformers==4.57.6 accelerate bitsandbytes qwen-vl-utils
pip install -r requirements.txt python-dotenv opencc pdf2image
python -m venv .venv-paddle                              # từ python 3.11 bất kỳ (vd của my_nlp)
.\.venv-paddle\Scripts\pip install "torch>=2.6" --index-url https://download.pytorch.org/whl/cu126
.\.venv-paddle\Scripts\pip install "transformers>=5" python-dotenv pillow numpy
```
`python-dotenv` (KHÔNG phải `loadenv`) bắt buộc ở cả hai môi trường vì `src/ocr/engines/__init__.py` nạp
`kimhannom.py`. `opencc` chỉ cần ở `my_nlp` (thống kê chữ giản thể; thiếu thì bỏ qua). `KIMHANNOM_TOKEN` trong `.env`
KHÔNG cần cho runbook này; đừng commit/gửi `.env`.

### Tải model lần đầu (cần mạng), sau đó chạy offline
```powershell
$env:HF_HUB_OFFLINE="0"
.\.venv-paddle\Scripts\python -c "from huggingface_hub import snapshot_download as s; s('PaddlePaddle/PaddleOCR-VL-1.6')"
python -c "from huggingface_hub import snapshot_download as s; s('Qwen/Qwen2.5-VL-3B-Instruct')"    # my_nlp, ~7 GB
```
Script đặt `HF_HUB_OFFLINE=1` (máy nhóm từng lỗi SSL tới huggingface.co). Thư mục cache đổi được bằng `HF_HUB_CACHE`.

## 4. Phần cứng và thời gian (đo thật trên RTX 3050 Laptop 6 GB)
| engine | s/trang (trung vị) | s/trang (trung bình) | 800 trang |
|---|---|---|---|
| paddlevl (bf16, horizontal) | 15-25 | 21-25 | ~5 giờ |
| Qwen2.5-VL-3B 4-bit | ~28 | 30-40 | ~7 giờ |

Một work ~800 trang: ~12 giờ GPU trên RTX 3050 (để chạy qua đêm; resume được). Thời gian lần đầu nạp model ~30 s.
- **Colab T4** (compute 7.5): KHÔNG dùng bf16. paddlevl tự chọn float32 (`PADDLEVL_DTYPE=auto`; có thể thử
  `float16`, dễ tràn số); Qwen 4-bit compute fp16. Chưa đo; ước 1-1,5 lần thời gian RTX 3050. Phiên Colab ngắt
  ~12 giờ: chạy theo từng bước (`STEPS="han"`, rồi `STEPS="vie"`), lưu `data/<WORK>/03_ocr` lên Drive giữa chừng.
- **Không có GPU**: cả hai engine báo lỗi rõ ("yêu cầu CUDA GPU"). Không hỗ trợ chạy CPU cho cả work (quá chậm).

## 5. Khi có lỗi
| hiện tượng | cách xử lý |
|---|---|
| `No module named 'src'` | chạy dạng module từ gốc repo: `python -m src.ocr.ocr ...`, không `python src\ocr\ocr.py` |
| `No module named 'dotenv'` | `pip install python-dotenv` trong ĐÚNG môi trường đang chạy |
| `PaddleOCR-VL-1.6 cần transformers>=5` / lỗi torch < 2.6 | đang chạy paddlevl bằng `my_nlp`; dùng `.venv-paddle` |
| SSL / `huggingface.co` | `HF_HUB_OFFLINE=1` (model phải tải sẵn, mục 3) |
| `CUDA out of memory` | đóng chương trình khác dùng GPU; script tự thử lại lần cuối với `*_MAX_PIXELS_TOKENS=768` |
| `pdftoppm`/`Unable to get page count` | Poppler chưa có trong PATH: `conda activate my_nlp` hoặc `-PopplerPath` |
| trang báo lỗi trong `manifests/ocr_<layer>.json` (`error_pages`) | chạy lại lệnh; vẫn lỗi thì ghi số trang vào báo cáo gửi nhóm |
| check báo `no_text` / `*_degenerate` | mở ảnh trang đó; xem mục 6 |

Muốn OCR lại một trang với tham số khác: đặt biến môi trường (vd `QWEN_BAND_INCHES=1.0`) rồi chạy
`python -m src.ocr.ocr --config configs/<W>.json --layer vie --engines qwen --pages 208`. Run mới được THÊM vào
file thô (khoá khác), `build_layers` chọn run theo manifest, rồi run hợp lệ, rồi run mới nhất.

## 6. Đọc kết quả kiểm tra (`scripts/check_ocr_complete.py --work-id <W>`)
"ĐỦ" = 0 `error`. `warning` cần người xem; `info` là ghi nhận hợp lệ.

| mã | mức | ý nghĩa |
|---|---|---|
| `missing_image`, `no_han_run`, `no_vie_run` | error | trang trong phạm vi chưa có ảnh / chưa OCR |
| `stale_build` | error | có run mới mà chưa chạy `build_layers` |
| `vie_degenerate` / `han_degenerate` | error | một dải bị cắt cụt/lặp có chứa chữ của đúng luồng -> OCR lại trang đó (mục 5) |
| `no_text`, `vie_too_few_lines` | error | trang có mực mà không ra (đủ) dòng |
| `*_xml`, `*_txt_name`, `vie_has_cjk`, `*_empty_line`, `*_control_char` | error | XML/TXT hỏng: chạy lại `build_layers` |
| `han_many_lines`, `han_long_line`, `han_single_char_lines`, `han_gap_units` | warning | trang Hán có thể bị cắt sai/đọc theo cột: mở ảnh xem; nếu in dọc thì đặt `paddlevl_orientation` |
| `vie_low_conf`, `vie_english_like`, `*_run_no_lines` | warning | Qwen kém chắc / dịch sang tiếng Anh / trang không ra dòng nào |
| `han_only_page`, `blank_page`, `han_simplified`, `*_multi_run` | info | trang chỉ có Hán / trang trắng / chữ giản thể (chuẩn hoá ở bước clean) / nhiều run |

`--sample 5 --seed 0` in 5 trang song ngữ ngẫu nhiên để soát bằng mắt (ảnh <-> `han/NNNN.txt`, `vie/NNNN.txt`).

## 7. Gửi kết quả về
```powershell
python scripts\pack_ocr.py --work-id CPO_19          # -> dist\CPO_19_ocr_<ngày>.zip (in kích thước)
python scripts\unpack_ocr.py dist\CPO_19_ocr_<ngày>.zip --dry-run     # người nhận: xem trước
python scripts\unpack_ocr.py dist\CPO_19_ocr_<ngày>.zip               # giải vào data\
```
Gói chứa `03_ocr/` (không có ảnh/PDF; trích lại được), `ocr_check.json`, `PACK_INFO.json`, `SHA256SUMS`. Gửi qua
Drive/Teams; KHÔNG commit `data/` hay `dist/`. `unpack_ocr` không bao giờ ghi đè file khác nội dung (báo xung đột);
file thô chỉ được nhận thêm run.
