"""Regression test trích xuất metadata cấp tài liệu (tiêu đề/tác giả/quy trách).

Mô phỏng ĐÚNG cấu trúc CPO_18: FILE -> SECT -> PAGE -> STC/LINE, ba lớp han/am/nghia;
nội dung lấy từ dữ liệu thật ở data/CPO_18/02_extraction (rút gọn).

Chạy từ thư mục gốc repo:  python -m pytest tests/test_metadata.py -q
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.common.corpus import DataLayout, RawPage, build_document, make_meta, write_xml  # noqa: E402
from src.common.sentences import load_sentences                                         # noqa: E402
from src.segmentation import serialization as segxml                                    # noqa: E402
from src.cleaning import MetadataConfig, RawLine, extract_metadata, read_raw_lines   # noqa: E402
from src.cleaning.metadata import (aggregate_document_metadata, aggregate_file_metadata,   # noqa: E402
                                   read_file_meta, write_layer_metadata)
from src.preprocessing import clean                                                       # noqa: E402

WORK = "CPO_18"
DOC = "CPO_18.001"

# --- nội dung rút gọn từ CPO_18 (giữ nguyên các dòng metadata để test nhận diện) --- #
HAN_PAGES = {
    205: ["1﹒ 與高明二法師雙佛不見形書 |", "李深", "一 * 流炳交州刺史",
          "夫這處清虛 ， 四大理常 ， 而有法門 ， 妙山群",
          "域。若稱其巨能利物 ， 度脫無量炳教 ， 何早不見",
          "真形於世 ﹖ 真空說而無實耶 ﹖",
          "弟子李淼和南 ﹕",
          "( 弘明集卷十一 = 見 ” 全上古三代秦漢", "三國公鞮文 ﹒ 全宅文卻五十七 ﹚"],
}
AM_PAGES = {
    205: ["5au đây là toàn văn Sáu bức thư, có kèm theo phần",
          "phiên âm, dịch nghĩa và chú thích của chúng tôi.",
          ": 1. 9U ở) B]— 2 ÉP ## 1$ “1,7 $ |",
          "2á",
          "PHIÊN ÂM :",
          "DỮ CAO, MINH NHỊ PHÁP SƯ",
          "NẠN PHẬT BẤT KIẾN HÌNH THƯ",
          "LÝ MIỄU",
          "* Miễu vì Giao Châu Thứ sử |",
          "Phù Đạo xứ thanh hư, tứ đại lý thường, nhi hữu",
          "pháp môn, diệu xuất quần vực. Nhược xưng kỳ xảo năng",
          "Đệ tử Lý Miễu hòa nam.",
          "( Hoằng minh tập, Quyển thập nhất ;",
          "Toàn Thượng cổ, Tam đại, Tần, Hán,",
          "Tam quốc, Lục triều uăn,Toàn Tổng ,",
          "uăn. Quyên ngũ thập thất )",
          "DỊCH NGHĨA :"],
    206: ["THƯ GỬI HAI VỊ PHÁP SƯ CAO VÀ MINH",
          "ĐỀ VẶN HỎI VỀ VIỆC KHÔNG THẤY HÌNH THỂ PHẬT",
          "Rằng, chỗ Đạo thanh hư, tứ đại lẽ thường, nhờ"],
}
NGHIA_PAGES = {
    206: ["Đệ tử Lý Miễu hòa nam.",
          "( Hoằng minh tập, Q. 11 ; Toàn Thương",
          "cổ, Tam đại, Tôn, Hán, Tam quốc, Lục",
          "triều uăn, phần Toàn Tống uăn, Q.57 )",
          "DỊCH NGHĨA :",
          "THƯ GỬI HAI VỊ PHÁP SƯ CAO VÀ MINH",
          "ĐỀ VẶN HỎI VỀ VIỆC KHÔNG THẤY HÌNH THỂ PHẬT",
          "LÝ MIỄU",
          "* Miễu là Thứ sử Giao Châu",
          "Rằng, chỗ Đạo thanh hư, tứ đại lẽ thường, nhờ",
          "Đệ tử Lý Miễu kính lễ !,",
          "( Hoằng minh tập, Q. 11 ; Toàn Thương",
          "cổ, Tam đại, Tôn, Hán, Tam quốc, Lục",
          "triều uăn, phần Toàn Tống uăn, Q.57 )",
          "CHÚ THÍCH :",
          "(1) Pháp sư : người am tường kinh Phật, có khả năng giảng",
          "dạy cho người khác."],
}


def write_layer(root: Path, layer: str, pages: dict[int, list[str]]) -> None:
    layout = DataLayout(root, WORK)
    layout.ensure()
    doc = build_document(WORK, layer, make_meta(layer, title="Demo CPO_18", method="OCR: fixture"),
                         [RawPage(p, lines) for p, lines in pages.items()])
    write_xml(doc, layout.layer_xml(layer), force=True)


@pytest.fixture()
def root(tmp_path):
    for layer, pages in (("han", HAN_PAGES), ("am", AM_PAGES), ("nghia", NGHIA_PAGES)):
        write_layer(tmp_path, layer, pages)
    return tmp_path


def meta_cfg(**kw) -> MetadataConfig:
    return MetadataConfig(**kw)


# --------------------------------------------------------------------------- #
# 1. Lớp Hán: tiêu đề (có số thứ tự), tác giả, quy trách
# --------------------------------------------------------------------------- #
def test_han_title_author_attribution(root):
    raw = read_raw_lines(root / WORK / "02_extraction" / "han.xml")
    cfg = meta_cfg(title_patterns=[r"^\s*\d{1,3}\s*[·.．、﹒)]"],
                   attribution_patterns=[r"^.{0,3}\*"], author_max_len=8)
    kept, res = extract_metadata(raw, cfg, "han")
    doc = res.documents[0]
    assert doc.document_id == DOC
    by = {f.field: f for f in doc.fields}
    assert by["title"].text == "1﹒ 與高明二法師雙佛不見形書 |"
    assert by["title"].source_ids == [f"{DOC}.P205.S001"] and by["title"].pages == [205]
    assert by["author"].text == "李深" and by["author"].source_ids == [f"{DOC}.P205.S002"]
    assert by["attribution"].text == "一 * 流炳交州刺史" and by["attribution"].confidence == "high"
    # dòng chính bắt đầu bằng câu thân bài ĐẦU TIÊN, không còn tiêu đề/tác giả
    assert kept[0].text.startswith("夫這處清虛")
    assert all("李深" not in ln.text and "與高明" not in ln.text for ln in kept)


# --------------------------------------------------------------------------- #
# 2. Lớp Âm: marker PHIÊN ÂM, tiêu đề ghép 2 dòng, front matter / rác OCR
# --------------------------------------------------------------------------- #
def test_am_metadata_and_front_matter(root):
    raw = read_raw_lines(root / WORK / "02_extraction" / "am.xml")
    cfg = meta_cfg(section_start=[r"^PHIÊN ÂM\s*:?$"], section_end=[r"^DỊCH NGHĨA\s*:?$"],
                   attribution_patterns=[r"^\*"])
    kept, res = extract_metadata(raw, cfg, "am")
    doc = res.documents[0]
    by = {f.field: f for f in doc.fields}
    assert by["title"].text == "DỮ CAO, MINH NHỊ PHÁP SƯ NẠN PHẬT BẤT KIẾN HÌNH THƯ"  # ghép 2 dòng
    assert by["title"].source_ids == [f"{DOC}.P205.L006", f"{DOC}.P205.L007"]
    assert by["title"].pages == [205]
    assert by["author"].text == "LÝ MIỄU" and by["author"].source_ids == [f"{DOC}.P205.L008"]
    assert by["attribution"].text == "* Miễu vì Giao Châu Thứ sử |"
    # dòng trước marker: lời dẫn = front_matter, dòng rác = ocr_noise; marker = section_marker
    reasons = {(ln.id, reason) for ln, reason in doc.excluded}
    assert (f"{DOC}.P205.L001", "front_matter") in reasons          # lời dẫn "Sau đây là..."
    assert (f"{DOC}.P205.L003", "ocr_noise") in reasons             # ": 1. 9U ở) B]—..."
    assert (f"{DOC}.P205.L005", "section_marker") in reasons        # "PHIÊN ÂM :"
    assert (f"{DOC}.P205.L017", "section_marker") in reasons        # "DỊCH NGHĨA :" (marker kết thúc am)
    assert (f"{DOC}.P206.L001", "after_section_end") in reasons     # phần sau marker kết thúc
    # dòng chính bắt đầu đúng ở câu thân bài đầu tiên
    assert kept[0].text.startswith("Phù Đạo xứ")
    assert any(ln.text == "Đệ tử Lý Miễu hòa nam." for ln in kept)  # chữ ký cuối vẫn trong thân bài
    assert all("LÝ MIỄU" != ln.text and "DỮ CAO" not in ln.text for ln in kept)


# --------------------------------------------------------------------------- #
# 3. Lớp Nghĩa: tiêu đề ghép 2 dòng, CHÚ THÍCH bị loại, chữ ký giữ lại
# --------------------------------------------------------------------------- #
def test_nghia_metadata_and_footnote_section(root):
    raw = read_raw_lines(root / WORK / "02_extraction" / "nghia.xml")
    cfg = meta_cfg(section_start=[r"^DỊCH NGHĨA\s*:?$"], section_end=[r"^CHÚ THÍCH\s*:?$"],
                   attribution_patterns=[r"^\*"])
    kept, res = extract_metadata(raw, cfg, "nghia")
    doc = res.documents[0]
    by = {f.field: f for f in doc.fields}
    assert by["title"].text == "THƯ GỬI HAI VỊ PHÁP SƯ CAO VÀ MINH ĐỀ VẶN HỎI VỀ VIỆC KHÔNG THẤY HÌNH THỂ PHẬT"
    assert len(by["title"].source_ids) == 2
    assert by["author"].text == "LÝ MIỄU" and by["author"].confidence == "high"
    assert by["attribution"].text == "* Miễu là Thứ sử Giao Châu"
    assert by["author"].pages == [206] and by["author"].source_ids != by["attribution"].source_ids
    reasons = {(ln.id, reason) for ln, reason in doc.excluded}
    assert (f"{DOC}.P206.L001", "front_matter") in reasons           # "Đệ tử Lý Miễu hòa nam." (trước marker)
    assert (f"{DOC}.P206.L005", "section_marker") in reasons        # DỊCH NGHĨA :
    assert (f"{DOC}.P206.L015", "section_marker") in reasons        # CHÚ THÍCH :
    assert (f"{DOC}.P206.L016", "after_section_end") in reasons     # mục (1)...
    assert kept[0].text.startswith("Rằng, chỗ Đạo thanh hư")
    assert any(ln.text.startswith("Đệ tử Lý Miễu kính lễ") for ln in kept)


# --------------------------------------------------------------------------- #
# 4. Hai SECT: metadata không bao giờ trộn lẫn
# --------------------------------------------------------------------------- #
def test_two_documents_never_mixed(tmp_path):
    layout = DataLayout(tmp_path, "T_02")
    doc = build_document("T_02", "han", make_meta("han", title="t", method="m"),
                         [RawPage(205, ["1﹒ Thư thứ nhất", "阮甲", "* Ghi chú A",
                                        "Thân mời ông đến chơi , rất mong.", "( Trích tập I )"],
                                  heading="Thư 1"),
                          RawPage(206, ["2﹒ Thư thứ hai", "陳乙", "* Ghi chú B",
                                        "Mời bà đến dự , xin cảm ơn."], heading="Thư 2")])
    layout.ensure()
    write_xml(doc, layout.layer_xml("han"), force=True)
    raw = read_raw_lines(layout.layer_xml("han"))
    cfg = meta_cfg(title_patterns=[r"^\s*\d{1,3}\s*[·.．、﹒)]"],
                   attribution_patterns=[r"^.{0,3}\*"], author_max_len=8)
    kept, res = extract_metadata(raw, cfg, "han")
    assert [d.document_id for d in res.documents] == ["T_02.001", "T_02.002"]
    f1 = {f.field: f for f in res.documents[0].fields}
    f2 = {f.field: f for f in res.documents[1].fields}
    assert f1["title"].text == "1﹒ Thư thứ nhất" and f1["author"].text == "阮甲"
    assert f2["title"].text == "2﹒ Thư thứ hai" and f2["author"].text == "陳乙"
    assert f1["attribution"].source_ids != f2["attribution"].source_ids
    assert all("阮甲" not in ln.text and "陳乙" not in ln.text for ln in kept)


# --------------------------------------------------------------------------- #
# 5. Không cấu hình = no-op tuyệt đối; --metadata-keep giữ dòng trong corpus chính
# --------------------------------------------------------------------------- #
def test_no_config_is_noop(root):
    raw = read_raw_lines(root / WORK / "02_extraction" / "am.xml")
    kept, res = extract_metadata(raw, meta_cfg(), "am")
    assert res.status == "no_op" and res.documents == []
    assert [ln.id for ln in kept] == [ln.id for ln in raw]


def test_exclude_false_keeps_lines_in_main_stream(root):
    raw = read_raw_lines(root / WORK / "02_extraction" / "am.xml")
    cfg = meta_cfg(section_start=[r"^PHIÊN ÂM\s*:?$"], attribution_patterns=[r"^\*"],
                   exclude_from_main_text=False)
    kept, res = extract_metadata(raw, cfg, "am")
    assert res.status == "ok" and res.documents[0].fields          # vẫn nhận diện đủ
    assert [ln.id for ln in kept] == [ln.id for ln in raw]          # nhưng không loại dòng nào


def test_uncertain_lines_reviewed_not_silently_dropped(root):
    raw = read_raw_lines(root / WORK / "02_extraction" / "am.xml")
    # dòng hoa vô nghĩa sau marker -> uncertain (trình review, không xoá lặng lẽ)
    raw.insert(6, RawLine(f"{DOC}.P205.LXXX", 205, "＃＄％＆＇（ＯＫ"))
    cfg = meta_cfg(section_start=[r"^PHIÊN ÂM\s*:?$"], attribution_patterns=[r"^\*"])
    kept, res = extract_metadata(raw, cfg, "am")
    doc = res.documents[0]
    assert any(u.text == "＃＄％＆＇（ＯＫ" for u in doc.uncertain)
    assert any(ln.id == f"{DOC}.P205.LXXX" for ln, _ in res.uncertain)
    assert all(ln.id != f"{DOC}.P205.LXXX" for ln in kept)


# --------------------------------------------------------------------------- #
# 6. Ghép file metadata: document_metadata.jsonl + file_metadata.json
# --------------------------------------------------------------------------- #
def test_aggregate_document_and_file_metadata(root, tmp_path):
    layout = DataLayout(root, WORK)
    out_dir = root / WORK / "05_cleaning"
    out_dir.mkdir()
    for layer, cfg in (("han", meta_cfg(title_patterns=[r"^\s*\d{1,3}\s*[·.．、﹒)]"],
                                        attribution_patterns=[r"^.{0,3}\*"], author_max_len=8)),
                       ("am", meta_cfg(section_start=[r"^PHIÊN ÂM\s*:?$"], section_end=[r"^DỊCH NGHĨA\s*:?$"],
                                       attribution_patterns=[r"^\*"])),
                       ("nghia", meta_cfg(section_start=[r"^DỊCH NGHĨA\s*:?$"],
                                          section_end=[r"^CHÚ THÍCH\s*:?$"], attribution_patterns=[r"^\*"]))):
        raw = read_raw_lines(layout.layer_xml(layer))
        kept, res = extract_metadata(raw, cfg, layer)
        rows = [d.to_row(WORK, layer) for d in res.documents]
        write_layer_metadata(out_dir, layer, rows)
    aggregate_document_metadata(out_dir, WORK)
    aggregate_file_metadata(layout, out_dir)
    rows = segxml.read_document_metadata(out_dir / "document_metadata.xml")
    assert len(rows) == 1 and rows[0]["document_id"] == DOC
    meta = rows[0]["metadata"]
    assert meta["title"]["han"][0]["text"] == "1﹒ 與高明二法師雙佛不見形書 |"
    assert meta["title"]["am"][0]["source_ids"] == [f"{DOC}.P205.L006", f"{DOC}.P205.L007"]
    assert meta["title"]["nghia"][0]["pages"] == [206]
    assert meta["author"]["han"][0]["text"] == "李深"
    assert meta["author"]["nghia"][0]["text"] == "LÝ MIỄU"
    assert meta["attribution"]["am"][0]["text"] == "* Miễu vì Giao Châu Thứ sử |"
    assert rows[0]["source_pages"] == [205, 206]
    fmeta = json.loads((out_dir / "file_metadata.json").read_text(encoding="utf-8"))
    assert fmeta["files"]["han"]["LANGUAGE"] == "Hán" and "TITLE" in fmeta["files"]["am"]
    assert read_file_meta(layout.layer_xml("nghia"))["VERSION"] == "Semantic translation"


# --------------------------------------------------------------------------- #
# 7. Đầu-cuối qua CLI clean: metadata không lọt vào corpus / document_metadata.jsonl đủ 3 lớp
# --------------------------------------------------------------------------- #
def run_clean(root, layer, *extra):
    argv = ["--work-id", WORK, "--layer", layer, "--data-root", str(root), "--force", *extra]
    assert clean.main(argv) == 0


def test_clean_cli_metadata_end_to_end(root):
    run_clean(root, "han", "--meta-title-pattern", r"^\s*\d{1,3}\s*[·.．、﹒)]",
              "--meta-attribution-pattern", r"^.{0,3}\*", "--meta-author-max-len", "8")
    run_clean(root, "am", "--meta-section-start", r"^PHIÊN ÂM\s*:?$",
              "--meta-section-end", r"^DỊCH NGHĨA\s*:?$", "--meta-attribution-pattern", r"^\*")
    run_clean(root, "nghia", "--meta-section-start", r"^DỊCH NGHĨA\s*:?$",
              "--meta-section-end", r"^CHÚ THÍCH\s*:?$", "--meta-attribution-pattern", r"^\*")
    out = root / WORK / "05_cleaning"
    rows = segxml.read_document_metadata(out / "document_metadata.xml")
    assert len(rows) == 1 and rows[0]["document_id"] == DOC
    assert set(rows[0]["metadata"]["title"]) == {"han", "am", "nghia"}
    assert set(rows[0]["metadata"]["author"]) == {"han", "am", "nghia"}
    # corpus chính không còn tiêu đề / tác giả / quy trách
    for layer in ("han", "am", "nghia"):
        sents = load_sentences(DataLayout(root, WORK), layer)
        assert sents, layer
        for s in sents:
            assert "LÝ MIỄU" not in s.text
            assert "DỮ CAO" not in s.text and "THƯ GỬI HAI VỊ" not in s.text
            assert "* Miễu" not in s.text
        assert not any(s.text.startswith("PHIÊN ÂM") or s.text.startswith("DỊCH NGHĨA") for s in sents)
    am_texts = [s.text for s in load_sentences(DataLayout(root, WORK), "am")]
    assert am_texts[0].startswith("Phù Đạo xứ")
    assert any("Đệ tử Lý Miễu hòa nam." in t for t in am_texts)      # chữ ký cuối còn nguyên
    nghia_texts = [s.text for s in load_sentences(DataLayout(root, WORK), "nghia")]
    assert nghia_texts[0].startswith("Rằng, chỗ Đạo thanh hư")
    assert not any("Pháp sư" in t for t in nghia_texts)               # CHÚ THÍCH không vào corpus
    # dropped.tsv có đầy đủ dòng metadata với lý do rõ ràng
    dropped = (out / "am.dropped.tsv").read_text(encoding="utf-8")
    assert "\tfront_matter\t" in dropped and "\tsection_marker\t" in dropped
    assert "\tmetadata_title\t" in dropped and "\tmetadata_author\t" in dropped
