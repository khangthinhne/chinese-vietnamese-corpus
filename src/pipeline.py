"""
Điều phối cả pipeline từ MỘT file config:  PDF -> ảnh -> OCR thô -> làm sạch/tách câu -> dóng hàng -> TSV/XLSX

    python -m src.pipeline --config configs/CPO_18_demo.json                 # chạy tất cả
    python -m src.pipeline --config configs/CPO_18_demo.json --steps clean,align,export
    python -m src.pipeline --config configs/CPO_18_demo.json --layers han --steps extract,ocr
    python -m src.pipeline --config configs/CPO_18_demo.json --dry-run       # chỉ in lệnh tương đương

Mỗi bước vẫn chạy được riêng bằng module của nó (python -m src.ocr.ocr ...); file này chỉ ghép lệnh
cho đúng thư mục/tham số để cả nhóm chạy cùng một quy trình và tái lập được.

Config (JSON):
{
  "common": {"work_id": "CPO_18", "data_root": "data", "title": "...", "dpi": 300},
  "layers": {                                   # lớp có "pdf" -> extract + ocr; không có -> bỏ qua 2 bước đó
    "han":   {"pdf": "data/CPO_18/01_sources/book.pdf", "pages": "205-206",
              "region": "0,0.08,1,0.93", "engines": "tesseract"},
    "nghia": {"pdf": "...", "pages": "207-208", "engines": "tesseract"}
  },
  "clean": {"nghia": {"drop_pattern": ["^Phần II"]}},     # tham số riêng cho từng lớp ở bước clean
  "pairs": {"han-nghia": {"method": "length", "name": "CPO_18_parallel"}},
  "extra_args": {"extract": [], "ocr": [], "clean": [], "align": [], "export": []}
}
"""

from __future__ import annotations

import argparse
import importlib
import json
import logging
import shlex
import sys
from pathlib import Path

from src.common.sentences import parse_pair

log = logging.getLogger("pipeline")

STEPS = ("extract", "ocr", "clean", "align", "export")
MODULES = {"extract": "src.extraction.pdf_extraction", "ocr": "src.ocr.ocr", "clean": "src.preprocessing.clean",
           "align": "src.alignment.align", "export": "src.alignment.export"}


def _opt(argv: list[str], flag: str, value) -> None:
    if value not in (None, "", False):
        argv += [flag, str(value)]


