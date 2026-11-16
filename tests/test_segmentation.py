import csv
import json

from src.alignment import align
from src.alignment.serialization import read_alignments
from src.cleaning import pipeline as cleaning
from src.common.corpus import DataLayout
from src.common.sentences import alignment_dir, load_sentences
from src.ocr import raw_store
from src.segmentation import pipeline as segmentation
from src.segmentation import serialization as segxml


def read_docs(layout):
    return segxml.read_documents(layout.segmentation_dir / "documents.xml")


def read_layer(layout, name):
    return segxml.read_layer(layout.segmentation_layer(name))


def read_headings(layout):
    return segxml.read_headings(layout.segmentation_dir / "section_headings.xml")


def read_document_meta(layout):
    return segxml.read_document_metadata(layout.segmentation_dir / "document_metadata.xml")


def read_tsv(path):
    with path.open(encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f, delimiter="\t"))


def add_ocr(layout: DataLayout, writing_system: str, page: int, texts: list[str], keep: str) -> None:
    key = f"{writing_system}-{page}"
    store = {"schema_version": 1, "work_id": layout.work_id,
             "writing_system": writing_system, "page": page, "runs": [{
                 "key": key, "parameters": {}, "created_at": "2026-01-01T00:00:00+00:00",
                 "image": f"page_{page:04d}.png", "image_size": [100, 100],
                 "crop_box": [0, 0, 100, 100], "raw": {},
                 "lines": [{"text": t, "bbox": [0, i * 10, 10, i * 10 + 8],
                            "confidence": 90.0} for i, t in enumerate(texts)]}]}
    raw_store.save_page(layout, writing_system, page, store)
    manifest = raw_store.load_manifest(layout, writing_system) or {
        "schema_version": 1, "work_id": layout.work_id, "writing_system": writing_system,
        "lang": writing_system, "keep": keep, "engines": {"fake": {"name": "fake"}},
        "parameters": {}, "pages": [], "low_conf_pages": [], "empty_pages": [],
        "review_required": True}
    manifest["pages"].append({
        "page": page, "image": f"page_{page:04d}.png", "image_sha256": "x",
        "raw": f"03_ocr/raw/{writing_system}/page_{page:04d}.json",
        "selected_engine": "fake", "selected_key": key, "confidence": 90.0,
        "empty": not texts, "n_lines": len(texts),
        "lines": [{"text": t, "bbox": [0, i * 10, 10, i * 10 + 8],
                   "confidence": 90.0} for i, t in enumerate(texts)],
        "engines": [{"engine": "fake", "key": key, "cache_hit": False}]})
    raw_store.write_manifest(layout, writing_system, manifest)


def make_docs(tmp_path):
    layout = DataLayout(tmp_path, "T_01")
    add_ocr(layout, "chi_tra", 1, ["第一回", "正文甲。"], "cjk")
    add_ocr(layout, "chi_tra", 2, ["第二回", "正文乙。"], "cjk")
    add_ocr(layout, "vie", 1, ["PHIÊN ÂM", "phien am mot", "DỊCH NGHĨA",
                               "nghia mot", "CHÚ THÍCH", "ghi chu"], "latin")
    add_ocr(layout, "vie", 2, ["PHIÊN ÂM", "phien am hai"], "latin")
    return layout


def seg_config(tmp_path, layers_extra=None, metadata=None, document=None):
    layers = {
        "han": {"source": "chi_tra"},
        "am": {"source": "vie", "start_patterns": ["^PHIÊN ÂM"], "end_patterns": ["^DỊCH NGHĨA"]},
        "nghia": {"source": "vie", "start_patterns": ["^DỊCH NGHĨA"], "end_patterns": ["^CHÚ THÍCH"]},
    }
    for name, extra in (layers_extra or {}).items():
        layers.setdefault(name, {}).update(extra)
    cfg = {"common": {"work_id": "T_01", "data_root": str(tmp_path), "pages": "1-2"},
           "segmentation": {"document": document or {"start_patterns": ["^第[一二]回"]},
                            "layers": layers},
           "metadata_extraction": {"enabled": metadata is not None}, "pairs": {"han-am": {}}}
    if metadata is not None:
        cfg["metadata_extraction"]["layers"] = metadata
    path = tmp_path / "seg.json"
    path.write_text(json.dumps(cfg, ensure_ascii=False), encoding="utf-8")
    return path, cfg


