"""Đóng gói / giải gói / ghép gói OCR theo khoảng trang (scripts/pack_ocr.py, unpack_ocr.py, merge_ocr_packs.py).
Không cần GPU: dữ liệu giả nhỏ."""
import importlib.util
import json
import zipfile
from pathlib import Path

import pytest

from src.common.corpus import DataLayout
from src.ocr import raw_store


def load(name):
    spec = importlib.util.spec_from_file_location(name, Path("scripts") / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


pack_ocr, unpack_ocr, merge_ocr_packs = load("pack_ocr"), load("unpack_ocr"), load("merge_ocr_packs")
WORK = "W"


def run(engine, page, key=None, text=None):
    return {"key": key or f"{engine}-{page}", "parameters": {"engine": {"name": engine, "adapter_version": 1}},
            "created_at": "2026-01-01T00:00:00+00:00",
            "lines": [{"text": text or ("天地" if engine == "paddlevl" else "Trời đất"), "bbox": [0, 0, 9, 9],
                       "confidence": 90.0}],
            "raw": {"bands": [{"band": 1, "flags": [], "generated_text": "x"}]}}


def make_work(root: Path, pages, config_pages="1-6", work=WORK) -> Path:
    """data root với raw paddlevl+qwen cho `pages`, manifest OCR, và configs/<work>.json (khoảng đầy đủ)."""
    layout = DataLayout(root, work)
    for p in pages:
        for engine in ("paddlevl", "qwen"):
            raw_store.save_engine_page(layout, engine, p, {"schema_version": 1, "work_id": work, "engine": engine,
                                                          "page": p, "runs": [run(engine, p)]})
    layout.ocr_manifest_dir.mkdir(parents=True, exist_ok=True)
    for layer, engine in (("han", "paddlevl"), ("vie", "qwen")):
        (layout.ocr_manifest_dir / f"ocr_{layer}.json").write_text(json.dumps({
            "updated_at": "2026-01-01T00:00:00+00:00",
            "pages": [{"page": p, "engine": engine, "key": f"{engine}-{p}", "status": "ocr"} for p in pages]}),
            encoding="utf-8")
    (layout.work_dir / "03_ocr" / "ocr_raw").mkdir(parents=True, exist_ok=True)
    (layout.work_dir / "03_ocr" / "ocr_raw" / "han.xml").write_text("<root/>", encoding="utf-8")
    cfg = root.parent / "configs" / f"{work}.json"
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text(json.dumps({"common": {"work_id": work}, "ocr": {"streams": {"pages": config_pages}}}),
                   encoding="utf-8")
    return cfg


def do_pack(root, cfg, pages, member, out):
    return pack_ocr.pack(WORK, root, out, pages=pages, member=member, config=cfg, date="20260101")


def test_pack_range_only_raw_and_range_manifests(tmp_path):
    root = tmp_path / "data"
    cfg = make_work(root, [1, 2, 3, 4])
    target, info = do_pack(root, cfg, "2-3", "tv2", tmp_path / "dist")
    assert target.name == "W_0002-0003_tv2_20260101.zip"
    with zipfile.ZipFile(target) as zf:
        names = set(zf.namelist())
        sums = zf.read("MANIFEST.sha256").decode().splitlines()
    assert {"W/03_ocr/raw/paddlevl/0002.json", "W/03_ocr/raw/qwen/0003.json"} <= names
    assert "W/03_ocr/raw/paddlevl/0001.json" not in names and "W/03_ocr/raw/qwen/0004.json" not in names
    assert not any(n.endswith(".xml") or "/ocr_raw_text/" in n for n in names)      # tái tạo được: không đóng gói
    assert "W/03_ocr/manifests/ocr_han_0002-0003.json" in names and "W/03_ocr/ocr_check_0002-0003.json" in names
    assert len(sums) == len(names) - 2
    assert info["missing_han"] == "" and info["pages"] == "2-3"
    _, info = do_pack(root, cfg, "3-5", None, tmp_path / "dist")                        # trang 5 chưa OCR
    assert info["missing_han"] == "5" and info["missing_vie"] == "5"
    with pytest.raises(SystemExit):
        do_pack(root, cfg, "2-3", "tv2", tmp_path / "dist")                             # không ghi đè


def test_merge_two_halves_is_complete_without_overlap(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    cfg = make_work(a, [1, 2, 3])
    make_work(b, [4, 5, 6])
    za, _ = do_pack(a, cfg, "1-3", "tv2", tmp_path / "dist")
    zb, _ = do_pack(b, cfg, "4-6", "tv3", tmp_path / "dist")
    dest = tmp_path / "dest"
    code, s = merge_ocr_packs.merge([za, zb], dest, config=cfg)
    assert code == 0 and s["n_missing"] == 0 and s["overlaps"] == [] and s["written"] > 0
    assert "đủ 1-6, 0 thiếu, 0 chồng" in merge_ocr_packs.format_summary(s)
    layout = DataLayout(dest, WORK)
    assert raw_store.available_pages(layout, ["paddlevl", "qwen"]) == [1, 2, 3, 4, 5, 6]
    assert (layout.ocr_manifest_dir / "ocr_vie_0004-0006.json").exists()
    code, s = merge_ocr_packs.merge([za, zb], dest, config=cfg)                         # lần hai: toàn "giống"
    assert code == 0 and s["counts"]["new"] == 0 and s["counts"]["conflict"] == 0


def test_merge_reports_missing_pages_between_packs(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    cfg = make_work(a, [1, 2])
    make_work(b, [5, 6])
    za, _ = do_pack(a, cfg, "1-3", "tv2", tmp_path / "dist")
    zb, _ = do_pack(b, cfg, "4-6", "tv3", tmp_path / "dist")
    code, s = merge_ocr_packs.merge([za, zb], tmp_path / "dest", dry_run=True, config=cfg)
    assert code == 0 and s["written"] == 0 and s["missing_han"] == "3-4" and s["n_missing"] == 2
    assert not (tmp_path / "dest").exists()                                             # dry-run không ghi


def test_overlap_same_content_ok_different_content_conflict(tmp_path):
    a, b, c = tmp_path / "a", tmp_path / "b", tmp_path / "c"
    cfg = make_work(a, [1, 2, 3])
    make_work(b, [3, 4, 5, 6])                         # trang 3 giống hệt a (cùng run giả)
    za, _ = do_pack(a, cfg, "1-3", "tv2", tmp_path / "dist")
    zb, _ = do_pack(b, cfg, "3-6", "tv3", tmp_path / "dist")
    code, s = merge_ocr_packs.merge([za, zb], tmp_path / "d1", config=cfg)
    assert code == 0 and s["overlaps"][0]["pages"] == "3" and s["n_missing"] == 0
    # c: trang 3 có run KHÁC nội dung -> xung đột, mặc định không ghi gì
    make_work(c, [3, 4, 5, 6])
    layout = DataLayout(c, WORK)
    path = raw_store.engine_raw_path(layout, "qwen", 3)
    data = json.loads(path.read_text(encoding="utf-8"))
    data["runs"][0]["lines"][0]["text"] = "Khác"
    path.write_text(json.dumps(data), encoding="utf-8")
    zc, _ = do_pack(c, cfg, "3-6", "tv9", tmp_path / "dist")
    code, s = merge_ocr_packs.merge([za, zc], tmp_path / "d2", config=cfg)
    assert code == 1 and s["written"] == 0 and any("qwen/0003.json" in x for x in s["conflicts"])
    assert not (tmp_path / "d2").exists()
    code, s = merge_ocr_packs.merge([za, zc], tmp_path / "d3", keep_going=True, config=cfg)
    assert code == 1 and s["written"] > 0
    kept = json.loads((tmp_path / "d3/W/03_ocr/raw/qwen/0003.json").read_text(encoding="utf-8"))
    assert kept["runs"][0]["lines"][0]["text"] == "Trời đất"                            # bản đầu tiên được giữ


def test_seed_then_member_append_is_accepted(tmp_path):
    """Gói hạt giống giải vào máy thành viên; thành viên thêm run cho trang hạt giống -> gói của họ là 'append'."""
    seed_src, member = tmp_path / "seed", tmp_path / "member"
    cfg = make_work(seed_src, [1, 2])
    zseed, _ = do_pack(seed_src, cfg, "1-2", "seed", tmp_path / "dist")
    assert unpack_ocr.main([str(zseed), "--data-root", str(member), "--config", str(cfg)]) == 0
    layout = DataLayout(member, WORK)
    store = raw_store.load_engine_page(layout, "qwen", 2, WORK)
    store["runs"].append(run("qwen", 2, key="new-key", text="Trời đất mới"))
    raw_store.save_engine_page(layout, "qwen", 2, store)
    for p in (3, 4, 5, 6):
        for engine in ("paddlevl", "qwen"):
            raw_store.save_engine_page(layout, engine, p, {"schema_version": 1, "work_id": WORK, "engine": engine,
                                                          "page": p, "runs": [run(engine, p)]})
    zmem, info = do_pack(member, cfg, "1-6", "tv2", tmp_path / "dist")
    assert info["missing_han"] == ""
    coord = tmp_path / "coord"
    assert unpack_ocr.main([str(zseed), "--data-root", str(coord)]) == 0                # người điều phối có hạt giống
    code, s = merge_ocr_packs.merge([zmem], coord, config=cfg)
    assert code == 0 and s["counts"]["append"] == 1 and s["n_missing"] == 0
    runs = json.loads((coord / "W/03_ocr/raw/qwen/0002.json").read_text(encoding="utf-8"))["runs"]
    assert [r["key"] for r in runs] == ["qwen-2", "new-key"]


def test_tampered_pack_is_rejected_and_nothing_written(tmp_path):
    a = tmp_path / "a"
    cfg = make_work(a, [1, 2])
    za, _ = do_pack(a, cfg, "1-2", "tv2", tmp_path / "dist")
    bad = tmp_path / "bad.zip"
    with zipfile.ZipFile(za) as src, zipfile.ZipFile(bad, "w") as dst:
        for item in src.infolist():
            data = src.read(item.filename)
            if item.filename == "W/03_ocr/raw/qwen/0001.json":
                data = data.replace("Trời".encode(), "Trăng".encode())
            dst.writestr(item, data)
    code, s = merge_ocr_packs.merge([bad], tmp_path / "dest", config=cfg)
    assert code == 2 and s["written"] == 0 and any("checksum sai" in p for p in s["problems"])
    assert not (tmp_path / "dest").exists()
    assert unpack_ocr.main([str(bad), "--data-root", str(tmp_path / "dest2")]) == 2


def test_unpack_rejects_unsafe_paths(tmp_path):
    assert unpack_ocr.safe_target("../x/03_ocr/a", tmp_path) is None
    assert unpack_ocr.safe_target("W/04_segmentation/a.xml", tmp_path) is None
    assert unpack_ocr.safe_target("W/03_ocr/../../a.json", tmp_path) is None
    assert unpack_ocr.safe_target("W/03_ocr/raw/a.json", tmp_path) == tmp_path / "W/03_ocr/raw/a.json"
