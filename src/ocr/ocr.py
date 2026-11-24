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

from src.common import deps
from src.common.corpus import (
    LAYERS, DataLayout, MetadataStore, RawPage, base_parser, build_document,
    filter_lines, finalize, make_meta, parse_args, parse_pages, parse_region,
    register_source, sha256_file, write_json,
)
from src.ocr import raw_store, raw_xml
from src.ocr.build_layers import ENGINE_ENVS, LAYER_ENGINES
from src.ocr.engines import (ENGINES, DEFAULT_ENGINE, OcrEngine, OcrLine, OcrResult, register_engine,
                             require_engine)

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
    print(f"Phát hiện {len(found)} ảnh trong {source}, thiếu trang: {missing}")
    if missing:
        raise ValueError(f"Thiếu ảnh cho trang: {missing}")
    selected = {p: found[p] for p in pages}
    print(f"Chọn {len(selected)} trang: {list(selected.keys())}")
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
        if line.bbox is not None:
            x0, y0, x1, y1 = line.bbox
            if not (0 <= x0 < x1 <= size[0] and 0 <= y0 < y1 <= size[1]):
                raise ValueError(f"Engine trả bbox ngoài ảnh crop: {line.bbox}")
        if line.confidence is not None and not (0 <= line.confidence <= 100):
            raise ValueError("Confidence chuẩn hóa phải nằm trong 0..100")
    json.dumps(result.raw, ensure_ascii=False, allow_nan=False)


# def process_page(page: int, image_path: Path, layout: DataLayout, args,
#                  descriptors: dict, worker_state: threading.local) -> dict:
#     path = layout.ocr_raw / f"page_{page:04d}.json"
#     try:
#         cache = load_cache(path, page)
#         digest = sha256_file(image_path)
#         with Image.open(image_path) as opened:
#             image = opened.convert("RGB")
#         cropped, box = crop_image(image, args.region)
#         candidates, records = [], []
#         for name in args.engines:
#             parameters = {
#                 "image_sha256": digest, "layer": args.layer,
#                 "engine": descriptors[name], "lang": args.lang,
#                 "region": args.region, "psm": args.psm, "dpi": args.dpi,
#                 "preprocessing": "rgb-crop-v1",
#             }
#             key = cache_key(parameters)
#             record = next((r for r in cache["runs"] if r.get("key") == key), None)
#             hit = record is not None
#             if record is None:
#                 if not hasattr(worker_state, "engines"):
#                     worker_state.engines = {}
#                 if name not in worker_state.engines:
#                     worker_state.engines[name] = ENGINES[name]()
#                 result = worker_state.engines[name].recognize(
#                     cropped, lang=args.lang, psm=args.psm, dpi=args.dpi)
#                 validate_result(result, cropped.size)
#                 lines = [OcrLine(x.text, (x.bbox[0] + box[0], x.bbox[1] + box[1],
#                                          x.bbox[2] + box[0], x.bbox[3] + box[1]),
#                                  x.confidence) for x in result.lines]
#                 record = {
#                     "key": key, "parameters": parameters,
#                     "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
#                     "image": layout.rel(image_path), "image_size": list(image.size),
#                     "crop_box": list(box), "lines": [asdict(x) for x in lines],
#                     "raw": result.raw,
#                 }
#                 cache["runs"].append(record)
#                 write_json(path, cache)
#             lines = [OcrLine(x["text"], tuple(x["bbox"]), x["confidence"]) for x in record["lines"]]
#             candidates.append(OcrResult(name, lines, record["raw"]))
#             records.append({"engine": name, "key": key, "cache_hit": hit})
        
#         filtered = [OcrResult(result.engine, [line for line in result.lines
#                     if line.text.strip() and filter_lines([line.text], args.keep)], result.raw)
#                     for result in candidates]
#         selected = SELECTORS[args.select](filtered)
#         confidence = mean_confidence(selected.lines)
#         return {"page": page, "image": layout.rel(image_path), "image_sha256": digest,
#                 "raw": layout.rel(path), "selected_engine": selected.engine,
#                 "confidence": confidence, "empty": not selected.lines,
#                 "lines": [line.text for line in selected.lines], "engines": records}
#     except Exception as exc:
#         raise RuntimeError(f"OCR trang {page} ({image_path.name}) thất bại: {exc}") from exc


