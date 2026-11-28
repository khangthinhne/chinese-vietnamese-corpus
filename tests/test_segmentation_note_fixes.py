"""Vòng sửa lớp nghĩa/thơ 2026-11-28: chú thích "(65)" và chú thích không số, KHẢO DỊ đóng mọi lớp Việt,
tiêu đề dịch viết hoa dài, điểm cắt nghia/tho."""
import pytest

from src.segmentation import streams
from src.segmentation.am_detect import verse_tho_split
from src.segmentation.layers import (NOTE_ENTRY_RE, assign_document_unified, fold_latin, is_upper_title,
                                     unnumbered_note_term)
from src.segmentation.model import Document, OcrLine

CFG = {"han": {"start_patterns": [r"^\s*[一-鿿]{2,12}\s*$"]},
       "am": {"heading_mode": True, "start_patterns": [r"^PHI[ÊE]N\s+ÂM"], "end_patterns": [r"^DỊCH\s+NGHĨA"]},
       "nghia": {"heading_mode": True, "start_patterns": [r"^DỊCH\s+NGHĨA"]},
       "tho": {"heading_mode": True, "optional": True, "start_patterns": [r"^DỊCH\s+THƠ"]}}


def line(stream, page, idx, text):
    return OcrLine(page=page, index=idx, text=text, bbox=(0, 100 * idx, 100, 100 * idx + 10), writing_system=stream,
                   source_id=f"{stream}.P{page:04d}.L{idx:03d}")


def assign(rows, **kw):
    lines = [line(s, p, i, t) for i, (s, p, t) in enumerate(rows, 1)]
    doc = Document(id="W.001", pages=sorted({p for _, p, _ in rows}), lines=lines)
    return assign_document_unified("W", doc, CFG, {}, keep_line=streams.keep_line, dropped={},
                                   implicit_next={"han": "am"}, title_next={"am": "nghia"},
                                   is_header=streams.header_predicate(lines), **kw)


@pytest.mark.parametrize("text", ["(65) Dư Nhượng: người nước Tấn.", "( 3 ) Sứ quân: tiếng gọi.",
                                  "1. Nguyên chủ: Tư Đường.", "193) Đạo kinh: tức Đạo đức kinh."])
def test_note_entry_re_accepts_parenthesized_and_three_digit_numbers(text):
    assert NOTE_ENTRY_RE.match(text)


@pytest.mark.parametrize("text", ["(1979) là năm xuất bản.", "Nhị Lạng giang sơn cảnh giới liên,"])
def test_note_entry_re_rejects_non_notes(text):
    assert not NOTE_ENTRY_RE.match(text)


def test_unnumbered_note_term_requires_term_seen_in_text():
    seen = fold_latin("Lưỡng triều quyến luyến nan thù đáp. Đinh hồ long khứ vân phiêu đãng.")
    assert unnumbered_note_term("Lương triều: Hai triều; chỉ triều vua Quang Trung.", fold_latin("Lương triều")) \
        == "Lương triều"
    assert unnumbered_note_term("Đinh Hồ long khứ: Vua Hoàng Đế cưỡi rồng.", seen) == "Đinh Hồ long khứ"
    assert unnumbered_note_term("Núi Kỳ: Nơi phát tích nhà Chu.", seen) is None          # thuật ngữ chưa xuất hiện
    assert unnumbered_note_term("Đáp: Thành quỷ thần.", fold_latin("đáp")) is None         # 1 từ
    assert unnumbered_note_term("Ông nói rằng: hãy về.", fold_latin("ông nói rằng")) is None   # lời dẫn
    long_term = "Kính thấy Tước các đốc bộ đường đại nhân đài hạ: Trung hiền làm đức."
    assert unnumbered_note_term(long_term, fold_latin(long_term)) is None                # > 6 từ


def test_unnumbered_notes_end_nghia_until_page_break():
    rows = [("han", 1, "兩朝"), ("vie", 1, "PHIÊN ÂM:"), ("vie", 1, "Lưỡng triều quyến luyến nan thù đáp,"),
            ("vie", 1, "DỊCH NGHĨA:"), ("vie", 1, "Tình quyến luyến hai triều chất chứa, khó bề báo đáp,"),
            ("vie", 1, "Lưỡng triều: Hai triều; chỉ triều vua Quang Trung và Quang Toản."),
            ("vie", 1, "Kim liên chúc: Đuốc sen vàng."), ("vie", 2, "Ánh đuốc sen vàng mấy độ chiếu về.")]
    res = assign(rows, note_entry_ends={"am", "nghia", "tho"})
    assert [r.text for r in res.lines["nghia"]] == ["Tình quyến luyến hai triều chất chứa, khó bề báo đáp,",
                                                     "Ánh đuốc sen vàng mấy độ chiếu về."]
    assert [r["text"] for r in res.review if r["reason"] == "unnumbered_note_entry"] == [rows[5][2]]
    off = assign(rows)
    assert len(off.lines["nghia"]) == 4                               # tắt note_entry_ends: giữ như cũ


