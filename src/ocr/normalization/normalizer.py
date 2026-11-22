"""Unified OCR Output Normalization Layer.

Normalizes raw OCR outputs across engines (Qwen, PaddleOCR, KimHanNom, Tesseract)
into a single, consistent, engine-independent JSON schema.
"""

from __future__ import annotations

import argparse
import json
import logging
import math
import os
import sys
from pathlib import Path
from typing import Any

from src.common.corpus import (
    DataLayout,
    _atomic_write,
    parse_pages,
)
from src.ocr import raw_store
from src.ocr.normalization.schema import (
    CoordinateSystem,
    EngineMetadata,
    ImageSize,
    NormalizedEngineResult,
    NormalizedLine,
    NormalizedPage,
    PageInfo,
    SCHEMA_VERSION,
    SourceInfo,
    TokenInfo,
)
from src.ocr.normalization.validators import validate_normalized_page

log = logging.getLogger("ocr.normalization")

ACTIVE_ENGINES = ["kimhannom", "qwen", "tesseract"]

ENGINE_ALIASES: dict[str, str] = {
    "kimhannom": "kimhannom",
    "kim": "kimhannom",
    "kim_han_nom": "kimhannom",
    "qwen": "qwen",
    "qwen2.5-vl": "qwen",
    "qwen2.5": "qwen",
    "tesseract": "tesseract",
    "tess": "tesseract",
    "paddle": "paddleocr",
    "paddleocr": "paddleocr",
    "paddle_ocr": "paddleocr",
}


class AmbiguousRunError(ValueError):
    """Raised when multiple distinct OCR runs exist for an engine and cannot be resolved."""


def canonical_engine_name(name: str) -> str:
    """Returns canonical normalized engine name."""
    clean = str(name).strip().lower()
    if clean in ENGINE_ALIASES:
        return ENGINE_ALIASES[clean]
    if "kim" in clean:
        return "kimhannom"
    if "qwen" in clean:
        return "qwen"
    if "tesseract" in clean or "tess" in clean:
        return "tesseract"
    if "paddle" in clean:
        return "paddleocr"
    return clean


def make_relative_image_path(layout: DataLayout, image_path: str | Path | None) -> str:
    """Normalizes image path to be workspace-relative or work_dir-relative for portability.

    Prevents machine-specific absolute paths from leaking into normalized JSON.
    """
    if not image_path:
        return ""
    p = Path(image_path)
    try:
        return p.resolve().relative_to(layout.work_dir.resolve()).as_posix()
    except (ValueError, RuntimeError):
        pass

    try:
        return p.resolve().relative_to(layout.root.resolve()).as_posix()
    except (ValueError, RuntimeError):
        pass

    return p.as_posix()


