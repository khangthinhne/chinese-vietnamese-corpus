# Clean round 3 (2026-11-26): CPO_20 bỏ trang 321–621, bisect CPO_18, khảo dị mờ, metadata, export tách lớp

Nhánh `fix/clean-round2` (một commit cho cả prompt này), **chưa push**. Thử nghiệm đều chạy trên bản sao trong
scratchpad. Không dùng GPU ngoài job Qwen.

## 1. Hàng đợi Qwen CPO_20
- Đã dừng job cũ lúc 00:03, sau khi trang 486 ghi xong. Đó là trang cuối có run; trang 487 đang dở, bị bỏ, và vì
  nằm trong khoảng bỏ nên không chạy lại.
- **Config được sửa trước khi khởi động lại.** Job mới chạy từ 00:04 với
  `--layer vie --engines qwen --pages 622-806 --skip-done` (`scratchpad/run_cpo20_vie_622.sh`, log
  `logs/ocr_CPO_20_vie_20261126_000414.log`).
- Raw của trang 321–621 giữ nguyên, không xoá.
- **Ước lượng mới cho 622–806 (185 trang):** 12 trang đầu có trung vị 123 s/trang, trung bình 100 s/trang (trang
  622–633 dày chữ; CPU còn bị các lần chạy thử của round 3 tranh). Trang thơ phía sau (khoảng 700–806) trước đây chạy
  30–40 s/trang. Ước còn **2,5–5 giờ**, xong khoảng **03:00–05:30**.

## 2. Kiểm tra an toàn: dòng Hán ở trang 321–621
Dựng `han.xml` cho 321–621 trên bản sao raw paddlevl:
- **57/301 trang có dòng Hán, mỗi trang đúng 1 dòng:**
  - `欽定四庫全書` 23 lần;
  - `也` 18 lần;
  - `一` 13 lần;
  - `晉`, `晉書卷八`, `晉書卷四`, `年月日` mỗi chuỗi 1 lần;
  - trang 450 là một câu giản thể do model tự viết: "这里原文本可能存在识别偏差…".
- Đã mở ảnh các trang đáng ngờ nhất (347, 353, 437, 450, 491, 603): đều là văn xuôi tiếng Việt, không có chữ Hán.
- **Kết luận: 0 trang có chữ Hán thật trong 321–621.** Toàn bộ 57 dòng là paddlevl tự sinh.

## 3. Phạm vi mới ghi vào config (nhất quán)
- `configs/CPO_20.json`:
  - `common.pages` và `ocr.streams.pages` = `5-320,622-806`;
  - `clean.common.excluded_pages` = `"321-621"`.
- `src/pipeline.py`:
  - `excluded_pages` nhận cả chuỗi khoảng trang (trước đây chuỗi bị tách thành từng ký tự);
  - bước `layers` (build_layers) truyền `--pages` theo `ocr.streams.pages`.

  Vì vậy OCR (`--pages auto`), build_layers, segmentation (`common.pages`) và clean (`--exclude-pages`) đều bỏ 321–621.
  `--dry-run` cho ra `build_layers … --pages 5-320,622-806` và `--exclude-pages 321-621` ở mọi lớp. Có test.
- `docs/ocr_assignment.md`: bảng phân công, ghi chú phạm vi CPO_20 và lệnh của người dùng
  (`-Pages 5-320,622-806`).
- Ghi chú: hai file `configs/CPO_20.json` và `src/pipeline.py` cũng đã được sửa y hệt ở main tree (chưa commit ở
  đó), vì job Qwen đọc config lúc khởi động. Khi gộp nhánh, bỏ các thay đổi chưa commit ở main tree trước
  (`git checkout -- configs/CPO_20.json src/pipeline.py docs/ocr_assignment.md`).

## 4a. Bisect CPO_18: bead hợp lệ 24/28 → 22/31
- Cách đo: script `scratchpad/c3/bisect18.py`. Mỗi commit chạy pipeline CPO_18 205–212 với config của chính commit
  đó, trên **cùng** một bản sao raw, thử cả raw lọc về trạng thái đêm qua (bỏ run tạo sau 2026-11-25T05:00Z) và
  manifest của `787c72f`.
