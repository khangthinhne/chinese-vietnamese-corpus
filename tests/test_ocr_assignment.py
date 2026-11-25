"""Phân công OCR theo khoảng trang: source_sha256/source_pages trong config, check --pages + trang ngoài khoảng."""
import hashlib
import json

import pytest

from src.common.corpus import DataLayout
from src.ocr import ocr_check, raw_store, work_config


def test_source_sha_and_pages_validated(tmp_path):
    pdf = tmp_path / "src.pdf"
    pdf.write_bytes(b"%PDF-1.4 fake")
    sha = hashlib.sha256(pdf.read_bytes()).hexdigest()
    cfg = {"common": {"source": str(pdf)}, "ocr": {"streams": {"pages": "5-10", "source_sha256": sha,
                                                               "source_pages": 12}}}
    settings = work_config.stream_settings(cfg)
    assert settings["source_sha256"] == sha and settings["source_pages"] == 12
    assert work_config.verify_source(cfg, settings)[0]
    bad = {**cfg, "ocr": {"streams": {**cfg["ocr"]["streams"], "source_sha256": "0" * 64}}}
    ok, msg = work_config.verify_source(bad, work_config.stream_settings(bad))
    assert not ok and "KHÁC" in msg
    with pytest.raises(ValueError):                    # khoảng trang vượt số trang PDF
        work_config.stream_settings({"ocr": {"streams": {"pages": "5-13", "source_pages": 12}}})
    with pytest.raises(ValueError):
        work_config.stream_settings({"ocr": {"streams": {"source_sha256": "ABC"}}})
    assert work_config.stream_settings({})["source_sha256"] is None            # tương thích ngược


def test_verify_source_cli(tmp_path, capsys):
    pdf = tmp_path / "a.pdf"
    pdf.write_bytes(b"x")
    cfg = tmp_path / "W.json"
    cfg.write_text(json.dumps({"common": {"source": str(pdf)}, "ocr": {"streams": {
        "source_sha256": hashlib.sha256(b"x").hexdigest()}}}), encoding="utf-8")
    assert work_config.main(["--config", str(cfg), "--verify-source"]) == 0
    pdf.write_bytes(b"y")
    assert work_config.main(["--config", str(cfg), "--verify-source"]) == 1
    assert "LỖI" in capsys.readouterr().out


def test_check_pages_subset_warns_outside_range(tmp_path):
    layout = DataLayout(tmp_path, "W")
    for p in (1, 2, 9):
        for engine in ("paddlevl", "qwen"):
            raw_store.save_engine_page(layout, engine, p, {"schema_version": 1, "work_id": "W", "engine": engine,
                                                          "page": p, "runs": []})
    report = ocr_check.check_work(layout, "1-2")
    outside = [i for i in report["issues"] if i["code"] == "outside_range"]
    assert outside and outside[0]["level"] == "warning" and "9" in outside[0]["detail"]
    assert report["totals"]["raw_outside_range"] == "9" and report["totals"]["pages"] == 2


def test_error_pages_follow_newest_manifest_record(tmp_path):
    layout = DataLayout(tmp_path, "W")
    layout.ocr_manifest_dir.mkdir(parents=True)
    (layout.ocr_manifest_dir / "ocr_vie_0001-0005.json").write_text(json.dumps({
        "updated_at": "2026-01-01", "pages": [{"page": 3, "status": "error"}, {"page": 4, "status": "error"}]}),
        encoding="utf-8")
    (layout.ocr_manifest_dir / "ocr_vie_0003-0003.json").write_text(json.dumps({
        "updated_at": "2026-02-01", "pages": [{"page": 3, "status": "ocr"}]}), encoding="utf-8")
    assert ocr_check.manifest_error_pages(layout, "vie") == {4}
