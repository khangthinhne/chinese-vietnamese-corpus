"""Comprehensive tests for single-stream Qwen VLM OCR & Segmentation architecture."""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path
import xml.etree.ElementTree as ET

import pytest
from PIL import Image

# Ensure repository root is on sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.common.page_naming import page_stem, page_filename
from src.common.corpus import DataLayout
from src.segmentation.model import OcrLine, Document, DocumentLayers, LayerLine
from src.ocr import raw_store, raw_xml
from src.segmentation.documents import segment_documents_unified, load_unified_stream
from src.segmentation.layers import assign_document_unified
from src.pipeline import is_unified_ocr_config, validate_config, build_plan


# =========================================================================
# 1. Page Naming Convention Tests
# =========================================================================

class TestPageNaming:
    def test_page_stem_standard_cases(self):
        assert page_stem(1) == "0001"
        assert page_stem(15) == "0015"
        assert page_stem(206) == "0206"
        assert page_stem(9999) == "9999"

    def test_page_filename(self):
        assert page_filename(1) == "0001.txt"
        assert page_filename(15, ".txt") == "0015.txt"
        assert page_filename(15, "txt") == "0015.txt"
        assert page_filename(206, ".json") == "0206.json"
        assert page_filename(9999, ".png") == "9999.png"

    def test_page_stem_invalid(self):
        with pytest.raises(ValueError):
            page_stem(-1)
        with pytest.raises(ValueError):
            page_stem("15")  # type: ignore


# =========================================================================
# 2. Data Model & DataLayout Tests
# =========================================================================

class TestDataModelAndLayout:
    def test_ocr_line_unified_source_id(self):
        # Without writing system, defaults to 4-digit zero padded format: P0015.L001
        line = OcrLine(page=15, index=1, text="二諒江山境界連")
        assert line.source_id == "P0015.L001"
        assert line.page == 15
        assert line.index == 1
        assert line.text == "二諒江山境界連"
        assert line.writing_system == ""

    def test_ocr_line_legacy_source_id(self):
        # With writing system, preserves legacy format
        line = OcrLine(page=15, index=1, text="二諒江山境界連", writing_system="chi_tra")
        assert line.source_id == "chi_tra.P015.L001"

    def test_document_model_all_lines(self):
        # Flat list lines
        lines_list = [
            OcrLine(page=15, index=1, text="Line 1"),
            OcrLine(page=15, index=2, text="Line 2"),
        ]
        doc1 = Document(id="CPO_19.001", pages=[15], lines=lines_list)
        assert len(doc1.all_lines) == 2
        assert doc1.all_lines == lines_list

        # Dict lines (legacy format)
        lines_dict = {
            "chi_tra": [OcrLine(page=15, index=1, text="漢字", writing_system="chi_tra")],
            "vie": [OcrLine(page=15, index=2, text="tiếng Việt", writing_system="vie")],
        }
        doc2 = Document(id="CPO_19.002", pages=[15], lines=lines_dict)
        assert len(doc2.all_lines) == 2
        assert doc2.all_lines[0].text == "漢字"

    def test_data_layout_paths(self):
        layout = DataLayout(Path("data"), "CPO_19")
        assert layout.ocr_unified_xml_path == Path("data/CPO_19/03_ocr/ocr_raw/raw.xml")
        assert layout.ocr_raw_unified_path(15) == Path("data/CPO_19/03_ocr/raw/0015.json")
        assert layout.ocr_unified_text_path(15) == Path("data/CPO_19/03_ocr/ocr_raw_text/0015.txt")
        assert layout.ocr_metadata_path == Path("data/CPO_19/03_ocr/metadata.json")


# =========================================================================
# 3. Raw Store & XML Tests
# =========================================================================

