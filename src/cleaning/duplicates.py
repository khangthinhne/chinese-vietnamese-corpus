"""Bước 4: phát hiện dòng OCR trùng (exact + near-dup).

- exact: khoá = NFC + casefold; trùng trên CÙNG trang hoặc trang KẾ NHAU.
- near-dup: difflib ratio >= duplicate_threshold, chỉ xét vùng chồng lấn vùng quét OCR:
  C dòng cuối trang N vs C dòng đầu trang N+1 (và mọi cặp cùng trang).
- cụm ngắn hơn duplicate_min_len KHÔNG bao giờ bị bỏ (cụm từ/câu lặp hợp lệ).
- Giữ bản đầu tiên (canonical), ghi bản bị bỏ kèm ID của bản giữ (kept_id).
"""

from __future__ import annotations

import difflib
import unicodedata

from src.cleaning.audit import Audit
from src.cleaning.model import DUPLICATE_LINE, CleanConfig, RawLine


def _norm(text: str) -> str:
    return unicodedata.normalize("NFC", text).casefold().strip()


def detect_duplicates(lines: list[RawLine], cfg: CleanConfig, audit: Audit
                      ) -> tuple[list[RawLine], list[dict]]:
    if not cfg.remove_duplicates:
        return list(lines), []

    total: dict[int, int] = {}
    for ln in lines:                                   # tổng số dòng mỗi trang (đầu vào)
        total[ln.page] = total.get(ln.page, 0) + 1
    seen: dict[int, int] = {}                          # số dòng đã thấy của từng trang

    kept: list[RawLine] = []                           # (dòng, vị trí trên trang)
    kept_meta: list[tuple[RawLine, int]] = []
    records: list[dict] = []

    for ln in lines:
        pos = seen.get(ln.page, 0)
        seen[ln.page] = pos + 1
        key = _norm(ln.text)
        dup_of, ratio = None, 0.0
        if len(key) >= cfg.duplicate_min_len:
            near_edge = pos < cfg.duplicate_context    # dòng hiện tại nằm ở đầu trang nó
            for k, kpos in reversed(kept_meta):
                if k.page == ln.page:
                    cand = True                        # cùng trang: so mọi cặp
                elif abs(k.page - ln.page) == 1 and near_edge:
                    # vùng chồng lấn: bản giữ thuộc C dòng CUỐI trang nó
                    cand = (total[k.page] - kpos) <= cfg.duplicate_context
                else:
                    cand = False
                if not cand:
                    continue
                ratio = difflib.SequenceMatcher(None, _norm(k.text), key).ratio()
                if ratio >= cfg.duplicate_threshold:
                    dup_of = k
                    break
        if dup_of is not None:
            records.append({"kept_id": dup_of.id, "removed_id": ln.id, "page": ln.page,
                            "similarity": round(ratio, 4), "text": ln.text})
            audit.add("remove_line", DUPLICATE_LINE, rule=f"similar_to:{dup_of.id}",
                      line=ln, note=f"similarity={ratio:.3f}")
            continue
        kept.append(ln)
        kept_meta.append((ln, pos))
    return kept, records
