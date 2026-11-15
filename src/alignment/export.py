"""
04_alignment/<pair>.jsonl -> file bàn giao .tsv + .xlsx (+ báo cáo tự kiểm tra)

INPUT
    --work-id CPO_18 --pair han-am     [--data-root data]
    --name CPO_18_parallel             tên file (mặc định <work-id>_<pair>_parallel)
    --min-score 0.0                    chỉ xuất cặp có score >= ngưỡng (mặc định xuất hết cặp hợp lệ)
    --exclude-flag low_score           (lặp lại) bỏ cặp mang cờ này, vd sau khi rà soát

OUTPUT (data/<WORK_ID>/04_alignment/)
    <name>.tsv           <CÂU_HÁN>\\t<CÂU_VIỆT>, UTF-8, LF, không header
    <name>.xlsx          sheet DONG_HANG (đúng 2 cột Hán | Việt, hàng 1 là tiêu đề)
                         + sheet REVIEW (cùng thứ tự hàng: id, kind, score, cờ, trang, ID câu) để rà soát/QA
    <name>_report.json   số cặp, số dòng Hán/Việt, câu bị bỏ, phân bố kind, kết quả tự kiểm tra
    <name>_report.md     bản đọc được của báo cáo trên

Tự kiểm tra (xuất lỗi nếu sai): không ô rỗng, không tab/xuống dòng trong vế, TSV và XLSX đọc lại
khớp từng dòng, không công thức/ô gộp trong DONG_HANG.

Hán luôn ở cột 1: nếu --pair có Hán ở vế thứ hai (vd am-han) thì từ chối, hãy dùng han-<lớp>.
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from openpyxl import Workbook, load_workbook

from src.common.corpus import DataLayout, MetadataStore
from src.common.sentences import (alignment_dir, atomic_write_text, load_sentences, parse_args_by_pair,
                                  parse_pair, read_jsonl)

log = logging.getLogger("export")


def sanitize(text: str) -> str:
    return re.sub(r"\s+", " ", text.replace("\t", " ").replace("\r", " ").replace("\n", " ")).strip()


def validate_rows(rows: list[tuple[str, str]]) -> list[str]:
    errors = []
    for n, (han, vi) in enumerate(rows, 1):
        if not han or not vi:
            errors.append(f"dòng {n}: thiếu một vế")
        for cell in (han, vi):
            if "\t" in cell or "\n" in cell or "\r" in cell:
                errors.append(f"dòng {n}: có tab/xuống dòng trong vế")
    return errors


def write_tsv(path: Path, rows: list[tuple[str, str]]) -> None:
    atomic_write_text(path, "".join(f"{h}\t{v}\n" for h, v in rows))


def write_xlsx(path: Path, rows: list[tuple[str, str]], review: list[list]) -> None:
    wb = Workbook()
    ws = wb.active
    ws.title = "DONG_HANG"
    ws.append(["Hán", "Việt"])
    for r, (h, v) in enumerate(rows, 2):
        for c, val in enumerate((h, v), 1):
            cell = ws.cell(row=r, column=c, value=val)
            cell.data_type = "s"                      # không để chuỗi bắt đầu bằng "=" thành công thức
    ws.column_dimensions["A"].width = 60
    ws.column_dimensions["B"].width = 80
    rv = wb.create_sheet("REVIEW")
    rv.append(["row", "id", "kind", "score", "flags", "pages_han", "pages_viet", "src_ids", "tgt_ids"])
    for row in review:
        rv.append(row)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".xlsx.tmp")
    wb.save(tmp)
    tmp.replace(path)


def verify_outputs(tsv: Path, xlsx: Path, rows: list[tuple[str, str]]) -> list[str]:
    errors = []
    lines = tsv.read_text(encoding="utf-8").split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    if len(lines) != len(rows):
        errors.append(f"TSV có {len(lines)} dòng, kỳ vọng {len(rows)}")
    for n, line in enumerate(lines, 1):
        if line.count("\t") != 1:
            errors.append(f"TSV dòng {n}: phải có đúng 1 tab, có {line.count(chr(9))}")
    wb = load_workbook(xlsx)
    if "DONG_HANG" not in wb.sheetnames:
        return errors + ["XLSX thiếu sheet DONG_HANG"]
    ws = wb["DONG_HANG"]
    if ws.merged_cells.ranges:
        errors.append("DONG_HANG có ô gộp")
    if [c.value for c in ws[1]] != ["Hán", "Việt"] or ws.max_column != 2:
        errors.append("DONG_HANG phải có đúng 2 cột, tiêu đề Hán | Việt")
    xl = [(r[0], r[1]) for r in ws.iter_rows(min_row=2, values_only=True)]
    if xl != rows:
        errors.append("Nội dung XLSX không khớp TSV")
    for row in ws.iter_rows(min_row=2):
        if any(c.data_type == "f" for c in row):
            errors.append("DONG_HANG có công thức")
            break
    return errors


def run(args) -> Path:
    src_layer, tgt_layer = parse_pair(args.pair)
    if src_layer != "han":
        raise ValueError("Cột Hán phải đứng trước: dùng --pair han-<lớp>")
    layout = DataLayout(Path(args.data_root), args.work_id)
    out_dir = alignment_dir(layout)
    beads = read_jsonl(out_dir / f"{args.pair}.jsonl")
    name = args.name or f"{args.work_id}_{args.pair}_parallel"
    tsv, xlsx = out_dir / f"{name}.tsv", out_dir / f"{name}.xlsx"
    if (tsv.exists() or xlsx.exists()) and not args.force:
        raise FileExistsError(f"{tsv.name}/{xlsx.name} đã tồn tại; dùng --force để ghi đè")

    exclude = set(args.exclude_flag or [])
    rows, review, skipped = [], [], Counter()
    for b in beads:
        if "unaligned" in b["flags"]:
            skipped["unaligned"] += 1
            continue
        if b["score"] < args.min_score:
            skipped["below_min_score"] += 1
            continue
        hit = exclude & set(b["flags"])
        if hit:
            skipped[f"flag:{sorted(hit)[0]}"] += 1
            continue
        rows.append((sanitize(b["src_text"]), sanitize(b["tgt_text"])))
        review.append([len(rows) + 1, b["id"], b["kind"], b["score"], ",".join(b["flags"]),
                       ",".join(map(str, b["pages"][src_layer])), ",".join(map(str, b["pages"][tgt_layer])),
                       ",".join(b["src_ids"]), ",".join(b["tgt_ids"])])

    errors = validate_rows(rows)
    if not rows:
        errors.append("Không có cặp nào để xuất")
    if errors:
        raise ValueError("Dữ liệu xuất không hợp lệ:\n  " + "\n  ".join(errors[:10]))
    write_tsv(tsv, rows)
    write_xlsx(xlsx, rows, review)
    errors = verify_outputs(tsv, xlsx, rows)
    if errors:
        raise RuntimeError("Tự kiểm tra thất bại:\n  " + "\n  ".join(errors[:10]))

    n_src, n_tgt = len(load_sentences(layout, src_layer)), len(load_sentences(layout, tgt_layer))
    report = {
        "name": name, "pair": args.pair, "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "pairs_exported": len(rows), "han_sentences": n_src, f"{tgt_layer}_sentences": n_tgt,
        "kinds_exported": dict(Counter(r[2] for r in review)), "skipped": dict(skipped),
        "low_score_exported": sum("low_score" in r[4] for r in review),
        "cross_page_exported": sum("cross_page" in r[4] for r in review),
        "self_check": {"no_empty_cell": True, "no_tab_or_newline_in_cell": True,
                       "tsv_xlsx_identical": True, "no_formula_or_merged_cell": True},
        "params": {"min_score": args.min_score, "exclude_flag": sorted(exclude)},
    } 
    print("Báo cáo tự kiểm tra:\n" + render_report(report))
    atomic_write_text(out_dir / f"{name}_report.json", json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    atomic_write_text(out_dir / f"{name}_report.md", render_report(report))
    MetadataStore(layout).update(f"{args.pair}_export", {"name": name, "report": report, "review_required": True})
    log.info("Đã xuất %d cặp -> %s, %s", len(rows), tsv, xlsx)
    return tsv


def render_report(r: dict) -> str:
    skipped = ", ".join(f"{k}={v}" for k, v in r["skipped"].items()) or "không"
    return (f"# Báo cáo dóng hàng {r['name']}\n\n"
            f"- Cặp: `{r['pair']}`\n- Số cặp xuất: **{r['pairs_exported']}**\n"
            f"- Số câu Hán: {r['han_sentences']}; số câu {r['pair'].split('-')[1]}: "
            f"{r[r['pair'].split('-')[1] + '_sentences']}\n"
            f"- Phân bố kiểu dóng: {r['kinds_exported']}\n- Bị loại khi xuất: {skipped}\n"
            f"- Cặp điểm thấp (cần rà soát): {r['low_score_exported']}; cặp qua trang: {r['cross_page_exported']}\n"
            f"- Tự kiểm tra: không ô rỗng, không tab/xuống dòng trong vế, TSV = XLSX, không công thức/ô gộp: **đạt**\n\n"
            f"Lưu ý: điểm score chỉ để xếp hạng rà soát, không phải độ chính xác. "
            f"Tỉ lệ lỗi thật phải đo bằng mẫu kiểm tra thủ công (OMISSION/ADDITION/MISSING).\n")


def create_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Xuất TSV + XLSX + báo cáo từ 04_alignment/<pair>.jsonl",
                                formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    p.add_argument("--config")
    p.add_argument("--work-id")
    p.add_argument("--data-root", default="data")
    p.add_argument("--pair", required=True)
    p.add_argument("--name", help="Mặc định <work-id>_<pair>_parallel; đặt <MA_TAC_PHAM>_parallel để nộp")
    p.add_argument("--min-score", type=float, default=0.0)
    p.add_argument("--exclude-flag", action="append")
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
