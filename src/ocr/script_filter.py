"""Lọc dòng OCR theo hệ chữ cho hai luồng `han` / `vie` (hàm thuần, không I/O).

- ``han``: giữ dòng có >= 1 chữ Hán (CJK Unified); trong dòng chỉ giữ chữ Hán và dấu câu CJK,
  cắt mọi ký tự Latin/số/khoảng trắng/dấu ASCII. Dòng toàn Latin/số bị bỏ.
- ``vie``: xoá MỌI chữ Hán và dấu câu CJK/full-width, rồi dọn ngoặc rỗng, dấu nháy mồ côi,
  khoảng trắng thừa (chỉ trên dòng thực sự bị xoá ký tự). Bỏ dòng không còn chữ cái Latin nào.

Cả hai luồng chuẩn hoá Unicode NFC. Không đổi giản thể/phồn thể ở bước này.
Lưu ý: NFC ánh xạ CJK Compatibility Ideographs (U+F900–FAFF) về chữ Unified tương ứng.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from typing import Iterable

LAYERS = ("han", "vie")

# Chữ Hán: 4 khối theo yêu cầu + các khối mở rộng hiếm (C..G, Compatibility Supplement)
# để luồng vie không bao giờ sót chữ Hán.
_CJK_IDEOGRAPH_RANGES = (
    "一-鿿",            # CJK Unified Ideographs
    "㐀-䶿",            # Extension A
    "\U00020000-\U0002a6ff",    # Extension B
    "豈-﫿",            # Compatibility Ideographs
    "\U0002a700-\U0002ebef",    # Extension C..F
    "\U00030000-\U0003134f",    # Extension G
    "\U0002f800-\U0002fa1f",    # Compatibility Ideographs Supplement
    "⺀-⿟",            # CJK Radicals Supplement + Kangxi Radicals
)
# Dấu câu/ký hiệu CJK và dấu câu full-width (không gồm chữ/số full-width).
_CJK_PUNCT_RANGES = (
    "　-〿",            # CJK Symbols and Punctuation: 、。「」『』【】〈〉《》〔〕〇 ...
    "︐-︟",            # Vertical Forms
    "︰-﹏",            # CJK Compatibility Forms: ﹒ ...
    "﹐-﹯",            # Small Form Variants
    "！-／",            # ！＂＃＄％＆＇（）＊＋，－．／
    "：-＠",            # ：；＜＝＞？＠
    "［-｀",            # ［＼］＾＿｀
    "｛-･",            # ｛｜｝～｟｠｡｢｣､･
    "・",                   # ・ (katakana middle dot)
)

CJK_IDEOGRAPH_RE = re.compile("[" + "".join(_CJK_IDEOGRAPH_RANGES) + "]")
CJK_PUNCT_RE = re.compile("[" + "".join(_CJK_PUNCT_RANGES) + "]")
CJK_ANY_RE = re.compile("[" + "".join(_CJK_IDEOGRAPH_RANGES + _CJK_PUNCT_RANGES) + "]")
_NOT_HAN_RE = re.compile("[^" + "".join(_CJK_IDEOGRAPH_RANGES + _CJK_PUNCT_RANGES) + "]")

_EMPTY_BRACKETS_RE = re.compile(r"[(\[{]\s*[,.;:!?/\-–—…·*'\"“”‘’\s]*[)\]}]")
_EMPTY_QUOTES_RE = re.compile(r"\"\s*\"|“\s*”|‘\s*’|«\s*»")
_ORPHAN_QUOTE_RE = re.compile(r"(?<!\S)[\"“”‘’'«»](?!\S)")
_SPACE_BEFORE_PUNCT_RE = re.compile(r"\s+([,.;:!?)\]}])")
_SPACE_AFTER_OPEN_RE = re.compile(r"([(\[{])\s+")
_MULTI_SPACE_RE = re.compile(r"[ \t 　]{2,}")


def has_cjk(text: str) -> bool:
    """True nếu có ít nhất một chữ Hán (không tính dấu câu CJK)."""
    return CJK_IDEOGRAPH_RE.search(text) is not None


def count_cjk(text: str) -> int:
    """Số ký tự CJK (chữ Hán + dấu câu CJK/full-width) trong chuỗi."""
    return len(CJK_ANY_RE.findall(text))


def is_latin_letter(char: str) -> bool:
    return char.isalpha() and unicodedata.name(char, "").startswith("LATIN")


def has_latin(text: str) -> bool:
    return any(is_latin_letter(c) for c in text)


def filter_han_line(text: str) -> str | None:
    """Trả về dòng chỉ còn chữ Hán + dấu câu CJK, hoặc None nếu dòng không có chữ Hán."""
    text = unicodedata.normalize("NFC", text)
    if not has_cjk(text):
        return None
    return _NOT_HAN_RE.sub("", text).replace("　", "") or None   # bỏ cả khoảng trắng ý văn


def _tidy_vie(text: str) -> str:
    previous = None
    while previous != text:                      # ngoặc/nháy lồng nhau: lặp đến điểm dừng
        previous = text
        text = _EMPTY_BRACKETS_RE.sub(" ", text)
        text = _EMPTY_QUOTES_RE.sub(" ", text)
        text = _ORPHAN_QUOTE_RE.sub(" ", text)
    text = _MULTI_SPACE_RE.sub(" ", text)
    text = _SPACE_BEFORE_PUNCT_RE.sub(r"\1", text)
    text = _SPACE_AFTER_OPEN_RE.sub(r"\1", text)
    return text.strip()


def filter_vie_line(text: str) -> tuple[str | None, int]:
    """Trả về (dòng đã lọc hoặc None, số ký tự CJK đã xoá).

    Dòng không chứa CJK chỉ được NFC + strip, không bị "dọn" thêm để không sửa văn bản Việt hợp lệ.
    """
    text = unicodedata.normalize("NFC", text)
    removed = count_cjk(text)
    if removed:
        text = _tidy_vie(CJK_ANY_RE.sub(" ", text))
    else:
        text = text.strip()
    if not has_latin(text):
        return None, removed
    return text, removed


@dataclass
class PageFilterResult:
    """Kết quả lọc một trang. `kept` = [(src_line 1-based, text)] theo thứ tự đọc gốc."""
    layer: str
    kept: list[tuple[int, str]] = field(default_factory=list)
    n_src: int = 0
    cjk_removed: int = 0                         # chỉ có nghĩa với vie

    @property
    def n_kept(self) -> int:
        return len(self.kept)

    @property
    def n_dropped(self) -> int:
        return self.n_src - self.n_kept

    def stats(self) -> dict[str, int]:
        return {"n_src": self.n_src, "n_kept": self.n_kept, "n_dropped": self.n_dropped,
                "cjk_removed": self.cjk_removed}


def filter_page(layer: str, lines: Iterable[str]) -> PageFilterResult:
    """Lọc toàn bộ dòng của một trang. Dòng trắng của engine vẫn được đếm trong `n_src`."""
    if layer not in LAYERS:
        raise ValueError(f"Lớp lọc phải là {LAYERS}, nhận {layer!r}")
    result = PageFilterResult(layer)
    for index, text in enumerate(lines, 1):
        result.n_src += 1
        if layer == "han":
            kept = filter_han_line(text)
        else:
            kept, removed = filter_vie_line(text)
            result.cjk_removed += removed
        if kept:
            result.kept.append((index, kept))
    if layer == "vie":
        leaked = [t for _, t in result.kept if count_cjk(t)]
        if leaked:                               # bất biến của bộ lọc; xảy ra là bug
            raise AssertionError(f"Bộ lọc vie còn sót CJK trong {len(leaked)} dòng")
    return result
