"""Structural marker aliases, canonicalization and constrained fuzzy matching.

Only short heading-like lines are eligible for fuzzy matching.  Ordinary prose,
poetry and content lines are never rewritten by this module.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from difflib import SequenceMatcher
import re

from src.segmentation.headings import letter_count, nfc, squash, uppercase_ratio

# Canonical marker -> section state.
CANONICAL = {
    "phien_am": "PHIÊN ÂM:",
    "dich_nghia": "DỊCH NGHĨA:",
    "dich_tho": "DỊCH THƠ:",
    "khao_di": "KHẢO DỊ:",
    "chu_thich": "CHÚ THÍCH:",
    "nguyen_chu": "NGUYÊN CHÚ:",
}

MAIN_SECTIONS = ("han", "phien_am", "dich_nghia", "dich_tho")
VIETNAMESE_SECTIONS = ("phien_am", "dich_nghia", "dich_tho")
AUXILIARY_SECTIONS = ("khao_di", "chu_thich", "nguyen_chu")

DEFAULT_ALIASES = {
    "phien_am": ["PHIÊN ÂM:", "PHIỀN ÂM:", "PHIEN AM:", "PHIÊN AM:", "PHIEN ÂM:",
                 "PHIÊN ÂM", "PHIÊN ÂM :"],
    "dich_nghia": ["DỊCH NGHĨA:", "DICH NGHIA:", "DỊCH NGHĨA", "Dịch nghĩa:"],
    "dich_tho": ["DỊCH THƠ:", "DICH THO:", "DỊCH THO:", "DỊCH THƠ", "Dịch thơ:",
                 "DỊCH THOẠI:"],
    "khao_di": ["KHẢO DỊ:", "KHÁO DỊ:", "KHAO DI:", "Khảo dị:"],
    "chu_thich": ["CHÚ THÍCH:", "CHU THICH:", "CHÚ THÍCH", "Chú thích:"],
    "nguyen_chu": ["NGUYÊN CHÚ:", "NGUYEN CHU:", "NGUYÊN CHÚ", "Nguyên chú:"],
}

_KEY_ALIASES = {
    "PHIEN_AM": "phien_am", "PHIÊN ÂM": "phien_am", "AM": "phien_am",
    "DICH_NGHIA": "dich_nghia", "DỊCH NGHĨA": "dich_nghia", "NGHIA": "dich_nghia",
    "DICH_THO": "dich_tho", "DỊCH THƠ": "dich_tho", "THO": "dich_tho",
    "KHAO_DI": "khao_di", "KHẢO DỊ": "khao_di",
    "CHU_THICH": "chu_thich", "CHÚ THÍCH": "chu_thich",
    "NGUYEN_CHU": "nguyen_chu", "NGUYÊN CHÚ": "nguyen_chu",
}


@dataclass
class MarkerConfig:
    aliases: dict[str, list[str]] = field(default_factory=lambda: {
        k: list(v) for k, v in DEFAULT_ALIASES.items()})
    fuzzy_enabled: bool = True
    fuzzy_threshold: float = 80.0
    fuzzy_ambiguous_margin: float = 5.0
    max_heading_len: int = 70

    @classmethod
    def from_config(cls, seg_cfg: dict | None) -> "MarkerConfig":
        seg_cfg = seg_cfg or {}
        obj = cls()
        raw = seg_cfg.get("markers") or {}
        for key, values in raw.items():
            section = _KEY_ALIASES.get(str(key).upper().replace(" ", "_"), str(key).lower())
            if section not in CANONICAL:
                continue
            obj.aliases.setdefault(section, [])
            obj.aliases[section] = list(obj.aliases[section]) + [str(v) for v in values]
        fuzzy = seg_cfg.get("marker_fuzzy") or {}
        if "enabled" in fuzzy:
            obj.fuzzy_enabled = bool(fuzzy["enabled"])
        if "threshold" in fuzzy:
            obj.fuzzy_threshold = float(fuzzy["threshold"])
        if "ambiguous_margin" in fuzzy:
            obj.fuzzy_ambiguous_margin = float(fuzzy["ambiguous_margin"])
        if "max_heading_len" in fuzzy:
            obj.max_heading_len = int(fuzzy["max_heading_len"])
        return obj


@dataclass
class MarkerMatch:
    section: str
    canonical: str
    matched_alias: str
    method: str                 # exact | fuzzy
    score: float = 100.0
    status: str = "unchanged"   # unchanged | corrected | pending_review
    ambiguous: list[str] = field(default_factory=list)


def _strip_ocr_prefix(text: str) -> str:
    return re.sub(r"^(?:[lI|]\s+)", "", text).strip()


def _content_after_colon(text: str) -> bool:
    match = re.search(r"[:：]\s*(.+)$", text)
    return bool(match and match.group(1).strip())


def is_heading_like_marker(text: str, cfg: MarkerConfig | None = None) -> bool:
    cfg = cfg or MarkerConfig()
    probe = _strip_ocr_prefix(nfc(text).strip())
    if not probe or len(probe) > cfg.max_heading_len:
        return False
    if letter_count(probe) < 2:
        return False
    if _content_after_colon(probe):
        return False
    if not probe.endswith((":", "：")):
        return False
    return uppercase_ratio(probe) >= 0.55


def canonicalize_marker(text: str, cfg: MarkerConfig | None = None) -> MarkerMatch | None:
    """Return a marker match, or ``None`` when the line is not a known marker.

    Exact alias matching is attempted first.  Fuzzy matching is constrained to
    short uppercase heading-like lines ending in a colon, so content and poetry
    cannot be rewritten.
    """
    cfg = cfg or MarkerConfig()
    probe = _strip_ocr_prefix(nfc(text).strip())
    if not probe:
        return None
    probe_key = squash(probe)
    if not probe_key:
        return None

    exact: list[MarkerMatch] = []
    for section, aliases in cfg.aliases.items():
        canonical = CANONICAL.get(section)
        if not canonical:
            continue
        for alias in aliases:
            if probe_key == squash(alias):
                status = "unchanged" if probe == canonical else "corrected"
                exact.append(MarkerMatch(section, canonical, alias, "exact", 100.0, status))
                break
    if exact:
        if len({m.section for m in exact}) > 1:
            first = exact[0]
            first.status = "pending_review"
            first.ambiguous = sorted({m.section for m in exact})
            return first
        return exact[0]

    if not cfg.fuzzy_enabled or not is_heading_like_marker(probe, cfg):
        return None

    scored: list[tuple[float, str, str]] = []
    for section, aliases in cfg.aliases.items():
        if section not in CANONICAL:
            continue
        for alias in aliases:
            score = SequenceMatcher(None, probe_key, squash(alias)).ratio() * 100.0
            scored.append((score, section, alias))
    scored.sort(reverse=True)
    if not scored or scored[0][0] < cfg.fuzzy_threshold:
        return None
    best_score, best_section, best_alias = scored[0]
    ambiguous = sorted({sec for s, sec, _ in scored
                        if s >= cfg.fuzzy_threshold and sec != best_section
                        and best_score - s <= cfg.fuzzy_ambiguous_margin})
    match = MarkerMatch(best_section, CANONICAL[best_section], best_alias, "fuzzy",
                        round(best_score, 2), "corrected")
    if ambiguous:
        match.status = "pending_review"
        match.ambiguous = [best_section, *ambiguous]
    return match
