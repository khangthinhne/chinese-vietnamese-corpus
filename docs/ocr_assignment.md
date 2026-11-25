# Phân công OCR thô: mỗi người một khoảng trang

Mục tiêu: mỗi người OCR **đúng khoảng trang của mình** (Hán bằng paddlevl, Việt bằng Qwen), kiểm tra 0 lỗi, gửi
một file `.zip` về. Người điều phối ghép các gói. Cài đặt và xử lý lỗi xem `docs/ocr_runbook.md`.

## 1. Bảng phân công
Số trang = chỉ số trang trong file PDF (1-based), trùng tên ảnh `page_NNN.png` và file `NNNN.json`.

| Người | Work | `Pages` | Số trang | Đã OCR sẵn (gói hạt giống) | Còn phải OCR |
|---|---|---|---|---|---|
| Thành viên 1 (`tv1`) | CPO_18 | `205-544` | 340 | 205–212 | 332 |
| Thành viên 2 (`tv2`) | CPO_19 | `5-623` | 619 | 15–121 | 512 |
| Thành viên 3 (`tv3`) | CPO_19 | `624-833` | 210 | – | 210 |
| Người dùng (`tv4`) | CPO_20 | `5-806` | 802 | 5–806 (chạy trên máy điều phối) | 0 |

Giả định đã dùng (hỏi lại người điều phối nếu sai):
- **Thành viên 3:** đề bài ghi "623 tới 633". Mình hiểu là trang **624–833**: chạy tới hết CPO_19 và không trùng
  trang 623 của thành viên 2.
- **CPO_20:** phạm vi lấy theo `ocr.streams.pages` đã có trong config (5–806). Trang 3–4 là phần giới thiệu, không
  có chữ Hán; từ trang 807 là tác giả khác.
- **CPO_19:** trang 5–14 nằm ngoài phần đã OCR thử (15–121). Riêng trang 14 có "小引" (lời dẫn chữ Hán).

**Đề xuất cân bằng lại, chưa áp dụng:** thành viên 2 còn phải OCR khoảng 512 trang, thành viên 3 khoảng 210 trang.
Nếu đổi thành `5-470` / `471-833` thì mỗi người còn khoảng 360 trang. Người điều phối quyết định.

## 2. PDF nguồn: phải đúng bản này (nếu khác bản, số trang sẽ lệch)
| Work | Đặt file tại | Số trang | SHA-256 |
|---|---|---|---|
| CPO_18 | `data/CPO_18/01_sources/CPO18.pdf` | 565 | `02097c62708a086d9c83e3ca404e3fef187c217e9efb6a40e3586612e2233396` |
| CPO_19 | `data/CPO_19/01_sources/NgoThiNhamToanTap3_LamGiang.pdf` | 850 | `07b443fda96ca3f3491f0fc7031bbeeca428f9127228167f620dd0513c2501a0` |
| CPO_20 | `data/CPO_20/01_sources/Tong Tap Van Hoc Viet Nam 08.pdf` | 854 | `1c0845a265f70364d80c892a22638de8a7415a57ee9b4213fcf6bda053472cfc` |

Tự kiểm tra (script `ocr_work` cũng tự chạy lệnh này và DỪNG nếu sai):
```powershell
python -m src.ocr.work_config --config configs/CPO_19.json --verify-source
```

## 3. Chuẩn bị (một lần)
1. Lấy code đúng **cùng một tag/commit** với cả nhóm: `git fetch --tags; git checkout ocr-v1` (người điều phối thông
   báo tag). Mỗi run ghi commit vào `provenance`, nên lệch commit sẽ bị phát hiện khi ghép.
2. Cài hai môi trường và tải model: `docs/ocr_runbook.md` mục 3. Hoặc dùng notebook
   `notebooks/ocr_work.ipynb` (Colab hoặc local), chỉ sửa ô 1.
