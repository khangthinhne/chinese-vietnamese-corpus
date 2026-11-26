# Vòng clean trên dữ liệu đầy đủ, có cổng chất lượng (2026-11-27)

Nhánh `fix/clean-full`, tách từ `audit/segmentation` (`5975260`). Cả task gom vào **một commit** ở cuối, không push.
Mọi lần chạy pipeline đều dùng bản sao trong scratchpad; `data/` thật không bị ghi. `git status --short data` là 216
dòng trước và sau, đúng như lúc bắt đầu.

## 0. Điều kiện bắt đầu
Task trước đã xong và đã commit (`272cd68`…`5975260`, xem `reports/segmentation_audit_20261126.md` mục 10):
- `am_detection` bật cho CPO_20;
- ghi đè CPO_18: .012/.028 `exclude`, .013/.015 `notes`;
- .054 được sửa bằng luật chung `c7e249c`; trang của .019 (tr.369) đưa vào danh sách OCR lại;
- quét ảo giác Qwen so với dòng Latin của paddlevl;
- báo cáo 69 file bị xoá.

**Trang chờ OCR lại** (19 trang, tách khỏi số liệu cổng):
- CPO_18: 208, 286, 353, 369, 402, 470, 523, 528.
- CPO_19: 102.
- CPO_20: 76, 90, 215, 296, 298, 622, 646, 678, 685, 771.

**Trạng thái: CHƯA OCR lại.** Đã chạy Qwen (my_nlp, `HF_HUB_OFFLINE=1`, `QWEN_BAND_INCHES=1.0` để tạo khoá run mới,
raw cũ giữ nguyên) trên một bản sao `03_ocr`. Cả 3 lần nạp model đều thất bại:
- lỗi `OSError: The paging file is too small for this operation to complete. (os error 1455)` và segfault khi nạp
  checkpoint;
- **ổ C: đầy 100%** (176 GB, còn 0 byte; sau khi xoá các bản sao cũ của chính tôi trong scratchpad thì còn khoảng
  2 GB), nên pagefile không nới ra được;
- GPU vẫn rảnh (0 MiB).

Không có run mới nào được ghi. Cần giải phóng ổ C: (ít nhất khoảng 10 GB) rồi chạy lại
`scratchpad/clean_full/reocr.sh`. Script này resume được và chỉ ghi vào bản sao. Sau đó chép run mới vào `data/`
(append-only) hoặc chạy cùng lệnh với `--data-root data`.

## 1. Cấu hình
`ocr_streams = "han_vie"` đã bật cho cả `configs/CPO_18.json`, `CPO_19.json`, `CPO_20.json` (commit `a5ba17c`,
`ef4777b`). Task này **không đổi config thật**.

Phạm vi trang đã kiểm tra, khớp với phạm vi đã chốt:

| Work | `common.pages` = `ocr.streams.pages` | `clean.common.excluded_pages` |
|---|---|---|
| CPO_18 | 205-260,282-305,308-326,330-334,336-360,363-544 (205–544 bỏ 29 trang ảnh chụp thủ bản) | 261-281,306-307,327-329,335,361-362 |
| CPO_19 | 5-257,267-833 | 5-13,258-266 |
| CPO_20 | 5-320,622-806 | 321-621 |

Chế độ cũ (raw.xml, chi_tra.xml) không bị đụng tới. Mọi nhánh mới chỉ chạy khi có cờ, và bộ test cũ của chế độ cũ
vẫn qua.

## 2–3. Số liệu nền và sau cùng; cổng
- **Nền:** code `480960c` với config thật (bản sao `audit_r3c`).
- **Sau cùng:** code của commit này với các cờ đề xuất bật (bản sao `clean_full/m_r8on`, mục 4). Khi tắt các cờ thì số
  liệu y hệt nền (đo `r1off`), tức code mới không đổi hành vi mặc định.
- Rò rỉ tính sau clean cấu trúc, theo **số dòng riêng biệt** chia cho số dòng của lớp. Dòng trên trang chờ OCR lại
  không tính; Hán–âm không tính các bài chạm trang chờ OCR lại.
