"""Gán dòng của MỘT tài liệu vào các lớp (han/am/nghia) bằng state machine cấu hình.

- `segmentation.layers.<layer>.source`: writing system nguồn (chi_tra|vie).
- `start_patterns` / `end_patterns`: heading mở/đóng đoạn; heading KHÔNG vào dòng chính
  nhưng được giữ trong `section_headings` (metadata) cùng source_ids/pages.
- Không cấu hình pattern cho system chỉ có một lớp -> toàn bộ system thuộc lớp đó
  (`han` mặc định chi_tra; `am`/`nghia` mặc định vie).
- Dòng không gán được, thiếu heading, chồng lấn -> review; không đoán lặng lẽ.
- Metadata tiêu đề/tác giả/quy trách chạy SAU khi gán lớp, trên dòng đầu của từng lớp,
  dùng lại `src.cleaning.metadata.classify_leading_block` (một nguồn logic duy nhất).
"""

from __future__ import annotations

import re
import unicodedata

from src.cleaning.metadata import classify_leading_block
from src.cleaning.model import MetadataConfig, RawLine
from src.segmentation.documents import review_row
from src.segmentation.model import Document, DocumentLayers, LayerLine, OcrLine
from src.segmentation.patterns import (
    DEFAULT_HEADING_PREFIX,
    compile_matcher,
    compile_patterns,
    matched_labels,
    matches,
)

DEFAULT_SOURCE = {"han": "chi_tra", "am": "vie", "nghia": "vie", "tho": "vie"}


def _compile_layer_matcher(layer_cfg: dict, kind: str, name: str):
    case_sensitive = bool(layer_cfg.get("case_sensitive", False))
    mode = layer_cfg.get("heading_mode")
    heading = bool(mode) if not isinstance(mode, dict) else mode.get("enabled", True)
    if not heading:
        return compile_matcher(layer_cfg.get(kind), name, case_sensitive)
    prefix = layer_cfg.get("heading_prefix_pattern", DEFAULT_HEADING_PREFIX)
    if isinstance(mode, dict) and "prefix_pattern" in mode:
        prefix = mode["prefix_pattern"]
    return compile_matcher(layer_cfg.get(kind), name, case_sensitive,
                           heading=True, prefix_pattern=prefix or None)


def metadata_config(layer: str, layer_cfg: dict, meta_cfg: dict) -> MetadataConfig:
    merged = {k: v for k, v in (meta_cfg or {}).items()
              if k not in ("enabled", "exclude_from_main_text", "layers")}
    merged = {**merged, **(meta_cfg or {}).get("layers", {}).get(layer, {})}
    local = layer_cfg.get("metadata") if isinstance(layer_cfg.get("metadata"), dict) else {}
    merged = {**merged, **local}
    for key in ("title_patterns", "author_patterns", "attribution_patterns",
                "author_max_tokens", "author_max_len", "exclude_from_main_text"):
        if key in layer_cfg:
            merged[key] = layer_cfg[key]
    enabled = (meta_cfg or {}).get("enabled", True) and layer_cfg.get("enabled", True)
    enabled = enabled and layer_cfg.get("metadata", True) is not False
    return MetadataConfig(
        enabled=bool(enabled),
        exclude_from_main_text=merged.get("exclude_from_main_text",
                                          (meta_cfg or {}).get("exclude_from_main_text", True)),
        title_patterns=list(merged.get("title_patterns") or []),
        author_patterns=list(merged.get("author_patterns") or []),
        attribution_patterns=list(merged.get("attribution_patterns") or []),
        author_max_tokens=int(merged.get("author_max_tokens", 4)),
        author_max_len=int(merged.get("author_max_len", 24)),
    )


def _heading(work_id: str, doc: Document, layer: str, kind: str, line: OcrLine) -> dict:
    return {"work_id": work_id, "document_id": doc.id, "layer": layer, "kind": kind,
            "text": line.text, "page": line.page, "bbox": line.bbox,
            "writing_system": line.writing_system,
            "source_ids": [line.source_id]}


