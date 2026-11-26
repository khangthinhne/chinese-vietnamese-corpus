"""scripts/audit_segmentation.py: các phép đo trên dữ liệu giả nhỏ."""
import importlib.util
from pathlib import Path

_spec = importlib.util.spec_from_file_location(
    "audit_segmentation", Path(__file__).resolve().parents[1] / "scripts" / "audit_segmentation.py")
audit = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(audit)


def seg(doc, layer, idx, text, page=1, sids=None):
    return {"id": f"{doc}.P{page:04d}.L{idx:03d}", "document_id": doc, "layer": layer, "page": page, "text": text,
            "source_ids": sids or [f"{'han' if layer == 'han' else 'vie'}.P{page:04d}.L{idx:03d}"]}


def test_counts_han_units_and_syllables():
    assert audit.han_units("講室之□。") == 4
    assert audit.han_units("花銀[MASK][MASK]，") == 4
    assert audit.syllables("Cao sơn lưu thủy 2 hữu tri âm,") == 7


def test_account_lines_lost_and_double():
    lines = {"han.P0001.L001": {"page": 1, "stream": "han", "text": "一二"},
             "vie.P0001.L001": {"page": 1, "stream": "vie", "text": "Nhất nhị"},
             "vie.P0001.L002": {"page": 1, "stream": "vie", "text": "PHIÊN ÂM:"}}
    dest = {"han.P0001.L001": ["layer:han"], "vie.P0001.L002": ["heading", "heading", "layer:am"]}
    r = audit.account_lines(lines, dest)
    assert r["lost"] == 1 and r["lost_by_stream"] == {"vie": 1}
    assert r["double"] == 1


def test_metadata_title_spanning_two_lines_maps_both():
    lines = {"vie.P0021.L001": {"page": 21, "stream": "vie", "text": "SƠ VĂN TÔN TỔNG ĐỐC ¹"},
             "vie.P0021.L002": {"page": 21, "stream": "vie", "text": "THOÁI SƯ XƯƠNG GIANG ² NGẪU PHÚ"}}
    md = [{"document_id": "D", "metadata": {"title": {"am": [{"text": "SƠ VĂN TÔN TỔNG ĐỐC ¹ THOÁI SƯ XƯƠNG "
                                                                     "GIANG ² NGẪU PHÚ", "pages": [21]}]}},
           "uncertain": {}}]
    assert set(audit.metadata_source_ids(md, lines)) == {"vie.P0021.L001", "vie.P0021.L002"}


def test_leakage_kinds():
    rows = {"han": [seg("D", "han", 1, "春江 abc")],
            "am": [seg("D", "am", 2, "Cao sơn lưu thủy 2 hữu tri âm,"), seg("D", "am", 3, "1. Nguyên dẫn: Vừa qua"),
                   seg("D", "am", 4, "Xuân giang 春"), seg("D", "am", 5, "205")]}
    counts = audit.leakage(rows)["counts"]
    assert counts["han"] == {"latin": 1}
    assert counts["am"] == {"inline_note_digit": 1, "note_entry": 1, "cjk": 1, "page_number": 1}


def test_han_am_consistency_verse_lines_and_title_variant():
    han = [seg("D", "han", 1, "寄示弟姪")] + [seg("D", "han", k, "誰教口腹役身忙") for k in range(2, 4)]
    am = [seg("D", "am", k, "Thùy giao khẩu phúc dịch thân mang,") for k in range(1, 3)]
    r = audit.han_am_consistency(han, am)
    assert r["exact"] == 1 and r["per_doc"]["D"]["verse_line_match"] == 1.0


def test_poem_forms():
    assert audit.poem_form([7, 7, 7, 7]) == "that_ngon"
    assert audit.poem_form([6, 8, 6, 8]) == "luc_bat"
    assert audit.poem_form([7, 7, 6, 8]) == "song_that_luc_bat"
    assert audit.poem_form([7, 12]) == "mixed"


