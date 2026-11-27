"""Bước 8: chuẩn hoá từng dòng theo lớp (tách từ clean.py cũ, giữ nguyên hành vi).

Han: ASCII -> dấu câu Hán, bỏ khoảng trắng/ký tự rác/chữ Latin lẫn, gộp dấu lặp.
Vi:  NFC, bỏ marker (đã xoá ở bước artifacts nên ở đây là no-op an toàn), "|" -> cách,
     bỏ khoảng trắng trước dấu câu, gộp khoảng trắng.
Không đổi nghĩa, không sửa tên riêng/niên hiệu/số (yêu cầu 2.7).
"""

from __future__ import annotations

import re
import unicodedata

from src.common.corpus import normalize_line
from src.cleaning.model import ASCII_TO_HAN, HAN_JUNK, RawLine, CleanConfig, LEADING_NUM_RE


# Chữ khuyết "□" (và [MASK] đã có sẵn) -> token [MASK] mà tool dóng Hán-Âm của Lab hiểu là 1 chữ hỏng.
# Giữ qua bước lọc bằng một ký tự tạm (Private Use Area) vì "[", "]" và chữ Latin bị xoá khỏi dòng Hán.
HAN_MASK = "[MASK]"
_MASK_SENTINEL = ""


def clean_han_line(text: str, cfg: CleanConfig) -> str:
    text = unicodedata.normalize("NFC", text)
    text = LEADING_NUM_RE.sub("", text)
    text = text.replace(HAN_MASK, _MASK_SENTINEL).replace("□", _MASK_SENTINEL)
    text = "".join(ASCII_TO_HAN.get(c, c) for c in text)
    kept = []
    for c in text:
        if c.isspace() or c in HAN_JUNK:
            continue
        if cfg.han_strip_latin and c.isascii():          # chữ/số Latin lẫn trong dòng Hán = rác OCR
            continue
        kept.append(c)
    text = "".join(kept)
    text = re.sub(r"([，。！？；：、])\1+", r"\1", text)   # dấu lặp do OCR
    return text.strip("，、：—–…").replace(_MASK_SENTINEL, HAN_MASK)   # dấu ngắt/gạch ở biên dòng vô nghĩa


def clean_vi_line(text: str, cfg: CleanConfig) -> str:
    text = normalize_line(text)
    text = text.replace("|", " ").replace("¦", " ")
    text = re.sub(r"\s+([,.;:?!])", r"\1", text)            # bỏ khoảng trắng trước dấu câu
    text = re.sub(r"\s{2,}", " ", text)
    return text.strip()


def normalize_lines(lines: list[RawLine], cfg: CleanConfig, layer: str) -> list[RawLine]:
    out = []
    for ln in lines:
        cleaned = clean_han_line(ln.text, cfg) if layer == "han" else clean_vi_line(ln.text, cfg)
        out.append(RawLine(ln.id, ln.page, cleaned, ln.is_last_on_page, ln.raw_text, ln.flags,
                           list(ln.source_ids), ln.document_id))
    return out


__all__ = ["clean_han_line", "clean_vi_line", "normalize_lines", "is_parenthesized", "cjk_ratio"]


def is_parenthesized(text: str) -> bool:
    """Dòng bọc ngoặc trọn vẹn; chấp nhận đóng ASCII lẫn phiên bản nhỏ/toàn dải OCR hay dùng."""
    closers = {"(": (")", "﹚"), "（": ("）", "﹚", ")"), "[": ("]", "］"), "［": ("］", "]")}
    t = text.strip()
    return len(t) > 2 and t[0] in closers and t[-1] in closers[t[0]]
