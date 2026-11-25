"""Test cho 3 lỗi pipeline: tách câu Hán auto, LengthAligner một phía rỗng, OCR bị chặn vì cache."""
import json
import logging
from pathlib import Path

import pytest
from PIL import Image

from src.alignment import aligners, export
from src.alignment.aligners import LengthAligner, lookup_readings
from src.cleaning.model import RawLine
from src.common.corpus import DataLayout
from src.common.sentences import Sentence
from src.ocr import ocr, raw_store
from src.ocr.engines import OcrEngine, OcrLine, OcrResult, register_engine
from src.segmentation.sentences import (SentenceConfig, choose_han_split, default_split, make_sentences,
                                        sentence_count_mismatches)

POEM = ["二諒江山境界連", "旁通荆廣接高宣", "溪流奔駛爭歸海", "嶺道垂梯直上天"]
PROSE = ["夫道處清虛，四大理常，而有法門，妙出群", "域。若稱其巧能利物，度脫無量爲敎，何呂不見",
         "眞形於世？眞空說而無實耶？"]


def lines(doc: str, texts: list[str], page: int = 1) -> list[RawLine]:
    return [RawLine(f"{doc}.P{page:04d}.S{i:03d}", page, t, source_ids=[f"P{page:04d}.L{i:03d}"],
                    document_id=doc) for i, t in enumerate(texts, 1)]


# --------------------------------------------------------------------------- 1. tách câu Hán
def test_default_split_per_layer():
    assert (default_split("han"), default_split("am"), default_split("nghia"), default_split("tho")) == \
        ("auto", "auto", "punct", "line")


def test_choose_han_split_threshold():
    assert choose_han_split(lines("W.001", POEM)) == "line"                    # 0 dấu / 4 dòng
    assert choose_han_split(lines("W.001", PROSE)) == "punct"                  # 3 dấu / 3 dòng
    # đúng ngưỡng: 1 dấu mỗi 4 dòng -> punct; 1 dấu / 5 dòng -> line
    assert choose_han_split(lines("W.001", POEM[:3] + ["溪流奔駛爭歸海。"])) == "punct"
    assert choose_han_split(lines("W.001", POEM + ["溪流奔駛爭歸海；"])) == "line"


def test_auto_split_decides_per_document():
    raw = lines("W.001", POEM) + lines("W.002", PROSE, page=2)
    decisions: dict[str, str] = {}
    sents, _ = make_sentences("W", "han", raw, SentenceConfig(layer="han", split="auto"), decisions)
    assert decisions == {"W.001": "line", "W.002": "punct"}
    poem = [s for s in sents if s.document_id == "W.001"]
    prose = [s for s in sents if s.document_id == "W.002"]
    assert [s.text for s in poem] == POEM                                       # mỗi câu thơ một câu
    assert [s.source_ids for s in poem] == [[f"P0001.L{i:03d}"] for i in range(1, 5)]
    assert [s.text for s in prose] == ["夫道處清虛，四大理常，而有法門，妙出群域。",
                                       "若稱其巧能利物，度脫無量爲敎，何呂不見眞形於世？", "眞空說而無實耶？"]


def test_punct_and_line_modes_unchanged():
    raw = lines("W.001", POEM)
    punct, _ = make_sentences("W", "han", raw, SentenceConfig(layer="han", split="punct"))
    assert [s.text for s in punct] == ["".join(POEM)]                            # hành vi cũ: gộp cả bài
    line, _ = make_sentences("W", "han", raw, SentenceConfig(layer="han", split="line"))
    assert [s.text for s in line] == POEM


def test_auto_is_punct_for_nghia_layer():
    raw = lines("W.001", ["Nhị Lạng giang sơn cảnh giới liên,", "Bàng thông Kinh, Quảng tiếp Cao, Tuyên."])
    sents, _ = make_sentences("W", "nghia", raw, SentenceConfig(layer="nghia", split="auto"))
    assert [s.text for s in sents] == ["Nhị Lạng giang sơn cảnh giới liên, Bàng thông Kinh, Quảng tiếp Cao, Tuyên."]


