"""Canonical Schema definition for Normalized OCR Output.

This module defines the unified, engine-independent data structures representing
OCR page recognition results across multiple engines (Qwen, PaddleOCR, KimHanNom,
Tesseract, etc.) before Weighted Voting.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal

SCHEMA_VERSION = "1.0"

EngineStatus = Literal["success", "empty", "failed", "not_run"]


@dataclass
class ImageSize:
    width: int
    height: int

    def to_dict(self) -> dict[str, int]:
        return {"width": self.width, "height": self.height}


@dataclass
class PageInfo:
    page_id: str
    page_number: int
    image: str
    image_size: ImageSize

    def to_dict(self) -> dict[str, Any]:
        return {
            "page_id": self.page_id,
            "page_number": self.page_number,
            "image": self.image,
            "image_size": self.image_size.to_dict(),
        }


@dataclass
class SourceInfo:
    run_key: str | None
    raw_file: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_key": self.run_key,
            "raw_file": self.raw_file,
        }


@dataclass
class CoordinateSystem:
    unit: str = "pixel"
    origin: str = "top_left"
    format: str = "xyxy"
    reference: str = "original_page"

    def to_dict(self) -> dict[str, str]:
        return {
            "unit": self.unit,
            "origin": self.origin,
            "format": self.format,
            "reference": self.reference,
        }


@dataclass
class TokenInfo:
    token_id: str
    text: str
    bbox: list[int] | None = None
    confidence: float | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        res: dict[str, Any] = {
            "token_id": self.token_id,
            "text": self.text,
            "bbox": self.bbox,
            "confidence": self.confidence,
        }
        if self.metadata:
            res["metadata"] = self.metadata
        return res


@dataclass
class NormalizedLine:
    line_id: str
    order: int
    text: str
    bbox: list[int] | None
    confidence: float | None
    tokens: list[TokenInfo] | None = None
    comparison_text: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        res: dict[str, Any] = {
            "line_id": self.line_id,
            "order": self.order,
            "text": self.text,
            "bbox": self.bbox,
            "confidence": self.confidence,
            "tokens": [t.to_dict() for t in self.tokens] if self.tokens is not None else None,
            "metadata": self.metadata,
        }
        if self.comparison_text is not None:
            res["comparison_text"] = self.comparison_text
        return res


@dataclass
class EngineMetadata:
    engine: str
    engine_version: str | None = None
    model: str | None = None
    parameters: dict[str, Any] = field(default_factory=dict)
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        res: dict[str, Any] = {
            "engine": self.engine,
            "engine_version": self.engine_version,
            "model": self.model,
            "parameters": self.parameters,
        }
        if self.extra:
            res.update(self.extra)
        return res


@dataclass
class NormalizedEngineResult:
    status: EngineStatus
    source: SourceInfo
    metadata: EngineMetadata
    coordinate_system: CoordinateSystem
    lines: list[NormalizedLine] = field(default_factory=list)
    raw_output: Any = field(default_factory=dict)
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        res: dict[str, Any] = {
            "status": self.status,
            "source": self.source.to_dict(),
            "metadata": self.metadata.to_dict(),
            "coordinate_system": self.coordinate_system.to_dict(),
            "lines": [line.to_dict() for line in self.lines],
            "raw_output": self.raw_output,
        }
        if self.error:
            res["error"] = self.error
        return res


@dataclass
class NormalizedPage:
    document_id: str
    writing_system: str
    page: PageInfo
    engines: dict[str, NormalizedEngineResult] = field(default_factory=dict)
    schema_version: str = SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "document_id": self.document_id,
            "writing_system": self.writing_system,
            "page": self.page.to_dict(),
            "engines": {name: eng.to_dict() for name, eng in self.engines.items()},
        }