class TestRawStoreAndXml:
    def test_raw_store_load_save_unified(self, tmp_path):
        layout = DataLayout(tmp_path, "TEST_WORK")
        layout.ensure(layout.ocr_raw_unified_dir, layout.ocr_manifest_dir)

        # Initial load when file does not exist
        empty_page = raw_store.load_unified_page(layout, 15, "TEST_WORK")
        assert empty_page["page"] == 15
        assert empty_page["runs"] == []

        # Add a run and save
        run_record = {
            "key": "testkey123",
            "parameters": {"dpi": 300},
            "lines": [{"text": "其一", "bbox": [10, 20, 100, 40], "confidence": 98.0}],
            "raw": {"response": "dummy"}
        }
        empty_page["runs"].append(run_record)
        saved_path = raw_store.save_unified_page(layout, 15, empty_page)
        assert saved_path.name == "0015.json"
        assert saved_path.exists()

        # Reload
        reloaded = raw_store.load_unified_page(layout, 15, "TEST_WORK")
        assert len(reloaded["runs"]) == 1
        assert reloaded["runs"][0]["lines"][0]["text"] == "其一"

    def test_unified_xml_roundtrip_and_text_export(self, tmp_path):
        layout = DataLayout(tmp_path, "TEST_WORK")
        layout.ensure(layout.ocr_raw_unified_dir, layout.ocr_manifest_dir,
                      layout.ocr_raw_xml, layout.ocr_unified_text_dir)

        # Create raw page data
        page_data = {
            "schema_version": 1,
            "work_id": "TEST_WORK",
            "page": 15,
            "runs": [{
                "key": "k1",
                "parameters": {"dpi": 300},
                "lines": [
                    {"text": "其一", "bbox": [100, 200, 1200, 260], "confidence": 95.0},
                    {"text": "二諒江山境界連", "bbox": [100, 280, 1200, 340], "confidence": 92.0},
                ],
                "raw": {}
            }]
        }
        raw_store.save_unified_page(layout, 15, page_data)

        # Write manifest
        manifest = {
            "schema_version": 1,
            "work_id": "TEST_WORK",
            "mode": "unified",
            "source": {"path": "test/source"},
            "engines": {"qwen": {"name": "qwen"}},
            "parameters": {"pages": [15]},
            "pages": [{
                "page": 15,
                "image": "0015.png",
                "selected_engine": "qwen",
                "selected_key": "k1",
                "confidence": 93.5,
                "empty": False,
                "n_lines": 2,
                "lines": [
                    {"text": "其一", "bbox": [100, 200, 1200, 260], "confidence": 95.0},
                    {"text": "二諒江山境界連", "bbox": [100, 280, 1200, 340], "confidence": 92.0},
                ]
            }]
        }
        raw_store.write_unified_manifest(layout, manifest)

        # Generate XML and TXT exports
        xml_path = raw_xml.write_unified(layout, [15])
        assert xml_path.exists()
        assert xml_path == layout.ocr_unified_xml_path

        # Verify TXT export was generated
        txt_path = layout.ocr_unified_text_path(15)
        assert txt_path.exists()
        content = txt_path.read_text(encoding="utf-8")
        assert "其一\n二諒江山境界連\n" == content

        # Verify load_unified reads lines correctly
        loaded_lines = raw_xml.load_unified(layout, [15])
        assert len(loaded_lines) == 2
        assert loaded_lines[0].source_id == "P0015.L001"
        assert loaded_lines[0].text == "其一"
        assert loaded_lines[0].bbox == (100, 200, 1200, 260)
        assert loaded_lines[0].confidence == 95.0
        assert loaded_lines[1].source_id == "P0015.L002"
        assert loaded_lines[1].text == "二諒江山境界連"


# =========================================================================
# 4. Document Segmentation Tests (Unified Single Stream)
# =========================================================================

