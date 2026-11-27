# Trạng thái dự án (cập nhật 2026-11-28)

Đọc file này đầu mỗi phiên làm việc mới. Chi tiết nằm trong các báo cáo ở `reports/`.

## Nhánh và commit
- Nhánh hiện tại: `fix/clean-full`. Chưa push; nhánh tách từ `audit/segmentation`, nhánh này tách từ `feat/merge-member-ocr`.
- Commit mới nhất: commit cập nhật tài liệu này (xem `git log -1`). Trước đó:
  - `5d49ba6`: người dùng commit dữ liệu sau khi chạy lại pipeline;
  - `8f7f048`: bật cờ clean/segmentation và các ghi đè;
  - `d4ebc58`: vòng clean trên dữ liệu đầy đủ.
- `data/` đã được commit đến `5d49ba6`, gồm cả 16 trang OCR lại.
- `47fcc60` (2026-11-28 01:32): người dùng merge `origin/main` vào nhánh này. Merge mang về các file dóng tay
  `assignment_submission/align50/` và commit `dc05a52` (dóng CPO_19 tr.14–63, sửa `data/CPO_19/07_export/` và
  `05_cleaning/validation_report.json`).
- 69 TXT `ocr_raw_text` (mục 10.5 của báo cáo audit segmentation) đã bị xoá trong commit `5d49ba6`. Không còn thay
  đổi nào đang chờ trong `git status data/`.

## Quyết định đã chốt
- **Định dạng nộp:** một file 4 cột `Hán | Dịch âm | Dịch nghĩa | Dịch thơ` cho mỗi tác phẩm, xuất bằng
  `python -m src.alignment.export --pair han-viet`. File `.tsv` và `.xlsx` phải giống hệt nhau, sheet `DONG_HANG`.
  Text sạch nằm trong `data/<W>/05_cleaning/text/`; khi đóng gói thì chép sang `<MA_DE_TAI>_<W>/03_text_sach/`.
- **Phạm vi trang** (`common.pages` = `ocr.streams.pages`; trang loại trong `clean.common.excluded_pages`):

  | Work | Phạm vi | Loại khi clean | Phụ trách OCR |
  |---|---|---|---|
  | CPO_18 | 205–544, trừ 29 trang ảnh chụp thủ bản | 261-281, 306-307, 327-329, 335, 361-362 | tv1 |
  | CPO_19 | 5–833 | 5–13 (lời giới thiệu), 258–266 (toàn tiếng Việt) | tv2 làm 5–623, tv3 làm 624–833 |
  | CPO_20 | 5–320 và 622–806 | 321–621 (không có bản Hán) | tv4 (người dùng) |

  Chi tiết phân công: `docs/ocr_assignment.md`.
- **Tách lớp:** `segmentation.ocr_streams = "han_vie"` cho cả 3 work. han.xml đến từ paddlevl, vie.xml từ Qwen.
- **Tách câu âm theo cách tách câu Hán:** `sentence.am_follow_han_split = true` cho cả 3 work.
- **`segmentation.am_detection`:**
  - CPO_20: `enabled = true`, ngưỡng 0,35. Bộ nhận diện chuyển phần âm mở ngầm của 12 bài văn xuôi chỉ có dịch nghĩa sang lớp nghĩa.
  - `split_lines = true` cho cả 3 work. CPO_18 và CPO_19 không bật `enabled`.
- **`segmentation.note_entry_ends = ["am","nghia","tho"]`:** chỉ CPO_20.
- **Cờ clean bật cho cả 3 work:** `stream_running_headers`, `stream_note_markers`, `am_markers_extended`,
  `capture_source_lines`, `footnotes.note_entry_markers`. CPO_19 bật thêm `footnotes.note_entries`.
  Segmentation bật thêm `verse_tho_split`.