- Mọi phép đo chạy lại được bằng `scripts/audit_segmentation.py --pending-pages ...` (mục `gate` trong JSON).


### CPO_18

| Tiêu chí | nền (cờ tắt) | sau cùng (cờ đề xuất bật) |
|---|---|---|
| Hán trong Việt / Latin trong Hán | 0 / 0 | 0 / 0 |
| dòng mất / hai nơi | 0 / 0 | 0 / 0 |
| rò rỉ han / am / nghia / tho | 0.08% (1/1185) / 0.80% (6/754) / 0.29% (2/687) / 5.88% (1/17) | 0.00% (0/1184) / 0.00% (0/707) / 0.00% (0/731) / 0.00% (0/16) |
| rò rỉ theo loại (am+nghia+tho) | inline_note_digit 5, repeated_line 4 | 0 |
| Hán–âm khớp tuyệt đối / ±10% (cổng) | 0.314 / 0.817 | 0.357 / 0.917 |
| ±10% thơ / văn xuôi | 1.0 (3 bài) / 0.807 (57 bài) | 1.0 (3 bài) / 0.912 (57 bài) |
| câu Hán rất dài / 1 chữ | 30 / 0 | 30 / 0 |
| câu Việt rất ngắn / kết thúc viết tắt | 14 / 12 | 15 / 11 |
| ngoặc kép lệch (Việt) | 134 | 134 |
| bài lệch số câu han/am > 30% | 13 / 70 | 6 / 70 |
| câu xuyên trang | 125 | 124 |
| bead han-am hợp lệ | 1108/1350 (trung vị theo bài 1.0) | 1108/1231 (trung vị theo bài 1.0) |
| CỔNG 1–3 | KHÔNG ĐẠT | ĐẠT |

### CPO_19

| Tiêu chí | nền (cờ tắt) | sau cùng (cờ đề xuất bật) |
|---|---|---|
| Hán trong Việt / Latin trong Hán | 0 / 0 | 0 / 0 |
| dòng mất / hai nơi | 0 / 0 | 0 / 0 |
| rò rỉ han / am / nghia / tho | 0.04% (2/4886) / 0.89% (29/3264) / 1.69% (58/3434) / 0.36% (3/842) | 0.00% (0/4884) / 0.09% (3/3181) / 0.38% (13/3438) / 0.12% (1/849) |
| rò rỉ theo loại (am+nghia+tho) | inline_note_digit 11, note_entry 61, repeated_line 18, translator_name 1 | inline_note_digit 4, note_entry 12, translator_name 1 |
| Hán–âm khớp tuyệt đối / ±10% (cổng) | 0.433 / 0.858 | 0.441 / 0.873 |
| ±10% thơ / văn xuôi | 1.0 (106 bài) / 0.765 (162 bài) | 1.0 (108 bài) / 0.787 (160 bài) |
| câu Hán rất dài / 1 chữ | 289 / 0 | 289 / 0 |
| câu Việt rất ngắn / kết thúc viết tắt | 79 / 16 | 79 / 15 |
| ngoặc kép lệch (Việt) | 54 | 54 |
| bài lệch số câu han/am > 30% | 21 / 270 | 16 / 270 |
| câu xuyên trang | 245 | 245 |
| bead han-am hợp lệ | 3507/3926 (trung vị theo bài 0.98) | 3511/3837 (trung vị theo bài 1.0) |
| CỔNG 1–3 | KHÔNG ĐẠT | ĐẠT |

### CPO_20

