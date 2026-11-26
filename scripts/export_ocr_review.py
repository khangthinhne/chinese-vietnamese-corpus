"""Danh sách dòng nghi lỗi OCR để người rà soát (chỉ xuất, chưa có cơ chế áp bản sửa).

    python scripts/export_ocr_review.py --work-id CPO_20 [--data-root data] [--out reports/review_ocr_CPO_20.xlsx]

Mỗi dòng: loại, trang, ID (dòng OCR hoặc tài liệu), chi tiết, văn bản, ảnh trang cần mở. Loại:
- `qwen_low_conf`: dòng vie.xml có CONFIDENCE < --low-conf (mặc định 60; thang 0..100, không phải độ chính xác).
- `vie_unconfirmed_line` / `vie_cross_low_precision` / `vie_cross_low_recall`: so với chữ Latin paddlevl đọc trên cùng
  trang (`src/ocr/cross_check.py`, cùng ngưỡng với check_ocr_complete).
- `han_am_mismatch`: tài liệu có số chữ Hán và số âm tiết lệch > 10% (sau clean cấu trúc, như audit_segmentation).
- `han_simplified`: chữ giản thể trong dòng han.xml (OpenCC s2t; bỏ qua nếu chưa cài opencc).
Sheet chia theo khoảng trang phụ trách (`ASSIGNMENTS`, docs/ocr_assignment.md); trang ngoài mọi khoảng vào sheet "khac".
"""

from __future__ import annotations

import argparse
import importlib.util
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.common.corpus import DataLayout, parse_pages  # noqa: E402
from src.common.xmlstore import file_element  # noqa: E402
from src.ocr import raw_store  # noqa: E402
from src.ocr.build_layers import LAYER_WRITING_SYSTEMS, preferred_keys, select_run  # noqa: E402
from src.ocr.cross_check import compare_page, latin_lines  # noqa: E402
from src.ocr.work_config import CHECK_DEFAULTS  # noqa: E402

# Khoảng trang của từng thành viên (docs/ocr_assignment.md)
ASSIGNMENTS = {
    "CPO_18": [("tv1_205-544", "205-544")],
    "CPO_19": [("tv2_5-623", "5-623"), ("tv3_624-833", "624-833")],
    "CPO_20": [("ca_work", "5-806")],
}
COLUMNS = ["loai", "trang", "id", "chi_tiet", "van_ban", "anh_trang"]


def stream_lines(layout: DataLayout, stream: str) -> list[dict]:
    path = layout.ocr_layer_xml_path(stream)
    if not path.exists():
        return []
    root = file_element(ET.parse(path).getroot())
    out = []
    for page in root.findall("PAGE"):
        p = int(page.get("NUMBER"))
        for ln in page.findall("LINE"):
            conf = ln.get("CONFIDENCE")
            out.append({"page": p, "id": f"{stream}.P{p:04d}.{ln.get('ID').split('.')[-1]}", "text": ln.text or "",
                        "conf": float(conf) if conf not in (None, "") else None})
    return out


def image_path(layout: DataLayout, page: int) -> str:
    for name in (f"page_{page:03d}.png", f"page_{page:04d}.png"):
        if (layout.pages_dir / name).exists():
            return str(layout.pages_dir / name)
    return str(layout.pages_dir / f"page_{page:03d}.png")


