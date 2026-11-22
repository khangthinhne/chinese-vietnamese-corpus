"""Module tách câu (đã di chuyển sang src/segmentation/sentences.py).

File này giữ lại để tương thích ngược cho các import cũ:
    from src.cleaning.segmentation import build_stream, make_sentences, split_han, split_vi
"""

from __future__ import annotations

from src.segmentation.sentences import (
    SentenceConfig,
    build_stream,
    make_sentences,
    split_han,
    split_vi,
)

__all__ = [
    "SentenceConfig",
    "build_stream",
    "make_sentences",
    "split_han",
    "split_vi",
]