def test_tho_odd_lines_skip_credit_and_notes():
    rows = [seg("D", "tho", k, "Núi sông xứ Lạng kế liền nhau,") for k in range(1, 5)]
    rows.append(seg("D", "tho", 5, "1. Trúc tín: Tin trúc, tin báo bình an từ phương xa gửi về"))
    r = audit.tho_lines(rows)
    assert r["forms"] == {"that_ngon": 1} and r["odd_lines"] == 0


def test_document_checks_missing_and_repeated_layers():
    data = {"documents": [{"document_id": "A", "pages": [1], "layers": {"han": 2, "am": 0}},
                          {"document_id": "B", "pages": [2], "layers": {"han": 1, "am": 2}}],
            "order": {"han.P0002.L001": 0, "vie.P0002.L001": 1, "vie.P0002.L002": 3, "han.P0002.L002": 2},
            "layers": {"han": [seg("B", "han", 1, "一", 2)],
                       "am": [seg("B", "am", 1, "a", 2), seg("B", "am", 2, "b", 2)], "nghia": [], "tho": []}}
    data["layers"]["han"][0]["source_ids"] = ["han.P0002.L001"]
    data["layers"]["am"][0]["source_ids"] = ["vie.P0002.L001"]
    data["layers"]["am"][1]["source_ids"] = ["vie.P0002.L002"]
    r = audit.document_checks(data, optional={"tho"})
    assert {"document_id": "A", "problem": "han_without_am", "pages": [1]} in r["missing_layers"]
    assert r["repeated_layers"] == [] and r["order_anomalies"] == []


def test_toc_parse_and_match_with_offset():
    lines = {f"vie.P0900.L{k:03d}": {"page": 900, "stream": "vie", "text": t}
             for k, t in enumerate(["MỤC LỤC", "Lạng Sơn đạo trung ........ 11", "Đăng Mẫu Tử sơn ..... 15",
                                    "Thụ hàng thành 20"], 1)}
    pages, entries = audit.parse_toc(lines)
    assert pages == [900] and [e["printed"] for e in entries] == [11, 15, 20]
    docs = [{"document_id": "D1", "pages": [15, 16]}, {"document_id": "D2", "pages": [19]},
            {"document_id": "D3", "pages": [24]}]
    titles = {"D1": ["LẠNG SƠN ĐẠO TRUNG"], "D2": ["ĐĂNG MẪU TỬ SƠN"], "D3": ["THỤ HÀNG THÀNH"]}
    r = audit.match_toc(entries, docs, titles, covered=set(range(1, 30)))
    assert r["offset"] == 4 and r["found"] == 3 and not r["missing"]


def test_bead_rates_and_ranking():
    beads = [{"document_id": "D", "kind": "1-1", "score": 0.9}, {"document_id": "D", "kind": "0-1", "score": 0},
             {"document_id": "D", "kind": "1-1", "score": 0.2}]
    rates = audit.bead_rates(beads)
    assert rates["D"] == {"beads": 3, "valid": 1, "rate": 0.333}
    checks = {"missing_layers": [{"document_id": "D", "problem": "han_without_am"}], "order_anomalies": [],
              "repeated_layers": []}
    top = audit.rank_suspects([{"document_id": "D", "pages": [1]}, {"document_id": "E", "pages": [2]}], checks,
                              {"per_doc": {}}, {}, rates, audit.Counter(), top=5)
    assert [t["document_id"] for t in top] == ["D"] and top[0]["score"] == 5.0


def test_stratified_sample_is_deterministic():
    docs = [{"document_id": f"D{k}", "pages": [k]} for k in range(20)]
    sus = [{"document_id": "D3"}, {"document_id": "D7"}]
    a = audit.stratified_sample(docs, sus, 5, seed=1)
    b = audit.stratified_sample(docs, sus, 5, seed=1)
    assert a == b and len(a["random"]) == 5 and "D3" not in [x["document_id"] for x in a["random"]]
