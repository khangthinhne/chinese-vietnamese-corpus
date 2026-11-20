"""Qwen2.5-VL OCR adapter.

The model is intentionally fixed in this adapter. The OCR orchestrator selects
this engine with ``--engines qwen``; no model ID CLI argument is required.

The orchestrator passes an already-cropped PIL image to ``recognize``.
Qwen currently returns page/region-level text rather than line-level boxes,
so this adapter emits one OcrLine whose bbox covers the supplied crop.
"""

from __future__ import annotations

import time
from typing import Any

from PIL import Image

from src.ocr.engines import OcrEngine, OcrLine, OcrResult, register_engine


MODEL_ID = "Qwen/Qwen2.5-VL-3B-Instruct"
MODEL_REVISION = "main"

# Conservative settings for RTX 3050 6GB VRAM + 32GB system RAM.
MIN_PIXELS = 256 * 28 * 28
MAX_PIXELS = 384 * 28 * 28
MAX_NEW_TOKENS = 1024
GPU_MEMORY_LIMIT = "4GiB"
CPU_MEMORY_LIMIT = "24GiB"

OCR_PROMPT = """
You are an OCR engine specialized in historical Chinese literature.

Transcribe the Chinese text visible in this image.

Rules:
1. Transcribe the original Chinese characters (Hanzi) only.
2. Do not transcribe Vietnamese pronunciation, Vietnamese translation,
   Latin text, page headers, or unrelated notes.
3. Preserve the original characters, punctuation, and reading order.
4. Preserve paragraph and line boundaries where possible.
5. Do not translate, explain, modernize, or normalize historical characters.
6. Do not guess illegible characters. Use [UNCLEAR].
7. Ignore faint bleed-through, stains, handwritten marks, and page numbers
   unless they are clearly part of the original printed Chinese text.

Return only the transcription. Do not add explanations.
"""


