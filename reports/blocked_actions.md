# Hành động bị chặn

## 2026-11-26: audit segmentation
- **Commit nền trên nhánh `audit/segmentation`** (bị auto-mode classifier từ chối).
  - Nội dung định commit: các sửa đổi chưa commit của lượt trước, gồm `streams.py` (tiêu đề chạy OCR sai dấu, khối Hán
    liền, dòng Việt rác), `script_filter.py` (pinyin, lời tự nói tiếng Việt, giữ `□`), `□`→`[MASK]` ở bước làm sạch,
    `validate` đọc ID dòng của luồng, config CPO_18/19 (bật `ocr_streams`, phạm vi trang thật, CPO_19 bỏ tr.258–266),
    test `tests/test_streams_layout.py`, báo cáo `reports/segment_clean_20261126.md`.
  - Hiện trạng: vẫn chưa commit trong cây làm việc của nhánh `audit/segmentation`. Bản audit đo trên cây làm việc này.
  - Cần người dùng: duyệt commit (message đề xuất: "Stream segmentation fixes: running headers, han blocks,
    pinyin/vi chatter, □ as [MASK], validate stream ids").
  - Vì commit nền chưa có, các bản sửa sau báo cáo (mục 6.2) cũng chưa commit được. Chúng được để thành thay đổi riêng
    và liệt kê trong báo cáo.

  - **Đã xử lý 2026-11-27:** người dùng duyệt tại máy; các commit được tạo theo thứ tự đề xuất trong
    `reports/segmentation_audit_20261126.md` mục 7.

## 2026-11-27: commit áp quyết định (config + ghi đè theo trang + báo cáo mục 7)
- Bị chặn: `git add configs/CPO_18.json configs/CPO_19.json configs/CPO_20.json src/segmentation/overrides.py tests/test_segmentation_overrides.py reports/clean_full_20261127.md && git commit` trên nhánh `fix/clean-full`.
- Lý do bộ phân loại: Destructive or Unwanted Actions (commit chưa được duyệt riêng).
- Trạng thái: thay đổi để nguyên trong cây làm việc, đã qua test (476 passed); chờ người dùng duyệt commit.