def normalize_bbox(
    bbox: Any,
    image_size: tuple[int, int] | list[int] | ImageSize | None = None,
    crop_offset: tuple[int, int] | list[int] | None = None,
    crop_box: tuple[int, int, int, int] | list[int] | None = None,
    force_crop_relative: bool = False,
) -> list[int] | None:
    """Normalizes bounding box to [x0, y0, x1, y1] in original-page pixel coordinates.

    Coordinate Contract:
    1. Raw store line bboxes (run['lines']) are already in original-page pixel coordinates
       as produced by src/ocr/ocr.py. They must not have crop offsets added again.
    2. Normalized bboxes must also be in original-page pixel coordinates.
    3. If an input bbox is crop-relative (such as raw engine output before ocr.py storage),
       caller passes crop_offset or crop_box to convert it to page coordinates exactly once.
    4. Missing bboxes (bbox=None) are preserved as None without fabricating coordinates.
    5. Coordinates are validated (x0 <= x1, y0 <= y1) and clamped to image dimensions [0, width], [0, height].
    """
    if bbox is None:
        return None

    # 1. Extract 4 coordinates from polygon, rectangle list, or dict
    x0, y0, x1, y1 = 0.0, 0.0, 0.0, 0.0
    if isinstance(bbox, (list, tuple)):
        if len(bbox) == 4 and all(isinstance(v, (int, float)) for v in bbox):
            x0, y0, x1, y1 = [float(v) for v in bbox]
        elif len(bbox) >= 3 and all(isinstance(pt, (list, tuple)) and len(pt) >= 2 for pt in bbox):
            xs = [float(pt[0]) for pt in bbox]
            ys = [float(pt[1]) for pt in bbox]
            x0, y0, x1, y1 = min(xs), min(ys), max(xs), max(ys)
        else:
            return None
    elif isinstance(bbox, dict):
        if all(k in bbox for k in ("x0", "y0", "x1", "y1")):
            x0, y0, x1, y1 = float(bbox["x0"]), float(bbox["y0"]), float(bbox["x1"]), float(bbox["y1"])
        elif all(k in bbox for k in ("left", "top", "width", "height")):
            x0 = float(bbox["left"])
            y0 = float(bbox["top"])
            x1 = x0 + float(bbox["width"])
            y1 = y0 + float(bbox["height"])
        else:
            return None
    else:
        return None

    if math.isnan(x0) or math.isnan(y0) or math.isnan(x1) or math.isnan(y1):
        return None

    # Ensure x0 <= x1 and y0 <= y1
    if x0 > x1:
        x0, x1 = x1, x0
    if y0 > y1:
        y0, y1 = y1, y0

    # 2. Add crop offset if explicitly specified (e.g. for crop-relative raw engine inputs)
    offset = crop_offset if crop_offset is not None else crop_box
    if offset and len(offset) >= 2:
        cx0, cy0 = float(offset[0]), float(offset[1])
        x0 += cx0
        x1 += cx0
        y0 += cy0
        y1 += cy0

    # 3. Clamp to page dimensions if available
    w_limit: int | None = None
    h_limit: int | None = None
    if isinstance(image_size, ImageSize):
        w_limit, h_limit = image_size.width, image_size.height
    elif image_size and len(image_size) >= 2:
        w_limit, h_limit = int(image_size[0]), int(image_size[1])

    ix0 = max(0, int(round(x0)))
    iy0 = max(0, int(round(y0)))
    ix1 = max(ix0, int(round(x1)))
    iy1 = max(iy0, int(round(y1)))

    if w_limit is not None and w_limit > 0:
        ix0 = min(ix0, w_limit)
        ix1 = min(ix1, w_limit)
    if h_limit is not None and h_limit > 0:
        iy0 = min(iy0, h_limit)
        iy1 = min(iy1, h_limit)

    return [ix0, iy0, ix1, iy1]


def normalize_confidence(conf: Any, engine_name: str | None = None) -> float | None:
    """Normalizes confidence score to a display scale of 0.0 - 100.0 without inventing missing values."""
    if conf is None:
        return None
    try:
        val = float(conf)
    except (ValueError, TypeError):
        return None

    if math.isnan(val) or math.isinf(val):
        return None

    eng = canonical_engine_name(engine_name) if engine_name else ""

    # PaddleOCR outputs rec_scores in 0..1 scale
    if eng == "paddleocr" and 0.0 <= val <= 1.0:
        val = val * 100.0
    elif 0.0 < val <= 1.0 and eng not in ("tesseract", "qwen"):
        val = val * 100.0

    val = max(0.0, min(100.0, val))
    return round(val, 2)


def extract_engine_name(run: dict[str, Any]) -> str | None:
    """Extracts canonical engine name from a raw OCR run."""
    engine_val = run.get("parameters", {}).get("engine")
    if isinstance(engine_val, dict):
        name = engine_val.get("name")
        if name:
            return canonical_engine_name(name)
    elif isinstance(engine_val, str) and engine_val:
        return canonical_engine_name(engine_val)

    if run.get("engine"):
        return canonical_engine_name(run["engine"])

    return None


