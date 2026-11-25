"""Kiểm tra trước khi OCR một luồng: đúng môi trường, có GPU CUDA, model đã tải về máy. Không nạp model.

    python -m src.ocr.preflight --engine paddlevl      # trong .venv-paddle
    python -m src.ocr.preflight --engine qwen          # trong my_nlp

Mã thoát 0 = sẵn sàng; 3 = thiếu GPU/model/thư viện (thông báo nói rõ phải làm gì). Dùng trong scripts/ocr_work.*.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

MODEL_IDS = {"paddlevl": "PaddlePaddle/PaddleOCR-VL-1.6", "qwen": "Qwen/Qwen2.5-VL-3B-Instruct"}
ENVS = {"paddlevl": ".venv-paddle", "qwen": "my_nlp"}


def model_cached(model_id: str) -> bool | None:
    """True/False nếu biết, None nếu không có huggingface_hub để kiểm tra."""
    try:
        from huggingface_hub import try_to_load_from_cache
    except ImportError:
        return None
    hit = try_to_load_from_cache(model_id, "config.json")
    return isinstance(hit, str)


def check(engine: str) -> tuple[bool, dict]:
    info: dict = {"engine": engine, "python": sys.executable, "model_id": MODEL_IDS[engine]}
    from src.ocr.engines import ENGINES          # import nặng (torch); chỉ khi thật sự kiểm tra
    if engine not in ENGINES:
        info["problem"] = f"engine {engine} không nạp được trong python này; dùng môi trường {ENVS[engine]}"
        return False, info
    try:
        descriptor = ENGINES[engine].describe("all")     # kiểm tra thư viện + CUDA, không nạp model
    except Exception as exc:                              # noqa: BLE001 - báo nguyên văn cho người dùng
        info["problem"] = f"{exc} (môi trường đúng: {ENVS[engine]})"
        return False, info
    import torch
    props = torch.cuda.get_device_properties(0)
    info.update({"gpu": props.name, "vram_gb": round(props.total_memory / 2**30, 1),
                 "compute_capability": f"{props.major}.{props.minor}",
                 "dtype": descriptor.get("dtype") or (descriptor.get("quantization") or {}).get("compute_dtype"),
                 "adapter_version": descriptor.get("adapter_version"),
                 "transformers": descriptor.get("transformers_version"), "torch": descriptor.get("torch_version"),
                 "hf_hub_offline": os.environ.get("HF_HUB_OFFLINE", "")})
    cached = model_cached(MODEL_IDS[engine])
    info["model_cached"] = cached
    if cached is False:
        info["problem"] = (f"chưa tải model {MODEL_IDS[engine]} vào cache HF ({os.environ.get('HF_HUB_CACHE', 'mặc định')})."
                           " Tải một lần khi có mạng: xem docs/ocr_runbook.md mục 3")
        return False, info
    if info["vram_gb"] < 5.5:
        info["warning"] = "GPU dưới 6 GB: có thể OOM; script tự thử lại với *_MAX_PIXELS_TOKENS=768"
    return True, info


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--engine", choices=sorted(MODEL_IDS), required=True)
    args = parser.parse_args(argv)
    ok, info = check(args.engine)
    print(json.dumps(info, ensure_ascii=False))
    if not ok:
        print(f"KHÔNG SẴN SÀNG [{args.engine}]: {info['problem']}", file=sys.stderr)
        return 3
    print(f"Sẵn sàng [{args.engine}]: {info['gpu']} {info['vram_gb']} GB, dtype {info['dtype']}, "
          f"adapter {info['adapter_version']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
