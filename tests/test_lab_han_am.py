"""Wrapper tool dóng Hán–Âm của Lab. Unit test không cần tool; hồi quy với data/sample-data.csv cần tool + từ điển."""
import csv
import importlib.util
import json
from pathlib import Path

import pytest

from src.alignment import lab_han_am as lab

TOOL = Path("third_party/SN-QN-alignment")
S2T = {"数": "數", "于": "於", "谢": "謝"}


def conv(ch):
    return S2T.get(ch, ch)


def bead(i, han, am, flags=(), kind="1-1", src=("h",), tgt=("a",)):
    return {"id": f"W.han-am.{i:05d}", "document_id": "W.001", "kind": kind, "score": 0.9, "flags": list(flags),
            "src_ids": list(src), "tgt_ids": list(tgt), "pages": {"han": [1], "am": [1]},
            "src_text": han, "tgt_text": am}


# --------------------------------------------------------------------------- làm sạch
def test_clean_hannom_strips_punct_and_space_keeps_mask():
    assert lab.clean_hannom("夫道處清虛，四大理常。 (1) 「妙」") == "夫道處清虛四大理常1妙"
    assert lab.clean_hannom("天[MASK]玄，黃") == "天[MASK]玄黃"
    assert lab.hannom_units("天[MASK]玄黃") == 4


def test_clean_dicham_single_spaces_nfc_keeps_numbers():
    import unicodedata
    raw = unicodedata.normalize("NFD", "Nhị Lạng giang,  sơn – cảnh “giới” liên; 12 (a).")
    assert lab.clean_dicham(raw) == "Nhị Lạng giang sơn cảnh giới liên 12 a"


def test_to_traditional_only_for_chars_missing_from_lab_dict_and_reversible():
    text, changes = lab.to_traditional_hannom("数于谢[MASK]", known={"數", "于", "於"}, converter=conv)
    assert text == "數于谢[MASK]"                       # 于 có sẵn -> giữ; 謝 không có trong từ điển -> giữ 谢
    assert changes == [(0, "数", "數")]
    text, changes = lab.to_traditional_hannom("数于", converter=conv)   # không có từ điển: đổi mọi chữ
    assert text == "數於" and len(changes) == 2


# --------------------------------------------------------------------------- bead -> CSV
def test_beads_to_rows_drops_unaligned_joins_nm_and_records_originals():
    beads = [bead(1, "二諒江山境界連", "Nhị Lạng giang sơn cảnh giới liên,"),
             bead(2, "", "Trích Dật thi", flags=["unaligned"], src=()),
             bead(3, "数路，旁通", "Số lộ, bàng thông.", kind="2-1", src=("h2", "h3")),
             bead(4, "天地", "Thiên địa huyền hoàng vũ trụ")]
    out = lab.beads_to_rows(beads, known={"數"}, converter=conv)
    assert [r["ID"] for r in out.rows] == ["W.han-am.00001", "W.han-am.00003", "W.han-am.00004"]
    assert out.unaligned == 1 and out.total == 4
    assert out.rows[1] == {"ID": "W.han-am.00003", "HanNom": "數路旁通", "DichAm": "Số lộ bàng thông",
                           "Fallback": "", "Bbox": ""}
    assert out.originals[1]["HanNom_original"] == "数路，旁通" and out.originals[1]["changed_chars"] == "0:数>數"
    assert out.length_warnings == ["W.han-am.00004: 2 chữ Hán / 6 âm tiết"]


def test_beads_to_rows_rejects_duplicate_ids():
    with pytest.raises(ValueError, match="trùng"):
        lab.beads_to_rows([bead(1, "天", "thiên"), bead(1, "地", "địa")], converter=conv)


def test_precheck_stops_on_empty_or_too_many_unaligned():
    with pytest.raises(ValueError, match="rỗng"):
        lab.precheck([])
    beads = [bead(1, "天", "thiên")] + [bead(i, "", "x", flags=["unaligned"]) for i in range(2, 4)]
    with pytest.raises(ValueError, match="unaligned"):
        lab.precheck(beads)
    lab.precheck([bead(1, "天", "thiên"), bead(2, "地", "địa"), bead(3, "玄", "huyền"),
                  bead(4, "", "x", flags=["unaligned"])])                    # 25% <= 30%: qua


