"""Xuất bản text sạch theo cấu trúc nộp bài: 03_text_sach/han.txt + viet.txt (mỗi câu một dòng).

    python scripts/export_clean_text.py --work-id CPO_19 [--data-root data] [--out DIR] [--viet-layers am,nghia,tho]

Đọc câu đã làm sạch ở `05_cleaning/<lớp>.xml` (không chuẩn hoá thêm gì), giữ thứ tự tài liệu như trong file.
- `han.txt`: lớp han.
- `viet.txt`: các lớp tiếng Việt theo `--viet-layers`, trong từng tài liệu theo thứ tự lớp đã cho
  (mặc định am -> nghia -> tho); thêm `am.txt`, `nghia.txt`, `tho.txt` riêng từng lớp.
- `mapping.tsv`: file, dòng (1-based), ID câu, tài liệu, lớp, trang, SOURCE_IDS (truy vết về dòng OCR).
Mặc định ghi vào `<data-root>/<WORK>/07_export/03_text_sach/`. Dòng rỗng/không có câu không được ghi.
"""

from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.common.corpus import DataLayout  # noqa: E402
from src.common.sentences import atomic_write_text, find_cleaned_dir, load_sentences  # noqa: E402

VIET_LAYERS = ("am", "nghia", "tho")


def export(layout: DataLayout, out_dir: Path, viet_layers: list[str]) -> dict:
    directory = find_cleaned_dir(layout)
    sentences = {layer: (load_sentences(layout, layer) if (directory / f"{layer}.xml").exists() else [])
                 for layer in ("han", *VIET_LAYERS)}
    if not sentences["han"] and not any(sentences[l] for l in viet_layers):
        raise SystemExit(f"Không có câu đã làm sạch trong {directory}; chạy bước clean/sentence trước")
    out_dir.mkdir(parents=True, exist_ok=True)
    mapping: list[list[str]] = []
    counts: dict[str, int] = {}

    def write(name: str, items: list[tuple[str, object]]) -> None:
        lines = []
        for layer, s in items:
            text = " ".join(s.text.split())
            if not text:
                continue
            lines.append(text)
            mapping.append([name, str(len(lines)), s.id, s.document_id, layer,
                            ",".join(map(str, s.pages)), ",".join(s.source_ids)])
        atomic_write_text(out_dir / name, "\n".join(lines) + ("\n" if lines else ""))
        counts[name] = len(lines)

    write("han.txt", [("han", s) for s in sentences["han"]])
    for layer in VIET_LAYERS:
        if sentences[layer]:
            write(f"{layer}.txt", [(layer, s) for s in sentences[layer]])
    # viet.txt: theo thứ tự tài liệu xuất hiện, trong mỗi tài liệu theo thứ tự lớp đã chọn
    order: list[str] = []
    for layer in viet_layers:
        for s in sentences[layer]:
            if s.document_id not in order:
                order.append(s.document_id)
    items = [(layer, s) for doc in order for layer in viet_layers for s in sentences[layer] if s.document_id == doc]
    write("viet.txt", items)
    with open(out_dir / "mapping.tsv", "w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh, delimiter="\t", lineterminator="\n")
        w.writerow(["file", "line", "sentence_id", "document_id", "layer", "pages", "source_ids"])
        w.writerows(mapping)
    return counts


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--work-id", required=True)
    p.add_argument("--data-root", default="data")
    p.add_argument("--out", help="Thư mục ra (mặc định <data-root>/<WORK>/07_export/03_text_sach)")
    p.add_argument("--viet-layers", default="am,nghia,tho", help="Lớp tiếng Việt gộp vào viet.txt, theo thứ tự")
    args = p.parse_args(argv)
    layout = DataLayout(Path(args.data_root), args.work_id)
    viet = [x.strip() for x in args.viet_layers.split(",") if x.strip()]
    bad = [x for x in viet if x not in VIET_LAYERS]
    if bad:
        p.error(f"--viet-layers chỉ nhận {VIET_LAYERS}, nhận {bad}")
    out = Path(args.out) if args.out else layout.work_dir / "07_export" / "03_text_sach"
    counts = export(layout, out, viet)
    print(f"{out}: " + ", ".join(f"{k} {v} dòng" for k, v in counts.items()) + "; mapping.tsv")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
