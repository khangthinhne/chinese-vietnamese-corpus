"""PaddleOCR-VL-1.6 OCR adapter (0.9B, chạy cục bộ bằng transformers).

Chọn bằng ``--engines paddlevl``. Tham số vận hành chỉnh bằng biến môi trường (đều nằm trong
``describe()`` nên đổi giá trị sẽ tự đổi khoá cache):

    PADDLEVL_ORIENTATION       auto (mặc định) | horizontal | vertical
                               horizontal: tách TỪNG DÒNG ngang, mỗi dòng một lần chép (chữ Hán in ngang, đọc trái sang phải)
                               vertical  : tách từng CỘT (chữ Hán in dọc), đọc phải sang trái, mỗi cột = một dòng
                               auto      : đoán theo khoảng trắng của trang (nên đặt tay nếu biết trước)
    PADDLEVL_DTYPE             auto (mặc định) | bfloat16 | float16 | float32
                               auto = bf16 nếu GPU có compute capability >= 8 (RTX 3050), còn lại fp32 (T4)
    PADDLEVL_MAX_PIXELS_TOKENS số "token ảnh" tối đa mỗi lần chép, mặc định 1280 (theo README của model)
    PADDLEVL_BAND_INCHES       chiều cao mỗi dải (chế độ ngang), mặc định 2.0
    PADDLEVL_MAX_NEW_TOKENS    trần token sinh ra mỗi lần chép, mặc định 512

Cài đặt (nên dùng môi trường riêng; adapter Qwen đang dùng transformers 4.x):
    pip install "transformers>=5.0.0" torch pillow numpy

Giới hạn cần biết:
  * Đây là đường "element-level" của model (prompt ``OCR:``), KHÔNG có phân tích bố cục. Trang trộn cả khối
    chữ dọc lẫn chữ ngang (vd chú thích tiếng Việt dưới trang) sẽ bị cắt theo MỘT hướng. Đường pipeline chính
    thức của PaddleOCR (có layout + thứ tự đọc) theo tài liệu cần GPU compute capability cao (>= 8.5) và
    Windows phải qua WSL/Docker, nên không dùng ở đây.
  * Confidence = trung bình nhân xác suất token (0..100, KHÔNG phải độ chính xác), cùng quy ước adapter Qwen.
    Dải/cột bị lặp được dừng sớm, gắn cờ và bị hạ confidence.
  * bbox: chế độ ngang là chia đều phần mực của dải (xấp xỉ); chế độ dọc là hộp mực thật của từng cột.
"""

from __future__ import annotations

import hashlib
import inspect
import logging
import math
import os
import re
import threading
import time
from typing import Any, Sequence

from PIL import Image

from src.ocr.engines import OcrEngine, OcrLine, OcrResult, register_engine

log = logging.getLogger("paddlevl")

MODEL_ID = "PaddlePaddle/PaddleOCR-VL-1.6"
MODEL_REVISION = "main"
ADAPTER_VERSION = 4
PROMPT = "OCR:"
MIN_PIXELS = 384 * 384                  # mặc định của image processor (transformers 5 không còn thuộc tính min_pixels)
COL_REL_THRESHOLD = 0.05                # cột "trắng" nếu mực <= 5% mực cột chữ điển hình (bỏ qua hạt nhiễu của bản quét)
MIN_UNIT_INK_REL = 0.08                 # bỏ đơn vị có mực < 8% phân vị 75 (hạt nhiễu, dấu câu lẻ); đủ thấp để giữ cột cuối bài ngắn
MAX_ASPECT = 100                        # smart_resize báo lỗi nếu tỉ lệ cạnh > 200; đệm trắng để an toàn

MIN_BAND_SIDE = 56                      # đệm trắng cho crop quá nhỏ
MIN_SPAN_PX = 14                        # cột hẹp hơn mức này coi là nhiễu/đường kẻ viền
DEGENERATE_CONFIDENCE_CAP = 10.0
TRUNCATED_CONFIDENCE_CAP = 30.0
LOOP_CHECK_EVERY = 8                    # kiểm tra vòng lặp mỗi N token sinh
LOOP_MIN_TOKENS = 24

