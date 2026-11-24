import unicodedata

import pytest

from src.ocr.script_filter import (
    count_cjk, filter_han_line, filter_page, filter_vie_line, has_cjk,
)


# --------------------------------------------------------------------------- han
def test_han_pure_line_kept_unchanged():
    assert filter_han_line("初聞其語半疑信") == "初聞其語半疑信"


def test_han_keeps_cjk_punctuation_strips_latin_digits_and_spaces():
    assert filter_han_line("1. 諒山道中（二首）") == "諒山道中（二首）"
    assert filter_han_line("夫道處清虛，四大理常。 abc 12") == "夫道處清虛，四大理常。"
    assert filter_han_line("初 聞　其") == "初聞其"


def test_han_mixed_annotation_line_keeps_only_cjk():
    # ngoặc ASCII không phải dấu câu CJK nên bị cắt, chữ Hán trong chú thích được giữ
    assert filter_han_line("海口千家逐波開 (奇華海口門)") == "海口千家逐波開奇華海口門"
    assert filter_han_line("海口千家逐波開（奇華海口門）") == "海口千家逐波開（奇華海口門）"


@pytest.mark.parametrize("line", ["57", "18", "PHIÊN ÂM:", "NGÔ THÌ NHẬM TOÀN TẬP - TẬP III", "", "  ", "(1)"])
def test_han_drops_lines_without_cjk(line):
    assert filter_han_line(line) is None


def test_han_rare_blocks_count_as_cjk():
    assert has_cjk("\U00020000")                 # Extension B
    assert filter_han_line("x㐀y") == "㐀"


def test_han_nfc_maps_compatibility_ideograph():
    out = filter_han_line("豈")
    assert out == unicodedata.normalize("NFC", "豈") and has_cjk(out)


# --------------------------------------------------------------------------- vie
def test_vie_pure_line_untouched_and_diacritics_kept():
    line = "Nhị Lạng giang sơn cảnh giới liên,"
    assert filter_vie_line(line) == (line, 0)


def test_vie_nfc_normalizes_decomposed_diacritics():
    decomposed = unicodedata.normalize("NFD", "Phù Đạo xứ thanh hư")
    out, removed = filter_vie_line(decomposed)
    assert out == "Phù Đạo xứ thanh hư" and unicodedata.is_normalized("NFC", out)
    assert removed == 0


def test_vie_drops_pure_han_line():
    assert filter_vie_line("二諒江山境界連") == (None, 7)


def test_vie_mixed_line_removes_cjk_and_empty_brackets():
    out, removed = filter_vie_line("Hải khẩu thiên gia (奇華海口門)")
    assert out == "Hải khẩu thiên gia"
    assert removed == 5


def test_vie_fullwidth_brackets_and_punct_removed():
    out, removed = filter_vie_line("Lạng Sơn đạo trung （諒山道中）。")
    assert out == "Lạng Sơn đạo trung"
    assert removed == 7                          # 4 chữ + （ ） 。


def test_vie_orphan_and_empty_quotes_cleaned():
    assert filter_vie_line("chữ “奇華” là tên cửa")[0] == "chữ là tên cửa"
    assert filter_vie_line("Theo “弘明集” 「卷十一」, tr. 5")[0] == "Theo, tr. 5"
    assert filter_vie_line("sách 《全宋文》 \" ghi")[0] == "sách ghi"


def test_vie_keeps_quotes_attached_to_words():
    line = "“Kim chính tựu tầm tây phương"
    assert filter_vie_line(line) == (line, 0)


@pytest.mark.parametrize("line", ["57", "207", "  18  ", "(1)", "", "…"])
def test_vie_drops_lines_without_latin_letters(line):
    assert filter_vie_line(line)[0] is None


def test_vie_keeps_numbers_inside_text():
    assert filter_vie_line("Bài số 12 (二首)")[0] == "Bài số 12"


# --------------------------------------------------------------------------- page
def test_filter_page_han_records_source_line_numbers():
    lines = ["57", "初聞其語半疑信", "PHIÊN ÂM:", "發世懷囊如是哉 (1)"]
    res = filter_page("han", lines)
    assert res.kept == [(2, "初聞其語半疑信"), (4, "發世懷囊如是哉")]
    assert res.stats() == {"n_src": 4, "n_kept": 2, "n_dropped": 2, "cjk_removed": 0}


def test_filter_page_vie_never_leaks_cjk():
    lines = ["1. 諒山道中（二首）", "其一", "PHIÊN ÂM:", "LẠNG SƠN ĐẠO TRUNG (NHỊ THỦ)",
             "Nhị Lạng giang 江山 sơn cảnh giới liên,", "18", "« 弘明集 » quyển 11"]
    res = filter_page("vie", lines)
    assert [n for n, _ in res.kept] == [3, 4, 5, 7]
    assert all(count_cjk(t) == 0 for _, t in res.kept)
    assert res.kept[2][1] == "Nhị Lạng giang sơn cảnh giới liên,"
    assert res.kept[3][1] == "quyển 11"
    assert res.n_dropped == 3 and res.cjk_removed == 4 + 4 + 2 + 2 + 3


def test_filter_page_rejects_unknown_layer():
    with pytest.raises(ValueError):
        filter_page("am", ["x"])
