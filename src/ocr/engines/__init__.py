"""OCR extension interface. Optional dependencies are imported only on use."""
from __future__ import annotations
import logging
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


# Mỗi engine import riêng: thiếu phụ thuộc của một engine (vd python-dotenv, requests) chỉ làm
# engine đó không dùng được, không làm hỏng các engine khác. Lỗi được báo rõ khi người dùng chọn
# đúng engine đó (xem `require_engine`). Các adapter không được nạp model lúc import.
UNAVAILABLE_ENGINES: dict[str, str] = {}


def _optional_import(module: str, engine: str) -> None:
    import importlib
    try:
        importlib.import_module(f"{__name__}.{module}")
    except ImportError as exc:
        UNAVAILABLE_ENGINES[engine] = f"{type(exc).__name__}: {exc}"
        logging.getLogger(__name__).warning(
            "Engine %s không dùng được (thiếu phụ thuộc): %s", engine, exc)


def require_engine(name: str) -> type[OcrEngine]:
    """Trả về lớp engine; báo lỗi rõ nếu engine không tồn tại hoặc thiếu phụ thuộc."""
    if name in ENGINES:
        return ENGINES[name]
    if name in UNAVAILABLE_ENGINES:
        raise ValueError(f"Engine {name} không nạp được trong môi trường này "
                         f"({UNAVAILABLE_ENGINES[name]}). Cài phụ thuộc hoặc đổi môi trường.")
    raise ValueError(f"Engine không hợp lệ: {name}. Có sẵn: {', '.join(sorted(ENGINES))}")


_optional_import("kimhannom", "kimhannom")
_optional_import("qwen", "qwen")
_optional_import("tesseract", "tesseract")
from . import paddle_ocr as _paddle_ocr  # noqa: E402,F401
