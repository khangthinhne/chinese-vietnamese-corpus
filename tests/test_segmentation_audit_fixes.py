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


def test_note_entry_ends_vietnamese_layer_only_when_enabled():
    texts = [("han", "夢遊文灘次思堂"), ("vie", "PHIÊN ÂM:"), ("vie", "Nguyệt tiền ngô diệc tác tư du,"),
             ("vie", "1. Nguyên chủ: Tư Đường là ông Hoàng giáp ở Bảo Triện."),
             ("vie", "2. Cửu châu: Chín châu.")]
    off = assign(texts)
    assert len(off.lines["am"]) == 3
    on = assign(texts, note_entry_ends={"am", "nghia", "tho"})
    assert [r.text for r in on.lines["am"]] == ["Nguyệt tiền ngô diệc tác tư du,"]
    assert sum(r["reason"] == "unassigned_line" for r in on.review) == 2


def test_am_can_follow_han_split_mode():
    am = [RawLine(f"W.001.P0001.L{k:03d}", 1, t) for k, t in enumerate(
        ["Cao sơn lưu thủy hữu tri âm,", "Lương địa thần giao ký ý thâm.",
         "Danh mạo khởi tu bằng vấn tấn,", "Phong tình quân dị ngụ ca ngâm."], 1)]
    cfg = SentenceConfig(layer="am", split="auto")
    auto, _ = make_sentences("W", "am", am, cfg)
    assert len(auto) == 4                                         # văn vần -> theo dòng
    forced, _ = make_sentences("W", "am", am, cfg, forced_modes={"W.001": "punct"})
    assert len(forced) == 2                                       # theo lớp han (tách 。) -> theo dấu câu


def test_note_entries_end_layer_only_until_page_break():
    rows = [("han", 1, "夢遊文灘次思堂"), ("vie", 1, "PHIÊN ÂM:"), ("vie", 1, "Nguyệt tiền ngô diệc tác tư du,"),
            ("vie", 1, "1. Nguyên chủ: Tư Đường là ông Hoàng giáp."), ("vie", 2, "Bồng trục thương doanh nhất diệp phù."),
            ("vie", 2, "DỊCH NGHĨA:"), ("vie", 2, "Trước trăng ta cũng làm cuộc chơi ấy,")]
    doc = Document(id="W.001", pages=[1, 2], lines=[line(s, p, i, t, 100 * i) for i, (s, p, t) in enumerate(rows, 1)])
    res = assign_document_unified("W", doc, CFG, {}, keep_line=streams.keep_line, dropped={},
                                  implicit_next={"han": "am"}, title_next={"am": "nghia"},
                                  is_header=streams.header_predicate(doc.lines), note_entry_ends={"am", "nghia"})
    assert [r.text for r in res.lines["am"]] == ["Nguyệt tiền ngô diệc tác tư du,", "Bồng trục thương doanh nhất diệp phù."]
    assert [r.text for r in res.lines["nghia"]] == ["Trước trăng ta cũng làm cuộc chơi ấy,"]


def test_fuzzy_section_headings():
    from src.segmentation.layers import fuzzy_note_heading, fuzzy_section_layer
    layers = ["am", "nghia", "tho"]
    for text, layer in (("DỊCH THOẠI:", "tho"), ("DỊCH NHĨA:", "nghia"), ("Phần âm:", "am"), ("Dịch phú:", "tho"),
                        ("PHIÊN ÂM", "am")):
        assert fuzzy_section_layer(text, layers) == layer, text
    for text in ("Đích thị", "Dịch thơ hay quá:", "DỊCH :", "LÀO"):
        assert fuzzy_section_layer(text, layers) is None, text
    assert fuzzy_note_heading("CHÚ TÍCH:") and not fuzzy_note_heading("Chú ý:")


def test_fuzzy_heading_opens_tho_layer():
    texts = [("han", "諒山道中"), ("vie", "PHIÊN ÂM:"), ("vie", "Nhị Lạng giang sơn cảnh giới liên,"),
             ("vie", "DỊCH NGHĨA:"), ("vie", "Núi sông xứ Lạng liền nhau,"), ("vie", "DỊCH THOẠI:"),
             ("vie", "Núi sông xứ Lạng kế liền nhau,")]
    res = assign(texts)
    assert [r.text for r in res.lines["tho"]] == ["Núi sông xứ Lạng kế liền nhau,"]
    assert any(r["reason"] == "fuzzy_section_heading" for r in res.review)


