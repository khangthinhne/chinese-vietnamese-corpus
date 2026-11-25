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


class UpToDate(FileExistsError):
    """Output chính đã có và đầu vào không đổi kể từ lần ghi: bước được BỎ QUA (main trả mã 0).

    Là FileExistsError để code cũ gọi thẳng hàm (không qua main) vẫn thấy "không ghi đè"."""


def is_fresh(layout: DataLayout, stage: str, inputs: Iterable[Path], primary: Path) -> bool:
    """Output chính tồn tại và fingerprint đầu vào trùng bản đã ghi lần trước."""
    dep = recorded(layout, stage)
    return Path(primary).exists() and bool(dep) and dep.get("fingerprint") == fingerprint(inputs)


def check_fresh(layout: DataLayout, stage: str, inputs: Iterable[Path],
                outputs: Iterable[Path], force: bool, error: str) -> None:
    """Không ghi đè output đã có khi thiếu --force.

    - đầu vào không đổi (fingerprint khớp, output chính còn) -> `UpToDate` (bỏ qua, mã 0);
    - đầu vào đã đổi -> FileExistsError (cần --force), như trước;
    - không có bản ghi phụ thuộc (output do tay/bản cũ) -> FileExistsError(error), như trước."""
    outputs = [Path(p) for p in outputs]
    existing = [p for p in outputs if p.exists()]
    if not existing or force:
        return
    dep = recorded(layout, stage)
    inputs = list(inputs)
    if dep and dep.get("fingerprint") != fingerprint(inputs):
        raise FileExistsError(
            f"{existing[0]} đã tồn tại và đầu vào của bước '{stage}' đã thay đổi; "
            "dùng --force để tạo lại (artifact cũ được giữ nguyên nếu không chạy)")
    if dep and outputs and outputs[0].exists():
        raise UpToDate(f"[{stage}] {outputs[0].name} đã có, đầu vào không đổi: bỏ qua")
    raise FileExistsError(error)
