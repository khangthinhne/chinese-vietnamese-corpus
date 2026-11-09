"""
Local page images -> append-only OCR JSON -> shared raw XML.
Run from the repository root: python -m src.ocr.ocr --help.
Sources are never edited. Cleaning, sentence splitting and alignment are separate
pipeline stages. See docs/ocr.md for the cache schema and reproducible examples.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import math
import re
import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from PIL import Image

from src.common.corpus import (
    LAYERS, DataLayout, MetadataStore, RawPage, base_parser, build_document,
    filter_lines, finalize, make_meta, parse_args, parse_pages, parse_region,
    register_source, sha256_file, write_json,
)
from src.ocr.engines import ENGINES, DEFAULT_ENGINE, OcrEngine, OcrLine, OcrResult, register_engine

log = logging.getLogger(__name__)
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp"}
SCHEMA_VERSION = 1


def requested_pages(spec: str) -> list[int]:
    """Use the shared parser, rejecting ranges it would silently discard."""
    if not isinstance(spec, str) or not spec.strip():
        raise ValueError("Cần --pages (CLI hoặc config); không tự OCR cả thư mục")
    for part in spec.split(","):
        match = re.fullmatch(r"\s*(\d+)\s*(?:-\s*(\d+)\s*)?", part)
        if not match:
            raise ValueError(f"Phạm vi trang không hợp lệ: {part!r}")
        start, end = int(match[1]), int(match[2] or match[1])
        if start < 1 or end < start:
            raise ValueError(f"Phạm vi trang phải tăng và bắt đầu từ 1: {part!r}")
    return parse_pages(spec)


def discover_images(source: Path, pages: list[int]) -> dict[int, Path]:
    if not source.is_dir():
        raise ValueError("--source phải là thư mục ảnh; hãy tách PDF thành ảnh trước")
    found: dict[int, Path] = {}
    for path in sorted(source.iterdir()):
        if not path.is_file() or path.suffix.lower() not in IMAGE_EXTENSIONS:
            continue
        numbers = re.findall(r"\d+", path.stem)
        if not numbers:
            log.warning("Bỏ qua ảnh không có số trang: %s", path.name)
            continue
        page = int(numbers[-1])
        if page in found:
            raise ValueError(f"Trùng số trang {page}: {found[page].name}, {path.name}")
        found[page] = path
    missing = sorted(set(pages) - found.keys())
    if missing:
        raise ValueError(f"Thiếu ảnh cho trang: {missing}")
    selected = {p: found[p] for p in pages}
    for page, path in selected.items():
        try:
            with Image.open(path) as image:
                if getattr(image, "n_frames", 1) != 1:
                    raise ValueError("Ảnh nhiều frame: cần tách mỗi trang thành một file")
                image.verify()
        except (OSError, ValueError) as exc:
            raise ValueError(f"Không đọc được ảnh trang {page}: {path}: {exc}") from exc
    return selected


def crop_image(image: Image.Image, region):
    width, height = image.size
    if region is None:
        box = (0, 0, width, height)
    else:
        x0, y0, x1, y1 = region
        box = (math.floor(x0 * width), math.floor(y0 * height),
               math.ceil(x1 * width), math.ceil(y1 * height))
    if box[2] <= box[0] or box[3] <= box[1]:
        raise ValueError("Vùng crop không có pixel")
    return image.crop(box), box


def mean_confidence(lines: list[OcrLine]) -> float | None:
    weighted = [(line.confidence, len(line.text.strip())) for line in lines
                if line.confidence is not None and line.text.strip()]
    total = sum(weight for _, weight in weighted)
    return sum(conf * weight for conf, weight in weighted) / total if total else None


def first(results: list[OcrResult]) -> OcrResult:
    return next((result for result in results if any(x.text.strip() for x in result.lines)), results[0])


def best_conf(results: list[OcrResult]) -> OcrResult:
    # Scores share a display scale, NOT a calibrated probability across engines.
    return max(results, key=lambda r: mean_confidence(r.lines)
               if mean_confidence(r.lines) is not None else -1)


SELECTORS: dict[str, Callable[[list[OcrResult]], OcrResult]] = {
    "first": first, "best_conf": best_conf,
}


def load_cache(path: Path, page: int) -> dict:
    if not path.exists():
        return {"schema_version": SCHEMA_VERSION, "page": page, "runs": []}
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"Cache phải là JSON object: {path}")
    if "schema_version" not in data:
        return {"schema_version": SCHEMA_VERSION, "page": page, "runs": [], "legacy": data}
    if data["schema_version"] != SCHEMA_VERSION or data.get("page") != page:
        raise ValueError(f"Schema/số trang cache không phù hợp: {path}")
    if not isinstance(data.get("runs"), list):
        raise ValueError(f"Cache thiếu danh sách runs: {path}")
    return data


def cache_key(parameters: dict) -> str:
    return hashlib.sha256(json.dumps(parameters, sort_keys=True, ensure_ascii=False,
                                     allow_nan=False).encode("utf-8")).hexdigest()


def validate_result(result: OcrResult, size: tuple[int, int]) -> None:
    if not isinstance(result, OcrResult):
        raise ValueError("Engine phải trả về OcrResult")
    for line in result.lines:
        x0, y0, x1, y1 = line.bbox
        if not (0 <= x0 < x1 <= size[0] and 0 <= y0 < y1 <= size[1]):
            raise ValueError(f"Engine trả bbox ngoài ảnh crop: {line.bbox}")
        if line.confidence is not None and not (0 <= line.confidence <= 100):
            raise ValueError("Confidence chuẩn hóa phải nằm trong 0..100")
    json.dumps(result.raw, ensure_ascii=False, allow_nan=False)


def process_page(page: int, image_path: Path, layout: DataLayout, args,
                 descriptors: dict, worker_state: threading.local) -> dict:
    path = layout.ocr_raw / f"page_{page:04d}.json"
    try:
        cache = load_cache(path, page)
        digest = sha256_file(image_path)
        with Image.open(image_path) as opened:
            image = opened.convert("RGB")
        cropped, box = crop_image(image, args.region)
        candidates, records = [], []
        for name in args.engines:
            parameters = {
                "image_sha256": digest, "layer": args.layer,
                "engine": descriptors[name], "lang": args.lang,
                "region": args.region, "psm": args.psm, "dpi": args.dpi,
                "preprocessing": "rgb-crop-v1",
            }
            key = cache_key(parameters)
            record = next((r for r in cache["runs"] if r.get("key") == key), None)
            hit = record is not None
            if record is None:
                if not hasattr(worker_state, "engines"):
                    worker_state.engines = {}
                if name not in worker_state.engines:
                    worker_state.engines[name] = ENGINES[name]()
                result = worker_state.engines[name].recognize(
                    cropped, lang=args.lang, psm=args.psm, dpi=args.dpi)
                validate_result(result, cropped.size)
                lines = [OcrLine(x.text, (x.bbox[0] + box[0], x.bbox[1] + box[1],
                                         x.bbox[2] + box[0], x.bbox[3] + box[1]),
                                 x.confidence) for x in result.lines]
                record = {
                    "key": key, "parameters": parameters,
                    "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                    "image": layout.rel(image_path), "image_size": list(image.size),
                    "crop_box": list(box), "lines": [asdict(x) for x in lines],
                    "raw": result.raw,
                }
                cache["runs"].append(record)
                write_json(path, cache)
            lines = [OcrLine(x["text"], tuple(x["bbox"]), x["confidence"]) for x in record["lines"]]
            candidates.append(OcrResult(name, lines, record["raw"]))
            records.append({"engine": name, "key": key, "cache_hit": hit})
        filtered = [OcrResult(result.engine, [line for line in result.lines
                    if line.text.strip() and filter_lines([line.text], args.keep)], result.raw)
                    for result in candidates]
        selected = SELECTORS[args.select](filtered)
        confidence = mean_confidence(selected.lines)
        return {"page": page, "image": layout.rel(image_path), "image_sha256": digest,
                "raw": layout.rel(path), "selected_engine": selected.engine,
                "confidence": confidence, "empty": not selected.lines,
                "lines": [line.text for line in selected.lines], "engines": records}
    except Exception as exc:
        raise RuntimeError(f"OCR trang {page} ({image_path.name}) thất bại: {exc}") from exc


def create_parser() -> argparse.ArgumentParser:
    parser = base_parser("OCR thư mục ảnh -> JSON thô -> XML; không làm sạch/tách câu")
    parser.add_argument("--source", help="Thư mục ảnh, mỗi file là một trang")
    parser.add_argument("--pages", help="Bắt buộc, ví dụ 205-210,212")
    parser.add_argument("--engines", default=DEFAULT_ENGINE, help="Tên registry, phân cách bằng dấu phẩy")
    parser.add_argument("--select", choices=sorted(SELECTORS), default="first")
    parser.add_argument("--lang", help="Mặc định từ LAYERS; adapter chuyển mã engine")
    parser.add_argument("--psm", type=int, default=6, help="Tesseract PSM; engine khác bỏ qua")
    parser.add_argument("--region", help="x0,y0,x1,y1, theo tỉ lệ 0..1")
    parser.add_argument("--keep", choices=["cjk", "latin", "none"], help="Lọc chữ viết; mặc định theo lớp")
    parser.add_argument("--dpi", type=int, default=300, help="DPI khai báo, không resample ảnh")
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--low-conf", type=float, default=60)
    return parser


def run(args) -> Path:
    pages = requested_pages(args.pages)
    args.region = parse_region(args.region)
    if not args.source:
        raise ValueError("Thiếu --source (CLI hoặc config)")
    source = Path(args.source).resolve()
    images = discover_images(source, pages)
    args.engines = list(dict.fromkeys(x.strip() for x in args.engines.split(",") if x.strip()))
    if not args.engines or any(name not in ENGINES for name in args.engines):
        raise ValueError(f"Engine không hợp lệ. Có sẵn: {', '.join(ENGINES)}")
    if args.select not in SELECTORS:
        raise ValueError("Selector không hợp lệ")
    if args.workers < 1 or args.dpi < 1 or not 0 <= args.psm <= 13:
        raise ValueError("Cần workers >= 1, dpi >= 1, psm trong 0..13")
    if not math.isfinite(args.low_conf) or not 0 <= args.low_conf <= 100:
        raise ValueError("--low-conf phải trong 0..100")
    if args.keep is not None and args.keep not in ("cjk", "latin", "none"):
        raise ValueError("--keep phải là cjk, latin hoặc none")
    args.lang = args.lang or LAYERS[args.layer].ocr_lang
    args.keep = LAYERS[args.layer].keep if args.keep is None else (None if args.keep == "none" else args.keep)
    layout = DataLayout(Path(args.data_root), args.work_id)
    if layout.layer_xml(args.layer).exists() and not args.force:
        raise FileExistsError(f"{layout.layer_xml(args.layer)} đã tồn tại; dùng --force để xuất lại XML")
    MetadataStore(layout).load()
    descriptors = {name: ENGINES[name].describe(args.lang) for name in args.engines}
    source_info = register_source(layout, source)
    registered = layout.work_dir / source_info["path"]
    registered_images = discover_images(registered, pages)
    for page in pages:
        if sha256_file(images[page]) != sha256_file(registered_images[page]):
            raise ValueError(f"Nguồn lưu trữ khác ảnh trang {page}; dùng tên thư mục nguồn mới")
    workers = min(args.workers, len(pages), *(ENGINES[n].max_workers for n in args.engines))
    worker_state = threading.local()
    with ThreadPoolExecutor(max_workers=workers) as pool:
        results = list(pool.map(lambda p: process_page(p, registered_images[p], layout,
                            args, descriptors, worker_state), pages))
    low_conf = [r["page"] for r in results if r["confidence"] is None or r["confidence"] < args.low_conf]
    empty = [r["page"] for r in results if r["empty"]]
    if empty:
        log.warning("Trang không có dòng sau lọc: %s", empty)
    doc = build_document(args.work_id, args.layer, make_meta(
        args.layer, title=args.title, source=args.source_label or source.name,
        method="OCR: " + ", ".join(args.engines), version=args.version, translator=args.translator),
        [RawPage(r["page"], r["lines"]) for r in results])
    return finalize(doc, layout, source_info, {
        "method": "ocr", "engines": descriptors,
        "parameters": {"pages": pages, "region": args.region, "lang": args.lang,
                       "psm": args.psm, "dpi": args.dpi, "keep": args.keep,
                       "select": args.select, "low_conf": args.low_conf,
                       "workers_requested": args.workers, "workers_used": workers},
        "low_conf_pages": low_conf, "empty_pages": empty,
        "pages": [{k: v for k, v in r.items() if k != "lines"} for r in results],
        "review_required": True,
    }, force=args.force)


def main(argv: list[str] | None = None) -> int:
    parser = create_parser()
    try:
        args = parse_args(parser, argv)
        run(args)
    except (OSError, ValueError, RuntimeError, KeyError, TypeError, subprocess.SubprocessError) as exc:
        log.error("%s", exc)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
