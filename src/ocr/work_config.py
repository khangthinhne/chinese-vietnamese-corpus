"""Cấu hình OCR hai luồng theo work: khối `ocr.streams` trong `configs/CPO_xx.json`.

    "ocr": {
      "streams": {
        "pages": "5-806",                        # khoảng trang cần OCR (mặc định: mọi ảnh đã trích)
        "paddlevl_orientation": "horizontal",    # auto | horizontal | vertical (hướng chữ Hán)
        "engines": {"han": "paddlevl", "vie": "qwen"},
        "check": {"qwen_low_conf": 60},          # ngưỡng của scripts/check_ocr_complete.py
        "source_sha256": "1c08...",              # SHA-256 của PDF nguồn: mọi máy phải dùng ĐÚNG file này
        "source_pages": 854                      # số trang PDF (pdfinfo); số trang OCR = chỉ số trang PDF
      }
    }

Thứ tự ưu tiên: biến môi trường (vd `PADDLEVL_ORIENTATION`) > config > mặc định. Thiếu khối này thì mọi thứ
chạy như trước (tương thích ngược). Không import engine nào: dùng được ở cả `my_nlp` lẫn `.venv-paddle`.

    python -m src.ocr.work_config --config configs/CPO_20.json --get pages
    python -m src.ocr.work_config --config configs/CPO_20.json --show
    python -m src.ocr.work_config --config configs/CPO_20.json --verify-source   # so SHA-256 của PDF
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import sys
from collections.abc import MutableMapping
from pathlib import Path

from src.common.corpus import DataLayout, parse_pages

log = logging.getLogger(__name__)

STREAM_ENGINES = {"han": "paddlevl", "vie": "qwen"}      # bản sao của build_layers.LAYER_ENGINES (tránh vòng import)
ORIENTATIONS = ("auto", "horizontal", "vertical")
# Biến môi trường mà config được phép đặt thay cho người dùng (env luôn thắng).
ENGINE_ENV = {"paddlevl": {"paddlevl_orientation": "PADDLEVL_ORIENTATION"}}

CHECK_DEFAULTS: dict[str, float] = {
    "qwen_low_conf": 60.0,            # confidence trung bình (0..100) của run Qwen dưới mức này -> cảnh báo
    "han_line_factor": 2.5,           # số dòng Hán của trang > factor * trung vị của work -> cảnh báo
    "han_max_line_chars": 40,         # dòng Hán dài hơn mức này (chữ) -> nghi đọc gộp nhiều dòng/cột
    "han_single_char_share": 0.5,     # > tỉ lệ này dòng một chữ trên trang -> nghi đọc theo cột
    "han_gap_unit_share": 0.5,        # > tỉ lệ này đơn vị paddlevl bị cờ han_gap_removed -> cảnh báo
    "vie_min_lines_ink": 0.05,        # trang có mực >= mức này mà vie < vie_min_lines dòng và han rỗng -> lỗi
    "vie_min_lines": 3,
    "blank_ink": 0.004,               # tỉ lệ điểm ảnh mực dưới mức này -> trang trắng
}


def load_config(path: str | Path | None) -> dict:
    if not path:
        return {}
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"Config phải là JSON object: {path}")
    return data


def stream_settings(cfg: dict) -> dict:
    """Khối ocr.streams đã gộp mặc định và kiểm tra hợp lệ."""
    raw = ((cfg.get("ocr") or {}).get("streams") or {})
    if not isinstance(raw, dict):
        raise ValueError("ocr.streams phải là object")
    unknown = set(raw) - {"pages", "paddlevl_orientation", "engines", "check", "source_sha256", "source_pages"}
    if unknown:
        raise ValueError(f"ocr.streams có khoá lạ: {sorted(unknown)}")
    orientation = str(raw.get("paddlevl_orientation", "auto")).strip().lower()
    if orientation not in ORIENTATIONS:
        raise ValueError(f"ocr.streams.paddlevl_orientation phải thuộc {ORIENTATIONS}")
    engines = {**STREAM_ENGINES, **(raw.get("engines") or {})}
    if engines != STREAM_ENGINES:
        raise ValueError(f"ocr.streams.engines chỉ hỗ trợ {STREAM_ENGINES} (nhận {engines})")
    pages = raw.get("pages")
    if pages is not None:
        parse_pages(str(pages))                       # báo lỗi sớm nếu sai cú pháp
    check = {**CHECK_DEFAULTS, **(raw.get("check") or {})}
    bad = set(check) - set(CHECK_DEFAULTS)
    if bad:
        raise ValueError(f"ocr.streams.check có khoá lạ: {sorted(bad)}")
    sha = raw.get("source_sha256")
    if sha is not None and not (isinstance(sha, str) and len(sha) == 64 and all(c in "0123456789abcdef" for c in sha)):
        raise ValueError("ocr.streams.source_sha256 phải là 64 ký tự hex thường")
    n_pages = raw.get("source_pages")
    if n_pages is not None and (not isinstance(n_pages, int) or n_pages < 1):
        raise ValueError("ocr.streams.source_pages phải là số nguyên dương")
    if n_pages and pages is not None and max(parse_pages(str(pages))) > n_pages:
        raise ValueError(f"ocr.streams.pages {pages} vượt quá số trang PDF ({n_pages})")
    return {"pages": str(pages) if pages is not None else None, "paddlevl_orientation": orientation,
            "engines": engines, "check": check, "source_sha256": sha, "source_pages": n_pages}


def file_sha256(path: str | Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def verify_source(cfg: dict, settings: dict, root: Path = Path(".")) -> tuple[bool, str]:
    """PDF nguồn (common.source) có đúng SHA-256 trong config không. (ok, thông báo)"""
    source = (cfg.get("common") or {}).get("source")
    if not source:
        return False, "config không có common.source"
    path = Path(source) if Path(source).is_absolute() else root / source
    if not path.is_file():
        return False, f"không thấy PDF nguồn {path} (đặt file vào đúng chỗ, xem docs/ocr_assignment.md)"
    expected = settings.get("source_sha256")
    if not expected:
        return True, f"{path}: config chưa ghi source_sha256, bỏ qua so khớp"
    actual = file_sha256(path)
    if actual != expected:
        return False, (f"{path}: SHA-256 {actual} KHÁC config ({expected}). Sai bản PDF -> số trang sẽ lệch; "
                       "lấy đúng file từ nhóm")
    return True, f"{path}: SHA-256 khớp ({actual[:12]}...)"



def image_pages(layout: DataLayout) -> list[int]:
    """Số trang có ảnh trong 02_extraction/pages (page_NNN.png)."""
    import re
    out = []
    if layout.pages_dir.is_dir():
        for path in layout.pages_dir.iterdir():
            m = re.search(r"(\d+)$", path.stem)
            if path.is_file() and path.suffix.lower() in (".png", ".jpg", ".jpeg", ".tif", ".tiff") and m:
                out.append(int(m[1]))
    return sorted(out)


def compact(pages: list[int]) -> str:
    out, start, prev = [], None, None
    for p in sorted(pages):
        if start is None:
            start = prev = p
        elif p == prev + 1:
            prev = p
        else:
            out.append(f"{start}-{prev}" if start != prev else str(start))
            start = prev = p
    if start is not None:
        out.append(f"{start}-{prev}" if start != prev else str(start))
    return ",".join(out)


def resolve_pages(settings: dict, layout: DataLayout) -> str:
    """Khoảng trang OCR: ocr.streams.pages, nếu thiếu thì mọi ảnh đã trích."""
    if settings.get("pages"):
        return settings["pages"]
    pages = image_pages(layout)
    if not pages:
        raise ValueError(f"Chưa có ảnh trang trong {layout.pages_dir} và config không có ocr.streams.pages")
    return compact(pages)


def apply_engine_env(settings: dict, engine: str,
                     environ: MutableMapping[str, str] | None = None) -> dict[str, str]:
    """Đặt biến môi trường của engine từ config nếu người dùng CHƯA đặt. Trả về {biến: nguồn=giá trị}."""
    environ = os.environ if environ is None else environ
    used = {}
    for key, var in ENGINE_ENV.get(engine, {}).items():
        if environ.get(var, "").strip():
            used[var] = f"env={environ[var]}"
        elif settings.get(key) and settings[key] != "auto":
            environ[var] = settings[key]
            used[var] = f"config={settings[key]}"
        else:
            used[var] = "default=auto"
    return used


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="In cấu hình OCR hai luồng của một work")
    parser.add_argument("--config", required=True)
    parser.add_argument("--data-root", default="data")
    parser.add_argument("--get", choices=["pages", "paddlevl_orientation", "work_id", "source", "dpi",
                                          "source_sha256", "source_pages"])
    parser.add_argument("--show", action="store_true")
    parser.add_argument("--verify-source", action="store_true",
                        help="So SHA-256 của common.source với ocr.streams.source_sha256; mã thoát 1 nếu sai/thiếu")
    args = parser.parse_args(argv)
    cfg = load_config(args.config)
    common = cfg.get("common") or {}
    work_id = common.get("work_id") or Path(args.config).stem
    settings = stream_settings(cfg)
    if args.verify_source:
        ok, message = verify_source(cfg, settings)
        print(("OK " if ok else "LỖI ") + message)
        return 0 if ok else 1
    layout = DataLayout(Path(common.get("data_root") or args.data_root), work_id)
    values = {"work_id": work_id, "source": common.get("source", ""), "dpi": common.get("dpi", 300),
              "paddlevl_orientation": settings["paddlevl_orientation"],
              "source_sha256": settings["source_sha256"] or "", "source_pages": settings["source_pages"] or ""}
    if args.get == "pages" or args.show:
        values["pages"] = resolve_pages(settings, layout)
    if args.get:
        print(values[args.get])
    else:
        print(json.dumps({**values, **{k: v for k, v in settings.items() if k != "pages"}},
                         ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main())
