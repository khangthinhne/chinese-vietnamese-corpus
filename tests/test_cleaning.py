"""Unit test cho TỪNG BƯỚC của bộ làm sạch module-hoá (src/cleaning/).

Chạy từ thư mục gốc repo:  python -m pytest tests/test_cleaning.py -q
Dữ liệu GIẢ LẬP, gồm cả mẫu OCR thật ở task (® ° Ø ‡ ` N, chú thích nhiều dòng...).
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.cleaning import (Audit, CleanConfig, CleaningResult, FootnoteConfig, OCRCleaningConfig,  # noqa: E402
                          RawLine, build_stream, clean_lines, clean_ocr_artifacts, clean_vi_line,
                          detect_duplicates, detect_footnotes, extract_section,
                          filter_pages, normalize_unicode, reconstruct_page_boundaries,
                          run_cleaning, split_han, split_vi)
from src.cleaning.filtering import filter_invalid_lines   # noqa: E402
from src.cleaning.segmentation import make_sentences      # noqa: E402

WORK = "T_01"


def rl(i: int, page: int, text: str, last: bool = False) -> RawLine:
    return RawLine(f"{WORK}.x.{page:03d}.L{i:03d}", page, text, last)


def ids(lines: list[RawLine]) -> list[str]:
    return [ln.id for ln in lines]


# --------------------------------------------------------------------------- #
# 1. Unicode / chuẩn hoá
# --------------------------------------------------------------------------- #
def test_unicode_nfc_diacritics():
    assert normalize_unicode("e\u0301") == "é"                       # ghép dấu Việt
    assert normalize_unicode("Hà\t Nội\n") == "Hà Nội"               # tab/xuống dòng -> cách
    assert normalize_unicode("夫\u200b道") == "夫道"                   # zero-width biến mất
    assert normalize_unicode("，。？！") == "，。？！"                  # dấu đầy dải giữ nguyên
    assert normalize_unicode("㗂") == "㗂"                             # chữ Hán-Nôm hiếm giữ nguyên


# --------------------------------------------------------------------------- #
# 2. Lọc trang
# --------------------------------------------------------------------------- #
def test_page_filter_excluded_and_blank():
    lines = [rl(1, 1, "trang 1"), rl(2, 2, "   "), rl(3, 3, "trang 3"), rl(4, 4, "quảng cáo")]
    audit = Audit(WORK, "am")
    kept, records = filter_pages(lines, CleanConfig(layer="am", excluded_pages=[4]), audit)
    assert ids(kept) == [lines[0].id, lines[2].id]
    reasons = {r["page"]: r["reason"] for r in records}
    assert reasons[4] == "irrelevant_page" and reasons[2] == "blank_page"
    assert any(r["reason"] == "irrelevant_page" for r in audit.rows)


def test_page_filter_recurring_header_footer_and_edge_content_kept():
    lines = []
    for p in (1, 2, 3, 4):
        lines += [rl(1, p, "SAUU TẦM TÁC PHẨM" if p < 4 else "Trích chương 2"),  # header lặp 3 trang
                  rl(2, p, f"nội dung trang {p}"),
                  rl(3, p, f"tiếp nội dung {p}"),
                  rl(4, p, "— 205 —" if p > 1 else "số 205")]                    # footer lặp 3 trang
    audit = Audit(WORK, "am")
    kept, records = filter_pages(lines, CleanConfig(layer="am"), audit)
    texts = [ln.text for ln in kept]
    assert texts.count("SAUU TẦM TÁC PHẨM") == 0          # header lặp >=3 trang bị bỏ
    assert texts.count("— 205 —") == 0                    # footer lặp bị bỏ
    assert "Trích chương 2" in texts                      # trang 4 nội dung khác: GIỮ (không xoá nhầm)
    assert "số 205" in texts                              # trang 1 footer khác: GIỮ
    assert any(r["reason"] == "page_header" for r in records)
    assert any(r["reason"] == "page_footer" for r in records)


# --------------------------------------------------------------------------- #
# 3. Trùng lặp OCR
# --------------------------------------------------------------------------- #
def test_duplicates_exact_same_page_and_short_repeat_kept():
    lines = [rl(1, 1, "một dòng nội dung dài để xét trùng"), rl(2, 1, "một dòng nội dung dài để xét trùng"),
             rl(3, 1, "Amen"), rl(4, 1, "Amen"),                            # ngắn: không bao giờ coi là trùng
             rl(5, 1, "dòng khác hẳn")]
    audit = Audit(WORK, "am")
    kept, records = detect_duplicates(lines, CleanConfig(layer="am"), audit)
    assert ids(kept) == [lines[0].id, lines[2].id, lines[3].id, lines[4].id]
    assert records[0]["kept_id"] == lines[0].id and records[0]["removed_id"] == lines[1].id


def test_duplicates_near_across_overlapping_pages():
    a = "Nhược xưng kỳ công năng lợi vật, độ thoát vô lượng"
    b = "Nhược xưng kỳ công năng lợi vật, độ thoát vô luợng"          # lệch 1 ký tự OCR
    lines = [rl(1, 7, a), rl(2, 7, "trang 7 dòng 2"), rl(3, 7, "trang 7 dòng 3"),
             rl(4, 8, b), rl(5, 8, "trang 8 dòng 2")]
    audit = Audit(WORK, "am")
    kept, records = detect_duplicates(lines, CleanConfig(layer="am"), audit)
    assert ids(kept) == [lines[0].id, lines[1].id, lines[2].id, lines[4].id]
    assert records and abs(records[0]["similarity"] - 1.0) < 0.05 and records[0]["page"] == 8


def test_duplicates_similar_but_not_in_overlap_kept():
    t = "câu này giống nhau về mặt văn bản trông thấy rõ ràng"
    lines = [rl(1, 1, t), rl(2, 1, "đệm"), rl(3, 1, "đệm"), rl(4, 1, "đệm"), rl(5, 1, "đệm"), rl(6, 1, "đệm"),
             rl(7, 2, t.replace("rõ ràng", "rõ ràng."))]
    audit = Audit(WORK, "am")
    kept, _ = detect_duplicates(lines, CleanConfig(layer="am", duplicate_context=5), audit)
    assert ids(kept) == [ln.id for ln in lines]                       # xa vùng biên: không xét


# --------------------------------------------------------------------------- #
# 4. Nhiễu OCR (artifacts)
# --------------------------------------------------------------------------- #
def test_artifacts_remove_and_replace_patterns_recorded():
    cfg = OCRCleaningConfig(remove_patterns=[r"\*"], replace_patterns=[(r"\|", " ")])
    res = clean_ocr_artifacts("Lục triều * uăn|phần", cfg)
    assert res.text == "Lục triều  uăn phần"
    assert ("\\*", "*") in res.removed and res.replaced[0][0] == "\\|"


def test_artifacts_markers_removed_editorial_kept():
    text = "lợi vật [1], (2) lợi （3） lợi ④ cho [Phật], [muôn]vật, [người]"
    res = clean_ocr_artifacts(text, OCRCleaningConfig())
    assert "[Phật]" in res.text and "[muôn]vật" in res.text and "[người]" in res.text
    assert "[1]" not in res.text and "(2)" not in res.text and "（3）" not in res.text and "④" not in res.text


def test_artifacts_noise_removed_suspect_flagged():
    text = "suông mà không có thực ®? lợi N cho M4 ` không"
    audit = Audit(WORK, "nghia")
    out = clean_lines([RawLine("a", 1, text)], OCRCleaningConfig(), audit, "nghia")
    t = out[0].text
    assert "®" not in t                                                # nhiễu biết chắc: XOÁ
    assert ("noise_char", "®") in clean_ocr_artifacts(text, OCRCleaningConfig()).removed
    for ch in ("N", "M4", "`"):
        assert ch in t                                                 # nghi vấn: GIỮ, chỉ cờ
    assert "uncertain:`" in out[0].flags
    assert audit.reviews and all(r["line_id"] == "a" for r in audit.reviews)


# --------------------------------------------------------------------------- #
# 5. Chú thích / trích dẫn
# --------------------------------------------------------------------------- #
def test_footnote_single_line_paren():
    lines = [rl(1, 1, "nội dung chính."), rl(2, 1, "( 『弘明集』卷十一 , 全宅文卻五十七 )")]
    res = detect_footnotes(lines, FootnoteConfig(), "han")
    assert res.excluded_ids == {lines[1].id}
    assert res.notes[0].category == "footnote" and res.notes[0].pages == [1]


def test_footnote_multiline_bibliographic():
    lines = [rl(1, 1, "Đệ tử Lý Miễu kính lễ !,"),
             rl(2, 1, "( Hoằng minh tập, Q. 11 ; Toàn Thương"),
             rl(3, 1, "cổ, Tam đại, Tôn, Hán, Tam quốc, Lục"),
             rl(4, 1, "triều uăn, phần Toàn Tống uăn, Q.57 )"),
             rl(5, 1, "câu chính thức tiếp theo.")]
    res = detect_footnotes(lines, FootnoteConfig(), "nghia")
    assert res.excluded_ids == {lines[1].id, lines[2].id, lines[3].id}
    note = res.notes[0]
    assert note.category == "bibliographic_note" and note.action == "exclude"
    assert note.line_ids == [lines[1].id, lines[2].id, lines[3].id]


def test_footnote_across_pages_and_fullwidth():
    lines = [rl(1, 9, "nội dung."),
             rl(2, 9, "（ Hoằng minh tập, Quyển thập nhất ;"),
             rl(3, 10, "Toàn Thượng cổ, Tam đại,"),
             rl(4, 10, "Quyên ngũ thập thất ）"),
             rl(5, 10, "câu tiếp.")]
    res = detect_footnotes(lines, FootnoteConfig(), "nghia")
    assert res.excluded_ids == {lines[1].id, lines[2].id, lines[3].id}
    assert res.notes[0].pages == [9, 10]                              # qua trang, gom MỘT chú thích


def test_footnote_numbered_item_multiline():
    lines = [rl(1, 1, "CHÚ THÍCH :"),
             rl(2, 1, "(1) Pháp sư : người am tường kinh Phật, có khả năng giảng"),
             rl(3, 1, "dạy cho người khác."),
             rl(4, 1, "câu chính.")]
    res = detect_footnotes(lines, FootnoteConfig(), "am")
    assert res.excluded_ids == {lines[1].id, lines[2].id}


def test_footnote_ordinary_parens_and_editorial_kept():
    lines = [rl(1, 1, "nói Đạo [Phật] là khéo (xem thêm ở đây) để làm lợi."),
             rl(2, 1, "ông (đệ tử) kính lễ, vui vẻ.")]
    res = detect_footnotes(lines, FootnoteConfig(), "nghia")
    assert res.excluded_ids == set()                                   # giữ nguyên cả hai dòng


def test_footnote_policies():
    lines = [rl(1, 1, "( Hoằng minh tập, Q. 11 )"), rl(2, 1, "( Toàn Thượng cổ, Q.57 )"), rl(3, 1, "chính.")]
    for policy, n_excluded in (("exclude", 2), ("preserve", 0), ("review", 0)):
        res = detect_footnotes([RawLine(ln.id, ln.page, ln.text) for ln in lines],
                               FootnoteConfig(policy=policy), "am")
        assert len(res.excluded_ids) == n_excluded
        assert res.notes[0].action == policy


def test_footnote_unclosed_is_review_not_deleted():
    lines = [rl(1, 1, "nội dung."), rl(2, 1, "( Hoằng minh tập, Q. 11 ;")]   # không đóng
    res = detect_footnotes(lines, FootnoteConfig(), "nghia")
    assert res.excluded_ids == set()
    assert res.notes[0].category == "uncertain_note" and res.notes[0].action == "review"


# --------------------------------------------------------------------------- #
# 6. Nối qua ranh giới trang
# --------------------------------------------------------------------------- #
def test_reconstruction_dehyphenate_and_continuation():
    lines = [rl(1, 1, "tra conjunct-"), rl(2, 2, "ion còn nữa.")]
    audit = Audit(WORK, "am")
    out, ops = reconstruct_page_boundaries(lines, CleanConfig(layer="am"), audit, "am")
    assert out[0].text == "tra conjunct" and "dehyphenated" in out[0].flags
    assert ops[0]["type"] == "dehyphenate" and ops[0]["from_page"] == 1 and ops[0]["to_page"] == 2


def test_reconstruction_ambiguous_join_flagged():
    lines = [rl(1, 1, "câu chưa trọn qua trang"), rl(2, 2, "Câu mới viết hoa.")]
    audit = Audit(WORK, "nghia")
    out, ops = reconstruct_page_boundaries(lines, CleanConfig(layer="nghia"), audit, "nghia")
    assert [o["type"] for o in ops] == ["ambiguous_join"]
    assert audit.reviews and out[0].text == lines[0].text             # không tự ý sửa


def test_reconstruction_complete_paragraph_not_merged():
    lines = [rl(1, 1, "Đoạn một trọn vẹn."), rl(2, 2, "Đoạn hai trọn vẹn.")]
    audit = Audit(WORK, "nghia")
    out, ops = reconstruct_page_boundaries(lines, CleanConfig(layer="nghia"), audit, "nghia")
    assert ops == []                                                  # đoạn độc lập không ghi phép nối


def test_reconstruction_han_continuation():
    lines = [rl(1, 1, "夫道處清虛"), rl(2, 2, "四大理常。")]
    audit = Audit(WORK, "han")
    out, ops = reconstruct_page_boundaries(lines, CleanConfig(layer="han"), audit, "han")
    assert [o["type"] for o in ops] == ["sentence_continuation"]
    assert out[0].text == lines[0].text


def test_build_stream_dehyphenated_no_space():
    a = RawLine("a", 1, "tra conjunct", flags=["dehyphenated"])
    b = RawLine("b", 2, "ion.")
    text, owner = build_stream([a, b], "am")
    assert text == "tra conjunction."                                 # không chèn dấu cách


# --------------------------------------------------------------------------- #
# 7. Lọc dòng (filtering)
# --------------------------------------------------------------------------- #
def test_filtering_patterns_and_heading_context():
    cfg = CleanConfig(layer="nghia", drop_patterns=list(["^Phần II"]))
    audit = Audit(WORK, "nghia")
    lines = [RawLine("a", 1, "Phần II: Nguyên văn"), RawLine("b", 1, "Đạo vốn ở nơi thanh tịnh."),
             RawLine("c", 1, "một dòng ngắn")]
    kept, rows = filter_invalid_lines(lines, cfg, audit)
    reasons = {r[0]: r[2] for r in rows}
    assert reasons["a"] == "pattern:^Phần II"
    assert ids(kept) == ["b", "c"]


def test_filtering_bibliographic_note_not_heading():
    # dòng trích nguồn nhiều dòng đã bị bước footnotes loại trước -> filtering không thấy
    lines = [rl(1, 1, "Nay đang lúc đi tìm nguồn cội Phương tây, cúi"),
             rl(2, 1, "mong Đại hòa thượng rủ lòng, chấp nhận.")]
    cfg = CleanConfig(layer="nghia")
    audit = Audit(WORK, "nghia")
    kept, rows = filter_invalid_lines(lines, cfg, audit)
    assert rows == [] and len(kept) == 2                              # không bị nhầm là heading


def test_filtering_pure_heading_label():
    lines = [rl(1, 1, "CHÚ THÍCH :"), rl(2, 1, "nội dung thật.")]
    audit = Audit(WORK, "am")
    kept, rows = filter_invalid_lines(lines, CleanConfig(layer="am"), audit)
    assert [r[2] for r in rows] == ["heading"] and len(kept) == 1


# --------------------------------------------------------------------------- #
# 8. Tách câu (segmentation)
# --------------------------------------------------------------------------- #
def test_segmentation_vi_abbrev_and_numbers():
    cfg = CleanConfig(layer="nghia")
    text = "Ông ở TP. Hồ Chí Minh. Bà đến lúc 5 giờ; rồi đi. Năm 205 thì xong?"
    spans = split_vi(text, cfg)
    assert [text[a:b] for a, b in spans] == [
        "Ông ở TP. Hồ Chí Minh.", "Bà đến lúc 5 giờ;", "rồi đi.", "Năm 205 thì xong?"]


def test_segmentation_han_fullwidth_small_punct():
    text = "真形於世﹖真空說而無實耶﹖今正就尋西方根源。"
    spans = split_han(text)
    assert [text[a:b] for a, b in spans] == ["真形於世﹖", "真空說而無實耶﹖", "今正就尋西方根源。"]


def test_segmentation_keeps_source_metadata():
    lines = [RawLine("a", 1, "Câu một."), RawLine("b", 2, "Câu hai trải qua trang.")]
    sents, dropped = make_sentences(WORK, "nghia", lines, CleanConfig(layer="nghia"))
    assert [s.text for s in sents] == ["Câu một.", "Câu hai trải qua trang."]
    assert sents[0].src_ids == ["a"] and sents[0].pages == [1]
    assert sents[1].src_ids == ["b"] and sents[1].pages == [2]
    assert dropped == []


# --------------------------------------------------------------------------- #
# 9. Whole-pipeline trên dữ liệu giả (run_cleaning)
# --------------------------------------------------------------------------- #
def test_run_cleaning_end_to_end_synthetic():
    lines = [rl(1, 205, "SAUU TẦM"), rl(2, 205, "Trang 205"),
             rl(3, 205, "* Chú thích: theo Hoằng minh tập."),
             rl(4, 205, "Rằng, chỗ Đạo thanh hư, tứ đại ® lẽ thường. `"),
             rl(5, 205, "Nay đang lúc di tìm nguồn cội Phương tây, cúi"),
             rl(6, 206, "( Hoằng minh tập, Q. 11 ; Toàn Thương"),
             rl(7, 206, "cổ, Tam đại, Tôn, Hán, Q.57 )"),
             rl(8, 206, "mong Đại hòa thượng rủ lòng.")]
    cfg = CleanConfig(layer="nghia", start_after=r"^Trang 205", end_before=r"^mong Đại")
    out = run_cleaning(WORK, "nghia", lines, cfg)
    texts = [s.text for s in out.sentences]
    assert any("Rằng, chỗ Đạo thanh hư" in t for t in texts)          # nội dung chính được giữ
    assert not any("Hoằng minh tập" in t for t in texts)               # chú thích nguồn bị loại
    assert out.section.status == "ok" and out.section.n_selected == 5
    assert any(n.category == "bibliographic_note" for n in out.notes)  # note 2 dòng/qua trang
    assert any(r["reason"] == "uncertain" for r in out.audit.reviews)  # ` được trình review (® bị xoá)
    assert any(r[2].startswith("section_") for r in out.dropped_rows)