def create_parser() -> argparse.ArgumentParser:
    parser = base_parser("OCR ảnh -> kho thô unified, theo HỆ CHỮ, hoặc theo LUỒNG han/vie (--layer)",
                         layer_required=False, layer_choices=[*LAYERS, *LAYER_ENGINES])
    parser.add_argument("--source", help="Thư mục ảnh, mỗi file là một trang")
    parser.add_argument("--pages", help="Bắt buộc, ví dụ 205-210,212")
    parser.add_argument("--writing-system", choices=list(raw_store.WRITING_SYSTEMS),
                        help="chế độ mới: chi_tra | vie (mỗi hệ chữ OCR MỘT lần)")
    parser.add_argument("--unified", action="store_true",
                        help="chế độ unified (single-stream): OCR một lần cho tất cả hệ chữ")
    parser.add_argument("--engines", default=DEFAULT_ENGINE, help="Tên registry, phân cách bằng dấu phẩy")
    parser.add_argument("--select", choices=sorted(SELECTORS), default="first")
    parser.add_argument("--lang", help="Mặc định: hệ chữ (mới) hoặc theo lớp (cũ)")
    parser.add_argument("--psm", type=int, default=6, help="Tesseract PSM; engine khác bỏ qua")
    parser.add_argument("--region", help="x0,y0,x1,y1, theo tỉ lệ 0..1")
    parser.add_argument("--keep", choices=["cjk", "latin", "none"], help="Lọc chữ viết")
    parser.add_argument("--dpi", type=int, default=300, help="DPI khai báo, không resample ảnh")
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--low-conf", type=float, default=60)
    parser.add_argument("--force-reocr", action="store_true", help="ignore cache runs")
    return parser


def process_writing_system_page(page: int, image_path: Path, layout: DataLayout, args,
                                writing_system: str, lang: str, keep: str | None,
                                descriptors: dict, worker_state: threading.local) -> dict:
    try:
        store = raw_store.load_page(layout, writing_system, page, args.work_id)
        digest = sha256_file(image_path)
        with Image.open(image_path) as opened:
            image = opened.convert("RGB")
        cropped, box = crop_image(image, args.region)
        candidates, records = [], []
        for name in args.engines:
            parameters = {
                "image_sha256": digest, "writing_system": writing_system,
                "engine": descriptors[name], "lang": lang, "region": args.region,
                "psm": args.psm, "dpi": args.dpi, "preprocessing": "rgb-crop-v1",
            }
            key = raw_store.cache_key(parameters)
            record = None if args.force_reocr else raw_store.find_run(store, key)
            hit = record is not None

            if record is None:
                if not hasattr(worker_state, "engines"):
                    worker_state.engines = {}
                if name not in worker_state.engines:
                    worker_state.engines[name] = ENGINES[name]()
                
                result = worker_state.engines[name].recognize(
                    cropped, lang=lang, psm=args.psm, dpi=args.dpi)
                validate_result(result, cropped.size)
                
                lines = [OcrLine(x.text, (x.bbox[0] + box[0], x.bbox[1] + box[1],
                                         x.bbox[2] + box[0], x.bbox[3] + box[1]) if x.bbox is not None else None,
                                 x.confidence) for x in result.lines]
                record = {
                    "key": key, "parameters": parameters, "created_at": raw_store.now(),
                    "image": str(image_path), "image_size": list(image.size),
                    "crop_box": list(box), "lines": [asdict(x) for x in lines],
                    "raw": result.raw,
                }
                store["runs"].append(record)
                raw_store.save_page(layout, writing_system, page, store)
            lines = [OcrLine(x["text"], tuple(x["bbox"]) if x.get("bbox") is not None else None, x.get("confidence")) for x in record["lines"]]
            candidates.append(OcrResult(name, lines, record["raw"]))
            records.append({"engine": name, "key": key, "cache_hit": hit})
        # for result in candidates:
        #     print(f"\n[DEBUG] Engine: {result.engine}")
        #     print(f"[DEBUG] Number of lines: {len(result.lines)}")

        #     for i, line in enumerate(result.lines):
        #         print(f"[DEBUG] Line {i}: {line.text[:500]!r}")
        #         print(f"[DEBUG] CJK filtered: {filter_lines([line.text], keep)}")

        # print(f"[DEBUG] keep = {keep!r}")
     
     
        # filtered = [OcrResult(result.engine, [line for line in result.lines
        #             if line.text.strip() and filter_lines([line.text], keep)], result.raw)
        #             for result in candidates]
     
     
        # filtered = []

        # for result in candidates:
        #     if result.engine == "qwen":
        #         # Qwen returns page-level text, not individual text lines.
        #         # Preserve the full transcription for downstream processing.
        #         lines = [
        #             line for line in result.lines
        #             if line.text.strip()
        #         ]
        #     else:
        #         lines = [
        #             line for line in result.lines
        #             if line.text.strip()
        #             and filter_lines([line.text], keep)
        #         ]

        #     filtered.append(
        #         OcrResult(
        #             result.engine,
        #             lines,
        #             result.raw
        #         )
        #     )


        if keep:
            candidates = [
                OcrResult(
                    result.engine,
                    [
                        line for line in result.lines
                        if line.text.strip() and filter_lines([line.text], keep)
                    ],
                    result.raw
                )
                for result in candidates
            ]
        else:
            candidates = [
                OcrResult(
                    result.engine,
                    [
                        line for line in result.lines
                        if line.text.strip()
                    ],
                    result.raw
                )
                for result in candidates
            ]

        # selected = SELECTORS[args.select](filtered)
        selected = SELECTORS[args.select](candidates)
        confidence = mean_confidence(selected.lines)
        selected_key = next(r["key"] for r in records if r["engine"] == selected.engine)
        return {"page": page, "image": str(image_path), "image_sha256": digest,
                "raw": layout.rel(raw_store.raw_path(layout, writing_system, page)),
                "selected_engine": selected.engine, "selected_key": selected_key,
                "confidence": confidence, "empty": not selected.lines,
                "n_lines": len(selected.lines),
                "lines": [{"text": x.text, "bbox": list(x.bbox) if x.bbox is not None else None, "confidence": x.confidence}
                          for x in selected.lines],
                "engines": records}
    except Exception as exc:
        raise RuntimeError(f"OCR trang {page} ({image_path.name}) thất bại: {exc}") from exc


