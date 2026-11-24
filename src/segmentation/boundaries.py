"""Document boundary detection on the cleaned, ordered unified stream.

Boundaries are *candidates*: a CJK block after a Vietnamese section is only one
signal.  Strong heading evidence (a top-level numbered heading, a configured
title pattern) is required before a boundary is auto-accepted; script-only
transitions are recorded for review and do not split the stream by default.

Sub-work labels (``其一`` / ``其二`` / ``KỲ NHẤT`` / ``KỲ NHỊ`` / ``BÀI n``) are
never top-level boundaries.  A Hán sub-work label switches the section state
back to ``han`` inside the current document.

Nothing is dropped: every line belongs to exactly one span (front matter or a
work), and rejected/pending candidates keep their lines in the current span.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import re

from src.segmentation.documents import review_row
from src.segmentation.headings import (HeadingConfig, is_subwork_label,
                                       is_top_level_numbered_heading, nfc,
                                       title_text)
from src.segmentation.markers import (CANONICAL, MarkerConfig, MarkerMatch,
                                      VIETNAMESE_SECTIONS, canonicalize_marker)
from src.segmentation.model import Document, OcrLine


@dataclass
class BoundaryConfig:
    split_on_vie_to_han_after_vietnamese_section: bool = True
    require_heading_evidence_for_auto_accept: bool = True
    review_ambiguous_boundaries: bool = True
    detect_same_script_boundaries: bool = True
    same_script_require_completed_sections: bool = True
    boundary_title_patterns: list[str] = field(default_factory=list)
    capture_front_matter: bool = True
    front_matter_section: str = "front_matter"
    preface_document_index: int = 0

    @classmethod
    def from_config(cls, seg_cfg: dict | None) -> "BoundaryConfig":
        seg_cfg = seg_cfg or {}
        raw = seg_cfg.get("document_boundary") or {}
        obj = cls()
        for key in ("split_on_vie_to_han_after_vietnamese_section",
                    "require_heading_evidence_for_auto_accept",
                    "review_ambiguous_boundaries",
                    "detect_same_script_boundaries",
                    "same_script_require_completed_sections",
                    "capture_front_matter"):
            if key in raw:
                setattr(obj, key, bool(raw[key]))
        if raw.get("boundary_title_patterns"):
            obj.boundary_title_patterns = [str(p) for p in raw["boundary_title_patterns"]]
        if raw.get("front_matter_section"):
            obj.front_matter_section = str(raw["front_matter_section"])
        if "preface_document_index" in raw:
            obj.preface_document_index = int(raw["preface_document_index"])
        return obj


@dataclass
class BoundaryCandidate:
    boundary_id: str
    following_line_id: str
    preceding_line_id: str
    page: int
    title_candidate: str
    evidence: list[str]
    status: str                       # pending_review | accepted | rejected | auto_accepted
    decision_source: str              # rule | config | human
    start_index: int | None = None    # index applied as a split, None if not applied
    note: str = ""
    script_transition: str = ""

    def to_dict(self) -> dict:
        return {
            "boundary_id": self.boundary_id,
            "preceding_line_id": self.preceding_line_id,
            "following_line_id": self.following_line_id,
            "page": self.page,
            "title_candidate": self.title_candidate,
            "evidence": list(self.evidence),
            "status": self.status,
            "decision_source": self.decision_source,
            "start_index": self.start_index,
            "applied": self.start_index is not None,
            "note": self.note,
            "script_transition": self.script_transition,
        }


@dataclass
class BoundarySpan:
    document_id: str
    kind: str                          # front_matter | work
    start: int
    end: int                           # exclusive
    pages: list[int]
    lines: list[OcrLine]
    boundary_status: str = ""
    evidence: list[str] = field(default_factory=list)
    title_candidate: str = ""
    heading_line: OcrLine | None = None
    section_events: list[dict] = field(default_factory=list)


@dataclass
class BoundaryResult:
    spans: list[BoundarySpan]
    candidates: list[BoundaryCandidate]
    review: list[dict]
    front_matter: BoundarySpan | None = None
    stats: dict = field(default_factory=dict)


def _marker_match(line: OcrLine, cfg: MarkerConfig) -> MarkerMatch | None:
    if line.marker:
        return MarkerMatch(line.marker, CANONICAL.get(line.marker, line.marker),
                           line.marker_raw or line.text, "preprocessed", 100.0, "unchanged")
    return canonicalize_marker(line.text, cfg)


def _matches_patterns(text: str, patterns: list[str]) -> bool:
    probe = nfc(text).strip()
    for pattern in patterns:
        try:
            if re.search(pattern, probe, re.IGNORECASE):
                return True
        except re.error:
            continue
    return False


def _first_han_index(lines: list[OcrLine]) -> int | None:
    for index, line in enumerate(lines):
        if line.script_label == "han" and not _marker_match(line, MarkerConfig()):
            return index
    return None


def detect_boundaries(work_id: str, lines: list[OcrLine],
                      cfg: BoundaryConfig | None = None,
                      marker_cfg: MarkerConfig | None = None,
                      heading_cfg: HeadingConfig | None = None,
                      all_pages: list[int] | None = None) -> BoundaryResult:
    cfg = cfg or BoundaryConfig()
    marker_cfg = marker_cfg or MarkerConfig()
    heading_cfg = heading_cfg or HeadingConfig()
    review: list[dict] = []
    candidates: list[BoundaryCandidate] = []
    spans: list[BoundarySpan] = []

    if not lines:
        return BoundaryResult([], [], [review_row(work_id, "", "", "no_lines", [], "")],
                              None, {"documents": 0, "boundaries": 0})

    first_han = _first_han_index(lines)
    preface_end = 0
    if first_han is None:
        review.append(review_row(work_id, "", "", "no_han_lines",
                                 sorted({ln.page for ln in lines}), lines[0].text,
                                 [lines[0].source_id]))
    elif first_han > 0 and cfg.capture_front_matter:
        preface_end = first_han
    elif first_han > 0:
        # Front matter exists but capture is disabled: keep it as document 1 and
        # record an explicit review instead of silently discarding it.
        review.append(review_row(work_id, "", "", "front_matter_not_captured",
                                 sorted({ln.page for ln in lines[:first_han]}),
                                 lines[0].text, [ln.source_id for ln in lines[:first_han]]))
        preface_end = 0

    front_matter: BoundarySpan | None = None
    if preface_end > 0:
        pre_lines = lines[:preface_end]
        front_matter = BoundarySpan(
            document_id=f"{work_id}.{cfg.preface_document_index:03d}",
            kind="front_matter", start=0, end=preface_end,
            pages=sorted({ln.page for ln in pre_lines}), lines=pre_lines,
            boundary_status="auto_accepted", evidence=["preface_to_han"],
            title_candidate="")
        spans.append(front_matter)
        candidates.append(BoundaryCandidate(
            boundary_id=f"{work_id}.B{len(candidates) + 1:03d}",
            following_line_id=lines[preface_end].source_id,
            preceding_line_id=pre_lines[-1].source_id,
            page=lines[preface_end].page,
            title_candidate="",
            evidence=["preface_to_han", "front_matter_end"],
            status="auto_accepted", decision_source="rule",
            start_index=preface_end,
            note="first Hán work starts after Vietnamese front matter",
            script_transition="vie_to_han"))

    work_index = 0
    doc_start = preface_end
    state = "han"
    sections_seen: list[str] = []
    section_events: list[dict] = []
    doc_count = 1 if front_matter else 0

    def close_span(end: int, title: str = "", heading: OcrLine | None = None,
                   status: str = "", evidence: list[str] | None = None) -> BoundarySpan:
        nonlocal work_index, doc_count
        work_index += 1
        doc_count += 1
        span_lines = lines[doc_start:end]
        span = BoundarySpan(
            document_id=f"{work_id}.{work_index:03d}", kind="work",
            start=doc_start, end=end,
            pages=sorted({ln.page for ln in span_lines}), lines=span_lines,
            boundary_status=status, evidence=list(evidence or []),
            title_candidate=title, heading_line=heading,
            section_events=list(section_events))
        spans.append(span)
        return span

    def reset_document() -> None:
        nonlocal state, sections_seen, section_events
        state = "han"
        sections_seen = []
        section_events = []

    def add_candidate(index: int, evidence: list[str], status: str,
                      title: str = "", note: str = "", applied: bool = True,
                      script_transition: str = "") -> BoundaryCandidate:
        candidate = BoundaryCandidate(
            boundary_id=f"{work_id}.B{len(candidates) + 1:03d}",
            following_line_id=lines[index].source_id,
            preceding_line_id=lines[index - 1].source_id if index > 0 else "",
            page=lines[index].page,
            title_candidate=title,
            evidence=evidence,
            status=status,
            decision_source="rule",
            start_index=index if applied else None,
            note=note,
            script_transition=script_transition)
        candidates.append(candidate)
        if status == "pending_review":
            review.append(review_row(work_id, "", "", "ambiguous_boundary",
                                     [lines[index].page], lines[index].text,
                                     [lines[index].source_id]))
        return candidate

    index = preface_end
    # If there is no preface, the first work starts at the first line.
    if not front_matter and index == 0:
        state = "han"

    for index in range(preface_end, len(lines)):
        line = lines[index]
        if index == doc_start:
            # The first line of a span is its title/heading candidate, never a
            # boundary against itself.
            match = _marker_match(line, marker_cfg)
            if match and match.status != "pending_review":
                state = match.section
                sections_seen.append(state)
                section_events.append({"event": "marker", "section": state,
                                       "line_id": line.source_id})
            elif is_subwork_label(line.text, heading_cfg):
                state = "han" if line.script_label == "han" else state
                section_events.append({"event": "subwork", "section": state,
                                       "line_id": line.source_id})
            continue

        match = _marker_match(line, marker_cfg)
        if match is not None and match.status != "pending_review":
            state = match.section
            sections_seen.append(state)
            section_events.append({"event": "marker", "section": state,
                                   "line_id": line.source_id})
            continue

        # Sub-work labels are explicitly not top-level boundaries.
        if is_subwork_label(line.text, heading_cfg):
            if line.script_label == "han":
                state = "han"
            section_events.append({"event": "subwork", "section": state,
                                   "line_id": line.source_id})
            continue

        numbered = is_top_level_numbered_heading(line.text, heading_cfg)
        title_like_pattern = _matches_patterns(line.text, cfg.boundary_title_patterns)
        han_after_vie = state in VIETNAMESE_SECTIONS and line.script_label == "han"

        if numbered or title_like_pattern:
            evidence = ["numbered_heading" if numbered else "title_pattern"]
            if han_after_vie:
                evidence.append("script_transition_vie_to_han")
            if state not in VIETNAMESE_SECTIONS:
                evidence.append("same_script_heading")
            # Heading evidence is strong enough to accept when the previous work
            # already entered a Vietnamese section, or when a numbered top-level
            # heading appears in a document that already has Vietnamese sections.
            completed = bool(set(sections_seen) & set(VIETNAMESE_SECTIONS))
            accept = (han_after_vie or title_like_pattern
                      or (numbered and (completed or not cfg.same_script_require_completed_sections)))
            if not cfg.require_heading_evidence_for_auto_accept:
                accept = accept or han_after_vie
            if accept:
                close_span(index, title=title_text(line.text, heading_cfg),
                           heading=line, status="auto_accepted", evidence=evidence)
                add_candidate(index, evidence, "auto_accepted",
                              title=title_text(line.text, heading_cfg),
                              script_transition="vie_to_han" if han_after_vie else "han_to_han")
                doc_start = index
                reset_document()
                # The new span starts with this heading line.
                match = _marker_match(line, marker_cfg)
                if match and match.status != "pending_review":
                    state = match.section
                    sections_seen.append(state)
                    section_events.append({"event": "marker", "section": state,
                                           "line_id": line.source_id})
                continue
            if cfg.detect_same_script_boundaries:
                add_candidate(index, evidence + ["review_required"], "pending_review",
                              title=title_text(line.text, heading_cfg), applied=False,
                              note="heading evidence without a completed previous work")
                continue

        if han_after_vie:
            evidence = ["script_transition_vie_to_han"]
            if cfg.split_on_vie_to_han_after_vietnamese_section and not cfg.require_heading_evidence_for_auto_accept:
                close_span(index, title="", heading=line, status="auto_accepted",
                           evidence=evidence)
                add_candidate(index, evidence, "auto_accepted",
                              script_transition="vie_to_han")
                doc_start = index
                reset_document()
                continue
            # Default: record the candidate, keep the line in the current span.
            add_candidate(index, evidence + ["missing_heading_evidence"],
                          "pending_review", applied=False,
                          note="CJK line after Vietnamese section without heading evidence",
                          script_transition="vie_to_han")
            # Treat it as Hán content inside the current document (flagged by
            # section assignment) and keep the state as-is for subsequent lines.
            continue

        # Ordinary content: update observed sections for later evidence checks.
        if line.script_label == "vie" and state in VIETNAMESE_SECTIONS:
            pass
        elif line.script_label == "han" and state == "han":
            pass

    close_span(len(lines), title="", heading=None,
               status="auto_accepted" if not candidates else "auto_accepted",
               evidence=["end_of_stream"])

    # Post-conditions: every line in exactly one span, in order, no gaps.
    covered: list[int] = []
    for span in spans:
        covered.extend(range(span.start, span.end))
    if covered != list(range(len(lines))):
        covered_set = set(covered)
        missing = [lines[i].source_id for i in range(len(lines)) if i not in covered_set]
        review.append(review_row(work_id, "", "", "line_accounting_gap",
                                 sorted({ln.page for ln in lines}), "", missing))

    pending = sum(1 for c in candidates if c.status == "pending_review")
    stats = {
        "raw_lines": len(lines),
        "documents": len(spans),
        "works": len([s for s in spans if s.kind == "work"]),
        "front_matter_lines": len(front_matter.lines) if front_matter else 0,
        "boundaries": len(candidates),
        "auto_accepted": sum(1 for c in candidates if c.status == "auto_accepted"),
        "pending_review": pending,
        "rejected": sum(1 for c in candidates if c.status == "rejected"),
    }
    return BoundaryResult(spans, candidates, review, front_matter, stats)


def build_documents(result: BoundaryResult) -> list[Document]:
    """Convert accepted spans to :class:`Document` objects (no line is dropped)."""
    docs: list[Document] = []
    for span in result.spans:
        heading = None
        if span.heading_line is not None:
            line = span.heading_line
            heading = {"text": line.text, "page": line.page,
                       "writing_system": line.writing_system,
                       "source_ids": [line.source_id]}
        docs.append(Document(id=span.document_id, pages=span.pages,
                             lines=list(span.lines), heading=heading))
    return docs