# --------------------------------------------------------------------------- config
def test_build_config_uses_absolute_paths(tmp_path):
    tool = tmp_path / "tool"
    (tool / "configs/alignment_configs").mkdir(parents=True)
    (tool / "configs/alignment_configs/sample-config.yaml").write_text(
        "dataset:\n  name: s\n  input_file: SinoNom-alignment/data/x.csv\n  columns: {encoding: utf-8-sig}\n"
        "dictionary: {translation_dict: a, similarity_dict: b}\nalignment: {costs: {match: 0}}\n"
        "output: {directory: outputs, filename: x.tsv}\nquick_test: false\ntest_limit: 10\n", encoding="utf-8")
    cfg = lab.build_config(tool, tmp_path / "in.csv", tmp_path / "out", "CPO_19_parallel.tsv", quick=5)
    for value in (cfg["dataset"]["input_file"], cfg["dictionary"]["translation_dict"],
                  cfg["dictionary"]["similarity_dict"], cfg["output"]["directory"]):
        assert Path(value).is_absolute(), value
    assert cfg["dataset"]["columns"]["fallback_column"] is None
    assert cfg["alignment"]["damaged_tokens"] == ["[MASK]"]
    assert cfg["quick_test"] is True and cfg["test_limit"] == 5
    assert cfg["output"]["filename"] == "CPO_19_parallel.tsv"


def test_quality_warnings_and_review_rows():
    stats = {"summary": {"mismatch_rate": 0.2, "processed_rows": 2, "not_processed_rows": 0},
             "rows": [{"ID": "a", "status": "processed", "mismatch_rate": 0.5, "hannom_error_count": 2},
                      {"ID": "b", "status": "processed", "mismatch_rate": 0.0, "hannom_error_count": 0},
                      {"ID": "c", "status": "skipped_empty"}]}
    warns = lab.quality_warnings(stats, 3)
    assert any("mismatch_rate" in w for w in warns) and any("dòng a" in w for w in warns)
    assert any("dòng c bị tool bỏ qua" in w for w in warns)
    rows = lab.review_rows(stats, [{"ID": "a", "HanNom": "天_", "DichAm": "thiên địa"},
                                   {"ID": "b", "HanNom": "地", "DichAm": "địa"}], {})
    assert [r[0] for r in rows] == ["c", "a"]          # dòng bị bỏ qua (coi như 100% lỗi) lên đầu


def test_ocr_suggestions_from_similarity_positions():
    sugg = lab.ocr_suggestions({"r1": [(1, "未", "0000FF"), (0, "天", "FF0000")]},
                               [{"ID": "r1", "DichAm": "thiên mạt"}], {"r1": {"pages": "{}", "src_ids": "h"}},
                               readings={"末": {"mạt"}, "未": {"vị"}}, similar={"未": ["末", "朱"]})
    assert sugg == [{"ID": "r1", "index": 1, "ocr_char": "未", "suggested": "末", "syllable": "mạt",
                     "kind": "similarity", "pages": "{}", "src_ids": "h"}]


def test_check_tool_reports_missing_files(tmp_path):
    missing = lab.check_tool(tmp_path)
    assert any("align_texts.py" in m for m in missing) and any("QuocNgu_SinoNom_TongHop3.csv" in m for m in missing)


# --------------------------------------------------------------------------- hồi quy với tool thật
HAVE_TOOL = not lab.check_tool(TOOL) and all(importlib.util.find_spec(m) for m in ("pandas", "yaml", "xlsxwriter"))


@pytest.mark.skipif(not HAVE_TOOL, reason="cần third_party/SN-QN-alignment + 2 từ điển + pandas/PyYAML/XlsxWriter")
def test_regression_lab_sample_data(tmp_path):
    stats = lab.run_on_csv(TOOL / "data/sample-data.csv", tmp_path, "sample-data_align.tsv", TOOL)
    s = stats["summary"]
    assert (s["alignment_positions"], s["match_count"]) == (139, 134)
    assert round(s["match_rate"] * 100, 2) == 96.40 and round(s["mismatch_rate"] * 100, 2) == 3.60
    assert all((tmp_path / n).exists() for n in ("sample-data_align.tsv", "sample-data_align.xlsx",
                                                  "sample-data_align_color.xlsx", "statistic.json"))
