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
  "clean": {"nghia": {"drop_pattern": ["^Phần II"],                # regex dòng cần bỏ
                      "start_after": "^Dịch nghĩa",                # ranh giới đầu đoạn (tùy chọn)
                      "end_before": "^Chú thích"}},                # ranh giới cuối đoạn (tùy chọn)
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
            common_clean = cfg.get("clean", {}).get("common", {})    # áp cho mọi lớp
            cc = {**common_clean, **cfg.get("clean", {}).get(layer, {})}
            argv = ["--layer", layer, *base]
            _opt(argv, "--split", cc.get("split"))
            for pat in cc.get("drop_pattern", []):
                argv += ["--drop-pattern", pat]
            _opt(argv, "--start-after", cc.get("start_after"))
            _opt(argv, "--end-before", cc.get("end_before"))
            _opt(argv, "--abbrev", cc.get("abbrev"))
            for flag, key in (("--split-colon", "split_colon"), ("--no-heading-heuristic", "no_heading_heuristic"),
                              ("--keep-markers", "keep_markers"), ("--no-headers", "remove_headers"),
                              ("--no-footers", "remove_footers"), ("--no-duplicates", "remove_duplicates"),
                              ("--no-reconstruct", "reconstruct_page_boundaries"),
                              ("--no-footnotes", "enabled")):
                if key == "enabled":
                    if cc.get("footnotes", {}).get("enabled") is False:
                        argv.append(flag)
                elif cc.get(key) is False:
                    argv.append(flag)
            if cc.get("excluded_pages"):
                argv += ["--exclude-pages", ",".join(str(p) for p in cc["excluded_pages"])]
            _opt(argv, "--footnote-policy", cc.get("footnote_policy")
                 or cc.get("footnotes", {}).get("policy"))
            _opt(argv, "--header-footer-min-pages", cc.get("header_footer_min_pages"))
            _opt(argv, "--dup-threshold", cc.get("duplicate_threshold"))
            _opt(argv, "--dup-min-len", cc.get("duplicate_min_len"))
            for flag, key in (("--no-paren-notes", "detect_parentheses"),
                              ("--no-bib-notes", "detect_bibliographic_notes")):
                if cc.get("footnotes", {}).get(key) is False:
                    argv.append(flag)
            ocr_cfg = cc.get("ocr", {})
            for pat in ocr_cfg.get("remove_patterns", []):
                argv += ["--ocr-remove", pat]
            for rule in ocr_cfg.get("replace_patterns", []):
                pat, repl = (rule["pattern"], rule.get("replacement", "")) if isinstance(rule, dict) else rule
                argv += ["--ocr-replace", f"{pat}=>{repl}"]
            # metadata cấp tài liệu: "metadata_extraction": {"enabled", "exclude_from_main_text", "layers": {...}}
            meta_cfg = cfg.get("metadata_extraction", {})
            meta_layer = {k: v for k, v in meta_cfg.items()
                          if k not in ("enabled", "exclude_from_main_text", "layers")}
            meta_layer = {**meta_layer, **meta_cfg.get("layers", {}).get(layer, {})}
            if meta_cfg.get("enabled") is False:
                argv.append("--no-metadata")
            if meta_cfg.get("exclude_from_main_text") is False or meta_layer.get("exclude_from_main_text") is False:
                argv.append("--metadata-keep")
            for key, flag in (("section_start", "--meta-section-start"), ("section_end", "--meta-section-end"),
                              ("title_patterns", "--meta-title-pattern"), ("author_patterns", "--meta-author-pattern"),
                              ("attribution_patterns", "--meta-attribution-pattern")):
                for pat in meta_layer.get(key, []) or []:
                    argv += [flag, pat]
            _opt(argv, "--meta-author-max-tokens", meta_layer.get("author_max_tokens"))
            _opt(argv, "--meta-author-max-len", meta_layer.get("author_max_len"))
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
