"""Bước clean: file kiểm toán dùng chung không được chặn lớp khác; tách câu dùng bản đã làm sạch."""
from pathlib import Path

from src.cleaning import pipeline as clean_pipeline
from src.common.corpus import DataLayout
from src.segmentation import serialization as segxml
from src.segmentation import sentences as sent_pipeline

WORK = "T_CLEAN"
LAYERS = {
    "han": ["二諒江山境界連", "旁通荆廣接高宣"],
    "am": ["Phần II", "Nhị Lạng giang sơn cảnh giới liên,", "Bàng thông Kinh, Quảng tiếp Cao, Tuyên."],
    "nghia": ["Phần II", "Hai xứ Lạng núi sông liền cõi."],
    "tho": ["Phần II", "Núi sông xứ Lạng liền nhau"],
}


def write_layers(root: Path) -> DataLayout:
    layout = DataLayout(root, WORK)
    layout.ensure(layout.segmentation_layers)
    for layer, texts in LAYERS.items():
        segxml.write_layer(layout.segmentation_layer(layer), WORK, layer, [
            {"id": f"{WORK}.001.P0001.S{i:03d}", "document_id": f"{WORK}.001", "page": 1, "text": t,
             "source_ids": [f"P0001.L{i:03d}"]} for i, t in enumerate(texts, 1)])
    return layout


def clean(root: Path, layer: str, *extra: str) -> int:
    return clean_pipeline.main(["--work-id", WORK, "--layer", layer, "--data-root", str(root),
                                "--structural-only", "--drop-pattern", "^Phần II", *extra])


def test_layer_outputs_drops_only_shared_files(tmp_path):
    outs = [tmp_path / "structural" / "am.xml", tmp_path / "am.dropped.tsv",
            tmp_path / "review_required.xml", tmp_path / "cleaning_audit.xml"]
    assert clean_pipeline.layer_outputs(outs) == outs[:2]


def test_structural_clean_all_layers_without_force(tmp_path):
    layout = write_layers(tmp_path)
    for layer in LAYERS:                                   # trước đây: am/nghia/tho bị chặn vì file của han
        assert clean(tmp_path, layer) == 0, layer
        assert layout.cleaning_structural(layer).exists()
    audit = (layout.cleaning_root / "cleaning_audit.xml").read_text(encoding="utf-8")
    # audit dùng chung giữ bản ghi của mọi lớp đã có thay đổi (han không có dòng nào bị loại)
    assert all(f'LAYER="{layer}" ACTION="remove_line"' in audit for layer in ("am", "nghia", "tho"))

    # chạy lại không --force, đầu vào không đổi: bỏ qua êm (mã 0), không ghi đè
    before = layout.cleaning_structural("am").read_bytes()
    assert clean(tmp_path, "am") == 0
    assert layout.cleaning_structural("am").read_bytes() == before
    assert clean(tmp_path, "am", "--force") == 0


def test_sentence_step_reads_structurally_cleaned_text(tmp_path):
    layout = write_layers(tmp_path)
    for layer in LAYERS:
        assert clean(tmp_path, layer) == 0
    for layer in LAYERS:
        assert sent_pipeline.main(["--work-id", WORK, "--layer", layer, "--data-root", str(tmp_path)]) == 0
        structural = [r["text"] for r in segxml.read_structural_layer(layout.cleaning_structural(layer))]
        sentences = " ".join(r["text"] for r in segxml.read_sentences(layout.segmentation_sentence(layer)))
        assert "Phần II" not in sentences, layer                  # dòng bị --drop-pattern loại ở bước clean
        assert all(t in sentences or t.replace(" ", "") in sentences.replace(" ", "") for t in structural)
