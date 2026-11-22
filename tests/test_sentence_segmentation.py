"""Unit and integration tests for the 3-level segmentation architecture.

Covers:
1. Document boundary detection
2. Layer assignment
3. Chinese sentence splitting
4. Vietnamese sentence splitting
5. Multi-line sentence reconstruction
6. Cross-page sentence reconstruction
7. Source ID and page traceability
8. Stable sentence IDs
9. Sentence-level cleaning
10. Alignment compatibility
11. Regression comparison with the current CPO_18 output (pages 205-206)
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.alignment import align
from src.alignment.serialization import read_alignments
from src.cleaning import pipeline as clean_pipeline
from src.cleaning.model import CleanConfig, RawLine
from src.cleaning.sentence_cleaner import (
    SentenceCleaner,
    clean_sentence_han,
    clean_sentence_vi,
    clean_sentences,
)
from src.common.corpus import DataLayout
from src.common.sentences import Sentence, alignment_dir, load_sentences, save_sentences
from src.ocr import raw_store
from src.segmentation import pipeline as seg_pipeline
from src.segmentation import sentences as sent_pipeline
from src.segmentation import serialization as segxml
from src.segmentation.sentences import (
    SentenceConfig,
    build_stream,
    make_sentences,
    split_han,
    split_vi,
)
from src.validation.validate import validate_corpus


# --------------------------------------------------------------------------- #
# Helpers & Fixtures
# --------------------------------------------------------------------------- #
def add_ocr(layout: DataLayout, writing_system: str, page: int, texts: list[str], keep: str) -> None:
    key = f"{writing_system}-{page}"
    store = {
        "schema_version": 1,
        "work_id": layout.work_id,
        "writing_system": writing_system,
        "page": page,
        "runs": [{
            "key": key,
            "parameters": {},
            "created_at": "2026-01-01T00:00:00+00:00",
            "image": f"page_{page:04d}.png",
            "image_size": [100, 100],
            "crop_box": [0, 0, 100, 100],
            "raw": {},
            "lines": [
                {"text": t, "bbox": [0, i * 10, 10, i * 10 + 8], "confidence": 90.0}
                for i, t in enumerate(texts)
            ],
        }],
    }
    raw_store.save_page(layout, writing_system, page, store)
    manifest = raw_store.load_manifest(layout, writing_system) or {
        "schema_version": 1,
        "work_id": layout.work_id,
        "writing_system": writing_system,
        "lang": writing_system,
        "keep": keep,
        "engines": {"fake": {"name": "fake"}},
        "parameters": {},
        "pages": [],
        "low_conf_pages": [],
        "empty_pages": [],
        "review_required": True,
    }
    manifest["pages"].append({
        "page": page,
        "image": f"page_{page:04d}.png",
        "image_sha256": "x",
        "raw": f"03_ocr/raw/{writing_system}/page_{page:04d}.json",
        "selected_engine": "fake",
        "selected_key": key,
        "confidence": 90.0,
        "empty": not texts,
        "n_lines": len(texts),
        "lines": [
            {"text": t, "bbox": [0, i * 10, 10, i * 10 + 8], "confidence": 90.0}
            for i, t in enumerate(texts)
        ],
        "engines": [{"engine": "fake", "key": key, "cache_hit": False}],
    })
    raw_store.write_manifest(layout, writing_system, manifest)


# --------------------------------------------------------------------------- #
# 1. Chinese sentence splitting
# --------------------------------------------------------------------------- #
def test_chinese_sentence_splitting():
    # Various terminators and closers
    text = "夫道處清虛，四大理常！而有法門？妙出群域；何居不見真形於世「真形」？真空說而無實耶。"
    spans = split_han(text)
    splits = [text[a:b] for a, b in spans]
    assert splits == [
        "夫道處清虛，四大理常！",
        "而有法門？",
        "妙出群域；",
        "何居不見真形於世「真形」？",
        "真空說而無實耶。",
    ]

    # Small punctuation variants
    text_small = "真形於世﹖真空說而無實耶﹕今正就尋西方根源。"
    spans_small = split_han(text_small)
    assert [text_small[a:b] for a, b in spans_small] == [
        "真形於世﹖",
        "真空說而無實耶﹕",
        "今正就尋西方根源。",
    ]


# --------------------------------------------------------------------------- #
# 2. Vietnamese sentence splitting
# --------------------------------------------------------------------------- #
def test_vietnamese_sentence_splitting():
    cfg = SentenceConfig(layer="am", split="punct", split_colon=True)
    text = (
        "Ông ở TP. Hồ Chí Minh lúc 5 giờ. GS. Nguyễn đến xem; thấy tốt: "
        "khen thưởng! Số 205.1 là trang. Đúng không? Vâng."
    )
    spans = split_vi(text, cfg)
    splits = [text[a:b] for a, b in spans]
    assert splits == [
        "Ông ở TP. Hồ Chí Minh lúc 5 giờ.",
        "GS. Nguyễn đến xem;",
        "thấy tốt:",
        "khen thưởng!",
        "Số 205.1 là trang.",
        "Đúng không?",
        "Vâng.",
    ]


# --------------------------------------------------------------------------- #
# 3. Multi-line sentence reconstruction
# --------------------------------------------------------------------------- #
def test_multiline_sentence_reconstruction():
    lines = [
        RawLine("DOC.001.P001.L001", 1, "Phù Đạo xứ thanh hư,", raw_text="Phù Đạo xứ thanh hư,"),
        RawLine("DOC.001.P001.L002", 1, "tứ đại lý thường,", raw_text="tứ đại lý thường,"),
        RawLine("DOC.001.P001.L003", 1, "nhi hữu pháp môn, diệu xuất quần vực.", raw_text="nhi hữu pháp môn, diệu xuất quần vực."),
    ]
    lines[0].source_ids = ["vie.P001.L001"]
    lines[1].source_ids = ["vie.P001.L002"]
    lines[2].source_ids = ["vie.P001.L003"]

    sents, dropped = make_sentences("TEST", "am", lines, SentenceConfig(layer="am"))
    assert dropped == []
    assert len(sents) == 1
    s = sents[0]
    assert s.text == "Phù Đạo xứ thanh hư, tứ đại lý thường, nhi hữu pháp môn, diệu xuất quần vực."
    assert s.pages == [1]
    assert s.src_ids == ["DOC.001.P001.L001", "DOC.001.P001.L002", "DOC.001.P001.L003"]
    assert s.source_ids == ["vie.P001.L001", "vie.P001.L002", "vie.P001.L003"]
    assert s.document_id == "DOC.001"


# --------------------------------------------------------------------------- #
# 4. Cross-page sentence reconstruction
# --------------------------------------------------------------------------- #
def test_cross_page_sentence_reconstruction():
    lines = [
        RawLine("DOC.001.P205.L001", 205, "Kim chính tựu tầm tây phương căn nguyên, phục nguyện đại hòa thượng thùy hoài,"),
        RawLine("DOC.001.P206.L002", 206, "doãn nạp hạ tâm, vô tích thần ngữ."),
    ]
    lines[0].source_ids = ["vie.P205.L006"]
    lines[1].source_ids = ["vie.P206.L001"]

    sents, dropped = make_sentences("TEST", "am", lines, SentenceConfig(layer="am"))
    assert len(sents) == 1
    s = sents[0]
    assert s.pages == [205, 206]
    assert "cross_page" in s.flags
    assert s.text == "Kim chính tựu tầm tây phương căn nguyên, phục nguyện đại hòa thượng thùy hoài, doãn nạp hạ tâm, vô tích thần ngữ."
    assert s.src_ids == ["DOC.001.P205.L001", "DOC.001.P206.L002"]
    assert s.source_ids == ["vie.P205.L006", "vie.P206.L001"]


def test_cross_page_dehyphenated_reconstruction():
    lines = [
        RawLine("DOC.001.P001.L001", 1, "tra conjunct", flags=["dehyphenated"]),
        RawLine("DOC.001.P002.L002", 2, "ion còn nữa."),
    ]
    sents, _ = make_sentences("TEST", "am", lines, SentenceConfig(layer="am"))
    assert len(sents) == 1
    assert sents[0].text == "tra conjunction còn nữa."
    assert sents[0].pages == [1, 2]
    assert "cross_page" in sents[0].flags


# --------------------------------------------------------------------------- #
# 5. Source ID and page traceability
# --------------------------------------------------------------------------- #
def test_source_id_and_page_traceability(tmp_path):
    layout = DataLayout(tmp_path, "TEST_WORK")
    lines = [
        RawLine("TEST.001.P001.S001", 1, "夫道處清虛，", raw_text="夫道處清虛，"),
        RawLine("TEST.001.P001.S002", 1, "四大理常。", raw_text="四大理常。"),
    ]
    lines[0].source_ids = ["chi_tra.P001.L001"]
    lines[1].source_ids = ["chi_tra.P001.L002"]

    sents, _ = make_sentences("TEST_WORK", "han", lines, SentenceConfig(layer="han"))
    assert len(sents) == 1
    s = sents[0]
    assert s.segment_ids == ["TEST.001.P001.S001", "TEST.001.P001.S002"]
    assert s.source_ids == ["chi_tra.P001.L001", "chi_tra.P001.L002"]
    assert s.pages == [1]

    # Save to sentences XML and read back
    out_xml = layout.segmentation_sentence("han")
    layout.ensure(layout.segmentation_sentences)
    segxml.write_sentences(out_xml, "TEST_WORK", "han", [
        {
            "id": s.id,
            "document_id": s.document_id,
            "pages": s.pages,
            "segment_ids": s.segment_ids,
            "source_ids": s.source_ids,
            "flags": s.flags,
            "raw_text": s.raw_text,
            "text": s.text,
        }
    ])
    assert out_xml.exists()
    xml_content = out_xml.read_text(encoding="utf-8")
    assert "SRC_IDS=" not in xml_content
    assert 'SEGMENT_IDS="TEST.001.P001.S001,TEST.001.P001.S002"' in xml_content
    assert 'SOURCE_IDS="chi_tra.P001.L001,chi_tra.P001.L002"' in xml_content

    read_back = segxml.read_sentences(out_xml)
    assert len(read_back) == 1
    r = read_back[0]
    assert r["id"] == s.id
    assert r["segment_ids"] == ["TEST.001.P001.S001", "TEST.001.P001.S002"]
    assert r["source_ids"] == ["chi_tra.P001.L001", "chi_tra.P001.L002"]
    assert r["pages"] == [1]
    assert r["document_id"] == "TEST.001"


# --------------------------------------------------------------------------- #
# 6. Stable sentence IDs
# --------------------------------------------------------------------------- #
def test_stable_sentence_ids():
    lines = [
        RawLine("DOC.001.P001.L001", 1, "Câu một. Câu hai."),
        RawLine("DOC.001.P001.L002", 1, "Câu ba. Câu bốn."),
    ]
    sents1, _ = make_sentences("WORK", "vi", lines, SentenceConfig(layer="am"))
    sents2, _ = make_sentences("WORK", "vi", lines, SentenceConfig(layer="am"))

    ids1 = [s.id for s in sents1]
    ids2 = [s.id for s in sents2]
    assert ids1 == ids2
    assert ids1 == ["WORK.vi.00001", "WORK.vi.00002", "WORK.vi.00003", "WORK.vi.00004"]


# --------------------------------------------------------------------------- #
# 7. Document boundary detection
# --------------------------------------------------------------------------- #
def test_document_boundary_detection():
    lines = [
        RawLine("WORK.001.P001.S001", 1, "Đoạn cuối tài liệu một không có chấm"),
        RawLine("WORK.002.P002.S001", 2, "Đoạn đầu tài liệu hai."),
    ]
    sents, _ = make_sentences("WORK", "am", lines, SentenceConfig(layer="am"))
    assert len(sents) == 2
    assert sents[0].document_id == "WORK.001"
    assert sents[1].document_id == "WORK.002"
    assert sents[0].text == "Đoạn cuối tài liệu một không có chấm"
    assert "no_terminal_punct" in sents[0].flags


# --------------------------------------------------------------------------- #
# 8. Layer assignment
# --------------------------------------------------------------------------- #
def test_layer_assignment():
    # Han layer
    han_lines = [RawLine("D.001.P001.S001", 1, "第一句。第二句。")]
    han_sents, _ = make_sentences("W", "han", han_lines, SentenceConfig(layer="han"))
    assert [s.text for s in han_sents] == ["第一句。", "第二句。"]

    # Tho layer defaults to line split
    tho_lines = [
        RawLine("D.001.P001.L001", 1, "Gió đưa cành trúc la đà"),
        RawLine("D.001.P001.L002", 1, "Tiếng chuông Trấn Vũ canh gà Thọ Xương"),
    ]
    tho_sents, _ = make_sentences("W", "tho", tho_lines, SentenceConfig(layer="tho", split="line"))
    assert [s.text for s in tho_sents] == [
        "Gió đưa cành trúc la đà",
        "Tiếng chuông Trấn Vũ canh gà Thọ Xương",
    ]


# --------------------------------------------------------------------------- #
# 9. Sentence-level cleaning
# --------------------------------------------------------------------------- #
def test_sentence_level_cleaning():
    s_han = Sentence(
        id="W.han.00001",
        text="  夫 道 處 清 虛 , 四 大 理 常 .  ",
        pages=[1],
        src_ids=["seg1"],
        flags=[],
        document_id="W.001",
    )
    s_vi = Sentence(
        id="W.am.00001",
        text="  Phù Đạo xứ thanh hư , tứ đại lý thường .  ",
        pages=[1],
        src_ids=["seg2"],
        flags=[],
        document_id="W.001",
    )

    cleaned_han, _ = clean_sentences([s_han], "han")
    assert len(cleaned_han) == 1
    # Punctuation normalized to CJK, CJK gap spaces stripped
    assert cleaned_han[0].text == "夫道處清虛，四大理常。"
    assert cleaned_han[0].id == "W.han.00001"
    assert cleaned_han[0].src_ids == ["seg1"]

    cleaned_vi, _ = clean_sentences([s_vi], "am")
    assert len(cleaned_vi) == 1
    assert cleaned_vi[0].text == "Phù Đạo xứ thanh hư, tứ đại lý thường."
    assert cleaned_vi[0].src_ids == ["seg2"]


# --------------------------------------------------------------------------- #
# 10. Alignment compatibility
# --------------------------------------------------------------------------- #
def test_alignment_compatibility(tmp_path):
    layout = DataLayout(tmp_path, "ALIGN_TEST")
    layout.ensure(layout.cleaning_root, layout.alignment_root)

    # Save cleaned sentences for han and am
    han_sents = [
        Sentence("ALIGN.han.00001", "夫道處清虛，四大理常。", [205], ["s1"], document_id="ALIGN.001"),
        Sentence("ALIGN.han.00002", "真空說而無實耶？", [205], ["s2"], document_id="ALIGN.001"),
        Sentence("ALIGN.han.00003", "無對應之漢文句。", [205], ["s3"], document_id="ALIGN.001"),
    ]
    am_sents = [
        Sentence("ALIGN.am.00001", "Phù Đạo xứ thanh hư, tứ đại lý thường.", [205], ["s4"], document_id="ALIGN.001"),
        Sentence("ALIGN.am.00002", "Chân không thuyết nhi vô thực da?", [205], ["s5"], document_id="ALIGN.001"),
    ]

    from src.common.sentences import save_sentences
    save_sentences(layout, "han", han_sents)
    save_sentences(layout, "am", am_sents)

    # Run alignment
    argv = [
        "--work-id", "ALIGN_TEST",
        "--data-root", str(tmp_path),
        "--pair", "han-am",
        "--method", "levenshtein",
        "--opt", "reading_dict=resources/han_viet.tsv",
    ]
    assert align.main(argv) == 0

    align_xml = layout.alignment_root / "han-am.xml"
    assert align_xml.exists()
    beads = read_alignments(align_xml)
    assert len(beads) >= 2
    # Check document IDs and sentence IDs are preserved
    assert all(b["document_id"] == "ALIGN.001" for b in beads)
    src_ids = [sid for b in beads for sid in b["src_ids"]]
    assert "ALIGN.han.00001" in src_ids
    assert "ALIGN.han.00002" in src_ids
    # Unaligned sentence is identifiable
    unaligned = [b for b in beads if "unaligned" in b["flags"]]
    assert len(unaligned) >= 1
    assert "ALIGN.han.00003" in unaligned[0]["src_ids"]


# --------------------------------------------------------------------------- #
# 11. End-to-end integration & regression comparison with CPO_18 pages 205-206
# --------------------------------------------------------------------------- #
def test_cpo_18_regression_pages_205_206(tmp_path):
    """Integration test using CPO_18 real OCR fixtures for pages 205-206."""
    from src import pipeline
    from src.segmentation import pipeline as seg_pipeline
    from src.cleaning import pipeline as clean_pipeline
    from src.segmentation import sentences as sent_pipeline

    layout = DataLayout(tmp_path, "CPO_18")

    # Populate OCR raw data matching CPO_18 pages 205-206
    han_205 = [
        "1. 與高明二法師雙佛不見形書 |",
        "李淼",
        "夫道至虚，四大理，而有法门，妙群域。 若其巧能利物，度脱无量，何吕不见真形耶？",
        "今就尋西方根源，伏願大和尚垂懷，允納下心，無惜神誥。",
        "弟子李森和南。",
        "三国六朝文.全宋文卷五十七)",
    ]
    add_ocr(layout, "chi_tra", 205, han_205, "cjk")

    vie_205_206 = [
        "l PHIÊN ÂM : |",
        "Phù Đạo xứ thanh hư, tứ đại lý thường, nhi hữu pháp môn, diệu xuất quần vực.",
        "Nhược xưng kỳ xảo năng lợi vật, độ thoát vô lượng vì giáo, hà dĩ bất kiến chân hình ư thế?",
        "Chân không thuyết nhi vô thực da?",
        "Kim chính tựu tầm tay phương căn nguyên, phục nguyện Đại hòa thượng thủy hoài, doãn nạp hạ tâm, vô tích thần cáo.",
        "Đệ tử Lý Miễ hòa nam.",
        "DỊCH NGHĨA : |",
        "Rằng, chỗ Đạo thanh hư, tứ đại lẽ thường, nhờ có giáo pháp mà sự nhiệm màu lan khắp mọi cõi.",
        "Nếu nói Đạo [Phật] là khéo giỏi, có thể lập giáo để làm lợi cho [muôn]vật, giải thoát cho vô số [người], thì tại sao không thấy chân hình Phật ở trên đời?",
        "Hay là chỉ nói suông mà không có thực?",
        "Nay đang lúc đi tìm nguồn cội Phương tây, cúi mong Đại hòa thượng rủ lòng, chấp nhận nỗi băn khoăn của kẻ dưới, không tiếc lời chỉ bảo thần tình.",
        "Đệ tử Lý Miễu kính lễ (11).",
        "(Hoằng minh tập, Q. 11; Toàn Thượng)",
        "cổ, Tam đại, Tần, Hán, Tam quốc, Lục triều văn, phần Toàn Tống văn, Q. 57)",
        "CHÚ THÍCH :",
        "(1) Chú thích một.",
    ]
    add_ocr(layout, "vie", 205, vie_205_206, "latin")

    # Config matching configs/CPO_18.json
    cfg = {
        "common": {"work_id": "CPO_18", "data_root": str(tmp_path), "pages": "205-206"},
        "ocr": {"chi_tra": {"engines": "tesseract"}, "vie": {"engines": "tesseract"}},
        "segmentation": {
            "document": {"numbered_heading": True},
            "layers": {
                "han": {
                    "source": "chi_tra",
                    "start_patterns": [r"^\s*\d{1,3}\s*[·.．、﹒)]"],
                    "end_patterns": [r"^PHIÊN\s+ÂM\b"],
                },
                "am": {
                    "source": "vie",
                    "heading_mode": True,
                    "start_patterns": [r"^PHIÊN\s+ÂM\b"],
                    "end_patterns": [r"^DỊCH\s+NGHĨA\b"],
                },
                "nghia": {
                    "source": "vie",
                    "heading_mode": True,
                    "start_patterns": [r"^DỊCH\s+NGHĨA\b"],
                    "end_patterns": [r"^CHÚ\s+THÍCH\b"],
                },
            },
        },
        "clean": {"am": {}, "nghia": {}},
        "metadata_extraction": {"enabled": True},
        "pairs": {
            "han-am": {"method": "length"},
            "han-nghia": {"method": "length"},
        },
    }
    cfg_path = tmp_path / "cpo18_test.json"
    cfg_path.write_text(json.dumps(cfg, ensure_ascii=False), encoding="utf-8")

    # 1. Step: segment
    seg_code = seg_pipeline.main([
        "--config", str(cfg_path),
        "--work-id", "CPO_18",
        "--data-root", str(tmp_path),
        "--force",
    ])
    assert seg_code == 0
    assert (layout.segmentation_dir / "documents.xml").exists()
    assert layout.segmentation_layer("han").exists()
    assert layout.segmentation_layer("am").exists()
    assert layout.segmentation_layer("nghia").exists()

    # Paragraph-level segmentation is preserved
    han_segments = segxml.read_layer(layout.segmentation_layer("han"))
    assert len(han_segments) >= 3

    # 2. Step: clean (structural cleaning)
    for l in ("han", "am", "nghia"):
        clean_code = clean_pipeline.main([
            "--work-id", "CPO_18",
            "--layer", l,
            "--data-root", str(tmp_path),
            "--structural-only",
            "--force",
        ])
        assert clean_code == 0
        assert layout.cleaning_structural(l).exists()

    # 3. Step: sentence (sentence segmentation)
    for l in ("han", "am", "nghia"):
        sent_code = sent_pipeline.main([
            "--work-id", "CPO_18",
            "--layer", l,
            "--data-root", str(tmp_path),
            "--force",
        ])
        assert sent_code == 0
        # Sentence records written to 04_segmentation/sentences/<layer>.xml
        assert layout.segmentation_sentence(l).exists()
        # Cleaned sentence records written to 05_cleaning/<layer>.xml
        assert (layout.cleaning_root / f"{l}.xml").exists()

    # Check sentence records
    han_sents = segxml.read_sentences(layout.segmentation_sentence("han"))
    am_sents = segxml.read_sentences(layout.segmentation_sentence("am"))
    nghia_sents = segxml.read_sentences(layout.segmentation_sentence("nghia"))

    assert len(han_sents) == 4
    assert len(am_sents) == 5
    assert len(nghia_sents) == 5

    # Check that signature in nghia is completely clean
    assert nghia_sents[-1]["text"] == "Đệ tử Lý Miễu kính lễ."

    # Check that OCR source IDs are present and not equal to segment IDs
    for s in han_sents:
        assert s["source_ids"]
        assert all(sid.startswith("chi_tra.") for sid in s["source_ids"])
        assert not any(sid.startswith("CPO_18.") for sid in s["source_ids"])

    for s in am_sents + nghia_sents:
        assert s["source_ids"]
        assert all(sid.startswith("vie.") for sid in s["source_ids"])
        assert not any(sid.startswith("CPO_18.") for sid in s["source_ids"])

    # Paragraph-level XML was NOT overwritten
    assert layout.segmentation_layer("han").exists()
    assert segxml.read_layer(layout.segmentation_layer("han")) == han_segments

    # 4. Step: validate
    report = validate_corpus(layout, ["han", "am", "nghia"], ["han-am", "han-nghia"])
    assert report.valid is True
    assert report.summary["errors"] == 0

    # 5. Step: align
    align_code = align.main([
        "--work-id", "CPO_18",
        "--data-root", str(tmp_path),
        "--pair", "han-am",
        "--method", "length",
        "--force",
    ])
    assert align_code == 0
    beads = read_alignments(layout.alignment_root / "han-am.xml")
    assert len(beads) >= 4
    assert all(b["document_id"] == "CPO_18.001" for b in beads)


# --------------------------------------------------------------------------- #
# Group A: Provenance Tests
# --------------------------------------------------------------------------- #
def test_group_a_provenance_preservation(tmp_path):
    layout = DataLayout(tmp_path, "PROV_WORK")
    # 1. Raw OCR source IDs survive read_segmentation_lines
    layer_xml = layout.segmentation_layer("han")
    layout.ensure(layout.segmentation_layers)
    segxml.write_layer(layer_xml, "PROV_WORK", "han", [
        {
            "id": "PROV_WORK.001.P001.S001",
            "document_id": "PROV_WORK.001",
            "page": 1,
            "text": "夫道至虚，四大理常。",
            "source_ids": ["chi_tra.P001.L001", "chi_tra.P001.L002"],
        }
    ])
    raw_lines = clean_pipeline.read_segmentation_lines(layer_xml)
    assert len(raw_lines) == 1
    assert raw_lines[0].source_ids == ["chi_tra.P001.L001", "chi_tra.P001.L002"]
    assert raw_lines[0].document_id == "PROV_WORK.001"

    # 2. Source IDs survive structural cleaning
    cfg = CleanConfig(layer="han")
    struct_out = clean_pipeline.run_structural_cleaning("PROV_WORK", "han", raw_lines, cfg)
    assert len(struct_out.kept) == 1
    assert struct_out.kept[0].source_ids == ["chi_tra.P001.L001", "chi_tra.P001.L002"]

    # 3. Source IDs written to structural XML and read by sentence segmentation
    struct_xml = layout.cleaning_structural("han")
    layout.ensure(layout.cleaning_structural_dir)
    segxml.write_structural_layer(struct_xml, "PROV_WORK", "han", [
        {
            "id": struct_out.kept[0].id,
            "document_id": struct_out.kept[0].document_id,
            "page": struct_out.kept[0].page,
            "text": struct_out.kept[0].text,
            "raw_text": struct_out.kept[0].raw_text,
            "flags": struct_out.kept[0].flags,
            "source_ids": struct_out.kept[0].source_ids,
        }
    ])
    seg_lines = sent_pipeline.read_segment_input(layout, "han")
    assert seg_lines[0].source_ids == ["chi_tra.P001.L001", "chi_tra.P001.L002"]

    # 4. make_sentences attaches correct OCR source_ids and segment_ids
    sents, _ = make_sentences("PROV_WORK", "han", seg_lines, SentenceConfig(layer="han"))
    assert len(sents) == 1
    assert sents[0].source_ids == ["chi_tra.P001.L001", "chi_tra.P001.L002"]
    assert sents[0].segment_ids == ["PROV_WORK.001.P001.S001"]

    # 5. Missing provenance is NOT fabricated with segment ID
    seg_no_src = [RawLine("PROV_WORK.001.P001.S002", 1, "Câu không có nguồn.", raw_text="Câu không có nguồn.")]
    sents_no_src, _ = make_sentences("PROV_WORK", "am", seg_no_src, SentenceConfig(layer="am"))
    assert sents_no_src[0].source_ids == []
    assert sents_no_src[0].segment_ids == ["PROV_WORK.001.P001.S002"]
    assert "missing_provenance" in sents_no_src[0].flags


# --------------------------------------------------------------------------- #
# Group B: Canonical XML Schema & Backward Compatibility Tests
# --------------------------------------------------------------------------- #
def test_group_b_xml_schema(tmp_path):
    layout = DataLayout(tmp_path, "SCHEMA_WORK")
    s = Sentence(
        id="SCHEMA_WORK.han.00001",
        text="夫道至虚。",
        pages=[1],
        segment_ids=["SCHEMA_WORK.001.P001.S001"],
        source_ids=["chi_tra.P001.L001"],
        flags=[],
        document_id="SCHEMA_WORK.001",
    )
    # Save sentence XML
    out_xml = layout.cleaning_root / "han.xml"
    layout.ensure(layout.cleaning_root)
    save_sentences(layout, "han", [s], out_xml)

    # Verify no SRC_IDS attribute in Sentence XML
    content = out_xml.read_text(encoding="utf-8")
    assert "SRC_IDS=" not in content
    assert 'SEGMENT_IDS="SCHEMA_WORK.001.P001.S001"' in content
    assert 'SOURCE_IDS="chi_tra.P001.L001"' in content

    # Read back canonical Sentence XML
    loaded = load_sentences(layout, "han", out_xml)
    assert len(loaded) == 1
    assert loaded[0].segment_ids == ["SCHEMA_WORK.001.P001.S001"]
    assert loaded[0].source_ids == ["chi_tra.P001.L001"]
    # Backward compatibility: s.src_ids returns segment_ids
    assert loaded[0].src_ids == ["SCHEMA_WORK.001.P001.S001"]

    # Verify backward compatibility reading legacy XML with only SRC_IDS
    legacy_xml = tmp_path / "legacy_sentence.xml"
    legacy_xml.write_text(
        """<?xml version='1.0' encoding='utf-8'?>
