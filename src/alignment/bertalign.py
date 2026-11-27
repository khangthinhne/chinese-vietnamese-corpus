"""Aligner `bertalign`: dóng Hán ↔ dịch nghĩa bằng embedding câu đa ngữ (theo Liu & Zhu 2022, Bertalign).

Tự cài lõi thuật toán (không dùng gói bertalign, gói này cứng LaBSE + faiss):
    1. Mỗi phía, nhúng mọi đoạn liên tiếp tối đa `max_side` câu (Hán nối liền, Việt nối bằng dấu cách).
    2. Quy hoạch động đơn điệu (src/alignment/dp.py) trên các bước a-b, 1 <= a,b <= max_side, a+b <= max_total,
       cộng 1-0 / 0-1 (gắn cờ "unaligned"). Cực đại tổng điểm bead.
    Bỏ bước neo 1-1 lần đầu của Bertalign gốc: mỗi tài liệu ở đây <= ~130 câu, DP có dải (band) chạy thẳng được.

Điểm một bead a-b (a,b >= 1):
    cos(E_han[i0:i1], E_nghia[j0:j1])                     embedding (LaBSE | bge-m3)
  + bridge_weight * overlap(âm[i0:i1], nghia[j0:j1])       cầu nối âm (0 = tắt)
  + len_weight * (log2(1 + min(r, 1/r)) - 1)               tiên nghiệm độ dài, trong [-len_weight, 0]
        r = (số âm tiết nghĩa / c) / số chữ Hán, c = tỉ lệ toàn work (prepare) hoặc toàn tài liệu
  - merge_penalty * (a + b - 2)
Bead một phía: điểm `skip` (âm nhỏ). Bead.score xuất ra = điểm trên (không gồm phạt gộp) kẹp về [0,1];
chỉ để xếp hạng rà soát, KHÔNG so được giữa các mô hình.

Cầu nối âm: với mỗi đoạn Hán, lấy câu âm tương ứng từ 06_alignment/han-am.xml (bead chứa ID câu Hán).
Trùng từ = Dice có trọng số trên n-gram âm tiết (n = 1..3) sau khi bỏ dấu + chữ thường; trọng số
= ngram_weights[n] * IDF (IDF tính trên các câu nghĩa của work). Cụm 2-3 âm tiết (tên riêng, niên hiệu,
địa danh) giữ nguyên trong bản dịch nghĩa nên được trọng số cao hơn.
"""

from __future__ import annotations

import logging
import math
from collections import Counter
from pathlib import Path

import numpy as np

from src.alignment.aligners import (Aligner, han_chars, one_sided_beads, register_aligner, strip_tone,
                                    vi_syllables)
from src.alignment.dp import monotonic_align
from src.common.sentences import Bead, Sentence

log = logging.getLogger("bertalign")


def bead_moves(max_side: int, max_total: int) -> tuple[tuple[int, int], ...]:
    moves = [(a, b) for a in range(1, max_side + 1) for b in range(1, max_side + 1) if a + b <= max_total]
    moves.sort(key=lambda m: (m[0] + m[1], m))
    return tuple(moves) + ((1, 0), (0, 1))


def bare_tokens(text: str) -> list[str]:
    return [strip_tone(t) for t in vi_syllables(text)]


def ngrams(tokens: list[str], max_n: int) -> Counter:
    out: Counter = Counter()
    for n in range(1, max_n + 1):
        for k in range(len(tokens) - n + 1):
            out[" ".join(tokens[k:k + n])] += 1
    return out


