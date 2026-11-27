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
MAX_SYLLABLES = 400
MAX_HAN = 400
# Dòng không phải nội dung khi chấm điểm: mục chú thích "1. Thuật ngữ: ..." / "2. Nguyên bản chép ..." và tiêu đề
# viết hoa (CPO_20.067: hai mục chú thích đứng trước phiên âm từng kéo điểm xuống 0,10).
NOTE_LINE_RE = re.compile(r"^\s*\(?\d{1,3}[.)]\s*\S")
MIN_UNITS = 6                       # quá ít chữ/âm tiết thì không kết luận
SYLLABLE_RE = re.compile(r"[^\W\d_]+")


def fold(text: str) -> str:
    text = unicodedata.normalize("NFD", unicodedata.normalize("NFC", text).lower().replace("đ", "d"))
    return "".join(c for c in text if not unicodedata.combining(c))


def _is_upper_title(text: str) -> bool:
    letters = [c for c in text if c.isalpha()]
    return len(letters) >= 3 and all(c.isupper() for c in letters)


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
    body = [t for t in am_texts if not NOTE_LINE_RE.match(t) and not _is_upper_title(t)]
    am = [fold(s) for t in body for s in SYLLABLE_RE.findall(t)][:MAX_SYLLABLES]
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


SPLIT_LINE_MAX = 0.55       # dòng có < 55% âm tiết là cách đọc của chữ Hán trong bài = dòng dịch (câu dịch nhiều
                            # tên riêng "Hỏi rằng: Khổng Tử nói ... Di Địch" khớp 0,50; dòng phiên âm thấp nhất 0,44
                            # nhưng không bao giờ hai dòng liền nhau)
SPLIT_PREFIX_MIN = 0.6      # phần am trước điểm tách phải khớp trung bình >= 60% (chữ Hán OCR rác thì không tách)
SPLIT_CONSUMED = 0.8        # số âm tiết trước điểm tách >= 80% số chữ Hán
SPLIT_MIN_SYLLABLES = 6     # dòng ngắn hơn không dùng để quyết định (văn tán 4 chữ khớp dao động, CPO_19.265)


def line_coverage(text: str, han_readings: set[str]) -> float | None:
    sy = [fold(s) for s in SYLLABLE_RE.findall(text)]
    if len(sy) < SPLIT_MIN_SYLLABLES:
        return None
    return sum(1 for s in sy if s in han_readings) / len(sy)


def split_point(han_texts: list[str], am_texts: list[str], readings: dict[str, frozenset[str]],
                skip: list[bool] | None = None) -> int | None:
    """Chỉ số dòng am đầu tiên thuộc phần DỊCH (dính vào sau phiên âm vì OCR mất nhãn "Dịch nghĩa:", CPO_20 tr.137),
    hoặc None. Điều kiện: hai dòng liền nhau (đủ dài) khớp < SPLIT_LINE_MAX, số âm tiết trước đó >= SPLIT_CONSUMED x số
    chữ Hán, và phần trước khớp trung bình >= SPLIT_PREFIX_MIN."""
    han = [c for t in han_texts for c in CJK_IDEOGRAPH_RE.findall(t)]
    if len(han) < MIN_UNITS:
        return None
    pool: set[str] = set()
    for c in han:
        pool |= _readings_of(readings, c)
    # tiêu đề chạy (skip), mục chú thích/khảo dị "(2) Bản B: ...", tiêu đề viết hoa: không dùng để quyết định
    cov = [None if ((skip and skip[i]) or NOTE_LINE_RE.match(t) or _is_upper_title(t)) else line_coverage(t, pool)
           for i, t in enumerate(am_texts)]
    consumed = 0
    for i, t in enumerate(am_texts):
        if consumed >= SPLIT_CONSUMED * len(han):
            nxt = [c for c in cov[i:] if c is not None][:2]
            prev = [c for c in cov[:i] if c is not None]
            if (len(nxt) == 2 and all(c < SPLIT_LINE_MAX for c in nxt) and cov[i] is not None
                    and prev and sum(prev) / len(prev) >= SPLIT_PREFIX_MIN):
                return i
        if not NOTE_LINE_RE.match(t) and not _is_upper_title(t):
            consumed += len(SYLLABLE_RE.findall(t))
    return None


VERSE_THO_RATIO = 1.6       # nghia >= 1,6 x số dòng thân am (bài thơ, không có lớp tho) -> phần đuôi là dịch thơ
VERSE_MAX_SYLLABLES = 12    # dòng dịch thơ: <= 12 âm tiết (lục bát, thất ngôn, ngũ ngôn)


def verse_tho_split(am_texts: list[str], nghia_texts: list[str]) -> int | None:
    """Chỉ số dòng nghia đầu tiên thuộc DỊCH THƠ (OCR mất nhãn "Dịch thơ:", CPO_20.039/.145), hoặc None. Bài thơ dịch
    nghĩa từng câu: n dòng thân am <-> n dòng thân nghia; phần còn lại (>= 4 dòng, đều <= VERSE_MAX_SYLLABLES âm tiết)
    là dịch thơ."""
    body = [t for t in am_texts if not NOTE_LINE_RE.match(t) and not _is_upper_title(t)]
    n = len(body)
    if n < 4 or len(am_texts) and any(len(SYLLABLE_RE.findall(t)) > VERSE_MAX_SYLLABLES + 4 for t in body):
        return None
    nb = [i for i, t in enumerate(nghia_texts) if not _is_upper_title(t)]
    if len(nb) < VERSE_THO_RATIO * n or len(nb) <= n:
        return None
    k = nb[n]
    tail = [t for t in nghia_texts[k:] if not _is_upper_title(t)]
    if len(tail) < 4 or any(len(SYLLABLE_RE.findall(t)) > VERSE_MAX_SYLLABLES for t in tail):
        return None
    return k


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
        self.split_lines = bool(cfg.get("split_lines"))    # sửa F, mặc định tắt

    def split(self, han_texts: list[str], am_texts: list[str], skip: list[bool] | None = None) -> int | None:
        if not self.split_lines:
            return None
        return split_point(han_texts, am_texts, load_folded_readings(self.path), skip)

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
