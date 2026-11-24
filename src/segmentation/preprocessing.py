"""OCR text normalization and conservative pre-segmentation cleaning.

This stage runs on the unified OCR stream before any structural segmentation:

1. Unicode normalization (NFC) and whitespace normalization.
2. Known marker OCR correction (delegated to :mod:`src.segmentation.markers`).
3. Detection of empty lines, running headers/footers, page-number candidates and
   duplicate candidates.  Nothing is removed without an explicit record.
4. Line-level Unicode script classification.

The original OCR text is kept in ``OcrLine.original_text``; only
``OcrLine.text`` is derived.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field
import re
import unicodedata

from src.segmentation.headings import HeadingConfig, is_unknown_uppercase_heading, squash
from src.segmentation.markers import MarkerConfig, canonicalize_marker
from src.segmentation.model import OcrLine
from src.segmentation.script import ScriptConfig, classify

_NUMERIC_ONLY = re.compile(r"^[\s\-–—]*(\d{1,4})[\s\-–—]*$")
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def normalize_ocr_text(text: str, collapse_whitespace: bool = True) -> str:
    text = unicodedata.normalize("NFC", text)
    text = _CONTROL.sub("", text)
    if collapse_whitespace:
        text = " ".join(text.split())
    else:
        text = text.strip()
    return text


@dataclass
class PreprocessConfig:
    markers: MarkerConfig = field(default_factory=MarkerConfig)
    script: ScriptConfig = field(default_factory=ScriptConfig)
    headings: HeadingConfig = field(default_factory=HeadingConfig)
    running_headers: list[str] = field(default_factory=list)
    running_footers: list[str] = field(default_factory=list)
    header_footer_min_pages: int = 3
    detect_page_numbers: bool = True
    page_number_max_offset: int = 8
    page_number_min_pages: int = 3
    detect_duplicates: bool = True
    duplicate_min_len: int = 12
    collapse_whitespace: bool = True

    @classmethod
    def from_config(cls, seg_cfg: dict | None) -> "PreprocessConfig":
        seg_cfg = seg_cfg or {}
        cleaning = seg_cfg.get("cleaning") or {}
        obj = cls(
            markers=MarkerConfig.from_config(seg_cfg),
            script=ScriptConfig.from_config(seg_cfg.get("script_classification")),
            headings=HeadingConfig.from_config(seg_cfg.get("headings")),
        )
        obj.running_headers = [str(x) for x in cleaning.get("running_headers", [])]
        obj.running_footers = [str(x) for x in cleaning.get("running_footers", [])]
        obj.header_footer_min_pages = int(cleaning.get("header_footer_min_pages", 3))
        obj.detect_page_numbers = bool(cleaning.get("detect_page_numbers", True))
        obj.page_number_max_offset = int(cleaning.get("page_number_max_offset", 8))
        obj.page_number_min_pages = int(cleaning.get("page_number_min_pages", 3))
        obj.detect_duplicates = bool(cleaning.get("detect_duplicates", True))
        obj.duplicate_min_len = int(cleaning.get("duplicate_min_len", 12))
        obj.collapse_whitespace = bool(cleaning.get("collapse_whitespace", True))
        return obj


@dataclass
class PreprocessResult:
    lines: list[OcrLine]
    all_lines: list[OcrLine]
    cleaning_log: list[dict]
    correction_log: list[dict]
    stats: dict


def _numeric_value(text: str) -> int | None:
    match = _NUMERIC_ONLY.match(text)
    return int(match.group(1)) if match else None


def _matches_configured(text: str, configured: list[str]) -> str:
    key = squash(text)
    if not key:
        return ""
    for item in configured:
        target = squash(item)
        if target and (key == target or key.startswith(target) or target in key):
            return item
    return ""


def _log_row(line: OcrLine, action: str, reason: str = "", rule: str = "",
             status: str = "") -> dict:
    return {
        "line_id": line.source_id,
        "page": line.page,
        "order": line.index,
        "original_text": line.original_text,
        "corrected_text": line.text,
        "action": action,
        "reason": reason,
        "rule": rule,
        "status": status or action,
        "correction_status": line.correction.get("status", ""),
    }


def _page_layout(lines: list[OcrLine]) -> dict[int, list[OcrLine]]:
    pages: dict[int, list[OcrLine]] = defaultdict(list)
    for line in lines:
        pages[line.page].append(line)
    for items in pages.values():
        items.sort(key=lambda x: x.index)
    return pages


def _detect_repeated_edges(pages: dict[int, list[OcrLine]], cfg: PreprocessConfig
                           ) -> tuple[dict[str, str], dict[str, str], list[dict]]:
    """Return {line_id: reason} for headers/footers and review flags."""
    header_ids: dict[str, str] = {}
    footer_ids: dict[str, str] = {}
    records: list[dict] = []

    first_counts: Counter[str] = Counter()
    last_counts: Counter[str] = Counter()
    first_lines: dict[str, list[OcrLine]] = defaultdict(list)
    last_lines: dict[str, list[OcrLine]] = defaultdict(list)
    for page, items in pages.items():
        non_empty = [x for x in items if x.text]
        if not non_empty:
            continue
        first, last = non_empty[0], non_empty[-1]
        first_key, last_key = squash(first.text), squash(last.text)
        if first_key:
            first_counts[first_key] += 1
            first_lines[first_key].append(first)
        if last_key and last_key != first_key:
            last_counts[last_key] += 1
            last_lines[last_key].append(last)

    for key, count in first_counts.items():
        samples = first_lines[key]
        configured = _matches_configured(samples[0].text, cfg.running_headers)
        if configured or (count >= cfg.header_footer_min_pages and not _numeric_value(samples[0].text)):
            for line in samples:
                header_ids[line.source_id] = "running_header"
                records.append({"line": line, "reason": "running_header",
                                "rule": "configured_header" if configured else "repeated_first_line",
                                "count": count})
    for key, count in last_counts.items():
        samples = last_lines[key]
        configured = _matches_configured(samples[0].text, cfg.running_footers)
        if configured or (count >= cfg.header_footer_min_pages and not _numeric_value(samples[0].text)):
            for line in samples:
                footer_ids[line.source_id] = "running_footer"
                records.append({"line": line, "reason": "running_footer",
                                "rule": "configured_footer" if configured else "repeated_last_line",
                                "count": count})
    return header_ids, footer_ids, records


def _detect_page_numbers(pages: dict[int, list[OcrLine]], cfg: PreprocessConfig,
                         header_ids: dict[str, str]
                         ) -> tuple[dict[str, str], list[dict], list[dict]]:
    """Confirm numeric-only page numbers only when a consistent offset repeats."""
    confirmed: dict[str, str] = {}
    records: list[dict] = []
    candidates: list[dict] = []
    for page, items in pages.items():
        non_empty = [x for x in items if x.text and x.source_id not in header_ids]
        if not non_empty:
            continue
        # Numeric-only line at the bottom (or top if no header) of the page.
        positions = [("last", non_empty[-1])]
        if non_empty[0].source_id not in header_ids:
            positions.append(("first", non_empty[0]))
        for position, line in positions:
            value = _numeric_value(line.text)
            if value is None:
                continue
            candidates.append({"line": line, "position": position,
                               "value": value, "offset": value - page})
            break

    offsets = Counter(c["offset"] for c in candidates)
    mode_offset, mode_count = (offsets.most_common(1)[0] if offsets else (None, 0))
    for cand in candidates:
        line = cand["line"]
        confirmed_number = (
            cand["offset"] == mode_offset and mode_count >= cfg.page_number_min_pages
        ) or (cand["value"] == line.page and offsets[cand["offset"]] >= 2)
        if confirmed_number and cfg.detect_page_numbers:
            confirmed[line.source_id] = "page_number"
            records.append({"line": line, "reason": "page_number",
                            "rule": f"repeated_offset:{cand['offset']}",
                            "count": offsets[cand["offset"]]})
        else:
            records.append({"line": line, "reason": "page_number_candidate",
                            "rule": "unconfirmed_numeric_line", "count": 1,
                            "review": True})
    return confirmed, records, candidates


def preprocess_stream(work_id: str, raw_lines: list[OcrLine],
                      cfg: PreprocessConfig | None = None) -> PreprocessResult:
    cfg = cfg or PreprocessConfig()
    all_lines: list[OcrLine] = []
    cleaning_log: list[dict] = []
    correction_log: list[dict] = []

    # 1. Normalize + marker correction + script classification.
    for raw in raw_lines:
        line = OcrLine(
            page=raw.page, index=raw.index, text=raw.text, bbox=raw.bbox,
            confidence=raw.confidence, source_id=raw.source_id,
            writing_system=raw.writing_system, engine=raw.engine,
            cache_key=raw.cache_key, image=raw.image, original_text=raw.original_text or raw.text,
        )
        normalized = normalize_ocr_text(line.text, cfg.collapse_whitespace)
        line.text = normalized
        if normalized != line.original_text:
            line.correction = {"status": "normalized", "method": "unicode_whitespace",
                               "original": line.original_text, "corrected": normalized}
        match = canonicalize_marker(normalized, cfg.markers)
        if match is not None:
            line.marker_raw = normalized
            if match.status in ("unchanged", "corrected"):
                line.marker = match.section
                line.text = match.canonical
                line.correction = {
                    "status": match.status, "method": match.method,
                    "original": line.original_text, "corrected": match.canonical,
                    "canonical": match.canonical, "matched_alias": match.matched_alias,
                    "score": match.score,
                }
                correction_log.append({
                    "line_id": line.source_id, "page": line.page, "order": line.index,
                    "original_text": line.original_text, "corrected_text": match.canonical,
                    "canonical": match.canonical, "matched_alias": match.matched_alias,
                    "method": match.method, "score": match.score, "status": match.status,
                })
            else:
                line.review_flags.append("marker_ambiguous")
                correction_log.append({
                    "line_id": line.source_id, "page": line.page, "order": line.index,
                    "original_text": line.original_text, "corrected_text": line.text,
                    "canonical": "", "matched_alias": match.matched_alias,
                    "method": match.method, "score": match.score,
                    "status": "pending_review", "ambiguous": match.ambiguous,
                })
        line.script = classify(line.text, cfg.script)
        if line.script.review:
            line.review_flags.append(f"script_review:{line.script.review_reason}")
        if (match is None and is_unknown_uppercase_heading(line.text, cfg.headings)):
            line.review_flags.append("unknown_heading")
        all_lines.append(line)

    # 2. Empty lines and page layout.
    non_empty: list[OcrLine] = []
    for line in all_lines:
        if not line.text:
            line.cleaning = {"action": "excluded", "reason": "empty", "rule": "empty_line",
                             "status": "excluded"}
            cleaning_log.append(_log_row(line, "excluded", "empty", "empty_line", "excluded"))
            continue
        non_empty.append(line)
    pages = _page_layout(non_empty)

    # 3. Running headers/footers.
    header_ids, footer_ids, edge_records = _detect_repeated_edges(pages, cfg)

    # 4. Page numbers (only after headers are known).
    page_ids, page_records, _ = _detect_page_numbers(pages, cfg, header_ids)

    # 5. Duplicate candidates (flagged, never removed).
    duplicate_ids: dict[str, str] = {}
    if cfg.detect_duplicates:
        previous: OcrLine | None = None
        for line in non_empty:
            if previous is not None:
                key, prev_key = squash(line.text), squash(previous.text)
                if key and key == prev_key and len(key) >= cfg.duplicate_min_len:
                    duplicate_ids[line.source_id] = "duplicate_candidate"
            previous = line

    excluded = {**header_ids, **footer_ids, **page_ids}
    edge_by_id = {r["line"].source_id: r for r in edge_records}
    kept: list[OcrLine] = []
    for line in non_empty:
        if line.source_id in excluded:
            reason = excluded[line.source_id]
            record = edge_by_id.get(line.source_id, {})
            rule = record.get("rule", reason)
            line.cleaning = {"action": "excluded", "reason": reason, "rule": rule,
                             "status": "excluded"}
            cleaning_log.append(_log_row(line, "excluded", reason, rule, "excluded"))
            continue
        reasons: list[str] = []
        if line.source_id in duplicate_ids:
            reasons.append(duplicate_ids[line.source_id])
        if line.source_id in edge_by_id and edge_by_id[line.source_id].get("review"):
            reasons.append(edge_by_id[line.source_id]["reason"])
        if line.review_flags:
            reasons.extend(line.review_flags)
        if reasons:
            line.cleaning = {"action": "flagged", "reason": ",".join(dict.fromkeys(reasons)),
                             "rule": "preprocessing_review", "status": "pending_review"}
            cleaning_log.append(_log_row(line, "flagged", line.cleaning["reason"],
                                         "preprocessing_review", "pending_review"))
        else:
            line.cleaning = {"action": "kept", "reason": "", "rule": "", "status": "kept"}
            cleaning_log.append(_log_row(line, "kept"))
        kept.append(line)

    script_counts = Counter(line.script_label or "unknown" for line in all_lines)
    stats = {
        "work_id": work_id,
        "raw_lines": len(raw_lines),
        "kept_lines": len(kept),
        "excluded_lines": len(raw_lines) - len(kept),
        "empty_lines": sum(1 for r in cleaning_log if r["reason"] == "empty"),
        "running_headers": sum(1 for r in cleaning_log if r["reason"] == "running_header"),
        "running_footers": sum(1 for r in cleaning_log if r["reason"] == "running_footer"),
        "page_numbers": sum(1 for r in cleaning_log if r["reason"] == "page_number"),
        "page_number_candidates": sum(1 for r in page_records if r.get("review")),
        "duplicate_candidates": len(duplicate_ids),
        "corrected_markers": sum(1 for r in correction_log if r["status"] == "corrected"),
        "ambiguous_markers": sum(1 for r in correction_log if r["status"] == "pending_review"),
        "unknown_headings": sum(1 for line in all_lines if "unknown_heading" in line.review_flags),
        "script_classes": dict(sorted(script_counts.items())),
        "flagged_lines": sum(1 for r in cleaning_log if r["action"] == "flagged"),
    }
    return PreprocessResult(kept, all_lines, cleaning_log, correction_log, stats)
