import unicodedata

import pytest

from src.ocr.script_filter import (
    count_cjk, english_reject_reason, filter_han_line, filter_page, filter_vie_line, han_reject_reason, has_cjk,
    vie_reject_reason,
)


# --------------------------------------------------------------------------- han
def test_han_pure_line_kept_unchanged():
    assert filter_han_line("初聞其語半疑信") == "初聞其語半疑信"


def test_han_keeps_cjk_punctuation_strips_latin_digits_and_spaces():
    assert filter_han_line("1. 諒山道中（二首）") == "1. 諒山道中（二首）"      # số thứ tự tiêu đề được giữ
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
    assert res.stats() == {"n_src": 4, "n_kept": 2, "n_dropped": 2, "cjk_removed": 0, "n_rejected": 0}


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


# --------------------------------------------------------------------------- dòng Hán giả
@pytest.mark.parametrize("line", [
    "a. Bản A.404: Hội khê 洄溪.",                              # khảo dị tiếng Việt trích chữ Hán
    "d. Bản A.1579: Phục trung tín 伏忠信.",
    "Khoa chép là “Vô thuật tà 無術邪”.",
    "c. Bản A.404: Phiêu 飘.",
    "J. 一",                                                    # paddlevl đọc nhầm "5. 其二"
    "山 [r]",
    "nam\". Van Lama is a people that are波杰丹答时",
])
def test_han_rejects_vietnamese_annotation_and_garbage(line):
    assert han_reject_reason(line) is not None
    res = filter_page("han", [line])
    assert res.kept == [] and len(res.rejected) == 1 and res.rejected[0][2] == line


@pytest.mark.parametrize("line", [
    "二諒江山境界連", "2. 其二", "6. 受降城", "1. 與高明二法師難佛不見形書", "謹白。", "喜雨作",
    r"\(^{*}\)森爲交州刺史", "海口千家逐波開 (奇華海口門)", "玄",
])
def test_han_keeps_real_han_lines(line):
    assert han_reject_reason(line) is None
    assert filter_page("han", [line]).n_kept == 1


def test_han_reject_thresholds_are_configurable():
    line = "a. Bản A.404: Hội khê 洄溪."
    assert filter_page("han", [line], han_min_cjk_share=0.0, han_min_cjk_with_latin=0).n_kept == 1
    assert han_reject_reason("Kỳ 其二", min_cjk_share=0.3, min_cjk_with_latin=3).startswith("few_cjk")
    assert filter_page("han", [line]).stats()["n_rejected"] == 1


# --------------------------------------------------------------------------- vie: lời dẫn của mô hình
@pytest.mark.parametrize("line", [
    "The image contains Chinese text. I will transcribe it accurately according to the provided rules:",
    "The text provided is already transcribed accurately from the image. Here's the transcription:",
    "Here is the transcription of the text:", "Sure, here's the text:",
])
def test_vie_rejects_model_chatter(line):
    assert vie_reject_reason(line) == "model_chatter"
    res = filter_page("vie", ["Phiên âm:", line, "Dịch nghĩa"])
    assert [t for _, t in res.kept] == ["Phiên âm:", "Dịch nghĩa"]
    assert res.rejected == [(2, "model_chatter", line)] and res.stats()["n_rejected"] == 1


@pytest.mark.parametrize("line", [
    "TRÍCH DẬT THI LƯỢC TOÀN", "The Ngoc Hoa temple", "Ngô Linh Ngọc dịch",
    "1. Hà Đình: tên đất", "I. HOÀNG HOA ĐỒ PHẢ", "Trần Văn Giáp, The Vietnamese transcription", "Theo bản A.",
])
def test_vie_keeps_vietnamese_and_ordinary_latin(line):
    assert vie_reject_reason(line) is None
    assert filter_page("vie", [line]).n_kept == 1


@pytest.mark.parametrize("line", [
    "snow does not fall,", "snow does not fall.",            # CPO_18 tr.531 (Qwen bịa trên trang mực thấm)
    "It is not what it was.", "and they have been there",
])
def test_vie_rejects_short_english(line):
    assert vie_reject_reason(line) == "english_function_words"
    assert filter_page("vie", ["Phiên âm:", line]).rejected == [(2, "english_function_words", line)]


@pytest.mark.parametrize("line", [
    "; snow,",                                   # < 3 từ
    "QUY AN NAM", "Do an to may",                # từ trùng tiếng Việt không dấu không được tính
    "Cot du tiu so gian thao am.", "(BOI THAN SU BO)", "Jian You Qing Shi Nan Xiang Wei",   # dòng thật, gần ngưỡng
    "Snow falls on the mountain.",               # có dấu chấm nhưng 2/5 từ chức năng (< 0,5)
    "snow does not fall — tuyết",                # không toàn ASCII
])
def test_english_rule_keeps_non_english_or_short(line):
    assert english_reject_reason(line) is None


def test_english_rule_thresholds():
    assert english_reject_reason("rain is not here") == "english_function_words"
    assert english_reject_reason("rain is not here", min_share=0.8) is None
    assert english_reject_reason("is not", min_words=2) == "english_function_words"
