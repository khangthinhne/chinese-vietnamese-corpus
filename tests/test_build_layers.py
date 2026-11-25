import json
from pathlib import Path

import pytest
from PIL import Image

from src.common.corpus import DataLayout, sha256_file
from src.common.xmlstore import file_element, read_root
from src.ocr import build_layers, ocr, raw_store
from src.ocr.engines import ENGINES, UNAVAILABLE_ENGINES, OcrEngine, OcrLine, OcrResult, require_engine
from src.ocr.script_filter import count_cjk, has_cjk

WORK = "T_LAYER"


# --------------------------------------------------------------------------- helpers
def make_images(root: Path, pages: list[int]) -> Path:
    target = root / WORK / "02_extraction" / "pages"
    target.mkdir(parents=True, exist_ok=True)
    for page in pages:
        Image.new("RGB", (60, 60), (255, page, 255)).save(target / f"page_{page:03d}.png")
    return target


def make_run(engine: str, texts: list[str], key: str, created_at: str = "2026-01-01T00:00:00+00:00") -> dict:
    return {"key": key, "parameters": {"image_sha256": "x", "engine": {"name": engine, "adapter_version": 1},
                                       "lang": "all", "region": None, "psm": 6, "dpi": 300,
                                       "preprocessing": "rgb-crop-v1"},
            "created_at": created_at, "image": f"page_{key}.png", "image_size": [60, 60],
            "crop_box": [0, 0, 60, 60],
            "lines": [{"text": t, "bbox": [0, i, 10, i + 1], "confidence": 90.0} for i, t in enumerate(texts)],
            "raw": {"engine": engine}}


def write_unified(layout: DataLayout, page: int, runs: list[dict]) -> Path:
    path = layout.ocr_raw_unified_path(page)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"schema_version": 1, "work_id": WORK, "page": page, "runs": runs},
                               ensure_ascii=False), encoding="utf-8")
    return path


def write_engine(layout: DataLayout, engine: str, page: int, runs: list[dict]) -> Path:
    data = {"schema_version": 1, "work_id": WORK, "engine": engine, "page": page, "runs": runs}
    return raw_store.save_engine_page(layout, engine, page, data)


def fake_engine(name: str, texts: list[str], fail_on: set[int] = frozenset()):
    class Fake(OcrEngine):
        max_workers = 1
        calls: list[int] = []

        @classmethod
        def describe(cls, lang):
            return {"name": name, "adapter_version": 99}

        def recognize(self, image, *, lang, psm, dpi):
            page = image.getpixel((0, 0))[1]               # số trang mã hoá trong màu ảnh
            Fake.calls.append(page)
            if page in fail_on:
                raise RuntimeError("GPU hỏng giả lập")
            return OcrResult(name, [OcrLine(t, (0, i, 10, i + 1), 80.0) for i, t in enumerate(texts)], {})
    Fake.name = name
    return Fake


