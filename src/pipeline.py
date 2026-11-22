"""
Điều phối cả pipeline từ MỘT file config:
- CHẾ ĐỘ MỚI (có khối 'ocr' và/hoặc 'segmentation'): PDF -> ảnh trang dùng chung ->
  OCR MỘT lần mỗi hệ chữ (chi_tra/vie) -> phân đoạn tài liệu/lớp -> clean -> align -> export.
- CHẾ ĐỘ CŨ (có 'layers'): PDF -> ảnh/OCR/XML theo từng lớp han/am/nghia -> clean -> align -> export.

    python -m src.pipeline --config configs/CPO_18_demo.json                 # chạy tất cả
    python -m src.pipeline --config configs/CPO_18_demo.json --steps clean,align,export
    python -m src.pipeline --config configs/CPO_18_demo.json --layers han --steps extract,ocr
    python -m src.pipeline --config configs/CPO_18_demo.json --dry-run       # chỉ in lệnh tương đương

Mỗi bước vẫn chạy được riêng bằng module của nó (python -m src.ocr.ocr ...); file này chỉ ghép lệnh
cho đúng thư mục/tham số để cả nhóm chạy cùng một quy trình và tái lập được.

Config chế độ mới (rút gọn):
{
  "common": {"work_id": "CPO_18", "data_root": "data", "source": ".../CPO18.pdf", "pages": "205-210"},
  "ocr": {"chi_tra": {"engines": "tesseract", "region": "0,0.08,1,0.93"},
          "vie": {"engines": "tesseract", "region": "0,0.08,1,0.93"}},
  "segmentation": {"document": {"start_patterns": []},
                   "layers": {"han": {"source": "chi_tra"},
                              "am": {"source": "vie", "start_patterns": ["^PHIÊN ÂM"]},
                              "nghia": {"source": "vie", "start_patterns": ["^DỊCH NGHĨA"]}}},
  "metadata_extraction": {"enabled": true},
  "pairs": {"han-am": {"method": "levenshtein"}, "han-nghia": {"method": "length"}}
}

Config chế độ cũ: xem configs/CPO_18_demo_legacy.json (layers.han/am/nghia có 'pdf', 'pages', ...).
"""

from __future__ import annotations

import argparse
import importlib
import json
import logging
import shlex
import sys
from pathlib import Path

from src.alignment.aligners import ALIGNERS
from src.common.corpus import LAYERS, parse_pages
from src.common.sentences import parse_pair
from src.ocr import raw_store
from src.segmentation.patterns import compile_matcher

log = logging.getLogger("pipeline")

# python -m src.pipeline --config configs/CPO_18.json --force

STEPS = ("extract", "ocr", "segment", "clean", "sentence", "validate", "align", "export")
MODULES = {"extract": "src.extraction.pdf_extraction", "ocr": "src.ocr.ocr",
           "segment": "src.segmentation.pipeline", "clean": "src.cleaning.pipeline",
           "sentence": "src.segmentation.sentences", "validate": "src.validation.validate",
           "align": "src.alignment.align", "export": "src.alignment.export"}


def uses_writing_system_config(cfg: dict) -> bool:
    """Config kiểu mới có khối 'ocr' (OCR theo hệ chữ) và/hoặc 'segmentation'."""
    return "ocr" in cfg or "segmentation" in cfg