class OverlapScorer:
    """Dice có trọng số IDF giữa n-gram âm (bỏ dấu) của câu âm và câu nghĩa."""

    def __init__(self, corpus: list[str], ngram_weights: tuple[float, ...]):
        self.nw = ngram_weights
        self.max_n = len(ngram_weights)
        df: Counter = Counter()
        for text in corpus:
            df.update(set(ngrams(bare_tokens(text), self.max_n)))
        self.df, self.n_docs = df, max(len(corpus), 1)
        self._w: dict[str, float] = {}
        self._prof: dict[str, tuple[frozenset, float]] = {}

    def weight(self, gram: str) -> float:
        w = self._w.get(gram)
        if w is None:
            n = gram.count(" ") + 1
            w = self._w[gram] = self.nw[n - 1] * math.log((self.n_docs + 1) / (self.df.get(gram, 0) + 1) + 1)
        return w

    def profile(self, text: str) -> tuple[frozenset, float]:
        """(tập n-gram, tổng trọng số) của một văn bản; có cache (DP hỏi lại cùng đoạn rất nhiều lần)."""
        prof = self._prof.get(text)
        if prof is None:
            grams = frozenset(ngrams(bare_tokens(text), self.max_n))
            prof = self._prof[text] = (grams, sum(self.weight(g) for g in grams))
        return prof

    def score(self, am_text: str, nghia_text: str) -> float:
        a, wa = self.profile(am_text)
        b, wb = self.profile(nghia_text)
        if not a or not b:
            return 0.0
        small, big = (a, b) if len(a) <= len(b) else (b, a)
        shared = sum(self.weight(g) for g in small if g in big)
        return 2 * shared / (wa + wb) if wa + wb else 0.0


def length_units(layer: str, text: str) -> int:
    return len(han_chars(text)) if layer == "han" else len(vi_syllables(text))


def bridge_map(path: Path) -> dict[str, list[tuple[int, str]]]:
    """ID câu Hán -> [(thứ tự bead, văn bản âm)] từ file dóng han-am (bỏ bead unaligned / không có âm)."""
    from src.alignment.serialization import read_alignments
    out: dict[str, list[tuple[int, str]]] = {}
    for k, bead in enumerate(read_alignments(path)):
        if not bead["tgt_ids"] or not bead["src_ids"]:
            continue
        for sid in bead["src_ids"]:
            out.setdefault(sid, []).append((k, bead["tgt_text"]))
    return out