def select_run_for_engine(
    runs: list[dict[str, Any]],
    engine_name: str,
    page: int = 1,
    manifest: dict[str, Any] | None = None,
    selected_run_keys: dict[str, str] | None = None,
    explicit_key: str | None = None,
) -> dict[str, Any] | None:
    """Selects the designated run for an engine on a given page, preventing silent arbitrary selection."""
    engine_name = canonical_engine_name(engine_name)

    # 1. Explicit run key provided by user or caller
    if explicit_key:
        for r in runs:
            if r.get("key") == explicit_key:
                return r
        raise ValueError(
            f"Page {page}: Specified run_key '{explicit_key}' for engine '{engine_name}' not found"
        )

    if selected_run_keys and engine_name in selected_run_keys:
        target_key = selected_run_keys[engine_name]
        for r in runs:
            if r.get("key") == target_key:
                return r
        raise ValueError(
            f"Page {page}: Specified run_key '{target_key}' for engine '{engine_name}' not found"
        )

    # 2. Check manifest selection for this page
    if manifest:
        # Check manifest top-level selection ONLY if selected_engine matches this engine
        if canonical_engine_name(manifest.get("selected_engine", "")) == engine_name:
            m_key = manifest.get("selected_key")
            if m_key:
                for r in runs:
                    if r.get("key") == m_key:
                        return r

        m_engines = manifest.get("engines")
        if isinstance(m_engines, list):
            for entry in m_engines:
                if isinstance(entry, dict) and canonical_engine_name(entry.get("engine", "")) == engine_name:
                    m_key = entry.get("key")
                    if m_key:
                        for r in runs:
                            if r.get("key") == m_key:
                                return r

        page_recs = {p.get("page"): p for p in manifest.get("pages", []) if isinstance(p, dict)}
        page_rec = page_recs.get(page)
        if page_rec:
            p_engines = page_rec.get("engines")
            if isinstance(p_engines, list):
                for entry in p_engines:
                    if isinstance(entry, dict) and canonical_engine_name(entry.get("engine", "")) == engine_name:
                        key = entry.get("key")
                        if key:
                            for r in runs:
                                if r.get("key") == key:
                                    return r

            if canonical_engine_name(page_rec.get("selected_engine", "")) == engine_name:
                key = page_rec.get("selected_key")
                if key:
                    for r in runs:
                        if r.get("key") == key:
                            return r

    # 3. Filter runs by engine name
    candidates = [r for r in runs if extract_engine_name(r) == engine_name]

    if not candidates:
        return None

    if len(candidates) == 1:
        return candidates[0]

    # Check if all candidate runs have the identical key (e.g. repeated idempotent cache runs)
    unique_keys = {r.get("key") for r in candidates if r.get("key")}
    if len(unique_keys) == 1:
        return candidates[-1]

    # Multiple runs exist with different keys and neither manifest nor explicit config resolved it
    raise AmbiguousRunError(
        f"Page {page} has {len(candidates)} distinct runs for engine '{engine_name}' "
        f"with different run keys {sorted(unique_keys)}. Use manifest or explicit run_key selection."
    )


def extract_engine_metadata(run: dict[str, Any], engine_name: str) -> EngineMetadata:
    """Builds EngineMetadata from a raw OCR run."""
    params = dict(run.get("parameters", {}))
    eng_info = params.get("engine", {})

    version: str | None = None
    model: str | None = None

    if isinstance(eng_info, dict):
        version = eng_info.get("version") or eng_info.get("paddleocr_version")
        if not version and "adapter_version" in eng_info:
            version = str(eng_info["adapter_version"])
        model = (
            eng_info.get("model_id")
            or eng_info.get("model")
            or eng_info.get("models", {}).get(params.get("lang", ""))
        )
        if not model and "model" in params:
            model = str(params["model"])

    # Engine-specific fallback extractions
    if engine_name == "qwen":
        if not model and isinstance(eng_info, dict):
            model = eng_info.get("model_id")
    elif engine_name == "tesseract":
        if not version and isinstance(eng_info, dict):
            version = eng_info.get("version")
    elif engine_name == "paddleocr":
        if not version and isinstance(eng_info, dict):
            version = eng_info.get("paddleocr_version")
    elif engine_name == "kimhannom":
        if not version and isinstance(eng_info, dict):
            version = eng_info.get("version")
        if not model and isinstance(eng_info, dict):
            model = str(eng_info.get("ocr_id", "")) or None

    return EngineMetadata(
        engine=engine_name,
        engine_version=version,
        model=model,
        parameters=params,
    )


