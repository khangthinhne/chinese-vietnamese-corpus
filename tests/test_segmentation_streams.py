"""Segmentation với hai luồng han.xml (paddlevl) + vie.xml (qwen)."""
import json
from pathlib import Path

from src.common.corpus import DataLayout
from src.ocr import build_layers, raw_store
from src.segmentation import pipeline as seg_pipeline
from src.segmentation import serialization as segxml
from src.segmentation import streams
from src.segmentation.model import OcrLine

WORK = "T_STREAM"


def line(stream, page, idx, text, y):
    return OcrLine(page=page, index=idx, text=text, bbox=(0, y, 100, y + 10), writing_system=stream,
                   source_id=f"{stream}.P{page:04d}.L{idx:03d}")


# --------------------------------------------------------------------------- hàm thuần
def test_merge_streams_orders_by_y_and_keeps_internal_order():
    han = [line("han", 1, 1, "諒山道中", 100), line("han", 1, 2, "二諒江山境界連", 140)]
    vie = [line("vie", 1, 1, "PHIÊN ÂM:", 300), line("vie", 1, 2, "Nhị Lạng giang", 320),
           line("vie", 2, 1, "DỊCH NGHĨA:", 50)]
    merged = streams.merge_streams(han, vie)
    assert [ln.text for ln in merged] == ["諒山道中", "二諒江山境界連", "PHIÊN ÂM:", "Nhị Lạng giang", "DỊCH NGHĨA:"]
    assert [ln.index for ln in merged] == [1, 2, 3, 4, 1]                  # đánh lại theo thứ tự trong trang


def test_merge_streams_line_without_bbox_follows_previous_line_of_its_stream():
    han = [line("han", 1, 1, "甲", 100), OcrLine(page=1, index=2, text="乙", writing_system="han")]
    vie = [line("vie", 1, 1, "Giữa", 105)]
    assert [ln.text for ln in streams.merge_streams(han, vie)] == ["甲", "乙", "Giữa"]


def test_allowed_document_starts_only_first_line_of_han_block_and_skip_headers():
    merged = []
    for page in (1, 2, 3):
        merged.append(line("vie", page, 1, "NGÔ THÌ NHẬM TOÀN TẬP", 10))          # tiêu đề chạy
    merged = [merged[0], line("han", 1, 2, "諒山道中", 50), line("han", 1, 3, "二諒江山境界連", 60),
              merged[1], line("han", 2, 2, "旁通荆廣接高宣", 50),                  # bài thơ sang trang
              line("vie", 2, 3, "PHIÊN ÂM:", 80), line("han", 2, 4, "其二", 90), merged[2]]
    assert streams.allowed_document_starts(merged, min_block_lines=1) == {1, 6}
    assert streams.allowed_document_starts(merged) == {1}           # khối 1 dòng (其二) không mở tài liệu


def test_keep_line_routes_layers_to_streams():
    han, vie = line("han", 1, 1, "甲", 1), line("vie", 1, 1, "Giáp", 1)
    assert streams.keep_line("han", han) and not streams.keep_line("han", vie)
    assert streams.keep_line("am", vie) and not streams.keep_line("nghia", han)
    assert streams.keep_line("other", han)


# --------------------------------------------------------------------------- end-to-end nhỏ
def raw_run(engine, texts_y, key):
    return {"key": key, "parameters": {"image_sha256": "x", "engine": {"name": engine, "adapter_version": 1},
                                       "lang": "all", "region": None, "psm": 6, "dpi": 300,
                                       "preprocessing": "rgb-crop-v1"},
            "created_at": "2026-01-01T00:00:00+00:00", "image": "p.png", "image_size": [1000, 2000],
            "crop_box": [0, 0, 1000, 2000],
            "lines": [{"text": t, "bbox": [10, y, 900, y + 30], "confidence": 90.0} for t, y in texts_y],
            "raw": {"orientation": "horizontal",
                    "bands": [{"unit": i, "n_lines": 1, "flags": []} for i in range(len(texts_y))]}}


