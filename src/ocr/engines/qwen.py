"""Qwen2.5-VL OCR adapter (chạy cục bộ, 4-bit).

Chọn bằng ``--engines qwen``. Model cố định trong adapter; tham số vận hành chỉnh bằng biến môi trường
(đều nằm trong ``describe()`` nên đổi giá trị sẽ tự đổi khoá cache):

    QWEN_OCR_MODE          all (mặc định) | script
    QWEN_MAX_PIXELS_TOKENS số "token ảnh" tối đa mỗi dải, mặc định 1024 (≈ 1024*28*28 điểm ảnh)
    QWEN_BAND_INCHES       chiều cao mỗi dải theo inch ở DPI khai báo, mặc định 2.0
    QWEN_MAX_NEW_TOKENS    trần token sinh ra mỗi dải, mặc định 768

Vì sao viết lại (lỗi của bản trước, thấy trong log OCR trang 205):
  1. Cả trang 2480x2984 bị ép về ~300k điểm ảnh -> chữ Hán/Việt nhỏ không đọc nổi -> model "bịa" chữ
     (vd phần PHIÊN ÂM tiếng Việt bị sinh thành chữ Hán rồi lặp vô hạn "非有法門，度出群域，...").
  2. Prompt bắt model CHỈ chép một hệ chữ, nhưng trang có cả Hán lẫn Việt -> model vẫn lẫn chữ và bịa
     phần thuộc hệ chữ kia. Việc lọc theo chữ viết đã có ở ocr.py (--keep), nên mặc định chép MỌI chữ.
  3. Mọi dòng dùng chung bbox cả crop và confidence=None -> bước phân đoạn không có vị trí/độ tin cậy.
  4. describe() tham chiếu PROMPT_VERSION/prompt chưa định nghĩa (NameError).
  5. Mỗi hệ chữ nạp lại model và OCR lại cùng một ảnh.

Cách làm: cắt crop thành các DẢI ngang tại khoảng trắng giữa các dòng chữ, OCR từng dải ở độ phân giải
đủ lớn, ghép lại theo thứ tự. bbox mỗi dòng là chia đều phần mực của dải (xấp xỉ, ghi rõ trong raw).
Confidence = trung bình nhân xác suất token (thang 0..100, KHÔNG phải độ chính xác). Dải bị lặp vô hạn
hoặc bị cắt cụt được thu gọn/gắn cờ và bị hạ confidence để selector/voting không ưu tiên.
"""

from __future__ import annotations

import hashlib
import logging
import math
import os
import re
import threading
import time
from typing import Any

from PIL import Image

from src.ocr.engines import OcrEngine, OcrLine, OcrResult, register_engine

log = logging.getLogger("qwen")

MODEL_ID = "Qwen/Qwen2.5-VL-3B-Instruct"
MODEL_REVISION = "main"
ADAPTER_VERSION = 4                 # 4: mặt nạ mực cho trang thưa mực (CPO_20 tr.692)
PROMPT_VERSION = 3

# Cấu hình cho RTX 3050 6GB + 32GB RAM.
MIN_PIXELS = 64 * 28 * 28 
GPU_MEMORY_LIMIT = "4GiB"
CPU_MEMORY_LIMIT = "24GiB"
REPETITION_PENALTY = 1.05
MIN_BAND_SIDE = 56                      # Qwen2.5-VL cần cạnh >= 28; đệm trắng cho dải quá thấp
DEGENERATE_CONFIDENCE_CAP = 10.0
TRUNCATED_CONFIDENCE_CAP = 30.0

PROMPT_ALL = """You are a high-accuracy OCR system for historical Chinese and Vietnamese books.

Transcribe ALL printed text in the image exactly as it appears, in reading order.

Rules:
1. Keep Chinese text as Chinese characters, including variant forms. Never convert it to pinyin or Vietnamese.
2. Keep Vietnamese text in Latin script with all diacritics. Never translate it into Chinese.
3. Preserve punctuation, capitalization and spelling. Do not correct, modernize, paraphrase or complete anything.
4. Output one printed line per output line. Do not merge or split lines.
5. Do not repeat any line or phrase.
6. If a character is genuinely illegible write [UNCLEAR]. Never guess from context.
7. Return plain text only: no headings, explanations, translations or Markdown.
8. If the image contains no text, return an empty response."""