3. Đặt PDF vào đúng chỗ (bảng mục 2).
4. Giải gói hạt giống nếu có (thành viên 1 và 2): trang trong gói không bị OCR lại.
   ```powershell
   python scripts\unpack_ocr.py dist\CPO_19_0015-0121_seed_20261125.zip --dry-run   # xem trước
   python scripts\unpack_ocr.py dist\CPO_19_0015-0121_seed_20261125.zip
   ```

## 4. Lệnh của từng người (copy-paste, chạy từ gốc repo)
Chạy khô trước. Lệnh này không cần GPU, không nạp model; nó cho biết bao nhiêu trang đã xong (`done`/`cached`) và
bao nhiêu trang sẽ OCR:
```powershell
powershell -ExecutionPolicy Bypass -File scripts\ocr_work.ps1 -Work CPO_19 -Pages 5-623 -DryRun
```
Chạy thật cả quy trình (extract → han → vie → build → check → pack). Bị ngắt thì chạy lại đúng lệnh đó:

| Người | Windows (PowerShell) | Linux / Git Bash |
|---|---|---|
| Thành viên 1 | `powershell -ExecutionPolicy Bypass -File scripts\ocr_work.ps1 -Work CPO_18 -Pages 205-544 -Pack -Member tv1` | `PACK=1 MEMBER=tv1 bash scripts/ocr_work.sh CPO_18 205-544` |
| Thành viên 2 | `powershell -ExecutionPolicy Bypass -File scripts\ocr_work.ps1 -Work CPO_19 -Pages 5-623 -Pack -Member tv2` | `PACK=1 MEMBER=tv2 bash scripts/ocr_work.sh CPO_19 5-623` |
| Thành viên 3 | `powershell -ExecutionPolicy Bypass -File scripts\ocr_work.ps1 -Work CPO_19 -Pages 624-833 -Pack -Member tv3` | `PACK=1 MEMBER=tv3 bash scripts/ocr_work.sh CPO_19 624-833` |
| Người dùng | `powershell -ExecutionPolicy Bypass -File scripts\ocr_work.ps1 -Work CPO_20 -Pages 5-806 -Pack -Member tv4` | `PACK=1 MEMBER=tv4 bash scripts/ocr_work.sh CPO_20 5-806` |

Thiếu `-Work` hoặc `-Pages` thì script dừng và in cách dùng; script không bao giờ tự OCR cả work. Hướng chữ Hán
(`horizontal`) lấy từ config, không cần đặt biến môi trường.

### Chạy từng bước bằng tay (cùng kết quả), ví dụ thành viên 2
```powershell
# trích ảnh (Poppler: my_nlp\Library\bin trong PATH, hoặc --poppler-path)
python -m src.extraction.pdf_extraction --config configs/CPO_19.json --pages 5-623
# Hán: môi trường .venv-paddle
.\.venv-paddle\Scripts\python -m src.ocr.ocr --config configs/CPO_19.json --layer han --engines paddlevl --pages 5-623 --skip-done
# Việt: môi trường my_nlp, offline
$env:HF_HUB_OFFLINE="1"
python -m src.ocr.ocr --config configs/CPO_19.json --layer vie --engines qwen --pages 5-623 --skip-done
# dựng XML/TXT (mọi trang có raw), kiểm tra trong khoảng, đóng gói
python -m src.ocr.build_layers --config configs/CPO_19.json
python scripts\check_ocr_complete.py --work-id CPO_19 --pages 5-623 --sample 5
python scripts\pack_ocr.py --work-id CPO_19 --pages 5-623 --member tv2
```
Mỗi lệnh OCR ghi `manifests/ocr_<han|vie>_<đầu>-<cuối>.json` riêng theo khoảng trang, nên các khoảng khác nhau không
đè manifest của nhau. `han.xml`/`vie.xml`/TXT luôn dựng lại được từ `raw/`, nên chúng không phải nguồn dữ liệu gốc.

## 5. Ước lượng thời gian (RTX 3050 6 GB, ước lượng)
Số đo thật trên máy điều phối (`reports/ocr_inventory_20261125.md`), tính trung bình: paddlevl khoảng 22 s/trang,
Qwen khoảng 35 s/trang, tổng khoảng 1 phút/trang. Các con số dưới đây là **ước lượng**; trang chữ dày chậm hơn, máy
bận việc khác cũng chậm hơn (đã thấy tới 60 s/trang paddlevl).

