"""Cắt tài liệu từ kho OCR thô theo SYSTEM, không theo lớp Hán/âm/nghĩa.

Ranh giới tài liệu lấy từ writing system CHÍNH (chi_tra nếu có, ngược lại vie) bằng
`segmentation.document.start_patterns` / `end_patterns`; tài liệu của system còn lại
được ghép theo TRANG (hai system OCR cùng bộ trang PDF). Không đủ tin cậy -> review,
không bao giờ đoán lặng lẽ.
"""

from __future__ import annotations

from src.common.corpus import DataLayout, filter_lines
from src.ocr import raw_store
from src.segmentation.model import Document, OcrLine
from src.segmentation.patterns import compile_patterns, matches

PRIMARY_ORDER = ("chi_tra", "vie")
NUMBERED_HEADING_PATTERN = r"^\s*(\d{1,3})\s*[.．﹒·。]\s+"


def start_patterns(cfg: dict) -> list[str]:
    """start_patterns tường minh + quy ước numbered heading (nếu bật trong config)."""
    patterns = list(cfg.get("start_patterns") or [])
    numbered = cfg.get("numbered_heading")
    if numbered is True:
        patterns.insert(0, NUMBERED_HEADING_PATTERN)
    elif isinstance(numbered, str):
        patterns.insert(0, numbered)
    elif isinstance(numbered, dict) and numbered.get("enabled", True):
        patterns.insert(0, numbered.get("pattern") or NUMBERED_HEADING_PATTERN)
    return patterns


def review_row(work_id: str, document_id: str, layer: str, reason: str,
               pages: list[int] | None = None, text: str = "",
               source_ids: list[str] | None = None) -> dict:
    return {"work_id": work_id, "document_id": document_id, "layer": layer,
            "reason": reason, "pages": sorted(set(pages or [])), "text": text,
            "source_ids": list(source_ids or [])}


def load_writing_system(layout: DataLayout, writing_system: str, pages: list[int]) -> list[OcrLine]:
    manifest = raw_store.load_manifest(layout, writing_system)
    if manifest is None:
        return []
    keep = manifest.get("keep")
    page_records = {p["page"]: p for p in manifest.get("pages", [])}
    out: list[OcrLine] = []
    for page in pages:
        rec = page_records.get(page)
        if rec is None:
            continue
        store = raw_store.load_page(layout, writing_system, page, layout.work_id)
        run = next((r for r in store["runs"] if r.get("key") == rec.get("selected_key")), None)
        if run is None:
            raise ValueError(f"Manifest {writing_system} trang {page} trỏ tới run không tồn tại")
        for index, item in enumerate(run.get("lines", []), 1):
            text = item.get("text", "")
            if keep and not filter_lines([text], keep):
                continue
            bbox = item.get("bbox")
            out.append(OcrLine(page=page, index=index, text=text,
                               bbox=tuple(bbox) if bbox else None,
                               confidence=item.get("confidence"), writing_system=writing_system,
                               engine=rec.get("selected_engine", ""),
                               cache_key=rec.get("selected_key", ""),
                               image=rec.get("image", "")))
    return out


def _page_span(all_pages: list[int], start_page: int, next_page: int | None) -> list[int]:
    if not all_pages:
        return [start_page] if next_page is None else [start_page, next_page - 1]
    if start_page not in all_pages:
        return [p for p in all_pages if p >= start_page
                and (next_page is None or p < next_page)]
    i0 = all_pages.index(start_page)
    i1 = len(all_pages) - 1 if next_page is None else all_pages.index(next_page) - 1
    return all_pages[i0:max(i0, i1) + 1]


def detect_spans(work_id: str, primary: str, lines: list[OcrLine], cfg: dict,
                 review: list[dict], all_pages: list[int]) -> list[dict]:
    case_sensitive = bool(cfg.get("case_sensitive", False))
    configured = start_patterns(cfg)
    start_res = compile_patterns(configured, "segmentation.document.start_patterns",
                                 case_sensitive)
    end_res = compile_patterns(cfg.get("end_patterns"), "segmentation.document.end_patterns",
                               case_sensitive)
    starts = [i for i, ln in enumerate(lines) if matches(start_res, ln.text)]
    explicit = bool(starts)
    if not starts:
        starts = [0]
        if configured:
            review.append(review_row(work_id, "", "", "document_heading_not_found",
                                     all_pages, lines[0].text if lines else ""))
        elif len(all_pages) > 1:
            review.append(review_row(work_id, "", "", "document_boundary_assumed",
                                     all_pages, lines[0].text if lines else ""))
    else:
        for j in range(0, starts[0]):
            ln = lines[j]
            review.append(review_row(work_id, "", primary, "unassigned_line", [ln.page],
                                     ln.text, [ln.source_id]))
    spans = []
    for k, start in enumerate(starts):
        limit = starts[k + 1] if k + 1 < len(starts) else len(lines)
        end = limit
        trailing: list[int] = []
        for j in range(start, limit):
            if matches(end_res, lines[j].text):
                end = j + 1
                trailing = list(range(j + 1, limit))
                break
        page_start = lines[start].page
        next_page = lines[starts[k + 1]].page if k + 1 < len(starts) else None
        heading = None
        if explicit:
            line = lines[start]
            heading = {"text": line.text, "page": line.page,
                       "writing_system": line.writing_system,
                       "source_ids": [line.source_id]}
        spans.append({"start": start, "end": end,
                      "pages": _page_span(all_pages, page_start, next_page),
                      "unassigned": trailing, "heading": heading})
    for k, sp in enumerate(spans):
        for j in sp["unassigned"]:
            ln = lines[j]
            review.append(review_row(work_id, "", primary, "unassigned_line", [ln.page],
                                     ln.text, [ln.source_id]))
        # dòng giữa end marker và start kế tiếp (nếu end < limit) cũng là unassigned
        if sp["end"] < (spans[k + 1]["start"] if k + 1 < len(spans) else len(lines)):
            for j in range(sp["end"], spans[k + 1]["start"] if k + 1 < len(spans) else len(lines)):
                if j in sp["unassigned"]:
                    continue
                ln = lines[j]
                review.append(review_row(work_id, "", primary, "unassigned_line", [ln.page],
                                         ln.text, [ln.source_id]))
    return spans


