"""Vòng clean trên dữ liệu đầy đủ (reports/clean_full_20261127.md): tiêu đề chạy theo hai luồng, mục chú thích khớp
chữ số chỉ mục, phép đo cổng trong scripts/audit_segmentation.py."""
import importlib.util
from pathlib import Path

from src.cleaning.audit import Audit
from src.cleaning.footnotes import detect_footnotes
from src.cleaning.model import CleanConfig, FootnoteConfig, RawLine
from src.cleaning.page_filter import filter_pages
from src.segmentation import streams
from src.segmentation.model import OcrLine

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("audit_seg", ROOT / "scripts" / "audit_segmentation.py")
audit_seg = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(audit_seg)


def oc(page, idx, text, stream="vie"):
    return OcrLine(page=page, index=idx, text=text, bbox=(0, idx * 50, 100, idx * 50 + 40), writing_system=stream,
                   source_id=f"{stream}.P{page:04d}.L{idx:03d}")


def pages_with_header(header_variants, middle=None):
    out = []
    words = ["sông", "núi", "mây", "gió", "trăng", "hoa", "tuyết", "mưa", "đèn", "thuyền", "cầu", "chợ", "đình", "chùa",
             "rừng", "biển"]
    for p, h in enumerate(header_variants, 10):
        w = words[(p - 10) * 4:(p - 10) * 4 + 4]
        out += [oc(p, 1, h)] + [oc(p, k + 2, f"Câu văn nói về {x} ở đây.") for k, x in enumerate(w)]
        if middle:
            out.insert(len(out) - 2, oc(p, 9, middle))
    return out


def test_edge_running_headers_fuzzy_and_middle_repeats_kept():
    lines = pages_with_header(["Cẩm Đường Nhàn Thoại", "Cẩm Đường Nhàn Thoại", "Cẩm Đường Nhân Thoại",
                               "Cẩm Đường Nhàn Thoại"], middle="Ngươi chẳng thấy:")
    ids = streams.edge_running_header_ids(lines)
    assert ids == {"vie.P0010.L001", "vie.P0011.L001", "vie.P0012.L001", "vie.P0013.L001"}


def test_stream_header_ids_drop_lines_in_page_filter():
    raw = [RawLine("CPO.001.P0010.L001", 10, "Cẩm Dưỡng Nhân Thọ", source_ids=["vie.P0010.L001"]),
           RawLine("CPO.001.P0010.L002", 10, "Nội dung thật.", source_ids=["vie.P0010.L002"])]
    cfg = CleanConfig(layer="nghia", stream_header_ids=frozenset({"vie.P0010.L001"}))
    kept, records = filter_pages(raw, cfg, Audit("W", "nghia"))
    assert [x.text for x in kept] == ["Nội dung thật."]
    assert records[0]["reason"] == "page_header"
    kept_off, _ = filter_pages(raw, CleanConfig(layer="nghia"), Audit("W", "nghia"))
    assert len(kept_off) == 2


def test_note_entry_matching_inline_marker_on_same_page():
    lines = [RawLine("a", 5, "Thành tiền thủy 4 hữu dư quang,"), RawLine("b", 5, "Tiền chính 5 nhân chi lộ,"),
             RawLine("c", 5, "4. Vâng theo lời dặn của vua khi sắp mất."), RawLine("d", 5, "5. Bảo vệ, giúp đỡ nhà vua."),
             RawLine("e", 6, "7. Số bảy không có chỉ mục trên trang này.")]
    on = detect_footnotes(lines, FootnoteConfig(note_entry_markers=True), "am")
    assert on.excluded_ids == {"c", "d"}
    off = detect_footnotes(lines, FootnoteConfig(), "am")
    assert off.excluded_ids == set()


def test_audit_inline_digit_in_nghia_needs_matching_note_entry():
    nums = {3: {"7"}}
    assert audit_seg.inline_note_digit("nghia", "cho rằng: Đạo lý của Xương Lê 7 cũng từ", 3, nums)
    assert not audit_seg.inline_note_digit("nghia", "Ngày 20 tháng 2 lên đường", 3, nums)
    assert audit_seg.inline_note_digit("am", "Ngày 20 tháng 2 lên đường", 3, nums)


