"""Kiểu dữ liệu của bước phân đoạn: dòng OCR thô -> tài liệu -> lớp."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class OcrLine:
    page: int
    index: int                       # thứ tự dòng trong run đã chọn (1-based)
    text: str
    bbox: tuple[int, int, int, int] | None
    confidence: float | None
    writing_system: str
    engine: str
    cache_key: str
    image: str

    @property
    def source_id(self) -> str:
        return f"{self.writing_system}.P{self.page:03d}.L{self.index:03d}"


@dataclass
class LayerLine:
    id: str
    document_id: str
    layer: str
    page: int
    text: str
    bbox: tuple[int, int, int, int] | None
    confidence: float | None
    writing_system: str
    source_ids: list[str] = field(default_factory=list)
    flags: list[str] = field(default_factory=list)

    def to_row(self) -> dict:
        return {"id": self.id, "document_id": self.document_id, "layer": self.layer,
                "page": self.page, "text": self.text,
                "bbox": list(self.bbox) if self.bbox else None,
                "confidence": self.confidence,
                "writing_system": self.writing_system,
                "source_ids": list(self.source_ids), "flags": list(self.flags)}


@dataclass
class Document:
    id: str
    pages: list[int]
    lines: dict[str, list[OcrLine]] = field(default_factory=dict)
    heading: dict | None = None
    review: list[dict] = field(default_factory=list)


@dataclass
class LayerResult:
    layer: str
    lines: list[LayerLine]
    headings: list[dict]
    metadata: dict
    uncertain: list[dict]
    review: list[dict]


@dataclass
class DocumentLayers:
    lines: dict[str, list[LayerLine]]
    headings: list[dict]
    metadata_rows: list[dict]
    uncertain: list[dict]
    review: list[dict]
