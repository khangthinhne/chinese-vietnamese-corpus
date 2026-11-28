"""Sửa tay OCR (03_ocr/corrections/corrections.tsv): đọc, áp ở build_layers, chỉ ghi thêm."""

import json

import pytest

from src.common.corpus import DataLayout
from src.common.xmlstore import file_element, read_root
from src.ocr import build_layers
from src.ocr import corrections as oc

WORK = "W"
HEADER = "\t".join(oc.COLUMNS) + "\n"


def row(line_id, old, new, note="n", editor="tester", date="2026-11-28"):
    return "\t".join([line_id, old, new, note, editor, date]) + "\n"


def qwen_run(texts, key="q1"):
    return {"key": key, "parameters": {"image_sha256": "x", "engine": {"name": "qwen", "adapter_version": 1}},
            "created_at": "2026-01-01T00:00:00+00:00", "image": "p.png", "image_size": [60, 60],
            "lines": [{"text": t, "bbox": [0, i, 10, i + 1], "confidence": 90.0} for i, t in enumerate(texts)],
            "raw": {"engine": "qwen"}}


def fixture(tmp_path):
    layout = DataLayout(tmp_path, WORK)
    path = layout.ocr_raw_unified_path(1)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"schema_version": 1, "work_id": WORK, "page": 1, "runs": [qwen_run(
        ["CHÚ THÍCH:", "(1) Quế đố: một thứ sâu.", "Sử dụng 20000000", "Chương thổ bất sinh trà."])]},
        ensure_ascii=False), encoding="utf-8")
    return layout


def vie_lines(layout):
    root = file_element(read_root(layout.ocr_layer_xml_path("vie")))
    return [(x.get("ID"), x.get("SRC_LINE"), x.text) for x in root.findall("PAGE/LINE")]


def test_parse_rejects_bad_rows():
    assert oc.parse_rows(HEADER + row("vie.P0402.S004", "a", ""))[0].is_delete
    with pytest.raises(ValueError, match="line_id"):
        oc.parse_rows(HEADER + row("vie.P0402.L004", "a", "b"))      # ID sau lọc, không phải SRC_LINE
    with pytest.raises(ValueError, match="tiêu đề"):
        oc.parse_rows("line_id\told\n" + row("vie.P0402.S004", "a", "b"))
    with pytest.raises(ValueError, match="ngày"):
        oc.parse_rows(HEADER + row("vie.P0402.S004", "a", "b", date="28/11/2026"))
    with pytest.raises(ValueError, match="text_old"):
        oc.parse_rows(HEADER + row("vie.P0402.S004", "", "b"))


def test_apply_page_statuses_and_chain():
    src = [{"src_line": 1}, {"src_line": 2}, {"src_line": 3}, {"src_line": 4}]
    kept = [(1, "A"), (2, "B"), (4, "D")]
    rejected = [(3, "pinyin", "gōng qì")]
    fixes = oc.parse_rows(HEADER + row("vie.P0001.S001", "A", "A1") + row("vie.P0001.S001", "A1", "A2")
                          + row("vie.P0001.S002", "B", "") + row("vie.P0001.S003", "gōng qì", "")
                          + row("vie.P0001.S004", "khác", "X") + row("vie.P0001.S009", "Z", ""))
    log = oc.apply_page(kept, rejected, src, fixes)
    assert [r["status"] for r in log] == ["applied", "applied", "deleted", "line_filtered", "text_mismatch",
                                          "line_not_found"]
    assert kept == [(1, "A2"), (4, "D")]
    assert rejected[-1] == (2, oc.DELETE_REASON, "B")