# Chế độ "script" (A/B với bản cũ): chỉ chép một hệ chữ. Dễ làm model bịa chữ khi trang lẫn hai hệ chữ.
PROMPT_CHI_TRA = """You are a high-accuracy OCR system for historical Chinese documents.

Transcribe ONLY the text written in Chinese characters (Hanzi) that is visibly printed in the image.
Ignore every line written in the Latin alphabet; do not translate or romanize it, and do not replace it
with Chinese. Preserve the original characters, punctuation and reading order, one printed line per output
line. Do not repeat any line or phrase. Use [UNCLEAR] for illegible characters. Return plain text only;
return an empty response if no Chinese text is visible."""

PROMPT_VIE = """You are a high-accuracy OCR system for historical Vietnamese documents.

Transcribe ONLY the text written in the Latin alphabet (Vietnamese) that is visibly printed in the image,
with all diacritics. Ignore every line written in Chinese characters; do not translate it. Preserve spelling,
capitalization, punctuation and reading order, one printed line per output line. Do not repeat any line or
phrase. Use [UNCLEAR] for illegible characters. Return plain text only; return an empty response if no
Vietnamese text is visible."""

_CJK_RANGE = "\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff"
_HAN_GAP_RE = re.compile(rf"(?<=[{_CJK_RANGE}]) +(?=[{_CJK_RANGE}])")
_LOOP_RE = re.compile(r"(.{4,80}?)\1{2,}", re.S)
_FENCE_RE = re.compile(r"^\s*```[a-zA-Z]*\s*$")


# --------------------------------------------------------------------------- #
# Cấu hình từ môi trường
# --------------------------------------------------------------------------- #
def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} phải là số nguyên, nhận {raw!r}") from exc
    if value < 1:
        raise ValueError(f"{name} phải >= 1")
    return value


def settings() -> dict[str, Any]:
    mode = os.environ.get("QWEN_OCR_MODE", "all").strip() or "all"
    if mode not in ("all", "script"):
        raise ValueError("QWEN_OCR_MODE phải là 'all' hoặc 'script'")
    band_inches = float(os.environ.get("QWEN_BAND_INCHES", "2.0") or 2.0)
    if not math.isfinite(band_inches) or band_inches <= 0:
        raise ValueError("QWEN_BAND_INCHES phải > 0")
    return {
        "mode": mode,
        "max_pixels": _env_int("QWEN_MAX_PIXELS_TOKENS", 1024) * 28 * 28,
        "band_inches": band_inches,
        "max_new_tokens": _env_int("QWEN_MAX_NEW_TOKENS", 768),
    }


def get_prompt(lang: str, mode: str) -> str:
    if mode == "all" or lang == "all":
        return PROMPT_ALL
    if lang == "chi_tra":
        return PROMPT_CHI_TRA
    if lang == "vie":
        return PROMPT_VIE
    return PROMPT_ALL


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


INK_PERCENTILES = (2.0, 98.0)
SPARSE_INK_PERCENTILE = 0.1             # trang thưa mực (< ~2% điểm mực): phân vị 2 vẫn là giấy trắng


def ink_mask(gray):
    """True ở điểm ảnh 'mực'. Ngưỡng thích nghi theo phân vị nên chịu được giấy ngả vàng.

    Trang thưa mực (vd 8 dòng thơ dịch, CPO_20 tr.692, ~1.4% điểm mực) có phân vị 2 = phân vị 98 = giấy;
    trước adapter 4 trang đó bị coi là trắng và Qwen không đọc dòng nào. Cùng cách sửa với paddlevl adapter 5."""
    import numpy as np
    lo, hi = np.percentile(gray, INK_PERCENTILES)
    if hi - lo < 30:
        lo = float(np.percentile(gray, SPARSE_INK_PERCENTILE))
    if hi - lo < 30:                      # ảnh trắng/không tương phản
        return np.zeros(gray.shape, dtype=bool)
    return gray < (lo + hi) / 2