| Tiêu chí | nền (cờ tắt) | sau cùng (cờ đề xuất bật) |
|---|---|---|
| Hán trong Việt / Latin trong Hán | 0 / 0 | 0 / 0 |
| dòng mất / hai nơi | 0 / 0 | 0 / 0 |
| rò rỉ han / am / nghia / tho | 0.00% (0/2806) / 4.10% (112/2730) / 1.29% (33/2552) / 0.71% (10/1418) | 0.00% (0/2806) / 0.44% (11/2511) / 0.21% (5/2349) / 0.28% (5/1766) |
| rò rỉ theo loại (am+nghia+tho) | inline_note_digit 62, note_entry 79, repeated_line 3, source_line 11 | inline_note_digit 8, note_entry 11, repeated_line 2 |
| Hán–âm khớp tuyệt đối / ±10% (cổng) | 0.502 / 0.803 | 0.522 / 0.869 |
| ±10% thơ / văn xuôi | 1.0 (117 bài) / 0.519 (81 bài) | 1.0 (123 bài) / 0.653 (75 bài) |
| câu Hán rất dài / 1 chữ | 59 / 0 | 59 / 0 |
| câu Việt rất ngắn / kết thúc viết tắt | 62 / 11 | 62 / 11 |
| ngoặc kép lệch (Việt) | 18 | 18 |
| bài lệch số câu han/am > 30% | 22 / 209 | 9 / 209 |
| câu xuyên trang | 34 | 34 |
| bead han-am hợp lệ | 2031/2783 (trung vị theo bài 0.889) | 2048/2621 (trung vị theo bài 0.889) |
| CỔNG 1–3 | KHÔNG ĐẠT | ĐẠT |


**Phép đo đã sửa** (`scripts/audit_segmentation.py`, test trong `tests/test_clean_full_fixes.py`). Phép đo cũ báo sai
ở mấy chỗ sau:
- **Chữ số chỉ mục ở nghia/tho:** phép đo cũ đếm cả số thật ("Ngày 20 tháng 2", "Bài 2"). Giờ chỉ tính khi cùng trang có
  mục chú thích mang đúng số đó. CPO_19 nghia giảm từ 156 xuống 6 dòng.
- **Dòng lặp:** phép đo cũ đếm cả điệp khúc ("Ngươi chẳng thấy:", "Than ôi!"). Giờ chỉ tính tiêu đề chạy, tức dòng lặp
  ≥ 3 trang mà đa số lần xuất hiện nằm ở 2 dòng đầu/cuối trang.
- **Ghép metadata với dòng OCR:** trước đây ghép không phân biệt hoa thường, gây "hai nơi" giả ("LÝ HOẠC LUẬN" và
  "Lý hoạc luận."). Giờ dòng có chữ y hệt được ưu tiên, dòng chưa nằm trong lớp nào được ưu tiên, và dòng 1 ký tự "I"
  trong tiêu đề ghép được nhận.
- **Kế toán dòng:** review `override_*` của ghi đè config giờ được tính là một nơi đến có dấu vết.
- **Loại rò rỉ mới:** `ocr_junk` (`[UNCLEAR]`, LaTeX, pinyin "-: zhuǎn", câu tiếng Anh) và `source_line` (cùng luật
  với bước clean).

### Kết quả cổng theo work
| Work | Cổng 1 | Cổng 2 (< 1% mỗi lớp) | Cổng 3 (±10% ≥ 85%) | Cổng 4 (rà 10 bài) | Kết luận |
|---|---|---|---|---|---|
| CPO_18 | ĐẠT | ĐẠT (0% mọi lớp) | ĐẠT 0,917 | ĐẠT có ghi chú (xem dưới) | **ĐẠT** khi bật cờ đề xuất |
| CPO_19 | ĐẠT | ĐẠT (lớn nhất nghia 0,38%) | ĐẠT 0,873 | ĐẠT | **ĐẠT** khi bật cờ đề xuất |
| CPO_20 | ĐẠT | ĐẠT (lớn nhất am 0,44%) | ĐẠT 0,869 | ĐẠT có ghi chú | **ĐẠT** khi bật cờ đề xuất |

Với **config thật hiện tại** (cờ tắt), cả 3 work đều **KHÔNG ĐẠT**:
- CPO_18: tho 5,9%, Hán–âm 0,817;
- CPO_19: nghia 1,7%;
- CPO_20: am 4,1%, Hán–âm 0,803.

Muốn đạt thì phải bật các cờ ở mục 4, nên việc bật cần bạn quyết.