def assign_document(work_id: str, doc: Document, layer_cfg: dict, meta_cfg: dict
                    ) -> DocumentLayers:
    review: list[dict] = []
    headings: list[dict] = []
    grouped: dict[str, list[OcrLine]] = {}
    sources = {name: (lc.get("source") or DEFAULT_SOURCE.get(name, "vie"), lc)
               for name, lc in layer_cfg.items()}
    by_system: dict[str, list[str]] = {}
    for name, (ws, _) in sources.items():
        by_system.setdefault(ws, []).append(name)

    for ws, names in by_system.items():
        lines = doc.lines.get(ws, [])
        if not lines:
            continue
        starts = {n: _compile_layer_matcher(
            layer_cfg[n], "start_patterns", f"segmentation.layers.{n}.start_patterns")
                  for n in names}
        ends = {n: _compile_layer_matcher(
            layer_cfg[n], "end_patterns", f"segmentation.layers.{n}.end_patterns")
                for n in names}
        default = names[0] if len(names) == 1 and not starts[names[0]] else None
        current: str | None = None
        started: set[str] = set()
        for ln in lines:
            starts_hit = matched_labels(starts, ln.text)
            ends_hit = matched_labels(ends, ln.text)
            if len(starts_hit) > 1:
                review.append(review_row(work_id, doc.id, ws, "layer_overlap",
                                         [ln.page], ln.text, [ln.source_id]))
            if starts_hit:
                target = starts_hit[0]
                if ends_hit:
                    for n in ends_hit:
                        if n != target:
                            headings.append(_heading(work_id, doc, n, "end", ln))
                    if current not in (None, target) and current not in ends_hit:
                        review.append(review_row(work_id, doc.id, current,
                                                 "missing_end_heading", [ln.page], ln.text,
                                                 [ln.source_id]))
                elif current and current != target:
                    review.append(review_row(work_id, doc.id, current,
                                             "missing_end_heading", [ln.page], ln.text,
                                             [ln.source_id]))
                current = target
                started.add(target)
                headings.append(_heading(work_id, doc, target, "start", ln))
                continue
            if ends_hit:
                target = current if current in ends_hit else ends_hit[0]
                if current is None:
                    review.append(review_row(work_id, doc.id, target,
                                             "missing_start_heading", [ln.page], ln.text,
                                             [ln.source_id]))
                headings.append(_heading(work_id, doc, target, "end", ln))
                if current in ends_hit:
                    current = None
                continue
            target = current or default
            if target is None:
                review.append(review_row(work_id, doc.id, ws, "unassigned_line",
                                         [ln.page], ln.text, [ln.source_id]))
                continue
            grouped.setdefault(target, []).append(ln)
        for n in names:
            optional = bool(layer_cfg[n].get("optional"))
            if starts[n] and n not in started and not optional:
                review.append(review_row(work_id, doc.id, n, "missing_heading",
                                         doc.pages, ""))
            if ends[n] and n in started and current == n and not optional:
                review.append(review_row(work_id, doc.id, n, "missing_end_heading",
                                         doc.pages, ""))

    for name, (ws, _) in sources.items():
        optional = bool(layer_cfg[name].get("optional"))
        if ws not in doc.lines:
            if not optional:
                review.append(review_row(work_id, doc.id, name, "missing_source", doc.pages, ws))
        elif name not in grouped and not optional:
            review.append(review_row(work_id, doc.id, name, "missing_layer", doc.pages, ""))

    lines_by_layer: dict[str, list[LayerLine]] = {}
    for layer, items in grouped.items():
        prefix = "S" if layer == "han" else "L"
        ordered = sorted(items, key=lambda x: (x.page, x.index))
        rows = []
        for seq, ln in enumerate(ordered, 1):
            rows.append(LayerLine(
                id=f"{doc.id}.P{ln.page:03d}.{prefix}{seq:03d}", document_id=doc.id,
                layer=layer, page=ln.page, text=ln.text, bbox=ln.bbox,
                confidence=ln.confidence, writing_system=ln.writing_system,
                source_ids=[ln.source_id]))
        lines_by_layer[layer] = rows

    metadata_rows: list[dict] = []
    uncertain: list[dict] = []
    for layer, rows in list(lines_by_layer.items()):
        cfg = metadata_config(layer, layer_cfg[layer], meta_cfg or {})
        raw = [RawLine(id=x.id, page=x.page, text=x.text, raw_text=x.text,
                       source_ids=list(x.source_ids), document_id=x.document_id) for x in rows]
        kept, md = classify_leading_block(raw, cfg, layer, doc.id)
        kept_ids = {r.id for r in kept}
        lines_by_layer[layer] = [x for x in rows if x.id in kept_ids]
        if not lines_by_layer[layer]:
            del lines_by_layer[layer]
        row = md.to_row(work_id, layer)
        metadata_rows.append(row)
        for u in row.get("uncertain", []):
            uncertain.append(u)
            review.append(review_row(work_id, doc.id, layer,
                                     "metadata_uncertain", u.get("pages", []),
                                     u.get("text", ""), u.get("source_ids", [])))

    return DocumentLayers(lines=lines_by_layer, headings=headings,
                          metadata_rows=metadata_rows, uncertain=uncertain, review=review)