# --------------------------------------------------------------------------- OCR theo luồng
def test_layer_ocr_reuses_legacy_cache_and_never_writes_legacy(tmp_path, monkeypatch):
    make_images(tmp_path, [1, 2])
    fake = fake_engine("qwen", ["Phiên âm:", "初聞其語"])
    monkeypatch.setitem(ENGINES, "qwen", fake)
    layout = DataLayout(tmp_path, WORK)
    image1 = layout.pages_dir / "page_001.png"

    class Args:  # khoá cache phải trùng hệt chế độ unified
        region, psm, dpi = None, 6, 300
    key = raw_store.cache_key(ocr.run_parameters(sha256_file(image1), fake.describe("all"), Args))
    legacy = write_unified(layout, 1, [make_run("qwen", ["cũ"], key)])
    before = legacy.read_bytes()

    code = ocr.main(["--work-id", WORK, "--data-root", str(tmp_path), "--layer", "vie",
                     "--engines", "qwen", "--pages", "1-3"])
    assert code == 0
    assert fake.calls == [2]                                   # trang 1 lấy từ cache cũ, trang 3 thiếu ảnh
    assert legacy.read_bytes() == before                        # raw/NNNN.json không bị ghi
    assert not layout.ocr_raw_engine_path("qwen", 1).exists()
    stored = json.loads(layout.ocr_raw_engine_path("qwen", 2).read_text(encoding="utf-8"))
    assert [x["text"] for x in stored["runs"][0]["lines"]] == ["Phiên âm:", "初聞其語"]
    manifest = json.loads(layout.ocr_layer_manifest_path("ocr", "vie").read_text(encoding="utf-8"))
    assert [(p["page"], p["status"]) for p in manifest["pages"]] == [(1, "cached"), (2, "ocr")]
    assert manifest["missing_images"] == [3]
    assert not layout.ocr_unified_xml_path.exists()            # không đụng raw.xml

    # chạy lại: không gọi engine; --force-reocr thì gọi lại và append run mới, không mất run cũ
    assert ocr.main(["--work-id", WORK, "--data-root", str(tmp_path), "--layer", "vie",
                     "--engines", "qwen", "--pages", "2"]) == 0
    assert fake.calls == [2]
    assert ocr.main(["--work-id", WORK, "--data-root", str(tmp_path), "--layer", "vie",
                     "--engines", "qwen", "--pages", "1,2", "--force-reocr"]) == 0
    assert fake.calls == [2, 1, 2]
    assert legacy.read_bytes() == before
    assert len(json.loads(layout.ocr_raw_engine_path("qwen", 2).read_text(encoding="utf-8"))["runs"]) == 2


def test_layer_ocr_page_error_does_not_stop_batch(tmp_path, monkeypatch):
    make_images(tmp_path, [1, 2, 3])
    fake = fake_engine("paddlevl", ["初聞其語"], fail_on={2})
    monkeypatch.setitem(ENGINES, "paddlevl", fake)
    layout = DataLayout(tmp_path, WORK)
    code = ocr.main(["--work-id", WORK, "--data-root", str(tmp_path), "--layer", "han",
                     "--engines", "paddlevl", "--pages", "1-3"])
    assert code == 1                                           # báo lỗi, nhưng đã chạy hết batch
    assert fake.calls == [1, 2, 3]
    assert layout.ocr_raw_engine_path("paddlevl", 3).exists()
    manifest = json.loads(layout.ocr_layer_manifest_path("ocr", "han").read_text(encoding="utf-8"))
    assert manifest["error_pages"] == [2]
    assert "GPU hỏng giả lập" in manifest["pages"][1]["error"]


@pytest.mark.parametrize("layer,engines", [("han", "qwen"), ("vie", "paddlevl"), ("han", "paddlevl,qwen")])
def test_layer_ocr_rejects_wrong_engine(tmp_path, layer, engines):
    assert ocr.main(["--work-id", WORK, "--data-root", str(tmp_path), "--layer", layer,
                     "--engines", engines, "--pages", "1"]) == 1


def test_require_engine_reports_missing_dependency(monkeypatch):
    monkeypatch.setitem(UNAVAILABLE_ENGINES, "ghost", "ModuleNotFoundError: No module named 'ghostlib'")
    with pytest.raises(ValueError, match="ghostlib"):
        require_engine("ghost")
    with pytest.raises(ValueError, match="không hợp lệ"):
        require_engine("nope")
    assert require_engine("paddlevl") is ENGINES["paddlevl"]


def test_save_engine_page_is_append_only(tmp_path):
    layout = DataLayout(tmp_path, WORK)
    write_engine(layout, "paddlevl", 1, [make_run("paddlevl", ["初"], "a")])
    with pytest.raises(ValueError, match="runs cũ"):
        write_engine(layout, "paddlevl", 1, [make_run("paddlevl", ["改"], "b")])
    with pytest.raises(ValueError):
        raw_store.engine_raw_path(layout, "vie", 1)             # trùng thư mục hệ chữ cũ


