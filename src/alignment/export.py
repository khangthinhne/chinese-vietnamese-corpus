"""
06_alignment/<pair>.xml -> file bàn giao .tsv + .xlsx (+ báo cáo tự kiểm tra)
hoặc --pair han-viet -> một workbook 4 cột Hán | Dịch âm | Dịch nghĩa | Dịch thơ.

INPUT
    --work-id CPO_18 --pair han-am|han-nghia|han-tho|han-viet     [--data-root data]
    --name CPO_18_parallel             tên file (mặc định <work-id>_<pair>_parallel)
    --min-score 0.0                    chỉ xuất cặp có score >= ngưỡng
    --exclude-flag low_score           (lặp lại) bỏ cặp mang cờ này

OUTPUT (data/<WORK_ID>/07_export/)
    <name>.tsv           <CÂU_HÁN>\\t<CÂU_VIỆT>, UTF-8, LF, không header (cặp đôi)
    <name>.xlsx          DONG_HANG + REVIEW (cặp đôi); 4 cột cho han-viet
    <name>_report.json   số cặp, số câu, câu bị bỏ, phân bố kind, tự kiểm tra
    <name>_report.md     bản đọc được của báo cáo
"""

from __future__ import annotations

import csv 
import argparse
import json
import logging
import re
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from openpyxl import Workbook, load_workbook

from src.common import deps
from src.common.corpus import DataLayout, MetadataStore
from src.common.sentences import (atomic_write_text, export_dir, find_alignment_file,
                                  find_cleaned_file, load_sentences, parse_args_by_pair,
                                  parse_pair)
from src.alignment.align import COLUMN_NAMES
from src.alignment.serialization import read_alignments

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
    rv.append(["row", "id", "document_id", "kind", "score", "flags", "pages_han",
               "pages_viet", "src_ids", "tgt_ids"])
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