def test_auto_am_splits_verse_by_line_and_prose_by_punctuation():
    verse = ["Giang thứ liên bài số tứ chu,", "Quan thịnh cô ỷ thảo đê đầu.",
             "Tùy nha tảo bích, yêu xuân tín,", "Hãn tốt bằng lan thủ dạ trù."]
    prose = ["Phù Đạo xứ thanh hư, tứ đại lý thường, nhi hữu pháp môn, diệu xuất quần vực. Nhược xưng",
             "kỳ xảo năng lợi vật, độ thoát vô lượng vi giáo, hà dĩ bất kiến chân hình ư thế?"]
    raw = lines("W.001", verse) + lines("W.002", prose, page=2)
    decisions: dict[str, str] = {}
    sents, _ = make_sentences("W", "am", raw, SentenceConfig(layer="am", split="auto"), decisions)
    assert decisions == {"W.001": "line", "W.002": "punct"}
    assert [s.text for s in sents if s.document_id == "W.001"] == verse
    assert len([s for s in sents if s.document_id == "W.002"]) == 2


def test_sentence_count_mismatch_over_30_percent():
    han = {"W.001": 8, "W.002": 1, "W.003": 10, "W.004": 3}
    am = {"W.001": 8, "W.002": 8, "W.003": 7, "W.005": 2}
    assert sentence_count_mismatches(han, am) == [("W.002", 1, 8)]              # W.003: 30% đúng ngưỡng, không báo


def test_sentence_step_warns_on_han_am_mismatch(tmp_path, caplog):
    from src.segmentation import sentences as seg_sentences
    from src.segmentation import serialization
    layout = DataLayout(tmp_path, "W")
    layout.ensure(layout.segmentation_sentences)
    serialization.write_sentences(layout.segmentation_sentence("am"), "W", "am", [
        {"id": f"W.am.{i:05d}", "document_id": "W.001", "pages": [1], "segment_ids": [], "source_ids": [],
         "flags": [], "raw_text": "x", "text": "x"} for i in range(1, 9)])
    han = [Sentence(id="W.han.00001", text="".join(POEM), pages=[1], document_id="W.001")]
    assert seg_sentences.han_am_mismatches(layout, "han", han) == [("W.001", 1, 8)]
    assert seg_sentences.han_am_mismatches(layout, "nghia", han) == []


# --------------------------------------------------------------------------- 1b. OpenCC chỉ khi tra từ điển
def test_lookup_readings_uses_traditional_only_when_missing(monkeypatch):
    readings = {"數": ("số",), "于": ("vu",), "於": ("ư",)}
    monkeypatch.setattr(aligners, "_S2T", type("Fake", (), {"convert": lambda self, c: {"数": "數", "于": "於"}.get(c, c)})())
    assert lookup_readings(readings, "数") == ("số",)                           # giản thể -> tra bằng phồn thể
    assert lookup_readings(readings, "于") == ("vu",)                           # đã có trong từ điển: không đổi
    assert lookup_readings(readings, "龘") is None


def test_lookup_readings_without_opencc(monkeypatch):
    monkeypatch.setattr(aligners, "_S2T", False)
    assert lookup_readings({"數": ("số",)}, "数") is None
    assert aligners.to_traditional("数") == "数"


def test_levenshtein_does_not_change_exported_text(tmp_path, monkeypatch):
    monkeypatch.setattr(aligners, "_S2T", type("Fake", (), {"convert": lambda self, c: {"数": "數"}.get(c, c)})())
    dict_path = tmp_path / "d.tsv"
    dict_path.write_text("數\tsố\n路\tlộ\n", encoding="utf-8")
    src = [Sentence(id="h1", text="数路", pages=[1])]
    tgt = [Sentence(id="a1", text="Số lộ", pages=[1])]
    beads = aligners.ALIGNERS["levenshtein"](reading_dict=str(dict_path)).align("han", "am", src, tgt)
    assert [(b.src, b.tgt) for b in beads] == [((0,), (0,))] and beads[0].score == 1.0
    assert src[0].text == "数路"                                                 # văn bản không bị đổi


# --------------------------------------------------------------------------- 2. một phía rỗng
@pytest.mark.parametrize("n_src,n_tgt", [(3, 0), (0, 2)])
def test_length_aligner_one_side_empty_returns_unaligned(n_src, n_tgt):
    src = [Sentence(id=f"h{i}", text="二諒江山", pages=[1]) for i in range(n_src)]
    tgt = [Sentence(id=f"n{i}", text="Hai xứ Lạng", pages=[1]) for i in range(n_tgt)]
    beads = LengthAligner().align("han", "nghia", src, tgt)
    assert len(beads) == n_src + n_tgt
    assert all(b.flags == ("unaligned",) and b.score == 0.0 for b in beads)
    assert [i for b in beads for i in b.src] == list(range(n_src))
    assert [j for b in beads for j in b.tgt] == list(range(n_tgt))