def test_segments_documents_and_layers(tmp_path):
    layout = make_docs(tmp_path)
    path, _ = seg_config(tmp_path)
    assert segmentation.main(["--config", str(path), "--work-id", "T_01",
                              "--data-root", str(tmp_path)]) == 0
    docs = read_docs(layout)
    assert [d["document_id"] for d in docs] == ["T_01.001", "T_01.002"]
    assert docs[0]["pages"] == [1] and docs[1]["pages"] == [2]

    han = read_layer(layout, "han")
    assert [r["id"] for r in han] == ["T_01.001.P001.S001", "T_01.001.P001.S002",
                                      "T_01.002.P002.S001", "T_01.002.P002.S002"]
    assert {r["writing_system"] for r in han} == {"chi_tra"}
    assert all(r["bbox"] and r["confidence"] == 90.0 for r in han)
    am = read_layer(layout, "am")
    assert [r["text"] for r in am] == ["phien am mot", "phien am hai"]
    nghia = read_layer(layout, "nghia")
    assert [r["text"] for r in nghia] == ["nghia mot"]
    assert all(r["document_id"] == "T_01.001" for r in nghia)

    headings = read_headings(layout)
    assert [(h["layer"], h["kind"]) for h in headings if h["kind"] != "document_start"] == [
        ("am", "start"), ("am", "end"), ("nghia", "start"), ("nghia", "end"),
        ("am", "start")]
    review = (layout.segmentation_dir / "review_required.tsv").read_text(encoding="utf-8")
    assert "unassigned_line" in review and "ghi chu" in review
    assert "missing_heading" in review


def test_tho_layer_is_optional_and_ignores_poetry_mentions(tmp_path):
    layout = DataLayout(tmp_path, "T_01")
    add_ocr(layout, "chi_tra", 1, ["第一回", "正文甲。"], "cjk")
    add_ocr(layout, "vie", 1, ["PHIÊN ÂM", "bài thơ này chỉ được nhắc trong lời kể",
                               "DỊCH NGHĨA", "nghia mot", "DỊCH THƠ", "tho mot",
                               "CHÚ THÍCH", "ghi chu"], "latin")
    path, _ = seg_config(
        tmp_path,
        layers_extra={"tho": {"source": "vie", "optional": True,
                              "start_patterns": ["^DỊCH THƠ"], "end_patterns": ["^CHÚ THÍCH"]}})
    assert segmentation.main(["--config", str(path), "--work-id", "T_01",
                              "--data-root", str(tmp_path)]) == 0
    assert [r["text"] for r in read_layer(layout, "tho")] == ["tho mot"]
    review = (layout.segmentation_dir / "review_required.tsv").read_text(encoding="utf-8")
    assert not any(line.startswith("missing_heading") and "\ttho\t" in line
                   for line in review.splitlines())


def test_missing_poetry_only_writes_empty_optional_layer(tmp_path):
    layout = make_docs(tmp_path)
    path, _ = seg_config(
        tmp_path,
        layers_extra={"tho": {"source": "vie", "optional": True,
                              "start_patterns": ["^DỊCH THƠ"], "end_patterns": ["^CHÚ THÍCH"]}})
    assert segmentation.main(["--config", str(path), "--work-id", "T_01",
                              "--data-root", str(tmp_path)]) == 0
    assert read_layer(layout, "tho") == []
    review = (layout.segmentation_dir / "review_required.tsv").read_text(encoding="utf-8")
    assert not any(line.startswith("missing_heading") and "\ttho\t" in line
                   for line in review.splitlines())


def test_metadata_extracted_from_segmented_layer(tmp_path):
    layout = DataLayout(tmp_path, "T_01")
    add_ocr(layout, "chi_tra", 1, ["一、標題", "作者名", "正文。"], "cjk")
    layers = {"han": {"source": "chi_tra", "title_patterns": ["^一、"],
                      "author_patterns": ["^作者"], "author_max_len": 8}}
    cfg = {"common": {"work_id": "T_01", "data_root": str(tmp_path), "pages": "1"},
           "segmentation": {"layers": layers},
           "metadata_extraction": {"enabled": True}}
    path = tmp_path / "meta.json"
    path.write_text(json.dumps(cfg, ensure_ascii=False), encoding="utf-8")
    assert segmentation.main(["--config", str(path), "--work-id", "T_01",
                              "--data-root", str(tmp_path)]) == 0
    han = read_layer(layout, "han")
    assert [r["text"] for r in han] == ["正文。"]
    meta = read_document_meta(layout)
    assert meta[0]["metadata"]["title"]["han"][0]["text"] == "一、標題"
    assert meta[0]["metadata"]["author"]["han"][0]["text"] == "作者名"


