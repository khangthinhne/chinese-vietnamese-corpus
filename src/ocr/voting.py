"""Standalone, deterministic weighted character voting for already matched OCR lines.

No OCR runtime is imported. GAP is represented by None, never a literal character.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
from decimal import Decimal, localcontext
import json
import math
import os
from pathlib import Path
import sys
import tempfile
import unicodedata

SCHEMA_VERSION = 1
ALGORITHM_VERSION = "progressive-weighted-sop-v1"


def _json_weight(value: Decimal) -> int | float:
    # JSON supports arbitrary-sized integers even when their float would overflow.
    if value == value.to_integral_value():
        return int(value)
    number = float(value)
    return number if math.isfinite(number) else int(value)


def _number(value, path: str) -> None:
    try:
        valid = (isinstance(value, (int, float)) and not isinstance(value, bool)
                 and math.isfinite(value))
    except OverflowError:
        valid = False
    if not valid:
        raise ValueError(f"{path}: phải là số hữu hạn")


def _identifier(value, path: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{path}: phải là chuỗi không rỗng")


def _validate(payload: dict) -> dict[str, Decimal]:
    if not isinstance(payload, dict):
        raise ValueError("input: phải là JSON object")
    _identifier(payload.get("page_id"), "page_id")
    weights = payload.get("engine_weights")
    if not isinstance(weights, dict) or not weights:
        raise ValueError("engine_weights: phải là object không rỗng")
    for name, value in weights.items():
        _identifier(name, "engine_weights key")
        _number(value, f"engine_weights.{name}")
        if value < 0:
            raise ValueError(f"engine_weights.{name}: không được âm")
    if not any(value > 0 for value in weights.values()):
        raise ValueError("engine_weights: tổng trọng số phải lớn hơn 0")
    results = payload.get("engine_results")
    if not isinstance(results, list):
        raise ValueError("engine_results: phải là danh sách")
    seen = set()
    for i, result in enumerate(results):
        path = f"engine_results[{i}]"
        if not isinstance(result, dict):
            raise ValueError(f"{path}: phải là object")
        name = result.get("engine")
        _identifier(name, f"{path}.engine")
        if name not in weights or name in seen:
            raise ValueError(f"{path}.engine: thiếu trọng số hoặc trùng engine {name!r}")
        seen.add(name)
        if not isinstance(result.get("lines"), list):
            raise ValueError(f"{path}.lines: phải là danh sách")
        ids = set()
        for j, line in enumerate(result["lines"]):
            lp = f"{path}.lines[{j}]"
            if not isinstance(line, dict):
                raise ValueError(f"{lp}: phải là object")
            _identifier(line.get("line_id"), f"{lp}.line_id")
            if line["line_id"] in ids:
                raise ValueError(f"{lp}.line_id: trùng ID {line['line_id']!r}")
            ids.add(line["line_id"])
            if not isinstance(line.get("text"), str):
                raise ValueError(f"{lp}.text: phải là chuỗi")
            bbox = line.get("bbox")
            if bbox is not None:
                if not isinstance(bbox, (list, tuple)) or len(bbox) != 4:
                    raise ValueError(f"{lp}.bbox: cần [x0, y0, x1, y1]")
                for value in bbox:
                    _number(value, f"{lp}.bbox")
                x0, y0, x1, y1 = bbox
                if not (0 <= x0 < x1 and 0 <= y0 < y1):
                    raise ValueError(f"{lp}.bbox: tọa độ không hợp lệ")
            if line.get("confidence") is not None:
                _number(line["confidence"], f"{lp}.confidence")
    return {name: Decimal(str(value)) for name, value in weights.items()}


def _align(profile: list[dict], prior: list[str], engine: str, text: str,
           weights: dict[str, Decimal]) -> list[dict]:
    """Add one sequence to a profile using weighted sum-of-pairs edit costs.

    Ties use diagonal, consume profile, then consume new character. Previous
    columns keep their order; new columns contain GAP for all previous engines.
    """
    n, m = len(profile), len(text)
    total = sum((weights[e] for e in prior), Decimal(0))
    deletions = [sum((weights[e] for e in prior if col[e] is not None), Decimal(0))
                 for col in profile]
    back = [bytearray(m + 1) for _ in range(n + 1)]
    previous = [total * j for j in range(m + 1)]
    for j in range(1, m + 1):
        back[0][j] = 2
    for i, col in enumerate(profile, 1):
        current = [previous[0] + deletions[i - 1]]
        back[i][0] = 1
        for j, char in enumerate(text, 1):
            mismatch = sum((weights[e] for e in prior if col[e] != char), Decimal(0))
            costs = (previous[j - 1] + mismatch,
                     previous[j] + deletions[i - 1], current[j - 1] + total)
            move = min(range(3), key=costs.__getitem__)
            current.append(costs[move])
            back[i][j] = move
        previous = current
    aligned = []
    i, j = n, m
    while i or j:
        move = back[i][j]
        if move == 0:
            aligned.append({**profile[i - 1], engine: text[j - 1]})
            i, j = i - 1, j - 1
        elif move == 1:
            aligned.append({**profile[i - 1], engine: None})
            i -= 1
        else:
            aligned.append({**dict.fromkeys(prior), engine: text[j - 1]})
            j -= 1
    return aligned[::-1]


def _vote_line(line_id: str, sources: list[dict], weights: dict[str, Decimal],
               total: Decimal) -> dict:
    active = [s for s in sources if weights[s["engine"]] > 0]
    participants = [s["engine"] for s in active]
    present = sum((weights[e] for e in participants), Decimal(0))
    profile, prior = [], []
    for source in active:
        engine = source["engine"]
        text = unicodedata.normalize("NFC", source["text"])
        if not prior:
            profile = [{engine: char} for char in text]
        else:
            profile = _align(profile, prior, engine, text, weights)
        prior.append(engine)
    columns, output, shares = [], [], []
    reasons = set()
    for index, column in enumerate(profile):
        supporters = {}
        for engine in participants:
            supporters.setdefault(column[engine], []).append(engine)
        candidates = []
        for token, engines in supporters.items():
            score = sum((weights[e] for e in engines), Decimal(0))
            # engines already follow descending weight, then ascending name.
            candidates.append((token, score, engines))
        candidates.sort(key=lambda c: (-c[1], -weights[c[2][0]], c[2][0]))
        winner, score, _ = candidates[0]
        tie = len(candidates) > 1 and candidates[1][1] == score
        share = score / present
        shares.append(share)
        if len(candidates) > 1:
            reasons.add("disagreement")
        if tie:
            reasons.add("tie")
        output_index = None
        if winner is not None:
            output_index = len(output)
            output.append(winner)
        columns.append({"alignment_index": index, "output_index": output_index,
                        "selected": winner, "support": float(share), "tie": tie,
                        "candidates": [{"token": token, "weight": _json_weight(weight),
                                        "engines": engines}
                                       for token, weight, engines in candidates]})
    if present < total:
        reasons.add("incomplete_coverage")
    if len(active) < 2:
        reasons.add("single_engine")
    if not output:
        reasons.add("empty_text")
    bbox_source = next((s for s in active if s.get("bbox") is not None), None)
    return {"line_id": line_id, "text": "".join(output),
            "bbox": list(bbox_source["bbox"]) if bbox_source else None,
            "bbox_engine": bbox_source["engine"] if bbox_source else None,
            "sources": sources, "columns": columns,
            "agreement": float(sum(shares) / len(shares)) if shares else None,
            "coverage": float(present / total), "needs_review": bool(reasons),
            "review_reasons": sorted(reasons)}


def vote_page(payload: dict) -> dict:
    """Vote one page; leave input untouched. Raise ValueError on invalid input.

    Confidence is source metadata, with its original engine-specific scale.
    Missing lines abstain; explicit empty strings vote GAP. Line IDs are sorted
    lexicographically (use zero-padded IDs to preserve numeric reading order).
    """
    weights = _validate(payload)
    # Enough precision for exact addition across all configured decimal weights;
    # ratios are rounded only when serialized as JSON numbers.
    values = list(weights.values())
    precision = max(28, max(w.adjusted() for w in values)
                    - min(w.as_tuple().exponent for w in values)
                    + len(str(len(values))) + 10)
    with localcontext() as ctx:
        ctx.prec = precision
        order = sorted(weights, key=lambda e: (-weights[e], e))
        results = {r["engine"]: r["lines"] for r in payload["engine_results"]}
        grouped = {}
        # Zero-weight sources may be retained as provenance for an active line,
        # but cannot introduce lines or affect output ordering.
        ids = {line["line_id"] for e in order if weights[e] > 0
               for line in results.get(e, [])}
        for engine in order:
            for line in results.get(engine, []):
                if line["line_id"] in ids:
                    grouped.setdefault(line["line_id"], []).append(
                        {**deepcopy(line), "engine": engine})
        total = sum(values, Decimal(0))
        lines = [_vote_line(lid, grouped[lid], weights, total) for lid in sorted(ids)]
    reasons = sorted({reason for line in lines for reason in line["review_reasons"]})
    if not lines:
        reasons = ["empty_page"]
    return {"schema_version": SCHEMA_VERSION, "algorithm_version": ALGORITHM_VERSION,
            "page_id": payload["page_id"], "method": "weighted_character_voting",
            "engine_weights": deepcopy(payload["engine_weights"]), "lines": lines,
            "needs_review": bool(reasons), "review_reasons": reasons}


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"JSON: trùng key {key!r}")
        result[key] = value
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--force", action="store_true", help="Cho phép ghi đè output")
    args = parser.parse_args(argv)
    temporary = None
    try:
        if args.input.resolve() == args.output.resolve():
            raise ValueError("--output phải khác --input")
        if args.output.exists() and not args.force:
            raise ValueError("Output đã tồn tại; dùng --force để ghi đè")
        payload = json.loads(args.input.read_text(encoding="utf-8"),
                             object_pairs_hook=_unique_object)
        result = vote_page(payload)
        content = json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", newline="\n",
                                         dir=args.output.parent, suffix=".tmp", delete=False) as f:
            temporary = Path(f.name)
            f.write(content)
        if args.force:
            os.replace(temporary, args.output)
        elif os.name == "nt":
            # Windows rename is atomic and refuses to replace an existing file.
            os.rename(temporary, args.output)
        else:
            # Atomic publish without replacing a concurrently created output.
            os.link(temporary, args.output)
        return 0
    except (OSError, ValueError) as exc:
        print(f"Weighted voting: {exc}", file=sys.stderr)
        return 1
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


if __name__ == "__main__":
    raise SystemExit(main())