def test_length_aligner_tgt_without_content_does_not_raise():
    src = [Sentence(id="h1", text="二諒江山", pages=[1])]
    tgt = [Sentence(id="n1", text="123", pages=[1])]                            # không có âm tiết nào
    assert [b.kind for b in LengthAligner().align("han", "nghia", src, tgt)] == ["1-0", "0-1"]


def test_align_and_export_warn_on_empty_translation_column(tmp_path, caplog):
    from src.alignment import align
    from src.common.sentences import save_sentences
    layout = DataLayout(tmp_path, "W")
    save_sentences(layout, "han", [
        Sentence(id="W.han.00001", text="二諒江山境界連", pages=[1], document_id="W.001"),
        Sentence(id="W.han.00002", text="旁通荆廣接高宣", pages=[2], document_id="W.002")])
    save_sentences(layout, "nghia", [
        Sentence(id="W.nghia.00001", text="Hai xứ Lạng núi sông liền cõi.", pages=[1], document_id="W.001")])
    caplog.set_level(logging.WARNING)
    assert align.main(["--work-id", "W", "--data-root", str(tmp_path), "--pair", "han-nghia",
                       "--method", "length"]) == 0
    assert "W.002" in caplog.text and "Dịch nghĩa" in caplog.text

    caplog.clear()
    counts = export.warn_empty_columns([("二", "nhị", "hai", None), ("三", None, None, None)])
    assert counts == {"am": 1, "nghia": 1, "tho": 2}
    assert "Cột 'Dịch thơ' TRỐNG HOÀN TOÀN" in caplog.text
    assert "Cột 'Dịch nghĩa' trống ở 1/2 dòng Hán" in caplog.text


# --------------------------------------------------------------------------- 3. OCR không bị chặn vì cache
@register_engine
class FakeUnifiedEngine(OcrEngine):
    name = "fake_unified"
    max_workers = 1
    calls: list[int] = []

    @classmethod
    def describe(cls, lang):
        return {"name": cls.name, "adapter_version": 1}

    def recognize(self, image, *, lang, psm, dpi):
        FakeUnifiedEngine.calls.append(1)
        return OcrResult(self.name, [OcrLine("初聞其語", (0, 0, 10, 8), 90.0)], {})


def test_unified_ocr_not_blocked_by_raw_cache(tmp_path):
    pages = tmp_path / "W" / "02_extraction" / "pages"
    pages.mkdir(parents=True)
    for p in (1, 2):
        Image.new("RGB", (40, 40), "white").save(pages / f"page_{p:03d}.png")
    base = ["--work-id", "W", "--data-root", str(tmp_path), "--unified", "--engines", "fake_unified",
            "--pages", "1-2", "--source", str(pages)]
    FakeUnifiedEngine.calls.clear()
    assert ocr.main(base) == 0
    layout = DataLayout(tmp_path, "W")
    raw_before = {p: p.read_bytes() for p in layout.ocr_raw_unified_dir.glob("*.json")}
    assert len(raw_before) == 2 and FakeUnifiedEngine.calls == [1, 1]

    # chỉ còn cache raw (manifest/XML đã xoá): chạy lại KHÔNG cần --force, không gọi engine
    raw_store.unified_manifest_path(layout).unlink()
    layout.ocr_metadata_path.unlink()
    layout.ocr_unified_xml_path.unlink()
    assert ocr.main(base) == 0
    assert FakeUnifiedEngine.calls == [1, 1]
    assert {p: p.read_bytes() for p in layout.ocr_raw_unified_dir.glob("*.json")} == raw_before

    # manifest/XML đã có, đầu vào không đổi: bỏ qua êm (mã 0, không ghi lại); --force ghi lại được
    xml_before = layout.ocr_unified_xml_path.read_bytes()
    assert ocr.main(base) == 0
    assert layout.ocr_unified_xml_path.read_bytes() == xml_before
    assert ocr.main([*base, "--force"]) == 0