def run_writing_system(args) -> Path:
    """OCR một HỆ CHỮ (chi_tra|vie) cho toàn bộ phạm vi trang -> 03_ocr/ (không tạo XML lớp)."""
    writing_system = args.writing_system
    pages = requested_pages(args.pages)
    args.region = parse_region(args.region)
    if not args.source:
        raise ValueError("Thiếu --source (thư mục ảnh)")
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
    lang = args.lang or writing_system
    keep = args.keep or ("cjk" if writing_system == "chi_tra" else "latin")
    keep = None if keep == "none" else keep
    print(f"[DEBUG] OCR {writing_system} trang {pages}, region={args.region}, lang={lang}, keep={keep}")
    layout = DataLayout(Path(args.data_root), args.work_id)
    target = raw_store.manifest_path(layout, writing_system)
    xml_path = layout.ocr_raw_xml_path(writing_system)
    outputs = [target, xml_path, *(raw_store.raw_path(layout, writing_system, p) for p in pages)]
    inputs = [*images.values()]
    if getattr(args, "config", None):
        inputs.append(Path(args.config))
    deps.check_fresh(
        layout, f"ocr:{writing_system}", inputs, outputs, args.force,
        f"{target} đã tồn tại; dùng --force để ghi lại manifest (cache vẫn dùng lại)")
    layout.ensure(layout.ocr_raw_root / writing_system, layout.ocr_manifest_dir,
                  layout.ocr_raw_xml)
    descriptors = {name: ENGINES[name].describe(lang) for name in args.engines}
    workers = min(args.workers, len(pages), *(ENGINES[n].max_workers for n in args.engines))
    worker_state = threading.local()
    with ThreadPoolExecutor(max_workers=workers) as pool:
        results = list(pool.map(
            lambda p: process_writing_system_page(p, images[p], layout, args, writing_system,
                                                  lang, keep, descriptors, worker_state),
            pages))
    print(f"[DEBUG] Results: {results}")
    
    # output result json


    low_conf = [r["page"] for r in results if r["confidence"] is None or r["confidence"] < args.low_conf]
    empty = [r["page"] for r in results if r["empty"]]
    if empty:
        log.warning("Trang không có dòng sau lọc: %s", empty)
    record = {
        "schema_version": raw_store.SCHEMA_VERSION, "work_id": args.work_id,
        "writing_system": writing_system, "lang": lang, "keep": keep,
        "source": {"path": str(source)},
        "engines": descriptors,
        "parameters": {"pages": pages, "region": args.region, "psm": args.psm,
                       "dpi": args.dpi, "keep": keep, "select": args.select,
                       "low_conf": args.low_conf, "workers_requested": args.workers,
                       "workers_used": workers},
        "pages": results, "low_conf_pages": low_conf, "empty_pages": empty,
        "review_required": True,
    }
    path = raw_store.write_manifest(layout, writing_system, record)
    xml_path = raw_xml.write_writing_system(layout, writing_system, pages)
    deps.record(layout, f"ocr:{writing_system}", inputs, [*outputs, xml_path])
    MetadataStore(layout).update(f"{writing_system}_ocr", {
        "output": layout.rel(path), "xml": layout.rel(xml_path),
        "source": {"path": str(source)},
        "counts": {"pages": len(pages), "lines": sum(r["n_lines"] for r in results)},
        "parameters": record["parameters"], "low_conf_pages": low_conf,
        "empty_pages": empty, "review_required": True})
    log.info("[%s] OCR %d trang -> %s", writing_system, len(pages), path)
    return path