# --- UNIFIED (Single-stream) LAYER SEGMENTATION ---

_TITLE_SUFFIX_RE = re.compile(r"\s*[\(\[]\s*\w{1,3}\s*[\)\]]\s*$")


def is_upper_title(text: str) -> bool:
    """Dòng tiêu đề Việt viết hoa toàn bộ (vd "LÊN NÚI MẪU TỬ (1)", "THỜ MÙNG MƯA"): >= 2 chữ cái Latin, không
    chữ thường, <= 12 từ, không kết thúc bằng dấu phẩy/chấm phẩy (câu thơ viết hoa nhầm)."""
    probe = _TITLE_SUFFIX_RE.sub("", text.strip())
    letters = [c for c in probe if c.isalpha() and unicodedata.name(c, "").startswith("LATIN")]
    if len(letters) < 2 or any(c.islower() for c in letters):
        return False
    return len(probe.split()) <= 12 and not probe.endswith((",", ";"))


def assign_document_unified(work_id: str, doc: Document, layer_cfg: dict, meta_cfg: dict,
                            keep_line=None, dropped: dict[str, int] | None = None,
                            implicit_next: dict[str, str] | None = None,
                            is_header=None, title_next: dict[str, str] | None = None,
                            notes_after_attribution: set[str] | frozenset[str] = frozenset()) -> DocumentLayers:
    """Gán dòng của MỘT tài liệu vào các lớp (han/am/nghia/tho) từ stream OCR duy nhất.

    - Không phụ thuộc vào 'source' (chi_tra/vie).
    - Duyệt tuần tự các dòng theo thứ tự đọc ban đầu bằng state machine.
    - Tách riêng dòng heading vào `section_headings`.
    - Hỗ trợ các lớp tuỳ chọn (optional: true) và bắt lỗi dòng không gán được vào review.
    - `keep_line(layer, line) -> bool` (tuỳ chọn): bỏ dòng đã gán lớp nhưng không thuộc lớp đó (vd chữ Hán trong
      khảo dị khi chạy hai luồng han/vie), TRƯỚC khi trích metadata; số dòng bỏ cộng vào `dropped[layer]`.
    - `implicit_next` (tuỳ chọn, cần `keep_line`): vd {"han": "am"}. Đang ở lớp han mà gặp dòng không thuộc
      han nhưng thuộc am, và am CHƯA bắt đầu trong tài liệu -> coi như mở lớp am không có tiêu đề mục (sách in
      phần phiên âm ngay sau chữ Hán, không có chữ "PHIÊN ÂM"). Ghi review `implicit_layer_start`.
    - `title_next` (tuỳ chọn), vd {"am": "nghia"}: đang ở am (hoặc vừa rời am để vào chú thích), am đã có ít nhất
      một dòng nội dung KHÔNG viết hoa, gặp dòng tiêu đề viết hoa toàn bộ -> mở lớp kế tiếp (sách in tiêu đề dịch
      "LÊN NÚI MẪU TỬ" mà OCR làm mất chữ "DỊCH NGHĨA"). Review `implicit_layer_start`.
    - `notes_after_attribution` (tuỳ chọn), vd {"am"}: trong lớp đó, sau dòng khớp `attribution_patterns` (dòng
      nguồn "Trích ..."), các dòng tiếp theo là chú thích: không gán lớp cho tới tiêu đề/tiêu đề viết hoa kế tiếp.
    """
    review: list[dict] = []
    headings: list[dict] = []
    grouped: dict[str, list[OcrLine]] = {}

    all_lines = doc.all_lines if hasattr(doc, "all_lines") else (
        doc.lines if isinstance(doc.lines, list) else
        sorted([ln for lns in doc.lines.values() for ln in lns], key=lambda x: (x.page, x.index))
    )

    if not all_lines:
        return DocumentLayers(lines={}, headings=[], metadata_rows=[], uncertain=[], review=[])

    names = list(layer_cfg.keys())
    starts = {n: _compile_layer_matcher(
        layer_cfg[n], "start_patterns", f"segmentation.layers.{n}.start_patterns")
        for n in names}
    ends = {n: _compile_layer_matcher(
        layer_cfg[n], "end_patterns", f"segmentation.layers.{n}.end_patterns")
        for n in names}

    # Lớp khởi đầu mặc định: lớp đầu tiên không có start_patterns bắt buộc (thường là 'han')
    default_start = None
    for n in names:
        if not starts[n]:
            default_start = n
            break
    if default_start is None and names:
        default_start = names[0]

    current: str | None = default_start
    started: set[str] = {default_start} if default_start else set()
    body_lines: dict[str, int] = {}              # số dòng nội dung không viết hoa đã gán cho mỗi lớp
    last_layer: str | None = current             # lớp nội dung gần nhất (để thoát khỏi đoạn chú thích)
    attribution = {n: compile_patterns(layer_cfg[n].get("attribution_patterns"),
                                       f"segmentation.layers.{n}.attribution_patterns", False)
                   for n in notes_after_attribution if n in layer_cfg}

    for ln in all_lines:
        starts_hit = matched_labels(starts, ln.text)
        ends_hit = matched_labels(ends, ln.text)

        if len(starts_hit) > 1:
            review.append(review_row(work_id, doc.id, "", "layer_overlap",
                                     [ln.page], ln.text, [ln.source_id]))

        if starts_hit:
            target = starts_hit[0]
            is_heading = bool(layer_cfg.get(target, {}).get("heading_mode", False))
            if is_heading:
                if current and current != target:
                    for n in ends_hit:
                        if n != target:
                            h_info = _heading(work_id, doc, n, "end", ln)
                            h_info["heading_id"] = f"{doc.id}.P{ln.page:04d}.H{len(headings) + 1:03d}"
                            headings.append(h_info)
                current = target
                started.add(target)
                h_info = _heading(work_id, doc, target, "start", ln)
                h_info["heading_id"] = f"{doc.id}.P{ln.page:04d}.H{len(headings) + 1:03d}"
                headings.append(h_info)
                continue
            else:
                current = target
                started.add(target)
                # Dòng nội dung (như tiêu đề thơ Hán không dùng heading_mode): giữ trong lớp
                grouped.setdefault(target, []).append(ln)
                continue

        if ends_hit:
            target = current if current in ends_hit else ends_hit[0]
            if current is None:
                review.append(review_row(work_id, doc.id, target,
                                         "missing_start_heading", [ln.page], ln.text,
                                         [ln.source_id]))
            h_info = _heading(work_id, doc, target, "end", ln)
            h_info["heading_id"] = f"{doc.id}.P{ln.page:04d}.H{len(headings) + 1:03d}"
            headings.append(h_info)
            if current in ends_hit:
                current = None
            continue

        if (implicit_next and keep_line is not None and current in implicit_next
                and not keep_line(current, ln) and not (is_header and is_header(ln))):
            nxt = implicit_next[current]
            if nxt not in started and keep_line(nxt, ln):
                review.append(review_row(work_id, doc.id, nxt, "implicit_layer_start",
                                         [ln.page], ln.text, [ln.source_id]))
                current = nxt
                started.add(nxt)

        if title_next and is_upper_title(ln.text) and not (is_header and is_header(ln)):
            prev = current or last_layer
            nxt = title_next.get(prev or "")
            if (nxt and nxt not in started and body_lines.get(prev, 0) > 0
                    and (keep_line is None or keep_line(nxt, ln))):
                review.append(review_row(work_id, doc.id, nxt, "implicit_layer_start",
                                         [ln.page], ln.text, [ln.source_id]))
                current = nxt
                started.add(nxt)

        target = current
        if target is None:
            review.append(review_row(work_id, doc.id, "", "unassigned_line",
                                     [ln.page], ln.text, [ln.source_id]))
            continue
        grouped.setdefault(target, []).append(ln)
        last_layer = target
        # dòng nội dung = có chữ thường (tiêu đề viết hoa nhiều dòng, kể cả dòng kết thúc bằng dấu phẩy, không tính)
        if any(c.islower() for c in ln.text) and (keep_line is None or keep_line(target, ln)):
            body_lines[target] = body_lines.get(target, 0) + 1
        if (target in attribution and body_lines.get(target, 0) > 0
                and matches(attribution[target], ln.text)):
            current = None                       # sau dòng nguồn: chú thích, không gán lớp

    if keep_line is not None:
        for layer in list(grouped):
            kept = [ln for ln in grouped[layer] if keep_line(layer, ln)]
            if dropped is not None and len(kept) != len(grouped[layer]):
                dropped[layer] = dropped.get(layer, 0) + len(grouped[layer]) - len(kept)
            if kept:
                grouped[layer] = kept
            else:
                del grouped[layer]

    for n in names:
        optional = bool(layer_cfg[n].get("optional"))
        if starts[n] and n not in started and not optional:
            review.append(review_row(work_id, doc.id, n, "missing_heading",
                                     doc.pages, ""))
        elif n not in grouped and not optional:
            review.append(review_row(work_id, doc.id, n, "missing_layer", doc.pages, ""))

    lines_by_layer: dict[str, list[LayerLine]] = {}
    for layer, items in grouped.items():
        prefix = "S" if layer == "han" else "L"
        ordered = sorted(items, key=lambda x: (x.page, x.index))
        rows = []
        for seq, ln in enumerate(ordered, 1):
            rows.append(LayerLine(
                id=f"{doc.id}.P{ln.page:04d}.{prefix}{seq:03d}", document_id=doc.id,
                layer=layer, page=ln.page, text=ln.text, bbox=ln.bbox,
                confidence=ln.confidence, writing_system=ln.writing_system,
                source_ids=[ln.source_id]))
        lines_by_layer[layer] = rows

    metadata_rows: list[dict] = []
    uncertain: list[dict] = []
    for layer, rows in list(lines_by_layer.items()):
        cfg_meta = metadata_config(layer, layer_cfg.get(layer, {}), meta_cfg or {})
        raw = [RawLine(id=x.id, page=x.page, text=x.text, raw_text=x.text,
                       source_ids=list(x.source_ids), document_id=x.document_id) for x in rows]
        kept, md = classify_leading_block(raw, cfg_meta, layer, doc.id)
        kept_ids = {r.id for r in kept}
        lines_by_layer[layer] = [x for x in rows if x.id in kept_ids]
        if not lines_by_layer[layer]:
            del lines_by_layer[layer]
        row = md.to_row(work_id, layer)
        metadata_rows.append(row)
        for u in row.get("uncertain", []):
            uncertain.append(u)
            review.append(review_row(work_id, doc.id, layer,
                                     "metadata_uncertain", u.get("pages", []),
                                     u.get("text", ""), u.get("source_ids", [])))

    return DocumentLayers(lines=lines_by_layer, headings=headings,
                          metadata_rows=metadata_rows, uncertain=uncertain, review=review)
