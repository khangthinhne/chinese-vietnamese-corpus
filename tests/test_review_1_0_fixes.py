"""Sửa theo duyệt tay câu Hán 1-0 của han-nghia (2026-11-28): vùng tiêu đề Hán theo bbox, tiêu đề chạy chỉ ở mép
trang, override `continue` (chữ Hán trích trong khảo dị không mở tài liệu mới)."""
import pytest

from src.cleaning.metadata import classify_leading_block
from src.cleaning.model import MetadataConfig, RawLine
from src.segmentation import streams
from src.segmentation.layers import han_title_zone
from src.segmentation.model import LayerLine, OcrLine
from src.segmentation.overrides import overrides_for, parse_overrides, suppressed_starts


def row(i, top, height, page=20, text="字"):
    return LayerLine(id=f"D.P{page:04d}.S{i:03d}", document_id="D", layer="han", page=page, text=text,
                     bbox=(500, top, 1500, top + height))


def body(start, n=8, top=2500, page=20):
    return [row(start + k, top + 140 * k, 60, page) for k in range(n)]


def test_title_zone_split_title_line():
    # "4. 過關留贈潘御史" đọc thành dòng thường + dòng mỏng chồng mép; dòng 2 tiêu đề cũng bị xẻ đôi (CPO_19 tr.20)
    rows = [row(1, 2167, 53), row(2, 2212, 29), row(3, 2278, 40), row(4, 2310, 42)] + body(5)
    assert han_title_zone(rows) == [0, 1, 2, 3]


def test_title_zone_ghost_before_numbered_title():
    rows = [row(1, 733, 31), row(2, 756, 57)] + body(3, top=911)      # CPO_19 tr.34: rác mỏng rồi "9.書示伴送李憲喬"
    assert han_title_zone(rows) == [0, 1]


def test_title_zone_tall_title():
    rows = [row(1, 233, 79)] + body(2, top=410)                       # CPO_20 tr.147 "望銅柱感懷" chữ to
    assert han_title_zone(rows) == [0]


def test_title_zone_none_for_plain_body():
    rows = body(1, n=10)
    assert han_title_zone(rows) == []


def test_title_zone_stops_at_body_and_needs_rows():
    rows = [row(1, 100, 30)] + body(2, top=300)
    rows.insert(5, row(99, 2000, 30))                                 # dòng mỏng giữa thân bài: không tính
    assert han_title_zone(rows) == [0]
    assert han_title_zone(rows[:3]) == []                             # quá ít dòng để có trung vị


def _raw(i, text):
    return RawLine(id=f"D.P0020.S{i:03d}", page=20, text=text, raw_text=text)


CFG19 = MetadataConfig(title_patterns=[r"^\s*\d{1,3}\s*[.．、﹒·。)]\s*[一-鿿]"], author_max_len=4)


def test_forced_title_lines_not_restored_to_body():
    lines = [_raw(1, "0每一座以木字兵"), _raw(2, "9.書下伴送李憲喬"), _raw(3, "太平景物十分妍"), _raw(4, "相送行人北上燕")]
    forced = frozenset({lines[0].id, lines[1].id})
    kept, md = classify_leading_block(lines, CFG19, "han", "D", forced)
    assert [x.text for x in kept] == ["太平景物十分妍", "相送行人北上燕"]
    title = [f for f in md.fields if f.field == "title"]
    assert title and title[0].text == "0每一座以木字兵9.書下伴送李憲喬"


def test_forced_title_beats_verse_check():
    cfg = MetadataConfig(title_patterns=[r"^\s*[一-鿿]{2,12}\s*$"], author_max_len=8)
    lines = [_raw(1, "望銅柱感懷"), _raw(2, "晓出明州城"), _raw(3, "尋訪銅柱跡"), _raw(4, "土人遥指點")]
    kept, _ = classify_leading_block(lines, cfg, "han", "D")
    assert kept[0].text == "望銅柱感懷"                               # 5 chữ như câu thơ: bị coi là thân bài
    kept, _ = classify_leading_block(lines, cfg, "han", "D", frozenset({lines[0].id}))
    assert [x.text for x in kept] == ["晓出明州城", "尋訪銅柱跡", "土人遥指點"]


def ocr(page, idx, text, ws="vie"):
    return OcrLine(page=page, index=idx, text=text, writing_system=ws, source_id=f"{ws}.P{page:04d}.L{idx:03d}")


def test_running_header_only_at_page_edge():
    lines = []
    for p in range(1, 5):
        lines += [ocr(p, 1, "BANG GIAO HẢO THOẠI"), ocr(p, 2, "nội dung một"), ocr(p, 3, "LẠI MỘT ĐẠO(1)"),
                  ocr(p, 4, "nội dung hai"), ocr(p, 5, "nội dung ba"), ocr(p, 6, "nội dung bốn")]
    is_header = streams.header_predicate(lines, edge_only=True)
    assert is_header(lines[0])                                        # tiêu đề chạy đầu trang
    assert not is_header(lines[2])                                    # lặp nhiều trang nhưng ở giữa trang
    assert streams.header_predicate(lines)(lines[2])                 # mặc định (CPO_18/20): vẫn là tiêu đề chạy
    assert is_header(ocr(9, 1, "ab"))                                 # dòng Việt nhiễu vẫn là header


def test_continue_override_requires_prefix_and_suppresses_start():
    with pytest.raises(ValueError):
        parse_overrides([{"start_page": 358, "action": "continue", "reason": "x"}])
    ovs = parse_overrides([{"start_page": 358, "action": "continue", "han_prefix": "内見", "reason": "khảo dị"},
                           {"start_page": 337, "action": "exclude", "reason": "rác"}])
    lines = [ocr(357, 1, "題旅櫬幷記", "han"), ocr(358, 1, "KHẢO DỊ:"), ocr(358, 2, "内見一貧病兒郎.", "han"),
             ocr(358, 3, "擬求救瘴", "han"), ocr(359, 1, "廖有方", "han")]
    suppressed, used = suppressed_starts(lines, {0, 2, 4}, ovs)
    assert suppressed == {2} and [o.han_prefix for o in used] == ["内見"]
    assert overrides_for(358, "内見一貧病兒郎.", ovs) == []           # continue không áp lên lớp của tài liệu
    assert [o.action for o in overrides_for(337, "", ovs)] == ["exclude"]