def recognize_page(
    image: Image.Image,
    page_number: int,
    config: dict | None = None,
) -> OcrResult:
    """Page-oriented execution interface for single-stream Qwen VLM OCR."""
    cfg = config or {}
    engine_name = cfg.get("engine", "qwen")
    if engine_name not in ENGINES:
        raise ValueError(f"Engine {engine_name} không hợp lệ")
    engine = ENGINES[engine_name]()
    region = cfg.get("region")
    if isinstance(region, str):
        region = parse_region(region)
    image_rgb = image.convert("RGB")
    cropped, box = crop_image(image_rgb, region)
    result = engine.recognize(cropped, lang=cfg.get("lang", "all"),
                              psm=cfg.get("psm", 6), dpi=cfg.get("dpi", 300))
    validate_result(result, cropped.size)
    lines = [OcrLine(x.text, (x.bbox[0] + box[0], x.bbox[1] + box[1],
                             x.bbox[2] + box[0], x.bbox[3] + box[1]) if x.bbox is not None else None,
                     x.confidence) for x in result.lines]
    return OcrResult(engine_name, lines, result.raw)


def process_unified_page(page: int, image_path: Path, layout: DataLayout, args,
                         descriptors: dict, worker_state: threading.local) -> dict:
    try:
        store = raw_store.load_unified_page(layout, page, args.work_id)
        digest = sha256_file(image_path)
        with Image.open(image_path) as opened:
            image = opened.convert("RGB")
        cropped, box = crop_image(image, args.region)
        candidates, records = [], []
        for name in args.engines:
            parameters = {
                "image_sha256": digest,
                "engine": descriptors[name], "lang": "all", "region": args.region,
                "psm": args.psm, "dpi": args.dpi, "preprocessing": "rgb-crop-v1",
            }
            key = raw_store.cache_key(parameters)
            record = None if args.force_reocr else raw_store.find_run(store, key)
            hit = record is not None

            if record is None:
                if not hasattr(worker_state, "engines"):
                    worker_state.engines = {}
                if name not in worker_state.engines:
                    worker_state.engines[name] = ENGINES[name]()

                result = worker_state.engines[name].recognize(
                    cropped, lang="all", psm=args.psm, dpi=args.dpi)
                validate_result(result, cropped.size)
                lines = [OcrLine(x.text, (x.bbox[0] + box[0], x.bbox[1] + box[1],
                                         x.bbox[2] + box[0], x.bbox[3] + box[1]) if x.bbox is not None else None,
                                 x.confidence) for x in result.lines]
                record = {
                    "key": key, "parameters": parameters, "created_at": raw_store.now(),
                    "image": str(image_path), "image_size": list(image.size),
                    "crop_box": list(box), "lines": [asdict(x) for x in lines],
                    "raw": result.raw,
                }
                store["runs"].append(record)
                raw_store.save_unified_page(layout, page, store)
            lines = [OcrLine(x["text"], tuple(x["bbox"]) if x.get("bbox") is not None else None, x.get("confidence")) for x in record["lines"]]
            candidates.append(OcrResult(name, lines, record["raw"]))
            records.append({"engine": name, "key": key, "cache_hit": hit})

        # OCR preservation: preserve all non-empty lines in original reading order
        candidates = [
            OcrResult(
                result.engine,
                [line for line in result.lines if line.text.strip()],
                result.raw
            )
            for result in candidates
        ]

        selected = SELECTORS[args.select](candidates)
        confidence = mean_confidence(selected.lines)
        selected_key = next(r["key"] for r in records if r["engine"] == selected.engine)
        return {"page": page, "image": str(image_path), "image_sha256": digest,
                "raw": layout.rel(raw_store.unified_raw_path(layout, page)),
                "selected_engine": selected.engine, "selected_key": selected_key,
                "confidence": confidence, "empty": not selected.lines,
                "n_lines": len(selected.lines),
                "lines": [{"text": x.text, "bbox": list(x.bbox) if x.bbox is not None else None, "confidence": x.confidence}
                          for x in selected.lines],
                "engines": records}
    except Exception as exc:
        raise RuntimeError(f"OCR trang {page} ({image_path.name}) thất bại: {exc}") from exc


