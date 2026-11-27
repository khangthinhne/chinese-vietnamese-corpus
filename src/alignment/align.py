"""
05_cleaning/<layer>.xml (2 lớp) -> dóng hàng THEO TỪNG TÀI LIỆU -> 06_alignment/<pair>.xml

INPUT
    --work-id CPO_18  --pair han-am | han-nghia | han-tho   [--data-root data]
    --method levenshtein | length | ...   (xem src/alignment/aligners.py)
    --opt KEY=VALUE        (lặp lại) tuỳ chọn riêng của aligner
    --low-score 0.6        cặp có score thấp hơn bị gắn cờ low_score

OUTPUT (data/<WORK_ID>/06_alignment/)
    <pair>.xml    mỗi Bead: id, document_id, kind, score, flags,
                  src_ids/tgt_ids (ID câu ở 05_cleaning), pages, src_text, tgt_text
    02_extraction/metadata.json -> layers["<pair>_alignment"]

Nếu lớp đích không tồn tại/rỗng (vd. tác phẩm không có DỊCH THƠ), bước này bỏ qua
thay vì tạo file rỗng hoặc lỗi; các artifact cũ không bị xoá.

Sentence.document_id (từ 04_segmentation) chia nhóm; aligner chạy riêng từng tài liệu,
không bao giờ bắc cầu hai tài liệu. Dữ liệu cũ document_id rỗng = một nhóm duy nhất.
"""

from __future__ import annotations

import argparse
import logging
import sys
from collections import Counter
from pathlib import Path

from src.alignment import serialization
from src.alignment.aligners import ALIGNERS
from src.common import deps
from src.common.corpus import DataLayout, MetadataStore
from src.common.sentences import (alignment_dir, find_cleaned_file, join_texts, load_sentences,
                                  parse_args_by_pair, parse_pair)

# Tên cột trong workbook han-viet ứng với từng lớp đích (dùng cho cảnh báo).
COLUMN_NAMES = {"am": "Dịch âm", "nghia": "Dịch nghĩa", "tho": "Dịch thơ"}

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


def run(args) -> Path | None:
    src_layer, tgt_layer = parse_pair(args.pair)
    method = args.method or default_method(args.pair)
    if method not in ALIGNERS:
        raise ValueError(f"Phương pháp không hợp lệ: {method}. Có sẵn: {', '.join(sorted(ALIGNERS))}")
    layout = DataLayout(Path(args.data_root), args.work_id)
    out = alignment_dir(layout) / f"{args.pair}.xml"

    src = load_sentences(layout, src_layer)
    try:
        tgt = load_sentences(layout, tgt_layer)
    except FileNotFoundError:
        log.warning("Bỏ qua %s: chưa có lớp %s (tác phẩm không có phần này?)",
                    args.pair, tgt_layer)
        return None
    if not tgt:
        log.warning("Bỏ qua %s: lớp %s rỗng; không tạo alignment giả", args.pair, tgt_layer)
        return None
    inputs = [find_cleaned_file(layout, src_layer), find_cleaned_file(layout, tgt_layer)]
    deps.check_fresh(layout, f"align:{args.pair}", inputs, [out], args.force,
                     f"{out} đã tồn tại; dùng --force để ghi đè")
    aligner = ALIGNERS[method](**parse_opts(args.opt))
    aligner.prepare(layout, src_layer, tgt_layer, src, tgt)
    groups: dict[str, tuple[list[int], list[int]]] = {}
    for i, s in enumerate(src):
        groups.setdefault(s.document_id, ([], []))[0].append(i)
    for i, s in enumerate(tgt):
        groups.setdefault(s.document_id, ([], []))[1].append(i)
    log.info("Dóng %s: %d câu %s ↔ %d câu %s bằng %s (%d tài liệu)", args.pair, len(src),
             src_layer, len(tgt), tgt_layer, method, len(groups))
    column = COLUMN_NAMES.get(tgt_layer, tgt_layer)
    no_tgt = [doc for doc, (s_idx, t_idx) in groups.items() if s_idx and not t_idx]
    no_src = [doc for doc, (s_idx, t_idx) in groups.items() if t_idx and not s_idx]
    if no_tgt:
        log.warning("%d/%d tài liệu có câu %s nhưng KHÔNG có câu %s -> cột '%s' sẽ trống cho các câu đó: %s",
                    len(no_tgt), len(groups), src_layer, tgt_layer, column, ", ".join(d or "<trống>" for d in no_tgt))
    if no_src:
        log.warning("%d/%d tài liệu có câu %s nhưng KHÔNG có câu %s (câu %s không có cặp): %s",
                    len(no_src), len(groups), tgt_layer, src_layer, tgt_layer,
                    ", ".join(d or "<trống>" for d in no_src))

    rows = []
    for doc_id, (s_idx, t_idx) in groups.items():
        local_src = [src[i] for i in s_idx]
        local_tgt = [tgt[i] for i in t_idx]
        beads = aligner.align(src_layer, tgt_layer, local_src, local_tgt)
        for side, n, getter in (("src", len(local_src), lambda b: b.src),
                                ("tgt", len(local_tgt), lambda b: b.tgt)):
            flat = [i for b in beads for i in getter(b)]
            if flat != list(range(n)):
                raise RuntimeError(f"Aligner {method} vi phạm hợp đồng: chỉ số {side} "
                                   f"không phủ đủ/đúng thứ tự (tài liệu {doc_id or '<trống>'})")
        for b in beads:
            s_items = [local_src[i] for i in b.src]
            t_items = [local_tgt[i] for i in b.tgt]
            flags = list(b.flags)
            if "unaligned" not in flags and b.score < args.low_score:
                flags.append("low_score")
            if any("cross_page" in x.flags for x in s_items + t_items):
                flags.append("cross_page")
            rows.append({
                "id": f"{args.work_id}.{args.pair}.{len(rows) + 1:05d}", "document_id": doc_id,
                "kind": b.kind, "score": round(b.score, 4), "flags": flags,
                "src_ids": [x.id for x in s_items], "tgt_ids": [x.id for x in t_items],
                "pages": {src_layer: sorted({p for x in s_items for p in x.pages}),
                          tgt_layer: sorted({p for x in t_items for p in x.pages})},
                "src_text": join_texts(src_layer, (x.text for x in s_items)),
                "tgt_text": join_texts(tgt_layer, (x.text for x in t_items)),
            })
    serialization.write_alignments(out, args.work_id, args.pair, src_layer, tgt_layer, rows)
    deps.record(layout, f"align:{args.pair}", inputs, [out])

    stats = {"src_sentences": len(src), "tgt_sentences": len(tgt), "beads": len(rows),
             "documents": len(groups),
             "kinds": dict(Counter(r["kind"] for r in rows)),
             "unaligned": sum("unaligned" in r["flags"] for r in rows),
             "low_score": sum("low_score" in r["flags"] for r in rows),
             "documents_without_tgt": no_tgt, "documents_without_src": no_src}
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
    except deps.UpToDate as exc:          # đầu ra còn mới: bỏ qua, không phải lỗi
        log.info("%s", exc)
        return 0
    except (OSError, ValueError, RuntimeError) as exc:
        log.error("%s", exc)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
