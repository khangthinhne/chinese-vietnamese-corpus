"""Đọc/ghi XML cho 04_segmentation (documents, layers, metadata, headings, review)."""

from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Iterable

from src.common.xmlstore import (add, bbox_attr, bbox_from_attr, file_element, int_list_attr,
                                 list_attr, opt_float, opt_int, read_root, set_attrs, text_of,
                                 write_file_root, write_root)


def _pages(values: Iterable[int] | None) -> str | None:
    values = sorted(set(values or []))
    return ",".join(str(x) for x in values) or None


def write_documents(path: Path, work_id: str, rows: Iterable[dict]) -> Path:
    root = write_file_root(path, work_id)
    file_el = file_element(root)
    for row in rows:
        el = add(file_el, "DOCUMENT", ID=row["document_id"], PAGES=_pages(row.get("pages")),
                 STATUS=row.get("status"))
        heading = row.get("heading")
        if heading:
            add(el, "HEADING", heading.get("text", ""), PAGE=heading.get("page"),
                WRITING_SYSTEM=heading.get("writing_system"),
                SOURCE_IDS=",".join(heading.get("source_ids") or []) or None)
        layers = add(el, "LAYERS")
        for name, count in (row.get("layers") or {}).items():
            add(layers, "LAYER", NAME=name, COUNT=count)
    return write_root(path, root)


def read_documents(path: Path) -> list[dict]:
    root = read_root(path)
    out = []
    for el in file_element(root).findall("DOCUMENT"):
        heading = el.find("HEADING")
        out.append({
            "work_id": file_element(root).get("ID", ""),
            "document_id": el.get("ID", ""),
            "pages": int_list_attr(el, "PAGES"),
            "heading": None if heading is None else {
                "text": text_of(heading), "page": opt_int(heading, "PAGE"),
                "writing_system": heading.get("WRITING_SYSTEM", ""),
                "source_ids": list_attr(heading, "SOURCE_IDS")},
            "layers": {x.get("NAME", ""): int(x.get("COUNT", "0"))
                       for x in el.findall("LAYERS/LAYER")},
            "status": el.get("STATUS", ""),
        })
    return out


def write_headings(path: Path, work_id: str, rows: Iterable[dict]) -> Path:
    root = write_file_root(path, work_id)
    file_el = file_element(root)
    for row in rows:
        add(file_el, "HEADING", row.get("text", ""), DOCUMENT_ID=row.get("document_id"),
            LAYER=row.get("layer"), KIND=row.get("kind"), PAGE=row.get("page"),
            WRITING_SYSTEM=row.get("writing_system"),
            SOURCE_IDS=",".join(row.get("source_ids") or []) or None)
    return write_root(path, root)


def read_headings(path: Path) -> list[dict]:
    root = read_root(path)
    return [{
        "work_id": file_element(root).get("ID", ""),
        "document_id": el.get("DOCUMENT_ID", ""), "layer": el.get("LAYER", ""),
        "kind": el.get("KIND", ""), "text": text_of(el), "page": opt_int(el, "PAGE"),
        "writing_system": el.get("WRITING_SYSTEM", ""),
        "source_ids": list_attr(el, "SOURCE_IDS"),
    } for el in file_element(root).findall("HEADING")]


def write_review(path: Path, work_id: str, rows: Iterable[dict]) -> Path:
    root = write_file_root(path, work_id)
    file_el = file_element(root)
    for row in rows:
        add(file_el, "REVIEW", row.get("text", ""), DOCUMENT_ID=row.get("document_id"),
            LAYER=row.get("layer"), REASON=row.get("reason"), PAGES=_pages(row.get("pages")),
            SOURCE_IDS=",".join(row.get("source_ids") or []) or None)
    return write_root(path, root)


def read_review(path: Path) -> list[dict]:
    root = read_root(path)
    return [{
        "work_id": file_element(root).get("ID", ""),
        "document_id": el.get("DOCUMENT_ID", ""), "layer": el.get("LAYER", ""),
        "reason": el.get("REASON", ""), "pages": int_list_attr(el, "PAGES"),
        "text": text_of(el), "source_ids": list_attr(el, "SOURCE_IDS"),
    } for el in file_element(root).findall("REVIEW")]


def write_layer(path: Path, work_id: str, layer: str, rows: Iterable[dict]) -> Path:
    root = write_file_root(path, work_id)
    layer_el = add(file_element(root), "LAYER", NAME=layer)
    for row in rows:
        add(layer_el, "SEG", row.get("text", ""), ID=row.get("id"),
            DOCUMENT_ID=row.get("document_id"), PAGE=row.get("page"),
            WRITING_SYSTEM=row.get("writing_system"),
            BBOX=bbox_attr(row.get("bbox")), CONFIDENCE=row.get("confidence"),
            SOURCE_IDS=",".join(row.get("source_ids") or []) or None,
            FLAGS=",".join(row.get("flags") or []) or None)
    return write_root(path, root)


