"""Phân đoạn 03_ocr (theo hệ chữ) -> tài liệu + lớp văn bản trong 04_segmentation.

    python -m src.segmentation.pipeline --config configs/CPO_18_demo_new.json --work-id CPO_18

Đầu ra:
    04_segmentation/documents.xml
    04_segmentation/document_metadata.xml
    04_segmentation/layers/<layer>.xml
    04_segmentation/<layer>.metadata.xml
    04_segmentation/section_headings.xml
    04_segmentation/review_required.xml (+ .tsv để đọc nhanh)

Không xoá/không sửa 03_ocr; chạy lại cần --force (trừ khi output chưa tồn tại).
"""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

from src.cleaning.metadata import aggregate_document_metadata, write_layer_metadata
from src.common import deps
from src.common.corpus import (DataLayout, MetadataStore, base_parser, parse_args,
                               parse_pages)
from src.ocr import raw_store
from src.segmentation import serialization
from src.segmentation.documents import (load_unified_stream, load_writing_system,
                                        segment_documents,
                                        segment_documents_unified)
from src.segmentation.layers import assign_document, assign_document_unified
from src.segmentation.refactor import run_refactor
from src.segmentation import streams as ocr_streams

log = logging.getLogger("segmentation")

DEFAULT_LAYERS = {"han": {"source": "chi_tra"},
                  "am": {"source": "vie"},
                  "nghia": {"source": "vie"}}

REFACTOR_KEYS = ("script_classification", "markers", "marker_fuzzy",
                 "document_boundary", "headings", "section_layers")


def refactor_enabled(seg: dict) -> bool:
    """The refactored path is opt-in, so legacy configs keep their behaviour."""
    return any(key in seg for key in REFACTOR_KEYS)


def create_parser():
    p = base_parser("Phân đoạn tài liệu/lớp từ kho OCR thô (03_ocr) -> 04_segmentation",
                    layer_required=False)
    p.add_argument("--ocr-streams", choices=[ocr_streams.MODE],
                   help="han_vie: lớp han từ 03_ocr/ocr_raw/han.xml (paddlevl), am/nghia/tho từ vie.xml (qwen). "
                        "Mặc định lấy segmentation.ocr_streams trong config; không có thì dùng chế độ cũ")
    return p