def write_fixture(root: Path) -> DataLayout:
    layout = DataLayout(root, WORK)
    # trang 1: bài 1 (Hán + phiên âm + khảo dị có trích chữ Hán); trang 2: hết nghĩa bài 1, bài 2 bắt đầu
    qwen = {1: [("NGÔ THÌ NHẬM TOÀN TẬP", 20), ("1. 諒山道中", 100), ("二諒江山境界連", 150),
                ("PHIÊN ÂM:", 300), ("Nhị Lạng giang sơn cảnh giới liên,", 350),
                ("DỊCH NGHĨA:", 500), ("Hai xứ Lạng núi sông liền cõi.", 550),
                ("a. Bản A.404: Hội khê 洄溪.", 600), ("18", 1900)],
            2: [("NGÔ THÌ NHẬM TOÀN TẬP", 20), ("Đường núi vút lên trời.", 100),
                ("2. 旁通荆廣", 300), ("旁通荆廣接高宣", 350),
                ("PHIÊN ÂM:", 500), ("Bàng thông Kinh, Quảng tiếp Cao, Tuyên.", 550), ("19", 1900)]}
    paddle = {1: [("NGO THI NHAM TOAN TAP", 20), ("1. 諒山道中", 100), ("二諒江山境界連", 150),
                  ("PHIEN AM:", 300), ("a. Bản A.404: Hội khê 洄溪.", 600), ("18", 1900)],
              2: [("2. 旁通荆廣", 300), ("旁通荆廣接高宣", 350), ("19", 1900)]}
    for page in (1, 2):
        path = layout.ocr_raw_unified_path(page)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"schema_version": 1, "work_id": WORK, "page": page,
                                    "runs": [raw_run("qwen", qwen[page], f"q{page}")]}, ensure_ascii=False),
                        encoding="utf-8")
        raw_store.save_engine_page(layout, "paddlevl", page, {
            "schema_version": 1, "work_id": WORK, "engine": "paddlevl", "page": page,
            "runs": [raw_run("paddlevl", paddle[page], f"p{page}")]})
    assert build_layers.main(["--work-id", WORK, "--data-root", str(root)]) == 0
    cfg = {"common": {"work_id": WORK, "data_root": str(root), "pages": "1-2"},
           "ocr": {"engine": "qwen"},
           "segmentation": {
               "ocr_streams": "han_vie",
               "document": {"start_patterns": [r"^\s*\d{1,3}\s*[.．]\s*[一-鿿]"]},
               "layers": {
                   "han": {"start_patterns": [r"^\s*\d{1,3}\s*[.．]\s*[一-鿿]"],
                           "end_patterns": [r"^\s*PHI[ÊE]N\s+ÂM"]},
                   "am": {"heading_mode": True, "start_patterns": [r"^\s*PHI[ÊE]N\s+ÂM"],
                          "end_patterns": [r"^\s*DỊCH\s+NGHĨA"]},
                   "nghia": {"heading_mode": True, "start_patterns": [r"^\s*DỊCH\s+NGHĨA"],
                             "end_patterns": [], "optional": True}}}}
    (root / "cfg.json").write_text(json.dumps(cfg, ensure_ascii=False), encoding="utf-8")
    return layout


def test_segmentation_uses_han_and_vie_streams(tmp_path):
    layout = write_fixture(tmp_path)
    assert seg_pipeline.main(["--config", str(tmp_path / "cfg.json"), "--work-id", WORK,
                              "--data-root", str(tmp_path)]) == 0
    docs = segxml.read_documents(layout.segmentation_dir / "documents.xml") \
        if hasattr(segxml, "read_documents") else None
    han = segxml.read_layer(layout.segmentation_layer("han"))
    am = segxml.read_layer(layout.segmentation_layer("am"))
    nghia = segxml.read_layer(layout.segmentation_layer("nghia"))
    # lớp han: chỉ dòng của han.xml; tiêu đề có số "1." được nhận nhờ RAW_TEXT
    assert all(sid.startswith("han.") for r in han for sid in r["source_ids"])
    assert [r["text"] for r in han] == ["1. 諒山道中", "二諒江山境界連", "2. 旁通荆廣", "旁通荆廣接高宣"]
    assert [r["document_id"] for r in han] == [f"{WORK}.001"] * 2 + [f"{WORK}.002"] * 2
    # lớp Việt: chỉ dòng của vie.xml; dòng khảo dị không còn chữ Hán; nghĩa bài 1 sang trang 2 vẫn thuộc bài 1
    assert all(sid.startswith("vie.") for r in am + nghia for sid in r["source_ids"])
    assert [r["text"] for r in am] == ["Nhị Lạng giang sơn cảnh giới liên,", "Bàng thông Kinh, Quảng tiếp Cao, Tuyên."]
    assert [r["document_id"] for r in am] == [f"{WORK}.001", f"{WORK}.002"]
    nghia_texts = [r["text"] for r in nghia]
    assert "Hai xứ Lạng núi sông liền cõi." in nghia_texts and "Đường núi vút lên trời." in nghia_texts
    assert all(r["document_id"] == f"{WORK}.001" for r in nghia)
    assert not any("洄溪" in r["text"] for r in han)                       # khảo dị không mở khối Hán
    meta = json.loads(layout.metadata_path.read_text(encoding="utf-8"))["layers"]["segmentation"]
    assert meta["sources"] == ["han", "vie"] and meta["ocr_streams"]["han_pages"] == [1, 2]
    assert meta["ocr_streams"]["han_rows_outside_document_pages"] == []


