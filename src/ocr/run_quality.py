"""Đánh giá một run OCR thô (hàm thuần, không I/O): đơn vị nào hỏng, run có dùng được cho luồng nào.

Dùng chung bởi `build_layers.select_run` (chọn run) và `ocr_check` (kiểm tra độ đủ).
"""

from __future__ import annotations

import unicodedata

from src.ocr.script_filter import CJK_IDEOGRAPH_RE

BAD_UNIT_FLAGS = frozenset({"truncated", "loop_stopped"})
LATIN_LETTERS_BAD_UNIT = 20          # dải Qwen hỏng chỉ làm hại luồng vie khi có >= ngần này chữ Latin


def latin_letters(text: str) -> int:
    return sum(1 for c in text if c.isalpha() and unicodedata.name(c, "").startswith("LATIN"))


def run_units(run: dict | None) -> list[dict]:
    """Dải (qwen) hoặc đơn vị (paddlevl) trong payload gốc của run."""
    return list(((run or {}).get("raw") or {}).get("bands") or [])


def bad_units(run: dict | None, layer: str) -> list[dict]:
    """Đơn vị hỏng (cắt cụt / dừng vì lặp) chứa chữ của ĐÚNG luồng: Latin cho vie, Hán cho han.

    Qwen lặp trên một dải toàn chữ Hán không làm hỏng luồng vie (chữ Hán bị lọc bỏ), nên không tính.
    Cờ `repetition_collapsed` một mình không tính: nó bắt cả đường kẻ "_____" trước chú thích."""
    out = []
    for unit in run_units(run):
        if not BAD_UNIT_FLAGS & set(unit.get("flags") or []):
            continue
        text = str(unit.get("generated_text") or "")
        if layer == "vie" and latin_letters(text) >= LATIN_LETTERS_BAD_UNIT:
            out.append(unit)
        elif layer == "han" and CJK_IDEOGRAPH_RE.search(text):
            out.append(unit)
    return out


def run_is_valid(run: dict | None, layer: str) -> bool:
    return bool(run) and not bad_units(run, layer)
