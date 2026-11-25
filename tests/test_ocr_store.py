import json
from pathlib import Path

from PIL import Image

from src.common.corpus import DataLayout
from src.common.xmlstore import read_root
from src.ocr import ocr, raw_store, raw_xml
from src.ocr.engines import OcrEngine, OcrLine, OcrResult, register_engine


@register_engine
class FakeWritingSystemEngine(OcrEngine):
    name = "fake_ws"
    max_workers = 2
    texts_by_lang: dict[str, list[str]] = {}
    calls: list[str] = []

    @classmethod
    def describe(cls, lang: str) -> dict:
        return {"name": cls.name, "version": "1", "lang": lang}

    def recognize(self, image, *, lang: str, psm: int, dpi: int) -> OcrResult:
        FakeWritingSystemEngine.calls.append(lang)
        texts = FakeWritingSystemEngine.texts_by_lang.get(lang, [])
        lines = [OcrLine(t, (0, i * 10, 10, i * 10 + 8), 88.0) for i, t in enumerate(texts)]
        return OcrResult(self.name, lines, {"fake": lang})


def make_pages(root: Path, pages: list[int]) -> None:
    target = root / "T_01" / "02_extraction" / "pages"
    target.mkdir(parents=True)
    for page in pages:
        Image.new("RGB", (40, 40), "white").save(target / f"page_{page:04d}.png")


def test_writing_system_ocr_stores_raw_and_reuses_cache(tmp_path):
    make_pages(tmp_path, [1, 2])
    FakeWritingSystemEngine.calls.clear()
    FakeWritingSystemEngine.texts_by_lang = {
        "chi_tra": ["標題", "abc", "內容"], "vie": ["phien am", "nghia"]}
    base = ["--work-id", "T_01", "--data-root", str(tmp_path), "--engines", "fake_ws",
            "--pages", "1-2", "--source", str(tmp_path / "T_01" / "02_extraction" / "pages")]

    assert ocr.main([*base, "--writing-system", "chi_tra"]) == 0
    layout = DataLayout(tmp_path, "T_01")
    manifest = raw_store.load_manifest(layout, "chi_tra")
    assert manifest and manifest["keep"] == "cjk"
    assert [p["page"] for p in manifest["pages"]] == [1, 2]
    assert manifest["pages"][0]["selected_engine"] == "fake_ws"
    assert manifest["pages"][0]["lines"] == [{"text": "標題", "bbox": [0, 0, 10, 8], "confidence": 88.0},
                                             {"text": "內容", "bbox": [0, 20, 10, 28], "confidence": 88.0}]
    store = raw_store.load_page(layout, "chi_tra", 1, "T_01")
    assert len(store["runs"]) == 1
    assert [line["text"] for line in store["runs"][0]["lines"]] == ["標題", "abc", "內容"]

    calls = len(FakeWritingSystemEngine.calls)
    assert ocr.main([*base, "--writing-system", "chi_tra", "--force"]) == 0
    assert len(FakeWritingSystemEngine.calls) == calls
    assert len(raw_store.load_page(layout, "chi_tra", 1, "T_01")["runs"]) == 1

    assert ocr.main([*base, "--writing-system", "chi_tra"]) == 1
    assert ocr.main([*base, "--writing-system", "vie"]) == 0
    vie = raw_store.load_manifest(layout, "vie")
    assert vie and vie["keep"] == "latin"
    assert [line["text"] for line in vie["pages"][0]["lines"]] == ["phien am", "nghia"]
    assert len(list(layout.ocr_raw_root.rglob("page_*.json"))) == 4
    assert len(list(layout.ocr_manifest_dir.glob("*.json"))) == 2

    xml_path = layout.ocr_raw_xml_path("chi_tra")
    assert xml_path.name == "chi_tra.xml" and xml_path.exists()
    root = read_root(xml_path)
    assert "DOCUMENT_ID" not in xml_path.read_text(encoding="utf-8")
    ocr_el = root.find(".//OCR")
    assert ocr_el is not None and ocr_el.get("WRITING_SYSTEM") == "chi_tra"
    pages = ocr_el.findall("PAGE")
    assert [int(p.get("NUMBER")) for p in pages] == [1, 2]
    expected = [FakeWritingSystemEngine.texts_by_lang["chi_tra"][0],
                FakeWritingSystemEngine.texts_by_lang["chi_tra"][2]]
    assert [line.text for line in pages[0].findall("LINE")] == expected
    assert pages[0].find("RUNS/RUN/RAW") is not None
    lines = raw_xml.load_writing_system(layout, "chi_tra", [1])
    assert [line.text for line in lines] == expected
    assert lines[0].confidence == 88.0 and lines[0].bbox == (0, 0, 10, 8)


def test_writing_system_ocr_rejects_missing_pages(tmp_path):
    make_pages(tmp_path, [1])
    assert ocr.main(["--work-id", "T_01", "--data-root", str(tmp_path), "--engines", "fake_ws",
                     "--pages", "1-2", "--writing-system", "vie",
                     "--source", str(tmp_path / "T_01" / "02_extraction" / "pages")]) == 1
