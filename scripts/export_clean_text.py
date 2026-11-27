"""Xuất text sạch mỗi câu một dòng: 05_cleaning/text/han.txt + viet_<lớp>.txt + mapping.tsv.

    python scripts/export_clean_text.py --work-id CPO_19 [--data-root data] [--out DIR] [--viet-layers am,nghia,tho]
    python scripts/export_clean_text.py --work-id CPO_19 --combined      # thêm viet.txt gộp

Đọc câu đã làm sạch ở `05_cleaning/<lớp>.xml` (không chuẩn hoá thêm gì), giữ thứ tự tài liệu như trong file.
- `han.txt`: lớp han.
- `viet_am.txt`, `viet_nghia.txt`, `viet_tho.txt`: từng lớp tiếng Việt (theo `--viet-layers`).
- `viet.txt` (chỉ khi `--combined`): gộp các lớp, trong từng tài liệu theo thứ tự lớp đã cho (mặc định am -> nghia -> tho).
- `mapping.tsv`: file, dòng (1-based), ID câu, tài liệu, lớp, trang, SOURCE_IDS (truy vết về dòng OCR).
Mặc định ghi vào `<data-root>/<WORK>/05_cleaning/text/` (`DataLayout.cleaning_text_dir`). Dòng rỗng không được ghi.
`03_text_sach/` chỉ là tên thư mục trong GÓI NỘP BÀI: `--package-root DIR --topic MA_DE_TAI` chép các file đã xuất
sang `DIR/<MA_DE_TAI>_<WORK>/03_text_sach/` (không tạo 03_text_sach/ trong cây data/).
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


def export(layout: DataLayout, out_dir: Path, viet_layers: list[str], combined: bool = False) -> dict:
    mapping: list[list[str]] = []
    counts = _export(layout, out_dir, viet_layers, combined, mapping)
    write_mapping(out_dir, mapping)
    return counts


def _export(layout: DataLayout, out_dir: Path, viet_layers: list[str], combined: bool,
            mapping: list[list[str]]) -> dict:
    directory = find_cleaned_dir(layout)
    sentences = {layer: (load_sentences(layout, layer) if (directory / f"{layer}.xml").exists() else [])
                 for layer in ("han", *VIET_LAYERS)}
    if not sentences["han"] and not any(sentences[l] for l in viet_layers):
        raise SystemExit(f"Không có câu đã làm sạch trong {directory}; chạy bước clean/sentence trước")
    out_dir.mkdir(parents=True, exist_ok=True)
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
    for layer in viet_layers:
        if sentences[layer]:
            write(f"viet_{layer}.txt", [(layer, s) for s in sentences[layer]])
    if not combined:
        return counts
    # viet.txt (tuỳ chọn --combined): theo thứ tự tài liệu, trong mỗi tài liệu theo thứ tự lớp đã chọn
    order: list[str] = []
    for layer in viet_layers:
        for s in sentences[layer]:
            if s.document_id not in order:
                order.append(s.document_id)
    items = [(layer, s) for doc in order for layer in viet_layers for s in sentences[layer] if s.document_id == doc]
    write("viet.txt", items)
    return counts


def package(out_dir: Path, package_root: Path, topic: str, work_id: str) -> Path:
    """Chép han.txt, viet_*.txt, mapping.tsv sang <package_root>/<topic>_<work_id>/03_text_sach/."""
    import shutil
    dest = package_root / f"{topic}_{work_id}" / "03_text_sach"
    dest.mkdir(parents=True, exist_ok=True)
    for f in sorted(out_dir.iterdir()):
        if f.is_file() and (f.name in ("han.txt", "mapping.tsv", "viet.txt") or f.name.startswith("viet_")):
            shutil.copy2(f, dest / f.name)
    return dest


def write_mapping(out_dir: Path, mapping: list[list[str]]) -> None:
    with open(out_dir / "mapping.tsv", "w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh, delimiter="\t", lineterminator="\n")
        w.writerow(["file", "line", "sentence_id", "document_id", "layer", "pages", "source_ids"])
        w.writerows(mapping)


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--work-id", required=True)
    p.add_argument("--data-root", default="data")
    p.add_argument("--out", help="Thư mục ra (mặc định <data-root>/<WORK>/05_cleaning/text)")
    p.add_argument("--package-root", help="Đóng gói: chép sang <ROOT>/<TOPIC>_<WORK>/03_text_sach/")
    p.add_argument("--topic", help="Mã đề tài cho tên thư mục gói (bắt buộc khi có --package-root)")
    p.add_argument("--viet-layers", default="am,nghia,tho", help="Lớp tiếng Việt xuất ra, theo thứ tự")
    p.add_argument("--combined", action="store_true", help="Ghi thêm viet.txt gộp các lớp tiếng Việt")
    args = p.parse_args(argv)
    layout = DataLayout(Path(args.data_root), args.work_id)
    viet = [x.strip() for x in args.viet_layers.split(",") if x.strip()]
    bad = [x for x in viet if x not in VIET_LAYERS]
    if bad:
        p.error(f"--viet-layers chỉ nhận {VIET_LAYERS}, nhận {bad}")
    if args.package_root and not args.topic:
        p.error("--package-root cần --topic (mã đề tài)")
    out = Path(args.out) if args.out else layout.cleaning_text_dir
    counts = export(layout, out, viet, combined=args.combined)
    print(f"{out}: " + ", ".join(f"{k} {v} dòng" for k, v in counts.items()) + "; mapping.tsv")
    if args.package_root:
        print(f"   gói: {package(out, Path(args.package_root), args.topic, args.work_id)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