# --------------------------------------------------------------------------- chọn run
def test_select_run_prefers_manifest_key_then_latest():
    runs = [make_run("qwen", ["a"], "k1", "2026-01-02"), make_run("qwen", ["b"], "k2", "2026-01-03"),
            make_run("paddlevl", ["c"], "k3", "2026-01-04")]
    assert build_layers.select_run(runs, "qwen", ["k1"])[0]["key"] == "k1"
    run, reason = build_layers.select_run(runs, "qwen", [])
    assert (run["key"], reason) == ("k2", "latest_of_many")
    assert build_layers.select_run(runs, "qwen", ["k3"])[0]["key"] == "k2"   # khoá của engine khác bị bỏ qua
    assert build_layers.select_run(runs, "tesseract", []) == (None, "none")


# --------------------------------------------------------------------------- dựng XML/TXT
def build_fixture(tmp_path) -> DataLayout:
    layout = DataLayout(tmp_path, WORK)
    # trang 1: song ngữ; trang 2: chỉ Hán; trang 3: chỉ Việt; trang 4: chỉ có run qwen
    write_unified(layout, 1, [make_run("qwen", ["1. 諒山道中（二首）", "PHIÊN ÂM:", "Nhị Lạng giang (江山)", "18"], "q1")])
    write_unified(layout, 2, [make_run("qwen", ["初聞其語半疑信", "57"], "q2")])
    write_unified(layout, 3, [make_run("qwen", ["DỊCH NGHĨA", "Trời mưa to.", "19"], "q3")])
    write_unified(layout, 4, [make_run("qwen", ["Bài 4"], "q4")])
    write_engine(layout, "paddlevl", 1, [make_run("paddlevl", ["1. 諒山道中（二首）", "PHIEN AM", "二諒江山境界連"], "p1")])
    write_engine(layout, "paddlevl", 2, [make_run("paddlevl", ["初聞其語半疑信", "57"], "p2")])
    write_engine(layout, "paddlevl", 3, [make_run("paddlevl", ["DICH NGHIA", "19"], "p3")])
    return layout


def test_build_layers_end_to_end(tmp_path, capsys):
    layout = build_fixture(tmp_path)
    raw_before = {p: p.read_bytes() for p in layout.ocr_raw_unified_dir.rglob("*.json")}
    assert build_layers.main(["--work-id", WORK, "--data-root", str(tmp_path)]) == 0

    han_txt = sorted(p.name for p in layout.ocr_layer_text_dir("han").iterdir())
    vie_txt = sorted(p.name for p in layout.ocr_layer_text_dir("vie").iterdir())
    assert han_txt == ["0001.txt", "0002.txt"]
    assert vie_txt == ["0001.txt", "0003.txt", "0004.txt"]
    assert layout.ocr_layer_text_path("han", 1).read_text(encoding="utf-8") == "諒山道中（二首）\n二諒江山境界連\n"
    assert layout.ocr_layer_text_path("vie", 1).read_text(encoding="utf-8") == "PHIÊN ÂM:\nNhị Lạng giang\n"

    han = file_element(read_root(layout.ocr_layer_xml_path("han")))
    lines = han.findall("PAGE/LINE")
    assert [(x.get("ID"), x.get("SRC_LINE"), x.get("CONFIDENCE")) for x in lines] == [
        ("P0001.L001", "1", "90.0"), ("P0001.L002", "3", "90.0"), ("P0002.L001", "1", "90.0")]
    assert all(has_cjk(x.text) for x in lines)
    assert {p.get("SELECTED_ENGINE") for p in han.findall("PAGE")} == {"paddlevl"}
    assert han.find("OCR").get("MODE") == "layer"

    vie_text = layout.ocr_layer_xml_path("vie").read_text(encoding="utf-8")
    assert count_cjk(vie_text) == 0
    vie = file_element(read_root(layout.ocr_layer_xml_path("vie")))
    assert [p.get("NUMBER") for p in vie.findall("PAGE")] == ["1", "3", "4"]
    assert vie.find("PAGE").get("CJK_REMOVED") == "10"

    for layer in ("han", "vie"):
        assert build_layers.verify_layer(layout, layer)[0] == []
    assert {p: p.read_bytes() for p in layout.ocr_raw_unified_dir.rglob("*.json")} == raw_before
    assert not layout.ocr_unified_xml_path.exists()

    out = capsys.readouterr().out
    assert "P0002: không có dòng Hán lẫn dòng Việt" not in out
    summary = build_layers.summarize(layout)
    assert (summary["han_pages"], summary["vie_pages"], summary["han_lines"], summary["vie_lines"]) == (2, 3, 3, 5)
    assert any("P0003: trang có run paddlevl nhưng không ra dòng Hán" in w for w in summary["warnings"])


