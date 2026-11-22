
import os
import tempfile
import time
import requests
import json
from pathlib import Path
from typing import Any
import mimetypes
from collections import deque
from dotenv import load_dotenv
from PIL import Image

from src.ocr.engines import OcrEngine, OcrLine, OcrResult, register_engine

load_dotenv()

class KimHanNomOCR:
    BASE_URL = "https://kimhannom.fit.hcmus.edu.vn"
    LIMITS = [(60, 20), (3600, 45), (86400, 190)]  
    UPLOAD_ENDPOINT = "/api/web/clc-sinonom/image-upload"
    OCR_ENDPOINT = "/api/web/clc-sinonom/image-ocr"

    def __init__(
        self,
        token: str | None = None,
        ocr_id: int = 1,
        lang_type: int = 1,
        reading_direction: int = 1,
        epitaph: int = 0,
        tag: str | None = None,
        timeout: int = 120,
    ):  
        self._calls = deque(maxlen=200)
        self.token = token or os.getenv("KIMHANNOM_TOKEN")

        if not self.token:
            raise ValueError("Missing KIMHANNOM_TOKEN")

        self.ocr_config = {
            "ocr_id": ocr_id,
            "lang_type": lang_type,
            "reading_direction": reading_direction,
            "epitaph": epitaph,
            "tag": tag,
        }

        self.timeout = timeout

        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 Chrome/138.0.0.0 Safari/537.36"
            ),
            "Authorization": f"Bearer {self.token}",
        })

    def _throttle(self):
        while True:
            now = time.time()
            wait = 0
            for window, limit in self.LIMITS:
                recent = [t for t in self._calls if now - t < window]
                if len(recent) >= limit:
                    wait = max(wait, window - (now - recent[0]) + 0.5)
            if wait <= 0:
                break
            time.sleep(wait)
        self._calls.append(time.time())


    def _check_response(self, response: requests.Response) -> dict:
        try:
            payload = response.json()
        except ValueError:
            response.raise_for_status()
            raise RuntimeError(f"Non-JSON response: {response.text[:200]}")

        if response.status_code >= 400 or not payload.get("is_success") \
                or payload.get("code") != "000000":
            raise RuntimeError(f"API error {response.status_code}: {payload}")
        return payload.get("data") or {}
    
    def upload_image(self, image_path: str | Path) -> str:
        image_path = Path(image_path)

        if not image_path.is_file():
            raise FileNotFoundError(image_path)
        
        mime = mimetypes.guess_type(image_path.name)[0] or "image/jpeg"
        self._throttle()
        with image_path.open("rb") as image:
            response = self.session.post(
                self.BASE_URL + self.UPLOAD_ENDPOINT,
                files={
                    "image_file": (
                        image_path.name,
                        image,
                        mime
                    )
                },
                timeout=self.timeout,
            )

        data = self._check_response(response)

        file_name = data.get("file_name")

        if not file_name:
            raise RuntimeError(
                "Upload succeeded but file_name is missing"
            )

        return file_name.strip()

    def recognize(self, file_name: str) -> dict[str, Any]:
        payload = {
            **self.ocr_config,
            "file_name": file_name,
        }
        self._throttle()
        response = self.session.post(
            self.BASE_URL + self.OCR_ENDPOINT,
            json=payload,
            timeout=self.timeout,
        )

        data = self._check_response(response)

        return {
            "text": data.get("result_ocr_text", []),
            "bbox": data.get("result_bbox", []),
            "result_file_name": data.get("result_file_name"),
            "raw_response": data,
        }

    def predict(self, image_path: str | Path) -> dict[str, Any]:
        image_path = Path(image_path)
        file_name = self.upload_image(image_path)

        # Conservative delay between API calls.
        # time.sleep(2.5)

        result = self.recognize(file_name)
        raw_text = result["text"]
         # API có thể trả về list các chuỗi
        if isinstance(raw_text, list):
            items = [
                {
                    "text": str(item).strip(),
                    "bbox": None,
                    "confidence": None,
                }
                for item in raw_text
                if str(item).strip()
            ]
            text = " ".join(item["text"] for item in items)

        else:
            text = str(raw_text or "").strip()
            items = [
                {
                    "text": text,
                    "bbox": None,
                    "confidence": None,
                }
            ] if text else []

        return {
            "engine": "kimhannom",
            "image": str(image_path),
            "text": text,
            "items": items,
            "raw": result["raw_response"],
        }
    
    
    def predict_cached(self, image_path, out_dir="02_ocr_tho/han", retries=3):
        image_path = Path(image_path)
        out = Path(out_dir) / f"{image_path.stem}.json"
        if out.exists():
            return json.loads(out.read_text(encoding="utf-8"))

        for attempt in range(retries):
            try:
                result = self.predict(image_path)
                break
            except (requests.RequestException, RuntimeError):
                if attempt == retries - 1:
                    raise
                time.sleep(5 * (attempt + 1))

        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        return result


