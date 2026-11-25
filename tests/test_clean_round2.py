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
    assert (out / "viet.txt").read_text(encoding="utf-8").splitlines() == [
        "Thiên địa huyền hoàng", "Trời đất đen vàng", "Vũ trụ hồng hoang"]      # theo tài liệu, rồi theo lớp
    rows = (out / "mapping.tsv").read_text(encoding="utf-8").splitlines()
    assert rows[1] == "han.txt\t1\tW.han.00001\tW.001\than\t1\than.P0001.L001"
    assert sum(r.startswith("viet.txt\t") for r in rows) == 3


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
