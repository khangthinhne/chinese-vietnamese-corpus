"""Bước 10: tách câu trên dòng chảy đã nối (tách từ clean.py cũ, giữ nguyên hành vi).

- Dòng chảy: toàn bộ dòng giữ lại nối thành MỘT chuỗi rồi mới tách theo dấu câu;
  câu trải >1 trang có pages=[...nhiều trang...] và cờ cross_page.
- Dòng có cờ "dehyphenated" (từ bị cắt đôi qua trang) được nối KHÔNG chèn dấu cách.
- Tách câu là bước SINH ỨNG CỬ ban đầu: dấu câu không phải lúc nào cũng là biên nghĩa
  (viết tắt, số, ngoặc trích nguồn được split_vi/split_han giữ lại — xem split_vi).
"""

from __future__ import annotations

import re

from src.common.corpus import cjk_ratio
from src.common.sentences import Sentence
from src.cleaning.model import (HAN_CLOSERS, HAN_TERMINATORS, VI_ABBREV, VI_CLOSERS,
                                VI_OPENERS, CleanConfig, RawLine, has_flag)


def build_stream(lines: list[RawLine], layer: str) -> tuple[str, list[int]]:
    """Nối các dòng giữ lại. Trả về (text, owner) với owner[k] = chỉ số dòng của ký tự k (-1 = dấu nối)."""
    sep = "" if layer == "han" else " "
    chars: list[str] = []
    owner: list[int] = []
    for idx, ln in enumerate(lines):
        if idx and sep and not has_flag(lines[idx - 1], "dehyphenated"):
            chars.append(sep)
            owner.append(-1)
        chars.extend(ln.text)
        owner.extend([idx] * len(ln.text))
    return "".join(chars), owner


def split_han(text: str) -> list[tuple[int, int]]:
    spans, start, i, n = [], 0, 0, len(text)
    while i < n:
        if text[i] in HAN_TERMINATORS:
            j = i + 1
            while j < n and text[j] in HAN_CLOSERS:
                j += 1
            spans.append((start, j))
            start, i = j, j
        else:
            i += 1
    if start < n:
        spans.append((start, n))
    return spans


def _token_before(text: str, pos: int) -> str:
    j = pos
    while j > 0 and not text[j - 1].isspace():
        j -= 1
    return text[j:pos].lstrip(VI_OPENERS)


def split_vi(text: str, cfg: CleanConfig) -> list[tuple[int, int]]:
    spans, start, n = [], 0, len(text)
    i = 0
    while i < n:
        c = text[i]
        cut = False
        if c == ";" or (c == ":" and cfg.split_colon):
            cut = True
        elif c in ".?!…":
            j = i + 1
            while j < n and text[j] in ".?!…":          # "..." / "?!"
                j += 1
            k = j
            while k < n and text[k] in VI_CLOSERS:
                k += 1
            m = k
            while m < n and text[m].isspace():
                m += 1
            at_end = m >= n
            next_ok = at_end or text[m].isupper() or text[m] in VI_OPENERS
            if c == "." and j == i + 1:                    # dấu chấm đơn: kiểm tra viết tắt/số thứ tự
                tok = _token_before(text, i).lower()
                if (tok in cfg.abbrev or tok.isdigit() or (len(tok) == 1 and tok.isalpha())
                        or "." in tok):
                    next_ok = False
            if next_ok:
                i = k - 1
                cut = True
        if cut:
            spans.append((start, i + 1))
            start = i + 1
            while start < n and text[start].isspace():    # dấu cách giữa hai câu: bỏ ở cả hai vế
                start += 1
        i += 1
    if start < n:
        spans.append((start, n))
    return spans


def _document_id(line_id: str) -> str:
    parts = line_id.rsplit(".", 2)
    return parts[0] if len(parts) == 3 else line_id


def make_sentences(work_id: str, layer: str, lines: list[RawLine], cfg: CleanConfig
                   ) -> tuple[list[Sentence], list[tuple[str, int, str, str]]]:
    """Trả về (sentences, dropped_sentence_rows)."""
    dropped: list[tuple[str, int, str, str]] = []
    sentences: list[Sentence] = []

    def emit(text: str, idxs: list[int]) -> None:
        text = text.strip()
        if layer == "han":
            ok = sum(1 for c in text if cjk_ratio(c) > 0) >= 2
        else:
            ok = any(ch.isalnum() for ch in text)
        ids = sorted({lines[k].id for k in idxs})
        pages = sorted({lines[k].page for k in idxs})
        if not ok:
            dropped.append((",".join(ids), pages[0] if pages else 0, "too_short", text))
            return
        flags = []
        if len(pages) > 1:
            flags.append("cross_page")
        if layer == "han" and text[-1] not in HAN_TERMINATORS + HAN_CLOSERS:
            flags.append("no_terminal_punct")
        if layer != "han" and not re.search(r"[.?!…;:\"”’')»\]]$", text):
            flags.append("no_terminal_punct")
        if len(text) > (200 if layer == "han" else 600):
            flags.append("long")
        doc_ids = {_document_id(lines[k].id) for k in idxs}
        document_id = next(iter(doc_ids)) if len(doc_ids) == 1 else ""
        sentences.append(Sentence(f"{work_id}.{layer}.{len(sentences) + 1:05d}", text,
                                  pages, ids, flags, document_id=document_id))

    if cfg.split == "line":
        for k, ln in enumerate(lines):
            emit(ln.text, [k])
        return sentences, dropped

    runs: list[tuple[int, int]] = []
    start = 0
    for k in range(1, len(lines) + 1):
        if k == len(lines) or _document_id(lines[k].id) != _document_id(lines[start].id):
            runs.append((start, k))
            start = k
    for start, end in runs:
        group = lines[start:end]
        text, owner = build_stream(group, layer)
        spans = split_han(text) if layer == "han" else split_vi(text, cfg)
        for a, b in spans:
            idxs = sorted({start + o for o in owner[a:b] if o >= 0})
            if idxs:
                emit(text[a:b], idxs)
    return sentences, dropped
