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

from src.cleaning.metadata import classify_leading_block
from src.cleaning.model import MetadataConfig, RawLine
from src.segmentation.documents import review_row
from src.segmentation.model import Document, DocumentLayers, LayerLine, OcrLine
from src.segmentation.patterns import (
    DEFAULT_HEADING_PREFIX,
    compile_matcher,
    matched_labels,
)

DEFAULT_SOURCE = {"han": "chi_tra", "am": "vie", "nghia": "vie"}


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
            "text": line.text, "page": line.page, "writing_system": line.writing_system,
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
            if starts[n] and n not in started:
                review.append(review_row(work_id, doc.id, n, "missing_heading",
                                         doc.pages, ""))
            if ends[n] and n in started and current == n:
                review.append(review_row(work_id, doc.id, n, "missing_end_heading",
                                         doc.pages, ""))

    for name, (ws, _) in sources.items():
        if ws not in doc.lines:
            review.append(review_row(work_id, doc.id, name, "missing_source", doc.pages, ws))
        elif name not in grouped:
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
        raw = [RawLine(id=x.id, page=x.page, text=x.text, raw_text=x.text) for x in rows]
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