def normalize_run(
    run: dict[str, Any],
    engine_name: str,
    raw_file_rel: Any = "",
    image_size: ImageSize | tuple[int, int] | None = None,
    crop_box: list[int] | None = None,
    crop_offset: list[int] | None = None,
) -> NormalizedEngineResult:
    """Converts a raw OCR run into a NormalizedEngineResult."""
    # If caller passed (run, engine_name, image_size) positionally:
    if isinstance(raw_file_rel, (ImageSize, tuple, list)):
        crop_box = image_size if isinstance(image_size, (list, tuple)) else crop_box
        image_size = raw_file_rel
        raw_file_rel = run.get("raw_file", "")

    effective_crop = crop_box if crop_box is not None else crop_offset
    canonical_eng = canonical_engine_name(engine_name)
    metadata = extract_engine_metadata(run, canonical_eng)
    source = SourceInfo(run_key=run.get("key"), raw_file=str(raw_file_rel or run.get("raw_file", "")))
    coord_sys = CoordinateSystem()

    raw_lines = run.get("lines")
    run_crop = run.get("crop_box") or effective_crop

    # Handle error or failure state
    if run.get("error") or run.get("status") == "failed":
        return NormalizedEngineResult(
            status="failed",
            source=source,
            metadata=metadata,
            coordinate_system=coord_sys,
            lines=[],
            raw_output=run.get("raw", {}),
            error=str(run.get("error") or "Run recorded as failed"),
        )

    # Extract lines from engine-specific structures if "lines" is missing
    if raw_lines is None:
        raw_payload = run.get("raw") or run.get("raw_response") or {}
        if isinstance(raw_payload, dict):
            k_texts = raw_payload.get("result_ocr_text")
            k_boxes = raw_payload.get("result_bbox") or []
            if isinstance(k_texts, list):
                raw_lines = []
                for idx, t in enumerate(k_texts):
                    b = k_boxes[idx] if idx < len(k_boxes) else None
                    raw_lines.append({"text": t, "bbox": b, "confidence": None})
            elif isinstance(k_texts, str) and k_texts.strip():
                raw_lines = [{"text": k_texts.strip(), "bbox": None, "confidence": None}]
        if raw_lines is None and run.get("items"):
            raw_lines = run["items"]

    if not raw_lines:
        return NormalizedEngineResult(
            status="empty",
            source=source,
            metadata=metadata,
            coordinate_system=coord_sys,
            lines=[],
            raw_output=run.get("raw", {}),
        )

    normalized_lines: list[NormalizedLine] = []
    page_dims = (image_size.width, image_size.height) if isinstance(image_size, ImageSize) else image_size

    # Coordinate Contract:
    # Lines stored in run["lines"] are ALREADY in original-page pixel coordinates as guaranteed
    # by src/ocr/ocr.py. Never re-apply crop offsets to them.
    # If lines are extracted directly from crop-relative raw payloads, apply run_crop offset once.
    is_from_store_lines = run.get("lines") is not None
    line_crop_offset = None if is_from_store_lines else run_crop

    for order_idx, r_line in enumerate(raw_lines, start=1):
        text_val = str(r_line.get("text", ""))
        bbox_val = normalize_bbox(r_line.get("bbox"), page_dims, crop_offset=line_crop_offset)
        conf_val = normalize_confidence(r_line.get("confidence"), canonical_eng)

        line_id = f"{canonical_eng}.L{order_idx:03d}"

        # Tokens (if available)
        tokens_val: list[TokenInfo] | None = None
        raw_tokens = r_line.get("tokens")
        if isinstance(raw_tokens, list):
            tokens_val = []
            for t_idx, tok in enumerate(raw_tokens, start=1):
                if isinstance(tok, dict):
                    tok_text = str(tok.get("text", ""))
                    tok_bbox = normalize_bbox(tok.get("bbox"), page_dims, crop_offset=line_crop_offset)
                    tok_conf = normalize_confidence(tok.get("confidence"), canonical_eng)
                    tok_id = tok.get("token_id") or f"{line_id}.C{t_idx:03d}"
                    tokens_val.append(TokenInfo(token_id=tok_id, text=tok_text, bbox=tok_bbox, confidence=tok_conf))

        # Preserve line metadata
        line_meta = dict(r_line.get("metadata", {}))
        if r_line.get("confidence") is not None and conf_val != r_line.get("confidence"):
            line_meta["original_confidence"] = r_line["confidence"]

        normalized_lines.append(
            NormalizedLine(
                line_id=line_id,
                order=order_idx,
                text=text_val,
                bbox=bbox_val,
                confidence=conf_val,
                tokens=tokens_val,
                metadata=line_meta,
            )
        )

    return NormalizedEngineResult(
        status="success",
        source=source,
        metadata=metadata,
        coordinate_system=coord_sys,
        lines=normalized_lines,
        raw_output=run.get("raw", {}),
    )


