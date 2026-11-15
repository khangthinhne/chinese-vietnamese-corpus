"""Mô hình dữ liệu + I/O dùng chung cho các tầng SAU extraction.

    02_extraction/<layer>.xml      (do pdf/web/ocr sinh ra)
 -> 03_cleaned/<layer>.jsonl       Sentence đã làm sạch + tách câu   (preprocessing/clean.py)
 -> 04_alignment/<pair>.jsonl      Bead = một cặp/nhóm dóng hàng     (alignment/align.py)
 -> 04_alignment/<name>.tsv|xlsx   file bàn giao                      (alignment/export.py)

Không sửa corpus.py: chỉ dùng lại DataLayout, MetadataStore, LAYERS.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Iterable

from src.common.corpus import LAYERS, DataLayout

log = logging.getLogger("sentences")

CLEANED_DIRNAME = "03_cleaned"
ALIGNMENT_DIRNAME = "04_alignment"


def cleaned_dir(layout: DataLayout) -> Path:
    return layout.work_dir / CLEANED_DIRNAME


def alignment_dir(layout: DataLayout) -> Path:
    return layout.work_dir / ALIGNMENT_DIRNAME


# --------------------------------------------------------------------------- #
# Dữ liệu
# --------------------------------------------------------------------------- #
@dataclass
class Sentence:
    id: str                                    # CPO_18.han.00001
    text: str
    pages: list[int]                           # trang nguồn (>1 phần tử = câu bị cắt qua trang)
    src_ids: list[str]                         # ID dòng ở 02_extraction đã tạo nên câu này
    flags: list[str] = field(default_factory=list)


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


def save_sentences(layout: DataLayout, layer: str, sentences: list[Sentence]) -> Path:
    out = cleaned_dir(layout) / f"{layer}.jsonl"
    write_jsonl(out, (asdict(s) for s in sentences))
    return out


def load_sentences(layout: DataLayout, layer: str) -> list[Sentence]:
    path = cleaned_dir(layout) / f"{layer}.jsonl"
    try:
        rows = read_jsonl(path)
    except FileNotFoundError:
        raise FileNotFoundError(f"Chưa có {path}; chạy src.preprocessing.clean --layer {layer} trước") from None
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
