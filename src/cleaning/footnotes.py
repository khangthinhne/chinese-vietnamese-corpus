"""Bước 5: phát hiện chú thích / trích dẫn nguồn / chỉ mục chú thích trong dòng.

Ba nhóm:
1. Chú thích ngoặc trọn kéo theo nhiều dòng (kể cả qua trang): mở ngoặc ở đầu dòng,
   đóng ở cuối dòng nào đó — mọi dòng nằm trọn trong ngoặc thuộc chú thích. Chạy trên
   danh sách dòng theo thứ tự nên tự nhiên hỗ trợ qua trang.
2. Heuristic cũ (tương thích): dòng bắt đầu "*", "＊", "[N]" (lớp Việt); dòng bọc ngoặc
   trọn trong MỘT dòng sẽ do filtering tự xử lý bằng is_parenthesized như cũ.
3. Mục chú thích đánh số "(1) ...", "（2） ...", "[1] ...": bắt đầu một chú thích nhiều
   dòng, nuốt các dòng kế tiếp cho tới khi gặp dòng trọn câu / mục kế / hết dữ liệu.
   Chỉ nổ khi dòng TRƯỚC đã trọn câu (có : ; . ? ! … ) ” ’ ]) — tránh nhầm ngoặc nội
   dung chính đang dang dở.

Chú thích mở ngoặc không đóng tới hết dữ liệu hoặc đóng giữa dòng còn chữ đuôi ->
uncertain_note: KHÔNG loại, chỉ trình review (nguyên tắc: không chắc thì giữ lại).
[Phật], [muôn], [người] không bị đụng tới (marker chỉ nhận CHỮ SỐ).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from src.cleaning.model import (BIB_NOTE, EDITORIAL_NOTE, FOOTNOTE, UNCERTAIN_NOTE,
                                CleanConfig, FootnoteConfig, RawLine)

OPEN_PAREN = "（(﹙"
CLOSE_PAREN = "）)﹚"
VI_COMPLETE = re.compile(r"[.?!…;:\"”’')﹚\]]$")   # dòng đã trọn câu (gồm ':')
HAN_COMPLETE_CH = "。！？；﹖﹕﹒」』”’）﹚"


@dataclass
class Footnote:
    note_id: str
    category: str                      # footnote | bibliographic_note | editorial_note | uncertain_note
    action: str = "exclude"            # exclude | preserve | review
    reason: str = "footnote"
    pages: list[int] = field(default_factory=list)
    line_ids: list[str] = field(default_factory=list)
    text: str = ""
    lines: list[RawLine] = field(default_factory=list)
    confidence: str = "high"           # high | low (low = heuristic hoặc chú thích chưa đóng ngoặc)

    def to_record(self) -> dict:
        return {"note_id": self.note_id, "category": self.category, "action": self.action,
                "reason": self.reason, "pages": self.pages, "line_ids": self.line_ids,
                "confidence": self.confidence,
                "lines": [{"id": ln.id, "page": ln.page, "text": ln.text} for ln in self.lines]}


@dataclass
class FootnoteDetectionResult:
    notes: list[Footnote] = field(default_factory=list)
    excluded_ids: set[str] = field(default_factory=set)


def _complete_han(text: str) -> bool:
    return bool(text) and text[-1] in HAN_COMPLETE_CH


def _complete_vi(text: str) -> bool:
    return bool(VI_COMPLETE.search(text.strip()))


def _complete(text: str, layer: str) -> bool:
    return _complete_han(text) if layer == "han" else _complete_vi(text)


def _prev_complete(prev: RawLine | None, layer: str) -> bool:
    return True if prev is None else _complete(prev.text, layer)


def detect_footnotes(lines: list[RawLine], cfg: FootnoteConfig, layer: str,
                     note_prefix: str = "FN") -> FootnoteDetectionResult:
    result = FootnoteDetectionResult()
    if not cfg.enabled:
        return result
    consumed: set[int] = set()         # chỉ số dòng đã thuộc một chú thích (kể cả được giữ)
    bib_res = [re.compile(p, re.IGNORECASE) for p in cfg.bib_patterns] if cfg.detect_bibliographic else []
    item_res = [re.compile(p) for p in cfg.item_patterns]
    counter = 0

    def is_bib(text: str) -> bool:
        return any(r.search(text) for r in bib_res)

    def make(cat: str, idxs: list[int], confidence: str = "high") -> Footnote:
        nonlocal counter
        counter += 1
        lns = [lines[i] for i in idxs]
        fn = Footnote(note_id=f"{note_prefix}{counter:03d}", category=cat, reason=cat,
                      pages=sorted({ln.page for ln in lns}),
                      line_ids=[ln.id for ln in lns], text=" ".join(ln.text for ln in lns),
                      lines=lns, confidence=confidence)
        fn.action = "review" if cat == UNCERTAIN_NOTE else cfg.policy
        if fn.action == "exclude":
            result.excluded_ids.update(ln.id for ln in lns)
        result.notes.append(fn)
        return fn

    # ---- 1. chú thích ngoặc trọn, kéo nhiều dòng / qua trang ----
    if cfg.detect_parentheses:
        i = 0
        while i < len(lines):
            if i in consumed:
                i += 1
                continue
            if lines[i].text.strip()[:1] not in OPEN_PAREN:
                i += 1
                continue
            # quét tới khi ngoặc đóng (cuối dòng) hoặc hết dữ liệu
            idxs: list[int] = []
            depth, closed_at, j = 0, None, i
            while j < len(lines) and j not in consumed:
                idxs.append(j)
                for pos, ch in enumerate(lines[j].text):
                    if ch in OPEN_PAREN:
                        depth += 1
                    elif ch in CLOSE_PAREN:
                        depth -= 1
                        if depth == 0:
                            closed_at = (j, pos)
                            break
                if closed_at is not None:
                    break
                j += 1
            if closed_at is not None:
                j_close, pos_close = closed_at
                tail = lines[j_close].text[pos_close + 1:].strip()
                if tail:
                    # mở và đóng ngay trong dòng, sau ngoặc còn chữ ("(1) Pháp sư : ..."):
                    # là ngoặc nội dung chính / chỉ mục trong dòng -> không phải chú thích,
                    # chừa nguyên cho bước mục chú thích (3) và artifacts xử lý tiếp
                    idxs = []
                    i += 1
                    continue
                # Kiểm tra các dòng kế tiếp có phải là phần nối tiếp của chú thích/trích dẫn này
                next_j = j_close + 1
                while next_j < len(lines) and next_j not in consumed:
                    next_t = lines[next_j].text.strip()
                    if not next_t:
                        break
                    next_open = sum(next_t.count(c) for c in OPEN_PAREN)
                    next_close = sum(next_t.count(c) for c in CLOSE_PAREN)
                    is_cont = False
                    if next_close > next_open and (next_t[-1] in CLOSE_PAREN or is_bib(next_t)):
                        is_cont = True
                    elif layer != "han" and next_t[:1].islower() and (is_bib(next_t) or next_t[-1] in CLOSE_PAREN):
                        is_cont = True
                    if is_cont:
                        idxs.append(next_j)
                        next_j += 1
                    else:
                        break
                j_close = idxs[-1]
                closed_at = (j_close, len(lines[j_close].text) - 1)
            consumed.update(idxs)
            if closed_at is None:
                # mở ngoặc không bao giờ đóng: KHÔNG loại, trình review
                make(UNCERTAIN_NOTE, idxs, confidence="low")
            else:
                text = " ".join(lines[k].text for k in idxs)
                if len(idxs) > 1:
                    make(BIB_NOTE if is_bib(text) else EDITORIAL_NOTE, idxs)
                else:
                    # 1 dòng trọn ngoặc: xử lý như cũ (reason "footnote"), kể cả khi nội dung
                    # giống trích dẫn nguồn — giữ tương thích reason trong dropped.tsv
                    make(FOOTNOTE, idxs)
            i = (closed_at[0] if closed_at else idxs[-1]) + 1

    # ---- 2+3. heuristic cũ, trích dẫn không mở ngoặc đầu dòng và mục chú thích đánh số ----
    last_kept: RawLine | None = None
    for i, ln in enumerate(lines):
        if i in consumed:
            continue
        probe = ln.text.strip()
        open_c = sum(probe.count(c) for c in OPEN_PAREN)
        close_c = sum(probe.count(c) for c in CLOSE_PAREN)

        # Trích dẫn nguồn kết thúc bằng ngoặc đóng đơn lẻ hoặc chứa mẫu thư mục Hán rõ ràng
        if cfg.detect_bibliographic and is_bib(probe):
            if (close_c > open_c and probe[-1] in CLOSE_PAREN) or (layer == "han" and re.search(r"[集卷]\s*([0-9一二三四五六七八九十百]+|第)", probe)):
                consumed.add(i)
                make(BIB_NOTE, [i])
                continue

        if layer != "han" and (probe.startswith(("*", "＊")) or re.match(r"^\s*\[\d{1,3}\]", probe)):
            consumed.add(i)
            make(FOOTNOTE, [i])
            continue
        if _prev_complete(last_kept, layer) and any(r.search(probe) for r in item_res):
            idxs, j = [i], i
            while (j + 1 < len(lines) and (j + 1) not in consumed
                   and not _complete(lines[j].text, layer)
                   and not any(r.search(lines[j + 1].text.strip()) for r in item_res)):
                j += 1
                idxs.append(j)
            unterminated = j == len(lines) - 1 and not _complete(lines[j].text, layer)
            consumed.update(idxs)
            make(FOOTNOTE, idxs, confidence="low" if unterminated else "high")
            last_kept = None
            continue
        last_kept = ln
    return result
