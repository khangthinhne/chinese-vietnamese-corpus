"""Registry các thuật toán dóng hàng. Thêm phương pháp mới = viết 1 class + @register_aligner.

    class Aligner:  align(src_layer, tgt_layer, src: list[Sentence], tgt: list[Sentence]) -> list[Bead]

Hợp đồng:
    - Bead.src / Bead.tgt là chỉ số 0-based, tăng dần, không chồng lấn, phủ hết cả hai phía
      (câu bị bỏ qua trả về Bead một phía rỗng + cờ "unaligned").
    - Bead.score ∈ [0,1], càng cao càng tin; ý nghĩa nêu trong docstring từng aligner.
      Điểm KHÔNG hiệu chuẩn giữa các aligner, chỉ dùng để xếp hạng/ưu tiên rà soát.

Đã có:
    levenshtein  han-am     so khớp âm Hán-Việt với bản phiên âm (cần từ điển --opt reading_dict=...)
    length       bất kỳ     dóng theo độ dài (Gale-Church), KHÔNG cần model; baseline/dự phòng
Dự kiến thêm (cùng giao diện): clc (API lab), bertalign/vecalign, llm (cho dịch thơ).
"""

from __future__ import annotations

import logging
import math
import re
import unicodedata
from abc import ABC, abstractmethod
from pathlib import Path

from src.alignment.dp import DEFAULT_MOVES, monotonic_align
from src.common.corpus import _CJK
from src.common.sentences import Bead, Sentence

log = logging.getLogger("aligners")

ALIGNERS: dict[str, type["Aligner"]] = {}


def register_aligner(cls):
    if cls.name in ALIGNERS:
        raise ValueError(f"Aligner đã đăng ký: {cls.name}")
    ALIGNERS[cls.name] = cls
    return cls


class Aligner(ABC):
    name: str
    # tên tuỳ chọn -> (kiểu, mặc định); truyền bằng --opt ten=giatri
    options: dict[str, tuple[type, object]] = {}

    def __init__(self, **opts):
        unknown = set(opts) - set(self.options)
        if unknown:
            raise ValueError(f"{self.name}: tuỳ chọn không hợp lệ {sorted(unknown)}; có: {sorted(self.options)}")
        self.opts = {k: (typ(opts[k]) if k in opts else default) for k, (typ, default) in self.options.items()}

    def describe(self) -> dict:
        return {"name": self.name, "options": self.opts}

    @abstractmethod
    def align(self, src_layer: str, tgt_layer: str, src: list[Sentence], tgt: list[Sentence]) -> list[Bead]:
        ...


def spans_to_beads(spans, score_fn) -> list[Bead]:
    beads = []
    for i0, i1, j0, j1 in spans:
        s, t = tuple(range(i0, i1)), tuple(range(j0, j1))
        flags = ("unaligned",) if not s or not t else ()
        beads.append(Bead(s, t, 0.0 if flags else float(score_fn(i0, i1, j0, j1)), flags))
    return beads


# --------------------------------------------------------------------------- #
# Tiện ích chữ
# --------------------------------------------------------------------------- #
def han_chars(text: str) -> list[str]:
    return [c for c in text if _CJK.match(c)]


def strip_tone(s: str) -> str:
    s = unicodedata.normalize("NFD", s.lower().replace("đ", "d"))
    return "".join(c for c in s if unicodedata.category(c) != "Mn")


def vi_syllables(text: str) -> list[str]:
    return [unicodedata.normalize("NFC", w.lower()) for w in re.findall(r"[^\W\d_]+", text)]


def lev(a: str, b: str) -> int:
    if a == b:
        return 0
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def load_reading_dict(path: str | Path) -> dict[str, tuple[str, ...]]:
    """TSV: <chữ Hán>\\t<âm1> <âm2> ...  (tạo bằng scripts/build_han_viet_dict.py từ Unihan)."""
    readings: dict[str, tuple[str, ...]] = {}
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        ch, _, rest = line.partition("\t")
        vals = tuple(dict.fromkeys(unicodedata.normalize("NFC", r.lower()) for r in rest.split()))
        if ch and vals:
            readings[ch] = vals
    if not readings:
        raise ValueError(f"Từ điển âm Hán-Việt rỗng: {path}")
    return readings