def read_layer(path: Path) -> list[dict]:
    root = read_root(path)
    file_el = file_element(root)
    layer_el = file_el.find("LAYER")
    layer = layer_el.get("NAME", "") if layer_el is not None else ""
    segments = layer_el.findall("SEG") if layer_el is not None else []
    return [{
        "id": el.get("ID", ""), "document_id": el.get("DOCUMENT_ID", ""), "layer": layer,
        "page": opt_int(el, "PAGE") or 0, "text": text_of(el),
        "bbox": bbox_from_attr(el.get("BBOX")), "confidence": opt_float(el, "CONFIDENCE"),
        "writing_system": el.get("WRITING_SYSTEM", ""),
        "source_ids": list_attr(el, "SOURCE_IDS"), "flags": list_attr(el, "FLAGS"),
    } for el in segments]


def write_layer_metadata(path: Path, work_id: str, layer: str, rows: Iterable[dict]) -> Path:
    root = write_file_root(path, work_id)
    layer_el = add(file_element(root), "LAYER", NAME=layer)
    for row in rows:
        doc = add(layer_el, "DOCUMENT", ID=row.get("document_id"), STATUS=row.get("status"))
        for field in row.get("fields", []):
            add(doc, "FIELD", field.get("text", ""), NAME=field.get("field"),
                CONFIDENCE=field.get("confidence", "high"),
                SOURCE_IDS=",".join(field.get("source_ids") or []) or None,
                PAGES=_pages(field.get("pages")))
        for uncertain in row.get("uncertain", []):
            add(doc, "UNCERTAIN", uncertain.get("text", ""), REASON=uncertain.get("reason"),
                SOURCE_IDS=",".join(uncertain.get("source_ids") or []) or None,
                PAGES=_pages(uncertain.get("pages")))
    return write_root(path, root)


def read_layer_metadata(path: Path) -> list[dict]:
    root = read_root(path)
    file_el = file_element(root)
    layer_el = file_el.find("LAYER")
    layer = layer_el.get("NAME", "") if layer_el is not None else ""
    out = []
    for doc in (layer_el.findall("DOCUMENT") if layer_el is not None else []):
        out.append({
            "work_id": file_el.get("ID", ""), "layer": layer,
            "document_id": doc.get("ID", ""), "status": doc.get("STATUS", ""),
            "fields": [{"field": f.get("NAME", ""), "text": text_of(f),
                        "source_ids": list_attr(f, "SOURCE_IDS"),
                        "pages": int_list_attr(f, "PAGES"),
                        "confidence": f.get("CONFIDENCE", "high")}
                       for f in doc.findall("FIELD")],
            "uncertain": [{"text": text_of(u), "source_ids": list_attr(u, "SOURCE_IDS"),
                           "pages": int_list_attr(u, "PAGES"), "reason": u.get("REASON", "")}
                          for u in doc.findall("UNCERTAIN")],
        })
    return out


def write_document_metadata(path: Path, work_id: str, rows: Iterable[dict]) -> Path:
    root = write_file_root(path, work_id)
    file_el = file_element(root)
    for row in rows:
        doc = add(file_el, "DOCUMENT", ID=row.get("document_id"),
                  SOURCE_PAGES=_pages(row.get("source_pages")))
        for name, per_layer in (row.get("metadata") or {}).items():
            field = add(doc, "FIELD", NAME=name)
            for layer, occurrences in per_layer.items():
                for occ in occurrences:
                    add(field, "OCCURRENCE", occ.get("text", ""), LAYER=layer,
                        CONFIDENCE=occ.get("confidence", "high"),
                        SOURCE_IDS=",".join(occ.get("source_ids") or []) or None,
                        PAGES=_pages(occ.get("pages")))
        for layer, uncertain in (row.get("uncertain") or {}).items():
            for item in uncertain:
                add(doc, "UNCERTAIN", item.get("text", ""), LAYER=layer,
                    REASON=item.get("reason"),
                    SOURCE_IDS=",".join(item.get("source_ids") or []) or None,
                    PAGES=_pages(item.get("pages")))
    return write_root(path, root)


def read_document_metadata(path: Path) -> list[dict]:
    root = read_root(path)
    file_el = file_element(root)
    out = []
    for doc in file_el.findall("DOCUMENT"):
        metadata: dict[str, dict[str, list[dict]]] = {}
        for field in doc.findall("FIELD"):
            name = field.get("NAME", "")
            for occ in field.findall("OCCURRENCE"):
                metadata.setdefault(name, {}).setdefault(occ.get("LAYER", ""), []).append({
                    "text": text_of(occ), "source_ids": list_attr(occ, "SOURCE_IDS"),
                    "pages": int_list_attr(occ, "PAGES"),
                    "confidence": occ.get("CONFIDENCE", "high")})
        uncertain: dict[str, list[dict]] = {}
        for item in doc.findall("UNCERTAIN"):
            uncertain.setdefault(item.get("LAYER", ""), []).append({
                "text": text_of(item), "source_ids": list_attr(item, "SOURCE_IDS"),
                "pages": int_list_attr(item, "PAGES"), "reason": item.get("REASON", "")})
        out.append({"work_id": file_el.get("ID", ""), "document_id": doc.get("ID", ""),
                    "metadata": metadata, "uncertain": uncertain,
                    "source_pages": int_list_attr(doc, "SOURCE_PAGES")})
    return out
