"""OCR extension interface. Optional dependencies are imported only on use."""
from __future__ import annotations
from abc import ABC, abstractmethod
from dataclasses import dataclass
from PIL import Image

@dataclass
class OcrLine:
    text: str
    bbox: tuple[int, int, int, int] | None  # x0,y0,x1,y1 in pixels, engine reading order, or None
    confidence: float | None      # display scale 0..100, not accuracy


@dataclass
class OcrResult:
    engine: str
    lines: list[OcrLine]
    raw: dict


class OcrEngine(ABC):
    name: str
    max_workers = 4

    @classmethod
    @abstractmethod
    def describe(cls, lang: str) -> dict:
        """Validate setup; fingerprint versions/model bytes without loading a model."""

    @abstractmethod
    def recognize(self, image: Image.Image, *, lang: str, psm: int, dpi: int) -> OcrResult:
        """Return ordered lines in crop coordinates and unmodified engine data."""


ENGINES: dict[str, type[OcrEngine]] = {}
DEFAULT_ENGINE = "tesseract"

def register_engine(cls: type[OcrEngine]):
    if cls.name in ENGINES:
        raise ValueError(f"Engine đã đăng ký: {cls.name}")
    ENGINES[cls.name] = cls
    return cls


try:
    from . import kimhannom as _kimhannom  # noqa: E402,F401
    from . import qwen as _qwen  # noqa: E402,F401
    from . import tesseract as _tesseract  # noqa: E402,F401
except ImportError as exc:
    # Do not hide genuine adapter errors; only make missing optional dependencies
    # explicit. The Qwen module should not load the model at import time.
    pass
    raise