@register_aligner
class BertalignAligner(Aligner):
    """Bertalign tự cài + tín hiệu cầu nối âm tuỳ chọn (xem docstring module)."""
    name = "bertalign"
    options = {
        "model": (str, "bge-m3"), "device": (str, "auto"), "fp16": (int, 1), "batch_size": (int, 32),
        "max_length": (int, 256), "cache_dir": (str, ""), "gpu_busy_mb": (int, 1024),
        "max_side": (int, 4), "max_total": (int, 8), "band": (int, 30),
        "skip": (float, -0.1), "len_weight": (float, 0.5), "merge_penalty": (float, 0.0),
        "length_ratio": (float, 0.0),
        "bridge_weight": (float, 0.0), "bridge_file": (str, ""), "ngram_weights": (str, "1,2,3"),
    }

    def __init__(self, **opts):
        super().__init__(**opts)
        self._encoder = None
        self.work_ratio: float | None = self.opts["length_ratio"] or None
        self.bridge: dict[str, list[tuple[int, str]]] | None = None
        self.overlap: OverlapScorer | None = None

    def describe(self) -> dict:
        d = super().describe()
        d["work_length_ratio"] = self.work_ratio
        return d

    @property
    def encoder(self):
        if self._encoder is None:
            from src.alignment.embeddings import CachedEncoder
            o = self.opts
            self._encoder = CachedEncoder(o["model"], o["cache_dir"], device=o["device"], fp16=bool(o["fp16"]),
                                          max_length=o["max_length"], batch_size=o["batch_size"],
                                          gpu_busy_mb=o["gpu_busy_mb"])
        return self._encoder

    # ------------------------------------------------------------------ #
    def prepare(self, layout, src_layer: str, tgt_layer: str, src: list[Sentence], tgt: list[Sentence]) -> None:
        """Gọi MỘT lần cho cả work trước khi dóng từng tài liệu: tỉ lệ độ dài theo work, nạp cầu nối âm."""
        if self.work_ratio is None:
            docs_src = Counter()
            docs_tgt = Counter()
            for s in src:
                docs_src[s.document_id] += length_units(src_layer, s.text)
            for t in tgt:
                docs_tgt[t.document_id] += length_units(tgt_layer, t.text)
            both = [d for d in docs_src if docs_src[d] and docs_tgt.get(d)]
            ls, lt = sum(docs_src[d] for d in both), sum(docs_tgt[d] for d in both)
            if ls and lt:
                self.work_ratio = lt / ls
                log.info("Tỉ lệ độ dài %s/%s theo work = %.3f (%d tài liệu)", tgt_layer, src_layer,
                         self.work_ratio, len(both))
        if self.opts["bridge_weight"] > 0:
            if src_layer != "han":
                raise ValueError("Cầu nối âm chỉ dùng khi nguồn là lớp han")
            from src.common.sentences import find_alignment_file
            path = Path(self.opts["bridge_file"]) if self.opts["bridge_file"] else find_alignment_file(layout, "han-am")
            if not path.exists():
                raise ValueError(f"bridge_weight > 0 nhưng không có file dóng han-am: {path}")
            self.bridge = bridge_map(path)
            covered = sum(1 for s in src if s.id in self.bridge)
            log.info("Cầu nối âm từ %s: %d/%d câu Hán có âm", path, covered, len(src))
            weights = tuple(float(x) for x in self.opts["ngram_weights"].split(","))
            self.overlap = OverlapScorer([t.text for t in tgt], weights)

    def _am_text(self, ids: list[str]) -> str:
        seen, parts = set(), []
        for sid in ids:
            for k, text in (self.bridge or {}).get(sid, []):
                if k not in seen:
                    seen.add(k)
                    parts.append((k, text))
        return " ".join(t for _, t in sorted(parts))

    # ------------------------------------------------------------------ #
    def align(self, src_layer, tgt_layer, src, tgt):
        o = self.opts
        n, m = len(src), len(tgt)
        if n == 0 or m == 0:
            return one_sided_beads(n, m)
        ls = [length_units(src_layer, s.text) for s in src]
        lt = [length_units(tgt_layer, t.text) for t in tgt]
        ratio = self.work_ratio or (sum(lt) / sum(ls) if sum(ls) and sum(lt) else 1.0)
        k_max = o["max_side"]
        moves = bead_moves(k_max, o["max_total"])

        def spans(items, joiner):
            keys, texts = [], []
            for i in range(len(items)):
                for k in range(1, k_max + 1):
                    if i + k <= len(items):
                        keys.append((i, i + k))
                        texts.append(joiner.join(x.text for x in items[i:i + k]))
            return keys, texts

        sk, st = spans(src, "")
        tk, tt = spans(tgt, " ")
        vecs = self.encoder.encode(st + tt)
        es = {k: vecs[x] for x, k in enumerate(sk)}
        et = {k: vecs[len(sk) + x] for x, k in enumerate(tk)}
        use_bridge = self.overlap is not None and o["bridge_weight"] > 0
        ov_cache: dict[tuple[int, int, int, int], float] = {}
        cache: dict[tuple[int, int, int, int], float] = {}

        def overlap(i0, i1, j0, j1):
            key = (i0, i1, j0, j1)
            if key not in ov_cache:
                am = self._am_text([s.id for s in src[i0:i1]])
                ov_cache[key] = self.overlap.score(am, " ".join(t.text for t in tgt[j0:j1])) if am else 0.0
            return ov_cache[key]

        def bead_score(i0, i1, j0, j1):
            key = (i0, i1, j0, j1)
            if key not in cache:
                sim = float(np.dot(es[(i0, i1)], et[(j0, j1)]))
                a, b = sum(ls[i0:i1]), sum(lt[j0:j1]) / ratio
                prior = (math.log2(1 + min(a, b) / max(a, b)) - 1) if a and b else -1.0
                s = sim + o["len_weight"] * prior
                if use_bridge:
                    s += o["bridge_weight"] * overlap(i0, i1, j0, j1)
                cache[key] = s
            return cache[key]

        def cost(i0, i1, j0, j1):
            a, b = i1 - i0, j1 - j0
            if a == 0 or b == 0:
                return -o["skip"]
            return -(bead_score(i0, i1, j0, j1) - o["merge_penalty"] * (a + b - 2))

        path = monotonic_align(n, m, cost, moves, o["band"])
        beads = []
        for i0, i1, j0, j1 in path:
            s_idx, t_idx = tuple(range(i0, i1)), tuple(range(j0, j1))
            if not s_idx or not t_idx:
                beads.append(Bead(s_idx, t_idx, 0.0, ("unaligned",)))
                continue
            beads.append(Bead(s_idx, t_idx, min(1.0, max(0.0, bead_score(i0, i1, j0, j1)))))
        return beads
