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
                 STATUS=row.get("status"), KIND=row.get("kind"),
                 TITLE=row.get("title"), HAN_TITLE=row.get("han_title"),
                 REVIEW_STATUS=row.get("review_status"),
                 SOURCE_LINE_IDS=",".join(row.get("source_line_ids") or []) or None)
        heading = row.get("heading")
        if heading:
            add(el, "HEADING", heading.get("text", ""), PAGE=heading.get("page"),
                WRITING_SYSTEM=heading.get("writing_system"),
                SOURCE_IDS=",".join(heading.get("source_ids") or []) or None)
        layers = add(el, "LAYERS")
        for name, count in (row.get("layers") or {}).items():
            add(layers, "LAYER", NAME=name, COUNT=count)
        sections = add(el, "SECTIONS")
        for name, count in (row.get("sections") or {}).items():
            add(sections, "SECTION", NAME=name, COUNT=count)
        subworks = add(el, "SUBWORKS")
        for item in row.get("subworks") or []:
            add(subworks, "SUBWORK", item.get("text", ""),
                ID=item.get("subwork_id"), PAGE=item.get("page"),
                INDEX=item.get("index"), LINE_ID=item.get("line_id"),
                STATUS=item.get("status"))
        boundary = row.get("boundary") or {}
        if boundary:
            add(el, "BOUNDARY", STATUS=boundary.get("status"),
                EVIDENCE=",".join(boundary.get("evidence") or []) or None,
                TITLE_CANDIDATE=boundary.get("title_candidate"))
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
            "sections": {x.get("NAME", ""): int(x.get("COUNT", "0"))
                         for x in el.findall("SECTIONS/SECTION")},
            "subworks": [{"subwork_id": x.get("ID", ""), "text": text_of(x),
                          "page": opt_int(x, "PAGE"), "index": opt_int(x, "INDEX"),
                          "line_id": x.get("LINE_ID", ""),
                          "status": x.get("STATUS", "")}
                         for x in el.findall("SUBWORKS/SUBWORK")],
            "kind": el.get("KIND", ""),
            "title": el.get("TITLE", ""),
            "han_title": el.get("HAN_TITLE", ""),
            "review_status": el.get("REVIEW_STATUS", ""),
            "source_line_ids": list_attr(el, "SOURCE_LINE_IDS"),
            "boundary": ({
                "status": b.get("STATUS", ""),
                "evidence": list_attr(b, "EVIDENCE"),
                "title_candidate": b.get("TITLE_CANDIDATE", ""),
            } if (b := el.find("BOUNDARY")) is not None else {}),
            "status": el.get("STATUS", ""),
        })
    return out


def write_headings(path: Path, work_id: str, rows: Iterable[dict]) -> Path:
    root = write_file_root(path, work_id)
    file_el = file_element(root)
    for row in rows:
        add(file_el, "HEADING", row.get("text", ""),
            ID=row.get("heading_id") or row.get("id"),
            DOCUMENT_ID=row.get("document_id"),
            LAYER=row.get("layer"), KIND=row.get("kind"), PAGE=row.get("page"),
            BBOX=bbox_attr(row.get("bbox")),
            WRITING_SYSTEM=row.get("writing_system"),
            SOURCE_IDS=",".join(row.get("source_ids") or []) or None)
    return write_root(path, root)


