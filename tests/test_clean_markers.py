"""Chỉ mục chú thích dạng chữ số mũ Unicode phải bị xoá ở bước làm sạch (có ghi audit)."""
import re

import pytest

from src.cleaning.artifacts import clean_ocr_artifacts
from src.cleaning.model import MARKER_RE, OCRCleaningConfig


@pytest.mark.parametrize("text,expected", [
    ("Hồi vọng Sài Sơn ² đệ nhất phong.", "Hồi vọng Sài Sơn đệ nhất phong."),
    ("Thị hành ung mục y thường hội ², Tự ngã", "Thị hành ung mục y thường hội, Tự ngã"),
    ("Phù dao cửu vạn tài chương thuần ⁴ Tư đắc", "Phù dao cửu vạn tài chương thuần Tư đắc"),
    ("câu xướng¹² bãi", "câu xướng bãi"),
    ("chú thích (1) và [2] và ①", "chú thích  và  và "),        # hành vi cũ giữ nguyên
])
def test_marker_re_removes_superscript_footnote_markers(text, expected):
    assert MARKER_RE.sub("", text) == expected


def test_superscript_marker_is_recorded():
    res = clean_ocr_artifacts("Hồi vọng Sài Sơn ² đệ nhất phong.", OCRCleaningConfig())
    assert res.text.startswith("Hồi vọng Sài Sơn đệ")
    assert [r for r, frag in res.removed if r == "in_text_marker" and "²" in frag]


def test_superscript_inside_word_or_math_not_mangled_beyond_marker():
    # chữ số mũ dính chữ cái phía sau không phải chỉ mục (vd "x²y"): giữ nguyên
    assert MARKER_RE.sub("", "x²y") == "x²y"