@register_engine
class QwenEngine(OcrEngine):
    """Qwen2.5-VL-3B-Instruct, loaded lazily and reused across pages."""

    name = "qwen"
    max_workers = 1

    def __init__(self) -> None:
        self._torch = None
        self._processor = None
        self._model = None
        self._process_vision_info = None

    @classmethod
    def describe(cls, lang: str) -> dict[str, Any]:
        """Validate dependencies without loading the 3B model into memory."""
        try:
            import torch
            import transformers
            import accelerate
            import bitsandbytes
            from qwen_vl_utils import process_vision_info  # noqa: F401
        except ImportError as exc:
            raise RuntimeError(
                "Qwen engine thiếu dependency. Cài bằng:\n"
                "pip install torch transformers accelerate bitsandbytes "
                "qwen-vl-utils"
            ) from exc

        if not torch.cuda.is_available():
            raise RuntimeError(
                "Qwen engine hiện yêu cầu CUDA GPU. "
                "Hãy kiểm tra PyTorch CUDA và NVIDIA driver."
            )

        return {
            "name": cls.name,
            "model_id": MODEL_ID,
            "model_revision": MODEL_REVISION,
            "transformers_version": transformers.__version__,
            "torch_version": torch.__version__,
            "cuda_version": torch.version.cuda,
            "bitsandbytes_version": bitsandbytes.__version__,
            "quantization": {
                "load_in_4bit": True,
                "quant_type": "nf4",
                "double_quant": True,
                "compute_dtype": "float16",
            },
            "min_pixels": MIN_PIXELS,
            "max_pixels": MAX_PIXELS,
            "max_new_tokens": MAX_NEW_TOKENS,
            "adapter_version": 1,
        }

    def _load_model(self) -> None:
        """Load processor/model once per engine instance."""
        if self._model is not None:
            return

        try:
            import torch
            from transformers import (
                AutoProcessor,
                BitsAndBytesConfig,
                Qwen2_5_VLForConditionalGeneration,
            )
            from qwen_vl_utils import process_vision_info
        except ImportError as exc:
            raise RuntimeError(
                "Không import được thư viện Qwen. "
                "Kiểm tra transformers, accelerate, bitsandbytes "
                "và qwen-vl-utils."
            ) from exc

        if not torch.cuda.is_available():
            raise RuntimeError("Qwen engine cần CUDA GPU.")

        print(f"[{self.name}] Loading processor: {MODEL_ID}")
        processor = AutoProcessor.from_pretrained(
            MODEL_ID,
            revision=MODEL_REVISION,
            min_pixels=MIN_PIXELS,
            max_pixels=MAX_PIXELS,
        )

        quant_config = BitsAndBytesConfig(
            load_in_4bit=True,
            bnb_4bit_quant_type="nf4",
            bnb_4bit_compute_dtype=torch.float16,
            bnb_4bit_use_double_quant=True,
        )

        print(f"[{self.name}] Loading 4-bit model...")
        model = Qwen2_5_VLForConditionalGeneration.from_pretrained(
            MODEL_ID,
            revision=MODEL_REVISION,
            quantization_config=quant_config,
            device_map="auto",
            max_memory={
                0: GPU_MEMORY_LIMIT,
                "cpu": CPU_MEMORY_LIMIT,
            },
            dtype=torch.float16,
            attn_implementation="sdpa",
            low_cpu_mem_usage=True,
        )
        model.eval()

        self._torch = torch
        self._processor = processor
        self._model = model
        self._process_vision_info = process_vision_info

        print(f"[{self.name}] Model ready. Device map: {model.hf_device_map}")

    def recognize(
        self,
        image: Image.Image,
        *,
        lang: str,
        psm: int,
        dpi: int,
    ) -> OcrResult:
        """Recognize Hanzi in one already-cropped page region."""
        del psm  # Qwen does not use Tesseract's page segmentation modes.

        self._load_model()

        torch = self._torch
        processor = self._processor
        model = self._model

        image = image.convert("RGB")
        width, height = image.size
        if width <= 0 or height <= 0:
            raise ValueError("Ảnh đầu vào rỗng.")

        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "image", "image": image},
                    {"type": "text", "text": OCR_PROMPT},
                ],
            }
        ]

        prompt_text = processor.apply_chat_template(
            messages,
            tokenize=False,
            add_generation_prompt=True,
        )
        image_inputs, video_inputs = self._process_vision_info(messages)

        inputs = processor(
            text=[prompt_text],
            images=image_inputs,
            videos=video_inputs,
            padding=True,
            return_tensors="pt",
        )

        # For device_map="auto", send inputs to the input embedding device.
        input_device = model.get_input_embeddings().weight.device
        inputs = inputs.to(input_device)

        started = time.perf_counter()
        with torch.inference_mode():
            generated_ids = model.generate(
                **inputs,
                max_new_tokens=MAX_NEW_TOKENS,
                do_sample=False,
            )
        if torch.cuda.is_available():
            torch.cuda.synchronize()
        elapsed = time.perf_counter() - started

        generated_ids_trimmed = [
            output_ids[len(input_ids):]
            for input_ids, output_ids in zip(inputs.input_ids, generated_ids)
        ]
        text = processor.batch_decode(
            generated_ids_trimmed,
            skip_special_tokens=True,
            clean_up_tokenization_spaces=False,
        )[0].strip()

        # Qwen generates a region-level transcription, not word/line boxes.
        lines = []
        if text:
            lines.append(
                OcrLine(
                    text=text,
                    bbox=(0, 0, width, height),
                    confidence=None,
                )
            )

        raw = {
            "model_id": MODEL_ID,
            "model_revision": MODEL_REVISION,
            "lang": lang,
            "dpi": dpi,
            "image_size": [width, height],
            "prompt": OCR_PROMPT,
            "generated_text": text,
            "inference_seconds": round(elapsed, 4),
            "max_new_tokens": MAX_NEW_TOKENS,
            "quantization": "4bit-nf4",
            "bbox_granularity": "whole_crop",
        }

        # Release per-page tensors. Keep model/processor loaded for next page.
        del inputs, generated_ids, generated_ids_trimmed
        if torch.cuda.is_available():
            torch.cuda.empty_cache()

        print(f"[qwen] Generated text length: {len(text)}")
        print(f"[qwen] Generated text preview: {text[:500]!r}")
        print(f"[qwen] Image size: {width}x{height}")

        return OcrResult(
            engine=self.name,
            lines=lines,
            raw=raw,
        )
