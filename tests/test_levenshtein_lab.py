"""Aligner levenshtein với từ điển của Lab (giai đoạn 2): không đổi hành vi khi không truyền tuỳ chọn mới."""
import pytest

from src.alignment import aligners
from src.alignment.aligners import ALIGNERS, merge_readings
from src.common.sentences import Sentence


@pytest.fixture
def dicts(tmp_path):
    unihan = tmp_path / "unihan.tsv"
    unihan.write_text("天\tthiên\n地\tđịa\n", encoding="utf-8")
    lab = tmp_path / "lab.csv"
    lab.write_text('﻿"QuocNgu","SinoNom"\n"thiên","天"\n"huyền","玄"\n"hoàng","黃"\n', encoding="utf-8")
    sim = tmp_path / "sim.csv"
    sim.write_text('﻿"Input Character","Top 20 Similar Characters"\n"王","[\'玉\', \'黃\']"\n"壞","not a list"\n',
                   encoding="utf-8")
    aligners._LAB_CACHE.clear()
    return {"reading_dict": str(unihan), "lab_translation_dict": str(lab), "lab_similarity_dict": str(sim)}


def sents(texts, prefix):
    return [Sentence(id=f"{prefix}{i}", text=t, pages=[1]) for i, t in enumerate(texts)]


def scores(opts, han, am):
    beads = ALIGNERS["levenshtein"](**opts).align("han", "am", sents(han, "h"), sents(am, "a"))
    return [(b.src, b.tgt, round(b.score, 4), b.flags) for b in beads]


def test_unihan_only_unchanged_when_lab_options_empty(dicts):
    han, am = ["天地", "玄黃"], ["Thiên địa", "Huyền hoàng"]
    base = scores({"reading_dict": dicts["reading_dict"]}, han, am)
    same = scores({"reading_dict": dicts["reading_dict"], "lab_translation_dict": "", "lab_similarity_dict": ""},
                  han, am)
    assert base == same


def test_lab_dict_adds_coverage_and_works_without_unihan(dicts, caplog):
    han, am = ["玄黃"], ["Huyền hoàng"]
    unihan_only = scores({"reading_dict": dicts["reading_dict"]}, han, am)
    union = scores({"reading_dict": dicts["reading_dict"], "lab_translation_dict": dicts["lab_translation_dict"]},
                   han, am)
    lab_only = scores({"lab_translation_dict": dicts["lab_translation_dict"]}, han, am)
    assert unihan_only[0][2] < union[0][2] == lab_only[0][2] == 1.0
    caplog.set_level("INFO")
    scores({"reading_dict": dicts["reading_dict"], "lab_translation_dict": dicts["lab_translation_dict"]},
           ["天玄"], ["thiên huyền"])
    assert "Từ điển Unihan phủ 50.0%" in caplog.text and "Từ điển Lab phủ 100.0%" in caplog.text


def test_similarity_dict_gives_similar_cost(dicts):
    # OCR đọc 黃 thành 王 (không có trong từ điển): có tập tương tự -> chi phí similar_cost thay vì unknown_cost
    opts = {"lab_translation_dict": dicts["lab_translation_dict"]}
    without = scores(opts, ["玄王"], ["huyền hoàng"])[0][2]
    with_sim = scores({**opts, "lab_similarity_dict": dicts["lab_similarity_dict"]}, ["玄王"], ["huyền hoàng"])[0][2]
    assert with_sim == pytest.approx(1 - 0.2 / 2) and with_sim > without
    custom = scores({**opts, "lab_similarity_dict": dicts["lab_similarity_dict"], "similar_cost": 0.4},
                    ["玄王"], ["huyền hoàng"])[0][2]
    assert custom == pytest.approx(1 - 0.4 / 2)


def test_requires_some_dictionary():
    with pytest.raises(ValueError, match="reading_dict"):
        ALIGNERS["levenshtein"]().align("han", "am", sents(["天"], "h"), sents(["thiên"], "a"))


def test_merge_readings_union_keeps_order():
    assert merge_readings({"天": ("thiên",)}, {"天": ("thiên", "thiến"), "玄": ("huyền",)}) == \
        {"天": ("thiên", "thiến"), "玄": ("huyền",)}


def test_bad_similarity_rows_skipped(dicts):
    sim = aligners.load_lab_similar(dicts["lab_similarity_dict"])
    assert sim == {"王": ("玉", "黃")}