def normalize_page(
    layout: DataLayout,
    writing_system: str,
    page: int,
    requested_engines: list[str] | None = None,
    selected_runs: dict[str, str] | None = None,
) -> NormalizedPage:
    """Loads a raw page JSON and normalizes all requested or available engine runs."""
    raw_file = raw_store.raw_path(layout, writing_system, page)
    if not raw_file.is_file():
        raise FileNotFoundError(f"Raw OCR page file not found: {raw_file}")

    raw_data = raw_store.load_page(layout, writing_system, page, layout.work_id)
    manifest = raw_store.load_manifest(layout, writing_system)

    runs = raw_data.get("runs", [])
    raw_rel_path = layout.rel(raw_file)

    # Determine image dimensions and path
    img_path_str = f"02_extraction/pages/page_{page:03d}.png"
    width, height = 2480, 3508
    crop_box: list[int] | None = None

    for cand in (
        layout.work_dir / f"02_extraction/pages/page_{page:04d}.png",
        layout.work_dir / f"02_extraction/pages/page_{page:03d}.png",
        layout.work_dir / f"02_extraction/01_page_image/page_{page:04d}.png",
        layout.work_dir / f"02_extraction/01_page_image/page_{page:03d}.png",
    ):
        if cand.is_file():
            img_path_str = layout.rel(cand)
            try:
                from PIL import Image as PILImage
                with PILImage.open(cand) as im:
                    width, height = im.size
            except Exception:
                pass
            break

    for r in runs:
        if r.get("image") and not img_path_str:
            img_path_str = make_relative_image_path(layout, r["image"])
        if r.get("image_size") and len(r["image_size"]) >= 2:
            width, height = int(r["image_size"][0]), int(r["image_size"][1])
        if r.get("crop_box"):
            crop_box = [int(v) for v in r["crop_box"]]
        if img_path_str and width > 0 and r.get("image_size"):
            break

    image_size = ImageSize(width=width, height=height)
    page_id = f"{layout.work_id}.P{page:04d}"
    page_info = PageInfo(
        page_id=page_id,
        page_number=page,
        image=img_path_str,
        image_size=image_size,
    )

    if requested_engines:
        target_engines = [canonical_engine_name(e) for e in requested_engines]
    else:
        # Default workflow normalizes all active engines in the project
        target_engines = list(ACTIVE_ENGINES)

    normalized_engines: dict[str, NormalizedEngineResult] = {}

    for eng in target_engines:
        run = select_run_for_engine(runs, eng, page, manifest, selected_runs)
        if run is None:
            # Engine requested but no matching run found
            meta = EngineMetadata(engine=eng)
            src = SourceInfo(run_key=None, raw_file=raw_rel_path)
            normalized_engines[eng] = NormalizedEngineResult(
                status="not_run",
                source=src,
                metadata=meta,
                coordinate_system=CoordinateSystem(),
                lines=[],
                raw_output={},
                error=f"No OCR run found for engine '{eng}' on page {page}",
            )
        else:
            normalized_engines[eng] = normalize_run(
                run=run,
                engine_name=eng,
                raw_file_rel=raw_rel_path,
                image_size=image_size,
                crop_box=crop_box,
            )

    normalized_page = NormalizedPage(
        document_id=layout.work_id,
        writing_system=writing_system,
        page=page_info,
        engines=normalized_engines,
    )

    # Validate normalized output before returning
    errors = validate_normalized_page(normalized_page)
    if errors:
        raise ValueError(
            f"Validation failed for normalized page {page} ({layout.work_id}/{writing_system}):\n  "
            + "\n  ".join(errors)
        )

    return normalized_page