**Cổng 4.** Rà 10 bài ngẫu nhiên mỗi work (seed 20261127, trừ bài chạm trang chờ OCR lại;
`scratchpad/clean_full/sample_review.py`). Cách rà: đọc văn bản các lớp (dòng đầu/cuối, số dòng từng lớp) và so cấu
trúc với sách. **Tôi chưa mở ảnh trang của 30 bài mẫu**; ảnh chỉ được mở cho các trang trong bản quét ảo giác.
- **CPO_19:** 10/10 bài đúng cấu trúc. Chỉ có lỗi OCR: tiêu đề Hán rác (.124 `达你及过田八玩之作`, .157 `士士，貝及制性`),
  phiên âm mất dấu (.148 "Bien chu hoan hai…"). Các lỗi này đã có trong danh sách rà soát OCR.
- **CPO_18:**
  - Đã sửa: .042 điểm tách trễ một câu ("Hỏi rằng: Khổng Tử nói…" còn ở am), xử lý bằng ngưỡng 0,55.
  - Còn lại: .008 có 1 dòng ghi chú biên tập cuối nghia ("…thu một số chữ hợp lý hơn ở các bản còn lại.") và 1 dòng
    tên sách nguồn cuối am ("Đại Đường Tây Vực cầu pháp cao tăng truyện"), mỗi lớp thừa 1 câu.
  - .014 (Đề lữ sấn) không có lớp nghia.
- **CPO_20:**
  - Đã sửa: .039 và .145 trước đây dịch thơ dính vào nghia (sửa G).
  - Còn lại: .080 có 2 dòng chú thích cuối am ("…Trung Quốc, ông không theo kịp, về làng ở ẩn.").
  - Nghi tách nhầm ở .134 (điểm tách "Ẩn ẩn cô phàm quái tịch chiếu," trông như phiên âm).
- Lỗi hình thức ghi nhận, không chặn: ngoặc kép lệch CPO_18 134 / CPO_19 54 / CPO_20 18 câu (chưa có luật an toàn,
  không sửa).

**Câu ghép xuyên trang:** có 124 / 245 / 34 câu (CPO_18/19/20), không đổi so với nền. **Chưa mở ảnh mẫu 10 câu mỗi
work.** Danh sách mẫu nằm trong JSON (`sentences.cross_page.sample`).

## 4. Các sửa (mỗi sửa có test trong `tests/test_clean_full_fixes.py`; số đo theo từng vòng)
| # | Sửa | Module | Cờ (mặc định tắt) |
|---|---|---|---|
| A | Tiêu đề chạy tính trên hai luồng cả work, khớp mờ, chỉ dòng ở mép trang ("Cẩm Dưỡng Nhân Thọ", "Sử tẩm và khảo luận…", "欽定四庫全書") | `streams.edge_running_header_ids`, `cleaning/page_filter.py` | `clean.common.stream_running_headers` |
| B | Mục chú thích "N. …" không có "Thuật ngữ:" được nhận khi N trùng chữ số chỉ mục cùng trang (kể cả sau dấu câu), hoặc liền sau mục N−1 đã nhận | `cleaning/footnotes.py` | `clean.common.footnotes.note_entry_markers` |
| C | Lớp am: chỉ mục sau dấu câu ("đại thần, 2 quốc gia") và cuối dòng ("Tỳ bà đình 1", "mao 4."), trừ số bài ("BÀI 2", "Kỳ 3") | `cleaning/artifacts.py`, `model.py` | `clean.common.am_markers_extended` |
| D | Dòng nguồn "Trích <Tên sách>" (không dấu kết câu) chuyển sang metadata `source`; dòng phiên âm "Trích thủy xao…" được giữ | `cleaning/metadata.py` | `clean.common.capture_source_lines` |
| E | Số mục chú thích và chỉ mục theo trang lấy từ cả vie.xml; lớp nghia/tho xoá chỉ mục trùng số ("Đảo Bồng 3 khói tỏa"); B dùng thêm chỉ mục của cả luồng | `cleaning/pipeline.py`, `artifacts.py` | `clean.common.stream_note_markers` |
| F | Phần dịch dính vào cuối am khi OCR mất "Dịch nghĩa:" (CPO_20 tr.137, CPO_18 Mâu Tử) chuyển sang nghia, bằng độ khớp cách đọc Hán–Việt từng dòng | `segmentation/am_detect.split_point`, `layers.py` | `segmentation.am_detection.split_lines` |
| G | Dịch thơ dính vào nghia khi mất "Dịch thơ:" (bài thơ, nghia ≥ 1,6× số dòng am, phần đuôi là câu ≤ 12 âm tiết) chuyển sang tho | `am_detect.verse_tho_split`, `layers.py` | `segmentation.verse_tho_split` |