def test_audit_leak_share_counts_distinct_rows_and_skips_pending_pages():
    rows = {"am": [{"id": "1", "page": 1, "text": "4. Vâng theo lời dặn của vua [UNCLEAR]"},
                   {"id": "2", "page": 1, "text": "Thành tiền thủy hữu dư quang"},
                   {"id": "3", "page": 2, "text": "[UNCLEAR] [UNCLEAR]"}]}
    r = audit_seg.leakage(rows, skip_pages={2})
    assert r["rows"]["am"] == 2 and r["leak_rows"]["am"] == 1 and r["leak_share"]["am"] == 0.5


def test_gate_verdict():
    leak = {"counts": {"am": {}, "han": {}}, "leak_share": {"am": 0.005, "han": 0.0}, "rows": {"am": 200, "han": 100}}
    ok = audit_seg.gate({"lost": 0, "double": 0}, leak, {"within_10pct_share": 0.9})
    assert ok["pass_1_3"]
    bad = audit_seg.gate({"lost": 1, "double": 0}, leak, {"within_10pct_share": 0.9})
    assert not bad["pass_1_3"] and not bad["checks"]["1_script_and_accounting"]["pass"]


def test_audit_metadata_mapping_prefers_exact_text_and_maps_single_letter_title_line():
    lines = {"vie.P0409.L001": {"page": 409, "stream": "vie", "text": "LÝ HOẠC LUẬN"},
             "vie.P0409.L003": {"page": 409, "stream": "vie", "text": "Lý hoạc luận."},
             "vie.P0058.L012": {"page": 58, "stream": "vie", "text": "TRÊN VÁCH QUÁN BẢO CHÂN"},
             "vie.P0058.L013": {"page": 58, "stream": "vie", "text": "I"}}
    meta = [{"metadata": {"title": {"nghia": [{"text": "LÝ HOẠC LUẬN", "pages": [409]},
                                              {"text": "TRÊN VÁCH QUÁN BẢO CHÂN I", "pages": [58]}]}},
             "uncertain": {}}]
    out = audit_seg.metadata_source_ids(meta, lines)
    assert "vie.P0409.L003" not in out and out["vie.P0409.L001"] == "metadata:title"
    assert out["vie.P0058.L013"] == "metadata:title"


def test_export_clean_text_defaults_to_cleaning_text_dir_and_packages(tmp_path):
    from src.common.corpus import DataLayout
    from src.common.sentences import Sentence, save_sentences
    layout = DataLayout(tmp_path, "W")
    save_sentences(layout, "han", [Sentence("W.han.00001", "天地玄黃", [1], document_id="W.001")])
    save_sentences(layout, "am", [Sentence("W.am.00001", "Thiên địa huyền hoàng", [1], document_id="W.001")])
    spec = importlib.util.spec_from_file_location("export_clean_text", ROOT / "scripts" / "export_clean_text.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    pkg = tmp_path / "pkg"
    assert mod.main(["--work-id", "W", "--data-root", str(tmp_path), "--package-root", str(pkg), "--topic", "DT01"]) == 0
    assert (layout.cleaning_root / "text" / "han.txt").read_text(encoding="utf-8") == "天地玄黃\n"
    assert not (layout.work_dir / "07_export" / "03_text_sach").exists()
    assert (pkg / "DT01_W" / "03_text_sach" / "viet_am.txt").exists()
    assert (pkg / "DT01_W" / "03_text_sach" / "mapping.tsv").exists()


def test_export_ocr_review_splits_sheets_by_member_range(tmp_path):
    spec = importlib.util.spec_from_file_location("export_ocr_review", ROOT / "scripts" / "export_ocr_review.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    rows = [{"loai": "qwen_low_conf", "trang": p, "id": f"vie.P{p:04d}.L001", "chi_tiet": "conf 40",
             "van_ban": "x", "anh_trang": "a.png"} for p in (10, 623, 624, 900)]
    sheets = mod.split_sheets(rows, "CPO_19")
    assert [len(sheets[k]) for k in ("tv2_5-623", "tv3_624-833", "khac")] == [2, 1, 1]
    out = tmp_path / "r.xlsx"
    mod.write_xlsx(out, sheets)
    from openpyxl import load_workbook
    wb = load_workbook(out)
    assert wb.sheetnames == ["tv2_5-623", "tv3_624-833", "khac"]
    assert [c.value for c in wb["tv2_5-623"][1]] == mod.COLUMNS