def run_unified(args) -> Path:
    """OCR single-stream (all scripts recognized once per page) -> 03_ocr/."""
    pages = requested_pages(args.pages)
    args.region = parse_region(args.region)
    if not args.source:
        raise ValueError("Thiếu --source (thư mục ảnh)")
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

    layout = DataLayout(Path(args.data_root), args.work_id)
    target = raw_store.unified_manifest_path(layout)
    xml_path = layout.ocr_unified_xml_path
    outputs = [target, xml_path, *(raw_store.unified_raw_path(layout, p) for p in pages)]
    inputs = [*images.values()]
    if getattr(args, "config", None):
        inputs.append(Path(args.config))
    deps.check_fresh(
        layout, "ocr:unified", inputs, outputs, args.force,
        f"{target} đã tồn tại; dùng --force để ghi lại manifest (cache vẫn dùng lại)")
    layout.ensure(layout.ocr_raw_unified_dir, layout.ocr_manifest_dir,
                  layout.ocr_raw_xml, layout.ocr_unified_text_dir)
    descriptors = {name: ENGINES[name].describe("all") for name in args.engines}
    workers = min(args.workers, len(pages), *(ENGINES[n].max_workers for n in args.engines))
    worker_state = threading.local()
    with ThreadPoolExecutor(max_workers=workers) as pool:
        results = list(pool.map(
            lambda p: process_unified_page(p, images[p], layout, args,
                                           descriptors, worker_state),
            pages))

    low_conf = [r["page"] for r in results if r["confidence"] is None or r["confidence"] < args.low_conf]
    empty = [r["page"] for r in results if r["empty"]]
    if empty:
        log.warning("Trang không có dòng sau OCR: %s", empty)
    record = {
        "schema_version": raw_store.UNIFIED_SCHEMA_VERSION, "work_id": args.work_id,
        "mode": "unified",
        "source": {"path": str(source)},
        "engines": descriptors,
        "parameters": {"pages": pages, "region": args.region, "psm": args.psm,
                       "dpi": args.dpi, "select": args.select,
                       "low_conf": args.low_conf, "workers_requested": args.workers,
                       "workers_used": workers},
        "pages": results, "low_conf_pages": low_conf, "empty_pages": empty,
        "review_required": True,
    }
    path = raw_store.write_unified_manifest(layout, record)
    xml_path = raw_xml.write_unified(layout, pages)
    deps.record(layout, "ocr:unified", inputs, [*outputs, xml_path])
    MetadataStore(layout).update("unified_ocr", {
        "output": layout.rel(path), "xml": layout.rel(xml_path),
        "source": {"path": str(source)},
        "counts": {"pages": len(pages), "lines": sum(r["n_lines"] for r in results)},
        "parameters": record["parameters"], "low_conf_pages": low_conf,
        "empty_pages": empty, "review_required": True})
    log.info("[unified] OCR %d trang -> %s", len(pages), path)
    return path