- **Ghi đè theo bài** (`segmentation.document_overrides`, khoá theo trang bắt đầu, có lý do):
  - CPO_18 .012, .013 (bản dọc của văn bia), .028: loại khỏi lớp dóng;
  - CPO_18 .015: chuyển sang vùng chú thích;
  - CPO_20 .053 tr.123–134 (văn Nôm, không có chữ Hán đối ứng): loại khỏi lớp dóng.
- **Quy ước chung:**
  - raw OCR chỉ được nối thêm, không sửa;
  - không commit `.env`, `third_party/`, từ điển Lab;
  - không chạy hai mô hình GPU cùng lúc;
  - mỗi commit đều phải được người dùng duyệt; không push.

## Kết quả mới nhất (dữ liệu thật, `reports/audit_CPO_*.json`)
Cả 3 work đạt cổng 1–3 (`scripts/audit_segmentation.py`):

| Work | Hán–âm ±10% | Bead han-am hợp lệ | Ghi chú |
|---|---|---|---|
| CPO_18 | 0,877 | 1096 | Trừ tr.402, 523, 528 |
| CPO_19 | 0,874 | 3519 | |
| CPO_20 | 0,865 | 2058 | |

## Task vừa xong: bộ dóng han-nghia (commit `95c0800`; đã chạy trên `data/` thật 2026-11-28)
Báo cáo: `reports/han_nghia_20261127.md`. Nhãn rà bằng mắt: `reports/han_nghia_review_20261127.tsv`.
Bộ chuẩn: `eval/gold/` (xem `eval/gold/README.md`).
- **Aligner `bertalign`** (`src/alignment/bertalign.py`, `src/alignment/embeddings.py`):
  - tự cài thuật toán; embedding LaBSE hoặc bge-m3, fp16, cache SQLite theo sha1 câu;
  - bead tối đa 4 câu mỗi phía, 0-1 và 1-0 có cờ `unaligned`;
  - cầu nối âm `bridge_weight` lấy câu âm từ han-am.xml;
  - `prepare()` hiệu chỉnh tỉ lệ độ dài theo work.