def write_review_tsv(path: Path, rows: list[dict]) -> Path:
    seen = set()
    lines = ["reason\tdocument_id\tlayer\tpages\ttext\tsource_ids"]
    for row in rows:
        key = (row["reason"], row["document_id"], row["layer"],
               tuple(row["pages"]), row["text"], tuple(row["source_ids"]))
        if key in seen:
            continue
        seen.add(key)
        text = " ".join(row["text"].split())
        lines.append("\t".join([row["reason"], row["document_id"], row["layer"],
                                ",".join(str(p) for p in row["pages"]), text,
                                ",".join(row["source_ids"])]))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def run(args) -> None:
    if not args.config:
        raise ValueError("Thiếu --config (bước phân đoạn đọc segmentation.* từ config)")
    cfg = json.loads(Path(args.config).read_text(encoding="utf-8"))
    seg = cfg.get("segmentation", {})
    common = cfg.get("common", {})
    pages = parse_pages(common.get("pages"))
    layout = DataLayout(Path(args.data_root), args.work_id)
    seg_dir = layout.segmentation_dir
    layer_cfg = {**DEFAULT_LAYERS, **seg.get("layers", {})}
    streams_mode = getattr(args, "ocr_streams", None) or seg.get("ocr_streams")
    if streams_mode not in (None, ocr_streams.MODE):
        raise ValueError(f"segmentation.ocr_streams chỉ nhận '{ocr_streams.MODE}', nhận {streams_mode!r}")
    if streams_mode and refactor_enabled(seg):
        raise ValueError("ocr_streams=han_vie chưa hỗ trợ cùng các khoá refactor "
                         f"({', '.join(k for k in REFACTOR_KEYS if k in seg)})")
    is_unified = bool(streams_mode) or (layout.ocr_unified_xml_path.exists() or
                  raw_store.unified_manifest_path(layout).exists() or
                  (cfg.get("ocr", {}).get("engine") is not None and not any(k in raw_store.WRITING_SYSTEMS for k in cfg.get("ocr", {}))))
    use_refactor = is_unified and refactor_enabled(seg)
    if use_refactor:
        # Auxiliary and unclassified lines are written to a separate layer.
        layer_cfg.setdefault("other", {"optional": True})
    refactor_targets = ([seg_dir / "preprocessing" / "preprocessed.xml",
                         seg_dir / "preprocessing" / "cleaning_log.json",
                         seg_dir / "preprocessing" / "correction_log.json",
                         seg_dir / "classification" / "line_classification.xml",
                         seg_dir / "boundary_detection" / "boundary_candidates.xml",
                         seg_dir / "boundary_detection" / "boundary_review.json",
                         seg_dir / "review" / "segmentation_review.json",
                         seg_dir / "line_accounting.json"]
                        if use_refactor else [])
    targets = [seg_dir / "documents.xml", seg_dir / "document_metadata.xml",
               seg_dir / "review_required.xml", seg_dir / "section_headings.xml",
               *(layout.segmentation_layer(name) for name in layer_cfg),
               *(seg_dir / f"{name}.metadata.xml" for name in layer_cfg),
               *refactor_targets]

    if streams_mode:
        inputs = [p for p in (layout.ocr_layer_xml_path("han"), layout.ocr_layer_xml_path("vie"))
                  if p.exists()]
    elif is_unified:
        inputs = [p for p in (layout.ocr_unified_xml_path, raw_store.unified_manifest_path(layout))
                  if p.exists()]
    else:
        inputs = [layout.ocr_raw_xml_path(ws) for ws in raw_store.WRITING_SYSTEMS
                  if layout.ocr_raw_xml_path(ws).exists()]
        inputs += [raw_store.manifest_path(layout, ws) for ws in raw_store.WRITING_SYSTEMS
                   if raw_store.manifest_path(layout, ws).exists()]
    inputs.append(Path(args.config))
    deps.check_fresh(layout, "segmentation", inputs, targets, args.force,
                     f"{targets[0]} đã tồn tại; dùng --force để phân đoạn lại")
    if not inputs[:-1]:
        raise ValueError("Chưa có 03_ocr/ocr_raw/*.xml hoặc manifest; chạy ocr trước")

    refactor_result = None
    stream_info: dict = {}
    if streams_mode:
        han_lines = ocr_streams.load_stream(layout, "han", pages)
        vie_lines = ocr_streams.load_stream(layout, "vie", pages)
        merged = ocr_streams.merge_streams(han_lines, vie_lines)
        if not merged:
            raise ValueError("han.xml/vie.xml không có dòng nào trong phạm vi trang")
        coverage = ocr_streams.coverage_review(args.work_id, han_lines, vie_lines, pages)
        allowed = ocr_streams.allowed_document_starts(
            merged, min_block_lines=int(seg.get("document", {}).get("min_han_block_lines",
                                                                   ocr_streams.MIN_HAN_BLOCK_LINES)))
        is_header = ocr_streams.header_predicate(merged)
        from src.segmentation.am_detect import AmDetector
        am_detector = AmDetector(seg.get("am_detection"), Path(__file__).resolve().parents[2])
        from src.segmentation.overrides import overrides_for, parse_overrides
        doc_overrides = parse_overrides(seg.get("document_overrides"))
        used_overrides: set[int] = set()
        docs, review = segment_documents_unified(args.work_id, merged,
                                                 ocr_streams.document_config(seg.get("document", {})),
                                                 pages, allowed_starts=allowed)
        review = coverage + review
        sources_used = ["han", "vie"]
        stream_info = {"mode": streams_mode, "lines": ocr_streams.stream_stats(merged),
                       "han_pages": sorted({ln.page for ln in han_lines}),
                       "vie_pages": sorted({ln.page for ln in vie_lines}),
                       "dropped_cross_stream": {}}
    elif use_refactor:
        unified_lines = load_unified_stream(layout, pages)
        if not unified_lines:
            raise ValueError("Chưa có dòng OCR thô unified; chạy ocr --unified trước")
        refactor_result = run_refactor(args.work_id, unified_lines, seg, pages)
        docs = refactor_result.documents
        review = list(refactor_result.review)
        sources_used = ["unified"]
    elif is_unified:
        unified_lines = load_unified_stream(layout, pages)
        if not unified_lines:
            raise ValueError("Chưa có dòng OCR thô unified; chạy ocr --unified trước")
        docs, review = segment_documents_unified(args.work_id, unified_lines, seg.get("document", {}), pages)
        sources_used = ["unified"]
    else:
        streams = {ws: load_writing_system(layout, ws, pages)
                   for ws in raw_store.WRITING_SYSTEMS
                   if raw_store.manifest_path(layout, ws).exists()
                   or layout.ocr_raw_xml_path(ws).exists()}
        if not any(streams.values()):
            raise ValueError("Chưa có dòng OCR thô; chạy ocr --writing-system chi_tra|vie trước")
        docs, review = segment_documents(args.work_id, streams, seg.get("document", {}), pages)
        sources_used = sorted(streams)

    if refactor_result is not None:
        layer_rows: dict[str, list] = {name: [] for name in layer_cfg}
        for name, rows in refactor_result.layer_rows.items():
            layer_rows.setdefault(name, []).extend(rows)
        metadata_rows: dict[str, list[dict]] = {name: [] for name in layer_cfg}
        headings = list(refactor_result.headings)
        doc_rows = list(refactor_result.document_rows)
    else:
        meta_cfg = cfg.get("metadata_extraction", {})
        layer_rows = {name: [] for name in layer_cfg}
        metadata_rows = {name: [] for name in layer_cfg}
        headings = []
        doc_rows = []
        for doc in docs:
            if streams_mode:
                implicit = seg.get("implicit_after_han", ocr_streams.IMPLICIT_AFTER_HAN)
                first_han = next((x.text for x in doc.all_lines if x.writing_system == "han"), "")
                matched = overrides_for(min(doc.pages) if doc.pages else 0, first_han, doc_overrides)
                used_overrides.update(id(o) for o in matched)
                assigned = assign_document_unified(args.work_id, doc, layer_cfg, meta_cfg,
                                                   keep_line=ocr_streams.keep_line,
                                                   dropped=stream_info["dropped_cross_stream"],
                                                   implicit_next={"han": implicit} if implicit else None,
                                                   is_header=is_header,
                                                   title_next=seg.get("implicit_title_next",
                                                                      ocr_streams.IMPLICIT_TITLE_NEXT),
                                                   notes_after_attribution=set(seg.get(
                                                       "notes_after_attribution",
                                                       ocr_streams.NOTES_AFTER_ATTRIBUTION)),
                                                   # mục chú thích "1. Thuật ngữ: ..." kết thúc lớp Việt (mặc định tắt)
                                                   note_entry_ends=set(seg.get("note_entry_ends", [])),
                                                   am_detector=am_detector,
                                                   overrides=matched,
                                                   # sửa G (mặc định tắt): dịch thơ dính vào nghia khi mất "Dịch thơ:"
                                                   verse_tho=bool(seg.get("verse_tho_split", False)))
            elif is_unified:
                assigned = assign_document_unified(args.work_id, doc, layer_cfg, meta_cfg)
            else:
                assigned = assign_document(args.work_id, doc, layer_cfg, meta_cfg)
            layer_rows.update({k: layer_rows.get(k, []) + v for k, v in assigned.lines.items()})
            headings.extend(assigned.headings)
            for row in assigned.metadata_rows:
                metadata_rows.setdefault(row["layer"], []).append(row)
            review.extend(doc.review)
            review.extend(assigned.review)
            if doc.heading:
                headings.append({"work_id": args.work_id, "document_id": doc.id,
                                 "layer": "document", "kind": "document_start", **doc.heading})
            doc_rows.append({"work_id": args.work_id, "document_id": doc.id,
                             "pages": doc.pages, "heading": doc.heading,
                             "layers": {k: len(v) for k, v in assigned.lines.items()},
                             "status": "ok"})

    if streams_mode:
        for ov in doc_overrides:
            if id(ov) not in used_overrides:
                log.warning("Ghi đè document_overrides không khớp tài liệu nào (ranh giới đã đổi?): trang %d, %s",
                            ov.start_page, ov.reason)
        review.extend(ocr_streams.pairing_review(args.work_id, doc_rows))
        outside = ocr_streams.document_page_check(layer_rows, doc_rows)
        if outside:
            log.warning("%d dòng lớp han nằm ngoài khoảng trang của tài liệu: %s", len(outside),
                        ", ".join(outside[:10]))
        stream_info["han_rows_outside_document_pages"] = outside
        if stream_info["dropped_cross_stream"]:
            log.info("Bỏ dòng khác luồng sau khi gán lớp (vd chữ Hán trong khảo dị): %s",
                     stream_info["dropped_cross_stream"])

    layout.ensure(seg_dir, layout.segmentation_layers)
    serialization.write_documents(seg_dir / "documents.xml", args.work_id, doc_rows)
    serialization.write_headings(seg_dir / "section_headings.xml", args.work_id, headings)
    for name in layer_cfg:
        serialization.write_layer(layout.segmentation_layer(name), args.work_id, name,
                                  [row.to_row() for row in layer_rows.get(name, [])])
        write_layer_metadata(seg_dir, name, metadata_rows.get(name, []))
    aggregate_document_metadata(seg_dir, args.work_id)
    serialization.write_review(seg_dir / "review_required.xml", args.work_id, review)
    write_review_tsv(seg_dir / "review_required.tsv", review)
    if refactor_result is not None:
        pre_dir = seg_dir / "preprocessing"
        cls_dir = seg_dir / "classification"
        bnd_dir = seg_dir / "boundary_detection"
        rev_dir = seg_dir / "review"
        layout.ensure(pre_dir, cls_dir, bnd_dir, rev_dir)
        serialization.write_preprocessed(pre_dir / "preprocessed.xml", args.work_id,
                                         refactor_result.all_lines)
        serialization.write_json(pre_dir / "cleaning_log.json",
                                 {"work_id": args.work_id,
                                  "records": refactor_result.cleaning_log})
        serialization.write_json(pre_dir / "correction_log.json",
                                 {"work_id": args.work_id,
                                  "records": refactor_result.correction_log})
        serialization.write_line_classification(cls_dir / "line_classification.xml",
                                                args.work_id, refactor_result.all_lines)
        serialization.write_boundary_candidates(
            bnd_dir / "boundary_candidates.xml", args.work_id,
            [c.to_dict() for c in refactor_result.boundary.candidates])
        serialization.write_json(bnd_dir / "boundary_review.json", {
            "work_id": args.work_id,
            "summary": refactor_result.boundary.stats,
            "candidates": [c.to_dict() for c in refactor_result.boundary.candidates],
            "review": [row for row in refactor_result.review
                       if row.get("reason") in ("ambiguous_boundary", "unassigned_line",
                                                "line_accounting_gap")],
        })
        serialization.write_json(rev_dir / "segmentation_review.json", {
            "work_id": args.work_id,
            "stats": refactor_result.stats,
            "marker_stats": refactor_result.marker_stats,
            "sections": refactor_result.section_stats,
            "review": refactor_result.review,
        })
        serialization.write_json(seg_dir / "line_accounting.json",
                                 refactor_result.line_accounting)
    deps.record(layout, "segmentation", inputs, targets)
    MetadataStore(layout).update("segmentation", {
        "documents": len(docs),
        "lines": {name: len(rows) for name, rows in layer_rows.items()},
        "headings": len(headings), "review_required": len(review),
        "sources": sources_used,
        **({"ocr_streams": stream_info} if stream_info else {}),
        **({"refactor": refactor_result.stats} if refactor_result is not None else {})})
    log.info("Phân đoạn %d tài liệu, %d dòng, %d review -> %s", len(docs),
             sum(len(v) for v in layer_rows.values()), len(review), seg_dir)


def main(argv: list[str] | None = None) -> int:
    parser = create_parser()
    try:
        args = parse_args(parser, argv)
        run(args)
        return 0
    except deps.UpToDate as exc:          # đầu ra còn mới: bỏ qua, không phải lỗi
        log.info("%s", exc)
        return 0
    except (ValueError, FileExistsError, OSError) as exc:
        log.error("%s", exc)
        return 2


if __name__ == "__main__":
    sys.exit(main())
