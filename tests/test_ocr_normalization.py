import json
from pathlib import Path
from unittest.mock import MagicMock
import pytest
from PIL import Image

from src.common.corpus import DataLayout
from src.ocr.engines import ENGINES, OcrEngine, OcrResult
from src.ocr.engines.kimhannom import KimHanNomEngine
from src.ocr.normalization.schema import (
    CoordinateSystem,
    EngineMetadata,
    ImageSize,
    NormalizedEngineResult,
    NormalizedLine,
    NormalizedPage,
    PageInfo,
    SourceInfo,
)
from src.ocr.normalization.validators import validate_normalized_page
from src.ocr.normalization.normalizer import (
    AmbiguousRunError,
    OcrNormalizer,
    canonical_engine_name,
    make_relative_image_path,
    normalize_bbox,
    normalize_confidence,
    normalize_page,
    normalize_run,
    select_run_for_engine,
)


# ============================================================================
# 1. Active Engines Registry & Adapters
# ============================================================================

def test_active_engines_registry():
    """Verify registry contains exactly the three active engines without live API calls."""
    active_keys = sorted(ENGINES.keys())
    assert active_keys == ["kimhannom", "qwen", "tesseract"]
    assert issubclass(ENGINES["kimhannom"], OcrEngine)
    assert issubclass(ENGINES["qwen"], OcrEngine)
    assert issubclass(ENGINES["tesseract"], OcrEngine)


def test_kimhannom_engine_adapter_describe_and_recognize(monkeypatch):
    """Verify KimHanNomEngine describes itself statically and adapts predict() cleanly."""
    desc = KimHanNomEngine.describe("chi_tra")
    assert desc["name"] == "kimhannom"
    assert desc["version"] == "1.0"
    assert desc["adapter_version"] == 1

    # Mock internal client to prevent live network calls during testing
    mock_client = MagicMock()
    mock_client.predict.return_value = {
        "engine": "kimhannom",
        "text": "夫道處清虛 域。若稱其功能",
        "raw": {
            "result_ocr_text": ["夫道處清虛", "域。若稱其功能"],
            "result_bbox": [
                [10, 20, 300, 60],
                [10, 70, 300, 110],
            ],
            "code": "000000",
        },
    }

    engine = KimHanNomEngine(token="mock_token")
    monkeypatch.setattr(engine, "_get_client", lambda: mock_client)

    img = Image.new("RGB", (400, 200), "white")
    result = engine.recognize(img, lang="chi_tra", psm=6, dpi=300)

    assert isinstance(result, OcrResult)
    assert result.engine == "kimhannom"
    assert len(result.lines) == 2
    assert result.lines[0].text == "夫道處清虛"
    assert result.lines[0].bbox == (10, 20, 300, 60)
    assert result.lines[0].confidence is None  # Never fabricate confidence!
    assert result.lines[1].text == "域。若稱其功能"
    assert result.lines[1].bbox == (10, 70, 300, 110)
    assert result.lines[1].confidence is None
    assert result.raw["code"] == "000000"


def test_kimhannom_engine_handles_errors(monkeypatch):
    """Verify KimHanNomEngine handles API errors explicitly."""
    engine = KimHanNomEngine(token="mock_token")
    mock_client = MagicMock()
    mock_client.predict.side_effect = RuntimeError("API rate limit exceeded")
    monkeypatch.setattr(engine, "_get_client", lambda: mock_client)

    img = Image.new("RGB", (100, 100), "white")
    with pytest.raises(RuntimeError, match="KimHanNom OCR inference failed"):
        engine.recognize(img, lang="chi_tra", psm=6, dpi=300)


# ============================================================================
# 2. Schema & Validation Tests
# ============================================================================