def test_cleaning_consumes_segmentation_and_alignment_is_per_document(tmp_path):
    layout = make_docs(tmp_path)
    path, cfg = seg_config(tmp_path)
    assert segmentation.main(["--config", str(path), "--work-id", "T_01",
                              "--data-root", str(tmp_path)]) == 0
    for layer in ("han", "am", "nghia"):
        assert cleaning.main(["--work-id", "T_01", "--data-root", str(tmp_path),
                              "--layer", layer, "--no-metadata", "--force"]) == 0
        assert (layout.cleaning_root / f"{layer}.xml").exists()
    assert {s.document_id for s in load_sentences(layout, "han")} == {"T_01.001", "T_01.002"}
    assert {s.document_id for s in load_sentences(layout, "am")} == {"T_01.001", "T_01.002"}

    assert align.main(["--work-id", "T_01", "--data-root", str(tmp_path),
                       "--pair", "han-am", "--method", "length"]) == 0
    beads = read_alignments(alignment_dir(layout) / "han-am.xml")
    assert {b["document_id"] for b in beads} == {"T_01.001", "T_01.002"}
    assert sum(len(b["src_ids"]) for b in beads) == 2
    assert sum(len(b["tgt_ids"]) for b in beads) == 2

def run_numbered(tmp_path, layers, document, pages="1-2"):
    layout = DataLayout(tmp_path, "T_01")
    for ws in raw_store.WRITING_SYSTEMS:
        manifest = raw_store.load_manifest(layout, ws)
        if manifest:
            manifest["keep"] = "latin"
            raw_store.write_manifest(layout, ws, manifest)
    cfg = {"common": {"work_id": "T_01", "data_root": str(tmp_path), "pages": pages},
           "segmentation": {"document": document, "layers": layers},
           "metadata_extraction": {"enabled": False}, "pairs": {"han-am": {}}}
    path = tmp_path / "numbered.json"
    path.write_text(json.dumps(cfg, ensure_ascii=False), encoding="utf-8")
    assert segmentation.main(["--config", str(path), "--work-id", "T_01",
                              "--data-root", str(tmp_path)]) == 0
    return DataLayout(tmp_path, "T_01")


def test_numbered_heading_same_page_two_documents(tmp_path):
    layout = DataLayout(tmp_path, "T_01")
    add_ocr(layout, "chi_tra", 1,
            ["1. Bài một", "nội dung một", "2. Bài hai", "nội dung hai"], "cjk")
    layout = run_numbered(tmp_path, {"han": {"source": "chi_tra"}}, {"numbered_heading": True})
    docs = read_docs(layout)
    assert [d["document_id"] for d in docs] == ["T_01.001", "T_01.002"]
    assert [d["heading"]["text"] for d in docs] == ["1. Bài một", "2. Bài hai"]
    han = read_layer(layout, "han")
    assert [(r["document_id"], r["text"]) for r in han] == [
        ("T_01.001", "1. Bài một"), ("T_01.001", "nội dung một"),
        ("T_01.002", "2. Bài hai"), ("T_01.002", "nội dung hai")]


def test_numbered_heading_punctuation_variants_and_leading_space(tmp_path):
    layout = DataLayout(tmp_path, "T_01")
    add_ocr(layout, "chi_tra", 1,
            ["1. Một", "2． Hai", "3﹒ Ba", "4· Bốn", "5。 Năm", "6. Sáu"], "cjk")
    layout = run_numbered(tmp_path, {"han": {"source": "chi_tra"}}, {"numbered_heading": True})
    docs = read_docs(layout)
    assert [d["document_id"] for d in docs] == [f"T_01.{i:03d}" for i in range(1, 7)]
    assert [d["heading"]["text"] for d in docs][-2:] == ["5。 Năm", "6. Sáu"]


def test_numbered_heading_requires_space_and_three_digits(tmp_path):
    layout = DataLayout(tmp_path, "T_01")
    add_ocr(layout, "chi_tra", 1,
            ["1.Bài", "1.2 Mục", "1920. Sự kiện", "12 Trang", "2．Hai"], "cjk")
    layout = run_numbered(tmp_path, {"han": {"source": "chi_tra"}}, {"numbered_heading": True})
    docs = read_docs(layout)
    assert [d["document_id"] for d in docs] == ["T_01.001"]
    assert docs[0]["heading"] is None
    review = read_tsv(layout.segmentation_dir / "review_required.tsv")
    assert any(r["reason"] == "document_heading_not_found" for r in review)


