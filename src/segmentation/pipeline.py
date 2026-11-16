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
from src.segmentation.documents import load_writing_system, segment_documents
from src.segmentation.layers import assign_document

log = logging.getLogger("segmentation")

DEFAULT_LAYERS = {"han": {"source": "chi_tra"},
                  "am": {"source": "vie"},
                  "nghia": {"source": "vie"}}


def create_parser():
    p = base_parser("Phân đoạn tài liệu/lớp từ kho OCR thô (03_ocr) -> 04_segmentation",
                    layer_required=False)
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
    targets = [seg_dir / "documents.xml", seg_dir / "document_metadata.xml",
               seg_dir / "review_required.xml", seg_dir / "section_headings.xml",
               *(layout.segmentation_layer(name) for name in layer_cfg),
               *(seg_dir / f"{name}.metadata.xml" for name in layer_cfg)]
    inputs = [layout.ocr_raw_xml_path(ws) for ws in raw_store.WRITING_SYSTEMS
              if layout.ocr_raw_xml_path(ws).exists()]
    inputs += [raw_store.manifest_path(layout, ws) for ws in raw_store.WRITING_SYSTEMS
               if raw_store.manifest_path(layout, ws).exists()]
    inputs.append(Path(args.config))
    deps.check_fresh(layout, "segmentation", inputs, targets, args.force,
                     f"{targets[0]} đã tồn tại; dùng --force để phân đoạn lại")
    if not inputs[:-1]:
        raise ValueError("Chưa có 03_ocr/ocr_raw/*.xml hoặc manifest; chạy ocr trước")

    streams = {ws: load_writing_system(layout, ws, pages)
               for ws in raw_store.WRITING_SYSTEMS
               if raw_store.manifest_path(layout, ws).exists()
               or layout.ocr_raw_xml_path(ws).exists()}
    if not any(streams.values()):
        raise ValueError("Chưa có dòng OCR thô; chạy ocr --writing-system chi_tra|vie trước")
    docs, review = segment_documents(args.work_id, streams, seg.get("document", {}), pages)

    meta_cfg = cfg.get("metadata_extraction", {})
    layer_rows: dict[str, list] = {name: [] for name in layer_cfg}
    metadata_rows: dict[str, list[dict]] = {name: [] for name in layer_cfg}
    headings: list[dict] = []
    doc_rows: list[dict] = []
    for doc in docs:
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
    deps.record(layout, "segmentation", inputs, targets)
    MetadataStore(layout).update("segmentation", {
        "documents": len(docs),
        "lines": {name: len(rows) for name, rows in layer_rows.items()},
        "headings": len(headings), "review_required": len(review),
        "sources": sorted(streams)})
    log.info("Phân đoạn %d tài liệu, %d dòng, %d review -> %s", len(docs),
             sum(len(v) for v in layer_rows.values()), len(review), seg_dir)


def main(argv: list[str] | None = None) -> int:
    parser = create_parser()
    try:
        args = parse_args(parser, argv)
        run(args)
        return 0
    except (ValueError, FileExistsError, OSError) as exc:
        log.error("%s", exc)
        return 2


if __name__ == "__main__":
    sys.exit(main())
