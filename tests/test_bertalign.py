"""Aligner bertalign: DP n-m, tiên nghiệm độ dài, cầu nối âm, cache embedding (không cần mô hình thật)."""

import numpy as np
import pytest

from src.alignment import embeddings
from src.alignment.aligners import ALIGNERS
from src.alignment.bertalign import OverlapScorer, bead_moves, bridge_map
from src.alignment import serialization
from src.common.sentences import Sentence


class FakeEncoder:
    """Vector theo 'chủ đề': mỗi câu mang nhãn [tN] -> one-hot N; đoạn nối = trung bình các nhãn."""

    def __init__(self, dim=16):
        self.dim, self.calls = dim, 0

    def encode(self, texts):
        self.calls += 1
        out = []
        for t in texts:
            v = np.zeros(self.dim, dtype=np.float32)
            for k in range(self.dim):
                if f"[t{k}]" in t:
                    v[k] = 1.0
            v = v if v.any() else np.ones(self.dim, dtype=np.float32)
            out.append(v / np.linalg.norm(v))
        return np.stack(out)


def sents(layer, texts, doc="W.001"):
    return [Sentence(f"W.{layer}.{k + 1:05d}", t, [1], document_id=doc) for k, t in enumerate(texts)]


def make(**opts):
    al = ALIGNERS["bertalign"](**opts)
    al._encoder = FakeEncoder()
    return al


def check_contract(beads, n, m):
    assert [i for b in beads for i in b.src] == list(range(n))
    assert [j for b in beads for j in b.tgt] == list(range(m))


def test_moves_respect_limits():
    moves = bead_moves(4, 8)
    assert (4, 4) in moves and (1, 0) in moves and (0, 1) in moves
    assert all(a <= 4 and b <= 4 for a, b in moves)
    assert (2, 3) not in bead_moves(4, 4)


def test_one_to_two_and_two_to_one():
    src = sents("han", ["[t1]甲乙丙丁", "[t2]戊己庚辛壬癸", "[t3]子丑", "[t3]寅卯"])
    tgt = sents("nghia", ["[t1] một hai ba bốn", "[t2] năm sáu", "[t2] bảy tám chín mười", "[t3] tý sửu dần mão"])
    beads = make(len_weight=0.0).align("han", "nghia", src, tgt)
    check_contract(beads, 4, 4)
    assert [(b.src, b.tgt) for b in beads] == [((0,), (0,)), ((1,), (1, 2)), ((2, 3), (3,))]


def test_extra_target_is_flagged_unaligned():
    src = sents("han", ["[t1]甲乙", "[t2]丙丁"])
    tgt = sents("nghia", ["[t1] giáp ất", "[t9] chú thích lạc chỗ", "[t2] bính đinh"])
    beads = make(len_weight=0.0, skip=-0.05).align("han", "nghia", src, tgt)
    check_contract(beads, 2, 3)
    assert any(b.tgt == (1,) and not b.src and "unaligned" in b.flags for b in beads)


def test_empty_side():
    beads = make().align("han", "nghia", sents("han", ["甲"]), [])
    assert [(b.src, b.tgt, b.flags) for b in beads] == [((0,), (), ("unaligned",))]


def test_prepare_work_ratio():
    al = make()
    src = sents("han", ["甲乙丙丁"], "W.001") + sents("han", ["戊己"], "W.002")
    tgt = sents("nghia", ["a b c d e f g h"], "W.001") + sents("nghia", ["x y z w"], "W.002")
    al.prepare(None, "han", "nghia", src, tgt)
    assert al.work_ratio == pytest.approx(12 / 6)
    assert al.describe()["work_length_ratio"] == pytest.approx(2.0)


def test_overlap_prefers_shared_names():
    corpus = ["Ngày 20 tháng 2 lên đường", "đến Yên Kinh", "qua sông Nhị Hà", "trời mưa to"]
    sc = OverlapScorer(corpus, (1.0, 2.0, 3.0))
    am = "Nhị thập nhật khởi trình, để Yên Kinh."
    assert sc.score(am, "đến Yên Kinh") > sc.score(am, "qua sông Nhị Hà")
    assert sc.score(am, "trời mưa to") == 0.0
    assert sc.score("", "x") == 0.0


def test_bridge_breaks_embedding_tie(tmp_path):
    # Embedding không phân biệt được (cùng nhãn): câu nghĩa giữa thuộc câu Hán nào là hoà; cầu nối âm
    # (tên riêng Yên Kinh) kéo nó về câu Hán đầu.
    src = sents("han", ["[t1]至燕京留燕京", "[t1]過珥河"])
    tgt = sents("nghia", ["[t1] đến Yên Kinh", "[t1] ở lại Yên Kinh", "[t1] qua sông Nhị Hà"])
    am = sents("am", ["Chí Yên Kinh, lưu Yên Kinh.", "Quá Nhị Hà."])
    rows = [{"id": f"W.han-am.{k:05d}", "document_id": "W.001", "kind": "1-1", "score": 1.0, "flags": [],
             "src_ids": [src[k].id], "tgt_ids": [am[k].id], "pages": {"han": [1], "am": [1]},
             "src_text": src[k].text, "tgt_text": am[k].text} for k in range(2)]
    path = tmp_path / "han-am.xml"
    serialization.write_alignments(path, "W", "han-am", "han", "am", rows)
    assert set(bridge_map(path)) == {src[0].id, src[1].id}
    al = make(len_weight=0.0, bridge_weight=1.0, bridge_file=str(path))
    al.prepare(None, "han", "nghia", src, tgt)
    beads = al.align("han", "nghia", src, tgt)
    assert [(b.src, b.tgt) for b in beads] == [((0,), (0, 1)), ((1,), (2,))]
    assert al._am_text([src[0].id, src[1].id]) == "Chí Yên Kinh, lưu Yên Kinh. Quá Nhị Hà."


def test_bridge_requires_file(tmp_path):
    al = make(bridge_weight=0.5, bridge_file=str(tmp_path / "missing.xml"))
    with pytest.raises(ValueError):
        al.prepare(None, "han", "nghia", sents("han", ["甲"]), sents("nghia", ["a"]))


def test_unknown_option_rejected():
    with pytest.raises(ValueError):
        ALIGNERS["bertalign"](nope=1)


def test_embedding_cache_roundtrip(tmp_path, monkeypatch):
    fake = FakeEncoder()
    monkeypatch.setattr(embeddings, "SentenceEncoder", lambda model, **kw: fake)
    enc = embeddings.CachedEncoder("labse", tmp_path, max_length=64)
    a = enc.encode(["[t1] x", "[t2] y", "[t1] x"])
    assert fake.calls == 1 and enc.cache.misses == 2
    enc2 = embeddings.CachedEncoder("sentence-transformers/LaBSE", tmp_path, max_length=64)
    b = enc2.encode(["[t2] y", "[t1] x"])
    assert enc2._encoder is None                     # toàn bộ từ cache, không nạp mô hình
    assert np.allclose(b[0], a[1], atol=1e-3) and np.allclose(b[1], a[0], atol=1e-3)
    assert (tmp_path / "LaBSE_ml64.sqlite").exists()