class OcrNormalizer:
    """Manages execution and persistence of normalized OCR output."""

    def __init__(self, layout: DataLayout):
        self.layout = layout

    def normalize_and_save(
        self,
        writing_system: str,
        page: int,
        requested_engines: list[str] | None = None,
        force: bool = False,
        dry_run: bool = False,
        selected_runs: dict[str, str] | None = None,
    ) -> NormalizedPage:
        out_path = self.layout.ocr_normalized_path(writing_system, page)
        if out_path.exists() and not force and not dry_run:
            raise FileExistsError(
                f"Normalized output already exists at {out_path}. Use --force to overwrite."
            )

        norm_page = normalize_page(
            self.layout,
            writing_system,
            page,
            requested_engines=requested_engines,
            selected_runs=selected_runs,
        )

        if dry_run:
            log.info(
                "[DRY RUN] Page %d (%s) normalized successfully with %d engines (not written)",
                page,
                writing_system,
                len(norm_page.engines),
            )
            return norm_page

        out_path.parent.mkdir(parents=True, exist_ok=True)
        json_data = json.dumps(norm_page.to_dict(), ensure_ascii=False, indent=2).encode("utf-8")
        _atomic_write(out_path, json_data)
        log.info(
            "Normalized page %d (%s) -> %s (%d engines)",
            page,
            writing_system,
            out_path,
            len(norm_page.engines),
        )
        return norm_page

    def run(
        self,
        writing_system: str,
        pages: list[int],
        engines: list[str] | None = None,
        force: bool = False,
        dry_run: bool = False,
    ) -> list[NormalizedPage]:
        results: list[NormalizedPage] = []
        for page in pages:
            res = self.normalize_and_save(
                writing_system=writing_system,
                page=page,
                requested_engines=engines,
                force=force,
                dry_run=dry_run,
            )
            results.append(res)
        return results


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Unified OCR Output Normalization Layer (standardizes raw OCR into canonical schema)",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--work-id", help="Work identifier, e.g. CPO_18")
    parser.add_argument(
        "--writing-system",
        required=True,
        choices=list(raw_store.WRITING_SYSTEMS),
        help="Writing system, e.g. chi_tra | vie",
    )
    parser.add_argument("--pages", help="Pages range or list, e.g. 205-206 or 205,206")
    parser.add_argument("--data-root", default="data", help="Root data folder")
    parser.add_argument(
        "--engines",
        help="Comma-separated engine list, e.g. qwen,paddleocr,kimhannom,tesseract",
    )
    parser.add_argument("--config", help="Optional path to pipeline config JSON")
    parser.add_argument("--force", action="store_true", help="Overwrite existing normalized outputs")
    parser.add_argument("--dry-run", action="store_true", help="Validate without writing files to disk")
    parser.add_argument("-v", "--verbose", action="store_true", help="Enable verbose debug logging")

    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    work_id = args.work_id
    pages_str = args.pages

    if args.config:
        cfg_path = Path(args.config)
        if cfg_path.is_file():
            cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
            common = cfg.get("common", {})
            if not work_id:
                work_id = common.get("work_id")
            if not pages_str:
                pages_str = str(common.get("pages", ""))

    if not work_id:
        parser.error("Missing --work-id (or specify in --config)")
    if not pages_str:
        parser.error("Missing --pages (or specify in --config)")

    pages = parse_pages(pages_str)
    layout = DataLayout(Path(args.data_root), work_id)

    engine_list = [e.strip() for e in args.engines.split(",") if e.strip()] if args.engines else None

    normalizer = OcrNormalizer(layout)
    try:
        normalizer.run(
            writing_system=args.writing_system,
            pages=pages,
            engines=engine_list,
            force=args.force,
            dry_run=args.dry_run,
        )
        return 0
    except (FileNotFoundError, ValueError, AmbiguousRunError, FileExistsError) as exc:
        log.error("Normalization failed: %s", exc)
        return 2


if __name__ == "__main__":
    sys.exit(main())