- Kết quả:

| commit | kết quả |
|---|---|
| `e5ca3fb`, `32ab77c`, `ca8f3ce` | **24/28** (han 26, am 30) |
| **`5d17762`** fix(segmentation-streams): ordering, document starts and implicit sections | **22/31** (han 26, am 33) |
| mọi commit sau, tới HEAD | 22/31; raw hiện tại hay raw đêm qua đều giống nhau |

**Nguyên nhân** (so câu am của hai commit):
- `5d17762` tách CPO_18 thành **2 tài liệu** (hai bức thư: 205–208 và 208–212). Đây là cách tách đúng; bản trước gộp
  làm 1 và còn nhét khảo dị trang 207 ("Bản Mạc Tư Khoa chép…", "Theo quan niệm nhà Phật…") vào lớp am.
- Sau khi tách, lớp am của mỗi tài liệu lọt thêm 2 thứ:
  1. tiêu đề chạy "Sưu tầm và khảo luận tác phẩm chữ Hán của người Việt Nam trước TK 10", dính vào câu đầu trang
     206 và chen giữa câu trang 209/210;
  2. trích dẫn nguồn nhiều dòng "( Hoằng minh tập, Quyển thập nhất; … Quyển ngũ thập thất". OCR mất ngoặc đóng, nên
     bộ chú thích chỉ đưa vào review chứ không loại.

  3 câu am thừa này không dóng được, nên bead hợp lệ giảm 2.
- Tiêu đề chạy sót chỉ vì mẫu 8 trang: trong lớp am nó chỉ lặp 2 trang, chưa tới ngưỡng `header_footer_min_pages` = 3.
  Với phạm vi mới 205–544, nó sẽ lặp hàng trăm trang và bị loại.
- **Kết luận:** không phải hồi quy của logic dóng hàng. Số 24/28 của đêm qua có được nhờ gộp sai tài liệu. Đề xuất
  (chưa làm): thêm `clean.common.drop_pattern: ["^Sưu tầm và khảo luận tác phẩm chữ Hán"]` cho CPO_18, và một luật
  khép ngoặc cho trích dẫn nguồn mất ngoặc đóng.

## 4b. Đã áp (có test)
1. **Mẫu kết thúc khảo dị so khớp mờ cho lớp nghia** (`src/segmentation/layers.py`, `NOTE_HEADING_RE`):
   - Khớp KHẢO DỊ / KHÁO DỊ / KHAO DI / KHẢO DỊC, NGUYÊN CHÚ, CHÚ THÍCH, với điều kiện theo sau là `:` hoặc hết dòng.
     Cho phép nội dung cùng dòng (`KHẢO DỊC: a. Bản A.404: …`).
   - Câu bắt đầu bằng "Chú thích của…" / "Khảo dị các bản…" (không có `:`) không bị khớp.
   - Chỉ áp cho lớp nghia; tắt bằng `segmentation.layers.nghia.note_heading_end: false`.
   - Đo CPO_19 15–121: câu nghia 460 → **386**, 0 tiêu đề khảo dị/chú thích còn trong nghia, bead han-am 467 (không
     đổi). CPO_18 và CPO_20 không đổi số câu nghia do luật này.
2. **Niên đại sáng tác và người dịch vào metadata** (`capture_note_lines`, `attach_captured` ở
   `src/cleaning/metadata.py`, bước 1.5 của clean):
   - Dòng `(Làm|Viết|Soạn|Sáng tác … )` thành field `composition`; dòng `<Tên> dịch` hoặc tên trong
     `clean.<lớp>.translator_names` (vd `["Lâm Giang"]` cho CPO_19) thành field `translator`.
   - Ghi vào `05_cleaning/<lớp>.metadata.xml` và `document_metadata.xml`, kể cả khi đã phân đoạn theo luồng; có
     `SOURCE_IDS`.
   - Tắt bằng `clean.<lớp>.capture_notes: false` / `--no-capture-notes`.
   - Đo CPO_20 5–415: 2 dòng niên đại (`(Làm mùa thu năm Bính Thìn 1796)`) và 186 dòng người dịch (7 tên, ví dụ
     "Ngô Linh Ngọc dịch" ×100). Trước đây một phần bị bỏ như chú thích, một phần nằm lẫn trong chính văn lớp thơ:
     tho 669 → 659 câu.
   - CPO_19 cần khai báo `translator_names: ["Lâm Giang"]` (tên đứng một mình, không có chữ "dịch"); chưa thêm vào
     config thật.
