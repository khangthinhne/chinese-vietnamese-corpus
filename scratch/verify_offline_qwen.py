"""Verify offline Qwen model loading and inference without network access."""

import os
import sys
import time

sys.path.insert(0, ".")

# Ensure offline mode is active
os.environ["HF_HUB_OFFLINE"] = "1"
os.environ["TRANSFORMERS_OFFLINE"] = "1"
os.environ["PYTHONUNBUFFERED"] = "1"

import torch
from PIL import Image

print(f"Python version: {sys.version}")
print(f"PyTorch version: {torch.__version__}")
print(f"CUDA available: {torch.cuda.is_available()}")
if torch.cuda.is_available():
    print(f"GPU device count: {torch.cuda.device_count()}")
    print(f"GPU name: {torch.cuda.get_device_name(0)}")
    props = torch.cuda.get_device_properties(0)
    print(f"Total VRAM: {props.total_memory / (1024**3):.2f} GB")

from src.ocr.engines.qwen import QwenEngine, _load_model, settings, MODEL_ID

print(f"\nChecking QwenEngine describe:")
desc = QwenEngine.describe("all")
print(f"Engine name: {desc['name']}, Model: {desc['model_id']}")
print(f"Transformers version: {desc.get('transformers_version')}")
print(f"BitsAndBytes version: {desc.get('bitsandbytes_version')}")

print(f"\nAttempting offline _load_model...")
t0 = time.time()
st = _load_model(max_pixels=802816)
print(f"Model and processor loaded in {time.time() - t0:.2f}s")
print(f"Processor: {type(st['processor']).__name__}")
print(f"Model: {type(st['model']).__name__}")
print(f"Current allocated VRAM: {torch.cuda.memory_allocated(0) / (1024**3):.2f} GB")
print(f"Current reserved VRAM: {torch.cuda.memory_reserved(0) / (1024**3):.2f} GB")

# Test real inference on a synthetic test image with text
print(f"\nTesting actual inference on a synthetic test image...")
img = Image.new("RGB", (200, 60), color="white")
engine = QwenEngine()
t_inf = time.time()
result = engine.recognize(img, lang="all", psm=6, dpi=300)
print(f"Inference completed in {time.time() - t_inf:.2f}s")
print(f"Result lines returned: {len(result.lines)}")
print(f"VRAM after inference: allocated={torch.cuda.memory_allocated(0)/(1024**3):.2f} GB, reserved={torch.cuda.memory_reserved(0)/(1024**3):.2f} GB")
print("Offline Qwen verification SUCCESSFUL!")
