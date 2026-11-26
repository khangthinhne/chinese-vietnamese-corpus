"""So chữ Việt của Qwen (vie.xml) với chữ Latin mà paddlevl đã đọc trên cùng trang (chỉ đọc, CPU).

Lần chạy luồng Hán (paddlevl) OCR mọi dòng của trang, kể cả dòng Việt; `build_layers` chỉ lọc chúng khỏi han.xml,
JSON thô vẫn giữ. Vì vậy paddlevl là một bản đọc độc lập để đối chiếu: dòng Qwen tự bịa (CPO_18 tr.369 "Một ngày,
một người đến gặp một thầy...") hay đoạn Qwen lặp/bỏ sót sẽ khớp kém với bản paddlevl.

Độ tương đồng tính trên chữ cái đã bỏ dấu (đ -> d), chữ thường, bỏ khoảng trắng/dấu câu/chữ số (paddlevl và Qwen
đều hay sai dấu), bằng tập k-gram ký tự (k = 4, không phụ thuộc thứ tự dòng):
- `precision`: tỉ lệ k-gram của Qwen có trong bản paddlevl. Thấp = Qwen có chữ mà paddlevl không thấy (bịa, lặp).
- `recall`: tỉ lệ k-gram của paddlevl có trong Qwen. Thấp = Qwen bỏ sót (tiêu đề, cả đoạn).
- `unsupported`: dòng Qwen đủ dài mà precision riêng của dòng thấp (dấu hiệu bịa rõ nhất, không bị cả trang pha loãng).
Trang mà paddlevl gần như không có chữ Latin (trang Hán, paddlevl bỏ vùng) thì không so (`None`).

Chỉ để CẢNH BÁO trong `check_ocr_complete` và xếp hạng trang cần xem/OCR lại; không sửa dữ liệu.
"""

from __future__ import annotations

import unicodedata

from src.ocr.script_filter import CJK_IDEOGRAPH_RE, count_cjk

K = 4
MIN_PAGE_CHARS = 60          # bản paddlevl / Qwen ngắn hơn (chữ cái đã gấp) thì không so trang
MIN_LINE_CHARS = 30          # dòng Qwen ngắn hơn thì không xét riêng
LINE_MIN_PRECISION = 0.35    # dòng Qwen có precision dưới mức này = không được paddlevl xác nhận


def fold_letters(text: str) -> str:
    """Chữ cái thường, bỏ dấu (đ -> d), bỏ mọi ký tự không phải chữ cái (khoảng trắng, dấu câu, chữ số)."""
    text = unicodedata.normalize("NFD", unicodedata.normalize("NFC", text).lower().replace("đ", "d"))
    return "".join(c for c in text if c.isalpha() and not unicodedata.combining(c) and c.isascii())


def kgrams(folded: str, k: int = K) -> set[str]:
    return {folded[i:i + k] for i in range(len(folded) - k + 1)}


def latin_lines(run: dict | None) -> list[str]:
    """Phần Latin của các dòng run paddlevl (dòng Việt mà paddlevl đọc được). Dòng lẫn chữ Hán (khảo dị
    "Bản A: 見; Bản B: ...") giữ phần Latin, bỏ chữ Hán; dòng chỉ có chữ Hán bị bỏ."""
    out = []
    for line in (run or {}).get("lines", []):
        text = str(line.get("text") or "")
        if count_cjk(text):
            text = CJK_IDEOGRAPH_RE.sub(" ", text)
        if text.strip() and fold_letters(text):
            out.append(text)
    return out


def compare_page(paddle_lines: list[str], vie_lines: list[str], k: int = K,
                 min_page_chars: int = MIN_PAGE_CHARS, min_line_chars: int = MIN_LINE_CHARS,
                 line_min_precision: float = LINE_MIN_PRECISION) -> dict | None:
    """Số đo một trang; None khi một trong hai bản quá ít chữ để so."""
    pad = "".join(fold_letters(x) for x in paddle_lines)
    vie = "".join(fold_letters(x) for x in vie_lines)
    if len(pad) < min_page_chars or len(vie) < min_page_chars:
        return None
    pad_set, vie_set = kgrams(pad, k), kgrams(vie, k)
    common = len(pad_set & vie_set)
    precision = common / len(vie_set)
    recall = common / len(pad_set)
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    unsupported = []
    for i, text in enumerate(vie_lines, 1):
        folded = fold_letters(text)
        if len(folded) < min_line_chars:
            continue
        grams = kgrams(folded, k)
        p = len(grams & pad_set) / len(grams)
        if p < line_min_precision:
            unsupported.append({"line": i, "precision": round(p, 3), "text": text[:120]})
    return {"precision": round(precision, 3), "recall": round(recall, 3), "f1": round(f1, 3),
            "paddle_chars": len(pad), "vie_chars": len(vie), "unsupported": unsupported}