<root>
    <FILE ID="LEGACY">
        <LAYER NAME="han">
            <SENTENCE ID="LEGACY.han.00001" DOCUMENT_ID="LEGACY.001" PAGES="1" SRC_IDS="LEGACY.001.P001.S001">夫道至虚。</SENTENCE>
        </LAYER>
    </FILE>
</root>""",
        encoding="utf-8",
    )
    loaded_legacy = load_sentences(layout, "han", legacy_xml)
    assert len(loaded_legacy) == 1
    assert loaded_legacy[0].segment_ids == ["LEGACY.001.P001.S001"]
    assert loaded_legacy[0].src_ids == ["LEGACY.001.P001.S001"]
    assert loaded_legacy[0].source_ids == []


# --------------------------------------------------------------------------- #
# Group C: Footnote & Bibliographic Leakage Tests
# --------------------------------------------------------------------------- #
def test_group_c_footnote_detection():
    from src.cleaning.footnotes import detect_footnotes
    from src.cleaning.model import FootnoteConfig

    cfg = FootnoteConfig()

    # 1. Vietnamese multi-line citation with unmatched closing parenthesis on line 2
    vi_lines = [
        RawLine("L01", 206, "Đệ tử Lý Miễu kính lễ (11)."),
        RawLine("L02", 206, "(Hoằng minh tập, Q. 11; Toàn Thượng)"),
        RawLine("L03", 206, "cổ, Tam đại, Tần, Hán, Tam quốc, Lục triều văn, phần Toàn Tống văn, Q. 57)"),
    ]
    res_vi = detect_footnotes(vi_lines, cfg, "nghia")
    assert "L02" in res_vi.excluded_ids
    assert "L03" in res_vi.excluded_ids
    assert "L01" not in res_vi.excluded_ids  # Signature not excluded!
    assert len(res_vi.notes) == 1
    assert res_vi.notes[0].line_ids == ["L02", "L03"]

    # 2. Chinese bibliographic citation ending with unbalanced parenthesis
    han_lines = [
        RawLine("H01", 205, "弟子李森和南。"),
        RawLine("H02", 205, "三国六朝文.全宋文卷五十七)"),
    ]
    res_han = detect_footnotes(han_lines, cfg, "han")
    assert "H02" in res_han.excluded_ids
    assert "H01" not in res_han.excluded_ids  # Signature not excluded!

    # 3. Legitimate Chinese prose containing character '文' or '六' not excluded
    han_prose = [
        RawLine("H03", 205, "夫道處清虛，四大理常，而有法門，妙出群域。"),
        RawLine("H04", 205, "今就尋西方根源，伏願大和尚垂懷，允納下心，無惜神誥。"),
    ]
    res_prose = detect_footnotes(han_prose, cfg, "han")
    assert len(res_prose.excluded_ids) == 0

    # 4. Legitimate Vietnamese parenthetical prose not excluded
    vi_prose = [
        RawLine("V01", 206, "Rằng, chỗ Đạo thanh hư (như lời xưa), tứ đại lẽ thường."),
    ]
    res_vi_prose = detect_footnotes(vi_prose, cfg, "nghia")
    assert len(res_vi_prose.excluded_ids) == 0


# --------------------------------------------------------------------------- #
# Group D: Validation Rule Tests
# --------------------------------------------------------------------------- #
def test_group_d_validation_rules(tmp_path):
    layout = DataLayout(tmp_path, "VAL_TEST")
    layout.ensure(layout.cleaning_root)
    layout.ensure(layout.segmentation_sentences)
    layout.ensure(layout.segmentation_layers)

    # 1. Segment XML
    segxml.write_layer(layout.segmentation_layer("han"), "VAL_TEST", "han", [
        {"id": "VAL_TEST.001.P001.S001", "document_id": "VAL_TEST.001", "page": 1,
         "text": "第一句。", "source_ids": ["chi_tra.P001.L001"]},
        {"id": "VAL_TEST.001.P001.S002", "document_id": "VAL_TEST.001", "page": 1,
         "text": "第二句。", "source_ids": ["chi_tra.P001.L002"]},
    ])

    # 2. Sentences XML with one sentence missing source_ids and another using segment ID as source_id
    segxml.write_sentences(layout.segmentation_sentence("han"), "VAL_TEST", "han", [
        {"id": "VAL_TEST.han.00001", "document_id": "VAL_TEST.001", "pages": [1],
         "segment_ids": ["VAL_TEST.001.P001.S001"], "source_ids": ["chi_tra.P001.L001"],
         "text": "第一句。"},
        {"id": "VAL_TEST.han.00002", "document_id": "VAL_TEST.001", "pages": [1],
         "segment_ids": ["VAL_TEST.001.P001.S002"], "source_ids": ["VAL_TEST.001.P001.S002"],  # ERROR: segment ID as source_id!
         "text": "第二句。"},
        {"id": "VAL_TEST.han.00003", "document_id": "VAL_TEST.001", "pages": [1],
         "segment_ids": ["VAL_TEST.001.P001.S002"], "source_ids": [],  # WARNING: missing provenance!
         "text": "第三句。"},
    ])

    # 3. Cleaned XML with duplicate sentence text
    sents = [
        Sentence(id="VAL_TEST.han.00001", text="夫道至虚，四大理常。", pages=[1],
                 segment_ids=["VAL_TEST.001.P001.S001"], source_ids=["chi_tra.P001.L001"],
                 document_id="VAL_TEST.001"),
        Sentence(id="VAL_TEST.han.00002", text="夫道至虚，四大理常。", pages=[1],  # Duplicate!
                 segment_ids=["VAL_TEST.001.P001.S002"], source_ids=["chi_tra.P001.L002"],
                 document_id="VAL_TEST.001"),
    ]
    save_sentences(layout, "han", sents)

    report = validate_corpus(layout, ["han"], [])
    # Should report error for segment ID as source_id
    errors = [i for i in report.issues if i.severity == "error"]
    assert any("sử dụng nhầm segment ID làm source_id" in e.message for e in errors)

    # Should report warning for missing source_ids
    warnings = [i for i in report.issues if i.severity == "warning"]
    assert any("thiếu source_ids" in w.message for w in warnings)

    # Should report warning for duplicate sentence
    assert any("trùng lặp văn bản" in w.message for w in warnings)
