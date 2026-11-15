"""
03_cleaned/<layer>.jsonl (2 lớp) -> dóng hàng -> 04_alignment/<pair>.jsonl

INPUT
    --work-id CPO_18  --pair han-am | han-nghia | han-tho   [--data-root data]
    --method levenshtein | length | ...   (xem src/alignment/aligners.py; mặc định: levenshtein cho han-am,
                                           length cho các cặp khác cho tới khi có clc/bertalign/llm)
    --opt KEY=VALUE        (lặp lại) tuỳ chọn riêng của aligner, vd --opt reading_dict=resources/han_viet.tsv
    --low-score 0.6        cặp có score thấp hơn bị gắn cờ low_score để ưu tiên rà soát

OUTPUT (data/<WORK_ID>/04_alignment/)
    <pair>.jsonl    mỗi dòng 1 Bead: id, kind (1-1, 1-2, 2-1, 1-0, 0-1...), score, flags,
                    src_ids/tgt_ids (ID câu ở 03_cleaned), pages, src_text, tgt_text
    02_extraction/metadata.json -> layers["<pair>_alignment"]

Chưa tạo file bàn giao: bước đó là src/alignment/export.py.

Ví dụ:
    python -m src.alignment.align --work-id CPO_18 --pair han-am --method levenshtein \\
        --opt reading_dict=resources/han_viet.tsv
    python -m src.alignment.align --work-id CPO_18 --pair han-nghia --method length
"""

from __future__ import annotations

import argparse
import logging
import sys
from collections import Counter
from pathlib import Path

from src.alignment.aligners import ALIGNERS
from src.common.corpus import DataLayout, MetadataStore
from src.common.sentences import (alignment_dir, join_texts, load_sentences, parse_args_by_pair,
                                  parse_pair, write_jsonl)

log = logging.getLogger("align")


def default_method(pair: str) -> str:
    return "levenshtein" if pair == "han-am" else "length"


def parse_opts(items: list[str] | None) -> dict[str, str]:
    out = {}
    for item in items or []:
        key, sep, value = item.partition("=")
        if not sep or not key:
            raise ValueError(f"--opt phải dạng KEY=VALUE: {item!r}")
        out[key.strip()] = value
    return out


def run(args) -> Path:
    src_layer, tgt_layer = parse_pair(args.pair)
    method = args.method or default_method(args.pair)
    if method not in ALIGNERS:
        raise ValueError(f"Phương pháp không hợp lệ: {method}. Có sẵn: {', '.join(sorted(ALIGNERS))}")
    layout = DataLayout(Path(args.data_root), args.work_id)
    out = alignment_dir(layout) / f"{args.pair}.jsonl"
    if out.exists() and not args.force:
        raise FileExistsError(f"{out} đã tồn tại; dùng --force để ghi đè")

    src = load_sentences(layout, src_layer)
    tgt = load_sentences(layout, tgt_layer)
    aligner = ALIGNERS[method](**parse_opts(args.opt))
    log.info("Dóng %s: %d câu %s ↔ %d câu %s bằng %s", args.pair, len(src), src_layer, len(tgt), tgt_layer, method)
    beads = aligner.align(src_layer, tgt_layer, src, tgt)

    # kiểm tra hợp đồng: phủ đủ, đúng thứ tự
    for side, n, getter in (("src", len(src), lambda b: b.src), ("tgt", len(tgt), lambda b: b.tgt)):
        flat = [i for b in beads for i in getter(b)]
        if flat != list(range(n)):
            raise RuntimeError(f"Aligner {method} vi phạm hợp đồng: chỉ số {side} không phủ đủ/đúng thứ tự")

    rows = []
    for k, b in enumerate(beads, 1):
        s_items, t_items = [src[i] for i in b.src], [tgt[i] for i in b.tgt]
        flags = list(b.flags)
        if "unaligned" not in flags and b.score < args.low_score:
            flags.append("low_score")
        if any("cross_page" in x.flags for x in s_items + t_items):
            flags.append("cross_page")
        rows.append({
            "id": f"{args.work_id}.{args.pair}.{k:05d}", "kind": b.kind, "score": round(b.score, 4),
            "flags": flags,
            "src_ids": [x.id for x in s_items], "tgt_ids": [x.id for x in t_items],
            "pages": {src_layer: sorted({p for x in s_items for p in x.pages}),
                      tgt_layer: sorted({p for x in t_items for p in x.pages})},
            "src_text": join_texts(src_layer, (x.text for x in s_items)),
            "tgt_text": join_texts(tgt_layer, (x.text for x in t_items)),
        })
    write_jsonl(out, rows)

    stats = {"src_sentences": len(src), "tgt_sentences": len(tgt), "beads": len(rows),
             "kinds": dict(Counter(r["kind"] for r in rows)),
             "unaligned": sum("unaligned" in r["flags"] for r in rows),
             "low_score": sum("low_score" in r["flags"] for r in rows)}
    MetadataStore(layout).update(f"{args.pair}_alignment", {
        "output": layout.rel(out), "aligner": aligner.describe(), "stats": stats,
        "low_score_threshold": args.low_score, "review_required": True})
    log.info("%s", stats)
    return out


def create_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Dóng hàng hai lớp đã làm sạch -> 04_alignment/<pair>.jsonl",
                                formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    p.add_argument("--config", help="JSON: {'common': {...}, 'pairs': {'han-am': {...}}}")
    p.add_argument("--work-id")
    p.add_argument("--data-root", default="data")
    p.add_argument("--pair", required=True, help="han-am | han-nghia | han-tho")
    p.add_argument("--method", help=f"Một trong: {', '.join(sorted(ALIGNERS))}")
    p.add_argument("--opt", action="append", help="KEY=VALUE cho aligner (lặp lại được)")
    p.add_argument("--low-score", type=float, default=0.6)
    p.add_argument("--force", action="store_true")
    p.add_argument("-v", "--verbose", action="store_true")
    return p


def main(argv: list[str] | None = None) -> int:
    parser = create_parser()
    args = parse_args_by_pair(parser, argv)
    try:
        run(args)
    except (OSError, ValueError, RuntimeError) as exc:
        log.error("%s", exc)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