def map_stream(work_id: str, writing_system: str, lines: list[OcrLine],
               spans: list[dict], primary_lines: list[OcrLine],
               primary_doc: list[int | None], review: list[dict]) -> list[list[OcrLine]]:
    """Ghép dòng system phụ vào tài liệu.

    Ưu tiên cùng TRANG: nếu trang có nhiều tài liệu (heading khác nhau trên một trang),
    chia dòng theo tỉ lệ số dòng của system chính trên trang đó. Chỉ khi system chính
    không có dòng nào trên trang mới lùi về khoảng trang (và gắn review nếu mơ hồ).
    """
    from collections import defaultdict

    buckets: list[list[OcrLine]] = [[] for _ in spans]
    page_lines: dict[int, list[OcrLine]] = defaultdict(list)
    for ln in lines:
        page_lines[ln.page].append(ln)
    primary_by_page: dict[int, list[int | None]] = defaultdict(list)
    for idx, ln in enumerate(primary_lines):
        primary_by_page[ln.page].append(primary_doc[idx])

    for page, page_items in page_lines.items():
        docs_on_page = [d for d in primary_by_page.get(page, []) if d is not None]
        if docs_on_page:
            counts: dict[int, int] = {}
            for d in docs_on_page:
                counts[d] = counts.get(d, 0) + 1
            edges, cumulative = [], 0
            for d in sorted(counts):
                cumulative += counts[d]
                edges.append((d, cumulative / len(docs_on_page)))
            if len(edges) > 1:
                review.append(review_row(work_id, "", writing_system,
                                         "document_match_estimated", [page],
                                         page_items[0].text, []))
            for pos, ln in enumerate(page_items):
                fraction = (pos + 0.5) / len(page_items)
                doc_idx = next((d for d, edge in edges if fraction <= edge), edges[-1][0])
                buckets[doc_idx].append(ln)
            continue
        for ln in page_items:
            candidates = [i for i, sp in enumerate(spans) if page in sp["pages"]]
            if not candidates:
                review.append(review_row(work_id, "", writing_system, "unassigned_line",
                                         [page], ln.text, [ln.source_id]))
                continue
            if len(candidates) > 1:
                review.append(review_row(work_id, "", writing_system,
                                         "document_match_ambiguous", [page], ln.text,
                                         [ln.source_id]))
            buckets[candidates[0]].append(ln)
    return buckets


def segment_documents(work_id: str, streams: dict[str, list[OcrLine]], cfg: dict,
                      pages: list[int]) -> tuple[list[Document], list[dict]]:
    review: list[dict] = []
    primary = next((ws for ws in PRIMARY_ORDER if streams.get(ws)), None)
    if primary is None:
        return [], [review_row(work_id, "", "", "no_lines", [], "")]
    primary_lines = streams[primary]
    spans = detect_spans(work_id, primary, primary_lines, cfg, review, pages)
    primary_doc: list[int | None] = [None] * len(primary_lines)
    for k, sp in enumerate(spans):
        for j in range(sp["start"], sp["end"]):
            primary_doc[j] = k
    per_system = {primary: [primary_lines[sp["start"]:sp["end"]] for sp in spans]}
    for ws, lines in streams.items():
        if ws == primary or not lines:
            continue
        per_system[ws] = map_stream(work_id, ws, lines, spans, primary_lines, primary_doc,
                                    review)
    docs: list[Document] = []
    for i, sp in enumerate(spans, 1):
        doc_id = f"{work_id}.{i:03d}"
        doc_lines = {ws: buckets[i - 1] for ws, buckets in per_system.items() if buckets[i - 1]}
        doc_pages = sorted({ln.page for lines in doc_lines.values() for ln in lines})
        if not doc_lines and sp["pages"]:
            review.append(review_row(work_id, doc_id, "", "document_empty", sp["pages"], ""))
            continue
        docs.append(Document(id=doc_id, pages=doc_pages, lines=doc_lines,
                             heading=sp.get("heading")))
    return docs, review
