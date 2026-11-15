"""Bước 1: chuẩn hoá Unicode an toàn (NFC, ký tự điều khiển, khoảng trắng).

Không đụng dấu câu/nghĩa; chữ Hán - Hán Nôm - Việt giữ nguyên. Bản gốc lưu vào
raw_text ở pipeline; hàm này thuần túy trả chuỗi mới.
"""

from __future__ import annotations

import re
import unicodedata

# ký tự điều khiển + zero-width (BOM, ZWSP, joiner...) — không bao giờ có nghĩa
_CTRL = re.compile("[\u0000-\u0008\u000b-\u001f\u007f\u200b-\u200d\u2060\ufeff]")


def normalize_unicode(text: str) -> str:
    """NFC + bỏ ký tự điều khiển/zero-width + gộp khoảng trắng. An toàn để chạy lặp lại."""
    text = unicodedata.normalize("NFC", text)
    text = _CTRL.sub("", text).replace("\t", " ").replace("\n", " ").replace("\r", " ")
    return re.sub(r"\s+", " ", text).strip()