3. **`export_clean_text.py`:**
   - Xuất `han.txt`, `viet_am.txt`, `viet_nghia.txt`, `viet_tho.txt` và `mapping.tsv`.
   - Bản gộp `viet.txt` chỉ có khi thêm `--combined`.

## 4c. Rà toàn bộ dòng `note_entries` loại ở CPO_20 (bộ 5–415)
188 dòng: 168 bắt đầu bằng số và có `:`, 20 bắt đầu bằng số mà không có `:`, 0 dòng nối tiếp.

**Đã đọc hết 188 dòng: 0 loại nhầm.** 20 dòng không có `:` đều là khảo dị/chú thích, ví dụ:
- `1. Nguyên bản chép chữ "cầu" không có nghĩa…`;
- `2. Tức Sông Mã ở Thanh Hóa.`;
- `3. Trong câu thơ này tác giả nhắc tới…`.

Một dòng nghi vấn nhẹ: `15. Trong quan lại có ai nên bỏ, có ai nên dùng.` (tr.128). Nó nằm ngay sau mục 14 cùng khối
chú thích, nên gần như chắc là chú thích.

Vẫn đề xuất bật `note_entries` cho `clean.am`, `clean.nghia`, `clean.tho` của CPO_20; chưa áp vào config thật.

## 4d. Mở ảnh
- **CPO_18 tr.543–544** (tiêu đề chạy "Phụ lục"): không có chữ Hán trên hai trang này.
  - Tr.543 là phần cuối PHIÊN ÂM và DỊCH NGHĨA của bài "Tam tu thi tam thủ (chi nhị)", mà nguyên văn Hán (7 dòng) in ở
    **tr.542**.
  - Tr.544 là phần cuối bản dịch và CHÚ THÍCH.

  → **Nên giữ** (là bản dịch của bài Hán tr.542); phạm vi 205–544 hợp lý.
- **CPO_19 tr.5–13:** lời giới thiệu bằng văn xuôi tiếng Việt, có bảng đối chiếu dị bản (tr.8, 10, 13) và chú thích
  (tr.13). **Không có văn bản Hán kèm bản dịch.** Văn bản Hán bắt đầu từ tr.14 (小引 + TIỂU DẪN + DỊCH NGHĨA).
  → Đề xuất `clean.common.excluded_pages: "5-13"` cho CPO_19, hoặc cho thành viên 2 OCR từ tr.14.

## Test, so sánh
- **Test:** 362 passed, 5 fail + 4 error, 1 skipped. Phần hỏng chỉ ở `test_paddle_engine.py`, có từ trước. Round 3
  thêm 11 test vào `tests/test_clean_round2.py`.
- **`--dry-run` config thật CPO_18/19/20:** giống hệt giữa main tree và nhánh.
- **Bộ full sau round 3:**

| Work | câu am | câu nghia | câu tho | bead han-am hợp lệ |
|---|---|---|---|---|
| CPO_19 | 509 | 386 | 244 | 467/561 |
| CPO_20 | 1832 | 1507 | 659 | 1130/1892 |

## Việc người dùng cần quyết
1. Bật `note_entries` cho CPO_20 (0 loại nhầm / 188).
2. CPO_19:
   - `excluded_pages: "5-13"` (hoặc đổi điểm bắt đầu của thành viên 2 sang tr.14);
   - `translator_names: ["Lâm Giang"]`.
3. CPO_18: thêm `drop_pattern` cho tiêu đề chạy "Sưu tầm và khảo luận…" (cần nếu chạy theo từng đoạn ngắn).