def test_streams_mode_is_opt_in(tmp_path):
    layout = write_fixture(tmp_path)
    cfg = json.loads((tmp_path / "cfg.json").read_text(encoding="utf-8"))
    del cfg["segmentation"]["ocr_streams"]
    (tmp_path / "cfg.json").write_text(json.dumps(cfg, ensure_ascii=False), encoding="utf-8")
    # không bật luồng và không có raw.xml -> chế độ cũ báo thiếu đầu vào, KHÔNG tự dùng han.xml/vie.xml
    assert seg_pipeline.main(["--config", str(tmp_path / "cfg.json"), "--work-id", WORK,
                              "--data-root", str(tmp_path)]) == 2
    assert not layout.segmentation_layer("han").exists()
    # bật bằng CLI
    assert seg_pipeline.main(["--config", str(tmp_path / "cfg.json"), "--work-id", WORK,
                              "--data-root", str(tmp_path), "--ocr-streams", "han_vie"]) == 0


def test_streams_mode_warns_han_without_am(tmp_path, caplog):
    rows = streams.pairing_review("W", [{"document_id": "W.001", "pages": [1], "layers": {"han": 4}},
                                        {"document_id": "W.002", "pages": [2], "layers": {"am": 3}},
                                        {"document_id": "W.003", "pages": [3], "layers": {"han": 1, "am": 1}}])
    assert [(r["document_id"], r["reason"]) for r in rows] == [("W.001", "han_without_am"),
                                                                 ("W.002", "am_without_han")]


def test_pipeline_plans_layers_step_only_when_streams_enabled():
    from src.pipeline import build_plan
    cfg = {"common": {"work_id": "W", "source": "x.pdf", "pages": "1-2", "data_root": "data"},
           "ocr": {"engine": "qwen"}, "segmentation": {"layers": {"han": {}, "am": {}}}}
    steps = ["ocr", "layers", "segment"]
    assert [s for s, _ in build_plan(cfg, steps=steps, layers=None, pairs=None, force=False, config_path="cfg.json")] == ["ocr", "segment"]
    cfg["segmentation"]["ocr_streams"] = "han_vie"
    plan = build_plan(cfg, steps=steps, layers=None, pairs=None, force=False, config_path="cfg.json")
    assert [s for s, _ in plan] == ["ocr", "layers", "segment"]
    assert plan[1][1] == ["--work-id", "W", "--data-root", "data"]


def test_merge_uses_bbox_top_for_tall_qwen_lines():
    # Qwen gộp nhiều dòng in: bbox 1080-1566 nằm TRÊN tiêu đề Hán (1221-1289) dù tâm của nó thấp hơn
    han = [OcrLine(page=15, index=1, text="1. 諒山道中", bbox=(0, 1221, 10, 1289), writing_system="han"),
           OcrLine(page=15, index=2, text="二諒江山境界連", bbox=(0, 1503, 10, 1563), writing_system="han")]
    vie = [OcrLine(page=15, index=1, text="cho đường đi sứ chăng?", bbox=(0, 1080, 10, 1566),
                   writing_system="vie")]
    assert [ln.text for ln in streams.merge_streams(han, vie)] == ["cho đường đi sứ chăng?", "1. 諒山道中",
                                                                  "二諒江山境界連"]


def test_document_config_starts_on_every_han_block_by_default():
    cfg = {"start_patterns": [r"^\d+\."], "numbered_heading": True}
    assert streams.document_config(cfg)["start_patterns"] == [r"\S"]
    assert streams.document_config({**cfg, "start_on_han_block": False}) == {**cfg, "start_on_han_block": False}