| Người | Trang còn OCR | Hán | Việt | Tổng |
|---|---|---|---|---|
| Thành viên 1 | 332 | ~2,0 giờ | ~3,2 giờ | **~5–6 giờ** |
| Thành viên 2 | 512 | ~3,1 giờ | ~5,0 giờ | **~8–9 giờ** (nên chạy qua đêm) |
| Thành viên 3 | 210 | ~1,3 giờ | ~2,0 giờ | **~3–4 giờ** |
| Người dùng | 0 (đã chạy) | – | – | – |

**Colab T4:** chưa đo. paddlevl tự chọn float32 (T4 không có bf16), Qwen 4-bit fp16. Ước lượng chậm hơn khoảng
1–1,5 lần. Phiên Colab bị ngắt sau vài giờ: notebook lưu kết quả lên Drive, *Run all* lại để chạy tiếp.

**Khác máy là bình thường:** máy khác (T4, dtype hoặc phiên bản thư viện khác) tạo khoá cache khác. Đây không phải
lỗi. `--skip-done` bỏ qua trang theo số trang chứ không theo khoá. Khi một trang có hai run, `build_layers` chọn theo
thứ tự: run do manifest chỉ định, rồi run hợp lệ (không lặp hay cắt cụt), rồi run mới nhất.

## 6. Gửi kết quả
Gói nằm ở `dist/<WORK>_<đầu>-<cuối>_<tv>_<ngày>.zip`, ví dụ `CPO_19_0005-0623_tv2_20261126.zip`. Gói chỉ chứa
`03_ocr/raw/` của các trang trong khoảng, manifest theo khoảng, `ocr_check_<khoảng>.json`, `PACK_INFO.json` và
`MANIFEST.sha256`. Gói không có ảnh, PDF hay XML.
- Kích thước dự kiến khoảng 7 KB/trang (gói hạt giống CPO_19 107 trang = 0,7 MB): thành viên 1 khoảng 2–3 MB,
  thành viên 2 khoảng 4–5 MB, thành viên 3 khoảng 1–2 MB.
- Lệnh `pack` in ra danh sách **trang còn thiếu** Hán/Việt và kết quả check. Gửi khi đã "ĐỦ"; nếu chưa đủ thì ghi rõ
  trang thiếu khi gửi.
- Gửi qua Drive/Teams. KHÔNG gửi `.env`, KHÔNG commit `data/` hay `dist/`.

## 7. Người điều phối ghép
```powershell
python scripts\merge_ocr_packs.py dist\CPO_19_0005-0623_tv2_*.zip dist\CPO_19_0624-0833_tv3_*.zip --dry-run
#   == CPO_19: 2 gói; mới ..., XUNG ĐỘT 0
#   đủ 5-833, 0 thiếu, 0 chồng
python scripts\merge_ocr_packs.py dist\CPO_19_0005-0623_tv2_*.zip dist\CPO_19_0624-0833_tv3_*.zip --build --check
```
- `merge_ocr_packs` kiểm tra checksum của mọi gói trước khi ghi. Gói hỏng thì dừng (mã 2) và không ghi gì.
- File cùng tên mà khác nội dung bị báo **xung đột**, không bao giờ bị ghi đè. Mặc định khi có xung đột thì không ghi
  gì (mã 1); `--keep-going` ghi phần không xung đột.
- Riêng file `raw/**.json` chỉ được nhận thêm run, ví dụ thành viên OCR lại một trang hạt giống.
- Lệnh báo trang thiếu so với `ocr.streams.pages` của config, và các khoảng chồng lấn giữa các gói. Chồng lấn cùng
  nội dung là bình thường, ví dụ gói hạt giống.
- Cảnh báo nếu các gói dùng PDF (`source_sha256`) hoặc commit khác nhau.
