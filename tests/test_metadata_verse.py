"""Thơ Hán không chấm câu không được bị gom thành 'tiêu đề' metadata."""
from src.cleaning.metadata import classify_leading_block, verse_starts
from src.cleaning.model import MetadataConfig, RawLine

CPO20_TITLE = r"^\s*[一-鿿]{2,12}\s*$"
CPO19_TITLES = [r"^\s*\d{1,3}\s*[.．、﹒·。)]\s*[一-鿿]",
                r"^\s*(?:\d{1,3}\s*[·.．、﹒。)]\s*)?其[一二三四五六七八九十]+\s*$"]
POEM = ["春二月中恒望雨", "夜初更半忽聞雷", "九笏玉屑繽紛下", "四野花叢梧畨開"]


def lines(texts):
    return [RawLine(f"W.001.P0014.S{i:03d}", 14, t, raw_text=t, source_ids=[f"han.P0014.L{i:03d}"],
                    document_id="W.001") for i, t in enumerate(texts, 1)]


def run(texts, patterns):
    cfg = MetadataConfig(title_patterns=patterns, author_max_len=8)
    kept, doc = classify_leading_block(lines(texts), cfg, "han", "W.001")
    titles = [f.text for f in doc.fields if f.field == "title"]
    return [k.text for k in kept], titles


def test_unpunctuated_poem_is_body_not_title():
    kept, titles = run(["喜雨作", *POEM], [CPO20_TITLE])
    assert titles == ["喜雨作"]
    assert kept == POEM


def test_verse_starts_requires_same_length_neighbours():
    import re
    res = [re.compile(CPO20_TITLE)]
    assert verse_starts(lines(["喜雨作", *POEM]), res) == {1, 2, 3}
    assert verse_starts(lines(["喜雨作", "春二月中恒望雨。"]), res) == set()   # có dấu câu -> không khớp mẫu


def test_numbered_titles_unchanged():
    kept, titles = run(["1. 諒山道中（二首）", "其一", "二諒江山境界連", "旁通荆廣接高宣"], CPO19_TITLES)
    assert kept == ["二諒江山境界連", "旁通荆廣接高宣"]
    assert titles and "諒山道中" in titles[0]


def test_vietnamese_layer_not_affected():
    cfg = MetadataConfig(title_patterns=[r"^[A-ZÀ-Ỹ ]+$"])
    kept, doc = classify_leading_block(lines(["HỶ VŨ TÁC", "XUÂN NHỊ NGUYỆT", "Xuân nhị nguyệt trung hằng vọng vũ,"]),
                                       cfg, "am", "W.001")
    assert [k.text for k in kept] == ["Xuân nhị nguyệt trung hằng vọng vũ,"]


def test_verse_without_title_pattern_not_absorbed_by_metadata_heuristic():
    # bài mất tiêu đề (OCR đọc rác) bắt đầu thẳng bằng câu thơ; mẫu tiêu đề chỉ là dạng có số thứ tự
    verse = ["四方弧矢是爲男", "矯步天梯豈不堪", "得意看花春九十", "生明發步月初三"]
    kept, titles = run(verse, CPO19_TITLES)
    assert kept == verse and titles == []