def test_implicit_am_after_han_block_without_heading():
    from src.segmentation.layers import assign_document_unified
    from src.segmentation.model import Document
    lines = [line("han", 12, 1, "歲晚題廳廨", 100), line("han", 12, 2, "江次聯排數四舟", 150),
             line("vie", 12, 3, "TUẾ VĂN ĐỀ THINH GIẢI", 300), line("vie", 12, 4, "Giang thứ liên bài số tứ chu,", 350),
             line("vie", 12, 5, "DỊCH NGHĨA:", 500), line("vie", 12, 6, "Thuyền đậu bên sông.", 550)]
    cfg = {"han": {"start_patterns": [r"^\s*[一-鿿]{2,12}\s*$"]},
           "am": {"heading_mode": True, "start_patterns": [r"^PHIÊN ÂM"]},
           "nghia": {"heading_mode": True, "start_patterns": [r"^DỊCH NGHĨA"]}}
    doc = Document(id="W.001", pages=[12], lines=lines)
    res = assign_document_unified("W", doc, cfg, {}, keep_line=streams.keep_line, dropped={},
                                  implicit_next={"han": "am"})
    assert [r.text for r in res.lines["am"]] == ["TUẾ VĂN ĐỀ THINH GIẢI", "Giang thứ liên bài số tứ chu,"]
    assert [r.text for r in res.lines["nghia"]] == ["Thuyền đậu bên sông."]
    assert any(r["reason"] == "implicit_layer_start" for r in res.review)
    # không bật implicit -> dòng Việt sau khối Hán bị bỏ như trước
    res = assign_document_unified("W", doc, cfg, {}, keep_line=streams.keep_line, dropped={})
    assert "am" not in res.lines


def test_implicit_am_not_triggered_by_running_header():
    from src.segmentation.layers import assign_document_unified
    from src.segmentation.model import Document
    lines = [line("han", 1, 1, "旁通荆廣接高宣", 100), line("vie", 2, 1, "NGÔ THÌ NHẬM TOÀN TẬP", 10),
             line("han", 2, 2, "溪流奔駛爭歸海", 100), line("vie", 2, 3, "Bàng thông Kinh,", 300)]
    cfg = {"han": {}, "am": {"heading_mode": True, "start_patterns": [r"^PHIÊN ÂM"]}}
    res = assign_document_unified("W", Document(id="W.001", pages=[1, 2], lines=lines), cfg, {},
                                  keep_line=streams.keep_line, dropped={}, implicit_next={"han": "am"},
                                  is_header=lambda ln: ln.text.startswith("NGÔ THÌ"))
    assert [r.text for r in res.lines["han"]] == ["旁通荆廣接高宣", "溪流奔駛爭歸海"]
    assert [r.text for r in res.lines["am"]] == ["Bàng thông Kinh,"]


def test_is_upper_title():
    from src.segmentation.layers import is_upper_title
    assert is_upper_title("LÊN NÚI MẪU TỬ (1)") and is_upper_title("THỜ MÙNG MƯA") and is_upper_title("KỲ NHẤT")
    assert not is_upper_title("Cờ sứ buổi sớm lên đường,") and not is_upper_title("17")
    assert not is_upper_title("NHỊ LẠNG GIANG SƠN CẢNH GIỚI LIÊN,")


def _cpo_like_doc():
    from src.segmentation.model import Document
    texts = [("han", "登母子山"), ("han", "旌旄曉發度穹窿"), ("han", "雲物橫斜指顧中"),
             ("vie", "ĐĂNG MẪU TỬ SƠN"), ("vie", "Tinh mao hiểu phát độ khung lung,"),
             ("vie", "Vân vật hoành tà chỉ cố trung."),
             ("vie", "Trích Dật thi lược toản"), ("vie", "1. Nguyên chú: Mẫu Tử là tên núi."),
             ("vie", "LÊN NÚI MẪU TỬ (1)"), ("vie", "Cờ sứ buổi sớm lên đường, vượt vòm trời cao,"),
             ("vie", "Dịch thơ:"), ("vie", "Sáng ra cờ sứ vượt non cao,")]
    lines = [line(s, 19, i, t, 100 * i) for i, (s, t) in enumerate(texts, 1)]
    return Document(id="W.003", pages=[19], lines=lines)


CFG = {"han": {"start_patterns": [r"^\s*[一-鿿]{2,12}\s*$"]},
       "am": {"heading_mode": True, "start_patterns": [r"^PHI[ÊE]N\s+ÂM"],
              "attribution_patterns": [r"^\s*(?:Trích|Theo)\b"]},
       "nghia": {"heading_mode": True, "start_patterns": [r"^DỊCH\s+NGHĨA"]},
       "tho": {"heading_mode": True, "start_patterns": [r"^DỊCH\s+THƠ"]}}


