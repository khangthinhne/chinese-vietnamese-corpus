"""Đóng gói / giải gói OCR (scripts/pack_ocr.py, scripts/unpack_ocr.py)."""
import importlib.util
import json
import zipfile
from pathlib import Path

import pytest


def load(name):
    spec = importlib.util.spec_from_file_location(name, Path("scripts") / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


pack_ocr, unpack_ocr = load("pack_ocr"), load("unpack_ocr")


def make_work(root: Path, work="W"):
    base = root / work / "03_ocr"
    for rel, text in {"raw/paddlevl/0001.json": json.dumps({"schema_version": 1, "work_id": work, "engine": "paddlevl", "page": 1, "runs": [{"key": "a"}]}),
                      "manifests/ocr_han.json": "{}", "ocr_raw/han.xml": "<root/>",
                      "ocr_raw_text/han/0001.txt": "天地\n"}.items():
        (base / rel).parent.mkdir(parents=True, exist_ok=True)
        (base / rel).write_text(text, encoding="utf-8")
    (root / work / "02_extraction" / "pages").mkdir(parents=True)
    (root / work / "02_extraction" / "pages" / "page_001.png").write_bytes(b"img")


def test_pack_excludes_images_and_has_checksums(tmp_path):
    make_work(tmp_path / "data")
    target = pack_ocr.pack("W", tmp_path / "data", tmp_path / "dist", date="20260101")
    assert target.name == "W_ocr_20260101.zip"
    with zipfile.ZipFile(target) as zf:
        names = set(zf.namelist())
        assert "W/03_ocr/raw/paddlevl/0001.json" in names and "W/03_ocr/ocr_check.json" in names
        assert not any("02_extraction" in n for n in names)
        assert len(zf.read("SHA256SUMS").decode().splitlines()) == len(names) - 2
    with pytest.raises(SystemExit):
        pack_ocr.pack("W", tmp_path / "data", tmp_path / "dist", date="20260101")      # không ghi đè


def test_unpack_new_same_append_and_conflict(tmp_path):
    make_work(tmp_path / "src")
    target = pack_ocr.pack("W", tmp_path / "src", tmp_path / "dist", date="20260101")
    dest = tmp_path / "dest"
    assert unpack_ocr.main([str(target), "--data-root", str(dest)]) == 0
    assert (dest / "W/03_ocr/ocr_raw_text/han/0001.txt").read_text(encoding="utf-8") == "天地\n"
    assert unpack_ocr.main([str(target), "--data-root", str(dest)]) == 0                    # lần hai: toàn "giống"
    # bản trong máy khác nội dung -> xung đột, không bị ghi đè
    txt = dest / "W/03_ocr/ocr_raw_text/han/0001.txt"
    txt.write_text("khác\n", encoding="utf-8")
    raw = dest / "W/03_ocr/raw/paddlevl/0001.json"
    raw.write_text(json.dumps({"schema_version": 1, "work_id": "W", "engine": "paddlevl", "page": 1, "runs": []}), encoding="utf-8")   # gói có thêm run
    assert unpack_ocr.main([str(target), "--data-root", str(dest)]) == 1
    assert txt.read_text(encoding="utf-8") == "khác\n"
    assert json.loads(raw.read_text(encoding="utf-8"))["runs"] == [{"key": "a"}]             # append hợp lệ


def test_unpack_rejects_unsafe_paths(tmp_path):
    assert unpack_ocr.safe_target("../x/03_ocr/a", tmp_path) is None
    assert unpack_ocr.safe_target("W/04_segmentation/a.xml", tmp_path) is None
    assert unpack_ocr.safe_target("W/03_ocr/raw/a.json", tmp_path) == tmp_path / "W/03_ocr/raw/a.json"