def validate_config(cfg: dict) -> None:
    """Kiểm tra cấu trúc/lớp/nguồn/regex/phương pháp trước khi chạy bất kỳ bước nào."""
    common = cfg.get("common", {})
    if not common.get("work_id"):
        raise ValueError("Config thiếu common.work_id")
    if common.get("pages"):
        parse_pages(str(common["pages"]))

    for writing_system in cfg.get("ocr", {}):
        if writing_system not in raw_store.WRITING_SYSTEMS:
            raise ValueError(f"ocr.{writing_system}: hệ chữ phải thuộc {raw_store.WRITING_SYSTEMS}")

    seg = cfg.get("segmentation", {})
    for name, layer in seg.get("layers", {}).items():
        if name not in LAYERS:
            raise ValueError(f"segmentation.layers.{name}: lớp không hợp lệ; có {sorted(LAYERS)}")
        source = layer.get("source") or ("chi_tra" if name == "han" else "vie")
        if source not in raw_store.WRITING_SYSTEMS:
            raise ValueError(f"segmentation.layers.{name}.source phải thuộc {raw_store.WRITING_SYSTEMS}")
        if name == "tho" and source != "vie":
            raise ValueError("segmentation.layers.tho phải lấy từ hệ chữ 'vie'")
        for kind in ("start_patterns", "end_patterns"):
            compile_matcher(layer.get(kind), f"segmentation.layers.{name}.{kind}",
                            bool(layer.get("case_sensitive")),
                            heading=bool(layer.get("heading_mode")))
    for kind in ("start_patterns", "end_patterns"):
        compile_matcher(seg.get("document", {}).get(kind), f"segmentation.document.{kind}")

    for pair, pc in cfg.get("pairs", {}).items():
        if pair == "han-viet":
            continue
        parse_pair(pair)
        method = (pc or {}).get("method")
        if method and method not in ALIGNERS:
            raise ValueError(f"pairs.{pair}.method không hợp lệ: {method}")

    name = (cfg.get("consolidated") or {}).get("name")
    if name is not None and not str(name).strip():
        raise ValueError("consolidated.name không được rỗng")


def _opt(argv: list[str], flag: str, value) -> None:
    if value not in (None, "", False):
        argv += [flag, str(value)]


def _clean_argv(cfg: dict, layer: str, base: list[str], force: bool, extra: list[str],
                *, boundaries: bool = True, metadata: bool = True) -> list[str]:
    common_clean = cfg.get("clean", {}).get("common", {})    # áp cho mọi lớp
    cc = {**common_clean, **cfg.get("clean", {}).get(layer, {})}
    argv = ["--layer", layer, *base]
    _opt(argv, "--split", cc.get("split"))
    for pat in cc.get("drop_pattern", []):
        argv += ["--drop-pattern", pat]
    if boundaries:
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
    if metadata:
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
    else:
        argv.append("--no-metadata")
    argv += (["--force"] if force else []) + list(extra)
    return argv


def _pair_steps(cfg: dict, steps: list[str], pairs: list[str] | None, base: list[str],
                force: bool, extra: dict) -> list[tuple[str, list[str]]]:
    pair_cfgs = cfg.get("pairs", {})
    plan: list[tuple[str, list[str]]] = []
    for pair in pairs or list(pair_cfgs):
        if pair == "han-viet":                    # chỉ dùng cho export tổng hợp
            continue
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


def _consolidated_steps(cfg: dict, steps: list[str], pairs: list[str] | None,
                        base: list[str], force: bool,
                        extra: dict) -> list[tuple[str, list[str]]]:
    if "export" not in steps or (pairs is not None and "han-viet" not in pairs):
        return []
    if not (cfg.get("consolidated") or "han-viet" in cfg.get("pairs", {})
            or (pairs is not None and "han-viet" in pairs)):
        return []
    pc = cfg.get("pairs", {}).get("han-viet", {})
    cc = cfg.get("consolidated", {})
    argv = ["--pair", "han-viet", *base]
    _opt(argv, "--name", cc.get("name") or pc.get("name"))
    _opt(argv, "--min-score", cc.get("min_score", pc.get("min_score")))
    for flag in cc.get("exclude_flag", pc.get("exclude_flag", [])):
        argv += ["--exclude-flag", flag]
    argv += (["--force"] if force else []) + list(extra.get("export", []))
    return [("export", argv)]