def test_headings_missing_upper_titles_and_source_line_split_sections():
    from src.segmentation.layers import assign_document_unified
    res = assign_document_unified("W", _cpo_like_doc(), CFG, {}, keep_line=streams.keep_line, dropped={},
                                  implicit_next={"han": "am"}, title_next={"am": "nghia"},
                                  notes_after_attribution={"am"})
    text = {k: [r.text for r in v] for k, v in res.lines.items()}
    assert text["han"] == ["登母子山", "旌旄曉發度穹窿", "雲物橫斜指顧中"]
    # tiêu đề viết hoa ở đầu lớp được bước metadata tách ra; dòng nguồn ở lại lớp am; chú thích không gán lớp
    assert text["am"][:2] == ["Tinh mao hiểu phát độ khung lung,", "Vân vật hoành tà chỉ cố trung."]
    assert not any("Nguyên chú" in t for t in sum(text.values(), []))
    assert text["nghia"][-1] == "Cờ sứ buổi sớm lên đường, vượt vòm trời cao,"
    assert text["tho"] == ["Sáng ra cờ sứ vượt non cao,"]
    reasons = [r["reason"] for r in res.review]
    assert reasons.count("implicit_layer_start") == 2 and "unassigned_line" in reasons   # chú thích


def test_upper_title_right_after_am_title_does_not_open_nghia():
    from src.segmentation.layers import assign_document_unified
    from src.segmentation.model import Document
    texts = [("han", "諒山道中"), ("vie", "PHIÊN ÂM:"), ("vie", "LẠNG SƠN ĐẠO TRUNG (NHỊ THỦ)"),
             ("vie", "KỲ NHẤT"), ("vie", "Nhị Lạng giang sơn cảnh giới liên,")]
    doc = Document(id="W.001", pages=[15], lines=[line(s, 15, i, t, 100 * i) for i, (s, t) in enumerate(texts, 1)])
    res = assign_document_unified("W", doc, CFG, {}, keep_line=streams.keep_line, dropped={},
                                  implicit_next={"han": "am"}, title_next={"am": "nghia"})
    assert [r.text for r in res.lines["am"]] == ["Nhị Lạng giang sơn cảnh giới liên,"]   # 2 tiêu đề -> metadata
    assert "nghia" not in res.lines
    assert not any(r["reason"] == "implicit_layer_start" and r["layer"] == "nghia" for r in res.review)


def test_running_header_does_not_open_nghia():
    from src.segmentation.layers import assign_document_unified
    from src.segmentation.model import Document
    texts = [("han", "諒山道中"), ("vie", "PHIÊN ÂM:"), ("vie", "Nhị Lạng giang sơn cảnh giới liên,"),
             ("vie", "HOÀNG HOA ĐỒ PHẢ"), ("vie", "Bàng thông Kinh, Quảng tiếp Cao, Tuyên.")]
    doc = Document(id="W.001", pages=[15, 16],
                   lines=[line(s, 15, i, t, 100 * i) for i, (s, t) in enumerate(texts, 1)])
    res = assign_document_unified("W", doc, CFG, {}, keep_line=streams.keep_line, dropped={},
                                  implicit_next={"han": "am"}, title_next={"am": "nghia"},
                                  is_header=lambda ln: ln.text == "HOÀNG HOA ĐỒ PHẢ")
    assert "nghia" not in res.lines
    assert res.lines["am"][-1].text == "Bàng thông Kinh, Quảng tiếp Cao, Tuyên."


def test_two_line_upper_title_with_comma_does_not_open_nghia():
    from src.segmentation.layers import assign_document_unified
    from src.segmentation.model import Document
    texts = [("han", "謁關留贈潘御史"), ("vie", "YẾT QUAN LƯU TẶNG PHAN NGỰ SỬ,"),
             ("vie", "VŨ CÔNG BỘ, NGÔ HIỆP TRẤN CHƯ CÔNG (NHỊ THỦ)"), ("vie", "Kỳ nhất"),
             ("vie", "Tinh mao liệp liệp xuất Dương Quan,")]
    doc = Document(id="W.004", pages=[21], lines=[line(s, 21, i, t, 100 * i) for i, (s, t) in enumerate(texts, 1)])
    res = assign_document_unified("W", doc, CFG, {}, keep_line=streams.keep_line, dropped={},
                                  implicit_next={"han": "am"}, title_next={"am": "nghia"})
    assert "nghia" not in res.lines
    assert res.lines["am"][-1].text == "Tinh mao liệp liệp xuất Dương Quan,"


def test_single_han_line_quoted_in_notes_does_not_open_document():
    merged = [line("han", 207, 1, "與高明二法師難佛不見形書", 50), line("han", 207, 2, "夫道處清虛", 60),
              line("vie", 207, 3, "(3) Bản Mạc Tư Khoa chép đầu đề là:", 300),
              line("han", 207, 4, "與道高法明二法師書難佛不見形", 320),
              line("vie", 207, 5, "(4) Thanh hư: trong trời trống không.", 340)]
    assert streams.allowed_document_starts(merged) == {0}
