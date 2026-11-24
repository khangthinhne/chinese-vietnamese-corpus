"""Orchestrator for the refactored CPO_19 post-OCR pipeline.

The orchestrator composes the stage modules and keeps every intermediate record
needed for provenance and review:

    raw lines
      -> preprocessing (normalization / marker correction / cleaning / script)
      -> boundary detection (candidates, front matter)
      -> document construction
      -> sequential section assignment per document
      -> layer aggregation + line accounting + review rows

It does not write files itself; :mod:`src.segmentation.pipeline` serializes the
returned records.  The raw OCR text is never modified.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

from src.segmentation.boundaries import (BoundaryConfig, BoundaryResult, detect_boundaries)
from src.segmentation.documents import review_row
from src.segmentation.headings import HeadingConfig
from src.segmentation.markers import MarkerConfig
from src.segmentation.model import Document, LayerLine, OcrLine
from src.segmentation.preprocessing import (PreprocessConfig, PreprocessResult,
                                            preprocess_stream)
from src.segmentation.sections import (assign_document_sections, section_layer_map)


@dataclass
class RefactorConfig:
    preprocess: PreprocessConfig = field(default_factory=PreprocessConfig)
    boundary: BoundaryConfig = field(default_factory=BoundaryConfig)
    markers: MarkerConfig = field(default_factory=MarkerConfig)
    headings: HeadingConfig = field(default_factory=HeadingConfig)
    layer_map: dict[str, str] = field(default_factory=section_layer_map)
    low_confidence_threshold: float | None = None

    @classmethod
    def from_config(cls, seg_cfg: dict | None) -> "RefactorConfig":
        seg_cfg = seg_cfg or {}
        validation = seg_cfg.get("validation") or {}
        threshold = validation.get("low_conf")
        if threshold is None:
            threshold = seg_cfg.get("low_conf")
        return cls(
            preprocess=PreprocessConfig.from_config(seg_cfg),
            boundary=BoundaryConfig.from_config(seg_cfg),
            markers=MarkerConfig.from_config(seg_cfg),
            headings=HeadingConfig.from_config(seg_cfg.get("headings")),
            layer_map=section_layer_map(seg_cfg),
            low_confidence_threshold=(float(threshold) if threshold is not None else None),
        )


@dataclass
class RefactorResult:
    documents: list[Document]
    layer_rows: dict[str, list[LayerLine]]
    headings: list[dict]
    subworks: list[dict]
    document_rows: list[dict]
    review: list[dict]
    cleaning_log: list[dict]
    correction_log: list[dict]
    boundary: BoundaryResult
    all_lines: list[OcrLine]
    kept_lines: list[OcrLine]
    line_accounting: dict
    stats: dict
    marker_stats: dict
    section_stats: dict


def _front_matter_assignment(work_id: str, doc: Document,
                             layer: str) -> tuple[dict[str, list[LayerLine]], list[dict]]:
    rows: list[LayerLine] = []
    for seq, line in enumerate(sorted(doc.all_lines, key=lambda x: (x.page, x.index)), 1):
        line.document_id = doc.id
        line.section = "front_matter"
        line.subwork_id = ""
        rows.append(LayerLine(
            id=f"{doc.id}.P{line.page:04d}.L{seq:03d}", document_id=doc.id,
            layer=layer, page=line.page, text=line.text, bbox=line.bbox,
            confidence=line.confidence, writing_system=line.writing_system,
            source_ids=[line.source_id], flags=["front_matter"]))
    return ({layer: rows} if rows else {}), []


def _document_heading_row(work_id: str, doc: Document, heading: dict | None) -> dict:
    return {"work_id": work_id, "document_id": doc.id, "layer": "document",
            "kind": "document_start", "text": (heading or {}).get("text", ""),
            "page": (heading or {}).get("page"), "bbox": None,
            "writing_system": (heading or {}).get("writing_system", ""),
            "source_ids": list((heading or {}).get("source_ids") or [])}


def _review_from_preprocessing(work_id: str, pre: PreprocessResult) -> list[dict]:
    rows: list[dict] = []
    for line in pre.all_lines:
        if (line.cleaning or {}).get("action") == "excluded":
            continue
        reasons = list(line.review_flags)
        if line.cleaning.get("status") == "pending_review" and line.cleaning.get("reason"):
            for reason in str(line.cleaning["reason"]).split(","):
                if reason and reason not in reasons:
                    reasons.append(reason)
        if not reasons:
            continue
        rows.append(review_row(work_id, "", "", "preprocessing_flagged",
                               [line.page], line.text, [line.source_id]))
    for record in pre.cleaning_log:
        if record.get("reason") == "page_number_candidate":
            rows.append(review_row(work_id, "", "", "page_number_candidate",
                                   [record["page"]], record["original_text"],
                                   [record["line_id"]]))
    return rows


def _line_accounting(work_id: str, all_lines: list[OcrLine], headings: list[dict],
                     review: list[dict]) -> dict:
    heading_ids = {sid for row in headings for sid in (row.get("source_ids") or [])}
    records: list[dict] = []
    by_category: Counter[str] = Counter()
    by_section: Counter[str] = Counter()
    by_document: Counter[str] = Counter()
    excluded: list[dict] = []
    unassigned: list[dict] = []
    flagged: list[dict] = []

    for line in all_lines:
        cleaning = line.cleaning or {}
        action = cleaning.get("action", "")
        if action == "excluded":
            category = "excluded"
            reason = cleaning.get("reason", "")
            excluded.append({"line_id": line.source_id, "page": line.page,
                             "reason": reason, "rule": cleaning.get("rule", ""),
                             "original_text": line.original_text})
        elif line.source_id in heading_ids:
            category = "section_heading"
            reason = line.section or ""
        elif line.section == "front_matter":
            category = "front_matter"
            reason = ""
        elif line.document_id and line.section:
            category = "assigned"
            reason = ""
        else:
            category = "unassigned"
            reason = "no_document_or_section"
            unassigned.append({"line_id": line.source_id, "page": line.page,
                               "original_text": line.original_text,
                               "review_flags": list(line.review_flags)})
        if line.review_flags or cleaning.get("status") == "pending_review":
            flagged.append({"line_id": line.source_id, "page": line.page,
                            "review_flags": list(line.review_flags),
                            "cleaning_status": cleaning.get("status", "")})
        by_category[category] += 1
        if line.section:
            by_section[line.section] += 1
        if line.document_id:
            by_document[line.document_id] += 1
        records.append({
            "line_id": line.source_id, "page": line.page, "order": line.index,
            "category": category, "reason": reason,
            "document_id": line.document_id, "section": line.section,
            "script": line.script_label,
            "original_text": line.original_text, "corrected_text": line.text,
            "review_flags": list(line.review_flags),
            "cleaning_status": cleaning.get("status", ""),
            "correction_status": (line.correction or {}).get("status", ""),
        })

    accounted = sum(by_category.values())
    review_ids = {sid for row in review for sid in (row.get("source_ids") or [])}
    unassigned_ids = {row["line_id"] for row in unassigned}
    return {
        "work_id": work_id,
        "raw_lines": len(all_lines),
        "accounted_lines": accounted,
        "unaccounted_lines": len(all_lines) - accounted,
        "by_category": dict(sorted(by_category.items())),
        "by_section": dict(sorted(by_section.items())),
        "by_document": dict(sorted(by_document.items())),
        "excluded": excluded,
        "flagged": flagged,
        "unassigned": unassigned,
        "review_source_line_ids": sorted(review_ids),
        "review_lines_not_accounted": sorted(unassigned_ids - review_ids),
        "records": records,
    }


def _marker_stats(pre: PreprocessResult, review: list[dict],
                  headings: list[dict]) -> dict:
    marker_rows = [row for row in headings if row.get("kind") in ("marker", "inferred_marker")]
    corrected = [r for r in pre.correction_log if r.get("status") == "corrected"]
    ambiguous = [r for r in pre.correction_log if r.get("status") == "pending_review"]
    unknown = [row for row in review if row.get("reason") == "unknown_heading"]
    order_anomaly = [row for row in review if row.get("reason") == "marker_order_anomaly"]
    missing = [row for row in review if row.get("reason") == "missing_marker"]
    sequence = Counter(row.get("section", "") for row in marker_rows)
    repeated = {name: count for name, count in sequence.items() if count > 1}
    return {
        "recognized_markers": len([r for r in pre.correction_log
                                   if r.get("status") in ("unchanged", "corrected")]),
        "corrected_aliases": len(corrected),
        "fuzzy_corrected": len([r for r in corrected if r.get("method") == "fuzzy"]),
        "ambiguous_markers": len(ambiguous),
        "unknown_uppercase_colon_headings": len(unknown),
        "marker_order_anomalies": len(order_anomaly),
        "missing_markers": len(missing),
        "repeated_markers": repeated,
        "marker_sequence": [row.get("section", "") for row in marker_rows],
        "corrections": corrected,
        "ambiguous": ambiguous,
    }


def run_refactor(work_id: str, raw_lines: list[OcrLine], seg_cfg: dict | None = None,
                 pages: list[int] | None = None) -> RefactorResult:
    cfg = RefactorConfig.from_config(seg_cfg)
    pre = preprocess_stream(work_id, raw_lines, cfg.preprocess)
    boundary = detect_boundaries(work_id, pre.lines, cfg.boundary, cfg.markers,
                                 cfg.headings, pages)

    layer_rows: dict[str, list[LayerLine]] = {}
    headings: list[dict] = []
    subworks: list[dict] = []
    document_rows: list[dict] = []
    review: list[dict] = list(boundary.review)
    review.extend(_review_from_preprocessing(work_id, pre))
    documents: list[Document] = []
    section_stats: dict[str, dict] = {}
    front_layer = cfg.layer_map.get("front_matter", "other")

    for span in boundary.spans:
        heading = None
        if span.heading_line is not None:
            line = span.heading_line
            heading = {"text": line.text, "page": line.page,
                       "writing_system": line.writing_system,
                       "source_ids": [line.source_id]}
        doc = Document(id=span.document_id, pages=span.pages,
                       lines=list(span.lines), heading=heading)
        documents.append(doc)

        if span.kind == "front_matter":
            assigned_lines, assigned_review = _front_matter_assignment(
                work_id, doc, front_layer)
            doc_title = ""
            doc_han_title = ""
            doc_subworks: list[dict] = []
            sections = {"front_matter": len(span.lines)}
        else:
            skip_ids = set((heading or {}).get("source_ids") or [])
            assigned = assign_document_sections(
                work_id, doc, cfg.markers, cfg.headings, cfg.layer_map, skip_ids)
            assigned_lines = assigned.lines
            assigned_review = assigned.review
            headings.extend(assigned.headings)
            subworks.extend(assigned.subworks)
            doc_subworks = assigned.subworks
            doc_title = assigned.title
            doc_han_title = assigned.han_title
            sections = dict(assigned.section_counts)
            if heading:
                headings.append(_document_heading_row(work_id, doc, heading))
            review.extend(assigned.review)

        for layer, rows in assigned_lines.items():
            layer_rows.setdefault(layer, []).extend(rows)

        doc_review = [r for r in review if r.get("document_id") == doc.id]
        boundary_pending = any(
            c.status == "pending_review"
            and c.following_line_id in {ln.source_id for ln in span.lines}
            for c in boundary.candidates)
        document_rows.append({
            "work_id": work_id, "document_id": doc.id, "pages": doc.pages,
            "heading": heading, "kind": span.kind, "title": doc_title,
            "han_title": doc_han_title, "subworks": doc_subworks,
            "sections": sections,
            "layers": {name: len(rows) for name, rows in assigned_lines.items()},
            "source_line_ids": [ln.source_id for ln in doc.all_lines],
            "review_status": ("pending_review" if (doc_review or boundary_pending)
                              else "not_required"),
            "status": "ok",
            "boundary": {
                "status": span.boundary_status,
                "evidence": list(span.evidence),
                "title_candidate": span.title_candidate,
            },
        })
        section_stats[doc.id] = {
            "title": doc_title, "sections": sections,
            "layers": {name: len(rows) for name, rows in assigned_lines.items()},
            "subworks": [s.get("subwork_id", "") for s in doc_subworks],
        }

    # Ensure every configured layer exists in the result mapping.
    for layer in set(cfg.layer_map.values()):
        layer_rows.setdefault(layer, [])

    accounting = _line_accounting(work_id, pre.all_lines, headings, review)
    if accounting["unaccounted_lines"]:
        review.append(review_row(work_id, "", "", "line_accounting_failed",
                                 sorted({ln.page for ln in pre.all_lines}), ""))
    if accounting["unassigned"]:
        review.append(review_row(
            work_id, "", "", "unassigned_line",
            sorted({r["page"] for r in accounting["unassigned"]}), "",
            [r["line_id"] for r in accounting["unassigned"]]))

    script_counts = Counter(line.script_label or "unknown" for line in pre.all_lines)
    low_conf = []
    if cfg.low_confidence_threshold is not None:
        for line in pre.all_lines:
            if line.confidence is not None and line.confidence < cfg.low_confidence_threshold:
                low_conf.append({"line_id": line.source_id, "page": line.page,
                                 "confidence": line.confidence})
                line.review_flags.append("low_confidence")
    marker_stats = _marker_stats(pre, review, headings)
    stats = {
        "work_id": work_id,
        "raw_lines": len(raw_lines),
        "preprocessed_lines": len(pre.lines),
        "excluded_lines": len(pre.all_lines) - len(pre.lines),
        "documents": len(documents),
        "works": len([d for d in documents if not d.id.endswith(".000")]),
        "front_matter_lines": len(boundary.front_matter.lines) if boundary.front_matter else 0,
        "boundary_candidates": len(boundary.candidates),
        "boundaries_auto_accepted": boundary.stats.get("auto_accepted", 0),
        "boundaries_pending_review": boundary.stats.get("pending_review", 0),
        "script_classes": dict(sorted(script_counts.items())),
        "corrected_markers": len([r for r in pre.correction_log
                                  if r.get("status") == "corrected"]),
        "ambiguous_markers": len([r for r in pre.correction_log
                                  if r.get("status") == "pending_review"]),
        "flagged_lines": accounting["by_category"].get("assigned", 0) and len(accounting["flagged"]),
        "headings": len(headings),
        "subworks": len(subworks),
        "review_rows": len(review),
        "layer_counts": {name: len(rows) for name, rows in sorted(layer_rows.items())},
        "section_counts": dict(sorted(Counter(
            section for doc in section_stats.values()
            for section in doc["sections"]).items())),
        "low_confidence": len(low_conf),
        "accounting": {k: accounting[k] for k in
                       ("raw_lines", "accounted_lines", "unaccounted_lines",
                        "by_category")},
    }
    return RefactorResult(
        documents=documents, layer_rows=layer_rows, headings=headings,
        subworks=subworks, document_rows=document_rows, review=review,
        cleaning_log=pre.cleaning_log, correction_log=pre.correction_log,
        boundary=boundary, all_lines=pre.all_lines, kept_lines=pre.lines,
        line_accounting=accounting, stats=stats, marker_stats=marker_stats,
        section_stats=section_stats)
