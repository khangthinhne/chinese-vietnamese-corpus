"""Unified OCR Output Normalization Layer."""

from src.ocr.normalization.normalizer import (
    AmbiguousRunError,
    OcrNormalizer,
    canonical_engine_name,
    normalize_bbox,
    normalize_confidence,
    normalize_page,
)
from src.ocr.normalization.schema import (
    SCHEMA_VERSION,
    CoordinateSystem,
    EngineMetadata,
    EngineStatus,
    ImageSize,
    NormalizedEngineResult,
    NormalizedLine,
    NormalizedPage,
    PageInfo,
    SourceInfo,
    TokenInfo,
)
from src.ocr.normalization.validators import validate_normalized_page

__all__ = [
    "SCHEMA_VERSION",
    "AmbiguousRunError",
    "CoordinateSystem",
    "EngineMetadata",
    "EngineStatus",
    "ImageSize",
    "NormalizedEngineResult",
    "NormalizedLine",
    "NormalizedPage",
    "OcrNormalizer",
    "PageInfo",
    "SourceInfo",
    "TokenInfo",
    "canonical_engine_name",
    "normalize_bbox",
    "normalize_confidence",
    "normalize_page",
    "validate_normalized_page",
]