def split_bands(mask, target_h: int, *, min_gap: int = 6, ink_frac: float = 0.002,
                max_overshoot: float = 1.5) -> list[tuple[int, int]]:
    """Trả về [(y0, y1)] phủ phần có mực; vết cắt đặt giữa các đoạn trắng >= min_gap hàng.

    Dải thường cao <= target_h. Nếu không có khoảng trắng nào trong tầm, cho phép dài tới
    max_overshoot*target_h rồi mới cắt cưỡng bức (khối chữ liền không kẽ hở).
    """
    import numpy as np
    h, w = mask.shape
    rows = mask.sum(axis=1)
    blank = rows <= max(1, int(ink_frac * w))
    content = np.flatnonzero(~blank)
    if content.size == 0:
        return []
    start, end = int(content[0]), int(content[-1]) + 1

    cuts: list[int] = []                  # tâm các đoạn trắng nằm giữa nội dung
    i = start
    while i < end:
        if blank[i]:
            j = i
            while j < end and blank[j]:
                j += 1
            if j - i >= min_gap:
                cuts.append((i + j) // 2)
            i = j
        else:
            i += 1

    bands: list[tuple[int, int]] = []
    min_h = max(1, target_h // 3)
    while end - start > target_h * max_overshoot:
        inside = [c for c in cuts if start + min_h < c <= start + target_h]
        if inside:
            cut = inside[-1]
        else:
            beyond = [c for c in cuts if start + target_h < c <= start + target_h * max_overshoot]
            cut = beyond[0] if beyond else start + target_h
        bands.append((start, cut))
        start = cut
    bands.append((start, end))
    return bands


def band_ink_box(mask, y0: int, y1: int, pad: int = 4) -> tuple[int, int, int, int] | None:
    """Hộp bao phần mực trong dải [y0, y1) (toạ độ crop), hoặc None nếu dải trắng."""
    import numpy as np
    sub = mask[y0:y1]
    ys, xs = np.nonzero(sub)
    if ys.size == 0:
        return None
    h, w = mask.shape
    return (max(0, int(xs.min()) - pad), max(0, y0 + int(ys.min()) - pad),
            min(w, int(xs.max()) + 1 + pad), min(h, y0 + int(ys.max()) + 1 + pad))


def clean_generated_text(text: str) -> tuple[list[str], list[str]]:
    """Trả về (các dòng, cờ). Cờ: han_gap_removed, repetition_collapsed, duplicate_lines_dropped, fence_removed."""
    flags: set[str] = set()
    lines_out: list[str] = []
    for raw in text.splitlines():
        if _FENCE_RE.match(raw):
            flags.add("fence_removed")
            continue
        line = raw.strip()
        if not line:
            continue
        fixed = _HAN_GAP_RE.sub("", line)           # Qwen hay chèn dấu cách giữa hai chữ Hán
        if fixed != line:
            flags.add("han_gap_removed")
            line = fixed
        collapsed = _LOOP_RE.sub(r"\1", line)       # vòng lặp "ABCDABCDABCD..." -> "ABCD"
        if collapsed != line:
            flags.add("repetition_collapsed")
            line = collapsed
        if len(line) >= 6 and line in lines_out[-3:]:
            flags.add("duplicate_lines_dropped")
            continue
        lines_out.append(line)
    return lines_out, sorted(flags)


def split_line_boxes(n_lines: int, ink: tuple[int, int, int, int]) -> list[tuple[int, int, int, int]]:
    """Chia đều chiều cao phần mực cho n dòng (xấp xỉ; Qwen không trả vị trí từng dòng)."""
    x0, y0, x1, y1 = ink
    height = y1 - y0
    if n_lines <= 0:
        return []
    if height < n_lines:                            # quá thấp để chia: mọi dòng dùng chung hộp
        return [ink] * n_lines
    edges = [y0 + round(height * i / n_lines) for i in range(n_lines + 1)]
    return [(x0, edges[i], x1, max(edges[i + 1], edges[i] + 1)) for i in range(n_lines)]


def band_confidence(logprobs: list[float], flags: list[str], truncated: bool) -> float | None:
    """Trung bình nhân xác suất token (0..100); bị hạ trần nếu dải lặp vô hạn hoặc cụt."""
    if not logprobs:
        return None
    conf = 100.0 * math.exp(sum(logprobs) / len(logprobs))
    conf = max(0.0, min(100.0, conf))
    if "repetition_collapsed" in flags or "duplicate_lines_dropped" in flags:
        conf = min(conf, DEGENERATE_CONFIDENCE_CAP)
    if truncated:
        conf = min(conf, TRUNCATED_CONFIDENCE_CAP)
    return round(conf, 2)


def pad_to_min_side(image: Image.Image) -> Image.Image:
    w, h = image.size
    if w >= MIN_BAND_SIDE and h >= MIN_BAND_SIDE:
        return image
    canvas = Image.new("RGB", (max(w, MIN_BAND_SIDE), max(h, MIN_BAND_SIDE)), "white")
    canvas.paste(image, (0, 0))
    return canvas


_MODEL_LOCK = threading.Lock()
_STATE: dict[str, Any] = {}
_MEMO: dict[str, dict] = {}                       # ảnh+tham số -> kết quả, tránh OCR lại ở hệ chữ thứ hai
_MEMO_LIMIT = 8


def _load_model(max_pixels: int) -> dict[str, Any]:
    with _MODEL_LOCK:
        if _STATE.get("max_pixels") == max_pixels and "model" in _STATE:
            return _STATE
        try:
            import torch
            from transformers import (AutoProcessor, BitsAndBytesConfig,
                                      Qwen2_5_VLForConditionalGeneration)
            from qwen_vl_utils import process_vision_info
        except ImportError as exc:
            raise RuntimeError("Không import được thư viện Qwen. Kiểm tra transformers, accelerate, "
                               "bitsandbytes và qwen-vl-utils.") from exc
        if not torch.cuda.is_available():
            raise RuntimeError("Qwen engine cần CUDA GPU.")

        log.info("Nạp processor %s (max_pixels=%d)", MODEL_ID, max_pixels)
        processor = AutoProcessor.from_pretrained(MODEL_ID, revision=MODEL_REVISION,
                                                  min_pixels=MIN_PIXELS, max_pixels=max_pixels)
        if "model" not in _STATE:
            quant = BitsAndBytesConfig(load_in_4bit=True, bnb_4bit_quant_type="nf4",
                                       bnb_4bit_compute_dtype=torch.float16,
                                       bnb_4bit_use_double_quant=True)
            kwargs = dict(revision=MODEL_REVISION, quantization_config=quant, device_map="auto",
                          max_memory={0: GPU_MEMORY_LIMIT, "cpu": CPU_MEMORY_LIMIT},
                          attn_implementation="sdpa", low_cpu_mem_usage=True)
            log.info("Nạp model 4-bit (một lần cho cả tiến trình)")
            try:
                model = Qwen2_5_VLForConditionalGeneration.from_pretrained(
                    MODEL_ID, dtype=torch.float16, **kwargs)
            except TypeError:                         # transformers cũ chưa có tham số dtype
                model = Qwen2_5_VLForConditionalGeneration.from_pretrained(
                    MODEL_ID, torch_dtype=torch.float16, **kwargs)
            model.eval()
            for name in ("temperature", "top_p", "top_k"):   # tắt cảnh báo cờ sampling khi greedy
                setattr(model.generation_config, name, None)
            _STATE["model"] = model
            log.info("Model sẵn sàng. device_map=%s", model.hf_device_map)
        _STATE.update(max_pixels=max_pixels, processor=processor, torch=torch,
                      process_vision_info=process_vision_info)
        return _STATE


def _generate(image: Image.Image, prompt: str, *, max_new_tokens: int, max_pixels: int
              ) -> tuple[str, int, list[float]]:
    """Một lần suy luận trên một dải. Trả về (text, số token sinh, logprob từng token)."""
    st = _load_model(max_pixels)
    torch, processor, model = st["torch"], st["processor"], st["model"]
    from transformers import LogitsProcessor, LogitsProcessorList

    class Recorder(LogitsProcessor):                  # greedy: token chọn = argmax -> logprob = max log_softmax
        def __init__(self):
            self.logprobs: list[float] = []

        def __call__(self, input_ids, scores):
            self.logprobs.append(float(torch.log_softmax(scores.float(), dim=-1).max(dim=-1).values[0]))
            return scores

    messages = [{"role": "user", "content": [{"type": "image", "image": image},
                                             {"type": "text", "text": prompt}]}]
    prompt_text = processor.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)
    image_inputs, video_inputs = st["process_vision_info"](messages)
    inputs = processor(text=[prompt_text], images=image_inputs, videos=video_inputs,
                       padding=True, return_tensors="pt")
    inputs = inputs.to(model.get_input_embeddings().weight.device)
    recorder = Recorder()
    with torch.inference_mode():
        generated = model.generate(**inputs, max_new_tokens=max_new_tokens, do_sample=False,
                                   repetition_penalty=REPETITION_PENALTY,
                                   logits_processor=LogitsProcessorList([recorder]))
    trimmed = [out[len(inp):] for inp, out in zip(inputs.input_ids, generated)]
    text = processor.batch_decode(trimmed, skip_special_tokens=True,
                                  clean_up_tokenization_spaces=False)[0].strip()
    count = int(trimmed[0].shape[-1])
    del inputs, generated, trimmed
    torch.cuda.empty_cache()
    return text, count, recorder.logprobs

@register_engine
class QwenEngine(OcrEngine):
    """Qwen2.5-VL-3B-Instruct, OCR theo dải ngang, nạp model một lần cho cả tiến trình."""

    name = "qwen"
    max_workers = 1

    @classmethod
    def describe(cls, lang: str) -> dict[str, Any]:
        """Kiểm tra phụ thuộc, KHÔNG nạp model. Mọi tham số ảnh hưởng kết quả phải nằm ở đây (khoá cache)."""
        try:
            import torch
            import transformers
            import accelerate  # noqa: F401
            import bitsandbytes
            from qwen_vl_utils import process_vision_info  # noqa: F401
        except ImportError as exc:
            raise RuntimeError("Qwen engine thiếu dependency. Cài bằng:\n"
                               "pip install torch transformers accelerate bitsandbytes qwen-vl-utils") from exc
        if not torch.cuda.is_available():
            raise RuntimeError("Qwen engine hiện yêu cầu CUDA GPU. "
                               "Hãy kiểm tra PyTorch CUDA và NVIDIA driver.")
        cfg = settings()
        prompt = get_prompt(lang, cfg["mode"])
        return {
            "name": cls.name, "model_id": MODEL_ID, "model_revision": MODEL_REVISION,
            "transformers_version": transformers.__version__, "torch_version": torch.__version__,
            "cuda_version": torch.version.cuda, "bitsandbytes_version": bitsandbytes.__version__,
            "quantization": {"load_in_4bit": True, "quant_type": "nf4", "double_quant": True,
                             "compute_dtype": "float16"},
            "mode": cfg["mode"], "min_pixels": MIN_PIXELS, "max_pixels": cfg["max_pixels"],
            "band_inches": cfg["band_inches"], "max_new_tokens": cfg["max_new_tokens"],
            "repetition_penalty": REPETITION_PENALTY,
            "adapter_version": ADAPTER_VERSION, "prompt_version": PROMPT_VERSION,
            "prompt_sha256": _sha(prompt),
        }

    def recognize(self, image: Image.Image, *, lang: str, psm: int, dpi: int) -> OcrResult:
        del psm                                       # Qwen không dùng PSM của Tesseract
        cfg = settings()
        prompt = get_prompt(lang, cfg["mode"])
        image = image.convert("RGB")
        width, height = image.size
        if width <= 0 or height <= 0:
            raise ValueError("Ảnh đầu vào rỗng.")

        # chế độ "all": mọi hệ chữ dùng chung một lần chép -> chỉ suy luận một lần cho mỗi ảnh
        memo_key = _sha("|".join([hashlib.sha256(image.tobytes()).hexdigest(), str(image.size), cfg["mode"],
                                  prompt if cfg["mode"] == "script" else "all", str(dpi),
                                  str(cfg["max_pixels"]), str(cfg["band_inches"]),
                                  str(cfg["max_new_tokens"]), str(ADAPTER_VERSION)]))
        hit = _MEMO.get(memo_key)
        if hit is not None:
            log.info("Dùng lại kết quả Qwen đã chép cho ảnh này (%d dòng)", len(hit["lines"]))
            return self._result(hit, lang)

        import numpy as np
        mask = ink_mask(np.asarray(image.convert("L")))
        target_h = max(MIN_BAND_SIDE * 2, int(round(dpi * cfg["band_inches"])))
        spans = split_bands(mask, target_h)

        lines: list[OcrLine] = []
        bands_raw: list[dict] = []
        started = time.perf_counter()
        for index, (y0, y1) in enumerate(spans, 1):
            ink = band_ink_box(mask, y0, y1)
            if ink is None:
                continue
            crop = pad_to_min_side(image.crop(ink))
            t0 = time.perf_counter()
            text, tokens, logprobs = _generate(crop, prompt, max_new_tokens=cfg["max_new_tokens"],
                                               max_pixels=cfg["max_pixels"])
            truncated = tokens >= cfg["max_new_tokens"]
            texts, flags = clean_generated_text(text)
            if truncated:
                flags.append("truncated")
            conf = band_confidence(logprobs, flags, truncated)
            for line_text, box in zip(texts, split_line_boxes(len(texts), ink)):
                lines.append(OcrLine(line_text, tuple(int(v) for v in box), conf))
            bands_raw.append({"band": index, "y0": y0, "y1": y1, "ink_box": list(ink),
                              "generated_text": text, "n_lines": len(texts), "tokens": tokens,
                              "seconds": round(time.perf_counter() - t0, 3), "confidence": conf,
                              "flags": flags})
            if flags:
                log.warning("Dải %d/%d có cờ %s", index, len(spans), flags)

        elapsed = time.perf_counter() - started
        log.info("Qwen: %d dải, %d dòng, %.1fs", len(bands_raw), len(lines), elapsed)
        entry = {
            "lines": [(x.text, x.bbox, x.confidence) for x in lines],
            "raw": {"model_id": MODEL_ID, "model_revision": MODEL_REVISION, "mode": cfg["mode"],
                    "prompt_sha256": _sha(prompt), "dpi": dpi, "image_size": [width, height],
                    "band_target_height": target_h, "bbox_granularity": "band_even_split",
                    "max_new_tokens": cfg["max_new_tokens"], "max_pixels": cfg["max_pixels"],
                    "inference_seconds": round(elapsed, 3), "bands": bands_raw,
                    "any_degenerate": any("repetition_collapsed" in b["flags"] for b in bands_raw),
                    "any_truncated": any("truncated" in b["flags"] for b in bands_raw)},
        }
        if len(_MEMO) >= _MEMO_LIMIT:
            _MEMO.pop(next(iter(_MEMO)))
        _MEMO[memo_key] = entry
        return self._result(entry, lang)

    def _result(self, entry: dict, lang: str) -> OcrResult:
        lines = [OcrLine(t, tuple(b), c) for t, b, c in entry["lines"]]
        return OcrResult(self.name, lines, {**entry["raw"], "lang": lang})