def test_build_removes_stale_txt_of_its_own_only(tmp_path):
    layout = build_fixture(tmp_path)
    text_dir = layout.ocr_layer_text_dir("han")
    text_dir.mkdir(parents=True)
    (text_dir / "0009.txt").write_text("cũ\n", encoding="utf-8")
    (text_dir / "notes.md").write_text("giữ\n", encoding="utf-8")
    layout.ocr_unified_text_dir.mkdir(parents=True, exist_ok=True)
    (layout.ocr_unified_text_dir / "0001.txt").write_text("raw\n", encoding="utf-8")
    assert build_layers.main(["--work-id", WORK, "--data-root", str(tmp_path), "--layers", "han"]) == 0
    assert not (text_dir / "0009.txt").exists()
    assert (text_dir / "notes.md").exists()
    assert (layout.ocr_unified_text_dir / "0001.txt").read_text(encoding="utf-8") == "raw\n"


def test_build_refuses_to_overwrite_legacy_han_xml(tmp_path):
    layout = build_fixture(tmp_path)
    legacy = layout.ocr_layer_xml_path("han")
    legacy.parent.mkdir(parents=True, exist_ok=True)
    legacy.write_text('<root><FILE ID="T"><OCR WRITING_SYSTEM="chi_tra"/></FILE></root>', encoding="utf-8")
    assert build_layers.main(["--work-id", WORK, "--data-root", str(tmp_path), "--layers", "han"]) == 1
    assert "WRITING_SYSTEM" in legacy.read_text(encoding="utf-8")
    assert build_layers.main(["--work-id", WORK, "--data-root", str(tmp_path), "--layers", "han", "--force"]) == 0


def test_verify_layer_catches_cjk_in_vie_and_txt_mismatch(tmp_path):
    layout = build_fixture(tmp_path)
    assert build_layers.main(["--work-id", WORK, "--data-root", str(tmp_path)]) == 0
    path = layout.ocr_layer_xml_path("vie")
    path.write_text(path.read_text(encoding="utf-8").replace("Trời mưa to.", "Trời 雨 to."), encoding="utf-8")
    layout.ocr_layer_text_path("vie", 4).unlink()
    errors, _ = build_layers.verify_layer(layout, "vie")
    assert any("CJK" in e for e in errors)
    assert any("lệch trang" in e for e in errors)


def test_multiline_source_line_is_split_and_keeps_src_line(tmp_path):
    layout = DataLayout(tmp_path, WORK)
    write_unified(layout, 1, [make_run("qwen", ["57", "PHIÊN ÂM:\nDữ Cao\n與高明\nMinh"], "q1")])
    assert build_layers.main(["--work-id", WORK, "--data-root", str(tmp_path), "--layers", "vie"]) == 0
    vie = file_element(read_root(layout.ocr_layer_xml_path("vie")))
    assert [(x.text, x.get("SRC_LINE")) for x in vie.findall("PAGE/LINE")] == [
        ("PHIÊN ÂM:", "2"), ("Dữ Cao", "2"), ("Minh", "2")]
    assert layout.ocr_layer_text_path("vie", 1).read_text(encoding="utf-8") == "PHIÊN ÂM:\nDữ Cao\nMinh\n"


