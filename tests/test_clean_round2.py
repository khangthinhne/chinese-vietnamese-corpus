"""Clean round 2: chỉ mục khảo dị chữ cái, chạy lại không --force thì bỏ qua êm, phép đo mới của audit_clean."""
import importlib.util
import json
from pathlib import Path

import pytest

from src.cleaning.artifacts import clean_ocr_artifacts
from src.cleaning.model import LETTER_MARKER_RE, OCRCleaningConfig
from src.common import deps
from src.common.corpus import DataLayout


@pytest.mark.parametrize("text, expected", [
    ("Điệp trĩ (a) mạn truyền", "Điệp trĩ mạn truyền"),                  # cách chữ
    ("Phong tòng(b) lĩnh kiều;", "Phong tòng lĩnh kiều;"),                # dính chữ
    ("Ấn lâu triện^{(b)} ấn giang hồ", "Ấn lâu triện ấn giang hồ"),       # chỉ số trên kiểu LaTeX của Qwen
    ("Quả phi vu, (a)", "Quả phi vu,"),                                    # cuối dòng
    ("THỨ CẢM HOÀI (a)", "THỨ CẢM HOÀI"),
    ("Hưu (b) luân bạc mộ", "Hưu luân bạc mộ"),
    ("Pháo thanh hưởng xuất(^a) hống", "Pháo thanh hưởng xuất hống"),     # CPO_19 tr.74
])
def test_letter_markers_removed_in_viet_layers(text, expected):
    res = clean_ocr_artifacts(text, OCRCleaningConfig(), layer="am")
    assert res.text == expected
    assert any(rule == "letter_marker" for rule, _ in res.removed)


@pytest.mark.parametrize("text", [
    "(a) Bản A.404 chép là ...",        # đầu dòng: mục liệt kê khảo dị, không phải chỉ mục
    "phương án (ab) và (1a)",           # không phải đúng một chữ cái
    "ký hiệu (x)y",                     # sau ngoặc là chữ: không phải chỉ mục đứng riêng
])
def test_letter_markers_kept_when_not_a_marker(text):
    assert clean_ocr_artifacts(text, OCRCleaningConfig(), layer="nghia").text == text


def test_letter_markers_only_in_viet_layers_and_can_be_disabled():
    assert clean_ocr_artifacts("天地 (a) 玄黃", OCRCleaningConfig(), layer="han").text == "天地 (a) 玄黃"
    off = OCRCleaningConfig(strip_letter_markers=False)
    assert clean_ocr_artifacts("Điệp trĩ (a) mạn", off, layer="am").text == "Điệp trĩ (a) mạn"
    assert clean_ocr_artifacts("Điệp trĩ (a) mạn", OCRCleaningConfig()).text == "Điệp trĩ (a) mạn"   # không rõ lớp
    assert LETTER_MARKER_RE.search("(đ)") is None                                                   # đầu chuỗi


def test_recurring_single_line_pages_dropped(tmp_path):
    from src.cleaning.audit import Audit
    from src.cleaning.model import CleanConfig, RawLine
    from src.cleaning.page_filter import filter_pages

    def ln(i, page, text):
        return RawLine(f"L{i}", page, text, False, text, [], [], "D1")
    lines = [ln(1, 1, "欽定四庫全書"), ln(2, 2, "欽定四庫全書"), ln(3, 3, "欽定四庫全書"),
             ln(4, 4, "天地玄黃"), ln(5, 4, "宇宙洪荒"), ln(6, 5, "春眠不覺曉")]
    kept, records = filter_pages(lines, CleanConfig(layer="han"), Audit("W", "han"))
    assert [x.text for x in kept] == ["天地玄黃", "宇宙洪荒", "春眠不覺曉"]       # câu thơ 1 dòng không lặp: giữ
    assert sum(r["reason"] == "page_header" for r in records) == 3
    off = CleanConfig(layer="han", drop_recurring_single_lines=False)
    assert len(filter_pages(lines, off, Audit("W", "han"))[0]) == 6
    two = CleanConfig(layer="han", header_footer_min_pages=4)                     # chỉ 3 trang < 4: giữ
    assert len(filter_pages(lines, two, Audit("W", "han"))[0]) == 6


