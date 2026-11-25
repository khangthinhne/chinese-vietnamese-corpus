"""Kiểm tra OCR thô hai luồng (han/vie) của một work đã đủ chưa. Chỉ đọc, không cần GPU, chạy lại được.

    python scripts/check_ocr_complete.py --work-id CPO_19
    python scripts/check_ocr_complete.py --work-id CPO_20 --json data/CPO_20/03_ocr/ocr_check.json
    python scripts/check_ocr_complete.py --work-id CPO_19 --sample 5 --seed 0    # trang để soát bằng mắt
    python scripts/check_ocr_complete.py --work-id CPO_19 --all-issues

Phạm vi trang lấy từ `ocr.streams.pages` của `configs/<work>.json` (thiếu thì mọi ảnh đã trích); `--pages` ghi đè.
Mã thoát 0 khi không còn lỗi (`error`), 1 khi còn lỗi. Chi tiết mã lỗi: docs/ocr_runbook.md.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.common.corpus import DataLayout  # noqa: E402
from src.ocr import ocr_check, work_config  # noqa: E402


def make_converter():
    """OpenCC s2t từng chữ; None nếu chưa cài opencc (bỏ qua kiểm tra chữ giản thể)."""
    try:
        import opencc
    except ImportError:
        logging.warning("Chưa cài opencc: bỏ qua thống kê chữ giản thể")
        return None
    conv = opencc.OpenCC("s2t")
    return conv.convert


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--work-id", required=True)
    parser.add_argument("--data-root", default="data")
    parser.add_argument("--config", help="Mặc định configs/<work-id>.json nếu có")
    parser.add_argument("--pages", help="Ghi đè phạm vi trang, vd 15-121")
    parser.add_argument("--json", help="Ghi báo cáo đầy đủ ra file JSON")
    parser.add_argument("--sample", type=int, default=0, help="In N trang ngẫu nhiên để soát bằng mắt")
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--all-issues", action="store_true", help="In mọi cảnh báo/lỗi (mặc định 40 dòng)")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")

    config = Path(args.config) if args.config else Path("configs") / f"{args.work_id}.json"
    cfg = work_config.load_config(config) if config.exists() else {}
    settings = work_config.stream_settings(cfg)
    layout = DataLayout(Path(args.data_root), args.work_id)
    pages = args.pages or work_config.resolve_pages(settings, layout)
    report = ocr_check.check_work(layout, pages, settings["check"], make_converter())
    report["config"] = str(config) if cfg else None
    if args.sample:
        report["visual_sample"] = {"seed": args.seed, "pages": ocr_check.sample_pages(report, args.sample, args.seed)}
    print(ocr_check.format_report(report, max_issues=10**9 if args.all_issues else 40))
    if args.sample:
        for p in report["visual_sample"]["pages"]:
            print(f"   soát: ảnh {layout.rel(layout.pages_dir)}/page_{p:03d}.png  <->  "
                  f"{layout.rel(layout.ocr_layer_text_path('han', p))}, {layout.rel(layout.ocr_layer_text_path('vie', p))}")
    if args.json:
        out = Path(args.json)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"   -> {out}")
    return 0 if report["complete"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