- **Đánh giá:** `scripts/eval_han_nghia.py` (độ đồng thuận, chấm theo chuẩn + `--tune`, lấy mẫu rà).
- **Không dùng API CLC** (không được cấp).
- **Kết quả** (bản sao ở `E:\claude_scratch\hn_20261127\`; mô hình ở `E:\claude_scratch\hf\`):
  - đã chấm theo bộ dóng tay `eval/gold/` (báo cáo mục 4a; số liệu ở `reports/han_nghia_gold_20261128.json`);
  - strict F1: `length` 0,62; LaBSE 0,90; BGE-M3 0,90; BGE-M3 + cầu nối w=3 0,91. Ba cấu hình embedding ngang nhau
    (khoảng tin cậy bootstrap chứa 0);
  - w=3 chọn trên dev; trên test row_acc 0,932.
- **Config đã đổi (người dùng duyệt 2026-11-28):** `pairs.han-nghia` của `configs/CPO_18/19/20.json` dùng
  `bertalign` với `model=bge-m3`, `bridge_weight=3`. Cả 3 work dùng chung một cấu hình, vì chênh lệch theo work nằm
  trong nhiễu. `CPO_19_unified.json` không đổi.
  - Chạy cần `HAN_VIET_MODEL_DIR` trỏ vào `E:\claude_scratch\hf` (Python trên máy local lỗi SSL khi tải từ HF).
- **Đã chạy trên `data/` thật (2026-11-28)** bằng `src.pipeline --steps align --pairs han-nghia --force`, lần lượt từng
  work, mất 23 / 21 / 6 s (embedding lấy từ cache `HAN_VIET_EMB_CACHE`).
  - `data/<W>/06_alignment/han-nghia.xml` trùng từng bead với bản `bge_bridge3` đã chấm trên bản sao.
  - Chấm lại trên `data/`: strict F1 0,913, row_acc 0,929.
  - Số bead: CPO_18 1132, CPO_19 3277, CPO_20 1777. Số bead 1-0 / 0-1: CPO_18 59 / 31, CPO_19 79 / 122,
    CPO_20 334 / 106.
- **Chưa làm:** xuất file nộp 4 cột (chờ han-tho xong). `07_export` vẫn là bản cũ, dóng nghĩa bằng `length`.
- **Nguồn lỗi lớn nhất:** chú thích, khảo dị, dịch thơ và âm lọt vào lớp nghĩa. Xem báo cáo, mục 6.
- **Lỗi tách lớp mà bộ chuẩn phát hiện (cần sửa ở segmentation, không vá ở bước dóng):**
  - `CPO_20.0046`: mất toàn bộ lớp nghĩa của bài. Bộ chuẩn có 8 câu Dịch nghĩa (hàng `CPO_20.0046.001`–`.008`), nhưng
    lớp nghĩa của pipeline không có câu nào.
  - `CPO_18.0209.002`: câu nghĩa "Nhưng nếu đã có thể thông suốt bên kia, thì sao lại còn vướng mắc đối với bên
    này?" không có trong `05_cleaning`.
- **Đề xuất bộ chuẩn thứ hai:** dóng tay khoảng 50 trang ở nửa sau mỗi cuốn. Bộ hiện tại là 50 trang đầu, nơi lớp nghĩa
  sạch nhất, nên số liệu có thể tốt hơn thực tế. Chú thích, khảo dị và dịch thơ lọt vào lớp nghĩa chủ yếu ở phần sau.

## Báo cáo chính (`reports/`)
- `segmentation_audit_20261126.md`: audit segmentation. Mục 9 là vòng 2; mục 10 là ghi đè CPO_18, quét ảo giác Qwen,
  danh sách OCR lại, 69 file bị xoá.
- `clean_full_20261127.md`: vòng clean có cổng chất lượng. Mục 4 là các cờ; mục 7 là các quyết định và kết quả OCR lại.
- `segment_clean_20261126.md`, `clean_round2_20261125.md`, `clean_round3_20261126.md`: các vòng clean trước.
- `review_ocr_CPO_*.xlsx`: dòng nghi lỗi OCR, chia theo khoảng trang của thành viên (chưa commit).
- `blocked_actions.md`: các thao tác từng bị chặn.

## Đang chờ người dùng quyết
1. **Bộ chuẩn thứ hai ở nửa sau mỗi cuốn:** ai làm, khoảng trang nào. Bộ đầu tiên đã chốt (2026-11-28): file CPO_19
   đổi tên thành `CPO_19_p14-63_Dat.xlsx`, đã chấm lại và số liệu không đổi.
2. **Sửa tay OCR:** CPO_18 tr.353, 402, 523, 528 và 531 (tr.531 có 3 dòng tiếng Anh bịa). Có làm thư mục
   `03_ocr/corrections/` để lưu bản sửa hay không.
3. **Luật nhận dạng câu tiếng Anh ngắn:** có sửa không. Câu kiểu "snow does not fall" hiện lọt qua.
4. **Cấu trúc gói nộp:** `Guideline.pdf` không có trong repo, cần tài liệu này để làm script đóng gói đúng.
5. **Xuất file nộp 4 cột:** chờ han-tho xong rồi chạy `export --pair han-viet` cho cả 3 work.
6. **Lọc lớp nghĩa:** chú thích, khảo dị, dịch thơ và âm lọt vào lớp nghĩa. Cần một vòng sửa segmentation/clean,
   gồm cả hai lỗi tách lớp ở trên (`CPO_20.0046`, `CPO_18.0209.002`).
7. **Dữ liệu tạm của Claude:** `E:\claude_scratch\hn_20261127\` (bản sao han-nghia + cache embedding ~280 MB) và
   `E:\claude_scratch\hf\` (LaBSE + bge-m3, ~4,2 GB). Ngoài ra còn `E:\claude_scratch\8d960ee1\` (bản sao, worktree `wt`, `wt2`, `wt_base`,
   `wt_commits`). Có giữ lại hay không.
