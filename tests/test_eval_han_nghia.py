"""scripts/eval_han_nghia.py: đồng thuận giữa cấu hình, đọc và chấm bộ chuẩn dóng tay."""

import importlib.util
from pathlib import Path

import openpyxl

from src.common.sentences import Sentence

spec = importlib.util.spec_from_file_location(
    "eval_han_nghia", Path(__file__).resolve().parents[1] / "scripts" / "eval_han_nghia.py")
ev = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ev)


def bead(src, tgt, text="", doc="W.001"):
    return {"id": f"b{src}{tgt}", "document_id": doc, "kind": f"{len(src)}-{len(tgt)}", "score": 0.5,
            "flags": [] if src and tgt else ["unaligned"], "src_ids": list(src), "tgt_ids": list(tgt),
            "src_text": "", "tgt_text": text}


def test_agreement_strict_and_loose():
    a = [bead(["h1"], ["n1"]), bead(["h2"], ["n2", "n3"])]
    b = [bead(["h1"], ["n1"]), bead(["h2"], ["n2"]), bead([], ["n3"])]
    r = ev.agreement(a, b)
    assert r["bead_f1"] == 2 * 1 / 5
    assert r["link_f1"] == 2 * 2 / 5
    assert r["row_agree"] == 0.5
    assert ev.agreement(a, a) == {"bead_f1": 1.0, "link_f1": 1.0, "row_agree": 1.0}


def _gold_xlsx(tmp_path, rows):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "DONG_HANG_LAM_VIEC"
    ws.append(["ID", "Trang", "Hán", "Dịch âm", "TT âm", "Dịch nghĩa"])
    for k, (han, nghia) in enumerate(rows, 1):
        ws.append([f"W.0001.{k:03d}", 1, han, "", "OK", nghia])
    path = tmp_path / "gold.xlsx"
    wb.save(path)
    return path


def test_gold_projection_merges_rows_sharing_a_pipeline_sentence(tmp_path):
    # Chuẩn: 4 hàng, đã sửa OCR (pipeline đọc 己 thành 巳) và tách khác pipeline:
    #  - hàng 1+2 nằm trong một câu Hán pipeline (h1), hàng 3, 4 mỗi hàng một câu Hán (h2, h3);
    #  - nghĩa hàng 3+4 nằm trong một câu nghĩa pipeline (n2); n3 là chú thích, không có trong chuẩn.
    path = _gold_xlsx(tmp_path, [("甲乙丙", "một hai ba"), ("丁戊己", "bốn năm sáu"),
                                 ("庚辛壬", "bảy tám chín"), ("癸子丑", "mười mười một mười hai")])
    han = [Sentence("h1", "甲乙丙丁戊巳", [1], document_id="W.001"), Sentence("h2", "庚辛壬", [1], document_id="W.001"),
           Sentence("h3", "癸子丑。", [1], document_id="W.001")]
    nghia = [Sentence("n1", "Một hai ba, bốn năm sáu.", [1], document_id="W.001"),
             Sentence("n2", "Bảy tám chín, mười mười một mười hai.", [1], document_id="W.001"),
             Sentence("n3", "(1) Chú thích không dịch.", [1], document_id="W.001")]
    rows = ev.read_gold_rows(path)
    assert [r["page"] for r in rows] == [1, 1, 1, 1]
    gold = ev.build_gold(rows, han, nghia)
    assert [(b["src_ids"], b["tgt_ids"]) for b in gold["beads"]] == [(["h1"], ["n1"]), (["h2", "h3"], ["n2"])]
    assert gold["stats"]["rows_nghia_unreachable"] == []

    perfect = [bead(["h1"], ["n1"]), bead(["h2", "h3"], ["n2"]), bead([], ["n3"])]
    r = ev.score_against_gold(perfect, gold["beads"])
    assert r["strict"]["f1"] == 1.0 and r["row_acc"] == 1.0
    # gắn chú thích n3 vào h3 và tách h2/h3: strict sai, loose vẫn khớp, link mất precision
    wrong = [bead(["h1"], ["n1"]), bead(["h2"], ["n2"]), bead(["h3"], ["n3"])]
    r = ev.score_against_gold(wrong, gold["beads"])
    assert r["strict"]["recall"] == 0.5 and r["strict"]["precision"] == 1 / 3
    assert r["loose"]["recall"] == 1.0
    assert r["link"]["precision"] == 2 / 3 and r["row_acc"] == 2 / 3


def test_gold_row_without_pipeline_nghia_is_unreachable(tmp_path):
    path = _gold_xlsx(tmp_path, [("甲乙丙", "một hai ba"), ("丁戊己", "bốn năm sáu")])
    han = [Sentence("h1", "甲乙丙", [1], document_id="W.001"), Sentence("h2", "丁戊己", [1], document_id="W.001")]
    nghia = [Sentence("n1", "Một hai ba.", [1], document_id="W.001")]
    gold = ev.build_gold(ev.read_gold_rows(path), han, nghia)
    assert [b["src_ids"] for b in gold["beads"]] == [["h1"]]
    assert gold["stats"]["rows_nghia_unreachable"] == ["W.0001.002"]