def test_valid_normalized_page_schema():
    page = NormalizedPage(
        schema_version="1.0",
        document_id="CPO_18",
        writing_system="chi_tra",
        page=PageInfo(
            page_id="CPO_18.P0205",
            page_number=205,
            image="02_extraction/pages/page_0205.png",
            image_size=ImageSize(width=2000, height=3000),
        ),
        engines={
            "qwen": NormalizedEngineResult(
                status="success",
                source=SourceInfo(run_key="run_01", raw_file="03_ocr/raw/chi_tra/page_0205.json"),
                metadata=EngineMetadata(engine="qwen", engine_version="3", model="Qwen2.5-VL-3B-Instruct"),
                coordinate_system=CoordinateSystem(),
                lines=[
                    NormalizedLine(
                        line_id="qwen.L001",
                        order=1,
                        text="第一行",
                        bbox=[100, 200, 500, 250],
                        confidence=96.5,
                    )
                ],
            ),
            "kimhannom": NormalizedEngineResult(
                status="not_run",
                source=SourceInfo(run_key=None, raw_file="03_ocr/raw/chi_tra/page_0205.json"),
                metadata=EngineMetadata(engine="kimhannom"),
                coordinate_system=CoordinateSystem(),
                lines=[],
            ),
        },
    )
    errors = validate_normalized_page(page)
    assert errors == []
    d = page.to_dict()
    assert d["schema_version"] == "1.0"
    assert d["engines"]["kimhannom"]["status"] == "not_run"


def test_validation_catches_invalid_fields():
    page = NormalizedPage(
        schema_version="2.0",
        document_id="",
        writing_system="chi_tra",
        page=PageInfo(
            page_id="",
            page_number=0,
            image="",
            image_size=ImageSize(width=0, height=-10),
        ),
        engines={
            "bad_engine": NormalizedEngineResult(
                status="unknown_status",
                source=SourceInfo(run_key="", raw_file=""),
                metadata=EngineMetadata(engine="bad_engine"),
                coordinate_system=CoordinateSystem(),
                lines=[
                    NormalizedLine(
                        line_id="bad.L001",
                        order=1,
                        text="Text",
                        bbox=[500, 200, 100, 250],  # x0 > x1
                        confidence=150.0,  # > 100
                    ),
                    NormalizedLine(
                        line_id="bad.L001",  # duplicate line_id
                        order=2,
                        text="Text 2",
                        bbox=[-50, 0, 100, 50],  # negative coordinate
                        confidence=-5.0,  # < 0
                    ),
                ],
            )
        },
    )
    errors = validate_normalized_page(page)
    error_str = " ".join(errors)
    assert "Invalid or unsupported schema_version: '2.0'" in error_str
    assert "Missing or invalid document_id" in error_str
    assert "engines.bad_engine.status must be one of" in error_str
    assert "x0 (500) > x1 (100)" in error_str
    assert "confidence out of range 0..100: 150.0" in error_str
    assert "duplicate ID: bad.L001" in error_str
    assert "coordinates cannot be negative" in error_str
    assert "confidence out of range 0..100: -5.0" in error_str


# ============================================================================
# 3. Coordinate Handling Contract Tests
# ============================================================================

def test_normalize_bbox_full_page():
    """Full-page OCR coordinates are kept without offset."""
    img_size = ImageSize(width=1000, height=1500)
    box = normalize_bbox([100, 200, 500, 600], image_size=img_size, crop_offset=None)
    assert box == [100, 200, 500, 600]


def test_normalize_bbox_cropped_with_offsets():
    """Cropped OCR with explicit non-zero x and y offsets adds them exactly once."""
    img_size = ImageSize(width=1000, height=2000)
    # Local box [10, 20, 110, 120] in crop of offset [200, 500]
    box = normalize_bbox([10, 20, 110, 120], image_size=img_size, crop_offset=(200, 500))
    assert box == [210, 520, 310, 620]


def test_normalize_bbox_already_page_relative():
    """Raw store line bboxes (already page-relative) are never shifted twice."""
    img_size = ImageSize(width=1000, height=2000)
    # Stored line is already [210, 520, 310, 620]; no crop_offset passed -> unchanged
    box = normalize_bbox([210, 520, 310, 620], image_size=img_size, crop_offset=None)
    assert box == [210, 520, 310, 620]


def test_normalize_bbox_missing():
    """Missing bbox is preserved as None."""
    img_size = ImageSize(width=1000, height=1500)
    assert normalize_bbox(None, image_size=img_size) is None


