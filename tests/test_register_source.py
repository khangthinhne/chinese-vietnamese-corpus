"""Kiểm thử register_source: đồng bộ thư mục nguồn vào 01_sources an toàn, idempotent.

Chạy từ thư mục gốc repo:  python -m pytest tests -q
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.common.corpus import DataLayout, register_source, sha256_file   # noqa: E402


def make_source(tmp_path: Path, name: str = "han", pages=(205, 206, 207)) -> Path:
    src = tmp_path / "incoming" / name
    src.mkdir(parents=True)
    for page in pages:
        (src / f"page_{page}.png").write_bytes(f"image-{page}".encode())
    return src


def layout_for(tmp_path: Path, work_id: str = "W_01") -> DataLayout:
    return DataLayout(tmp_path / "data", work_id)


def test_register_new_directory(tmp_path):
    layout = layout_for(tmp_path)
    src = make_source(tmp_path)

    info = register_source(layout, src)

    assert info["path"] == "01_sources/han"
    dst = layout.sources / "han"
    assert sorted(p.name for p in dst.iterdir()) == [
        "page_205.png", "page_206.png", "page_207.png"]
    for page in (205, 206, 207):
        assert sha256_file(dst / f"page_{page}.png") == sha256_file(src / f"page_{page}.png")


def test_existing_identical_directory_is_reused(tmp_path, monkeypatch):
    layout = layout_for(tmp_path)
    src = make_source(tmp_path)
    register_source(layout, src)
    dst = layout.sources / "han"
    inode = (dst / "page_205.png").stat().st_ino

    def boom(*args, **kwargs):
        raise AssertionError("không được copy khi nguồn đã đồng bộ")

    monkeypatch.setattr(shutil, "copy2", boom)
    monkeypatch.setattr(shutil, "copytree", boom)
    info = register_source(layout, src)

    assert info["path"] == "01_sources/han"
    assert (dst / "page_205.png").stat().st_ino == inode


def test_existing_incomplete_directory_syncs_missing_files(tmp_path):
    """Đúng ca CPO_18: nguồn có 205-210 nhưng bản đăng ký chỉ có page_205.png."""
    layout = layout_for(tmp_path)
    src = make_source(tmp_path, pages=(205, 206, 207, 208, 209, 210))
    dst = layout.sources / "han"
    dst.mkdir(parents=True)
    shutil.copy2(src / "page_205.png", dst / "page_205.png")
    old_inode = (dst / "page_205.png").stat().st_ino
    (dst / "page_999.png").write_bytes("không có trong nguồn".encode("utf-8"))   # tệp thừa phải được giữ

    register_source(layout, src)

    assert (dst / "page_205.png").stat().st_ino == old_inode        # không ghi đè tệp đã có
    for page in (206, 207, 208, 209, 210):
        assert (dst / f"page_{page}.png").read_bytes() == f"image-{page}".encode()
    assert (dst / "page_999.png").read_bytes() == "không có trong nguồn".encode("utf-8")  # không xoá


def test_conflicting_file_raises_and_keeps_destination(tmp_path):
    layout = layout_for(tmp_path)
    src = make_source(tmp_path, pages=(205, 206))
    dst = layout.sources / "han"
    dst.mkdir(parents=True)
    (dst / "page_205.png").write_bytes(b"old-205")

    with pytest.raises(ValueError, match="xung đột"):
        register_source(layout, src)

    assert (dst / "page_205.png").read_bytes() == b"old-205"
    assert not (dst / "page_206.png").exists()      # không copy dở dang trước khi báo lỗi


def test_nested_directories_preserved_and_synced(tmp_path):
    layout = layout_for(tmp_path)
    src = tmp_path / "incoming" / "scan"
    (src / "sub" / "deep").mkdir(parents=True)
    (src / "top.txt").write_text("top", encoding="utf-8")
    (src / "sub" / "deep" / "leaf.txt").write_text("leaf", encoding="utf-8")

    register_source(layout, src)
    dst = layout.sources / "scan"
    assert (dst / "sub" / "deep" / "leaf.txt").read_text(encoding="utf-8") == "leaf"

    (src / "sub" / "extra.txt").write_text("extra", encoding="utf-8")
    register_source(layout, src)
    assert (dst / "sub" / "extra.txt").read_text(encoding="utf-8") == "extra"
    assert (dst / "top.txt").is_file()
    assert (dst / "sub" / "deep" / "leaf.txt").is_file()


def test_existing_pdf_single_file_registration_unchanged(tmp_path):
    layout = layout_for(tmp_path)
    src = tmp_path / "incoming" / "book.pdf"
    src.parent.mkdir(parents=True)
    src.write_bytes(b"%PDF-1.4 fake")

    first = register_source(layout, src)
    second = register_source(layout, src)

    assert first["path"] == "01_sources/book.pdf"
    assert first["bytes"] == len(b"%PDF-1.4 fake")
    assert first["sha256"] == second["sha256"] == sha256_file(layout.sources / "book.pdf")
    assert (layout.sources / "book.pdf").read_bytes() == b"%PDF-1.4 fake"


def test_registration_never_modifies_source(tmp_path):
    layout = layout_for(tmp_path)
    src = make_source(tmp_path, pages=(205, 206))
    before = {p.name: (p.stat().st_mtime_ns, sha256_file(p)) for p in src.iterdir()}

    register_source(layout, src)
    (layout.sources / "han" / "page_205.png").write_bytes("đích bị sửa tay".encode("utf-8"))
    with pytest.raises(ValueError, match="xung đột"):
        register_source(layout, src)   # xung đột -> lỗi, nhưng không được đụng vào nguồn

    after = {p.name: (p.stat().st_mtime_ns, sha256_file(p)) for p in src.iterdir()}
    assert after == before