def test_am_digit_markers_removed_only_in_am_layer():
    from src.cleaning.artifacts import clean_ocr_artifacts
    from src.cleaning.model import OCRCleaningConfig
    cfg = OCRCleaningConfig()
    res = clean_ocr_artifacts("Cao sơn lưu thủy 2 hữu tri âm,", cfg, layer="am")
    assert res.text == "Cao sơn lưu thủy hữu tri âm," and ("am_digit_marker", "2") in res.removed
    assert clean_ocr_artifacts("Ngày 12 tháng 12 năm ấy", cfg, layer="nghia").text == "Ngày 12 tháng 12 năm ấy"
    assert clean_ocr_artifacts("trạo sơ 1, năm 1790", cfg, layer="am").text == "trạo sơ 1, năm 1790"


def test_short_translator_lines_go_to_metadata():
    from src.cleaning.metadata import is_translator_line
    for text, layer in (("Đỗ Ngọc Toại - Thạch Can dịch", "tho"), ("Dương Minh và Thạch Can dịch", "nghia"),
                        ("Nguyen Van Bach dich", "tho"), ("Thạch Can dịch thơ", "tho"), ("Ngô Linh Ngọc dịch", "am")):
        assert is_translator_line(text, layer), text
    for text, layer in (("Hình bất vì tâm dịch.", "am"), ("Hình bất vì tâm dịch.", "tho"),
                        ("Từng trải nắng mưa đường trạm dịch,", "nghia"), ("Lời này khó dịch", "nghia")):
        assert not is_translator_line(text, layer), text


def test_han_title_keeps_one_line_plus_parenthesized_subtitle():
    from src.cleaning.metadata import classify_leading_block
    from src.cleaning.model import MetadataConfig
    cfg = MetadataConfig(enabled=True, title_patterns=[r"^\s*[一-鿿]{2,12}\s*$"])
    lines = [RawLine(f"W.001.P0268.L{k:03d}", 268, t, raw_text=t) for k, t in enumerate(
        ["又安天氣和暢", "有二熟十一月立", "熟然冬夏之間", "八九月間天陰雨。東北風"], 1)]
    kept, md = classify_leading_block(lines, cfg, "han", "W.001")
    titles = [f for f in md.fields if f.field == "title"]
    assert [f.text for f in titles] == ["又安天氣和暢"]
    assert [ln.text for ln in kept] == ["有二熟十一月立", "熟然冬夏之間", "八九月間天陰雨。東北風"]
    assert [ln.text for ln, _ in md.restored] == ["有二熟十一月立", "熟然冬夏之間"]
    sub = [RawLine("W.002.P0001.L001", 1, "呈福大人", raw_text="呈福大人"),
           RawLine("W.002.P0001.L002", 1, "（國王書呈告）", raw_text="（國王書呈告）"),
           RawLine("W.002.P0001.L003", 1, "茲欽奉御頒金印誥敕。", raw_text="茲欽奉御頒金印誥敕。")]
    cfg2 = MetadataConfig(enabled=True, title_patterns=[r"^\s*[一-鿿（）]{2,12}\s*$"])
    kept2, md2 = classify_leading_block(sub, cfg2, "han", "W.002")
    assert [f.text for f in md2.fields if f.field == "title"] == ["呈福大人（國王書呈告）"]


def test_han_title_suspect_by_length():
    from src.segmentation.layers import han_title_suspect
    rows = [{"layer": "han", "fields": [{"field": "title", "text": "广一伯", "pages": [515]}]},
            {"layer": "am", "fields": [{"field": "title", "text": "TRÌNH THIÊN TRIỀU SẮC SỨ NHỊ VỊ ĐẠI NHÂN"}]}]
    assert han_title_suspect(rows)["pages"] == [515]
    rows[0]["fields"][0]["text"] = "呈天朝敕使二位大人"
    assert han_title_suspect(rows) is None


def test_reading_match_separates_phien_am_from_translation():
    from src.segmentation.am_detect import fold, reading_match
    readings = {"夕": "tịch", "照": "chiếu", "流": "lưu", "霞": "hà", "萬": "vạn", "頃": "khoảnh", "丹": "đan",
                "漁": "ngư", "舟": "chu", "次": "thứ", "第": "đệ", "望": "vọng", "回": "hồi", "灘": "than"}
    rd = {k: frozenset({fold(v)}) for k, v in readings.items()}
    han = ["夕照流霞萬頃丹", "漁舟次第望回灘"]
    assert reading_match(han, ["Tịch chiếu lưu hà vạn khoảnh đan,", "Ngư chu thứ đệ vọng hồi than."], rd) == 1.0
    assert reading_match(han, ["Mặt trời chiều chiếu trên đám mây bay", "Thuyền đánh cá lần lượt trở về bến,"], rd) < 0.35
    assert reading_match(["夕照"], ["Tịch chiếu"], rd) is None