def test_am_markers_extended_after_punct_and_line_end_only_when_enabled():
    from src.cleaning.artifacts import clean_ocr_artifacts
    from src.cleaning.model import OCRCleaningConfig
    on = OCRCleaningConfig(am_markers_extended=True)
    assert clean_ocr_artifacts("Nhị khanh huân cựu đại thần, 2 quốc gia trụ thạch", on, layer="am").text == \
        "Nhị khanh huân cựu đại thần, quốc gia trụ thạch"
    assert clean_ocr_artifacts("Tỳ bà đình 1", on, layer="am").text == "Tỳ bà đình"
    assert clean_ocr_artifacts("BÀI 2", on, layer="am").text == "BÀI 2"
    assert clean_ocr_artifacts("Tỳ bà đình 1", on, layer="nghia").text == "Tỳ bà đình 1"
    assert clean_ocr_artifacts("Tỳ bà đình 1", OCRCleaningConfig(), layer="am").text == "Tỳ bà đình 1"


def test_source_lines_captured_only_when_enabled_and_title_like():
    from src.cleaning.metadata import capture_note_lines
    from src.cleaning.model import MetadataConfig
    lines = [RawLine("a", 1, "Trích Dật thi lược toản"), RawLine("b", 1, "Trích thủy xao thiềm sổ cử bôi."),
             RawLine("c", 1, "Theo Tang thương ngẫu lục, ông là người huyện Đông Ngạc.")]
    kept, cap = capture_note_lines(lines, MetadataConfig(capture_source_lines=True), "am")
    assert [f for f, _ in cap] == ["source"] and cap[0][1].id == "a"
    kept_off, cap_off = capture_note_lines(lines, MetadataConfig(), "am")
    assert cap_off == []


def test_note_entry_marker_after_punctuation():
    lines = [RawLine("a", 9, "Nhị khanh huân cựu đại thần, 2 quốc gia trụ thạch,"),
             RawLine("b", 9, "2. Hai người là bậc đại thần có cựu và có công lao.")]
    assert detect_footnotes(lines, FootnoteConfig(note_entry_markers=True), "am").excluded_ids == {"b"}


def test_stream_page_note_numbers_strip_nghia_markers_only_for_listed_numbers():
    from src.cleaning.artifacts import clean_ocr_artifacts
    from src.cleaning.model import OCRCleaningConfig
    cfg = OCRCleaningConfig(page_note_numbers={104: {"3", "4"}})
    ln = RawLine("x", 104, "Đảo Bồng 3 khói tỏa, ngày 20 tháng 2 lên đường.")
    assert clean_ocr_artifacts(ln.text, cfg, None, ln, layer="nghia").text == \
        "Đảo Bồng khói tỏa, ngày 20 tháng 2 lên đường."
    other = RawLine("y", 105, "Đảo Bồng 3 khói tỏa.")
    assert clean_ocr_artifacts(other.text, cfg, None, other, layer="nghia").text == "Đảo Bồng 3 khói tỏa."
    entry = RawLine("z", 104, "3. Đảo Bồng: núi tiên.")
    assert clean_ocr_artifacts(entry.text, cfg, None, entry, layer="nghia").text == "3. Đảo Bồng: núi tiên."


def test_numbering_word_keeps_bai_number_but_strips_title_marker():
    from src.cleaning.artifacts import clean_ocr_artifacts
    from src.cleaning.model import OCRCleaningConfig
    on = OCRCleaningConfig(am_markers_extended=True)
    assert clean_ocr_artifacts("VŨ HOÀNG HẬU TANG, QUỐC ÂM VĂN 7", on, layer="am").text == \
        "VŨ HOÀNG HẬU TANG, QUỐC ÂM VĂN"
    assert clean_ocr_artifacts("Kỳ 3", on, layer="am").text == "Kỳ 3"


def test_source_line_with_comma_and_stream_markers_for_entries():
    from src.cleaning.metadata import capture_note_lines
    from src.cleaning.model import MetadataConfig
    _, cap = capture_note_lines([RawLine("a", 1, "Trích Dụ Am văn tập, quyển VII")],
                                MetadataConfig(capture_source_lines=True), "am")
    assert [f for f, _ in cap] == ["source"]
    lines = [RawLine("b", 156, "Ngô đình nay đổi là Đường đình."), RawLine("c", 156, "1. Đình này nguyên tên là Ngô đình.")]
    res = detect_footnotes(lines, FootnoteConfig(stream_page_markers={156: {"1"}}), "nghia")
    assert res.excluded_ids == {"c"}


