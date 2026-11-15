"""Kho OCR thô độc lập với lớp corpus.

    data/<WORK_ID>/03_ocr/raw/<writing_system>/page_0205.json
    data/<WORK_ID>/03_ocr/manifests/<writing_system>.json

Một tệp trang chứa danh sách `runs` append-only: mỗi lần nhận dạng là một run có
`key` (băm tham số), tham số đầy đủ (ảnh, engine/model, hệ chữ, ngôn ngữ, crop,
PSM, DPI) và các dòng OCR kèm bbox + confidence + payload gốc của engine.

Kho này KHÔNG biết `han/am/nghia`: phân đoạn tài liệu/lớp là bước sau
(`src/segmentation`). Cache dùng chung cho mọi lần chạy lại; `--force` chỉ ghi lại
manifest, không xoá run cũ.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from src.common.corpus import DataLayout, write_json

SCHEMA_VERSION = 1
WRITING_SYSTEMS = ("chi_tra", "vie")


def raw_path(layout: DataLayout, writing_system: str, page: int) -> Path:
    return layout.ocr_raw_root / writing_system / f"page_{page:04d}.json"


def manifest_path(layout: DataLayout, writing_system: str) -> Path:
    return layout.ocr_manifest_dir / f"{writing_system}.json"


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def cache_key(parameters: dict) -> str:
    return hashlib.sha256(json.dumps(parameters, sort_keys=True, ensure_ascii=False,
                                     allow_nan=False).encode("utf-8")).hexdigest()


def load_page(layout: DataLayout, writing_system: str, page: int, work_id: str) -> dict:
    path = raw_path(layout, writing_system, page)
    if not path.exists():
        return {"schema_version": SCHEMA_VERSION, "work_id": work_id,
                "writing_system": writing_system, "page": page, "runs": []}
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"OCR thô phải là JSON object: {path}")
    if data.get("schema_version") != SCHEMA_VERSION:
        raise ValueError(f"Schema OCR thô không phù hợp: {path}")
    if data.get("page") != page or data.get("writing_system") != writing_system:
        raise ValueError(f"Trang/hệ chữ trong {path} không khớp yêu cầu")
    if not isinstance(data.get("runs"), list):
        raise ValueError(f"OCR thô thiếu danh sách runs: {path}")
    return data


def save_page(layout: DataLayout, writing_system: str, page: int, data: dict) -> Path:
    path = raw_path(layout, writing_system, page)
    write_json(path, data)
    return path


def find_run(store: dict, key: str) -> dict | None:
    return next((r for r in store["runs"] if r.get("key") == key), None)


def write_manifest(layout: DataLayout, writing_system: str, record: dict) -> Path:
    path = manifest_path(layout, writing_system)
    write_json(path, record)
    return path


def load_manifest(layout: DataLayout, writing_system: str) -> dict | None:
    path = manifest_path(layout, writing_system)
    if not path.exists():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("schema_version") != SCHEMA_VERSION:
        raise ValueError(f"Schema manifest không phù hợp: {path}")
    return data
