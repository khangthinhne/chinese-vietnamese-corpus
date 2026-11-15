import pytest

from src.segmentation.patterns import compile_matcher, norm_text

HEADINGS = [r"^PHIÊN\s+ÂM\b", r"^DỊCH\s+NGHĨA\b"]


def matcher(patterns=HEADINGS, **kwargs):
    return compile_matcher(patterns, "test", heading=True, **kwargs)


@pytest.mark.parametrize("text", [
    "PHIÊN ÂM",
    "PHIÊN ÂM :",
    "l PHIÊN ÂM : |",
    "DỊCH NGHĨA",
    "DỊCH NGHĨA : |",
    "  phiên âm : |  ",
])
def test_heading_matches_structured_lines(text):
    assert matcher().matches(text)


@pytest.mark.parametrize("text", [
    "Sau đây là toàn văn, có kèm theo phần",
    "phiên âm, dịch nghĩa và chú thích của chúng tôi.",
    "Nội dung có nhắc đến phiên âm trong câu văn.",
    "PHIÊN ÂM là phần chuyển âm của văn bản.",
    "DỊCH NGHĨA của đoạn này rất sát.",
])
def test_heading_rejects_prose(text):
    assert not matcher().matches(text)


def test_heading_prefix_is_configurable():
    custom = matcher(patterns=[r"^PHIÊN\s+ÂM\b"], prefix_pattern=r"^§\s*")
    assert custom.matches("§ PHIÊN ÂM : |")
    assert not custom.matches("x PHIÊN ÂM : |")


def test_heading_prefix_can_be_disabled():
    strict = matcher(prefix_pattern=None)
    assert strict.matches("PHIÊN ÂM")
    assert not strict.matches("l PHIÊN ÂM : |")


def test_regular_mode_keeps_search_semantics():
    old = compile_matcher(["PHIÊN ÂM"], "old")
    assert old.matches("Nội dung có nhắc đến phiên âm trong câu văn.")


def test_heading_normalizes_unicode_and_whitespace():
    decomposed = "PHIE\u0302N \u00c2M : |"
    assert norm_text(decomposed) == "PHIÊN ÂM : |"
    assert matcher().matches(decomposed)

def test_heading_case_sensitivity_is_configurable():
    assert matcher(case_sensitive=False).matches("phiên âm : |")
    assert not matcher(case_sensitive=True).matches("phiên âm : |")