def test_numbered_heading_single_article_can_span_pages(tmp_path):
    layout = DataLayout(tmp_path, "T_01")
    add_ocr(layout, "chi_tra", 1, ["1. Một bài", "phần đầu"], "cjk")
    add_ocr(layout, "chi_tra", 2, ["phần tiếp", "phần cuối"], "cjk")
    layout = run_numbered(tmp_path, {"han": {"source": "chi_tra"}}, {"numbered_heading": True})
    docs = read_docs(layout)
    assert len(docs) == 1 and docs[0]["pages"] == [1, 2]


def test_numbered_heading_disabled_on_single_article_without_heading(tmp_path):
    layout = DataLayout(tmp_path, "T_01")
    add_ocr(layout, "chi_tra", 1, ["không có tiêu đề", "dòng hai"], "cjk")
    add_ocr(layout, "chi_tra", 2, ["dòng ba"], "cjk")
    layout = run_numbered(tmp_path, {"han": {"source": "chi_tra"}},
                          {"numbered_heading": False})
    docs = read_docs(layout)
    assert len(docs) == 1 and docs[0]["heading"] is None


def test_numbered_heading_custom_convention_and_prefix_review(tmp_path):
    layout = DataLayout(tmp_path, "T_01")
    add_ocr(layout, "chi_tra", 1,
            ["1. Không phải tiêu đề", "Article 1", "nội dung", "Article 2", "kết"], "cjk")
    layout = run_numbered(tmp_path, {"han": {"source": "chi_tra"}},
                          {"numbered_heading": False,
                           "start_patterns": [r"^Article \d+$"]})
    docs = read_docs(layout)
    assert [d["heading"]["text"] for d in docs] == ["Article 1", "Article 2"]
    review = read_tsv(layout.segmentation_dir / "review_required.tsv")
    assert any(r["reason"] == "unassigned_line" and
               r["text"] == "1. Không phải tiêu đề" for r in review)


def test_same_page_split_across_writing_systems(tmp_path):
    layout = DataLayout(tmp_path, "T_01")
    add_ocr(layout, "chi_tra", 1,
            ["1. Bài một", "a1", "b1", "2. Bài hai", "c1", "d1"], "cjk")
    add_ocr(layout, "vie", 1, ["v1", "v2", "v3", "v4", "v5", "v6"], "latin")
    layout = run_numbered(tmp_path,
                          {"han": {"source": "chi_tra"}, "am": {"source": "vie"},
                           "nghia": {"source": "none"}},
                          {"numbered_heading": True})
    am = read_layer(layout, "am")
    assert [r["document_id"] for r in am] == ["T_01.001"] * 3 + ["T_01.002"] * 3
    review = read_tsv(layout.segmentation_dir / "review_required.tsv")
    assert any(r["reason"] == "document_match_estimated" for r in review)


def test_numbered_heading_ids_stable_on_rerun(tmp_path):
    layout = DataLayout(tmp_path, "T_01")
    add_ocr(layout, "chi_tra", 1, ["1. A", "a", "2. B", "b", "3. C", "c"], "latin")
    cfg = {"common": {"work_id": "T_01", "data_root": str(tmp_path), "pages": "1"},
           "segmentation": {"document": {"numbered_heading": True},
                            "layers": {"han": {"source": "chi_tra"}}},
           "metadata_extraction": {"enabled": False}, "pairs": {}}
    path = tmp_path / "stable.json"
    path.write_text(json.dumps(cfg, ensure_ascii=False), encoding="utf-8")
    args = ["--config", str(path), "--work-id", "T_01", "--data-root", str(tmp_path),
            "--force"]
    assert segmentation.main(args) == 0
    first = read_docs(layout)
    assert [d["document_id"] for d in first] == ["T_01.001", "T_01.002", "T_01.003"]
    assert segmentation.main(args) == 0
    second = read_docs(layout)
    assert second == first

def test_numbered_heading_string_and_dict_pattern_forms(tmp_path):
    for name, document, lines in [
            ("string", {"numbered_heading": r"^#\d+ "},
             ["#1 A", "a", "#2 B", "b"]),
            ("dict", {"numbered_heading": {"pattern": r"^Item \d+:"}},
             ["Item 1: A", "a", "Item 2: B", "b"])]:
        root = tmp_path / name
        root.mkdir()
        layout = DataLayout(root, "T_01")
        add_ocr(layout, "chi_tra", 1, lines, "latin")
        layout = run_numbered(root, {"han": {"source": "chi_tra"}}, document, pages="1")
        docs = read_docs(layout)
        assert [d["document_id"] for d in docs] == ["T_01.001", "T_01.002"]
        assert [d["heading"]["text"] for d in docs] == [lines[0], lines[2]]

