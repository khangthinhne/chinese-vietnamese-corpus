"""Các sửa sau audit segmentation 2026-11-26 (reports/segmentation_audit_20261126.md, mục 5)."""
from src.cleaning.model import RawLine
from src.segmentation import streams
from src.segmentation.layers import assign_document_unified
from src.segmentation.model import Document, OcrLine
from src.segmentation.sentences import SentenceConfig, make_sentences

CFG = {"han": {"start_patterns": [r"^\s*[一-鿿]{2,12}\s*$"]},
       "am": {"heading_mode": True, "start_patterns": [r"^PHI[ÊE]N\s+ÂM"],
              "attribution_patterns": [r"^\s*(?:Trích|Theo)\b"]},
       "nghia": {"heading_mode": True, "start_patterns": [r"^DỊCH\s+NGHĨA"]},
       "tho": {"heading_mode": True, "optional": True, "start_patterns": [r"^DỊCH\s+THƠ"]}}


def line(stream, page, idx, text, y):
    return OcrLine(page=page, index=idx, text=text, bbox=(0, y, 100, y + 10), writing_system=stream,
                   source_id=f"{stream}.P{page:04d}.L{idx:03d}")


def assign(texts, **kw):
    doc = Document(id="W.001", pages=[1], lines=[line(s, 1, i, t, 100 * i) for i, (s, t) in enumerate(texts, 1)])
    merged = doc.lines
    return assign_document_unified("W", doc, CFG, {}, keep_line=streams.keep_line, dropped={},
                                   implicit_next={"han": "am"}, title_next={"am": "nghia"},
                                   is_header=streams.header_predicate(merged), **kw)


def test_noise_vie_lines():
    for t in ("E", "**N**", "Điều", "of contradiction!", "Vandana =", "Stone", "cuer"):
        assert streams.is_noise_vie(t), t
    for t in ("Kỳ nhị", "Nhị Lạng giang sơn cảnh giới liên,", "Ung dung muon hoc Chu khanh si,",
              "LAP NGUYET THAP THAT NHAT HOI"):
        assert not streams.is_noise_vie(t), t


def test_noise_between_han_blocks_does_not_open_document():
    # CPO_18 tr.234-235: chữ ký + dòng nguồn của bức thư sang trang, ngăn bởi dòng rác "Điều"
    merged = [line("han", 234, 1, "來論雖美故自循環之說耳", 2600), line("han", 234, 2, "深願大和尚垂納毫款", 2700),
              line("vie", 235, 1, "Điều", 50), line("han", 235, 1, "弟子李淼謹呈", 120),
              line("han", 235, 2, "（弘明集卷十一）", 200), line("vie", 235, 2, "PHIÊN ÂM", 400)]
    assert streams.allowed_document_starts(merged) == {0}


def test_explicit_heading_resets_body_so_title_after_it_does_not_open_nghia():
    # CPO_18.005: rác Việt + tiêu đề chạy ngầm mở am, rồi "PHIÊN ÂM:" thật, rồi tiêu đề bài viết hoa
    texts = [("han", "釋道高白重奉深诲"), ("vie", "of contradiction!"), ("vie", "PHIÊN ÂM:"),
             ("vie", "TRÙNG ĐÁP LÝ GIAO CHÂU THƯ"), ("vie", "Thích Đạo Cao bạch: Trùng phụng thâm hối,")]
    res = assign(texts)
    assert [r.text for r in res.lines["am"]][-1] == "Thích Đạo Cao bạch: Trùng phụng thâm hối,"
    assert "nghia" not in res.lines


def test_noise_vie_does_not_implicitly_start_am_and_han_lines_survive():
    # CPO_18 tr.285-286: "**N**" từng ngầm mở am, 17 câu Hán sau đó bị loại "khác luồng"
    texts = [("han", "石繙此芳猷義之重宣"), ("vie", "**N**"), ("han", "黎皇靈鬱"), ("han", "四海攸同")]
    res = assign(texts)
    assert [r.text for r in res.lines["han"]] == ["石繙此芳猷義之重宣", "黎皇靈鬱", "四海攸同"]


def test_dropped_cross_stream_lines_are_reviewed():
    texts = [("han", "諒山道中"), ("vie", "PHIÊN ÂM:"), ("vie", "Nhị Lạng giang sơn cảnh giới liên,"),
             ("han", "旁")]
    res = assign(texts)
    dropped = [r for r in res.review if r["reason"] == "dropped_cross_stream"]
    assert [(r["layer"], r["text"]) for r in dropped] == [("am", "旁")]