class TestDocumentSegmentationUnified:
    def test_single_document_single_page(self):
        lines = [
            OcrLine(page=15, index=1, text="其一"),
            OcrLine(page=15, index=2, text="二諒江山境界連"),
            OcrLine(page=15, index=3, text="旁通荆廣接高宣"),
        ]
        cfg = {
            "start_patterns": [r"^\s*其[一二三四五六七八九十]+\s*$"],
        }
        docs, review = segment_documents_unified("CPO_19", lines, cfg, [15])
        assert len(docs) == 1
        assert docs[0].id == "CPO_19.001"
        assert docs[0].pages == [15]
        assert len(docs[0].all_lines) == 3

    def test_multiple_documents_on_single_page(self):
        lines = [
            OcrLine(page=15, index=1, text="其一"),
            OcrLine(page=15, index=2, text="二諒江山境界連"),
            OcrLine(page=15, index=3, text="其二"),
            OcrLine(page=15, index=4, text="旁通荆廣接高宣"),
        ]
        cfg = {
            "start_patterns": [r"^\s*其[一二三四五六七八九十]+\s*$"],
        }
        docs, review = segment_documents_unified("CPO_19", lines, cfg, [15])
        assert len(docs) == 2
        assert docs[0].id == "CPO_19.001"
        assert len(docs[0].all_lines) == 2
        assert docs[1].id == "CPO_19.002"
        assert len(docs[1].all_lines) == 2

    def test_document_spanning_multiple_pages(self):
        lines = [
            OcrLine(page=15, index=1, text="其一"),
            OcrLine(page=15, index=2, text="二諒江山境界連"),
            OcrLine(page=16, index=1, text="旁通荆廣接高宣"),
            OcrLine(page=16, index=2, text="溪流奔馳爭歸海"),
        ]
        cfg = {
            "start_patterns": [r"^\s*其[一二三四五六七八九十]+\s*$"],
        }
        docs, review = segment_documents_unified("CPO_19", lines, cfg, [15, 16])
        assert len(docs) == 1
        assert docs[0].pages == [15, 16]
        assert len(docs[0].all_lines) == 4

    def test_numbered_footnotes_not_confused_with_document_headings(self):
        lines = [
            OcrLine(page=15, index=1, text="其一"),
            OcrLine(page=15, index=2, text="二諒江山境界連"),
            OcrLine(page=15, index=3, text="1. Hai xứ Lạng"),
            OcrLine(page=15, index=4, text="2. Kinh, Quảng"),
            OcrLine(page=15, index=5, text="3. Hòn đá tướng Minh"),
        ]
        # Document start pattern is strictly for poem headings like "其一"
        cfg = {
            "start_patterns": [r"^\s*其[一二三四五六七八九十]+\s*$"],
            "numbered_heading": False,
        }
        docs, review = segment_documents_unified("CPO_19", lines, cfg, [15])
        assert len(docs) == 1  # Only 1 document, footnotes are NOT split into separate docs
        assert len(docs[0].all_lines) == 5


# =========================================================================
# 5. Layer Segmentation Tests (State Machine on Single Stream)
# =========================================================================

