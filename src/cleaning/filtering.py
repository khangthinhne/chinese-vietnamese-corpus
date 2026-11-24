"""Bước 9: lọc dòng — lý do mã hoá rõ ràng, giữ nguyên các heuristic cũ + ngữ cảnh.

- empty_line / configured_drop_pattern / not_han / not_vi / no_text: như clean.py cũ
  (chuỗi lý do giữ nguyên để các file/THÌ report cũ không vỡ).
- heading: tiêu đề/tên tác giả — HÁN: ngắn, không dấu ngắt câu, dòng trước đã trọn câu,
  không phải dòng cuối trang. VIỆT: "N. Tên..." không kết câu + thêm hai bảo vệ mới:
  (a) không coi là tiêu đề nếu dòng KẾ bắt đầu chữ thường (câu còn dang dở),
  (b) nhãn tiêu đề thuần ("CHÚ THÍCH :", "MỤC LỤC"...) qua heading_patterns.
  Trích dẫn nguồn nhiều dòng đã bị bước footnotes loại trước nên không còn bị nhầm.
- footnote các dạng còn sót: ngoặc trọn 1 dòng (is_parenthesized, kể cả ﹚).
"""

from __future__ import annotations

import re

from src.common.corpus import cjk_ratio
from src.cleaning.audit import Audit
from src.cleaning.model import (FOOTNOTE, HEADING, HAN_PUNCT, CleanConfig, RawLine)
from src.cleaning.normalization import is_parenthesized


def drop_reason(raw: str, cleaned: str, line: RawLine, cfg: CleanConfig,
                prev_complete: bool = True, next_lower: bool = False) -> str | None:
    if not cleaned:
        return "empty"
    for pat in cfg.drop_patterns:
        if re.search(pat, raw) or re.search(pat, cleaned):
            return f"pattern:{pat}"
    if cfg.layer == "han":
        if cjk_ratio(cleaned) < cfg.min_cjk:
            return "not_han"
        if is_parenthesized(raw.strip()):
            return "footnote"
        for pat in cfg.han_heading_patterns:
            if re.search(pat, cleaned):
                return "heading"
        # Optional legacy heuristic: short, punctuation-free Hán line after a
        # complete line.  It is opt-in because it drops most Hán poetry lines.
        if (cfg.han_heading_heuristic and cfg.heading_heuristic and prev_complete
                and not line.is_last_on_page
                and len(cleaned) <= cfg.max_heading_han
                and not re.search(f"[{re.escape(HAN_PUNCT)}]", cleaned)):
            return "heading"
    else:
        if not any(c.isalnum() for c in cleaned):
            return "no_text"
        if cjk_ratio(cleaned) > 0.1:
            return "not_vi"
        if is_parenthesized(cleaned):
            return "footnote"
        if (cfg.heading_heuristic and re.match(r"^\d{1,3}\s*[.)]\s+\S", cleaned)
                and not re.search(r"[.?!;…]$", cleaned) and len(cleaned) <= cfg.max_heading_vi
                and not next_lower):        # câu còn dang dở thì không phải tiêu đề
            return "heading"
        for pat in cfg.heading_patterns:
            if re.search(pat, cleaned, re.IGNORECASE):
                return "heading"
    return None


def filter_invalid_lines(lines: list[RawLine], cfg: CleanConfig, audit: Audit
                         ) -> tuple[list[RawLine], list[tuple[str, int, str, str]]]:
    kept: list[RawLine] = []
    dropped_rows: list[tuple[str, int, str, str]] = []
    for i, ln in enumerate(lines):
        nxt = lines[i + 1] if i + 1 < len(lines) else None
        next_lower = bool(nxt) and nxt.text[:1].islower()
        reason = drop_reason(ln.raw_text or ln.text, ln.text, ln, cfg,
                             prev_complete=not kept or kept[-1].text[-1] in "。！？；﹖﹕﹒」』”’）﹚",
                             next_lower=next_lower)
        if reason:
            if reason != "empty":
                dropped_rows.append((ln.id, ln.page, reason, ln.raw_text or ln.text))
                audit.add("remove_line", reason,
                          rule="heading_patterns" if reason == "heading" and
                          any(re.search(p, ln.text, re.IGNORECASE) for p in cfg.heading_patterns)
                          else "heuristic", line=ln)
            continue
        kept.append(ln)
    return kept, dropped_rows
