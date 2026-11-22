"""Mô hình dữ liệu + I/O dùng chung cho các tầng SAU extraction.

    03_ocr/ocr_raw/<he>.xml          dòng OCR đã chọn (do ocr sinh ra)
 -> 05_cleaning/<layer>.xml         Sentence đã làm sạch + tách câu   (preprocessing/clean.py)
 -> 06_alignment/<pair>.xml         Bead = một cặp/nhóm dóng hàng     (alignment/align.py)
 -> 07_export/<name>.tsv|xlsx       file bàn giao                      (alignment/export.py)

JSONL cũ vẫn đọc được để tương thích dữ liệu đã sinh trước đây.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

from src.common.corpus import LAYERS, DataLayout
from src.common.xmlstore import (add, file_element, int_list_attr, list_attr, read_root,
                                 text_of, write_file_root, write_root)

log = logging.getLogger("sentences")

CLEANED_DIRNAME = "05_cleaning"
LEGACY_CLEANED_DIRNAME = "03_cleaned"
ALIGNMENT_DIRNAME = "06_alignment"
LEGACY_ALIGNMENT_DIRNAME = "04_alignment"
EXPORT_DIRNAME = "07_export"


def cleaned_dir(layout: DataLayout) -> Path:
    """Nơi GHI kết quả clean (layout mới)."""
    return layout.cleaning_root


def find_cleaned_dir(layout: DataLayout) -> Path:
    """Đọc: ưu tiên layout mới, lùi về 03_cleaned của dữ liệu cũ (không xoá gì)."""
    new = layout.cleaning_root
    if new.exists():
        return new
    legacy = layout.work_dir / LEGACY_CLEANED_DIRNAME
    return legacy if legacy.exists() else new


def alignment_dir(layout: DataLayout) -> Path:
    """Nơi GHI kết quả dóng hàng (layout mới)."""
    return layout.alignment_root


def export_dir(layout: DataLayout) -> Path:
    """Nơi GHI file bàn giao TSV/XLSX (layout mới)."""
    return layout.export_root


def find_alignment_dir(layout: DataLayout) -> Path:
    """Đọc: ưu tiên 06_alignment, lùi về 04_alignment của dữ liệu cũ."""
    new = layout.alignment_root
    if new.exists():
        return new
    legacy = layout.work_dir / LEGACY_ALIGNMENT_DIRNAME
    return legacy if legacy.exists() else new


def find_cleaned_file(layout: DataLayout, layer: str) -> Path:
    """Ưu tiên XML; chỉ rơi về JSONL khi XML chưa có nhưng JSONL cũ tồn tại."""
    directory = find_cleaned_dir(layout)
    xml, jsonl = directory / f"{layer}.xml", directory / f"{layer}.jsonl"
    return xml if xml.exists() or not jsonl.exists() else jsonl


def find_alignment_file(layout: DataLayout, pair: str) -> Path:
    """Ưu tiên XML; chỉ rơi về JSONL khi XML chưa có nhưng JSONL cũ tồn tại."""
    directory = find_alignment_dir(layout)
    xml, jsonl = directory / f"{pair}.xml", directory / f"{pair}.jsonl"
    return xml if xml.exists() or not jsonl.exists() else jsonl


# --------------------------------------------------------------------------- #
# Dữ liệu
# --------------------------------------------------------------------------- #
@dataclass
class Sentence:
    id: str                                    # CPO_18.han.00001
    text: str
    pages: list[int]                           # trang nguồn (>1 phần tử = câu bị cắt qua trang)
    segment_ids: list[str] = field(default_factory=list)
    source_ids: list[str] = field(default_factory=list)
    flags: list[str] = field(default_factory=list)
    document_id: str = ""                      # CPO_18.001 (dữ liệu cũ có thể để trống)
    raw_text: str = ""
    src_ids: list[str] = field(default_factory=list)

    def __post_init__(self):
        if self.src_ids and not self.segment_ids:
            self.segment_ids = list(self.src_ids)
        elif self.segment_ids and not self.src_ids:
            self.src_ids = list(self.segment_ids)


@dataclass
class Bead:
    """Một cặp dóng hàng: chỉ số (0-based) vào danh sách Sentence nguồn/đích."""
    src: tuple[int, ...]
    tgt: tuple[int, ...]
    score: float = 0.0                         # 0..1, ý nghĩa tuỳ aligner (xem docstring aligner)
    flags: tuple[str, ...] = ()

    @property
    def kind(self) -> str:
        return f"{len(self.src)}-{len(self.tgt)}"


# --------------------------------------------------------------------------- #
# I/O
# --------------------------------------------------------------------------- #
def atomic_write_text(path: Path, text: str) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def write_jsonl(path: Path, rows: Iterable[dict]) -> None:
    atomic_write_text(path, "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows))


def read_jsonl(path: Path) -> list[dict]:
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(path)
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def save_sentences(layout: DataLayout, layer: str, sentences: list[Sentence],
                   out_path: Path | None = None) -> Path:
    out = out_path or (cleaned_dir(layout) / f"{layer}.xml")
    root = write_file_root(out, layout.work_id)
    layer_el = add(file_element(root), "LAYER", NAME=layer)
    for sentence in sentences:
        segment_ids = getattr(sentence, "segment_ids", []) or getattr(sentence, "src_ids", [])
        source_ids = getattr(sentence, "source_ids", [])
        add(layer_el, "SENTENCE", sentence.text, ID=sentence.id,
            DOCUMENT_ID=sentence.document_id or None,
            PAGES=",".join(str(p) for p in sentence.pages) or None,
            SEGMENT_IDS=",".join(segment_ids) if segment_ids else None,
            SOURCE_IDS=",".join(source_ids) if source_ids else None,
            FLAGS=",".join(sentence.flags) or None,
            RAW_TEXT=getattr(sentence, "raw_text", "") or None)
    return write_root(out, root)


def load_sentences(layout: DataLayout, layer: str, path: Path | None = None) -> list[Sentence]:
    if path is not None:
        xml_path = Path(path)
    else:
        directory = find_cleaned_dir(layout)
        xml_path = directory / f"{layer}.xml"
    if xml_path.exists():
        root = read_root(xml_path)
        layer_el = file_element(root).find("LAYER")
        units = layer_el.findall("SENTENCE") if layer_el is not None else []
        return [Sentence(id=el.get("ID", ""), text=text_of(el),
                         pages=int_list_attr(el, "PAGES"),
                         segment_ids=list_attr(el, "SEGMENT_IDS") or list_attr(el, "SRC_IDS"),
                         source_ids=list_attr(el, "SOURCE_IDS"),
                         flags=list_attr(el, "FLAGS"),
                         document_id=el.get("DOCUMENT_ID", ""),
                         raw_text=el.get("RAW_TEXT", "")) for el in units]
    directory = find_cleaned_dir(layout)
    jsonl_path = directory / f"{layer}.jsonl"
    try:
        rows = read_jsonl(jsonl_path)
    except FileNotFoundError:
        raise FileNotFoundError(
            f"Chưa có {xml_path}; chạy clean --layer {layer} (hoặc pipeline) trước") from None
    return [Sentence(**r) for r in rows]


def join_texts(layer: str, texts: Iterable[str]) -> str:
    """Hán nối liền, các lớp Việt nối bằng một dấu cách."""
    return ("" if layer == "han" else " ").join(texts)


# --------------------------------------------------------------------------- #
# CLI: giống corpus.parse_args nhưng khoá theo --pair thay vì --layer
# --------------------------------------------------------------------------- #
def parse_pair(pair: str) -> tuple[str, str]:
    src, sep, tgt = pair.partition("-")
    if not sep or src not in LAYERS or tgt not in LAYERS or src == tgt:
        raise ValueError(f"--pair phải dạng <lớp>-<lớp> với lớp thuộc {sorted(LAYERS)}, vd han-nghia")
    return src, tgt


def parse_args_by_pair(parser: argparse.ArgumentParser, argv: list[str] | None = None):
    """Ưu tiên: mặc định < config.common < config.pairs[pair] < CLI."""
    pre, _ = parser.parse_known_args(argv)
    if pre.config:
        cfg = json.loads(Path(pre.config).read_text(encoding="utf-8"))
        merged = {**cfg.get("common", {}), **cfg.get("pairs", {}).get(pre.pair, {})}
        known = {a.dest for a in parser._actions}
        parser.set_defaults(**{k: v for k, v in merged.items() if k in known})
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    if not args.work_id:
        parser.error("Thiếu --work-id (CLI hoặc config)")
    return args