def build_plan(cfg: dict, steps: list[str], layers: list[str] | None, pairs: list[str] | None, force: bool
               ) -> list[tuple[str, list[str]]]:
    common = cfg.get("common", {})
    work, root = common.get("work_id"), common.get("data_root", "data")
    if not work:
        raise ValueError("Config thiếu common.work_id")
    extra = cfg.get("extra_args", {})
    base = ["--work-id", work, "--data-root", str(root)]
    plan: list[tuple[str, list[str]]] = []

    layer_cfgs = cfg.get("layers", {})
    pair_cfgs = cfg.get("pairs", {})
    needed_layers = layers or list(layer_cfgs)
    for layer in needed_layers:
        lc = layer_cfgs.get(layer, {})
        has_pdf = bool(lc.get("pdf"))
        if "extract" in steps and has_pdf:
            argv = ["--layer", layer, *base, "--source", lc["pdf"]]
            _opt(argv, "--pages", lc.get("pages"))
            _opt(argv, "--dpi", lc.get("dpi", common.get("dpi")))
            _opt(argv, "--title", common.get("title"))
            argv += (["--force"] if force else []) + list(extra.get("extract", [])) + list(lc.get("extract_args", []))
            plan.append(("extract", argv))
        if "ocr" in steps and has_pdf:
            images = f"{root}/{work}/02_extraction/01_page_image/{layer}"
            argv = ["--layer", layer, *base, "--source", images]
            _opt(argv, "--pages", lc.get("pages"))
            _opt(argv, "--engines", lc.get("engines"))
            _opt(argv, "--select", lc.get("select"))
            _opt(argv, "--lang", lc.get("lang"))
            _opt(argv, "--region", lc.get("region"))
            _opt(argv, "--psm", lc.get("psm"))
            _opt(argv, "--dpi", lc.get("dpi", common.get("dpi")))
            _opt(argv, "--title", common.get("title"))
            _opt(argv, "--translator", lc.get("translator"))
            argv += (["--force"] if force else []) + list(extra.get("ocr", [])) + list(lc.get("ocr_args", []))
            plan.append(("ocr", argv))
        if "clean" in steps:
            cc = cfg.get("clean", {}).get(layer, {})
            argv = ["--layer", layer, *base]
            _opt(argv, "--split", cc.get("split"))
            for pat in cc.get("drop_pattern", []):
                argv += ["--drop-pattern", pat]
            _opt(argv, "--abbrev", cc.get("abbrev"))
            for flag, key in (("--split-colon", "split_colon"), ("--no-heading-heuristic", "no_heading_heuristic"),
                              ("--keep-markers", "keep_markers")):
                if cc.get(key):
                    argv.append(flag)
            argv += (["--force"] if force else []) + list(extra.get("clean", []))
            plan.append(("clean", argv))

    for pair in pairs or list(pair_cfgs):
        parse_pair(pair)
        pc = pair_cfgs.get(pair, {})
        if "align" in steps:
            argv = ["--pair", pair, *base]
            _opt(argv, "--method", pc.get("method"))
            for item in pc.get("opt", []):
                argv += ["--opt", item]
            _opt(argv, "--low-score", pc.get("low_score"))
            argv += (["--force"] if force else []) + list(extra.get("align", []))
            plan.append(("align", argv))
        if "export" in steps:
            argv = ["--pair", pair, *base]
            _opt(argv, "--name", pc.get("name"))
            _opt(argv, "--min-score", pc.get("min_score"))
            for flag in pc.get("exclude_flag", []):
                argv += ["--exclude-flag", flag]
            argv += (["--force"] if force else []) + list(extra.get("export", []))
            plan.append(("export", argv))
    return plan


def run_step(step: str, argv: list[str]) -> int:
    module = importlib.import_module(MODULES[step])
    try:
        code = module.main(argv)
    except SystemExit as exc:                      # argparse.error trong pdf_extraction
        code = exc.code if isinstance(exc.code, int) else 1
    return int(code or 0)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--config", required=True)
    p.add_argument("--steps", default=",".join(STEPS), help=f"Phân cách dấu phẩy, thuộc: {', '.join(STEPS)}")
    p.add_argument("--layers", help="Chỉ xử lý các lớp này (extract/ocr/clean)")
    p.add_argument("--pairs", help="Chỉ xử lý các cặp này (align/export), vd han-am,han-nghia")
    p.add_argument("--force", action="store_true", help="Ghi đè output của các bước được chọn")
    p.add_argument("--dry-run", action="store_true", help="Chỉ in lệnh tương đương, không chạy")
    args = p.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    steps = [s.strip() for s in args.steps.split(",") if s.strip()]
    bad = [s for s in steps if s not in STEPS]
    if bad:
        p.error(f"Bước không hợp lệ: {bad}")
    cfg = json.loads(Path(args.config).read_text(encoding="utf-8"))
    try:
        plan = build_plan(cfg, steps, args.layers.split(",") if args.layers else None,
                          args.pairs.split(",") if args.pairs else None, args.force)
    except ValueError as exc:
        log.error("%s", exc)
        return 2
    if not plan:
        log.error("Không có bước nào để chạy (kiểm tra --steps/--layers/--pairs và config)")
        return 2
    for step, argv_ in plan:
        print(f"$ python -m {MODULES[step]} {shlex.join(argv_)}")
        if args.dry_run:
            continue
        code = run_step(step, argv_)
        if code != 0:
            log.error("Bước %s thất bại (mã %d); dừng pipeline", step, code)
            return code
    return 0


if __name__ == "__main__":
    sys.exit(main())
