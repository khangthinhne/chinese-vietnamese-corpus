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
    return next(
        (r for r in reversed(store["runs"]) if r.get("key") == key),
        None
    )

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


# --- UNIFIED (Single-stream) OCR STORAGE ---
UNIFIED_SCHEMA_VERSION = 1


def unified_raw_path(layout: DataLayout, page: int) -> Path:
    return layout.ocr_raw_unified_path(page)


def load_unified_page(layout: DataLayout, page: int, work_id: str) -> dict:
    path = unified_raw_path(layout, page)
    if not path.exists():
        return {"schema_version": UNIFIED_SCHEMA_VERSION, "work_id": work_id,
                "page": page, "runs": []}
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"OCR thô phải là JSON object: {path}")
    if data.get("schema_version") != UNIFIED_SCHEMA_VERSION:
        raise ValueError(f"Schema OCR thô không phù hợp: {path}")
    if data.get("page") != page:
        raise ValueError(f"Số trang trong {path} không khớp yêu cầu")
    if not isinstance(data.get("runs"), list):
        raise ValueError(f"OCR thô thiếu danh sách runs: {path}")
    return data


def save_unified_page(layout: DataLayout, page: int, data: dict) -> Path:
    path = unified_raw_path(layout, page)
    write_json(path, data)
    return path


def unified_manifest_path(layout: DataLayout) -> Path:
    return layout.ocr_manifest_dir / "unified.json"


def write_unified_manifest(layout: DataLayout, record: dict) -> Path:
    path = unified_manifest_path(layout)
    write_json(path, record)
    # Also write to 03_ocr/metadata.json as specified in layout
    write_json(layout.ocr_metadata_path, record)
    return path


def load_unified_manifest(layout: DataLayout) -> dict | None:
    path = unified_manifest_path(layout)
    if not path.exists():
        path = layout.ocr_metadata_path
    if not path.exists():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("schema_version") != UNIFIED_SCHEMA_VERSION:
        raise ValueError(f"Schema manifest không phù hợp: {path}")
    return data


# --- OCR THEO LUỒNG (han/vie): kho run riêng theo engine ---
# data/<WORK_ID>/03_ocr/raw/<engine>/NNNN.json, cùng schema với raw/NNNN.json (unified).
# raw/NNNN.json cũ chỉ được ĐỌC (tra cache, chọn run), không bao giờ bị ghi.
ENGINE_SCHEMA_VERSION = UNIFIED_SCHEMA_VERSION


def check_engine_dir_name(engine: str) -> str:
    if not engine or engine in WRITING_SYSTEMS or not engine.replace("_", "").isalnum():
        raise ValueError(f"Tên engine không dùng được làm thư mục kho OCR: {engine!r}")
    return engine


def engine_raw_path(layout: DataLayout, engine: str, page: int) -> Path:
    return layout.ocr_raw_engine_path(check_engine_dir_name(engine), page)


def load_engine_page(layout: DataLayout, engine: str, page: int, work_id: str) -> dict:
    path = engine_raw_path(layout, engine, page)
    if not path.exists():
        return {"schema_version": ENGINE_SCHEMA_VERSION, "work_id": work_id,
                "engine": engine, "page": page, "runs": []}
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or data.get("schema_version") != ENGINE_SCHEMA_VERSION:
        raise ValueError(f"Schema OCR thô không phù hợp: {path}")
    if data.get("page") != page or data.get("engine") != engine:
        raise ValueError(f"Trang/engine trong {path} không khớp yêu cầu")
    if not isinstance(data.get("runs"), list):
        raise ValueError(f"OCR thô thiếu danh sách runs: {path}")
    return data


def save_engine_page(layout: DataLayout, engine: str, page: int, data: dict) -> Path:
    path = engine_raw_path(layout, engine, page)
    if path.exists():                     # append-only: không bao giờ làm mất run đã có
        old = load_engine_page(layout, engine, page, data.get("work_id", ""))
        if data["runs"][:len(old["runs"])] != old["runs"]:
            raise ValueError(f"Từ chối ghi {path}: danh sách runs cũ bị thay đổi")
    write_json(path, data)
    return path


def legacy_runs(layout: DataLayout, page: int, work_id: str,
                writing_systems: tuple[str, ...] = WRITING_SYSTEMS) -> list[dict]:
    """Run trong raw/NNNN.json (unified) và raw/<chi_tra|vie>/page_NNNN.json (chế độ hệ chữ cũ).

    Chỉ đọc. Mỗi run được gắn `_source` (đường dẫn tương đối) để ghi provenance; khoá này
    không bao giờ được ghi ngược lại vào file thô."""
    out: list[dict] = []
    path = unified_raw_path(layout, page)
    if path.exists():
        out += [{**r, "_source": layout.rel(path)} for r in load_unified_page(layout, page, work_id)["runs"]]
    for ws in writing_systems:
        path = raw_path(layout, ws, page)
        if path.exists():
            out += [{**r, "_source": layout.rel(path)} for r in load_page(layout, ws, page, work_id)["runs"]]
    return out


def page_runs(layout: DataLayout, engine: str, page: int, work_id: str,
              writing_systems: tuple[str, ...] = WRITING_SYSTEMS) -> list[dict]:
    """Mọi run đọc được cho một trang: file cũ trước, kho của engine sau (run mới nhất ở cuối).

    `writing_systems` giới hạn kho hệ chữ cũ được đọc (vd luồng vie không lấy run của raw/chi_tra)."""
    path = engine_raw_path(layout, engine, page)
    own = load_engine_page(layout, engine, page, work_id)["runs"]
    return legacy_runs(layout, page, work_id, writing_systems) + [{**r, "_source": layout.rel(path)} for r in own]


def available_pages(layout: DataLayout, engines: list[str] | tuple[str, ...] = ()) -> list[int]:
    """Số trang có ít nhất một file OCR thô (unified, kho engine hoặc kho hệ chữ cũ)."""
    import re as _re
    pages: set[int] = set()
    dirs = [layout.ocr_raw_unified_dir, *(layout.ocr_raw_engine_dir(e) for e in engines),
            *(layout.ocr_raw_root / ws for ws in WRITING_SYSTEMS)]
    for folder in dirs:
        if not folder.is_dir():
            continue
        for entry in folder.iterdir():
            match = _re.fullmatch(r"(?:page_)?(\d{4})\.json", entry.name)
            if entry.is_file() and match:
                pages.add(int(match[1]))
    return sorted(pages)
