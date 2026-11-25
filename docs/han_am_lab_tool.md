# Dóng chữ Hán ↔ âm tiết bằng tool của Lab (SN-QN-alignment)

Theo hướng dẫn đồ án, phần dịch âm (Hán ↔ phiên âm Hán-Việt) phải dóng bằng Levenshtein của Lab. Tool chỉ dóng
TỪNG CHỮ Hán với TỪNG âm tiết trong MỘT cặp câu đã ghép sẵn; nó không OCR, không tách câu, không dịch nghĩa.

```
05_cleaning/han.xml + am.xml
  -> 06_alignment/han-am.xml            # ghép cặp câu bằng aligner 'levenshtein' (src.alignment.align)
  -> 06_alignment/lab/han-am_input.csv  # ID,HanNom,DichAm,Fallback,Bbox (utf-8-sig) + han-am_input_original.csv
  -> 06_alignment/lab/han-am_config.yaml (đường dẫn TUYỆT ĐỐI) -> align_texts.py của Lab
  -> 06_alignment/lab/out/<WORK>_parallel.{tsv,xlsx,_color.xlsx} + statistic.json
  -> han-am_review.xlsx, han-am_ocr_suggestions.xlsx -> 07_export/han-am/
```

## Tài nguyên (người dùng chuẩn bị; agent không tự tải)
```
third_party/SN-QN-alignment/            # giải nén SN-QN-alignment-main.zip, KHÔNG sửa align_texts.py
  align_texts.py, requirements.txt, configs/alignment_configs/sample-config.yaml, data/sample-data.csv
  dict/QuocNgu_SinoNom_TongHop3.csv     # từ dict.zip của Lab (cột QuocNgu, SinoNom)
  dict/SinoNom_Similar_Dic_v2.csv       # từ dict.zip của Lab (cột Input Character, Top 20 Similar Characters)
```
`third_party/SN-QN-alignment/dict/` và `outputs/` nằm trong `.gitignore` (từ điển ~5 MB, chưa rõ giấy phép).
Thư viện: `pandas`, `numpy`, `PyYAML`, `XlsxWriter` (đã thêm vào `requirements.txt`; `my_nlp` đã có đủ).

## Lệnh (môi trường `my_nlp`)
```powershell
python -m src.alignment.lab_han_am --work-id CPO_19                 # chạy, hậu xử lý, bàn giao
python -m src.alignment.lab_han_am --work-id CPO_19 --dry-run       # chỉ sinh CSV + config, in lệnh
python -m src.alignment.lab_han_am --work-id CPO_19 --quick 10      # 10 dòng đầu (quick_test/test_limit)
python -m src.alignment.lab_han_am --work-id CPO_19 --force         # chạy lại dù input không đổi
python -m src.alignment.lab_han_am --input-csv third_party/SN-QN-alignment/data/sample-data.csv --out-dir X
```
Input CSV, config và tool/từ điển không đổi mà output đã có thì bỏ qua (fingerprint trong `metadata.json`,
stage `lab_han_am`). `--python` chọn interpreter khác cho `align_texts.py` (mặc định Python đang chạy).

## Quy ước
- Tool hiểu đường dẫn tương đối theo thư mục CHA của nó, nên config sinh tự động dùng đường dẫn tuyệt đối cho
  `input_file`, `translation_dict`, `similarity_dict`, `output.directory`.
- Mỗi dòng CSV = một bead có cả hai phía; ID = id bead. Bead `unaligned` bị loại và đếm.
- `HanNom`: chuỗi liền, bỏ khoảng trắng và dấu câu `，。、；：！？「」『』（）《》〈〉·` `, . ! ? ; : " ' ( ) [ ]`, gạch ngang;
  giữ `[MASK]`. Giản -> phồn (OpenCC) TỪNG CHỮ, chỉ cho chữ KHÔNG có trong từ điển Lab mà dạng phồn thể có
  (tránh đổi chữ hợp lệ như 于 -> 於); bản gốc/đã chuẩn hoá/danh sách chữ đổi ở `han-am_input_original.csv`.
- `DichAm`: âm tiết cách nhau một dấu cách, bỏ dấu câu, NFC; số/viết tắt giữ như nguồn.
- `Fallback` để trống, config `fallback_column: null` (không có nguồn Hán dự phòng). Đường `--input-csv` dùng cột
  Fallback nếu CSV có dữ liệu ở cột đó (như `sample-data.csv`), để khớp kết quả chạy tay.
- Tiền kiểm: `han-am.xml` rỗng hoặc > 30% bead `unaligned` -> dừng (gần như chắc chắn do tách câu Hán sai).

## Hậu xử lý
- `02_extraction/metadata.json` -> `han-am_lab_alignment`: đường dẫn output, SHA-256 của `align_texts.py` và hai từ
  điển, `summary` của `statistic.json`.
- `han-am_review.xlsx`: mọi dòng có `_`/lỗi hoặc bị tool bỏ qua, kèm text gốc, trang, score bead, src/tgt ids; sắp
  theo mismatch_rate giảm dần.
- Cảnh báo: mismatch_rate toàn tài liệu > 10%; dòng > 30% vị trí lỗi; dòng bị bỏ qua; số dòng vào ≠ số dòng xử lý;
  lệch số chữ Hán / số âm tiết > 20% (thường do ghép cặp sai ở bước trước).
- `han-am_ocr_suggestions.xlsx`: vị trí tool dùng chữ tương tự (xanh dương) hoặc Fallback (xanh lá) trong
  `_color.xlsx`; chữ đề xuất = chữ trong tập tương tự có âm khớp. KHÔNG tự sửa han.xml/05_cleaning.
- Bàn giao `07_export/han-am/`: `<WORK>_parallel.tsv` (một TAB giữa các cột, một cặp mỗi dòng), `<WORK>_parallel.xlsx`
  (sheet `DONG_HANG`, cùng nội dung TSV), `<WORK>_parallel_color.xlsx`, `statistic.json`, `han-am_review.xlsx`.
  Không đụng export Hán–Việt `07_export/*_parallel.*`.

## Hồi quy
`tests/test_lab_han_am.py::test_regression_lab_sample_data`: qua wrapper, `sample-data.csv` cho 139 vị trí, 134 khớp,
match_rate 96,40%, mismatch_rate 3,60%; TSV và statistic.json giống hệt khi chạy tay.