# --- OCR THEO LUỒNG han/vie ---------------------------------------------------
# Hán lấy từ paddlevl, Việt lấy từ qwen; mỗi luồng MỘT engine, không bỏ phiếu theo dòng.
# Run mới ghi vào raw/<engine>/NNNN.json; raw/NNNN.json cũ chỉ được đọc để tra cache.
# Bước này KHÔNG ghi raw.xml, manifest unified hay XML luồng (xem src.ocr.build_layers).


def run_parameters(digest: str, descriptor: dict, args) -> dict:
    """Tham số của một run; trùng hệt chế độ unified để tái dùng cache Qwen đã có."""
    return {"image_sha256": digest, "engine": descriptor, "lang": "all", "region": args.region,
            "psm": args.psm, "dpi": args.dpi, "preprocessing": "rgb-crop-v1"}


def resolve_image_source(args, layout: DataLayout) -> Path:
    """--source nếu là thư mục ảnh; ngược lại (vd config trỏ tới PDF) dùng 02_extraction/pages."""
    if args.source and Path(args.source).is_dir():
        return Path(args.source).resolve()
    if args.source:
        log.info("--source %s không phải thư mục ảnh; dùng %s", args.source, layout.pages_dir)
    return layout.pages_dir.resolve()


def available_images(source: Path, pages: list[int]) -> tuple[dict[int, Path], list[int]]:
    """Như discover_images nhưng trang thiếu ảnh chỉ bị bỏ qua (trả về danh sách để báo cáo)."""
    found: dict[int, Path] = {}
    if source.is_dir():
        for path in sorted(source.iterdir()):
            numbers = re.findall(r"\d+", path.stem)
            if path.is_file() and path.suffix.lower() in IMAGE_EXTENSIONS and numbers:
                page = int(numbers[-1])
                if page in found:
                    raise ValueError(f"Trùng số trang {page}: {found[page].name}, {path.name}")
                found[page] = path
    missing = [p for p in pages if p not in found]
    return {p: found[p] for p in pages if p in found}, missing


def ocr_layer_page(page: int, image_path: Path, layout: DataLayout, args, engine: str,
                   descriptor: dict, state: dict) -> dict:
    """OCR một trang bằng một engine; bỏ qua nếu đã có run cùng khoá (cùng describe())."""
    digest = sha256_file(image_path)
    parameters = run_parameters(digest, descriptor, args)
    key = raw_store.cache_key(parameters)
    if not args.force_reocr:
        cached = [r for r in raw_store.page_runs(layout, engine, page, args.work_id) if r.get("key") == key]
        if cached:
            return {"page": page, "engine": engine, "key": key, "status": "cached",
                    "raw": cached[-1]["_source"], "n_lines": len(cached[-1].get("lines", []))}
    with Image.open(image_path) as opened:
        image = opened.convert("RGB")
    cropped, box = crop_image(image, args.region)
    if "engine" not in state:
        state["engine"] = ENGINES[engine]()
    result = state["engine"].recognize(cropped, lang="all", psm=args.psm, dpi=args.dpi)
    validate_result(result, cropped.size)
    lines = [OcrLine(x.text, (x.bbox[0] + box[0], x.bbox[1] + box[1],
                             x.bbox[2] + box[0], x.bbox[3] + box[1]) if x.bbox is not None else None,
                     x.confidence) for x in result.lines]
    record = {"key": key, "parameters": parameters, "created_at": raw_store.now(),
              "image": str(image_path), "image_size": list(image.size), "crop_box": list(box),
              "lines": [asdict(x) for x in lines], "raw": result.raw}
    store = raw_store.load_engine_page(layout, engine, page, args.work_id)
    store["runs"].append(record)
    path = raw_store.save_engine_page(layout, engine, page, store)
    return {"page": page, "engine": engine, "key": key, "status": "ocr",
            "raw": layout.rel(path), "n_lines": len(lines),
            "confidence": mean_confidence(lines), "created_at": record["created_at"]}


