"""Lọc dòng OCR theo hệ chữ cho hai luồng `han` / `vie` (hàm thuần, không I/O).

- ``han``: giữ dòng có >= 1 chữ Hán (CJK Unified); trong dòng chỉ giữ chữ Hán và dấu câu CJK,
  cắt mọi ký tự Latin/số/khoảng trắng/dấu ASCII. Dòng toàn Latin/số bị bỏ.
- ``vie``: xoá MỌI chữ Hán và dấu câu CJK/full-width, rồi dọn ngoặc rỗng, dấu nháy mồ côi,
  khoảng trắng thừa (chỉ trên dòng thực sự bị xoá ký tự). Bỏ dòng không còn chữ cái Latin nào, và dòng
  lời dẫn tiếng Anh của mô hình ("The image contains ..."; ghi vào `rejected`).

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


# Số thứ tự đầu tiêu đề bài ("37. 題安貞巢", "2.答李交州…"): segmentation CPO_18/19 mở bài mới theo số này
# (start_patterns/title_patterns "^\d{1,3}[.、…]\s*<chữ Hán>"), nên giữ lại dạng chuẩn "37. ".
_HAN_NUMBER_PREFIX_RE = re.compile(r"^\s*(\d{1,3})\s*[.．、﹒·。)]\s*(?=[" + "".join(_CJK_IDEOGRAPH_RANGES) + "])")


def filter_han_line(text: str) -> str | None:
    """Trả về dòng chỉ còn chữ Hán + dấu câu CJK (giữ số thứ tự đầu tiêu đề), hoặc None nếu dòng không có chữ Hán."""
    text = unicodedata.normalize("NFC", text)
    if not has_cjk(text):
        return None
    m = _HAN_NUMBER_PREFIX_RE.match(text)
    prefix = f"{m[1]}. " if m else ""
    body = _NOT_HAN_RE.sub("", text[m.end():] if m else text).replace("　", "")   # bỏ cả khoảng trắng ý văn
    return prefix + body if body else None


# Dòng Hán "giả": dòng tiếng Việt (khảo dị/chú thích) có trích vài chữ Hán, hoặc paddlevl đọc nhầm một dòng
# thành 1 chữ Hán lẫn ký tự Latin (vd "J. 一", "山 [r]"). Chỉ xét chữ cái Latin, không xét số/dấu câu, nên
# "2. 其二", "1. 與高明..." vẫn được giữ. Mặc định bảo thủ; đổi qua build_layers --han-min-cjk-share/...
HAN_MIN_CJK_SHARE = 0.3          # chữ Hán / (chữ Hán + chữ cái Latin) tối thiểu của dòng GỐC
HAN_MIN_CJK_WITH_LATIN = 2       # dòng có chữ cái Latin phải có ít nhất ngần này chữ Hán


# Lời tự nói của model bằng tiếng Trung hiện đại (giản thể) lọt vào luồng Hán, đã gặp ở CPO_20:
# tr.450 "这里原文本可能存在识别偏差…", tr.679/703 "作为一个人工智能语言模型，我还没学习如何…".
# Các cụm này không thể có trong văn bản Hán cổ của corpus.
HAN_CHATTER_RE = re.compile(r"人工智能|语言模型|語言模型|原文本|识别|識別偏差|无法识别|抱歉|作为一个|我还没|按要求"
                            r"|(?i:\bas\s+an?\s+ai\b|\blanguage\s+model\b)")

# Dòng "kẻ" rác: paddlevl đọc một lát ảnh mỏng giữa hai dòng thành 1-2 chữ + chuỗi gạch lặp,
# vd CPO_20 tr.631 "口 + — — — — …" (hai dòng thật trên/dưới vẫn được đọc đủ ở đơn vị riêng).
RULE_JUNK_MAX_CJK = 2
RULE_JUNK_MIN_DASHES = 6
_DASH_CHARS = frozenset("—–-─━_＿－")


def han_reject_reason(text: str, min_cjk_share: float = HAN_MIN_CJK_SHARE,
                      min_cjk_with_latin: int = HAN_MIN_CJK_WITH_LATIN) -> str | None:
    """Lý do loại một dòng có chữ Hán khỏi lớp han (None = giữ). Xét trên dòng GỐC, trước khi cắt Latin."""
    text = unicodedata.normalize("NFC", text)
    if HAN_CHATTER_RE.search(text):
        return "model_chatter"
    cjk = len(CJK_IDEOGRAPH_RE.findall(text))
    if cjk <= RULE_JUNK_MAX_CJK and sum(c in _DASH_CHARS for c in text) >= RULE_JUNK_MIN_DASHES:
        return "rule_junk"
    latin = sum(1 for c in text if is_latin_letter(c))
    if not cjk or not latin:
        return None
    if cjk < min_cjk_with_latin:
        return f"few_cjk_with_latin:{cjk}<{min_cjk_with_latin}"
    share = cjk / (cjk + latin)
    if share < min_cjk_share:
        return f"low_cjk_share:{share:.2f}<{min_cjk_share}"
    return None


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


# Câu "trò chuyện" của mô hình (Qwen đôi khi mở đầu bằng lời dẫn tiếng Anh thay vì chép ảnh), vd
# "The image contains Chinese text. I will transcribe it ...". Chỉ xét dòng KHÔNG có chữ cái Việt có dấu/đ,
# nên câu tiếng Việt (kể cả viết hoa) không bao giờ bị loại.
_VIE_SPECIFIC_RE = re.compile(r"[À-ỹĐđ]")
_CHATTER_RE = re.compile(
    r"^\W*(?:the\s+(?:image|text|provided|transcription|following)\b|here(?:'s|\s+is|\s+are)\b"
    r"|i\s+(?:will|can|'ll|have)\b|sure\b|certainly\b|below\s+is\b|this\s+(?:image|text)\b)"
    r"|\btranscri(?:be|bed|ption|bing)\b"
    r"|\bas\s+an?\s+ai\b|\blanguage\s+model\b|\bi(?:'m|\s+am)\s+sorry\b",
    re.IGNORECASE)


def vie_reject_reason(text: str) -> str | None:
    """Lý do loại một dòng khỏi lớp vie (None = giữ): hiện chỉ có lời dẫn tiếng Anh của mô hình."""
    text = unicodedata.normalize("NFC", text)
    if _VIE_SPECIFIC_RE.search(text):
        return None
    return "model_chatter" if _CHATTER_RE.search(text) else None


def chatter_reason(text: str, layer: str) -> str | None:
    """"model_chatter" nếu dòng là lời tự nói của mô hình theo luật build_layers của lớp đó, không thì None.

    Dùng ở check_ocr_complete: dòng như vậy còn sót trong han.xml/vie.xml là LỖI (build_layers phải đã loại)."""
    if layer == "han":
        return "model_chatter" if HAN_CHATTER_RE.search(unicodedata.normalize("NFC", text)) else None
    return vie_reject_reason(text)


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


# Dòng Hán "lẻ" do paddlevl tự sinh trên trang chỉ có chữ Việt (đã mở ảnh xác nhận, CPO_20: 一 ×7, 也, 隠, và
# 欽定四庫全書 ở tr.321-621). Chỉ loại khi trang còn ĐÚNG MỘT dòng Hán, dòng đó ngắn (<= LONE_HAN_MAX_CHARS chữ Hán)
# hoặc thuộc danh sách đã biết, VÀ luồng Việt cùng trang có >= min_vie_lines dòng (trang thật sự là trang chữ Việt).
LONE_HAN_MAX_CHARS = 3
LONE_HAN_KNOWN = frozenset({"欽定四庫全書"})
LONE_HAN_MIN_VIE_LINES = 8


def lone_han_reason(han_lines: list[str], vie_lines: int | None, *, min_vie_lines: int = LONE_HAN_MIN_VIE_LINES,
                    max_chars: int = LONE_HAN_MAX_CHARS) -> str | None:
    """Lý do loại dòng Hán duy nhất của trang, hoặc None. `vie_lines` None = chưa có OCR Việt -> không loại."""
    if min_vie_lines <= 0 or len(han_lines) != 1 or vie_lines is None or vie_lines < min_vie_lines:
        return None
    text = han_lines[0].strip()
    if count_cjk(text) <= max_chars or text in LONE_HAN_KNOWN:
        return "lone_han_on_vie_page"
    return None


@dataclass
class PageFilterResult:
    """Kết quả lọc một trang. `kept` = [(src_line 1-based, text)] theo thứ tự đọc gốc."""
    layer: str
    kept: list[tuple[int, str]] = field(default_factory=list)
    n_src: int = 0
    cjk_removed: int = 0                         # chỉ có nghĩa với vie
    rejected: list[tuple[int, str, str]] = field(default_factory=list)   # (src_line, lý do, dòng gốc)

    @property
    def n_kept(self) -> int:
        return len(self.kept)

    @property
    def n_dropped(self) -> int:
        return self.n_src - self.n_kept

    def stats(self) -> dict[str, int]:
        return {"n_src": self.n_src, "n_kept": self.n_kept, "n_dropped": self.n_dropped,
                "cjk_removed": self.cjk_removed, "n_rejected": len(self.rejected)}


def filter_page(layer: str, lines: Iterable[str], *, han_min_cjk_share: float = HAN_MIN_CJK_SHARE,
                han_min_cjk_with_latin: int = HAN_MIN_CJK_WITH_LATIN) -> PageFilterResult:
    """Lọc toàn bộ dòng của một trang. Dòng trắng của engine vẫn được đếm trong `n_src`.

    Dòng bị `han_reject_reason` (han) hoặc `vie_reject_reason` (vie) loại được ghi vào `rejected` (không mất dấu)."""
    if layer not in LAYERS:
        raise ValueError(f"Lớp lọc phải là {LAYERS}, nhận {layer!r}")
    result = PageFilterResult(layer)
    for index, text in enumerate(lines, 1):
        result.n_src += 1
        if layer == "han":
            kept = filter_han_line(text)
            reason = han_reject_reason(text, han_min_cjk_share, han_min_cjk_with_latin) if kept else None
            if reason:
                result.rejected.append((index, reason, text))
                kept = None
        else:
            kept, removed = filter_vie_line(text)
            result.cjk_removed += removed
            reason = vie_reject_reason(text) if kept else None
            if reason:
                result.rejected.append((index, reason, text))
                kept = None
        if kept:
            result.kept.append((index, kept))
    if layer == "vie":
        leaked = [t for _, t in result.kept if count_cjk(t)]
        if leaked:                               # bất biến của bộ lọc; xảy ra là bug
            raise AssertionError(f"Bộ lọc vie còn sót CJK trong {len(leaked)} dòng")
    return result