@register_engine
class KimHanNomEngine(OcrEngine):
    """Adapter integrating the KimHanNom Sino-Nom cloud OCR service into the shared OcrEngine interface."""

    name = "kimhannom"
    max_workers = 1  # Rate-limited remote service

    def __init__(
        self,
        token: str | None = None,
        ocr_id: int = 1,
        lang_type: int = 1,
        reading_direction: int = 1,
        epitaph: int = 0,
        tag: str | None = None,
        timeout: int = 120,
    ):
        self.token = token
        self.client_kwargs = {
            "ocr_id": ocr_id,
            "lang_type": lang_type,
            "reading_direction": reading_direction,
            "epitaph": epitaph,
            "tag": tag,
            "timeout": timeout,
        }
        self._client: KimHanNomOCR | None = None

    def _get_client(self) -> KimHanNomOCR:
        if self._client is None:
            token = self.token or os.getenv("KIMHANNOM_TOKEN")
            if not token:
                raise RuntimeError(
                    "Missing KIMHANNOM_TOKEN environment variable for KimHanNom OCR"
                )
            self._client = KimHanNomOCR(token=token, **self.client_kwargs)
        return self._client

    @classmethod
    def describe(cls, lang: str) -> dict[str, Any]:
        """Provides static fingerprint and version metadata without executing network calls."""
        return {
            "name": cls.name,
            "version": "1.0",
            "api_endpoint": KimHanNomOCR.BASE_URL,
            "ocr_id": 1,
            "lang_type": 1,
            "reading_direction": 1,
            "epitaph": 0,
            "adapter_version": 1,
        }

    def recognize(
        self,
        image: Image.Image,
        *,
        lang: str,
        psm: int,
        dpi: int,
    ) -> OcrResult:
        """Executes KimHanNom recognition on image and returns standard OcrResult."""
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp_file:
            tmp_path = Path(tmp_file.name)

        try:
            image.save(tmp_path, format="PNG")
            client = self._get_client()
            pred = client.predict(tmp_path)
        except Exception as exc:
            raise RuntimeError(f"KimHanNom OCR inference failed: {exc}") from exc
        finally:
            if tmp_path.exists():
                try:
                    tmp_path.unlink()
                except OSError:
                    pass

        raw_resp = pred.get("raw") or {}
        raw_texts = raw_resp.get("result_ocr_text")
        raw_bboxes = raw_resp.get("result_bbox") or []

        lines: list[OcrLine] = []
        if isinstance(raw_texts, list):
            for idx, text_item in enumerate(raw_texts):
                t = str(text_item).strip()
                if not t:
                    continue
                bbox_tuple: tuple[int, int, int, int] | None = None
                if isinstance(raw_bboxes, list) and idx < len(raw_bboxes):
                    b = raw_bboxes[idx]
                    poly = None
                    if isinstance(b, (list, tuple)):
                        if len(b) == 4 and all(isinstance(v, (int, float)) for v in b):
                            bbox_tuple = (int(b[0]), int(b[1]), int(b[2]), int(b[3]))
                        elif len(b) >= 2 and isinstance(b[0], (list, tuple)):
                            poly = b[0]
                        elif len(b) >= 3 and all(isinstance(pt, (list, tuple)) and len(pt) >= 2 for pt in b):
                            poly = b
                    if poly and all(isinstance(pt, (list, tuple)) and len(pt) >= 2 for pt in poly):
                        xs = [float(pt[0]) for pt in poly]
                        ys = [float(pt[1]) for pt in poly]
                        min_x, max_x = min(xs), max(xs)
                        min_y, max_y = min(ys), max(ys)
                        if max_x > min_x and max_y > min_y:
                            bx0 = max(0, min(image.width - 1, int(round(min_x))))
                            by0 = max(0, min(image.height - 1, int(round(min_y))))
                            bx1 = max(bx0 + 1, min(image.width, int(round(max_x))))
                            by1 = max(by0 + 1, min(image.height, int(round(max_y))))
                            if bx0 < bx1 and by0 < by1:
                                bbox_tuple = (bx0, by0, bx1, by1)
                lines.append(OcrLine(text=t, bbox=bbox_tuple, confidence=None))
        elif isinstance(raw_texts, str) and raw_texts.strip():
            lines.append(OcrLine(text=raw_texts.strip(), bbox=None, confidence=None))
        elif pred.get("items"):
            for it in pred["items"]:
                t = str(it.get("text", "")).strip()
                if t:
                    lines.append(OcrLine(text=t, bbox=None, confidence=None))

        return OcrResult(engine=self.name, lines=lines, raw=raw_resp)
