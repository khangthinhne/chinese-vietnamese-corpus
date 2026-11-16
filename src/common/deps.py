"""Theo dõi phụ thuộc giữa các bước để phát hiện artifact cũ.

Mỗi stage ghi fingerprint của ĐẦU VÀO vào metadata.json. Nếu output đã tồn tại và
fingerprint đầu vào đã đổi, stage từ chối ghi đè âm thầm (phải dùng --force).
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Iterable

from src.common.corpus import DataLayout, MetadataStore, sha256_file, write_json


def _file_digest(path: Path) -> str:
    return sha256_file(path)


def fingerprint(paths: Iterable[Path]) -> str:
    h = hashlib.sha256()
    for path in sorted({Path(p) for p in paths}):
        if path.is_dir():
            files = sorted(p for p in path.rglob("*") if p.is_file())
        elif path.exists():
            files = [path]
        else:
            files = []
        h.update(path.as_posix().encode("utf-8"))
        if not files:
            h.update(b"<missing>")
        for item in files:
            h.update(item.as_posix().encode("utf-8"))
            h.update(_file_digest(item).encode("ascii"))
    return h.hexdigest()


def _display(layout: DataLayout, path: Path) -> str:
    try:
        return layout.rel(path)
    except ValueError:
        return Path(path).as_posix()


def record(layout: DataLayout, stage: str, inputs: Iterable[Path],
           outputs: Iterable[Path] | None = None) -> None:
    store = MetadataStore(layout)
    data = store.load()
    data.setdefault("dependencies", {})[stage] = {
        "fingerprint": fingerprint(inputs),
        "inputs": [_display(layout, Path(p)) for p in inputs],
        "outputs": [_display(layout, Path(p)) for p in (outputs or []) if Path(p).exists()],
    }
    write_json(layout.metadata_path, data)


def recorded(layout: DataLayout, stage: str) -> dict | None:
    return MetadataStore(layout).load().get("dependencies", {}).get(stage)


def check_fresh(layout: DataLayout, stage: str, inputs: Iterable[Path],
                outputs: Iterable[Path], force: bool, error: str) -> None:
    """Chặn ghi đè khi output đã có; báo rõ nếu đầu vào đổi kể từ lần chạy trước."""
    existing = [Path(p) for p in outputs if Path(p).exists()]
    if not existing or force:
        return
    dep = recorded(layout, stage)
    if dep and dep.get("fingerprint") != fingerprint(inputs):
        raise FileExistsError(
            f"{existing[0]} đã tồn tại và đầu vào của bước '{stage}' đã thay đổi; "
            "dùng --force để tạo lại (artifact cũ được giữ nguyên nếu không chạy)")
    raise FileExistsError(error)
