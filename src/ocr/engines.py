"""OCR extension interface. Optional dependencies are imported only on use."""
from __future__ import annotations

import os
import re
import shutil
import subprocess
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path

from PIL import Image
from src.common.corpus import sha256_file


@dataclass
class OcrLine:
    text: str
    bbox: tuple[int, int, int, int]  # x0,y0,x1,y1 in pixels, engine reading order
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


def register_engine(cls: type[OcrEngine]):
    if cls.name in ENGINES:
        raise ValueError(f"Engine đã đăng ký: {cls.name}")
    ENGINES[cls.name] = cls
    return cls


def tesseract_setup():
    try:
        import pytesseract
    except ImportError as exc:
        raise RuntimeError("Cài dependencies: python -m pip install -r requirements-ocr.txt") from exc
    executable = os.environ.get("TESSERACT_CMD") or shutil.which("tesseract")
    if not executable or not Path(executable).is_file():
        raise RuntimeError("Không tìm thấy Tesseract; thêm vào PATH hoặc đặt TESSERACT_CMD")
    pytesseract.pytesseract.tesseract_cmd = executable
    return pytesseract


@register_engine
class TesseractEngine(OcrEngine):
    name = "tesseract"

    @classmethod
    def describe(cls, lang):
        tess = tesseract_setup()
        proc = subprocess.run([tess.pytesseract.tesseract_cmd, "--list-langs"],
                              capture_output=True, text=True, check=True, timeout=30)
        match = re.search(r'"(.+?)"', proc.stdout + proc.stderr)
        if not match:
            raise RuntimeError("Không xác định được thư mục tessdata từ tesseract --list-langs")
        root = Path(match[1])
        models = {}
        for code in lang.split("+"):
            model = root / f"{code}.traineddata"
            if not model.is_file():
                raise RuntimeError(f"Thiếu model Tesseract {code}: {model}")
            models[code] = sha256_file(model)
        return {"name": cls.name, "version": str(tess.get_tesseract_version()),
                "wrapper_version": tess.__version__, "models": models, "oem": 1,
                "adapter_version": 1}

    def recognize(self, image, *, lang, psm, dpi):
        tess = tesseract_setup()
        data = tess.image_to_data(image, lang=lang, config=f"--oem 1 --psm {psm} --dpi {dpi}",
                                  output_type=tess.Output.DICT, timeout=180)
        groups: dict[tuple, list[int]] = {}
        for i, text in enumerate(data["text"]):
            if text.strip() and float(data["conf"][i]) >= 0:
                key = tuple(data[k][i] for k in ("page_num", "block_num", "par_num", "line_num"))
                groups.setdefault(key, []).append(i)
        lines = []
        for indexes in groups.values():
            x0 = min(data["left"][i] for i in indexes)
            y0 = min(data["top"][i] for i in indexes)
            x1 = max(data["left"][i] + data["width"][i] for i in indexes)
            y1 = max(data["top"][i] + data["height"][i] for i in indexes)
            length = sum(len(data["text"][i]) for i in indexes)
            confidence = sum(float(data["conf"][i]) * len(data["text"][i]) for i in indexes) / length
            lines.append(OcrLine(" ".join(data["text"][i] for i in indexes),
                                 (x0, y0, x1, y1), confidence))
        return OcrResult(self.name, lines, data)


DEFAULT_ENGINE = "tesseract"