_CJK_RANGE = "\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff"
_HAN_GAP_RE = re.compile(rf"(?<=[{_CJK_RANGE}]) +(?=[{_CJK_RANGE}])")
_LOOP_RE = re.compile(r"(.{6,80}?)\1{3,}", re.S)
_FENCE_RE = re.compile(r"^\s*```[a-zA-Z]*\s*$")
MAX_IDENTICAL_RUN = 2                   # giữ tối đa N dòng giống hệt liên tiếp


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
    orientation = os.environ.get("PADDLEVL_ORIENTATION", "auto").strip().lower() or "auto"
    if orientation not in ("auto", "horizontal", "vertical"):
        raise ValueError("PADDLEVL_ORIENTATION phải là auto, horizontal hoặc vertical")
    dtype = os.environ.get("PADDLEVL_DTYPE", "auto").strip().lower() or "auto"
    if dtype not in ("auto", "bfloat16", "float16", "float32"):
        raise ValueError("PADDLEVL_DTYPE phải là auto, bfloat16, float16 hoặc float32")
    try:
        band_inches = float(os.environ.get("PADDLEVL_BAND_INCHES", "2.0") or 2.0)
    except ValueError as exc:
        raise ValueError("PADDLEVL_BAND_INCHES phải là số") from exc
    if not math.isfinite(band_inches) or band_inches <= 0:
        raise ValueError("PADDLEVL_BAND_INCHES phải > 0")
    return {
        "orientation": orientation,
        "dtype": dtype,
        "max_pixels": _env_int("PADDLEVL_MAX_PIXELS_TOKENS", 1280) * 28 * 28,
        "band_inches": band_inches,
        "max_new_tokens": _env_int("PADDLEVL_MAX_NEW_TOKENS", 512),
    }


def _dtype_name(torch, requested: str) -> str:
    """auto -> bfloat16 nếu compute capability >= 8; ngược lại float32 (T4 không có bf16 thật, fp16 dễ tràn số;
    model chỉ ~1B tham số nên fp32 vẫn vừa)."""
    if requested != "auto":
        return requested
    major, _ = torch.cuda.get_device_capability(0)
    return "bfloat16" if major >= 8 else "float32"


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# --------------------------------------------------------------------------- #
# Hình học trang: mặt nạ mực, dải ngang, cột dọc, đoán hướng
# --------------------------------------------------------------------------- #
def ink_mask(gray):
    """True ở điểm ảnh 'mực'. Ngưỡng thích nghi theo phân vị nên chịu được giấy ngả vàng."""
    import numpy as np
    lo, hi = np.percentile(gray, [2, 98])
    if hi - lo < 30:
        return np.zeros(gray.shape, dtype=bool)
    return gray < (lo + hi) / 2


def strip_rules(mask, frac: float = 0.8):
    """Xoá đường kẻ viền/khung và viền đen của bản quét: cột (hàng) có > frac chiều cao (rộng) là mực.
    Chữ thật không bao giờ đặc đến mức đó; nếu không xoá, khung sách cổ làm sai cả việc đoán hướng lẫn tách cột."""
    m = mask.copy()
    h, w = m.shape
    m[:, m.sum(axis=0) > frac * h] = False
    m[m.sum(axis=1) > frac * w, :] = False
    return m


def split_bands(mask, target_h: int, *, min_gap: int = 6, ink_frac: float = 0.002,
                max_overshoot: float = 1.5) -> list[tuple[int, int]]:
    """[(y0, y1)] phủ phần có mực; vết cắt đặt giữa các đoạn trắng >= min_gap hàng (cùng logic adapter Qwen)."""
    import numpy as np
    h, w = mask.shape
    rows = mask.sum(axis=1)
    blank = rows <= max(1, int(ink_frac * w))
    content = np.flatnonzero(~blank)
    if content.size == 0:
        return []
    start, end = int(content[0]), int(content[-1]) + 1
    cuts: list[int] = []
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