def test_am_detector_moves_implicit_translation_to_nghia_and_honors_overrides():
    from src.segmentation.am_detect import AmDetector
    det = AmDetector({"enabled": True, "min_match": 0.35, "nghia_only_pages": [5], "am_pages": [7]})
    assert det.decide(5, [], [])[0] is True and det.decide(7, ["一"] * 10, ["xyz"] * 10)[0] is False
    texts = [("han", "天氣和暢雖六陰節候盛陽常自如稼穡"), ("vie", "Xứ ấy trời đất ấm áp, mùa đông không lạnh lắm,"),
             ("vie", "mùa hè cũng dễ chịu, quanh năm cây cối tốt tươi.")]
    det.path = "resources/han_viet.tsv"
    res = assign(texts, am_detector=det)
    assert "am" not in res.lines and len(res.lines["nghia"]) == 2
    assert any(r["reason"] == "am_reassigned_to_nghia" for r in res.review)
    off = assign(texts, am_detector=AmDetector({}))
    assert len(off.lines["am"]) == 2


def test_reading_match_ignores_note_lines_before_phien_am():
    from src.segmentation.am_detect import fold, reading_match
    rd = {k: frozenset({fold(v)}) for k, v in {"扇": "phiến", "值": "trị", "清": "thanh", "秋": "thu", "盡": "tận",
                                                "日": "nhật", "閒": "nhàn"}.items()}
    notes = ['1. Nguyên bản chép "trú nhật gian", không có nghĩa. Ngờ là chép nhầm chữ nhàn ' * 3,
             "2. Nguyên bản chép chữ ánh không hợp nghĩa."]
    assert reading_match(["扇值清秋盡日閒"], notes + ["ĐỀ THU PHIẾN", "Phiến trị thanh thu tận nhật nhàn,"], rd) == 1.0


def test_single_numbered_han_title_line_opens_next_document():
    merged = [line("han", 434, 1, "實由力不從心。", 100), line("han", 434, 2, "敬祈垂鑒。", 200),
              line("vie", 434, 1, "PHIÊN ÂM:", 300), line("vie", 434, 2, "Thực do lực bất tòng tâm.", 400),
              line("han", 435, 1, "10. 又一道", 2900), line("vie", 436, 1, "Lại một đạo thư nữa gửi đi.", 100),
              line("han", 436, 1, "茲者恭奉", 300), line("vie", 436, 2, "Nay kính vâng mệnh.", 400),
              line("han", 436, 2, "論旨", 500)]
    starts = streams.allowed_document_starts(merged)
    assert 4 in starts                                   # "10. 又一道" mở bài mới dù khối chỉ 1 dòng
    assert 6 not in starts and 8 not in starts           # chữ Hán lẻ trong phần Việt vẫn không mở bài


def test_repeated_numbered_title_is_not_a_running_header():
    merged = []
    for k, page in enumerate((100, 200, 300, 435)):         # "又一道" là tên nhiều bài -> lặp >= 3 trang
        merged += [line("vie", page, 1, ["Mùa xuân năm ấy", "Gió thu lồng lộng", "Trăng soi bến cũ",
                                         "Nước chảy về đông"][k], 100),
                   line("han", page, 1, f"{k + 7}. 又一道", 2900),
                   line("han", page, 2, "（覆稽典禮。留守官稟）", 3000), line("han", page, 3, "前奉札飭本國查照册封典禮", 3100)]
    starts = streams.allowed_document_starts(merged)
    assert {i for i, ln in enumerate(merged) if ln.text.endswith("又一道")} <= starts


def test_am_detector_moves_only_implicit_am_not_explicit_phien_am():
    """CPO_20.053: tựa dịch nghĩa mở ngầm am, sau đó 'PHIÊN ÂM:' tường minh (chiếu Nôm) phải ở lại am."""
    from src.segmentation.am_detect import AmDetector
    det = AmDetector({"enabled": True, "min_match": 0.35})
    det.path = "resources/han_viet.tsv"
    texts = [("han", "天氣和暢雖六陰節候盛陽常自如稼穡"), ("vie", "Xứ ấy trời đất ấm áp, mùa đông không lạnh lắm,"),
             ("vie", "mùa hè cũng dễ chịu, quanh năm cây cối tốt tươi."), ("vie", "PHIÊN ÂM:"),
             ("vie", "Chiếu Thiếu phó Diệu quận công khâm tri."), ("vie", "Nhị khanh là huân cựu đại thần.")]
    res = assign(texts, am_detector=det)
    assert [x.text for x in res.lines["am"]] == ["Chiếu Thiếu phó Diệu quận công khâm tri.",
                                                 "Nhị khanh là huân cựu đại thần."]
    assert len(res.lines["nghia"]) == 2
