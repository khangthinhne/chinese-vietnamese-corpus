"""Sequential section segmentation inside one confirmed document.

The state machine is driven by canonical markers detected *before* this stage.
It never creates document boundaries; a CJK block after a Vietnamese section is
handled by :mod:`src.segmentation.boundaries` first.

Missing markers are inferred conservatively from strong evidence (a title-like
Vietnamese line after a Hán block / after enough phiên âm content) and recorded
as ``inferred_marker`` headings requiring review.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

from src.segmentation.documents import review_row
from src.segmentation.headings import (HeadingConfig, is_subwork_label, is_title_like,
                                       is_top_level_numbered_heading,
                                       is_unknown_uppercase_heading, title_text)
from src.segmentation.markers import (AUXILIARY_SECTIONS, CANONICAL, MAIN_SECTIONS,
                                      MarkerConfig, MarkerMatch, VIETNAMESE_SECTIONS,
                                      canonicalize_marker)
from src.segmentation.model import Document, LayerLine, OcrLine

# Section state -> output layer.  Auxiliary sections and unclassified lines are
# kept in a separate ``other`` layer so they are never appended to a main
# section (spec 9.3).  Front matter is handled by the refactor orchestrator.
DEFAULT_SECTION_LAYER_MAP = {
    "han": "han",
    "phien_am": "am",
    "dich_nghia": "nghia",
    "dich_tho": "tho",
    "khao_di": "other",
    "chu_thich": "other",
    "nguyen_chu": "other",
    "other": "other",
    "front_matter": "other",
}


def section_layer_map(seg_cfg: dict | None = None) -> dict[str, str]:
    mapping = dict(DEFAULT_SECTION_LAYER_MAP)
    for section, layer in ((seg_cfg or {}).get("section_layers") or {}).items():
        mapping[str(section)] = str(layer)
    return mapping


def _marker_from_line(line: OcrLine, cfg: MarkerConfig) -> MarkerMatch | None:
    if line.marker:
        return MarkerMatch(line.marker, CANONICAL.get(line.marker, line.marker),
                           line.marker_raw or line.text, "preprocessed", 100.0, "unchanged")
    return canonicalize_marker(line.text, cfg)


def _subwork_index(text: str) -> int | None:
    import re
    from src.segmentation.headings import squash, strip_number_prefix

    probe = strip_number_prefix(text).strip()
    key = squash(probe)
    han_numerals = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5,
                    "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}
    match = re.search(r"其([一二三四五六七八九十]+)", probe)
    if match:
        numerals = match.group(1)
        if numerals in han_numerals:
            return han_numerals[numerals]
        if numerals == "十一":
            return 11
        if numerals.startswith("十") and numerals[1:] in han_numerals:
            return 10 + han_numerals[numerals[1:]]
    vietnamese = {"nhat": 1, "nhi": 2, "mot": 1, "hai": 2, "ba": 3, "bon": 4}
    if key.startswith("ky"):
        rest = key[2:]
        if rest.isdigit():
            return int(rest)
        if rest in vietnamese:
            return vietnamese[rest]
    if key.startswith("bai") and key[3:].isdigit():
        return int(key[3:])
    return None


@dataclass
class TimelineEntry:
    line: OcrLine
    state_before: str
    state_after: str
    event: str                       # marker | inferred_marker | subwork | content
    section: str = ""
    match: MarkerMatch | None = None
    is_title: bool = False
    review: str = ""


def section_timeline(lines: list[OcrLine], marker_cfg: MarkerConfig | None = None,
                     heading_cfg: HeadingConfig | None = None,
                     document_heading_ids: set[str] | None = None) -> list[TimelineEntry]:
    marker_cfg = marker_cfg or MarkerConfig()
    heading_cfg = heading_cfg or HeadingConfig()
    state = "han"
    han_run = 0
    section_run = 0
    title_pending = False
    title_lines = 0
    entries: list[TimelineEntry] = []

    for line in lines:
        before = state
        if document_heading_ids and line.source_id in document_heading_ids:
            entries.append(TimelineEntry(line, before, state, "document_heading", state,
                                         is_title=True))
            continue
        match = _marker_from_line(line, marker_cfg)
        if match is not None and match.status != "pending_review":
            state = match.section
            title_pending = state in MAIN_SECTIONS and state != "han"
            title_lines = 0
            section_run = 0
            if state == "han":
                han_run = 0
            entries.append(TimelineEntry(line, before, state, "marker", state, match))
            continue

        if is_subwork_label(line.text, heading_cfg):
            if line.script_label == "han":
                state = "han"
                han_run = 0
            title_pending = False
            title_lines = 0
            entries.append(TimelineEntry(line, before, state, "subwork", state))
            continue

        if (state == "han" and line.script_label == "vie"
                and is_title_like(line.text, heading_cfg) and han_run >= 2):
            state = "phien_am"
            title_pending = True
            title_lines = 1
            section_run = 0
            entry = TimelineEntry(line, before, state, "inferred_marker", state,
                                  review="missing_marker:phien_am")
            entry.is_title = True
            entries.append(entry)
            continue

        if (state == "phien_am" and line.script_label == "vie"
                and is_title_like(line.text, heading_cfg) and section_run >= 4
                and not title_pending):
            state = "dich_nghia"
            title_pending = True
            title_lines = 1
            section_run = 0
            entry = TimelineEntry(line, before, state, "inferred_marker", state,
                                  review="missing_marker:dich_nghia")
            entry.is_title = True
            entries.append(entry)
            continue

        is_title = False
        if title_pending and is_title_like(line.text, heading_cfg) and title_lines < heading_cfg.title_max_lines:
            is_title = True
            title_lines += 1
        else:
            title_pending = False
            if state in VIETNAMESE_SECTIONS:
                section_run += 1
            if state == "han":
                han_run += 1
        entries.append(TimelineEntry(line, before, state, "content", state, is_title=is_title))
    return entries


@dataclass
class SectionAssignment:
    lines: dict[str, list[LayerLine]] = field(default_factory=dict)
    headings: list[dict] = field(default_factory=list)
    subworks: list[dict] = field(default_factory=list)
    review: list[dict] = field(default_factory=list)
    title: str = ""
    han_title: str = ""
    section_counts: dict[str, int] = field(default_factory=dict)
    layer_counts: dict[str, int] = field(default_factory=dict)
    marker_sequence: list[dict] = field(default_factory=list)
    missing_sections: list[str] = field(default_factory=list)
    started_sections: list[str] = field(default_factory=list)


def _heading(work_id: str, doc: Document, line: OcrLine, layer: str, kind: str,
             text: str | None = None, **extra) -> dict:
    row = {"work_id": work_id, "document_id": doc.id, "layer": layer, "kind": kind,
           "text": text if text is not None else line.text, "page": line.page,
           "bbox": line.bbox, "writing_system": line.writing_system,
           "source_ids": [line.source_id]}
    row.update(extra)
    return row


def assign_document_sections(work_id: str, doc: Document,
                             marker_cfg: MarkerConfig | None = None,
                             heading_cfg: HeadingConfig | None = None,
                             layer_map: dict[str, str] | None = None,
                             document_heading_ids: set[str] | None = None) -> SectionAssignment:
    marker_cfg = marker_cfg or MarkerConfig()
    heading_cfg = heading_cfg or HeadingConfig()
    layer_map = layer_map or DEFAULT_SECTION_LAYER_MAP
    lines = doc.all_lines
    result = SectionAssignment()
    grouped: dict[str, list[OcrLine]] = {}
    section_totals: Counter[str] = Counter()
    review: list[dict] = []
    headings: list[dict] = []
    subworks: list[dict] = []
    started: list[str] = []
    current_subwork = ""
    current_subwork_index = 0
    subwork_counter = 0
    marker_sequence: list[dict] = []

    for entry in section_timeline(lines, marker_cfg, heading_cfg,
                                  document_heading_ids):
        line = entry.line
        state = entry.state_after
        if entry.event == "document_heading":
            headings.append(_heading(work_id, doc, line, state, "document_title",
                                     text=title_text(line.text, heading_cfg)))
            if not result.title:
                result.title = title_text(line.text, heading_cfg)
            if line.script_label == "han" and not result.han_title:
                result.han_title = title_text(line.text, heading_cfg)
            line.document_id = doc.id
            line.section = "document_heading"
            continue
        if entry.event in ("marker", "inferred_marker"):
            match = entry.match
            synthetic = entry.event == "inferred_marker"
            if synthetic:
                headings.append(_heading(work_id, doc, line, state, "inferred_marker",
                                         canonical=CANONICAL.get(state, state),
                                         synthetic=True, review_status="pending_review",
                                         reason=entry.review))
                review.append(review_row(work_id, doc.id, state, "missing_marker",
                                         [line.page], line.text, [line.source_id]))
            else:
                headings.append(_heading(work_id, doc, line, state, "marker",
                                         canonical=match.canonical if match else line.text,
                                         marker_raw=line.marker_raw or line.original_text,
                                         correction_status=(line.correction or {}).get("status", ""),
                                         review_status=("pending_review"
                                                        if (line.correction or {}).get("status") == "corrected"
                                                        else "not_required")))
            if state not in started:
                started.append(state)
            marker_sequence.append({"section": state, "line_id": line.source_id,
                                    "page": line.page, "synthetic": synthetic,
                                    "status": "inferred" if synthetic else "recognized"})
            if entry.is_title:
                headings.append(_heading(work_id, doc, line, state, "section_title",
                                         text=title_text(line.text, heading_cfg)))
                if not result.title:
                    result.title = title_text(line.text, heading_cfg)
            line.document_id = doc.id
            line.section = state
            continue

        if entry.event == "subwork":
            index = _subwork_index(line.text)
            if index is not None:
                current_subwork_index = index
            else:
                subwork_counter += 1
                current_subwork_index = subwork_counter
            subwork_counter = max(subwork_counter, current_subwork_index)
            current_subwork = f"{doc.id}.SW{current_subwork_index:02d}"
            subworks.append({"subwork_id": current_subwork, "document_id": doc.id,
                             "text": line.text, "line_id": line.source_id,
                             "page": line.page, "index": current_subwork_index,
                             "status": "detected"})
            headings.append(_heading(work_id, doc, line, state, "subwork",
                                     subwork_id=current_subwork))
            if line.script_label == "han" and not result.han_title:
                result.han_title = title_text(line.text, heading_cfg)
            line.document_id = doc.id
            line.section = state
            line.subwork_id = current_subwork
            continue

        if entry.is_title:
            headings.append(_heading(work_id, doc, line, state, "section_title",
                                     text=title_text(line.text, heading_cfg)))
            if not result.title:
                result.title = title_text(line.text, heading_cfg)
            line.document_id = doc.id
            line.section = state
            continue

        if (is_unknown_uppercase_heading(line.text, heading_cfg)
                and not line.marker):
            headings.append(_heading(work_id, doc, line, state, "other_heading",
                                     review_status="pending_review"))
            review.append(review_row(work_id, doc.id, state, "unknown_heading",
                                     [line.page], line.text, [line.source_id]))
            line.document_id = doc.id
            line.section = state
            continue

        script = line.script_label or "unknown"
        flags: list[str] = []
        target = state
        if state == "han":
            if script == "han":
                target = "han"
            elif script == "vie":
                target = "han"
                flags.append("script_mismatch:vie_in_han")
                review.append(review_row(work_id, doc.id, "han", "script_mismatch",
                                         [line.page], line.text, [line.source_id]))
            else:
                target = "other"
                flags.append(f"script_mismatch:{script}")
                review.append(review_row(work_id, doc.id, "other", "script_mismatch",
                                         [line.page], line.text, [line.source_id]))
        elif state in VIETNAMESE_SECTIONS:
            if script == "vie":
                target = state
            elif script == "han":
                target = "other"
                flags.append("script_mismatch:han_in_vie")
                review.append(review_row(work_id, doc.id, state, "script_mismatch",
                                         [line.page], line.text, [line.source_id]))
            else:
                target = "other"
                flags.append(f"script_mismatch:{script}")
                review.append(review_row(work_id, doc.id, state, "script_mismatch",
                                         [line.page], line.text, [line.source_id]))
        elif state in AUXILIARY_SECTIONS:
            if script == "han":
                target = "han"
                flags.append("resumed_han_after_auxiliary")
                review.append(review_row(work_id, doc.id, "han",
                                         "resumed_han_after_auxiliary", [line.page],
                                         line.text, [line.source_id]))
            else:
                target = state
        else:
            target = "other"

        if current_subwork:
            flags.append(f"subwork:{current_subwork}")
        line.document_id = doc.id
        line.section = target
        line.subwork_id = current_subwork
        grouped.setdefault(target, []).append(line)
        section_totals[target] += 1
        if target not in started:
            started.append(target)

    # Build layer lines, preserving reading order.  Sections are mapped to
    # output layers; auxiliary/unclassified sections share ``other``.
    by_layer: dict[str, list[OcrLine]] = {}
    for section, items in grouped.items():
        layer = layer_map.get(section, "other")
        by_layer.setdefault(layer, []).extend(items)
    for layer, items in by_layer.items():
        prefix = "S" if layer == "han" else "L"
        ordered = sorted(items, key=lambda x: (x.page, x.index))
        rows: list[LayerLine] = []
        for seq, line in enumerate(ordered, 1):
            flags = [f for f in line.review_flags if f.startswith("script_review:")]
            if line.section == "han" and (line.script_label or "") == "vie":
                flags.append("script_mismatch:vie_in_han")
            if line.subwork_id:
                flags.append(f"subwork:{line.subwork_id}")
            rows.append(LayerLine(
                id=f"{doc.id}.P{line.page:04d}.{prefix}{seq:03d}",
                document_id=doc.id, layer=layer, page=line.page, text=line.text,
                bbox=line.bbox, confidence=line.confidence,
                writing_system=line.writing_system, source_ids=[line.source_id],
                flags=list(dict.fromkeys(flags))))
        result.lines[layer] = rows
        result.layer_counts[layer] = len(rows)
    result.section_counts = dict(section_totals)

    # Missing / empty section diagnostics.
    for section in ("han", "phien_am", "dich_nghia"):
        if section not in started:
            result.missing_sections.append(section)
            review.append(review_row(work_id, doc.id, section, "missing_section",
                                     doc.pages, ""))
        elif not grouped.get(section):
            review.append(review_row(work_id, doc.id, section, "empty_section",
                                     doc.pages, ""))
    if "dich_tho" not in started:
        result.missing_sections.append("dich_tho")
    if "han" not in started and any(s in started for s in VIETNAMESE_SECTIONS):
        review.append(review_row(work_id, doc.id, "han", "missing_han_section",
                                 doc.pages, ""))

    # Marker order anomaly: dich_nghia/dich_tho before phien_am.
    order = [m["section"] for m in marker_sequence]
    if "phien_am" in order:
        phi = order.index("phien_am")
        for later in ("dich_nghia", "dich_tho"):
            if later in order and order.index(later) < phi:
                review.append(review_row(work_id, doc.id, later, "marker_order_anomaly",
                                         doc.pages, later))

    result.headings = headings
    result.subworks = subworks
    result.review = review
    result.started_sections = started
    result.marker_sequence = marker_sequence
    if not result.title and doc.heading:
        result.title = title_text(doc.heading.get("text", ""), heading_cfg)
    return result