def test_normalize_bbox_image_boundaries():
    """Bboxes extending past image borders are clamped to boundaries."""
    img_size = ImageSize(width=1000, height=1500)
    box = normalize_bbox([-20, -5, 1050, 1550], image_size=img_size)
    assert box == [0, 0, 1000, 1500]


def test_normalize_bbox_polygon():
    """Polygon bounding boxes are converted to [min_x, min_y, max_x, max_y]."""
    img_size = ImageSize(width=1000, height=1000)
    poly = [[100, 150], [400, 150], [400, 250], [100, 250]]
    box = normalize_bbox(poly, image_size=img_size)
    assert box == [100, 150, 400, 250]


# ============================================================================
# 4. Confidence Normalization Tests
# ============================================================================

def test_normalize_confidence():
    # 0.0 - 100.0 scale (Qwen, Tesseract) preserved
    assert normalize_confidence(88.5) == 88.5
    assert normalize_confidence(100.0) == 100.0

    # None preserved without fabrication (KimHanNom)
    assert normalize_confidence(None) is None

    # Decimal 0-1 scale conversion
    assert normalize_confidence(0.954, engine_name="paddleocr") == 95.4


# ============================================================================
# 5. Image Path Portability Tests
# ============================================================================

def test_image_path_portability(tmp_path):
    layout = DataLayout(tmp_path, "TEST_CORPUS")
    # Absolute path inside work_dir
    abs_img = layout.work_dir / "02_extraction" / "pages" / "page_0001.png"
    rel_path = make_relative_image_path(layout, abs_img)
    assert rel_path == "02_extraction/pages/page_0001.png"
    assert "\\" not in rel_path

    # External path preserved cleanly without machine-specific absolute distortion
    external = Path("D:/external/dataset/img.png")
    preserved = make_relative_image_path(layout, external)
    assert preserved == "D:/external/dataset/img.png"


# ============================================================================
# 6. Run Selection & Disambiguation Tests
# ============================================================================

def test_select_run_multiple_qwen_models():
    """Verify distinct Qwen model runs are disambiguated by manifest or explicit key."""
    runs = [
        {
            "engine": "qwen",
            "key": "key_qwen_3b",
            "parameters": {"model": "Qwen2.5-VL-3B-Instruct"},
            "lines": [{"text": "3B result"}],
        },
        {
            "engine": "qwen",
            "key": "key_qwen_7b",
            "parameters": {"model": "Qwen2.5-VL-7B-Instruct"},
            "lines": [{"text": "7B result"}],
        },
    ]

    # Without disambiguation -> raises AmbiguousRunError
    with pytest.raises(AmbiguousRunError):
        select_run_for_engine(runs, "qwen", page=1)

    # Disambiguated by explicit key
    sel_7b = select_run_for_engine(runs, "qwen", page=1, explicit_key="key_qwen_7b")
    assert sel_7b["key"] == "key_qwen_7b"

    # Disambiguated by manifest
    manifest = {
        "selected_engine": "qwen",
        "selected_key": "key_qwen_3b",
        "engines": [{"engine": "qwen", "key": "key_qwen_3b"}],
    }
    sel_manifest = select_run_for_engine(runs, "qwen", page=1, manifest=manifest)
    assert sel_manifest["key"] == "key_qwen_3b"


def test_single_engine_manifest_does_not_select_other_engines():
    """A manifest selecting only Qwen must never resolve ambiguous Tesseract runs."""
    runs = [
        {"engine": "qwen", "key": "qwen_k1", "lines": []},
        {"engine": "tesseract", "key": "tess_k1", "lines": []},
        {"engine": "tesseract", "key": "tess_k2", "lines": []},
    ]
    manifest = {
        "selected_engine": "qwen",
        "selected_key": "qwen_k1",
        "engines": [{"engine": "qwen", "key": "qwen_k1"}],
    }
    # Qwen resolves cleanly
    assert select_run_for_engine(runs, "qwen", page=1, manifest=manifest)["key"] == "qwen_k1"

    # Tesseract must NOT use Qwen's selection and must raise AmbiguousRunError
    with pytest.raises(AmbiguousRunError):
        select_run_for_engine(runs, "tesseract", page=1, manifest=manifest)