def run_layer_ocr(args) -> Path:
    """OCR một luồng (han|vie) bằng đúng MỘT engine -> raw/<engine>/NNNN.json + manifests/ocr_<layer>.json."""
    layer = args.layer
    if getattr(args, "unified", False) or getattr(args, "writing_system", None):
        raise ValueError("--layer han|vie không dùng chung với --unified/--writing-system")
    engines = [x.strip() for x in str(args.engines).split(",") if x.strip()]
    expected = LAYER_ENGINES[layer]
    if engines != [expected]:
        raise ValueError(f"Luồng {layer} lấy từ đúng một engine: --engines {expected} (nhận {args.engines!r})")
    engine = expected
    print(f"[{layer}] engine {engine}: chạy lệnh này trong môi trường {ENGINE_ENVS.get(engine, '?')}")
    require_engine(engine)
    pages = requested_pages(args.pages)
    args.region = parse_region(args.region)
    if args.dpi < 1 or not 0 <= args.psm <= 13:
        raise ValueError("Cần dpi >= 1, psm trong 0..13")
    layout = DataLayout(Path(args.data_root), args.work_id)
    source = resolve_image_source(args, layout)
    images, missing = available_images(source, pages)
    if missing:
        log.warning("[%s] Bỏ qua %d trang chưa có ảnh trong %s: %s", layer, len(missing), source,
                    compact_pages(missing))
    try:
        descriptor = ENGINES[engine].describe("all")
    except Exception as exc:
        raise RuntimeError(f"Engine {engine} không sẵn sàng ({exc}). "
                           f"Hãy chạy trong môi trường {ENGINE_ENVS.get(engine, '?')}.") from exc
    log.info("[%s] %d trang có ảnh; engine %s adapter_version=%s", layer, len(images), engine,
             descriptor.get("adapter_version"))
    records, failed, state = [], [], {}
    for index, (page, image_path) in enumerate(images.items(), 1):
        try:
            rec = ocr_layer_page(page, image_path, layout, args, engine, descriptor, state)
            log.info("[%s] %d/%d trang %04d: %s, %d dòng", layer, index, len(images), page,
                     rec["status"], rec["n_lines"])
        except Exception as exc:                     # một trang lỗi không dừng cả batch
            log.error("[%s] trang %04d lỗi OCR: %s: %s", layer, page, type(exc).__name__, exc)
            rec = {"page": page, "engine": engine, "status": "error",
                   "error": f"{type(exc).__name__}: {exc}"}
            failed.append(page)
        records.append(rec)

    path = layout.ocr_layer_manifest_path("ocr", layer)
    previous = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    merged = {r["page"]: r for r in previous.get("pages", []) if isinstance(r, dict) and "page" in r}
    merged.update({r["page"]: r for r in records})   # cập nhật trang vừa chạy, giữ trang của lần trước
    write_json(path, {
        "schema_version": 1, "work_id": args.work_id, "layer": layer, "engine": engine,
        "engine_descriptor": descriptor, "updated_at": raw_store.now(),
        "source": {"path": str(source)},
        "parameters": {"region": args.region, "psm": args.psm, "dpi": args.dpi,
                       "force_reocr": bool(args.force_reocr)},
        "pages": [merged[p] for p in sorted(merged)],
        "missing_images": missing,
        "error_pages": sorted(p for p, r in merged.items() if r.get("status") == "error"),
    })
    counts = {s: sum(1 for r in records if r["status"] == s) for s in ("ocr", "cached", "error")}
    log.info("[%s] xong: %s, thiếu ảnh %d -> %s", layer, counts, len(missing), path)
    if failed:
        raise RuntimeError(f"[{layer}] {len(failed)} trang lỗi OCR (đã chạy hết các trang còn lại): "
                           f"{compact_pages(failed)}")
    return path


def compact_pages(pages: list[int]) -> str:
    """[1, 2, 3, 7] -> '1-3,7' cho log gọn."""
    out: list[tuple[int, int]] = []
    for p in sorted(pages):
        if out and out[-1][1] == p - 1:
            out[-1] = (out[-1][0], p)
        else:
            out.append((p, p))
    return ",".join(str(a) if a == b else f"{a}-{b}" for a, b in out)