def test_check_fresh_skips_when_inputs_unchanged_and_blocks_when_changed(tmp_path):
    layout = DataLayout(tmp_path, "W")
    src, out = tmp_path / "in.txt", tmp_path / "out.txt"
    src.write_text("a", encoding="utf-8")
    out.write_text("x", encoding="utf-8")
    with pytest.raises(FileExistsError) as exc:                    # chưa có bản ghi phụ thuộc: chặn như cũ
        deps.check_fresh(layout, "s", [src], [out], False, "đã có")
    assert not isinstance(exc.value, deps.UpToDate)
    deps.record(layout, "s", [src], [out])
    with pytest.raises(deps.UpToDate):                             # đầu vào không đổi: bỏ qua
        deps.check_fresh(layout, "s", [src], [out], False, "đã có")
    assert deps.is_fresh(layout, "s", [src], out)
    src.write_text("b", encoding="utf-8")                          # đầu vào đổi: chặn, cần --force
    with pytest.raises(FileExistsError) as exc:
        deps.check_fresh(layout, "s", [src], [out], False, "đã có")
    assert not isinstance(exc.value, deps.UpToDate)
    deps.check_fresh(layout, "s", [src], [out], True, "đã có")     # --force: chạy


def _write_raw(layout, engine, page, text):
    from src.ocr import raw_store
    run = {"key": f"{engine}{page}", "parameters": {"engine": {"name": engine, "adapter_version": 1}},
           "created_at": "2026-01-01T00:00:00+00:00",
           "lines": [{"text": text, "bbox": [0, 0, 10, 10], "confidence": 90.0}],
           "raw": {"bands": [{"band": 1, "flags": [], "generated_text": "x"}]}}
    raw_store.save_engine_page(layout, engine, page, {"schema_version": 1, "work_id": "W", "engine": engine,
                                                      "page": page, "runs": [run]})


def test_build_layers_skips_when_raw_unchanged(tmp_path):
    from src.ocr import build_layers
    layout = DataLayout(tmp_path, "W")
    _write_raw(layout, "paddlevl", 1, "天地玄黃")
    _write_raw(layout, "qwen", 1, "Trời đất")
    argv = ["--work-id", "W", "--data-root", str(tmp_path)]
    assert build_layers.main(argv) == 0
    xml = layout.ocr_layer_xml_path("han")
    first = xml.read_bytes(), xml.stat().st_mtime_ns
    assert build_layers.main(argv) == 0
    assert (xml.read_bytes(), xml.stat().st_mtime_ns) == first            # bỏ qua: không ghi lại
    _write_raw(layout, "paddlevl", 2, "宇宙洪荒")                          # raw mới -> dựng lại
    assert build_layers.main(argv) == 0
    assert "宇宙洪荒" in xml.read_text(encoding="utf-8")