# ============================================================================
# 7. Persistence & Active Engine Representation
# ============================================================================

def setup_mock_corpus(root: Path, work_id: str = "WORK_01") -> DataLayout:
    layout = DataLayout(root, work_id)
    img_dir = layout.work_dir / "02_extraction" / "pages"
    img_dir.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (1000, 1200), "white").save(img_dir / "page_0001.png")

    raw_dir = layout.ocr_raw_root / "chi_tra"
    raw_dir.mkdir(parents=True, exist_ok=True)
    raw_store_data = {
        "schema_version": 1,
        "work_id": work_id,
        "writing_system": "chi_tra",
        "page": 1,
        "runs": [
            {
                "key": "run_qwen_1",
                "engine": "qwen",
                "parameters": {"engine": {"name": "qwen", "model_id": "Qwen2.5-VL-3B-Instruct"}},
                "metadata": {"engine": "qwen", "model": "Qwen2.5-VL-3B-Instruct"},
                "lines": [
                    {"text": "寒來暑往", "bbox": [100, 150, 400, 190], "confidence": 98.0}
                ],
            },
            {
                "key": "run_tess_1",
                "engine": "tesseract",
                "parameters": {"engine": {"name": "tesseract"}},
                "metadata": {"engine": "tesseract"},
                "lines": [
                    {"text": "秋收冬藏", "bbox": [100, 200, 400, 240], "confidence": 80.0}
                ],
            },
        ],
    }
    (raw_dir / "page_0001.json").write_text(json.dumps(raw_store_data, ensure_ascii=False), encoding="utf-8")

    manifest_dir = layout.ocr_manifest_dir
    manifest_dir.mkdir(parents=True, exist_ok=True)
    manifest_data = {
        "schema_version": 1,
        "work_id": work_id,
        "writing_system": "chi_tra",
        "selected_engine": "qwen",
        "selected_key": "run_qwen_1",
        "engines": [
            {"engine": "qwen", "key": "run_qwen_1"},
            {"engine": "tesseract", "key": "run_tess_1"},
        ],
    }
    (manifest_dir / "chi_tra.json").write_text(json.dumps(manifest_data, ensure_ascii=False), encoding="utf-8")
    return layout


def test_normalize_page_and_save(tmp_path):
    layout = setup_mock_corpus(tmp_path)
    normalizer = OcrNormalizer(layout)

    # Normalize page 1: defaults to all active engines (kimhannom, qwen, tesseract)
    norm_page = normalizer.normalize_and_save("chi_tra", 1)
    assert norm_page.schema_version == "1.0"
    assert norm_page.document_id == "WORK_01"
    assert norm_page.writing_system == "chi_tra"
    assert norm_page.page.image_size.width == 1000
    assert norm_page.page.image_size.height == 1200
    assert set(norm_page.engines.keys()) == {"kimhannom", "qwen", "tesseract"}

    # Qwen & Tesseract are success; KimHanNom is not_run
    assert norm_page.engines["qwen"].status == "success"
    assert norm_page.engines["tesseract"].status == "success"
    assert norm_page.engines["kimhannom"].status == "not_run"
    assert norm_page.engines["kimhannom"].lines == []

    # Check saved file
    out_path = layout.ocr_normalized_path("chi_tra", 1)
    assert out_path.exists()
    saved = json.loads(out_path.read_text(encoding="utf-8"))
    assert saved["schema_version"] == "1.0"
    assert saved["engines"]["qwen"]["lines"][0]["text"] == "寒來暑往"
    assert saved["engines"]["tesseract"]["lines"][0]["text"] == "秋收冬藏"

    # Attempting to save again without force raises FileExistsError
    with pytest.raises(FileExistsError):
        normalizer.normalize_and_save("chi_tra", 1, force=False)

    # With force=True, overwrites successfully
    normalizer.normalize_and_save("chi_tra", 1, force=True)

    # Dry run does not fail even if file exists and force=False
    dry_page = normalizer.normalize_and_save("chi_tra", 1, dry_run=True, force=False)
    assert dry_page is not None