def run(args) -> Path:
    if getattr(args, "layer", None) in LAYER_ENGINES:
        return run_layer_ocr(args)
    if getattr(args, "unified", False) or (not getattr(args, "writing_system", None) and not getattr(args, "layer", None)):
        return run_unified(args)
    if args.writing_system:
        return run_writing_system(args)
    raise ValueError("Cần --unified hoặc --writing-system chi_tra|vie (chế độ mới) hoặc --layer (chế độ cũ)")
    # return run_legacy(args)


# def run_legacy(args) -> Path:
#     pages = requested_pages(args.pages)
#     args.region = parse_region(args.region)
#     if not args.source:
#         raise ValueError("Thiếu --source (CLI hoặc config)")
#     # print(f"Pages: {pages}")
    
#     source = Path(args.source).resolve()
#     images = discover_images(source, pages)
#     args.engines = list(dict.fromkeys(x.strip() for x in args.engines.split(",") if x.strip()))
#     if not args.engines or any(name not in ENGINES for name in args.engines):
#         raise ValueError(f"Engine không hợp lệ. Có sẵn: {', '.join(ENGINES)}")
#     if args.select not in SELECTORS:
#         raise ValueError("Selector không hợp lệ")
#     if args.workers < 1 or args.dpi < 1 or not 0 <= args.psm <= 13:
#         raise ValueError("Cần workers >= 1, dpi >= 1, psm trong 0..13")
#     if not math.isfinite(args.low_conf) or not 0 <= args.low_conf <= 100:
#         raise ValueError("--low-conf phải trong 0..100")
#     if args.keep is not None and args.keep not in ("cjk", "latin", "none"):
#         raise ValueError("--keep phải là cjk, latin hoặc none")
#     args.lang = args.lang or LAYERS[args.layer].ocr_lang
#     args.keep = LAYERS[args.layer].keep if args.keep is None else (None if args.keep == "none" else args.keep)
#     layout = DataLayout(Path(args.data_root), args.work_id)
#     if layout.layer_xml(args.layer).exists() and not args.force:
#         raise FileExistsError(f"{layout.layer_xml(args.layer)} đã tồn tại; dùng --force để xuất lại XML")
#     MetadataStore(layout).load()
#     descriptors = {name: ENGINES[name].describe(args.lang) for name in args.engines}
#     source_info = register_source(layout, source)
#     registered = layout.work_dir / source_info["path"]
#     print(f"[TEST] {pages} trang từ {source} -> {registered}")
#     registered_images = discover_images(registered, pages)
    
#     for page in pages:
#         if sha256_file(images[page]) != sha256_file(registered_images[page]):
#             raise ValueError(f"Nguồn lưu trữ khác ảnh trang {page}; dùng tên thư mục nguồn mới")
#     workers = min(args.workers, len(pages), *(ENGINES[n].max_workers for n in args.engines))
#     worker_state = threading.local()
#     with ThreadPoolExecutor(max_workers=workers) as pool:
#         results = list(pool.map(lambda p: process_page(p, registered_images[p], layout,
#                             args, descriptors, worker_state), pages))
#     low_conf = [r["page"] for r in results if r["confidence"] is None or r["confidence"] < args.low_conf]
#     empty = [r["page"] for r in results if r["empty"]]
#     if empty: 
#         log.warning("Trang không có dòng sau lọc: %s", empty)
#     doc = build_document(args.work_id, args.layer, make_meta(
#         args.layer, title=args.title, source=args.source_label or source.name,
#         method="OCR: " + ", ".join(args.engines), version=args.version, translator=args.translator),
#         [RawPage(r["page"], r["lines"]) for r in results])
#     return finalize(doc, layout, source_info, {
#         "method": "ocr", "engines": descriptors,
#         "parameters": {"pages": pages, "region": args.region, "lang": args.lang,
#                        "psm": args.psm, "dpi": args.dpi, "keep": args.keep,
#                        "select": args.select, "low_conf": args.low_conf,
#                        "workers_requested": args.workers, "workers_used": workers},
#         "low_conf_pages": low_conf, "empty_pages": empty,
#         "pages": [{k: v for k, v in r.items() if k != "lines"} for r in results],
#         "review_required": True,
#     }, force=args.force)


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