# --------------------------------------------------------------------------- #
# 1. Levenshtein: Hán <-> phiên âm
# --------------------------------------------------------------------------- #
@register_aligner
class LevenshteinAligner(Aligner):
    """Dóng Hán với bản phiên âm (dịch âm).

    Mỗi chữ Hán ↔ một âm tiết. Khoảng cách sửa (Levenshtein) tính ở mức âm tiết với chi phí thay thế mềm:
        âm đúng (kể cả dấu)                 0
        đúng khi bỏ dấu (OCR rớt dấu)        bare_cost
        chữ Hán không có trong từ điển       unknown_cost
        khác                                 lev(ký tự)/độ dài, tối đa 1
    Chi phí thêm/xoá = 1. DP chọn nhóm 1-1/1-2/2-1/... có tổng khoảng cách + phạt gộp nhỏ nhất.
    skip_penalty phải NHỎ HƠN merge_penalty: nếu không, một câu thừa không có đối ứng sẽ bị "nuốt" vào
    cặp bên cạnh (gộp) thay vì được đánh dấu unaligned.
    score = 1 - khoảng cách/độ dài dài hơn  (≈ tỉ lệ âm tiết khớp).
    """
    name = "levenshtein"
    options = {
        "reading_dict": (str, ""), "unknown_cost": (float, 0.5), "bare_cost": (float, 0.3),
        "merge_penalty": (float, 2.0), "skip_penalty": (float, 1.0), "band": (int, 30),
    }

    def align(self, src_layer, tgt_layer, src, tgt):
        if (src_layer, tgt_layer) != ("han", "am"):
            raise ValueError("levenshtein chỉ dùng cho cặp han-am")
        o = self.opts
        if not o["reading_dict"]:
            raise ValueError("Cần --opt reading_dict=<file.tsv> (xem scripts/build_han_viet_dict.py)")
        readings = load_reading_dict(o["reading_dict"])
        han = [han_chars(s.text) for s in src]
        am = [vi_syllables(s.text) for s in tgt]
        total = sum(map(len, han))
        known = sum(1 for seq in han for c in seq if c in readings)
        if total:
            log.info("Từ điển phủ %.1f%% chữ Hán (%d/%d)", 100 * known / total, known, total)
            if known / total < 0.9:
                log.warning("Từ điển chỉ phủ %.0f%% chữ Hán: kết quả kém tin cậy", 100 * known / total)

        sub_cache: dict[tuple[str, str], float] = {}
        bare_cache: dict[str, str] = {}

        def bare(s: str) -> str:
            if s not in bare_cache:
                bare_cache[s] = strip_tone(s)
            return bare_cache[s]

        def sub(ch: str, syl: str) -> float:
            key = (ch, syl)
            if key in sub_cache:
                return sub_cache[key]
            cands = readings.get(ch)
            if not cands:
                v = o["unknown_cost"]
            elif syl in cands:
                v = 0.0
            else:
                bs = bare(syl)
                v = 1.0
                for c in cands:
                    bc = bare(c)
                    v = min(v, o["bare_cost"] if bc == bs else min(1.0, lev(bc, bs) / max(len(bc), len(bs), 1)))
            sub_cache[key] = v
            return v

        def edit(a: list[str], b: list[str]) -> float:
            prev = [float(j) for j in range(len(b) + 1)]
            for i, ch in enumerate(a, 1):
                cur = [float(i)]
                for j, syl in enumerate(b, 1):
                    cur.append(min(prev[j] + 1.0, cur[j - 1] + 1.0, prev[j - 1] + sub(ch, syl)))
                prev = cur
            return prev[-1]

        dist_cache: dict[tuple[int, int, int, int], tuple[float, int]] = {}

        def group(i0, i1, j0, j1):
            key = (i0, i1, j0, j1)
            if key not in dist_cache:
                a = [c for k in range(i0, i1) for c in han[k]]
                b = [s for k in range(j0, j1) for s in am[k]]
                dist_cache[key] = (edit(a, b) if a and b else float(len(a) + len(b)), max(len(a), len(b), 1))
            return dist_cache[key]

        def cost(i0, i1, j0, j1):
            d, _ = group(i0, i1, j0, j1)
            a, b = i1 - i0, j1 - j0
            if a == 0 or b == 0:
                return d + o["skip_penalty"]
            return d + o["merge_penalty"] * (a + b - 2)

        def score(i0, i1, j0, j1):
            d, length = group(i0, i1, j0, j1)
            return max(0.0, 1.0 - d / length)

        spans = monotonic_align(len(src), len(tgt), cost, DEFAULT_MOVES, o["band"])
        return spans_to_beads(spans, score)


# --------------------------------------------------------------------------- #
# 2. Theo độ dài (Gale & Church 1993)
# --------------------------------------------------------------------------- #
@register_aligner
class LengthAligner(Aligner):
    """Dóng theo độ dài (Hán: số chữ Hán; Việt: số âm tiết). Không dùng nghĩa -> chỉ là baseline.

    score = xác suất hai phía (0..1) của độ lệch độ dài so với tỉ lệ trung bình toàn văn bản:
    "độ dài hợp lý", KHÔNG phải "đúng nghĩa". Dùng cho demo/dự phòng; bản chính nên dùng clc/bertalign/llm.
    """
    name = "length"
    options = {"variance": (float, 6.8), "skip_cost": (float, 6.0), "band": (int, 30)}
    PRIORS = {(1, 1): 0.89, (1, 2): 0.089, (2, 1): 0.089, (2, 2): 0.011, (1, 3): 0.005, (3, 1): 0.005}

    @staticmethod
    def _length(layer: str, text: str) -> int:
        return len(han_chars(text)) if layer == "han" else len(vi_syllables(text))

    def align(self, src_layer, tgt_layer, src, tgt):
        o = self.opts
        ls = [self._length(src_layer, s.text) for s in src]
        lt = [self._length(tgt_layer, s.text) for s in tgt]
        if sum(ls) == 0 or sum(lt) == 0:
            raise ValueError("Một phía không có nội dung")
        c = sum(lt) / sum(ls)
        log.info("Tỉ lệ độ dài trung bình đích/nguồn = %.2f", c)

        def prob(l1: int, l2: int) -> float:
            delta = (l2 - l1 * c) / math.sqrt(max(l1, 1) * o["variance"])
            return math.erfc(abs(delta) / math.sqrt(2))

        def cost(i0, i1, j0, j1):
            a, b = i1 - i0, j1 - j0
            if a == 0 or b == 0:
                return o["skip_cost"]
            prior = self.PRIORS.get((a, b))
            if prior is None:
                return 1e9
            p = prob(sum(ls[i0:i1]), sum(lt[j0:j1]))
            return -math.log(prior) - math.log(max(p, 1e-12))

        def score(i0, i1, j0, j1):
            return prob(sum(ls[i0:i1]), sum(lt[j0:j1]))

        spans = monotonic_align(len(src), len(tgt), cost, DEFAULT_MOVES, o["band"])
        return spans_to_beads(spans, score)