def run(args) -> Path | None:
    if args.pair == "han-viet":
        return run_consolidated(args)
    src_layer, tgt_layer = parse_pair(args.pair)
    if src_layer != "han":
        raise ValueError("Cột Hán phải đứng trước: dùng --pair han-<lớp>")
    layout = DataLayout(Path(args.data_root), args.work_id)
    out_dir = export_dir(layout)
    alignment_path = find_alignment_file(layout, args.pair)
    name = args.name or f"{args.work_id}_{args.pair}_parallel"
    tsv, xlsx = out_dir / f"{name}.tsv", out_dir / f"{name}.xlsx"
    if not alignment_path.exists():
        log.warning("Bỏ qua xuất %s: chưa có %s (lớp đích có thể vắng mặt)",
                    args.pair, alignment_path.name)
        return None
    deps.check_fresh(layout, f"export:{args.pair}",
                     [alignment_path, find_cleaned_file(layout, src_layer),
                      find_cleaned_file(layout, tgt_layer)],
                     [tsv, xlsx], args.force,
                     f"{tsv.name}/{xlsx.name} đã tồn tại; dùng --force để ghi đè")
    beads = read_alignments(alignment_path)

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
        review.append([len(rows) + 1, b["id"], b.get("document_id", ""), b["kind"], b["score"],
                       ",".join(b["flags"]),
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
    deps.record(layout, f"export:{args.pair}",
                [alignment_path, find_cleaned_file(layout, src_layer),
                 find_cleaned_file(layout, tgt_layer)], [tsv, xlsx])

    n_src, n_tgt = len(load_sentences(layout, src_layer)), len(load_sentences(layout, tgt_layer))
    report = {
        "name": name, "pair": args.pair, "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "pairs_exported": len(rows), "han_sentences": n_src, f"{tgt_layer}_sentences": n_tgt,
        "kinds_exported": dict(Counter(r[3] for r in review)), "skipped": dict(skipped),
        "low_score_exported": sum("low_score" in r[5] for r in review),
        "cross_page_exported": sum("cross_page" in r[5] for r in review),
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


VIET_LAYERS = ("am", "nghia", "tho")


def _consolidated_rows(layout: DataLayout, args) -> tuple[list[tuple], list[list], dict]:
    han = load_sentences(layout, "han")
    if not han:
        raise ValueError("Không có câu Hán để xuất workbook tổng hợp")
    index = {s.id: i for i, s in enumerate(han)}
    exclude = set(args.exclude_flag or [])
    translations: dict[str, dict[str, list[str]]] = {layer: {} for layer in VIET_LAYERS}
    review: list[list] = []
    skipped = Counter()
    for layer in VIET_LAYERS:
        pair = f"han-{layer}"
        path = find_alignment_file(layout, pair)
        if not path.exists():
            log.warning("Không có %s; cột '%s' để trống", path.name, COLUMN_NAMES.get(layer, layer))
            skipped[f"{layer}:missing_alignment"] += 1
            continue
        for bead in read_alignments(path):
            status = "included"
            if "unaligned" in bead["flags"]:
                status = "skipped:unaligned"
            elif bead["score"] < args.min_score:
                status = "skipped:below_min_score"
            elif exclude & set(bead["flags"]):
                status = f"skipped:flag:{sorted(exclude & set(bead['flags']))[0]}"
            if status != "included":
                skipped[status] += 1
            first = index.get(bead["src_ids"][0]) if bead["src_ids"] else None
            review.append([
                first + 2 if first is not None else None, pair, bead["id"],
                bead.get("document_id", ""), bead["kind"], bead["score"],
                ",".join(bead["flags"]), ",".join(bead["src_ids"]),
                ",".join(bead["tgt_ids"]),
                "; ".join(f"{k}={','.join(map(str, v))}" for k, v in bead["pages"].items()),
                status])
            if status != "included":
                continue
            text = sanitize(bead["tgt_text"])
            for sid in bead["src_ids"]:
                values = translations[layer].setdefault(sid, [])
                if text and text not in values:
                    values.append(text)
    rows = []
    for sentence in han:
        rows.append((sanitize(sentence.text),
                     *[" ".join(translations[layer].get(sentence.id, [])) or None
                       for layer in VIET_LAYERS]))
    return rows, review, dict(skipped)


def write_consolidated(path: Path, rows: list[tuple],  review: list[list]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)

    headers = ["Hán", "Dịch âm", "Dịch nghĩa", "Dịch thơ"]

    # 1. Export TSV
    tsv_path = path.with_suffix(".tsv")
    tsv_tmp = tsv_path.with_suffix(".tsv.tmp")
    with tsv_tmp.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(
            f,
            delimiter="\t",
            quoting=csv.QUOTE_MINIMAL,
            lineterminator="\n"
        )
        writer.writerow(headers)
        writer.writerows([
            ["" if value is None else str(value) for value in row]
            for row in rows
        ])

    # 2. Export XLSX
    wb = Workbook()
    ws = wb.active
    ws.title = "DONG_HANG"
    ws.append(headers)

    for values in rows:
        ws.append([
            "" if value is None else str(value)
            for value in values
        ])

    for col, width in zip("ABCD", (60, 60, 60, 60)):
        ws.column_dimensions[col].width = width

    rv = wb.create_sheet("REVIEW")
    rv.append([
        "row", "pair", "alignment_id", "document_id", "kind",
        "score", "flags", "src_ids", "tgt_ids", "pages", "status"
    ])

    for row in review:
        rv.append(row)

    xlsx_tmp = path.with_suffix(".xlsx.tmp")
    wb.save(xlsx_tmp)

    # 3. Replace output files after successful generation
    xlsx_tmp.replace(path)
    tsv_tmp.replace(tsv_path)


def verify_consolidated(path: Path, rows: list[tuple]) -> list[str]:
    errors = []
    wb = load_workbook(path)
    if "DONG_HANG" not in wb.sheetnames:
        return ["XLSX thiếu sheet DONG_HANG"]
    ws = wb["DONG_HANG"]
    if ws.merged_cells.ranges:
        errors.append("DONG_HANG có ô gộp")
    if [c.value for c in ws[1]] != ["Hán", "Dịch âm", "Dịch nghĩa", "Dịch thơ"]:
        errors.append("DONG_HANG phải có đúng 4 cột Hán | Dịch âm | Dịch nghĩa | Dịch thơ")
    if ws.max_column != 4:
        errors.append(f"DONG_HANG có {ws.max_column} cột, kỳ vọng 4")
    if ws.max_row != len(rows) + 1:
        errors.append(f"DONG_HANG có {ws.max_row - 1} dòng, kỳ vọng {len(rows)}")
    for n, row in enumerate(ws.iter_rows(min_row=2), 2):
        if any(c.data_type == "f" for c in row):
            errors.append("DONG_HANG có công thức")
            break
        if not row[0].value:
            errors.append(f"dòng {n}: thiếu câu Hán")
    return errors


def empty_column_counts(rows: list[tuple]) -> dict[str, int]:
    """Số dòng Hán có ô trống ở từng cột dịch (theo thứ tự VIET_LAYERS)."""
    return {layer: sum(1 for row in rows if not row[1 + k]) for k, layer in enumerate(VIET_LAYERS)}


def warn_empty_columns(rows: list[tuple]) -> dict[str, int]:
    """Cảnh báo rõ khi một cột Dịch âm/Dịch nghĩa/Dịch thơ trống toàn bộ hoặc một phần."""
    counts = empty_column_counts(rows)
    for layer, empty in counts.items():
        column = COLUMN_NAMES.get(layer, layer)
        if rows and empty == len(rows):
            log.warning("Cột '%s' TRỐNG HOÀN TOÀN (0/%d dòng Hán có bản dịch): kiểm tra bước align han-%s",
                        column, len(rows), layer)
        elif empty:
            log.warning("Cột '%s' trống ở %d/%d dòng Hán", column, empty, len(rows))
    return counts


def run_consolidated(args) -> Path:
    layout = DataLayout(Path(args.data_root), args.work_id)
    out_dir = export_dir(layout)
    name = args.name or f"{args.work_id}_han-viet_parallel"
    xlsx = out_dir / f"{name}.xlsx"
    inputs = [find_cleaned_file(layout, "han")]
    for layer in VIET_LAYERS:
        path = find_alignment_file(layout, f"han-{layer}")
        if path.exists():
            inputs.append(path)
    deps.check_fresh(layout, "export:han-viet", inputs, [xlsx], args.force,
                     f"{xlsx.name} đã tồn tại; dùng --force để ghi đè")
    rows, review, skipped = _consolidated_rows(layout, args)
    warn_empty_columns(rows)
    # print(f"[TESTTTTTTTTTT] Writing consolidated XLSX to {xlsx}")
    write_consolidated(xlsx, rows, review)
    errors = verify_consolidated(xlsx, rows)
    if errors:
        raise RuntimeError("Tự kiểm tra workbook tổng hợp thất bại:\n  " + "\n  ".join(errors[:10]))
    deps.record(layout, "export:han-viet", inputs, [xlsx])
    MetadataStore(layout).update("han-viet_export", {
        "name": name, "output": layout.rel(xlsx), "rows": len(rows),
        "missing_translations": {
            layer: sum(1 for row in rows if row[1 + VIET_LAYERS.index(layer)] is None)
            for layer in VIET_LAYERS},
        "skipped": skipped, "review_required": True})
    log.info("Đã xuất %d dòng Hán -> %s", len(rows), xlsx)
    return xlsx


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
    p = argparse.ArgumentParser(description="Xuất TSV/XLSX từ 06_alignment/<pair>.xml; "
                                            "han-viet = workbook 4 cột",
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
