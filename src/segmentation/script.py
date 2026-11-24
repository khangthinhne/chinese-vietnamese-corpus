"""Line-level Unicode script classification.

The classifier counts only *script characters* (letters / CJK ideographs) and
deliberately ignores whitespace, digits, punctuation and symbols so that page
numbers or OCR decoration cannot inflate a ratio.

Labels:
    han     predominantly CJK ideographs
    vie     predominantly Latin-script text (including Vietnamese diacritics)
    mixed   substantial CJK and Latin content
    unknown insufficient/ambiguous script evidence

Thresholds are configurable through ``segmentation.script_classification``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import unicodedata


# CJK ranges supported by the runtime.  The basic block is not enough: CPO_19
# contains compatibility ideographs and occasional extension characters.
CJK_RANGES = (
    (0x2E80, 0x2FDF),    # CJK radicals / Kangxi radicals
    (0x3005, 0x3005),    # ideographic iteration mark
    (0x3007, 0x3007),    # ideographic number zero
    (0x3400, 0x4DBF),    # CJK Extension A
    (0x4E00, 0x9FFF),    # CJK Unified Ideographs
    (0xF900, 0xFAFF),    # CJK Compatibility Ideographs
    (0x20000, 0x2FA1F),  # Extensions B..F and compatibility supplement
)


def is_cjk(ch: str) -> bool:
    cp = ord(ch)
    return any(lo <= cp <= hi for lo, hi in CJK_RANGES)


def is_latin(ch: str) -> bool:
    if not ch.isalpha():
        return False
    return "LATIN" in unicodedata.name(ch, "")


@dataclass
class ScriptConfig:
    han_cjk_ratio_min: float = 0.50
    vie_latin_ratio_min: float = 0.70
    mixed_cjk_ratio_min: float = 0.20
    mixed_latin_ratio_min: float = 0.20
    min_script_characters: int = 2
    method: str = "unicode_script_ratio"

    @classmethod
    def from_config(cls, cfg: dict | None) -> "ScriptConfig":
        cfg = cfg or {}
        return cls(
            han_cjk_ratio_min=float(cfg.get("han_cjk_ratio_min", 0.50)),
            vie_latin_ratio_min=float(cfg.get("vie_latin_ratio_min", 0.70)),
            mixed_cjk_ratio_min=float(cfg.get("mixed_cjk_ratio_min", 0.20)),
            mixed_latin_ratio_min=float(cfg.get("mixed_latin_ratio_min", 0.20)),
            min_script_characters=int(cfg.get("min_script_characters", 2)),
            method=str(cfg.get("method", "unicode_script_ratio")),
        )


@dataclass
class ScriptInfo:
    label: str = "unknown"
    cjk_count: int = 0
    latin_count: int = 0
    other_letter_count: int = 0
    script_character_count: int = 0
    cjk_ratio: float = 0.0
    latin_ratio: float = 0.0
    method: str = "unicode_script_ratio"
    review: bool = False
    review_reason: str = ""

    def to_dict(self) -> dict:
        return {
            "label": self.label,
            "cjk_count": self.cjk_count,
            "latin_count": self.latin_count,
            "other_letter_count": self.other_letter_count,
            "script_character_count": self.script_character_count,
            "cjk_ratio": round(self.cjk_ratio, 4),
            "latin_ratio": round(self.latin_ratio, 4),
            "method": self.method,
            "review": self.review,
            "review_reason": self.review_reason,
        }


def count_scripts(text: str) -> tuple[int, int, int]:
    """Return (cjk, latin, other_letter) counts; digits/punct are ignored."""
    cjk = latin = other = 0
    for ch in text:
        if ch.isspace():
            continue
        if is_cjk(ch):
            cjk += 1
        elif is_latin(ch):
            latin += 1
        elif ch.isalpha():
            other += 1
    return cjk, latin, other


def classify(text: str, cfg: ScriptConfig | None = None) -> ScriptInfo:
    cfg = cfg or ScriptConfig()
    cjk, latin, other = count_scripts(text)
    total = cjk + latin + other
    info = ScriptInfo(cjk_count=cjk, latin_count=latin, other_letter_count=other,
                      script_character_count=total, method=cfg.method)
    if total < cfg.min_script_characters:
        info.label = "unknown"
        info.review = True
        info.review_reason = "insufficient_script_characters"
        return info

    info.cjk_ratio = cjk / total
    info.latin_ratio = latin / total
    cjk_ratio, latin_ratio = info.cjk_ratio, info.latin_ratio

    if cjk_ratio >= cfg.han_cjk_ratio_min:
        info.label = "han"
        return info
    if latin_ratio >= cfg.vie_latin_ratio_min:
        if cjk_ratio >= cfg.mixed_cjk_ratio_min:
            info.label = "mixed"
            info.review = True
            info.review_reason = "substantial_cjk_in_latin_line"
        else:
            info.label = "vie"
        return info
    if cjk_ratio >= cfg.mixed_cjk_ratio_min and latin_ratio >= cfg.mixed_latin_ratio_min:
        info.label = "mixed"
        info.review = True
        info.review_reason = "substantial_mixed_scripts"
        return info
    # Lenient fallbacks are marked for review rather than guessed silently.
    if latin_ratio >= 0.50:
        info.label = "vie"
        info.review = True
        info.review_reason = "latin_ratio_below_vie_threshold"
    elif cjk_ratio >= 0.30:
        info.label = "han"
        info.review = True
        info.review_reason = "cjk_ratio_below_han_threshold"
    else:
        info.label = "unknown"
        info.review = True
        info.review_reason = "no_confident_script_rule"
    return info


def is_han_text(text: str, cfg: ScriptConfig | None = None) -> bool:
    return classify(text, cfg).label == "han"