Kèm theo:
- `scripts/export_clean_text.py` giờ ghi vào `05_cleaning/text/`, có thêm `--package-root/--topic` (mục 5).
- Script mới `scripts/export_ocr_review.py`.
- `DataLayout.cleaning_text_dir`.
- Phép đo cổng trong `audit_segmentation.py`.
- Docs: `docs/pipeline.md`, `docs/segmentation_architecture.md`.

**Số liệu từng vòng** (bản sao, CỔNG 1–3; rò rỉ của lớp tệ nhất; bead han-am hợp lệ):

| Vòng | Bật thêm | CPO_18 | CPO_19 | CPO_20 |
|---|---|---|---|---|
| nền / r1off | (tắt hết) | X: tho 5,9%, ±10% 0,817; bead 1108 | X: nghia 1,8%; 3507 | X: am 4,1%, 2 hai nơi\*, ±10% 0,803; 2031 |
| r1on | A, B, (CPO_19 `note_entries`) | X: ±10% 0,817; 1108 | **ĐẠT**; 3511 | X: am 3,7%; 2031 |
| r2on | + C, D | X: ±10% 0,817 | ĐẠT | X: am 1,7% (cổng 1 hết lỗi\*) |
| r3on | + E | X: ±10% 0,817 | ĐẠT (nghia 0,24%) | X: am 1,4% |
| r4on | B, C tinh chỉnh | X: ±10% 0,817 | ĐẠT | X: ±10% 0,803 (am 0,41%) |
| r6on | + F | **ĐẠT** 0,90; 1108 | ĐẠT 0,869; 3511 | **ĐẠT** 0,869; 2050 |
| r7on | F ngưỡng 0,55 + G | ĐẠT; 1103 (−5) | ĐẠT; 3507 (−4) | ĐẠT; 2048 |
| **r8on** | F: dòng quyết định ≥ 6 âm tiết (CPO_19.265 tách nhầm ở văn tán 4 chữ) | **ĐẠT 0,917; 1108** | **ĐẠT 0,873; 3511** | **ĐẠT 0,869; 2048** |

\* "Hai nơi" ở CPO_20 là lỗi phép đo (hai dòng "I" cùng trang), đã sửa ở phép đo.

Bead hợp lệ không tụt so với nền ở work nào: CPO_18 1108 = 1108, CPO_19 3507 → 3511, CPO_20 2031 → 2048. Tổng số bead
giảm (1350 → 1231, 3926 → 3837, 2783 → 2621) vì cặp rác bị loại.

**Đề xuất bật** (CHƯA áp vào config thật; cần bạn quyết):
```json
"clean": {"common": {"stream_running_headers": true, "am_markers_extended": true, "capture_source_lines": true,
                     "stream_note_markers": true, "footnotes": {"note_entry_markers": true}}},
"segmentation": {"am_detection": {"split_lines": true}, "verse_tho_split": true}
```
Riêng CPO_19 thêm `"clean.common.footnotes.note_entries": true`. Với CPO_18 và CPO_19 cần thêm khoá `am_detection`
chỉ để bật `split_lines`; `enabled` (đổi lớp cả bài) vẫn để tắt như đã quyết. Lưu ý: khoá `split_lines` nằm trong
`am_detection`, nhưng không phụ thuộc `enabled`.

