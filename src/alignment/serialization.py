"""Đọc/ghi XML cho 06_alignment/<pair>.xml."""

from __future__ import annotations

from pathlib import Path
from typing import Iterable

from src.common.xmlstore import (add, file_element, int_list_attr, list_attr, opt_float,
                                 read_root, text_of, write_file_root, write_root)


def write_alignments(path: Path, work_id: str, pair: str, src_layer: str, tgt_layer: str,
                     rows: Iterable[dict]) -> Path:
    root = write_file_root(path, work_id)
    pair_el = add(file_element(root), "PAIR", NAME=pair, SRC=src_layer, TGT=tgt_layer)
    for row in rows:
        el = add(pair_el, "ALIGNMENT", ID=row.get("id"),
                 DOCUMENT_ID=row.get("document_id"), KIND=row.get("kind"),
                 SCORE=row.get("score"),
                 FLAGS=",".join(row.get("flags") or []) or None,
                 SRC_IDS=",".join(row.get("src_ids") or []) or None,
                 TGT_IDS=",".join(row.get("tgt_ids") or []) or None)
        pages = add(el, "PAGES")
        for layer, values in (row.get("pages") or {}).items():
            add(pages, "LAYER", NAME=layer,
                PAGES=",".join(str(p) for p in values) or None)
        add(el, "SRC_TEXT", row.get("src_text", ""))
        add(el, "TGT_TEXT", row.get("tgt_text", ""))
    return write_root(path, root)


def read_alignments(path: Path) -> list[dict]:
    if path.suffix == ".jsonl":
        from src.common.sentences import read_jsonl
        return read_jsonl(path)
    root = read_root(path)
    pair_el = file_element(root).find("PAIR")
    out = []
    for el in (pair_el.findall("ALIGNMENT") if pair_el is not None else []):
        pages = {layer.get("NAME", ""): int_list_attr(layer, "PAGES")
                 for layer in el.findall("PAGES/LAYER")}
        out.append({
            "id": el.get("ID", ""), "document_id": el.get("DOCUMENT_ID", ""),
            "kind": el.get("KIND", ""), "score": opt_float(el, "SCORE") or 0.0,
            "flags": list_attr(el, "FLAGS"), "src_ids": list_attr(el, "SRC_IDS"),
            "tgt_ids": list_attr(el, "TGT_IDS"), "pages": pages,
            "src_text": text_of(el.find("SRC_TEXT")),
            "tgt_text": text_of(el.find("TGT_TEXT")),
        })
    return out
