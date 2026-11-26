"""Nhận diện "phần Việt ngay sau chữ Hán là phiên âm hay dịch nghĩa" bằng từ điển âm Hán–Việt.

Khi sách không in tiêu đề PHIÊN ÂM, segmentation (chế độ hai luồng) coi phần Việt ngay sau khối Hán là âm
(`implicit_after_han`). Bài văn xuôi chỉ có DỊCH NGHĨA (CPO_20 tr.97–135, 268–279…) vì thế bị gán nhầm vào am.

Điểm = độ dài dãy con chung dài nhất (LCS) giữa dãy âm tiết của lớp am và dãy chữ Hán của lớp han, trong đó một âm
tiết "khớp" một chữ nếu nó là một cách đọc Hán–Việt của chữ đó (so sau khi bỏ dấu, đ -> d, vì OCR hay sai dấu), chia
cho độ dài dãy ngắn hơn. Phiên âm đọc từng chữ theo thứ tự nên điểm cao; bản dịch nghĩa thuần Việt điểm thấp.

Cấu hình `segmentation.am_detection` (mặc định tắt):
    {"enabled": true, "min_match": 0.35, "reading_dict": "resources/han_viet.tsv",
     "nghia_only_pages": [268, 277], "am_pages": []}
`nghia_only_pages` / `am_pages`: ghi đè theo TRANG BẮT ĐẦU của tài liệu (ID tài liệu đổi khi segmentation đổi).
"""

from __future__ import annotations

import re
import unicodedata
from functools import lru_cache
from pathlib import Path

from src.ocr.script_filter import CJK_IDEOGRAPH_RE

DEFAULT_MIN_MATCH = 0.35
DEFAULT_READING_DICT = "resources/han_viet.tsv"
MAX_SYLLABLES = 150
MAX_HAN = 250
MIN_UNITS = 6                       # quá ít chữ/âm tiết thì không kết luận
SYLLABLE_RE = re.compile(r"[^\W\d_]+")


def fold(text: str) -> str:
    text = unicodedata.normalize("NFD", unicodedata.normalize("NFC", text).lower().replace("đ", "d"))
    return "".join(c for c in text if not unicodedata.combining(c))


@lru_cache(maxsize=4)
def load_folded_readings(path: str) -> dict[str, frozenset[str]]:
    from src.alignment.aligners import load_reading_dict
    return {ch: frozenset(fold(r) for r in vals) for ch, vals in load_reading_dict(Path(path)).items()}


def _readings_of(readings: dict[str, frozenset[str]], ch: str) -> frozenset[str]:
    found = readings.get(ch)
    if found is None:
        from src.alignment.aligners import to_traditional
        found = readings.get(to_traditional(ch))
    return found or frozenset()


def reading_match(han_texts: list[str], am_texts: list[str], readings: dict[str, frozenset[str]]) -> float | None:
    """Điểm 0..1 (None nếu quá ít dữ liệu)."""
    han = [c for t in han_texts for c in CJK_IDEOGRAPH_RE.findall(t)][:MAX_HAN]
    am = [fold(s) for t in am_texts for s in SYLLABLE_RE.findall(t)][:MAX_SYLLABLES]
    if len(han) < MIN_UNITS or len(am) < MIN_UNITS:
        return None
    rd = [_readings_of(readings, c) for c in han]
    prev = [0] * (len(han) + 1)
    for a in am:
        cur = [0] * (len(han) + 1)
        for j, r in enumerate(rd, 1):
            cur[j] = prev[j - 1] + 1 if a in r else max(prev[j], cur[j - 1])
        prev = cur
    return prev[-1] / min(len(han), len(am))


class AmDetector:
    """Gọi từ segmentation: quyết định có chuyển lớp am (mở ngầm) của một tài liệu sang nghia không."""

    def __init__(self, cfg: dict | None, base_dir: Path | None = None):
        cfg = cfg or {}
        self.enabled = bool(cfg.get("enabled"))
        self.min_match = float(cfg.get("min_match", DEFAULT_MIN_MATCH))
        path = Path(cfg.get("reading_dict", DEFAULT_READING_DICT))
        if not path.is_absolute() and base_dir is not None and not path.exists():
            path = base_dir / path
        self.path = str(path)
        self.nghia_only = {int(p) for p in cfg.get("nghia_only_pages", [])}
        self.keep_am = {int(p) for p in cfg.get("am_pages", [])}

    def decide(self, start_page: int, han_texts: list[str], am_texts: list[str]) -> tuple[bool, str]:
        """(chuyển am -> nghia?, lý do để ghi review)."""
        if start_page in self.keep_am:
            return False, "override:am_pages"
        if start_page in self.nghia_only:
            return True, "override:nghia_only_pages"
        if not self.enabled:
            return False, "disabled"
        score = reading_match(han_texts, am_texts, load_folded_readings(self.path))
        if score is None:
            return False, "too_short"
        return score < self.min_match, f"reading_match={score:.2f} (min {self.min_match})"