## 5. Đầu ra (trên bản sao `scratchpad/clean_full/m_r8on/`)
1. **Text sạch** `data/<work>/05_cleaning/text/` (mỗi câu một dòng, kèm `mapping.tsv` truy vết về ID dòng OCR):

   | Work | han.txt | viet_am.txt | viet_nghia.txt | viet_tho.txt |
   |---|---|---|---|---|
   | CPO_18 | 1219 | 1279 | 1253 | 16 |
   | CPO_19 | 3829 | 3771 | 3426 | 849 |
   | CPO_20 | 2530 | 2362 | 1660 | 1775 |

   - Trước đây script ghi vào `07_export/03_text_sach/`; giờ mặc định là `05_cleaning/text/`
     (`DataLayout.cleaning_text_dir`).
   - Khi đóng gói: `--package-root DIR --topic MA_DE_TAI` chép sang `DIR/<MA_DE_TAI>_<WORK>/03_text_sach/`.
   - Repo chưa có bước đóng gói 07_export nào khác.
2. **Danh sách rà soát OCR** `review/review_ocr_<work>.xlsx`:
   - có sheet riêng theo khoảng trang thành viên (CPO_18 `tv1_205-544`; CPO_19 `tv2_5-623` và `tv3_624-833`;
     CPO_20 `ca_work`);
   - mỗi dòng gồm: loại, trang, ID, chi tiết, văn bản, đường dẫn ảnh trang.

   | Work | dòng | qwen_low_conf | vie_cross_* / unconfirmed | han_am_mismatch | han_simplified |
   |---|---|---|---|---|---|
   | CPO_18 | 747 | 430 | 10 | 10 | 297 |
   | CPO_19 | 931 (710 + 221) | 84 | 3 | 35 | 809 |
   | CPO_20 | 796 | 62 | 8 | 27 | 699 |

   Chỉ xuất danh sách, chưa có cơ chế áp bản sửa. `han_simplified` nhiều vì OpenCC đếm cả dị thể. Các file xlsx này
   nằm trong bản sao, không commit.

## Checklist "2 – Làm sạch text" của hướng dẫn
| Yêu cầu | Module | Test | Số đo (sau cùng) | Trạng thái |
|---|---|---|---|---|
| Bỏ đầu/chân trang | `page_filter.py` + **A** `stream_running_headers` | `test_edge_running_headers_*`, `test_stream_header_ids_*` | Tiêu đề chạy trong lớp: 0 / 0 / 2 (CPO_20 tho "Ngô linh ngọc dịch", "I.") | đạt (khi bật A) |
| Bỏ trang không thuộc nội dung chính | `excluded_pages`, ghi đè `document_overrides` | test cũ, `test_segmentation_overrides` | Phạm vi đúng như đã chốt (mục 1) | đạt |
| Ghép từ/câu ngắt do chuyển trang | `reconstruct_page_boundaries` | test cũ | Câu xuyên trang 124/245/34, chưa mở ảnh mẫu | chưa kiểm bằng ảnh |
| Loại dòng trùng | `detect_duplicates` | test cũ | Không có sự cố mới | đạt |
| Loại chú thích chân trang, chỉ mục | `footnotes.py` + **B**, **C**, **E** | `test_note_entry_*`, `test_am_markers_*`, `test_stream_page_note_numbers_*` | Mục chú thích trong lớp 0 / 12 / 11; chỉ mục 0 / 4 / 8 | đạt (< 1%) |
| Sửa ký tự rác / khoảng trắng | `artifacts.py`, `normalization.py` | test cũ | `ocr_junk` sau clean: 0 (ngoài trang chờ OCR lại) | đạt |
| Chuẩn hoá dấu câu không đổi nghĩa | `normalization.py` | test cũ | Ngoặc kép lệch 134 / 54 / 18 | một phần (ghi nhận) |
| Giữ tên riêng / niên hiệu / số / đơn vị | Không bước nào thay chữ; **E** chỉ xoá số trùng số mục chú thích cùng trang | `test_stream_page_note_numbers_*` ("ngày 20 tháng 2" giữ nguyên) | — | đạt |
| Tiêu đề / người dịch / nguồn ra metadata | `capture_note_lines` + **D** | `test_source_lines_*` | `source_line` trong lớp: 0 | đạt (khi bật D) |
| Bỏ qua bảng biểu, tranh/ảnh | Loại theo trang (ảnh chụp thủ bản CPO_18, lời giới thiệu CPO_19) | — | — | đạt theo trang |