def split_rows(mask, *, min_gap: int = 4, ink_frac: float = 0.002, rel: float = COL_REL_THRESHOLD * 2,
               tall: float = 1.6) -> list[tuple[int, int]]:
    """[(y0, y1)] từng DÒNG chữ ngang (trên -> dưới). Ngưỡng "trắng" theo mật độ tương đối như split_columns.

    Dải cao hơn `tall` lần chiều cao dòng điển hình (hai dòng dính nhau do nhiễu/chữ lem) được chẻ lại tại
    chỗ ít mực nhất gần vị trí kỳ vọng."""
    import numpy as np
    h, w = mask.shape
    prof = mask.sum(axis=1)
    pos = prof[prof > 0]
    if pos.size == 0:
        return []
    blank = prof <= max(1, int(ink_frac * w), int(rel * float(np.percentile(pos, 95))))
    idx = np.flatnonzero(~blank)
    spans: list[tuple[int, int]] = []
    start = prev = int(idx[0])
    for y in idx[1:]:
        y = int(y)
        if y - prev - 1 >= min_gap:
            spans.append((start, prev + 1))
            start = y
        prev = y
    spans.append((start, prev + 1))
    heights = sorted(b - a for a, b in spans if b - a >= MIN_SPAN_PX)
    if not heights:
        return spans
    med = heights[len(heights) // 2]
    out: list[tuple[int, int]] = []
    for a, b in spans:
        n = round((b - a) / med)
        if (b - a) > tall * med and n >= 2:
            cuts = [a]
            for k in range(1, n):
                exp = a + round((b - a) * k / n)
                lo, hi = max(cuts[-1] + 1, exp - med // 3), min(b - 1, exp + med // 3)
                cuts.append(lo + int(np.argmin(prof[lo:hi + 1])) if hi >= lo else exp)
            cuts.append(b)
            out.extend(zip(cuts[:-1], cuts[1:]))
        else:
            out.append((a, b))
    return out


def split_columns(mask, *, min_gap: int = 6, ink_frac: float = 0.002, rel: float = COL_REL_THRESHOLD,
                  min_width: int = MIN_SPAN_PX) -> list[tuple[int, int]]:
    """[(x0, x1)] các cột có mực, thứ tự PHẢI -> TRÁI (đọc chữ Hán dọc). Cột hẹp hơn min_width bị bỏ.

    Ngưỡng "trắng" theo MẬT ĐỘ tương đối (rel * phân vị 95 của mực mỗi cột) thay vì vài điểm ảnh: bản quét
    có hạt nhiễu lấp kẽ giữa các cột, ngưỡng tuyệt đối làm nhiều cột dính thành một."""
    import numpy as np
    h, _ = mask.shape
    prof = mask.sum(axis=0)
    pos = prof[prof > 0]
    blank = prof <= max(1, int(ink_frac * h), int(rel * float(np.percentile(pos, 95))) if pos.size else 0)
    idx = np.flatnonzero(~blank)
    if idx.size == 0:
        return []
    spans: list[tuple[int, int]] = []
    start = prev = int(idx[0])
    for x in idx[1:]:
        x = int(x)
        if x - prev - 1 >= min_gap:
            spans.append((start, prev + 1))
            start = x
        prev = x
    spans.append((start, prev + 1))
    return [s for s in spans if s[1] - s[0] >= min_width][::-1]


def ink_box(mask, x0: int, y0: int, x1: int, y1: int, pad: int = 4) -> tuple[int, int, int, int] | None:
    """Hộp bao phần mực trong vùng [x0:x1, y0:y1) (toạ độ trang), hoặc None nếu vùng trắng."""
    import numpy as np
    ys, xs = np.nonzero(mask[y0:y1, x0:x1])
    if ys.size == 0:
        return None
    h, w = mask.shape
    return (max(0, x0 + int(xs.min()) - pad), max(0, y0 + int(ys.min()) - pad),
            min(w, x0 + int(xs.max()) + 1 + pad), min(h, y0 + int(ys.max()) + 1 + pad))


def detect_orientation(mask, *, margin: float = 1.4, min_blank: float = 0.10) -> str:
    """Đoán hướng chữ: chữ ngang có nhiều hàng trắng liền (kẽ giữa các dòng), chữ dọc có nhiều cột trắng liền.

    So tỷ lệ hàng trắng với tỷ lệ cột trắng trong hộp mực của trang. Không chắc thì trả "horizontal"."""
    import numpy as np
    ys, xs = np.nonzero(mask)
    if ys.size == 0:
        return "horizontal"
    sub = mask[ys.min():ys.max() + 1, xs.min():xs.max() + 1]
    h, w = sub.shape
    row_blank = float((sub.sum(axis=1) <= max(1, int(0.002 * w))).mean())
    col_blank = float((sub.sum(axis=0) <= max(1, int(0.002 * h))).mean())
    if col_blank >= min_blank and col_blank > row_blank * margin:
        return "vertical"
    return "horizontal"


def split_line_boxes(n_lines: int, ink: tuple[int, int, int, int]) -> list[tuple[int, int, int, int]]:
    """Chia đều chiều cao phần mực cho n dòng (xấp xỉ; model không trả vị trí từng dòng)."""
    x0, y0, x1, y1 = ink
    height = y1 - y0
    if n_lines <= 0:
        return []
    if height < n_lines:
        return [ink] * n_lines
    edges = [y0 + round(height * i / n_lines) for i in range(n_lines + 1)]
    return [(x0, edges[i], x1, max(edges[i + 1], edges[i] + 1)) for i in range(n_lines)]


def pad_to_min_side(image: Image.Image) -> Image.Image:
    w, h = image.size
    nw, nh = max(w, MIN_BAND_SIDE), max(h, MIN_BAND_SIDE)
    nw, nh = max(nw, math.ceil(nh / MAX_ASPECT)), max(nh, math.ceil(nw / MAX_ASPECT))
    if (nw, nh) == (w, h):
        return image
    canvas = Image.new("RGB", (nw, nh), "white")
    canvas.paste(image, (0, 0))
    return canvas


# --------------------------------------------------------------------------- #
# Chống lặp và làm sạch văn bản sinh ra
# --------------------------------------------------------------------------- #
def is_looping(ids: Sequence[int], *, window: int = 48, max_unique: int = 3, max_period: int = 32,
               min_span: int = 16, min_repeats: int = 4) -> bool:
    """True nếu đuôi chuỗi token đang lặp: (a) `window` token cuối chỉ có <= max_unique giá trị khác nhau, hoặc
    (b) một cụm dài p <= max_period token lặp liền nhau >= max(min_repeats, min_span/p) lần."""
    ids = list(ids[-(max_period * min_repeats):]) if len(ids) > max_period * min_repeats else list(ids)
    if len(ids) >= window and len(set(ids[-window:])) <= max_unique:
        return True
    for p in range(1, max_period + 1):
        need = p * max(min_repeats, -(-min_span // p))
        if len(ids) < need:
            continue
        seg = ids[-need:]
        if all(seg[i] == seg[i - p] for i in range(p, need)):
            return True
    return False


def _collapse_loop(match: re.Match) -> str:
    """Thu gọn vòng lặp trong một dòng; giữ nguyên nếu đơn vị lặp toàn dấu (dấu chấm dẫn mục lục...)."""
    unit = match.group(1)
    return match.group(0) if not re.search(r"\w", unit) else unit


def clean_generated_text(text: str) -> tuple[list[str], list[str]]:
    """Trả về (các dòng, cờ). Cờ: han_gap_removed, repetition_collapsed, duplicate_lines_dropped, fence_removed."""
    flags: set[str] = set()
    lines_out: list[str] = []
    run = 0
    for raw in text.splitlines():
        if _FENCE_RE.match(raw):
            flags.add("fence_removed")
            continue
        line = raw.strip()
        if not line:
            continue
        fixed = _HAN_GAP_RE.sub("", line)
        if fixed != line:
            flags.add("han_gap_removed")
            line = fixed
        collapsed = _LOOP_RE.sub(_collapse_loop, line)
        if collapsed != line:
            flags.add("repetition_collapsed")
            line = collapsed
        # dòng giống hệt dòng trước (mọi độ dài, kể cả 1 chữ): chỉ giữ MAX_IDENTICAL_RUN dòng liên tiếp
        if lines_out and line == lines_out[-1]:
            run += 1
            if run >= MAX_IDENTICAL_RUN:
                flags.add("duplicate_lines_dropped")
                continue
        else:
            run = 0
        if len(line) >= 6 and line in lines_out[-3:]:
            flags.add("duplicate_lines_dropped")
            continue
        lines_out.append(line)
    return lines_out, sorted(flags)


def band_confidence(logprobs: list[float], flags: list[str], truncated: bool) -> float | None:
    """Trung bình nhân xác suất token (0..100); bị hạ trần nếu bị lặp hoặc cụt."""
    if not logprobs:
        return None
    conf = 100.0 * math.exp(sum(logprobs) / len(logprobs))
    conf = max(0.0, min(100.0, conf))
    if {"repetition_collapsed", "duplicate_lines_dropped", "loop_stopped"} & set(flags):
        conf = min(conf, DEGENERATE_CONFIDENCE_CAP)
    if truncated:
        conf = min(conf, TRUNCATED_CONFIDENCE_CAP)
    return round(conf, 2)


# --------------------------------------------------------------------------- #
# Model
# --------------------------------------------------------------------------- #
_MODEL_LOCK = threading.Lock()
_STATE: dict[str, Any] = {}
_MEMO: dict[str, dict] = {}
_MEMO_LIMIT = 8


def _load_model() -> dict[str, Any]:
    with _MODEL_LOCK:
        if "model" in _STATE:
            return _STATE
        try:
            import torch
            from transformers import AutoModelForImageTextToText, AutoProcessor
        except ImportError as exc:
            raise RuntimeError('Không import được thư viện. Cần: pip install "transformers>=5.0.0" torch') from exc
        if not torch.cuda.is_available():
            raise RuntimeError("PaddleOCR-VL engine cần CUDA GPU.")
        cfg = settings()
        dtype_name = _dtype_name(torch, cfg["dtype"])
        dtype = getattr(torch, dtype_name)
        log.info("Nạp %s: GPU=%s cc=%s dtype=%s", MODEL_ID, torch.cuda.get_device_name(0),
                 torch.cuda.get_device_capability(0), dtype_name)
        processor = AutoProcessor.from_pretrained(MODEL_ID, revision=MODEL_REVISION)
        try:
            model = AutoModelForImageTextToText.from_pretrained(MODEL_ID, revision=MODEL_REVISION, dtype=dtype)
        except TypeError:                             # transformers cũ chưa có tham số dtype
            model = AutoModelForImageTextToText.from_pretrained(MODEL_ID, revision=MODEL_REVISION,
                                                                torch_dtype=dtype)
        model = model.to("cuda").eval()
        for name in ("temperature", "top_p", "top_k"):          # tắt cảnh báo cờ sampling khi greedy
            if hasattr(model.generation_config, name):
                setattr(model.generation_config, name, None)
        _STATE.update(model=model, processor=processor, torch=torch)
        log.info("Model sẵn sàng.")
        return _STATE


def _generate(image: Image.Image, *, max_new_tokens: int, max_pixels: int
              ) -> tuple[str, int, list[float], bool]:
    """Một lần chép một crop. Trả về (text, số token sinh, logprob từng token, bị dừng vì lặp)."""
    st = _load_model()
    torch, processor, model = st["torch"], st["processor"], st["model"]
    from transformers import (LogitsProcessor, LogitsProcessorList, StoppingCriteria,
                              StoppingCriteriaList)

    class Recorder(LogitsProcessor):                  # greedy: token chọn = argmax -> logprob = max log_softmax
        def __init__(self):
            self.logprobs: list[float] = []

        def __call__(self, input_ids, scores):
            self.logprobs.append(float(torch.log_softmax(scores.float(), dim=-1).max(dim=-1).values[0]))
            return scores

    class LoopStop(StoppingCriteria):                 # dừng sớm khi model sa vào vòng lặp
        def __init__(self, prompt_len: int):
            self.prompt_len = prompt_len
            self.hit = False

        def __call__(self, input_ids, scores, **kwargs):
            n = input_ids.shape[1] - self.prompt_len
            if not self.hit and n >= LOOP_MIN_TOKENS and n % LOOP_CHECK_EVERY == 0:
                self.hit = is_looping(input_ids[0, self.prompt_len:].tolist())
            return torch.full((input_ids.shape[0],), self.hit, dtype=torch.bool, device=input_ids.device)

    messages = [{"role": "user", "content": [{"type": "image", "image": image},
                                             {"type": "text", "text": PROMPT}]}]
    images_kwargs = {"size": {"shortest_edge": MIN_PIXELS, "longest_edge": max_pixels}}
    # transformers >= 5.x có `processor_kwargs`; truyền images_kwargs qua **kwargs vẫn chạy (được gộp vào cùng
    # chỗ, kết quả y hệt) nhưng in cảnh báo "Kwargs passed to processor.__call__..." cho MỖI dòng ảnh.
    if "processor_kwargs" in inspect.signature(processor.apply_chat_template).parameters:
        extra = {"processor_kwargs": {"images_kwargs": images_kwargs}}
    else:
        extra = {"images_kwargs": images_kwargs}
    inputs = processor.apply_chat_template(
        messages, add_generation_prompt=True, tokenize=True, return_dict=True, return_tensors="pt", **extra)
    inputs = inputs.to(device=model.device, dtype=model.dtype)   # chỉ ép kiểu các tensor số thực
    prompt_len = int(inputs["input_ids"].shape[-1])
    recorder, stop = Recorder(), LoopStop(prompt_len)
    with torch.inference_mode():
        out = model.generate(**inputs, max_new_tokens=max_new_tokens, do_sample=False,
                             logits_processor=LogitsProcessorList([recorder]),
                             stopping_criteria=StoppingCriteriaList([stop]))
    gen = out[0][prompt_len:]
    text = processor.decode(gen, skip_special_tokens=True).strip()
    count = int(gen.shape[-1])
    del inputs, out, gen
    return text, count, recorder.logprobs, stop.hit


def _generate_safe(image: Image.Image, *, max_new_tokens: int, max_pixels: int
                   ) -> tuple[str, int, list[float], bool, int]:
    """Như _generate; hết VRAM thì giảm max_pixels (÷1.5, tối đa 2 lần) rồi thử lại. Trả thêm max_pixels đã dùng."""
    torch = _load_model()["torch"]
    px = max_pixels
    for attempt in range(3):
        try:
            return (*_generate(image, max_new_tokens=max_new_tokens, max_pixels=px), px)
        except torch.cuda.OutOfMemoryError:
            torch.cuda.empty_cache()
            if attempt == 2:
                raise
            px = max(256 * 28 * 28 // 4, int(px / 1.5) // (28 * 28) * (28 * 28))
            log.warning("Hết VRAM, thử lại với max_pixels=%d", px)
    raise RuntimeError("unreachable")


@register_engine
class PaddleVLEngine(OcrEngine):
    """PaddleOCR-VL-1.6 (element-level, prompt "OCR:") theo dải ngang hoặc cột dọc; nạp model một lần."""

    name = "paddlevl"
    max_workers = 1

    @classmethod
    def describe(cls, lang: str) -> dict[str, Any]:
        """Kiểm tra phụ thuộc, KHÔNG nạp model. Mọi tham số ảnh hưởng kết quả phải nằm ở đây (khoá cache)."""
        try:
            import torch
            import transformers
        except ImportError as exc:
            raise RuntimeError('PaddleOCR-VL engine thiếu dependency. Cài bằng:\n'
                               'pip install "transformers>=5.0.0" torch') from exc
        if int(transformers.__version__.split(".")[0]) < 5:
            raise RuntimeError(f"PaddleOCR-VL-1.6 cần transformers>=5.0.0 (đang {transformers.__version__}). "
                               "Dùng môi trường riêng, adapter Qwen đang chạy transformers 4.x.")
        if not torch.cuda.is_available():
            raise RuntimeError("PaddleOCR-VL engine hiện yêu cầu CUDA GPU.")
        cfg = settings()
        return {
            "name": cls.name, "model_id": MODEL_ID, "model_revision": MODEL_REVISION,
            "transformers_version": transformers.__version__, "torch_version": torch.__version__,
            "cuda_version": torch.version.cuda, "dtype": _dtype_name(torch, cfg["dtype"]),
            "orientation": cfg["orientation"], "max_pixels": cfg["max_pixels"],
            "band_inches": cfg["band_inches"], "max_new_tokens": cfg["max_new_tokens"],
            "prompt": PROMPT, "col_rel_threshold": COL_REL_THRESHOLD, "min_unit_ink_rel": MIN_UNIT_INK_REL, "min_pixels": MIN_PIXELS, "min_band_side": MIN_BAND_SIDE, "min_span_px": MIN_SPAN_PX,
            "loop_pattern": _LOOP_RE.pattern, "max_identical_run": MAX_IDENTICAL_RUN,
            "confidence_caps": [DEGENERATE_CONFIDENCE_CAP, TRUNCATED_CONFIDENCE_CAP],
            "adapter_version": ADAPTER_VERSION,
        }

    def recognize(self, image: Image.Image, *, lang: str, psm: int, dpi: int) -> OcrResult:
        del psm                                       # không dùng PSM của Tesseract
        cfg = settings()
        image = image.convert("RGB")
        width, height = image.size
        if width <= 0 or height <= 0:
            raise ValueError("Ảnh đầu vào rỗng.")

        memo_key = _sha("|".join([hashlib.sha256(image.tobytes()).hexdigest(), str(image.size), str(dpi),
                                  cfg["orientation"], cfg["dtype"], str(cfg["max_pixels"]),
                                  str(cfg["band_inches"]), str(cfg["max_new_tokens"]), str(ADAPTER_VERSION)]))
        hit = _MEMO.get(memo_key)
        if hit is not None:
            log.info("Dùng lại kết quả đã chép cho ảnh này (%d dòng)", len(hit["lines"]))
            return self._result(hit, lang)

        import numpy as np
        mask = strip_rules(ink_mask(np.asarray(image.convert("L"))))
        orientation = cfg["orientation"] if cfg["orientation"] != "auto" else detect_orientation(mask)
        if orientation == "vertical":
            regions = [ink_box(mask, x0, 0, x1, height, pad=6) for x0, x1 in split_columns(mask)]
        else:
            # từng dòng ngang một: dải nhiều dòng làm model đọc lưới chữ theo cột (nhầm hướng)
            regions = [ink_box(mask, 0, y0, width, y1) for y0, y1 in split_rows(mask)]
        regions = [r for r in regions if r is not None]
        skipped: list[dict] = []
        if len(regions) > 1:
            inks = [int(mask[b[1]:b[3], b[0]:b[2]].sum()) for b in regions]
            floor = MIN_UNIT_INK_REL * float(np.percentile(inks, 75))
            skipped = [{"box": list(b), "ink": k} for b, k in zip(regions, inks) if k < floor]
            regions = [b for b, k in zip(regions, inks) if k >= floor]

        lines: list[OcrLine] = []
        units_raw: list[dict] = []
        started = time.perf_counter()
        for index, box in enumerate(regions, 1):
            crop = pad_to_min_side(image.crop(box))
            t0 = time.perf_counter()
            text, tokens, logprobs, loop_hit, used_px = _generate_safe(
                crop, max_new_tokens=cfg["max_new_tokens"], max_pixels=cfg["max_pixels"])
            truncated = tokens >= cfg["max_new_tokens"] and not loop_hit
            texts, flags = clean_generated_text(text)
            if loop_hit:
                flags.append("loop_stopped")
            if truncated:
                flags.append("truncated")
            if used_px < cfg["max_pixels"]:
                flags.append("oom_downscaled")
            conf = band_confidence(logprobs, flags, truncated)
            if orientation == "vertical":
                if texts:                              # một cột = một dòng
                    lines.append(OcrLine("".join(texts), tuple(int(v) for v in box), conf))
                n_lines = 1 if texts else 0
            else:
                for line_text, sub in zip(texts, split_line_boxes(len(texts), box)):
                    lines.append(OcrLine(line_text, tuple(int(v) for v in sub), conf))
                n_lines = len(texts)
            units_raw.append({"unit": index, "box": list(box), "generated_text": text, "n_lines": n_lines,
                              "tokens": tokens, "seconds": round(time.perf_counter() - t0, 3),
                              "confidence": conf, "flags": flags, "max_pixels": used_px})
            if flags:
                log.warning("Đơn vị %d/%d có cờ %s", index, len(regions), flags)

        elapsed = time.perf_counter() - started
        log.info("PaddleOCR-VL (%s): %d đơn vị, %d dòng, %.1fs", orientation, len(units_raw), len(lines), elapsed)
        entry = {
            "lines": [(x.text, x.bbox, x.confidence) for x in lines],
            "raw": {"model_id": MODEL_ID, "model_revision": MODEL_REVISION, "orientation": orientation,
                    "prompt": PROMPT, "dpi": dpi, "image_size": [width, height],
                    "bbox_granularity": "unit_exact" if orientation == "vertical" else "row_exact",
                    "max_new_tokens": cfg["max_new_tokens"], "max_pixels": cfg["max_pixels"],
                    "inference_seconds": round(elapsed, 3), "bands": units_raw, "skipped_units": skipped,
                    "any_degenerate": any({"repetition_collapsed", "loop_stopped"} & set(u["flags"])
                                          for u in units_raw),
                    "any_truncated": any("truncated" in u["flags"] for u in units_raw)},
        }
        if len(_MEMO) >= _MEMO_LIMIT:
            _MEMO.pop(next(iter(_MEMO)))
        _MEMO[memo_key] = entry
        return self._result(entry, lang)

    def _result(self, entry: dict, lang: str) -> OcrResult:
        lines = [OcrLine(t, tuple(b), c) for t, b, c in entry["lines"]]
        raw = {**entry["raw"], "lang": lang}
        raw["bands"] = [dict(b) for b in entry["raw"]["bands"]]      # tránh dùng chung list với memo
        return OcrResult(self.name, lines, raw)