def _new_plan(cfg: dict, steps: list[str], layers: list[str] | None, pairs: list[str] | None,
              force: bool, config_path: str | None) -> list[tuple[str, list[str]]]:
    """Config kiểu mới: extract 1 lần, OCR theo hệ chữ (chi_tra/vie), segment, clean theo lớp."""
    common = cfg.get("common", {})
    work, root = common.get("work_id"), common.get("data_root", "data")
    if not work:
        raise ValueError("Config thiếu common.work_id")
    extra = cfg.get("extra_args", {})
    base = ["--work-id", work, "--data-root", str(root)]
    source, pages = common.get("source"), common.get("pages")
    if ("extract" in steps or "ocr" in steps) and not source:
        raise ValueError("Config kiểu mới cần common.source (file PDF dùng chung)")
    if "ocr" in steps and not pages:
        raise ValueError("Config kiểu mới cần common.pages (phạm vi trang dùng chung)")
    plan: list[tuple[str, list[str]]] = []

    if "extract" in steps:
        argv = ["--source", source, *base]
        _opt(argv, "--pages", pages)
        _opt(argv, "--dpi", common.get("dpi"))
        _opt(argv, "--title", common.get("title"))
        argv += (["--force"] if force else []) + list(extra.get("extract", []))
        plan.append(("extract", argv))

    if "ocr" in steps:
        ocr_cfgs = cfg.get("ocr", {})
        if not ocr_cfgs:
            raise ValueError("Config kiểu mới cần khối 'ocr' với các hệ chữ (chi_tra, vie)")
        images = f"{root}/{work}/02_extraction/pages"
        for ws, oc in ocr_cfgs.items():
            argv = [*base, "--writing-system", ws, "--source", images]
            _opt(argv, "--pages", pages)
            _opt(argv, "--engines", oc.get("engines") or oc.get("engine"))
            _opt(argv, "--select", oc.get("select"))
            _opt(argv, "--lang", oc.get("lang"))
            _opt(argv, "--region", oc.get("region"))
            _opt(argv, "--psm", oc.get("psm"))
            _opt(argv, "--dpi", oc.get("dpi", common.get("dpi")))
            _opt(argv, "--workers", oc.get("workers", common.get("workers")))
            _opt(argv, "--low-conf", oc.get("low_conf"))
            _opt(argv, "--keep", oc.get("keep"))
            argv += (["--force"] if force else []) + list(extra.get("ocr", []))
            plan.append(("ocr", argv))

    if "segment" in steps:
        if not config_path:
            raise ValueError("Bước segment cần --config")
        argv = ["--config", str(config_path), *base]
        argv += (["--force"] if force else []) + list(extra.get("segment", []))
        plan.append(("segment", argv))

    if "clean" in steps:
        seg_layers = list(cfg.get("segmentation", {}).get("layers", {}))
        needed = layers or seg_layers or ["han", "am", "nghia"]
        for layer in needed:
            plan.append(("clean", _clean_argv(cfg, layer, base, force,
                                              ["--structural-only", *extra.get("clean", [])],
                                              boundaries=False, metadata=False)))

    if "sentence" in steps:
        seg_layers = list(cfg.get("segmentation", {}).get("layers", {}))
        needed = layers or seg_layers or ["han", "am", "nghia"]
        for layer in needed:
            argv = ["--layer", layer, *base]
            if config_path:
                argv += ["--config", str(config_path)]
            argv += (["--force"] if force else []) + list(extra.get("sentence", []))
            plan.append(("sentence", argv))

    if "validate" in steps:
        argv = [*base]
        if config_path:
            argv += ["--config", str(config_path)]
        if layers:
            argv += ["--layers", ",".join(layers)]
        if pairs:
            argv += ["--pairs", ",".join(pairs)]
        argv += (["--force"] if force else []) + list(extra.get("validate", []))
        plan.append(("validate", argv))

    plan += _pair_steps(cfg, steps, pairs, base, force, extra)
    plan += _consolidated_steps(cfg, steps, pairs, base, force, extra)
    return plan


def build_plan(cfg: dict, steps: list[str], layers: list[str] | None, pairs: list[str] | None, force: bool,
               config_path: str | None = None) -> list[tuple[str, list[str]]]:
    if uses_writing_system_config(cfg):
        return _new_plan(cfg, steps, layers, pairs, force, config_path)
    common = cfg.get("common", {})
    work, root = common.get("work_id"), common.get("data_root", "data")
    if not work:
        raise ValueError("Config thiếu common.work_id")
    extra = cfg.get("extra_args", {})
    base = ["--work-id", work, "--data-root", str(root)]
    plan: list[tuple[str, list[str]]] = []

    layer_cfgs = cfg.get("layers", {})
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
            plan.append(("clean", _clean_argv(cfg, layer, base, force, extra.get("clean", []))))

    plan += _pair_steps(cfg, steps, pairs, base, force, extra)
    plan += _consolidated_steps(cfg, steps, pairs, base, force, extra)
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
        validate_config(cfg)
        plan = build_plan(cfg, steps, args.layers.split(",") if args.layers else None,
                          args.pairs.split(",") if args.pairs else None, args.force,
                          config_path=args.config)
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
