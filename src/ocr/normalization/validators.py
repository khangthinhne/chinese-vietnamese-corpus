"""Validators for normalized OCR page output.

Ensures strict compliance with the Canonical Normalized Schema (v1.0),
validating coordinate bounds, confidence ranges, deterministic line IDs,
engine statuses, and JSON serializability.
"""

from __future__ import annotations

import json
import math
from typing import Any

from src.ocr.normalization.schema import SCHEMA_VERSION, NormalizedPage


def validate_normalized_page(data: dict[str, Any] | NormalizedPage) -> list[str]:
    """Validates a normalized page dictionary or dataclass.

    Returns:
        A list of error strings. Empty list indicates full validity.
    """
    if isinstance(data, NormalizedPage):
        raw = data.to_dict()
    elif isinstance(data, dict):
        raw = data
    else:
        return [f"Input must be a dict or NormalizedPage, got {type(data).__name__}"]

    errors: list[str] = []

    # 1. JSON serializability
    try:
        json.dumps(raw, ensure_ascii=False)
    except (TypeError, ValueError, OverflowError) as exc:
        errors.append(f"Payload is not valid JSON serializable: {exc}")
        return errors

    # 2. Top-level metadata
    version = raw.get("schema_version")
    if not version or not str(version).startswith("1."):
        errors.append(f"Invalid or unsupported schema_version: {version!r} (expected '1.x')")

    doc_id = raw.get("document_id")
    if not doc_id or not isinstance(doc_id, str) or not doc_id.strip():
        errors.append(f"Missing or invalid document_id: {doc_id!r}")

    ws = raw.get("writing_system")
    if not ws or not isinstance(ws, str) or not ws.strip():
        errors.append(f"Missing or invalid writing_system: {ws!r}")

    # 3. Page information
    page = raw.get("page")
    page_w, page_h = 0, 0
    if not isinstance(page, dict):
        errors.append(f"Missing or non-dict 'page' object: {page!r}")
    else:
        page_id = page.get("page_id")
        if not page_id or not isinstance(page_id, str):
            errors.append(f"page.page_id must be a non-empty string: {page_id!r}")

        page_no = page.get("page_number")
        if not isinstance(page_no, int) or page_no <= 0:
            errors.append(f"page.page_number must be a positive integer: {page_no!r}")

        image = page.get("image")
        if not image or not isinstance(image, str):
            errors.append(f"page.image must be a non-empty string path: {image!r}")

        image_size = page.get("image_size")
        if not isinstance(image_size, dict):
            errors.append(f"page.image_size must be a dict: {image_size!r}")
        else:
            w = image_size.get("width")
            h = image_size.get("height")
            if not isinstance(w, int) or w <= 0:
                errors.append(f"page.image_size.width must be a positive integer: {w!r}")
            else:
                page_w = w
            if not isinstance(h, int) or h <= 0:
                errors.append(f"page.image_size.height must be a positive integer: {h!r}")
            else:
                page_h = h

    # 4. Engines
    engines = raw.get("engines")
    if not isinstance(engines, dict) or not engines:
        errors.append(f"Field 'engines' must be a non-empty dict mapping engine names to results")
        return errors

    valid_statuses = {"success", "empty", "failed", "not_run"}
    for eng_name, eng_data in engines.items():
        ctx = f"engines.{eng_name}"
        if not isinstance(eng_data, dict):
            errors.append(f"{ctx} must be a dictionary")
            continue

        # Status
        status = eng_data.get("status")
        if status not in valid_statuses:
            errors.append(f"{ctx}.status must be one of {valid_statuses}, got: {status!r}")

        # Source
        source = eng_data.get("source")
        if not isinstance(source, dict):
            errors.append(f"{ctx}.source must be a dict")
        else:
            raw_file = source.get("raw_file")
            if not raw_file or not isinstance(raw_file, str):
                errors.append(f"{ctx}.source.raw_file must be a string path")

        # Metadata
        metadata = eng_data.get("metadata")
        if not isinstance(metadata, dict):
            errors.append(f"{ctx}.metadata must be a dict")
        else:
            engine_field = metadata.get("engine")
            if not engine_field or not isinstance(engine_field, str):
                errors.append(f"{ctx}.metadata.engine must be a non-empty string")
            if not isinstance(metadata.get("parameters"), dict):
                errors.append(f"{ctx}.metadata.parameters must be a dict")

        # Coordinate System
        coords = eng_data.get("coordinate_system")
        if not isinstance(coords, dict):
            errors.append(f"{ctx}.coordinate_system must be a dict")
        else:
            if coords.get("format") != "xyxy":
                errors.append(f"{ctx}.coordinate_system.format must be 'xyxy'")
            if coords.get("unit") != "pixel":
                errors.append(f"{ctx}.coordinate_system.unit must be 'pixel'")
            if coords.get("origin") != "top_left":
                errors.append(f"{ctx}.coordinate_system.origin must be 'top_left'")
            if coords.get("reference") != "original_page":
                errors.append(f"{ctx}.coordinate_system.reference must be 'original_page'")

        # Raw output
        if "raw_output" not in eng_data:
            errors.append(f"{ctx} missing 'raw_output' field")

        # Lines
        lines = eng_data.get("lines")
        if not isinstance(lines, list):
            errors.append(f"{ctx}.lines must be a list")
            continue

        if status == "empty" and lines:
            errors.append(f"{ctx} has status 'empty' but lines is not empty ({len(lines)} lines)")
        elif status == "failed" and lines:
            errors.append(f"{ctx} has status 'failed' but lines is not empty ({len(lines)} lines)")
        elif status == "success" and not lines:
            errors.append(f"{ctx} has status 'success' but lines is empty (should be 'empty')")

        seen_line_ids: set[str] = set()
        seen_orders: set[int] = set()

        for idx, line in enumerate(lines, start=1):
            line_ctx = f"{ctx}.lines[{idx-1}]"
            if not isinstance(line, dict):
                errors.append(f"{line_ctx} must be a dict")
                continue

            # line_id
            lid = line.get("line_id")
            if not lid or not isinstance(lid, str):
                errors.append(f"{line_ctx}.line_id must be a non-empty string")
            elif lid in seen_line_ids:
                errors.append(f"{line_ctx}.line_id duplicate ID: {lid}")
            else:
                seen_line_ids.add(lid)

            # order
            order = line.get("order")
            if not isinstance(order, int) or order <= 0:
                errors.append(f"{line_ctx}.order must be a positive integer, got: {order!r}")
            elif order in seen_orders:
                errors.append(f"{line_ctx}.order duplicate reading order: {order}")
            else:
                seen_orders.add(order)

            # text
            text = line.get("text")
            if text is None or not isinstance(text, str):
                errors.append(f"{line_ctx}.text must be a string, got: {type(text).__name__}")

            # bbox
            bbox = line.get("bbox")
            if bbox is not None:
                if not isinstance(bbox, (list, tuple)) or len(bbox) != 4:
                    errors.append(f"{line_ctx}.bbox must be [x0, y0, x1, y1] or None, got: {bbox!r}")
                else:
                    x0, y0, x1, y1 = bbox
                    if not all(isinstance(v, (int, float)) and not math.isnan(v) for v in (x0, y0, x1, y1)):
                        errors.append(f"{line_ctx}.bbox values must be non-NaN numbers: {bbox!r}")
                    else:
                        if x0 > x1:
                            errors.append(f"{line_ctx}.bbox x0 ({x0}) > x1 ({x1})")
                        if y0 > y1:
                            errors.append(f"{line_ctx}.bbox y0 ({y0}) > y1 ({y1})")
                        if x0 < 0 or y0 < 0:
                            errors.append(f"{line_ctx}.bbox coordinates cannot be negative: {bbox!r}")
                        if page_w > 0 and x1 > page_w + 1:
                            errors.append(f"{line_ctx}.bbox x1 ({x1}) exceeds page width ({page_w})")
                        if page_h > 0 and y1 > page_h + 1:
                            errors.append(f"{line_ctx}.bbox y1 ({y1}) exceeds page height ({page_h})")

            # confidence
            conf = line.get("confidence")
            if conf is not None:
                if not isinstance(conf, (int, float)) or math.isnan(conf) or math.isinf(conf):
                    errors.append(f"{line_ctx}.confidence must be float or None, got: {conf!r}")
                elif not 0.0 <= conf <= 100.0:
                    errors.append(f"{line_ctx}.confidence out of range 0..100: {conf}")

            # tokens
            tokens = line.get("tokens")
            if tokens is not None:
                if not isinstance(tokens, list):
                    errors.append(f"{line_ctx}.tokens must be a list or None")
                else:
                    for tidx, tok in enumerate(tokens):
                        if not isinstance(tok, dict) or "token_id" not in tok or "text" not in tok:
                            errors.append(f"{line_ctx}.tokens[{tidx}] missing token_id or text")

            # metadata
            if not isinstance(line.get("metadata"), dict):
                errors.append(f"{line_ctx}.metadata must be a dict")

    return errors