def test_export_clean_text_one_sentence_per_line_with_mapping(tmp_path):
    from src.common.sentences import Sentence, save_sentences
    layout = DataLayout(tmp_path, "W")
    save_sentences(layout, "han", [Sentence("W.han.00001", "天地玄黃", [1], source_ids=["han.P0001.L001"],
                                            document_id="W.001"),
                                   Sentence("W.han.00002", "宇宙 洪荒", [2], document_id="W.002")])
    save_sentences(layout, "am", [Sentence("W.am.00001", "Thiên địa huyền hoàng", [1], document_id="W.001"),
                                  Sentence("W.am.00002", "Vũ trụ hồng hoang", [2], document_id="W.002")])
    save_sentences(layout, "nghia", [Sentence("W.nghia.00001", "Trời  đất\nđen vàng", [1], document_id="W.001")])
    spec = importlib.util.spec_from_file_location("export_clean_text", Path("scripts") / "export_clean_text.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    out = tmp_path / "03_text_sach"
    assert mod.main(["--work-id", "W", "--data-root", str(tmp_path), "--out", str(out)]) == 0
    assert (out / "han.txt").read_text(encoding="utf-8") == "天地玄黃\n宇宙 洪荒\n"
    assert (out / "viet_am.txt").read_text(encoding="utf-8").splitlines() == [
        "Thiên địa huyền hoàng", "Vũ trụ hồng hoang"]
    assert (out / "viet_nghia.txt").read_text(encoding="utf-8") == "Trời đất đen vàng\n"
    assert not (out / "viet.txt").exists() and not (out / "viet_tho.txt").exists()
    rows = (out / "mapping.tsv").read_text(encoding="utf-8").splitlines()
    assert rows[1] == "han.txt\t1\tW.han.00001\tW.001\than\t1\than.P0001.L001"
    assert mod.main(["--work-id", "W", "--data-root", str(tmp_path), "--out", str(out), "--combined"]) == 0
    assert (out / "viet.txt").read_text(encoding="utf-8").splitlines() == [
        "Thiên địa huyền hoàng", "Trời đất đen vàng", "Vũ trụ hồng hoang"]      # theo tài liệu, rồi theo lớp
    rows = (out / "mapping.tsv").read_text(encoding="utf-8").splitlines()
    assert sum(r.startswith("viet.txt\t") for r in rows) == 3


def test_composition_date_and_translator_go_to_metadata():
    from src.cleaning.metadata import MetadataResult, attach_captured, capture_note_lines
    from src.cleaning.model import MetadataConfig, RawLine

    def ln(i, text):
        return RawLine(f"W.001.P0001.L{i:03d}", 1, text, False, text, [], [f"vie.P0001.L{i:03d}"], "W.001")
    lines = [ln(1, "Trung thu vô nguyệt"), ln(2, "(Làm mùa thu năm Bính Thìn 1796)"), ln(3, "Đêm thu không trăng,"),
             ln(4, "Ngô Linh Ngọc dịch"), ln(5, "Lâm Giang"), ln(6, "(Bản A.603 chép khác)")]
    kept, captured = capture_note_lines(lines, MetadataConfig(translator_names=["Lâm Giang"]), "am")
    assert [x.text for x in kept] == ["Trung thu vô nguyệt", "Đêm thu không trăng,", "(Bản A.603 chép khác)"]
    assert [(f, x.text) for f, x in captured] == [("composition", "(Làm mùa thu năm Bính Thìn 1796)"),
                                                  ("translator", "Ngô Linh Ngọc dịch"), ("translator", "Lâm Giang")]
    meta = MetadataResult(layer="am")
    attach_captured(meta, captured)
    row = meta.doc_row("W", meta.documents[0])
    assert row["document_id"] == "W.001" and [f["field"] for f in row["fields"]] == [
        "composition", "translator", "translator"]
    assert row["fields"][0]["source_ids"] == ["vie.P0001.L002"]
    # tắt / lớp han / không khai báo tên: giữ hành vi cũ
    assert capture_note_lines(lines, MetadataConfig(capture_notes=False), "am")[1] == []
    assert capture_note_lines(lines, MetadataConfig(), "han")[1] == []
    assert [f for f, _ in capture_note_lines(lines, MetadataConfig(), "am")[1]] == ["composition", "translator"]


@pytest.mark.parametrize("text, hit", [
    ("KHẢO DỊ:", True), ("KHÁO DỊ:", True), ("KHẢO DỊC: a. Bản A.404: Xanh không chương.", True),
    ("KHAO DI", True), ("NGUYÊN CHÚ: A. Miếu nằm trên bờ sông", True), ("CHÚ THÍCH:", True),
    ("Chú thích của người dịch rất dài", False), ("Khảo dị các bản cho thấy", False), ("DỊCH THƠ", False),
])
def test_nghia_note_heading_fuzzy_end(text, hit):
    from src.segmentation.layers import _compile_layer_matcher
    m = _compile_layer_matcher({"end_patterns": ["^\\s*DỊCH\\s+TH[ƠO]\\b"], "heading_mode": True},
                               "end_patterns", "segmentation.layers.nghia.end_patterns")
    assert m.matches(text) is (hit or text == "DỊCH THƠ")
    other = _compile_layer_matcher({"end_patterns": []}, "end_patterns", "segmentation.layers.am.end_patterns")
    assert not other.matches("KHẢO DỊ:")                                     # chỉ lớp nghia
    off = _compile_layer_matcher({"end_patterns": [], "note_heading_end": False}, "end_patterns",
                                 "segmentation.layers.nghia.end_patterns")
    assert not off.matches("KHẢO DỊC: a.")


def test_pipeline_page_range_excludes_pages_consistently(tmp_path, capsys):
    from src import pipeline
    cfg = {"common": {"work_id": "W", "data_root": str(tmp_path), "pages": "5-320,622-806"},
           "ocr": {"streams": {"pages": "5-320,622-806"}},
           "segmentation": {"ocr_streams": "han_vie", "layers": {"han": {}, "am": {}}},
           "clean": {"common": {"excluded_pages": "321-621"}}}
    path = tmp_path / "W.json"
    path.write_text(json.dumps(cfg), encoding="utf-8")
    assert pipeline.main(["--config", str(path), "--steps", "layers,clean", "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "build_layers --work-id W" in out and "--pages 5-320,622-806" in out
    assert out.count("--exclude-pages 321-621") == 2                        # mọi lớp clean
    cfg["clean"]["common"]["excluded_pages"] = [5, 6]                       # dạng danh sách cũ vẫn chạy
    path.write_text(json.dumps(cfg), encoding="utf-8")
    assert pipeline.main(["--config", str(path), "--steps", "clean", "--dry-run"]) == 0
    assert "--exclude-pages 5,6" in capsys.readouterr().out


@pytest.mark.parametrize("line, hit", [
    ("Lâm Giang", True), ("  Lâm Giang.", True), ("- Lâm  Giang -", True), ("LÂM GIANG", True),
    ("(Lâm Giang)", True),
    ("Theo Lâm Giang, bài này làm năm 1793.", False),          # tên nằm trong câu: giữ
    ("Lâm Giang chú thích thêm", False), ("Lâm", False), ("Lâm Giang Tây", False),
])
def test_translator_name_matches_only_standalone_line(line, hit):
    from src.cleaning.metadata import capture_note_lines
    from src.cleaning.model import MetadataConfig, RawLine
    ln = RawLine("W.001.P0001.L001", 1, line, False, line, [], [], "W.001")
    kept, captured = capture_note_lines([ln], MetadataConfig(translator_names=["Lâm Giang"]), "tho")
    assert (captured == [("translator", ln)]) is hit and (kept == []) is hit


@pytest.mark.parametrize("han, vie, why", [
    (["一"], 22, "lone_han_on_vie_page"), (["隠"], 8, "lone_han_on_vie_page"),
    (["欽定四庫全書"], 30, "lone_han_on_vie_page"),
    (["其二"], 3, None),                     # trang ít chữ Việt: có thể là tiêu đề thật -> giữ
    (["春眠不覺曉"], 25, None),              # câu thơ 5 chữ: giữ
    (["一", "二"], 25, None),               # trang có 2 dòng Hán: không áp
    (["一"], None, None),                   # chưa có OCR Việt: không loại
])
def test_lone_han_reason(han, vie, why):
    from src.ocr.script_filter import lone_han_reason
    assert lone_han_reason(han, vie) == why
    assert lone_han_reason(han, vie, min_vie_lines=0) is None              # tắt


def test_build_layers_drops_lone_han_on_vie_page(tmp_path):
    from src.ocr import build_layers
    layout = DataLayout(tmp_path, "W")
    _write_raw(layout, "paddlevl", 1, "一")
    _write_raw_lines(layout, "qwen", 1, [f"Dòng chữ Việt thứ {i} của trang." for i in range(10)])
    _write_raw(layout, "paddlevl", 2, "一")                                 # trang 2: ít chữ Việt -> giữ
    _write_raw_lines(layout, "qwen", 2, ["Một dòng."])
    assert build_layers.main(["--work-id", "W", "--data-root", str(tmp_path), "--force"]) == 0
    han = layout.ocr_layer_xml_path("han").read_text(encoding="utf-8")
    assert han.count(">一<") == 1
    manifest = json.loads(layout.ocr_layer_manifest_path("build", "han").read_text(encoding="utf-8"))
    page1 = next(p for p in manifest["pages"] if p["page"] == 1)
    assert page1["rejected"][0]["reason"] == "lone_han_on_vie_page"
    assert build_layers.main(["--work-id", "W", "--data-root", str(tmp_path), "--force",
                              "--lone-han-min-vie-lines", "0"]) == 0          # tắt: giữ cả hai
    assert layout.ocr_layer_xml_path("han").read_text(encoding="utf-8").count(">一<") == 2


def _write_raw_lines(layout, engine, page, texts):
    from src.ocr import raw_store
    run = {"key": f"{engine}{page}", "parameters": {"engine": {"name": engine, "adapter_version": 1}},
           "created_at": "2026-01-01T00:00:00+00:00",
           "lines": [{"text": t, "bbox": [0, i * 10, 10, i * 10 + 9], "confidence": 90.0} for i, t in enumerate(texts)],
           "raw": {"bands": [{"band": 1, "flags": [], "generated_text": "x"}]}}
    raw_store.save_engine_page(layout, engine, page, {"schema_version": 1, "work_id": "W", "engine": engine,
                                                      "page": page, "runs": [run]})


def test_replace_with_retry_survives_transient_permission_error(tmp_path, monkeypatch):
    import os
    from src.common import corpus
    calls = []
    real = os.replace

    def flaky(src, dst):
        calls.append(1)
        if len(calls) < 3:
            raise PermissionError(5, "Access is denied")
        return real(src, dst)
    monkeypatch.setattr(corpus.os, "replace", flaky)
    corpus.write_json(tmp_path / "m.json", {"a": 1})
    assert len(calls) == 3 and json.loads((tmp_path / "m.json").read_text(encoding="utf-8")) == {"a": 1}
    calls.clear()
    monkeypatch.setattr(corpus.os, "replace", lambda s, d: (_ for _ in ()).throw(PermissionError(5, "x")))
    with pytest.raises(PermissionError):
        corpus.replace_with_retry(tmp_path / "a", tmp_path / "b", attempts=2, delay=0)


@pytest.mark.parametrize("text, why", [
    ("作为一个人工智能语言模型，我还没学习如何回答这个问题", "model_chatter"),
    ("这里原文本可能存在识别偏差或表意不完整，但按要求照识别结果处理", "model_chatter"),
    ("春眠不覺曉，處處聞啼鳥。", None), ("欲識廬山真面目", None), ("天地玄黃", None),
])
def test_han_model_chatter_rejected(text, why):
    from src.ocr.script_filter import filter_page, han_reject_reason
    assert han_reject_reason(text) == why
    assert (filter_page("han", [text]).kept == []) is (why is not None)


def _audit():
    spec = importlib.util.spec_from_file_location("audit_clean", Path("scripts") / "audit_clean.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_audit_number_loss_and_page_loss():
    audit = _audit()
    nl = audit.number_loss(["Năm 1789 đánh quân Thanh (1)", "205"], ["Năm đánh quân Thanh"])
    assert nl["lost"] == 1 and nl["lost_examples"] == ["1789"]            # (1) và số trang không tính
    import xml.etree.ElementTree as ET
    seg = [ET.fromstring(f'<SEGMENT PAGE="{p}">{"x" * n}</SEGMENT>') for p, n in ((1, 100), (2, 100), (3, 100))]
    st = [ET.fromstring(f'<SEGMENT PAGE="{p}">{"x" * n}</SEGMENT>') for p, n in ((1, 99), (2, 98), (3, 40))]
    pl = audit.page_loss(seg, st)
    assert [o["page"] for o in pl["outliers"]] == ["3"]


def test_audit_punct_issues():
    audit = _audit()
    import xml.etree.ElementTree as ET
    els = [ET.fromstring(f'<SENTENCE ID="s{i}">{t}</SENTENCE>') for i, t in
           enumerate(["Trời đất ， mênh mông", "Ông nói : đi", "Câu (mở ngoặc", "Bình thường."])]
    pu = audit.punct_issues(els)
    assert (pu["fullwidth"], pu["space_before"], pu["unbalanced"]) == (1, 1, 1)   # "，" tính là full-width
