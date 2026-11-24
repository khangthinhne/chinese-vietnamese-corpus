"""Heading / sub-work / title heuristics shared by boundary and section stages.

These helpers deliberately describe *shape*, not semantic meaning.  They are
used as evidence only; callers still record review status when a decision is
ambiguous.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import re
import unicodedata


def nfc(text: str) -> str:
    return unicodedata.normalize("NFC", text)


def strip_diacritics(text: str) -> str:
    decomposed = unicodedata.normalize("NFD", nfc(text))
    return "".join(ch for ch in decomposed if unicodedata.category(ch) != "Mn")


def squash(text: str) -> str:
    """Case/diacritic/whitespace-insensitive key used for heading comparisons."""
    return "".join(ch for ch in strip_diacritics(text).casefold() if ch.isalnum())


def letter_count(text: str) -> int:
    return sum(1 for ch in text if ch.isalpha())


def uppercase_ratio(text: str) -> float:
    letters = [ch for ch in text if ch.isalpha()]
    if not letters:
        return 0.0
    return sum(1 for ch in letters if ch.isupper()) / len(letters)


@dataclass
class HeadingConfig:
    top_level_numbered_pattern: str = r"^\s*\d{1,3}\s*[.．、﹒·。)]\s*"
    subwork_patterns: list[str] = field(default_factory=lambda: [
        r"^\s*(?:\d{1,3}\s*[.．、﹒·。)]\s*)?其[一二三四五六七八九十百]+\s*$",
        r"^\s*KỲ\s+(?:NHẤT|NHỊ|THỨ\s+\S+|MỘT|HAI|BA|BỐN)\s*$",
        r"^\s*KỲ\s+\d{1,2}\s*$",
        r"^\s*BÀI\s+\d{1,2}\s*$",
    ])
    title_max_len: int = 110
    title_max_words: int = 18
    title_uppercase_ratio_min: float = 0.70
    title_max_lines: int = 2
    unknown_heading_max_len: int = 70
    unknown_heading_uppercase_ratio_min: float = 0.60

    @classmethod
    def from_config(cls, cfg: dict | None) -> "HeadingConfig":
        cfg = cfg or {}
        obj = cls()
        if cfg.get("top_level_numbered_pattern"):
            obj.top_level_numbered_pattern = str(cfg["top_level_numbered_pattern"])
        if cfg.get("subwork_patterns"):
            obj.subwork_patterns = [str(p) for p in cfg["subwork_patterns"]]
        for key in ("title_max_len", "title_max_words", "title_uppercase_ratio_min",
                    "title_max_lines", "unknown_heading_max_len",
                    "unknown_heading_uppercase_ratio_min"):
            if key in cfg:
                setattr(obj, key, type(getattr(obj, key))(cfg[key]))
        return obj


def _matches_any(text: str, patterns: list[str]) -> bool:
    probe = nfc(text).strip()
    for pattern in patterns:
        try:
            if re.search(pattern, probe, re.IGNORECASE):
                return True
        except re.error:
            continue
    return False


def is_subwork_label(text: str, cfg: HeadingConfig | None = None) -> bool:
    cfg = cfg or HeadingConfig()
    probe = nfc(text).strip()
    if not probe:
        return False
    if _matches_any(probe, cfg.subwork_patterns):
        return True
    # Diacritic-insensitive fallback for KỲ NHẤT / KỲ NHỊ / KỲ 1.
    key = squash(probe)
    if key.startswith("ky"):
        rest = key[2:]
        return rest in {"nhat", "nhi", "mot", "hai", "ba", "bon"} or rest.isdigit()
    return False


def strip_number_prefix(text: str, cfg: HeadingConfig | None = None) -> str:
    cfg = cfg or HeadingConfig()
    return re.sub(cfg.top_level_numbered_pattern, "", nfc(text).strip(), count=1).strip()


def is_top_level_numbered_heading(text: str, cfg: HeadingConfig | None = None) -> bool:
    cfg = cfg or HeadingConfig()
    probe = nfc(text).strip()
    if not probe or is_subwork_label(probe, cfg):
        return False
    try:
        return bool(re.search(cfg.top_level_numbered_pattern, probe))
    except re.error:
        return False


def is_title_like(text: str, cfg: HeadingConfig | None = None) -> bool:
    """Uppercase, short, non-sentence line: a *candidate* title, never a fact."""
    cfg = cfg or HeadingConfig()
    probe = nfc(text).strip()
    if not probe or len(probe) > cfg.title_max_len:
        return False
    if letter_count(probe) < 2:
        return False
    if len(probe.split()) > cfg.title_max_words:
        return False
    if uppercase_ratio(probe) < cfg.title_uppercase_ratio_min:
        return False
    # A sentence ending is strong evidence against a title.
    if re.search(r"[.?!;…]$", probe):
        return False
    return True


def is_unknown_uppercase_heading(text: str, cfg: HeadingConfig | None = None) -> bool:
    cfg = cfg or HeadingConfig()
    probe = nfc(text).strip()
    if not probe or len(probe) > cfg.unknown_heading_max_len:
        return False
    if letter_count(probe) < 2:
        return False
    if uppercase_ratio(probe) < cfg.unknown_heading_uppercase_ratio_min:
        return False
    return bool(re.search(r"[:：]\s*$", probe))


def title_text(text: str, cfg: HeadingConfig | None = None) -> str:
    return strip_number_prefix(text, cfg)
