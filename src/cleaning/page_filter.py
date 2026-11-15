"""Bước 2: lọc trang — loại trang bị loại thủ công, trang trắng, header/footer lặp lại.

- excluded_pages (config): loại nguyên trang, lý do irrelevant_page (quyết định của người).
- trang trắng: trang không còn dòng có nội dung sau NFC.
- header/footer: dòng ĐẦU (header) / CUỐI (footer) trang xuất hiện GIỐNG NHAU (sau NFC,
  chữ thường) trên >= header_footer_min_pages trang -> bỏ trên mọi trang; không bao giờ
  bỏ nếu trang chỉ còn 1 dòng (tránh xoá nhầm nội dung ngắn). Không dựa vào số trang.
"""

from __future__ import annotations

import unicodedata
from collections import OrderedDict

from src.cleaning.audit import Audit
from src.cleaning.model import (BLANK_PAGE, IRRELEVANT_PAGE, PAGE_FOOTER, PAGE_HEADER,
                                CleanConfig, RawLine)


def _key(text: str) -> str:
    return unicodedata.normalize("NFC", text).casefold().strip()


def filter_pages(lines: list[RawLine], cfg: CleanConfig, audit: Audit
                 ) -> tuple[list[RawLine], list[dict]]:
    excluded_pages = set(cfg.excluded_pages or [])
    pages: OrderedDict[int, list[RawLine]] = OrderedDict()
    for ln in lines:
        pages.setdefault(ln.page, []).append(ln)

    kept: list[RawLine] = []
    page_records: list[dict] = []
    content: dict[int, list[RawLine]] = {}

    for page, pls in pages.items():
        if page in excluded_pages:
            page_records.append({"page": page, "reason": IRRELEVANT_PAGE, "lines": len(pls)})
            for ln in pls:
                audit.add("remove_page", IRRELEVANT_PAGE, rule="excluded_pages", line=ln)
            continue
        nonempty = [ln for ln in pls if ln.text.strip()]
        if not nonempty:
            page_records.append({"page": page, "reason": BLANK_PAGE, "lines": len(pls)})
            for ln in pls:
                audit.add("remove_page", BLANK_PAGE, rule="no_content", line=ln)
            continue
        content[page] = nonempty
        kept.extend(pls)

    # header/footer: văn bản lặp lại đúng trên đủ nhiều trang, ở vị trí đầu/cuối trang
    def recurring(edge: int, enabled: bool, reason: str) -> set[str]:
        if not enabled:
            return set()
        counts: dict[str, int] = {}
        for pls in content.values():
            if len(pls) <= 1:
                continue                      # trang chỉ 1 dòng: không coi là header/footer
            counts[_key(pls[edge].text)] = counts.get(_key(pls[edge].text), 0) + 1
        return {k for k, n in counts.items()
                if n >= cfg.header_footer_min_pages and len(k) <= 120}

    headers = recurring(0, cfg.remove_headers, PAGE_HEADER)
    footers = recurring(-1, cfg.remove_footers, PAGE_FOOTER)
    if headers or footers:
        kept = []
        for page, pls in pages.items():
            if page not in content:
                continue
            nonempty = content[page]
            for ln in pls:
                if ln.text.strip():
                    k = _key(ln.text)
                    edge_first = ln is nonempty[0]
                    edge_last = ln is nonempty[-1]
                    if (edge_first and k in headers) or (edge_last and k in footers):
                        reason = PAGE_HEADER if edge_first else PAGE_FOOTER
                        page_records.append({"page": page, "reason": reason, "lines": 1,
                                             "text": ln.text})
                        audit.add("remove_line", reason, rule="recurring_edge_line", line=ln)
                        continue
                kept.append(ln)
    return kept, page_records