def test_consecutive_note_entry_after_marked_one_and_line_end_marker_before_punct():
    lines = [RawLine("a", 126, "người thì phụng tuân cố mệnh, 4 bảo dục trẫm cung,"),
             RawLine("b", 126, "4. Vâng theo lời dặn của vua khi sắp mất."), RawLine("c", 126, "5. Bảo vệ, giúp đỡ nhà vua."),
             RawLine("d", 127, "6. Chỉ Vũ Văn Dung cầm quyền cai giữ Thăng Long."), RawLine("e", 130, "7. Không liền mạch.")]
    res = detect_footnotes(lines, FootnoteConfig(note_entry_markers=True), "am")
    assert res.excluded_ids == {"b", "c", "d"}
    from src.cleaning.artifacts import clean_ocr_artifacts
    from src.cleaning.model import OCRCleaningConfig
    assert clean_ocr_artifacts("Can mao thoi xuat giai tinh mao 4.", OCRCleaningConfig(am_markers_extended=True),
                               layer="am").text == "Can mao thoi xuat giai tinh mao."


def test_split_point_moves_translation_glued_after_phien_am():
    from src.segmentation.am_detect import load_folded_readings, split_point
    rd = load_folded_readings(str(ROOT / "resources" / "han_viet.tsv"))
    han = ["天氣和暢雖六陰", "節候盛陽常自如"]
    am = ["Thiên khí hòa sướng tuy lục âm,", "Tiết hậu thịnh dương thường tự như.",
          "Xứ ấy trời đất ấm áp, mùa đông không lạnh lắm,", "mùa hè cũng dễ chịu, quanh năm cây cối tốt tươi."]
    assert split_point(han, am, rd) == 2
    assert split_point(han, am[:2], rd) is None
    assert split_point(["八丁么站当仅购买"], am, rd) is None          # chữ Hán rác: phần trước không khớp -> không tách


def test_split_point_ignores_running_headers_and_note_entries():
    from src.segmentation.am_detect import load_folded_readings, split_point
    rd = load_folded_readings(str(ROOT / "resources" / "han_viet.tsv"))
    han = ["天氣和暢雖六陰", "節候盛陽常自如", "稼穡"]
    am = ["Thiên khí hòa sướng tuy lục âm,", "Tiết hậu thịnh dương thường tự như.",
          "Sưu tầm và khảo luận tác phẩm chữ Hán của người Việt Nam", "(2) Bản B: sau chữ này còn có hai chữ khác nữa",
          "Giá sắc."]
    assert split_point(han, am, rd, [False, False, True, False, False]) is None


def test_verse_tho_split_tail_of_nghia():
    from src.segmentation.am_detect import verse_tho_split
    am = [f"Câu phiên âm số {x} bảy chữ," for x in "abcdefgh"]
    nghia = [f"Câu dịch nghĩa thứ {x} của bài thơ dài hơn một chút," for x in "abcdefgh"] + \
            [f"Dịch thơ câu {x} bảy chữ," for x in "abcdefg"]
    assert verse_tho_split(am, nghia) == 8
    assert verse_tho_split(am, nghia[:10]) is None                 # đuôi quá ngắn / không đủ tỉ lệ
    prose_tail = nghia[:8] + ["Đây là một đoạn văn xuôi rất dài nói về hoàn cảnh sáng tác bài thơ này của tác giả."] * 6
    assert verse_tho_split(am, prose_tail) is None


def test_split_point_does_not_split_short_four_syllable_lines():
    from src.segmentation.am_detect import load_folded_readings, split_point
    rd = load_folded_readings(str(ROOT / "resources" / "han_viet.tsv"))
    han = ["福德所及莫不來王", "恭祝聖度百世其量", "聲教所暨莫不來享"]
    am = ["Phúc đức sở cập,", "Mạc bất lai vương.", "Cung chúc thánh độ,", "Bách thế kỳ lượng.", "Thanh giáo sở ký,",
          "Mạc bất lai hưởng."]
    assert split_point(han, am, rd) is None