def test_note_heading_closes_any_open_vietnamese_layer():
    # CPO_19 tr.79-80: mất "DỊCH NGHĨA" nên vẫn ở am; KHẢO DỊ (end của nghia) trước đây không đóng am
    cfg = {**CFG, "nghia": {**CFG["nghia"], "end_patterns": [r"^KH[ẢA]O\s+D[ỊI]"]}}
    rows = [("han", 1, "潯州西上"), ("vie", 1, "PHIÊN ÂM:"), ("vie", 1, "Tầm Châu tây thượng chỉ tây thiên,"),
            ("vie", 1, "Phía tây Tầm Châu chệch hướng tây,"), ("vie", 1, "KHẢO DỊ:"),
            ("vie", 1, "a. Bản A. 1579: Tầm giang."), ("vie", 1, "NGUYÊN CHÚ:"), ("vie", 1, "A. Cố đô của Triệu Uý Đà.")]
    lines = [line(s, p, i, t) for i, (s, p, t) in enumerate(rows, 1)]
    doc = Document(id="W.001", pages=[1], lines=lines)
    res = assign_document_unified("W", doc, cfg, {}, keep_line=streams.keep_line, dropped={},
                                  implicit_next={"han": "am"}, title_next={"am": "nghia"},
                                  is_header=streams.header_predicate(lines))
    assert [r.text for r in res.lines["am"]] == ["Tầm Châu tây thượng chỉ tây thiên,", "Phía tây Tầm Châu chệch hướng tây,"]
    assert any(r["reason"] == "note_heading_closed_layer" for r in res.review)
    assert "nghia" not in res.lines


def test_long_upper_title_opens_nghia():
    title = "HỌA TIỂN QUÁN THỊ LÀNG BỘ HÌNH LÀ THANH PHÁI HẦU HỌ LÊ ĐI NHẬM CHỨC Ở HÒA NGHĨA"
    assert is_upper_title(title)                                       # 19 từ (CPO_20.022)
    assert not is_upper_title("Ba năm ở tòa cơ mật, được ơn mưa móc dồi dào,")
    rows = [("han", 1, "樞省三年"), ("vie", 1, "Xu sảnh tam niên hạ ốc đàm,"), ("vie", 1, title),
            ("vie", 1, "Ba năm ở tòa cơ mật, được ơn mưa móc dồi dào,")]
    res = assign(rows)
    assert [r.text for r in res.lines["nghia"]] == [title, "Ba năm ở tòa cơ mật, được ơn mưa móc dồi dào,"]


def test_verse_tho_split_backtracks_when_one_nghia_line_holds_two_couplets():
    am = [f"Câu phiên âm số {x} bảy chữ," for x in "abcdefgh"]
    # 7 dòng nghĩa cho 8 câu (dòng 6 gộp hai câu), rồi 8 dòng dịch thơ 7 chữ (CPO_20 tr.42)
    nghia = [f"Câu dịch nghĩa thứ {x} của bài thơ dài hơn một chút," for x in "abcde"] + [
        "Câu dịch nghĩa gộp hai câu, dài hơn hẳn các câu khác của bài thơ này,",
        "Câu cuối dịch nghĩa rất dài hơn chút,"] + [
             "Uyên bay thuở ấy vẫn còn đau,", "Rồng lại đi xa mất dịp hầu!", "Việc nước tình nhà thêm bối rối;",
             "Đêm trăng sầm gió xiết âu sầu!", "Non sông muôn dặm vẫn còn đây,", "Bóng nhạn chiều hôm lẻ một bầy.",
             "Ngoảnh lại kinh thành mây khói phủ,", "Lòng thương nhớ mãi chẳng hề khuây."]
    # không lùi thì điểm cắt = nb[8] = 8 ("Rồng lại ..."), câu thơ đầu ở lại nghia; dòng 6 (8 âm tiết) không lùi tiếp
    assert verse_tho_split(am, nghia) == 7


def test_repeated_phrase_in_nghia_is_not_a_note():
    # bài hành CPO_19: "Ngươi chẳng thấy: ..." lặp lại trong lớp nghĩa; thuật ngữ không có trong âm -> không cắt trang
    rows = [("han", 1, "君不見"), ("vie", 1, "PHIÊN ÂM:"), ("vie", 1, "Quân bất kiến Ngũ Lĩnh vân,"),
            ("vie", 1, "DỊCH NGHĨA:"), ("vie", 1, "Ngươi chẳng thấy: Mây ngoài Ngũ Lĩnh sớm chiều bay?"),
            ("vie", 1, "Ngươi chẳng thấy: Nước sông Minh cuồn cuộn chảy mãi?"), ("vie", 1, "Câu cuối của bài.")]
    res = assign(rows, note_entry_ends={"am", "nghia", "tho"})
    assert len(res.lines["nghia"]) == 3


def test_khao_di_entries_without_heading_end_layer_until_page_break():
    rows = [("han", 1, "使軺"), ("vie", 1, "PHIÊN ÂM:"), ("vie", 1, "Sứ diêu sạ chí cộng tương chỉ,"),
            ("vie", 1, "DỊCH NGHĨA:"), ("vie", 1, "Vui vẻ gật đầu chào, tiếng nói khó nghe."),
            ("vie", 1, "a. Bản A.117c/1: Nguyên cương."), ("vie", 1, "Bản A.1579: Động nguyên."),
            ("vie", 2, "Câu nghĩa trang sau.")]
    res = assign(rows)                                            # không cần note_entry_ends
    assert [r.text for r in res.lines["nghia"]] == ["Vui vẻ gật đầu chào, tiếng nói khó nghe.", "Câu nghĩa trang sau."]
    assert sum(r["reason"] == "khao_di_entry_without_heading" for r in res.review) == 1
