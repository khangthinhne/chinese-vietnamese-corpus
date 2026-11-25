"""Kiểm tra độ đủ của OCR thô hai luồng (src/ocr/ocr_check.py, scripts/check_ocr_complete.py)."""
import json
from pathlib import Path

from PIL import Image

from src.common.corpus import DataLayout
from src.ocr import build_layers, ocr_check, raw_store
from src.ocr.run_quality import bad_units

WORK = "T_CHECK"


def run(engine, texts, key, bands=None):
    return {"key": key, "parameters": {"engine": {"name": engine, "adapter_version": 1}},
            "created_at": "2026-01-01T00:00:00+00:00",
            "lines": [{"text": t, "bbox": [0, i, 10, i + 1], "confidence": 90.0} for i, t in enumerate(texts)],
            "raw": {"bands": bands if bands is not None else [{"band": 1, "flags": [], "generated_text": "x"}]}}


def setup_work(tmp_path, pages, han, vie, ink=None):
    layout = DataLayout(tmp_path, WORK)
    layout.pages_dir.mkdir(parents=True)
    for p in pages:
        im = Image.new("L", (100, 100), 255)
        if (ink or {}).get(p, True):
            for x in range(10, 90):
                for y in range(10, 30):
                    im.putpixel((x, y), 0)
        im.save(layout.pages_dir / f"page_{p:03d}.png")
    for engine, texts in (("paddlevl", han), ("qwen", vie)):
        for p, item in texts.items():
            r = item if isinstance(item, dict) else run(engine, item, f"{engine}{p}")
            raw_store.save_engine_page(layout, engine, p, {"schema_version": 1, "work_id": WORK, "engine": engine,
                                                          "page": p, "runs": [r]})
    build_layers.run(type("A", (), {"layers": "han,vie", "data_root": str(tmp_path), "work_id": WORK,
                                    "report_only": False, "build_pages": None, "force": False,
                                    "han_min_cjk_share": 0.3, "han_min_cjk_with_latin": 2})())
    return layout


def codes(report, level=None):
    return sorted({(i["page"], i["code"]) for i in report["issues"] if level is None or i["level"] == level})


def test_complete_work_has_no_errors_and_classifies_pages(tmp_path):
    layout = setup_work(tmp_path, [1, 2, 3],
                        han={1: ["天地玄黃"], 2: ["宇宙洪荒"], 3: []},
                        vie={1: ["Thiên địa huyền hoàng"], 2: ["宇宙洪荒"], 3: ["Trời đất", "Dịch nghĩa", "Vũ trụ"]})
    report = ocr_check.check_work(layout, "1-3")
    assert report["complete"], codes(report, "error")
    assert report["totals"]["classes"]["bilingual"] == 1
    assert [r["class"] for r in report["pages"]] == ["bilingual", "han_only", "vie_only"]
    assert (2, "han_only_page") in codes(report, "info")


def test_missing_runs_images_and_stale_build_are_errors(tmp_path):
    layout = setup_work(tmp_path, [1, 2], han={1: ["天地"]}, vie={1: ["Thiên địa"], 2: ["Trời"]})
    # thêm run paddlevl cho trang 2 SAU khi build -> build cũ
    raw_store.save_engine_page(layout, "paddlevl", 2, {"schema_version": 1, "work_id": WORK, "engine": "paddlevl",
                                                       "page": 2, "runs": [run("paddlevl", ["玄黃"], "late")]})
    report = ocr_check.check_work(layout, "1-3")
    errors = codes(report, "error")
    assert (3, "missing_image") in errors and (3, "no_han_run") in errors and (3, "no_vie_run") in errors
    assert (2, "stale_build") in errors
    assert not report["complete"]


def test_degenerate_latin_band_is_error_but_han_loop_in_qwen_is_not(tmp_path):
    bad = run("qwen", ["Theo Tăng sử lược thì"], "bad",
              bands=[{"band": 2, "flags": ["repetition_collapsed", "truncated"],
                      "generated_text": "kính lễ. Theo Tăng sử lược thì người Tây Vực ？？？？"}])
    han_loop = run("qwen", ["Phiên âm"], "hl",
                   bands=[{"band": 1, "flags": ["truncated"], "generated_text": "江江江江江江江江"}])
    underline = run("qwen", ["Chú thích"], "ul",
                    bands=[{"band": 1, "flags": ["repetition_collapsed"], "generated_text": "_____\n1. Chú thích dài"}])
    assert len(bad_units(bad, "vie")) == 1 and not bad_units(han_loop, "vie") and not bad_units(underline, "vie")
    layout = setup_work(tmp_path, [1, 2, 3], han={1: [], 2: ["天地"], 3: []}, vie={1: bad, 2: han_loop, 3: underline})
    report = ocr_check.check_work(layout, "1-3")
    assert [c for c in codes(report, "error") if c[1] == "vie_degenerate"] == [(1, "vie_degenerate")]


def test_blank_page_is_info_and_unreadable_page_is_error(tmp_path):
    layout = setup_work(tmp_path, [1, 2], han={1: [], 2: []}, vie={1: [], 2: ["123"]}, ink={1: False})
    report = ocr_check.check_work(layout, "1-2")
    assert (1, "blank_page") in codes(report, "info")
    assert (2, "no_text") in codes(report, "error")


def test_vie_english_like_and_han_anomalies():
    assert ocr_check.english_like("The following sentences are written by Mr. Hy Doan, making them more vivid.")
    assert not ocr_check.english_like("Trích Dật thi lược toàn của Phan Huy Ích")
    check = {"han_line_factor": 2.5, "han_max_line_chars": 40, "han_single_char_share": 0.5,
             "han_gap_unit_share": 0.5}
    lines = ["天"] * 8 + ["天地玄黃"]
    found = dict(ocr_check.han_page_anomalies(lines, [["han_gap_removed"]] * 5, median_lines=3, check=check))
    assert set(found) == {"han_single_char_lines", "han_gap_units"}          # 9 dòng < 10: chưa xét "nhiều dòng"
    many = dict(ocr_check.han_page_anomalies(["天地玄黃"] * 12, [], median_lines=3, check=check))
    assert set(many) == {"han_many_lines"}
    assert "han_long_line" in dict(ocr_check.han_page_anomalies(["天" * 50], [], 1, check))


def test_simplified_chars_with_converter():
    s2t = {"数": "數", "于": "於"}
    assert ocr_check.simplified_chars("數数于天", lambda c: s2t.get(c, c)) == ["数", "于"]


def test_sample_is_deterministic():
    report = {"pages": [{"page": p, "class": "bilingual"} for p in range(1, 50)]}
    assert ocr_check.sample_pages(report, 5, 0) == ocr_check.sample_pages(report, 5, 0)
    assert len(ocr_check.sample_pages(report, 5, 0)) == 5



def test_cli_runs(tmp_path):
    import importlib.util
    spec = importlib.util.spec_from_file_location("coc", Path("scripts/check_ocr_complete.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    layout = setup_work(tmp_path, [1], han={1: ["天地"]}, vie={1: ["Thiên địa"]})
    out = tmp_path / "check.json"
    code = mod.main(["--work-id", WORK, "--data-root", str(tmp_path), "--config", str(tmp_path / "none.json"),
                     "--json", str(out), "--sample", "1"])
    assert code == 0
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["complete"] and data["visual_sample"]["pages"] == [1]
    assert layout.work_id == data["work_id"]
