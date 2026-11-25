"""Giải MỘT gói OCR (từ scripts/pack_ocr.py) vào data/ một cách an toàn. Nhiều gói: scripts/merge_ocr_packs.py.

    python scripts/unpack_ocr.py dist/CPO_19_0015-0121_seed_20261125.zip --dry-run
    python scripts/unpack_ocr.py dist/CPO_19_0015-0121_seed_20261125.zip

- Kiểm tra checksum (MANIFEST.sha256 / SHA256SUMS) trước khi ghi; sai -> dừng, không ghi gì (mã thoát 2).
- Chỉ nhận đường dẫn `<WORK>/03_ocr/...` (chặn `..`, đường dẫn tuyệt đối).
- File chưa có -> ghi; có và giống hệt -> bỏ qua; có nhưng KHÁC -> xung đột, KHÔNG ghi đè, liệt kê (mã thoát 1).
  Riêng `03_ocr/raw/**.json` (append-only): gói chỉ có thêm run vào cuối -> được cập nhật.
  Khác với merge_ocr_packs, các file không xung đột VẪN được ghi khi có xung đột.
"""

from __future__ import annotations

import argparse
import importlib.util
import sys
from pathlib import Path

_spec = importlib.util.spec_from_file_location("merge_ocr_packs", Path(__file__).with_name("merge_ocr_packs.py"))
merge_ocr_packs = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(merge_ocr_packs)

safe_target = merge_ocr_packs.safe_target
raw_is_append = merge_ocr_packs.raw_is_append


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("zip")
    parser.add_argument("--data-root", default="data")
    parser.add_argument("--config", help="Mặc định configs/<work>.json (để báo trang còn thiếu)")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    code, summary = merge_ocr_packs.merge([Path(args.zip)], Path(args.data_root), dry_run=args.dry_run,
                                          keep_going=True, config=Path(args.config) if args.config else None)
    print(merge_ocr_packs.format_summary(summary))
    print(f"   {'(dry-run) ' if args.dry_run else ''}đã ghi {summary['written']} file vào {args.data_root}")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
