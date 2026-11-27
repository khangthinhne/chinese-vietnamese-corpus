# Trạng thái dự án (cập nhật 2026-11-27)

Đọc file này đầu mỗi phiên làm việc mới. Chi tiết nằm trong các báo cáo ở `reports/`.

## Nhánh và commit
- Nhánh hiện tại: `fix/clean-full`. Chưa push; nhánh tách từ `audit/segmentation`, nhánh này tách từ `feat/merge-member-ocr`.
- Commit mới nhất: commit cập nhật tài liệu này (xem `git log -1`). Trước đó:
  - `5d49ba6`: người dùng commit dữ liệu sau khi chạy lại pipeline;
  - `8f7f048`: bật cờ clean/segmentation và các ghi đè;
  - `d4ebc58`: vòng clean trên dữ liệu đầy đủ.
- `data/` đã được commit đến `5d49ba6`, gồm cả 16 trang OCR lại.

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

## Task đang dở: bộ dóng han-nghia
Đặc tả nằm trong tin nhắn của người dùng ngày 2026-11-27. Tóm tắt:
- **Aligner mới `bertalign`:** tự cài thuật toán, embedding cấu hình được (LaBSE, BAAI/bge-m3), fp16, cache embedding theo hash câu.
  Bead được phép 1-1 đến n-m, tối đa 4 câu mỗi phía; 0-1 và 1-0 có cờ.
- **Tín hiệu cầu nối âm:** câu âm tương ứng lấy từ han-am. Trùng từ được tính sau khi bỏ dấu, có trọng số IDF, ưu tiên cụm
  2–3 âm tiết. Điểm = embedding + w × trùng từ + tiên nghiệm độ dài.
- **So sánh 3 cấu hình:** LaBSE; BGE-M3; BGE-M3 + cầu nối.
  - Có file chuẩn `mau_dong_hang_50_trang.xlsx` thì đo độ chính xác và độ phủ bead.
  - Chưa có chuẩn thì đo độ đồng thuận giữa các cấu hình, và rà bằng mắt 30 bead mỗi work.
- **Cách chạy:** trên bản sao, một commit, **không dùng API CLC** (không được cấp). Báo cáo `reports/han_nghia_<ngày>.md`.
- **Đã làm:** mới đọc mã. Mỗi aligner là một class đăng ký bằng `@register_aligner` trong `src/alignment/aligners.py`.
  Lõi quy hoạch động là `src/alignment/dp.monotonic_align`, nhận `cost(i0,i1,j0,j1)` và các bước `moves`.
  `align.py` chạy riêng từng tài liệu. Bead han-am lưu ở `06_alignment/han-am.xml` (`SRC_IDS`/`TGT_IDS`).
- **Bước tiếp theo:**
  1. kiểm tra môi trường (sentence-transformers, mô hình đã tải offline chưa, GPU);
  2. tìm file chuẩn dóng tay;
  3. viết aligner và test;
  4. chạy 3 cấu hình trên bản sao ở `E:\claude_scratch\`;
  5. viết báo cáo.

## Báo cáo chính (`reports/`)
- `segmentation_audit_20261126.md`: audit segmentation. Mục 9 là vòng 2; mục 10 là ghi đè CPO_18, quét ảo giác Qwen,
  danh sách OCR lại, 69 file bị xoá.
- `clean_full_20261127.md`: vòng clean có cổng chất lượng. Mục 4 là các cờ; mục 7 là các quyết định và kết quả OCR lại.
- `segment_clean_20261126.md`, `clean_round2_20261125.md`, `clean_round3_20261126.md`: các vòng clean trước.
- `review_ocr_CPO_*.xlsx`: dòng nghi lỗi OCR, chia theo khoảng trang của thành viên (chưa commit).
- `blocked_actions.md`: các thao tác từng bị chặn.

## Đang chờ người dùng quyết
1. **Phân công dóng tay 50 trang đầu:** ai làm work nào. Cần cả mẫu file nộp (đề xuất: `assignment_submission/align50/`,
   mỗi người một file mẫu điền sẵn cặp máy dóng). Trước đó cần chạy `--steps export` vì `07_export` hiện là bản 26/11.
2. **Sửa tay OCR:** CPO_18 tr.353, 402, 523, 528 và 531 (tr.531 có 3 dòng tiếng Anh bịa). Có làm thư mục
   `03_ocr/corrections/` để lưu bản sửa hay không.
3. **Luật nhận dạng câu tiếng Anh ngắn:** có sửa không. Câu kiểu "snow does not fall" hiện lọt qua.
4. **Cấu trúc gói nộp:** `Guideline.pdf` không có trong repo, cần tài liệu này để làm script đóng gói đúng.
5. **Dữ liệu tạm của Claude:** nằm ở `E:\claude_scratch\8d960ee1\` (bản sao, worktree `wt`, `wt2`, `wt_base`,
   `wt_commits`). Có giữ lại hay không.
