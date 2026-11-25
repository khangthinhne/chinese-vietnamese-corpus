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
    assert streams.allowed_document_starts(merged) == {1, 6}


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