def test_build_layers_applies_corrections_and_logs(tmp_path):
    layout = fixture(tmp_path)
    oc.append_rows(layout.ocr_corrections_path, [
        {"line_id": "vie.P0001.S003", "text_old": "Sử dụng 20000000", "text_new": "", "note": "bịa",
         "editor": "tester", "date": "2026-11-28"},
        {"line_id": "vie.P0001.S004", "text_old": "Chương thổ bất sinh trà.", "text_new": "Chướng thổ bất sinh trà.",
         "note": "瘴", "editor": "tester", "date": "2026-11-28"},
        {"line_id": "vie.P0001.S002", "text_old": "không khớp", "text_new": "x", "note": "", "editor": "tester",
         "date": "2026-11-28"}])
    raw_before = layout.ocr_raw_unified_path(1).read_bytes()
    assert build_layers.main(["--work-id", WORK, "--data-root", str(tmp_path), "--layers", "vie"]) == 0
    # dòng bị xoá không để lại lỗ trong ID (đánh lại), SRC_LINE vẫn là số dòng gốc
    assert vie_lines(layout) == [("P0001.L001", "1", "CHÚ THÍCH:"), ("P0001.L002", "2", "(1) Quế đố: một thứ sâu."),
                                 ("P0001.L003", "4", "Chướng thổ bất sinh trà.")]
    assert layout.ocr_layer_text_path("vie", 1).read_text(encoding="utf-8").endswith("Chướng thổ bất sinh trà.\n")
    manifest = json.loads(layout.ocr_layer_manifest_path("build", "vie").read_text(encoding="utf-8"))
    assert [r["status"] for r in manifest["corrections"]["log"]] == ["deleted", "applied", "text_mismatch"]
    assert manifest["totals"]["n_corrections_skipped"] == 1
    assert any("vie.P0001.S002" in w for w in manifest["warnings"])
    assert {"src_line": 3, "reason": oc.DELETE_REASON, "text": "Sử dụng 20000000"} in manifest["pages"][0]["rejected"]
    assert layout.ocr_raw_unified_path(1).read_bytes() == raw_before          # JSON thô không đổi
    assert build_layers.verify_layer(layout, "vie")[0] == []
    review = build_layers.review_path(layout, "vie").read_text(encoding="utf-8").splitlines()
    assert review == ["page\tsrc_line\treason\ttext", f"1\t3\t{oc.DELETE_REASON}\tSử dụng 20000000"]


def test_rebuild_is_idempotent_and_triggered_by_new_rows(tmp_path):
    layout = fixture(tmp_path)
    args = ["--work-id", WORK, "--data-root", str(tmp_path), "--layers", "vie"]
    assert build_layers.main(args) == 0
    assert len(vie_lines(layout)) == 4
    oc.append_rows(layout.ocr_corrections_path, [{"line_id": "vie.P0001.S003", "text_old": "Sử dụng 20000000",
                                                  "text_new": "", "note": "", "editor": "t", "date": "2026-11-28"}])
    assert build_layers.main(args) == 0          # không --force: file sửa mới phải làm bước này chạy lại
    first = vie_lines(layout)
    assert len(first) == 3
    assert build_layers.main(args + ["--force"]) == 0
    assert vie_lines(layout) == first


def test_append_only(tmp_path):
    path = tmp_path / "c.tsv"
    oc.append_rows(path, [{"line_id": "vie.P0001.S001", "text_old": "a", "text_new": "b", "note": "",
                           "editor": "t", "date": "2026-11-28"}])
    before = path.read_text(encoding="utf-8")
    oc.append_rows(path, [{"line_id": "vie.P0001.S002", "text_old": "c", "text_new": "", "note": "",
                           "editor": "t", "date": "2026-11-28"}])
    after = path.read_text(encoding="utf-8")
    assert after.startswith(before) and after.count("line_id") == 1
    assert oc.is_append_only(before, after)
    assert not oc.is_append_only(after, before.replace("\tb\t", "\tB\t"))
    with pytest.raises(ValueError, match="tab"):
        oc.append_rows(path, [{"line_id": "vie.P0001.S003", "text_old": "a\tb", "text_new": "", "note": "",
                               "editor": "t", "date": "2026-11-28"}])
    assert path.read_text(encoding="utf-8") == after          # hàng hỏng không được ghi
