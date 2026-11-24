"""Shared page-naming utility. All page-specific filenames use 4-digit zero-padded numbers."""

from __future__ import annotations


def page_stem(page_number: int) -> str:
    """Generate 4-digit zero-padded page stem. E.g., 1 -> '0001', 15 -> '0015', 206 -> '0206', 9999 -> '9999'."""
    if not isinstance(page_number, int) or page_number < 0:
        raise ValueError(f"Page number must be a non-negative integer, got {page_number}")
    return f"{page_number:04d}"


def page_filename(page_number: int, ext: str = ".txt") -> str:
    """Generate page filename with extension. E.g., 15, '.txt' -> '0015.txt'."""
    if not ext.startswith("."):
        ext = "." + ext
    return page_stem(page_number) + ext
