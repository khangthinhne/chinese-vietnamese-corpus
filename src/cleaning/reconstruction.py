"""Bước 7: nối lại văn bản bị cắt qua ranh giới trang (chỉ ghi nhận + gạch nối rõ ràng).

- Nối lại CHỈ khi bằng chứng đủ rõ:
  * dehyphenate: dòng trước kết thúc bằng gạch nối (-, ‐, ‑, ­) và dòng sau bắt đầu
    bằng chữ thường (lớp Việt) -> xoá gạch, đánh cờ "dehyphenated" để build_stream
    nối KHÔNG chèn dấu cách (từ bị cắt đôi không bị chèn khoảng trắng đổi nghĩa).
  * sentence_continuation: dòng trước chưa trọn câu, dòng sau bắt đầu chữ thường ->
    chỉ GHI NHẬN phép nối (việc nối do build_stream/tách câu đảm nhiệm như cũ).
  * ambiguous_join: dòng trước chưa trọn câu nhưng dòng sau viết hoa -> có thể là câu
    mới mất dấu hoặc đoạn mới: KHÔNG tự ý xử lý, cờ review.
- Đoạn văn độc lập KHÔNG bao giờ được tự ghép: không chèn thêm dấu cách/ngữ gì ngoài
  hành vi cũ; phân đoạn do tách câu theo dấu câu đảm nhiệm.
- Han: dòng chưa kết bằng dấu kết Hán -> sentence_continuation (Hán không có hoa/thường).
"""

from __future__ import annotations

import re

from src.cleaning.audit import Audit
from src.cleaning.model import (CleanConfig, RawLine, has_flag)
from src.cleaning.footnotes import HAN_COMPLETE_CH

HYPHENS = "-‐‑­"
VI_COMPLETE = re.compile(r"[.?!…;:\"”’')﹚\]]$")


def _vi_incomplete(text: str) -> bool:
    return not VI_COMPLETE.search(text.strip())


def reconstruct_page_boundaries(lines: list[RawLine], cfg: CleanConfig, audit: Audit,
                                layer: str) -> tuple[list[RawLine], list[dict]]:
    ops: list[dict] = []
    if not cfg.reconstruct_page_boundaries:
        return list(lines), ops
    for i in range(len(lines) - 1):
        prev, cur = lines[i], lines[i + 1]
        if prev.page == cur.page:
            continue
        prev_t = prev.text.rstrip()
        # dehyphenate: từ bị cắt đôi rõ ràng
        if prev_t and prev_t[-1] in HYPHENS and layer != "han" \
                and cur.text.lstrip()[:1].islower():
            fixed = RawLine(prev.id, prev.page, prev_t[:-1].rstrip(), prev.is_last_on_page,
                            prev.raw_text, prev.flags + ["dehyphenated"],
                            list(prev.source_ids), prev.document_id)
            lines[i] = fixed
            prev = fixed
            ops.append({"type": "dehyphenate", "from_id": prev.id, "to_id": cur.id,
                        "from_page": prev.page, "to_page": cur.page,
                        "before": prev.raw_text or prev_t, "after": prev.text,
                        "confidence": "high"})
            audit.add("reconstruct", "dehyphenate", rule="trailing_hyphen", line=prev,
                      original=prev.raw_text or prev_t, processed=prev.text)
        # phân loại phép nối qua trang (không sửa text)
        incomplete = prev.text[-1] not in HAN_COMPLETE_CH if layer == "han" \
            else _vi_incomplete(prev.text)
        if not incomplete:
            continue
        next_first = cur.text.lstrip()[:1]
        ambiguous = layer != "han" and (next_first.isupper() or next_first in "\"“‘'([«")
        op_type = "ambiguous_join" if ambiguous else "sentence_continuation"
        ops.append({"type": op_type, "from_id": prev.id, "to_id": cur.id,
                    "from_page": prev.page, "to_page": cur.page,
                    "before": prev.raw_text or prev.text, "after": prev.text,
                    "confidence": "low" if ambiguous else "high"})
        audit.add("reconstruct", op_type, rule="cross_page_continuation", line=prev,
                  note=f"next={cur.id}")
        if ambiguous:
            audit.flag_review(cur, "uncertain", rule="ambiguous_join",
                              note=f"nối trang {prev.page}->{cur.page}, dòng trước chưa trọn câu")
    return lines, ops