def test_legacy_writing_system_store_read_only_by_matching_layer(tmp_path):
    layout = DataLayout(tmp_path, WORK)
    for ws, text, key in (("chi_tra", "初聞 Bài Hán", "c"), ("vie", "Bài Việt", "v")):
        path = raw_store.raw_path(layout, ws, 5)
        path.parent.mkdir(parents=True, exist_ok=True)
        run = make_run("qwen", [text], key, created_at="2026-01-0" + ("9" if ws == "chi_tra" else "1"))
        path.write_text(json.dumps({"schema_version": 1, "work_id": WORK, "writing_system": ws, "page": 5,
                                    "runs": [run]}, ensure_ascii=False), encoding="utf-8")
    assert build_layers.main(["--work-id", WORK, "--data-root", str(tmp_path), "--layers", "vie"]) == 0
    assert layout.ocr_layer_text_path("vie", 5).read_text(encoding="utf-8") == "Bài Việt\n"


def paddle_run(units: list[list[str]], orientation: str = "horizontal") -> dict:
    texts = [t for unit in units for t in unit]
    run = make_run("paddlevl", texts, "p")
    run["raw"] = {"orientation": orientation, "bands": [{"unit": i, "n_lines": len(u), "flags": []}
                                                        for i, u in enumerate(units, 1)]}
    return run


def test_merge_char_split_units_only_when_every_line_is_one_char():
    run = paddle_run([["樂歲預徵膏澤遍"], ["玄", "麻", "蛋", "副", "璽", "書", "來"], ["官廳", "亦作"]])
    lines, merged = build_layers.merge_char_split_units(run)
    assert [x["text"] for x in lines] == ["樂歲預徵膏澤遍", "玄麻蛋副璽書來", "官廳", "亦作"]
    assert [x["src_line"] for x in lines] == [1, 2, 9, 10]
    assert merged == [{"src_lines": [2, 3, 4, 5, 6, 7, 8], "text": "玄麻蛋副璽書來"}]
    assert lines[1]["bbox"] == [0, 1, 10, 8]                     # hộp bao các dòng con


def test_merge_skipped_for_vertical_or_inconsistent_runs():
    assert build_layers.merge_char_split_units(paddle_run([["玄", "麻"]], "vertical"))[1] == []
    broken = paddle_run([["玄", "麻"]])
    broken["raw"]["bands"][0]["n_lines"] = 5                      # không khớp số dòng -> không đoán
    assert build_layers.merge_char_split_units(broken)[1] == []
    assert build_layers.merge_char_split_units(make_run("qwen", ["玄", "麻"], "q"))[1] == []


def test_build_records_rejected_and_merged_lines(tmp_path):
    layout = DataLayout(tmp_path, WORK)
    run = paddle_run([["二諒江山境界連"], ["a. Bản A.404: Hội khê 洄溪."], ["玄", "麻", "蛋"]])
    write_engine(layout, "paddlevl", 1, [run])
    assert build_layers.main(["--work-id", WORK, "--data-root", str(tmp_path), "--layers", "han"]) == 0
    han = file_element(read_root(layout.ocr_layer_xml_path("han")))
    assert [(x.text, x.get("SRC_LINE")) for x in han.findall("PAGE/LINE")] == [("二諒江山境界連", "1"), ("玄麻蛋", "3")]
    assert han.find("OCR/FILTER").get("HAN_MIN_CJK_SHARE") == "0.3"
    manifest = json.loads(layout.ocr_layer_manifest_path("build", "han").read_text(encoding="utf-8"))
    page = manifest["pages"][0]
    assert page["rejected"] == [{"src_line": 2, "reason": "low_cjk_share:0.15<0.3", "text": "a. Bản A.404: Hội khê 洄溪."}]
    assert page["merged"][0]["src_lines"] == [3, 4, 5]
    assert manifest["totals"]["n_rejected"] == 1 and manifest["totals"]["n_merged_units"] == 1
    # tắt luật bằng CLI
    assert build_layers.main(["--work-id", WORK, "--data-root", str(tmp_path), "--layers", "han",
                              "--han-min-cjk-share", "0", "--han-min-cjk-with-latin", "0"]) == 0
    han = file_element(read_root(layout.ocr_layer_xml_path("han")))
    assert len(han.findall("PAGE/LINE")) == 3