def collect(layout: DataLayout, low_conf: float = 60.0, converter=None, audit_mod=None) -> list[dict]:
    rows: list[dict] = []
    vie = stream_lines(layout, "vie")
    han = stream_lines(layout, "han")

    def add(kind, page, ident, detail, text):
        rows.append({"loai": kind, "trang": page, "id": ident, "chi_tiet": detail, "van_ban": text[:300],
                     "anh_trang": image_path(layout, page)})

    for ln in vie:
        if ln["conf"] is not None and ln["conf"] < low_conf:
            add("qwen_low_conf", ln["page"], ln["id"], f"conf {ln['conf']:.1f}", ln["text"])
    # so với paddlevl
    by_page: dict[int, list[dict]] = {}
    for ln in vie:
        by_page.setdefault(ln["page"], []).append(ln)
    prefs = preferred_keys(layout, "han", "paddlevl")
    for page, lines in sorted(by_page.items()):
        runs = raw_store.page_runs(layout, "paddlevl", page, layout.work_id, LAYER_WRITING_SYSTEMS["han"])
        run, _ = select_run(runs, "paddlevl", prefs.get(page, []), layer="han")
        cross = compare_page(latin_lines(run), [x["text"] for x in lines],
                             line_min_precision=CHECK_DEFAULTS["vie_cross_line_precision"])
        if cross is None:
            continue
        if cross["precision"] < CHECK_DEFAULTS["vie_cross_min_precision"]:
            add("vie_cross_low_precision", page, f"vie.P{page:04d}", f"precision {cross['precision']}", "")
        if cross["recall"] < CHECK_DEFAULTS["vie_cross_min_recall"]:
            add("vie_cross_low_recall", page, f"vie.P{page:04d}", f"recall {cross['recall']}", "")
        for u in cross["unsupported"]:
            ln = lines[u["line"] - 1]
            add("vie_unconfirmed_line", page, ln["id"], f"precision dòng {u['precision']}", ln["text"])
    # Hán-âm lệch theo tài liệu (dùng chính phép đo của audit_segmentation)
    if audit_mod is not None:
        from src.segmentation import serialization as ser
        struct = {k: ser.read_structural_layer(layout.cleaning_structural(k))
                  for k in ("han", "am") if layout.cleaning_structural(k).exists()}
        if len(struct) == 2:
            ha = audit_mod.han_am_consistency(struct["han"], struct["am"])
            for doc, d in sorted(ha["per_doc"].items()):
                if d["diff"] > 0.10:
                    add("han_am_mismatch", d["pages"][0], doc,
                        f"{d['han_chars']} chữ Hán / {d['am_syllables']} âm tiết (lệch {d['diff']:.0%}), "
                        f"trang {d['pages'][0]}-{d['pages'][-1]}", " | ".join(d["example"]))
    if converter is not None:
        from src.ocr.ocr_check import simplified_chars
        for ln in han:
            simp = simplified_chars(ln["text"], converter)
            if simp:
                add("han_simplified", ln["page"], ln["id"], "".join(simp), ln["text"])
    return sorted(rows, key=lambda r: (r["trang"], r["loai"], r["id"]))


def split_sheets(rows: list[dict], work_id: str) -> dict[str, list[dict]]:
    sheets: dict[str, list[dict]] = {name: [] for name, _ in ASSIGNMENTS.get(work_id, [])}
    ranges = [(name, set(parse_pages(spec))) for name, spec in ASSIGNMENTS.get(work_id, [])]
    for r in rows:
        name = next((n for n, pages in ranges if r["trang"] in pages), "khac")
        sheets.setdefault(name, []).append(r)
    return sheets


def write_xlsx(path: Path, sheets: dict[str, list[dict]]) -> None:
    from openpyxl import Workbook
    wb = Workbook()
    wb.remove(wb.active)
    for name, rows in sheets.items():
        ws = wb.create_sheet(name[:31])
        ws.append(COLUMNS)
        for r in rows:
            ws.append([r[c] for c in COLUMNS])
        ws.freeze_panes = "A2"
    path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(path)


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--work-id", required=True)
    p.add_argument("--data-root", default="data")
    p.add_argument("--out", help="Mặc định reports/review_ocr_<WORK>.xlsx")
    p.add_argument("--low-conf", type=float, default=60.0)
    args = p.parse_args(argv)
    layout = DataLayout(Path(args.data_root), args.work_id)
    try:
        import opencc
        converter = opencc.OpenCC("s2t").convert
    except ImportError:
        converter = None
    spec = importlib.util.spec_from_file_location("audit_seg", Path(__file__).with_name("audit_segmentation.py"))
    audit_mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(audit_mod)
    rows = collect(layout, args.low_conf, converter, audit_mod)
    sheets = split_sheets(rows, args.work_id)
    out = Path(args.out) if args.out else Path("reports") / f"review_ocr_{args.work_id}.xlsx"
    write_xlsx(out, sheets)
    kinds: dict[str, int] = {}
    for r in rows:
        kinds[r["loai"]] = kinds.get(r["loai"], 0) + 1
    print(f"{out}: {len(rows)} dòng {kinds}; sheet " + ", ".join(f"{k} {len(v)}" for k, v in sheets.items())
          + ("" if converter else " (chưa cài opencc: bỏ qua han_simplified)"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