def read_headings(path: Path) -> list[dict]:
    root = read_root(path)
    return [{
        "work_id": file_element(root).get("ID", ""),
        "id": el.get("ID", ""),
        "heading_id": el.get("ID", ""),
        "document_id": el.get("DOCUMENT_ID", ""), "layer": el.get("LAYER", ""),
        "kind": el.get("KIND", ""), "text": text_of(el), "page": opt_int(el, "PAGE"),
        "bbox": bbox_from_attr(el.get("BBOX")),
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
        add(layer_el, "SEGMENT", row.get("text", ""), ID=row.get("id"),
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
    segments = layer_el.findall("SEGMENT") if layer_el is not None else []
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


def write_sentences(path: Path, work_id: str, layer: str, rows: Iterable[dict]) -> Path:
    root = write_file_root(path, work_id)
    layer_el = add(file_element(root), "LAYER", NAME=layer)
    for row in rows:
        pages = row.get("pages") or []
        pages_str = ",".join(str(p) for p in sorted(set(pages))) if pages else None
        segment_ids = row.get("segment_ids") or row.get("src_ids") or []
        segment_ids_str = ",".join(segment_ids) if segment_ids else None
        source_ids = row.get("source_ids") or []
        source_ids_str = ",".join(source_ids) if source_ids else None
        flags = row.get("flags") or []
        flags_str = ",".join(flags) if flags else None
        add(layer_el, "SENTENCE", row.get("text", ""),
            ID=row.get("id"),
            DOCUMENT_ID=row.get("document_id") or None,
            PAGES=pages_str,
            SEGMENT_IDS=segment_ids_str,
            SOURCE_IDS=source_ids_str,
            FLAGS=flags_str,
            RAW_TEXT=row.get("raw_text") or None)
    return write_root(path, root)


def read_sentences(path: Path) -> list[dict]:
    root = read_root(path)
    file_el = file_element(root)
    layer_el = file_el.find("LAYER")
    layer = layer_el.get("NAME", "") if layer_el is not None else ""
    units = layer_el.findall("SENTENCE") if layer_el is not None else []
    out = []
    for el in units:
        seg_ids = list_attr(el, "SEGMENT_IDS") or list_attr(el, "SRC_IDS")
        out.append({
            "id": el.get("ID", ""),
            "document_id": el.get("DOCUMENT_ID", ""),
            "layer": layer,
            "text": text_of(el),
            "pages": int_list_attr(el, "PAGES"),
            "segment_ids": seg_ids,
            "source_ids": list_attr(el, "SOURCE_IDS"),
            "src_ids": seg_ids,
            "flags": list_attr(el, "FLAGS"),
            "raw_text": el.get("RAW_TEXT", ""),
        })
    return out


def write_structural_layer(path: Path, work_id: str, layer: str, rows: Iterable[dict]) -> Path:
    root = write_file_root(path, work_id)
    layer_el = add(file_element(root), "LAYER", NAME=layer)
    for row in rows:
        source_ids = row.get("source_ids") or []
        flags = row.get("flags") or []
        add(layer_el, "SEGMENT", row.get("text", ""),
            ID=row.get("id"),
            DOCUMENT_ID=row.get("document_id"),
            PAGE=row.get("page"),
            WRITING_SYSTEM=row.get("writing_system"),
            SOURCE_IDS=",".join(source_ids) if source_ids else None,
            FLAGS=",".join(flags) if flags else None,
            RAW_TEXT=row.get("raw_text") or None)
    return write_root(path, root)


def read_structural_layer(path: Path) -> list[dict]:
    root = read_root(path)
    file_el = file_element(root)
    layer_el = file_el.find("LAYER")
    layer = layer_el.get("NAME", "") if layer_el is not None else ""
    segments = layer_el.findall("SEGMENT") if layer_el is not None else []
    return [{
        "id": el.get("ID", ""),
        "document_id": el.get("DOCUMENT_ID", ""),
        "layer": layer,
        "page": opt_int(el, "PAGE") or 0,
        "text": text_of(el),
        "writing_system": el.get("WRITING_SYSTEM", ""),
        "source_ids": list_attr(el, "SOURCE_IDS"),
        "flags": list_attr(el, "FLAGS"),
        "raw_text": el.get("RAW_TEXT", ""),
    } for el in segments]


# --------------------------------------------------------------------------- #
# Refactored pipeline artifacts (preprocessing / classification / boundaries)
# --------------------------------------------------------------------------- #

def write_json(path: Path, payload) -> Path:
    import json
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                   encoding="utf-8")
    tmp.replace(path)
    return path


def write_preprocessed(path: Path, work_id: str, lines: Iterable) -> Path:
    root = write_file_root(path, work_id)
    file_el = file_element(root)
    for line in lines:
        cleaning = line.cleaning or {}
        correction = line.correction or {}
        add(file_el, "LINE", line.text,
            ID=line.source_id, PAGE=line.page, ORDER=line.index,
            ORIGINAL_TEXT=line.original_text, SCRIPT=line.script_label,
            MARKER=line.marker, MARKER_RAW=line.marker_raw,
            CLEANING_ACTION=cleaning.get("action"), CLEANING_REASON=cleaning.get("reason"),
            CLEANING_RULE=cleaning.get("rule"), CLEANING_STATUS=cleaning.get("status"),
            CORRECTION_STATUS=correction.get("status"),
            CORRECTION_METHOD=correction.get("method"),
            REVIEW_FLAGS=",".join(line.review_flags) or None,
            BBOX=bbox_attr(line.bbox), CONFIDENCE=line.confidence,
            WRITING_SYSTEM=line.writing_system or None)
    return write_root(path, root)


def read_preprocessed(path: Path) -> list[dict]:
    root = read_root(path)
    return [{
        "line_id": el.get("ID", ""), "page": opt_int(el, "PAGE") or 0,
        "order": opt_int(el, "ORDER") or 0, "text": text_of(el),
        "original_text": el.get("ORIGINAL_TEXT", ""),
        "script": el.get("SCRIPT", ""), "marker": el.get("MARKER", ""),
        "marker_raw": el.get("MARKER_RAW", ""),
        "cleaning_action": el.get("CLEANING_ACTION", ""),
        "cleaning_reason": el.get("CLEANING_REASON", ""),
        "cleaning_status": el.get("CLEANING_STATUS", ""),
        "correction_status": el.get("CORRECTION_STATUS", ""),
        "review_flags": list_attr(el, "REVIEW_FLAGS"),
        "bbox": bbox_from_attr(el.get("BBOX")),
        "confidence": opt_float(el, "CONFIDENCE"),
        "writing_system": el.get("WRITING_SYSTEM", ""),
    } for el in file_element(root).findall("LINE")]


def write_line_classification(path: Path, work_id: str, lines: Iterable) -> Path:
    root = write_file_root(path, work_id)
    file_el = file_element(root)
    for line in lines:
        info = line.script
        data = info.to_dict() if hasattr(info, "to_dict") else {}
        add(file_el, "LINE", line.text,
            ID=line.source_id, PAGE=line.page, ORDER=line.index,
            LABEL=data.get("label", line.script_label),
            CJK_COUNT=data.get("cjk_count"), LATIN_COUNT=data.get("latin_count"),
            OTHER_LETTER_COUNT=data.get("other_letter_count"),
            SCRIPT_CHARACTER_COUNT=data.get("script_character_count"),
            CJK_RATIO=data.get("cjk_ratio"), LATIN_RATIO=data.get("latin_ratio"),
            METHOD=data.get("method"), REVIEW=data.get("review"),
            REVIEW_REASON=data.get("review_reason"))
    return write_root(path, root)


def read_line_classification(path: Path) -> list[dict]:
    root = read_root(path)
    return [{
        "line_id": el.get("ID", ""), "page": opt_int(el, "PAGE") or 0,
        "order": opt_int(el, "ORDER") or 0, "text": text_of(el),
        "label": el.get("LABEL", ""),
        "cjk_count": opt_int(el, "CJK_COUNT"),
        "latin_count": opt_int(el, "LATIN_COUNT"),
        "other_letter_count": opt_int(el, "OTHER_LETTER_COUNT"),
        "script_character_count": opt_int(el, "SCRIPT_CHARACTER_COUNT"),
        "cjk_ratio": opt_float(el, "CJK_RATIO"),
        "latin_ratio": opt_float(el, "LATIN_RATIO"),
        "method": el.get("METHOD", ""),
        "review": (el.get("REVIEW") == "true") if el.get("REVIEW") is not None else False,
        "review_reason": el.get("REVIEW_REASON", ""),
    } for el in file_element(root).findall("LINE")]


def write_boundary_candidates(path: Path, work_id: str, rows: Iterable[dict]) -> Path:
    root = write_file_root(path, work_id)
    file_el = file_element(root)
    for row in rows:
        add(file_el, "BOUNDARY", ID=row.get("boundary_id"),
            PRECEDING_LINE_ID=row.get("preceding_line_id"),
            FOLLOWING_LINE_ID=row.get("following_line_id"),
            PAGE=row.get("page"), TITLE_CANDIDATE=row.get("title_candidate"),
            EVIDENCE=",".join(row.get("evidence") or []) or None,
            STATUS=row.get("status"), DECISION_SOURCE=row.get("decision_source"),
            APPLIED=row.get("applied"), START_INDEX=row.get("start_index"),
            SCRIPT_TRANSITION=row.get("script_transition"),
            NOTE=row.get("note"))
    return write_root(path, root)


def read_boundary_candidates(path: Path) -> list[dict]:
    root = read_root(path)
    return [{
        "boundary_id": el.get("ID", ""),
        "preceding_line_id": el.get("PRECEDING_LINE_ID", ""),
        "following_line_id": el.get("FOLLOWING_LINE_ID", ""),
        "page": opt_int(el, "PAGE"),
        "title_candidate": el.get("TITLE_CANDIDATE", ""),
        "evidence": list_attr(el, "EVIDENCE"),
        "status": el.get("STATUS", ""),
        "decision_source": el.get("DECISION_SOURCE", ""),
        "applied": (el.get("APPLIED") == "true") if el.get("APPLIED") is not None else False,
        "start_index": opt_int(el, "START_INDEX"),
        "script_transition": el.get("SCRIPT_TRANSITION", ""),
        "note": el.get("NOTE", ""),
    } for el in file_element(root).findall("BOUNDARY")]