def _vie_heading_layers():
    return {
        "han": {"source": "chi_tra"},
        "am": {"source": "vie", "heading_mode": True,
               "start_patterns": [r"^PHIÊN\s+ÂM\b"],
               "end_patterns": [r"^DỊCH\s+NGHĨA\b"]},
        "nghia": {"source": "vie", "heading_mode": True,
                  "start_patterns": [r"^DỊCH\s+NGHĨA\b"],
                  "end_patterns": [r"^CHÚ\s+THÍCH\b"]},
    }


def test_vie_heading_sections_and_state_machine(tmp_path):
    layout = DataLayout(tmp_path, "T_01")
    add_ocr(layout, "chi_tra", 1, ["1. Article", "Han body"], "latin")
    add_ocr(layout, "vie", 1, [
        "Sau đây là toàn văn, có kèm theo phần",
        "phiên âm, dịch nghĩa và chú thích của chúng tôi.",
        "l PHIÊN ÂM : |",
        "Phù Đạo xứ thanh hư",
    ], "latin")
    add_ocr(layout, "vie", 2, [
        "pháp môn diệu xuất quần vực",
        "DỊCH NGHĨA : |",
        "Rằng chỗ Đạo thanh hư",
        "tứ đại lý thường",
    ], "latin")
    add_ocr(layout, "vie", 3, ["CHÚ THÍCH :", "Ghi chú thêm"], "latin")
    layout = run_numbered(tmp_path, _vie_heading_layers(),
                          {"numbered_heading": True}, pages="1-3")

    am = read_layer(layout, "am")
    assert [r["text"] for r in am] == ["Phù Đạo xứ thanh hư",
                                       "pháp môn diệu xuất quần vực"]
    assert [r["source_ids"] for r in am] == [["vie.P001.L004"], ["vie.P002.L001"]]
    assert {r["document_id"] for r in am} == {"T_01.001"}
    assert [r["id"] for r in am] == ["T_01.001.P001.L001", "T_01.001.P002.L002"]

    nghia = read_layer(layout, "nghia")
    assert [r["text"] for r in nghia] == ["Rằng chỗ Đạo thanh hư",
                                          "tứ đại lý thường"]
    assert [r["source_ids"] for r in nghia] == [["vie.P002.L003"], ["vie.P002.L004"]]

    headings = read_headings(layout)
    starts = [(h["layer"], h["kind"], h["text"], h["source_ids"][0])
              for h in headings if h["kind"] in ("start", "end")]
    assert ("am", "start", "l PHIÊN ÂM : |", "vie.P001.L003") in starts
    assert ("am", "end", "DỊCH NGHĨA : |", "vie.P002.L002") in starts
    assert ("nghia", "start", "DỊCH NGHĨA : |", "vie.P002.L002") in starts
    assert ("nghia", "end", "CHÚ THÍCH :", "vie.P003.L001") in starts

    review = read_tsv(layout.segmentation_dir / "review_required.tsv")
    unassigned = {sid for r in review if r["reason"] == "unassigned_line"
                  for sid in r["source_ids"].split(",") if sid}
    assert {"vie.P001.L001", "vie.P001.L002"} <= unassigned
    assert "vie.P001.L003" not in unassigned


def test_vie_missing_headings_are_reviewed_not_silently_dropped(tmp_path):
    layout = DataLayout(tmp_path, "T_01")
    add_ocr(layout, "chi_tra", 1, ["1. Article"], "latin")
    add_ocr(layout, "vie", 1, ["Dòng mở đầu", "Nội dung chưa có heading"], "latin")
    layout = run_numbered(tmp_path, _vie_heading_layers(),
                          {"numbered_heading": True}, pages="1")

    assert read_layer(layout, "am") == []
    assert read_layer(layout, "nghia") == []
    review = read_tsv(layout.segmentation_dir / "review_required.tsv")
    reasons = [r["reason"] for r in review]
    assert reasons.count("missing_heading") == 2
    unassigned = {sid for r in review if r["reason"] == "unassigned_line"
                  for sid in r["source_ids"].split(",") if sid}
    assert unassigned == {"vie.P001.L001", "vie.P001.L002"}