class TestLayerSegmentationUnified:
    @pytest.fixture
    def layer_cfg(self):
        return {
            "han": {
                "start_patterns": [r"^\s*其[一二三四五六七八九十]+\s*$"],
                "end_patterns": [r"^\s*PHI[ÊỀE]N\s+ÂM\s*[:：]?\s*$"],
                "optional": False,
            },
            "am": {
                "heading_mode": True,
                "start_patterns": [r"^\s*PHI[ÊỀE]N\s+ÂM\s*[:：]?\s*$"],
                "end_patterns": [r"^\s*DỊCH\s+NGHĨA\b"],
            },
            "nghia": {
                "heading_mode": True,
                "start_patterns": [r"^\s*DỊCH\s+NGHĨA\b"],
                "end_patterns": [r"^\s*DỊCH\s+TH[ƠO]\b"],
            },
            "tho": {
                "optional": True,
                "heading_mode": True,
                "start_patterns": [r"^\s*DỊCH\s+TH[ƠO]\b\s*[:：]?\s*$"],
                "end_patterns": [],
                "metadata": False,
            },
        }

    def test_specification_fixture_full_four_layers(self, layer_cfg):
        """Test exact fixture from Section 13.4 of the specification."""
        texts = [
            "其一",
            "二諒江山境界連",
            "旁通荆廣接高宣",
            "溪流奔馳爭歸海",
            "PHIÊN ÂM:",
            "Nhị Lạng giang sơn cảnh giới liên,",
            "Bàng thông Kinh, Quảng tiếp Cao, Tuyên.",
            "DỊCH NGHĨA:",
            "Hai xứ Lạng có cảnh giới liên tiếp...",
            "DỊCH THƠ:",
            "Lạng Sơn đường...",
        ]
        lines = [OcrLine(page=15, index=i + 1, text=t) for i, t in enumerate(texts)]
        doc = Document(id="CPO_19.001", pages=[15], lines=lines)

        result = assign_document_unified("CPO_19", doc, layer_cfg, {})

        # Check han layer: 4 lines exactly as specified in Section 13.4
        han_lines = [l.text for l in result.lines.get("han", [])]
        assert len(han_lines) == 4
        assert han_lines[0] == "其一"
        assert han_lines[1] == "二諒江山境界連"
        assert han_lines[2] == "旁通荆廣接高宣"
        assert han_lines[3] == "溪流奔馳爭歸海"

        # Check am layer
        am_lines = [l.text for l in result.lines.get("am", [])]
        assert len(am_lines) == 2
        assert am_lines[0] == "Nhị Lạng giang sơn cảnh giới liên,"
        assert am_lines[1] == "Bàng thông Kinh, Quảng tiếp Cao, Tuyên."

        # Check nghia layer
        nghia_lines = [l.text for l in result.lines.get("nghia", [])]
        assert len(nghia_lines) == 1
        assert nghia_lines[0] == "Hai xứ Lạng có cảnh giới liên tiếp..."

        # Check tho layer
        tho_lines = [l.text for l in result.lines.get("tho", [])]
        assert len(tho_lines) == 1
        assert tho_lines[0] == "Lạng Sơn đường..."

        # Check headings are stored separately and not mixed in layer content
        headings = result.headings
        assert len(headings) >= 3
        heading_texts = [h["text"] for h in headings]
        assert any("PHIÊN ÂM:" in h for h in heading_texts)
        assert any("DỊCH NGHĨA:" in h for h in heading_texts)
        assert any("DỊCH THƠ:" in h for h in heading_texts)

    def test_missing_optional_tho_layer(self, layer_cfg):
        texts = [
            "其一",
            "二諒江山境界連",
            "PHIÊN ÂM:",
            "Nhị Lạng giang sơn cảnh giới liên,",
            "DỊCH NGHĨA:",
            "Hai xứ Lạng có cảnh giới liên tiếp...",
        ]
        lines = [OcrLine(page=15, index=i + 1, text=t) for i, t in enumerate(texts)]
        doc = Document(id="CPO_19.001", pages=[15], lines=lines)
        result = assign_document_unified("CPO_19", doc, layer_cfg, {})

        assert "tho" not in result.lines or len(result.lines.get("tho", [])) == 0
        # No review error for missing optional layer
        tho_missing_review = [r for r in result.review if r.get("layer") == "tho"]
        assert len(tho_missing_review) == 0

    def test_heading_variant_ocr_error(self, layer_cfg):
        # Heading with OCR imperfection: "l PHIEN ÂM :"
        texts = [
            "二諒江山境界連",
            "l PHIEN ÂM :",
            "Nhị Lạng giang sơn cảnh giới liên,",
            "DỊCH NGHĨA",
            "Hai xứ Lạng...",
        ]
        lines = [OcrLine(page=15, index=i + 1, text=t) for i, t in enumerate(texts)]
        doc = Document(id="CPO_19.001", pages=[15], lines=lines)
        result = assign_document_unified("CPO_19", doc, layer_cfg, {})

        am_lines = [l.text for l in result.lines.get("am", [])]
        assert len(am_lines) == 1
        assert am_lines[0].startswith("Nhị Lạng")


# =========================================================================
# 6. Pipeline Config & Orchestration Tests
# =========================================================================

class TestPipelineConfig:
    def test_unified_config_detection(self):
        unified_cfg = {
            "common": {"work_id": "CPO_19"},
            "ocr": {"engine": "qwen", "workers": 4}
        }
        assert is_unified_ocr_config(unified_cfg) is True

        legacy_cfg = {
            "common": {"work_id": "CPO_19"},
            "ocr": {"chi_tra": {"engine": "qwen"}, "vie": {"engine": "tesseract"}}
        }
        assert is_unified_ocr_config(legacy_cfg) is False

    def test_validate_unified_config(self):
        cfg = {
            "common": {"work_id": "CPO_19", "pages": "15-17"},
            "ocr": {"engine": "qwen", "workers": 4},
            "segmentation": {
                "layers": {
                    "han": {"optional": False},
                    "am": {"start_patterns": ["^PHIÊN ÂM"]},
                    "nghia": {"start_patterns": ["^DỊCH NGHĨA"]},
                }
            }
        }
        # Should not raise ValueError even though no 'source' is in layers
        validate_config(cfg)

    def test_build_plan_unified_ocr(self):
        cfg = {
            "common": {
                "work_id": "CPO_19",
                "source": "dummy.pdf",
                "pages": "15-17",
                "data_root": "data"
            },
            "ocr": {"engine": "qwen", "workers": 4},
            "segmentation": {
                "layers": {
                    "han": {},
                    "am": {},
                    "nghia": {},
                }
            }
        }
        plan = build_plan(cfg, steps=["ocr"], layers=None, pairs=None, force=False)
        assert len(plan) == 1
        step, argv = plan[0]
        assert step == "ocr"
        assert "--unified" in argv
        assert "--writing-system" not in argv