## Vấn đề còn lại
| Vấn đề | Bằng chứng | Chặn dóng hàng? | Đề xuất |
|---|---|---|---|
| 19 trang chờ OCR lại | Mục 0; ổ C: đầy | Có, cho các bài liên quan (đã tách khỏi cổng) | Giải phóng C:, chạy `reocr.sh` |
| Văn xuôi Hán–âm ±10% thấp (CPO_19 0,787, CPO_20 0,653) | `gate.checks.3_han_am_10pct.by_kind` | Không (cổng tính chung) | Phần lớn do chú thích kẹp giữa văn và chữ Hán OCR rác; xem `han_am_mismatch` trong xlsx |
| CPO_20.053: tr.123–134 là thơ/chiếu Nôm, không có chữ Hán đối ứng | `segmentation_audit` mục 10.2 | Có, cho 1 bài | `document_overrides` `exclude` cho tr.123–134, hoặc tách bài |
| CPO_18.012: nửa đầu văn bia bị loại (`exclude`) | mục 10.3 | Không | Gộp vào .013 để giữ 28 dòng Hán |
| Tiêu đề "KHAO DI" sau khối Hán không chặn việc mở ngầm am | CPO_18.013 | Không (đã có ghi đè) | Luật chung có cờ |
| Ngoặc kép lệch | 134 / 54 / 18 câu | Không | Chưa có luật an toàn |
| Metadata `author = "I"` (số La Mã bài thơ) | CPO_20.111 tr.238 | Không | Bộ trích metadata bỏ số La Mã đứng một mình |
| Ổ C: đầy | `df`: 176 GB, 99–100% | Chặn OCR | Dọn ổ C: |

## Test, dry-run, commit
- Test: **475 passed**, 1 skipped (nền đầu task: 443 passed; sau task trước: 455). Lệnh:
  `pytest tests -q --ignore=tests/test_paddle_engine.py`. Có 20 test mới trong `tests/test_clean_full_fixes.py`.
- `python -m src.pipeline --config configs/CPO_XX.json --dry-run`: CPO_18 21 lệnh, CPO_19 20, CPO_20 20. Không khác
  trước, vì config thật không đổi và `ocr_streams` đã bật từ trước.
- Commit duy nhất: xem thông điệp commit trên nhánh `fix/clean-full`. Không push.

## Chạy lại vào `data/` thật (KHÔNG chạy; chỉ khi bạn đã bật các cờ đề xuất trong config)
```powershell
python -m src.pipeline --config configs/CPO_18.json --steps layers,segment,clean,sentence,align --force
python -m src.pipeline --config configs/CPO_19.json --steps layers,segment,clean,sentence,align --force
python -m src.pipeline --config configs/CPO_20.json --steps layers,segment,clean,sentence,align --force
foreach ($w in "CPO_18","CPO_19","CPO_20") {
  python scripts/audit_segmentation.py --work-id $w --json reports/audit_$w.json
  python scripts/export_clean_text.py --work-id $w
  python scripts/export_ocr_review.py --work-id $w
}
```

## Việc cần bạn quyết
1. Bật các cờ đề xuất ở mục 4 vào config thật. Đây là điều kiện để đạt cổng.
2. Giải phóng ổ C: rồi OCR lại 19 trang (hoặc cho phép đặt bản sao/pagefile sang ổ E:).
3. CPO_20.053 tr.123–134 (Nôm, không có chữ Hán): loại khỏi lớp dóng hay tách bài.
4. CPO_18.012: giữ `exclude` hay gộp vào .013.
