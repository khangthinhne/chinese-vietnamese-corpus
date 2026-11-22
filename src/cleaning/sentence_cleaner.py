"""Giao diện làm sạch cấp câu (Sentence-level Cleaning Interface).

Thực hiện chuẩn hoá và kiểm tra tính hợp lệ ở cấp câu mà KHÔNG làm mất
truy vết nguồn (source_ids, segment_ids, document_id, số trang).

Tránh lặp lại các thao tác làm sạch cấu trúc (như lọc trang, xoá tiêu đề lặp,
phát hiện trùng lặp giữa các trang, phát hiện chú thích nhiều dòng...).
"""

from __future__ import annotations

import argparse
import logging
import re
import sys
import unicodedata
from dataclasses import dataclass
from pathlib import Path

from src.cleaning.model import (
    ASCII_TO_HAN,
    HAN_CLOSERS,
    HAN_TERMINATORS,
    VI_CLOSERS,
    VI_OPENERS,
    CleanConfig,
)
from src.common import deps
from src.common.corpus import (
    DataLayout,
    MetadataStore,
    base_parser,
    cjk_ratio,
    parse_args,
)
from src.common.sentences import (
    Sentence,
    atomic_write_text,
    load_sentences,
    save_sentences,
)
from src.segmentation import serialization

log = logging.getLogger("sentence_cleaner")

_CJK_GAP = re.compile(r"(?<=[\u2E80-\u9FFF\uF900-\uFAFF])\s+(?=[\u2E80-\u9FFF\uF900-\uFAFF])")
_MULTISPACE = re.compile(r"[ \t]+")
_VI_PUNCT_SPACING = re.compile(r"\s+([,.:;?!…])")


def clean_sentence_han(text: str) -> str:
    """Chuẩn hoá câu Hán: NFC, chuyển dấu ASCII sang dấu Hán chuẩn, bỏ khoảng trắng giữa chữ Hán và quanh dấu câu."""
    text = unicodedata.normalize("NFC", text)
    # Thay thế dấu câu ASCII đơn lẻ bằng dấu CJK
    chars = []
    for c in text:
        chars.append(ASCII_TO_HAN.get(c, c))
    text = "".join(chars)
    # Bỏ khoảng trắng quanh dấu câu Hán
    text = re.sub(r"\s*([，。！？；：、])\s*", r"\1", text)
    # Bỏ khoảng trắng giữa các chữ Hán
    text = _CJK_GAP.sub("", text)
    return text.strip()


def clean_sentence_vi(text: str) -> str:
    """Chuẩn hoá câu tiếng Việt: NFC, gộp khoảng trắng, chuẩn hoá khoảng cách dấu câu."""
    text = unicodedata.normalize("NFC", text)
    text = _MULTISPACE.sub(" ", text)
    # Xoá khoảng trắng trước dấu ngắt câu (,.:;?!…)
    text = _VI_PUNCT_SPACING.sub(r"\1", text)
    return text.strip()


class SentenceCleaner:
    def __init__(self, layer: str, cfg: CleanConfig | None = None):
        self.layer = layer
        self.cfg = cfg

    def clean(self, sentence: Sentence) -> Sentence | None:
        """Làm sạch một câu, giữ nguyên toàn bộ thông tin nguồn."""
        text = sentence.text
        if self.layer == "han":
            text = clean_sentence_han(text)
            ok = sum(1 for c in text if cjk_ratio(c) > 0) >= 2
        else:
            text = clean_sentence_vi(text)
            ok = any(ch.isalnum() for ch in text)

        if not ok:
            return None

        flags = list(sentence.flags)

        # Kiểm tra dấu câu kết thúc
        if self.layer == "han":
            if text and text[-1] not in HAN_TERMINATORS + HAN_CLOSERS:
                if "no_terminal_punct" not in flags:
                    flags.append("no_terminal_punct")
            else:
                if "no_terminal_punct" in flags:
                    flags.remove("no_terminal_punct")
        else:
            if text and not re.search(r"[.?!…;:\"”’')»\]]$", text):
                if "no_terminal_punct" not in flags:
                    flags.append("no_terminal_punct")
            else:
                if "no_terminal_punct" in flags:
                    flags.remove("no_terminal_punct")

        # Kiểm tra độ dài câu bất thường
        max_len = 200 if self.layer == "han" else 600
        if len(text) > max_len and "long" not in flags:
            flags.append("long")

        segment_ids = list(getattr(sentence, "segment_ids", None) or getattr(sentence, "src_ids", []))
        source_ids = list(getattr(sentence, "source_ids", None) or [])

        return Sentence(
            id=sentence.id,
            text=text,
            pages=list(sentence.pages),
            segment_ids=segment_ids,
            source_ids=source_ids,
            flags=flags,
            document_id=sentence.document_id,
            raw_text=getattr(sentence, "raw_text", ""),
        )


def clean_sentences(
    sentences: list[Sentence],
    layer: str,
    cfg: CleanConfig | None = None
) -> tuple[list[Sentence], list[tuple[str, int, str, str]]]:
    """Làm sạch danh sách câu, trả về (cleaned_sentences, dropped_rows)."""
    cleaner = SentenceCleaner(layer, cfg)
    cleaned: list[Sentence] = []
    dropped: list[tuple[str, int, str, str]] = []

    for s in sentences:
        res = cleaner.clean(s)
        if res is not None:
            cleaned.append(res)
        else:
            page = s.pages[0] if s.pages else 0
            dropped.append((s.id, page, "invalid_or_too_short", s.text))

    return cleaned, dropped


def create_parser():
    p = base_parser("Làm sạch cấp câu độc lập -> 05_cleaning/<layer>.xml")
    return p


def run(args) -> Path:
    layer = args.layer
    layout = DataLayout(Path(args.data_root), args.work_id)
    out_dir = layout.cleaning_root
    layout.ensure(out_dir)
    out_xml = out_dir / f"{layer}.xml"

    # Nguồn vào: ưu tiên 04_segmentation/sentences/<layer>.xml
    sent_xml = layout.segmentation_sentence(layer)
    if sent_xml.exists():
        rows = serialization.read_sentences(sent_xml)
        sentences = [
            Sentence(
                id=r["id"],
                text=r["text"],
                pages=r["pages"],
                src_ids=r["src_ids"],
                flags=r["flags"],
                document_id=r["document_id"],
                segment_ids=r.get("segment_ids") or r["src_ids"],
                source_ids=r.get("source_ids") or [],
                raw_text=r.get("raw_text") or "",
            )
            for r in rows
        ]
        input_path = sent_xml
    else:
        # Lùi về đọc 05_cleaning/<layer>.xml nếu có
        input_path = layout.cleaning_root / f"{layer}.xml"
        if not input_path.exists():
            raise FileNotFoundError(f"Chưa có {sent_xml} hoặc {input_path}; chạy bước 'sentence' trước")
        sentences = load_sentences(layout, layer)

    inputs = [input_path]
    deps.check_fresh(layout, f"clean_sentence:{layer}", inputs, [out_xml], args.force,
                     f"{out_xml} đã tồn tại; dùng --force để làm sạch lại")

    cleaned_sents, dropped = clean_sentences(sentences, layer)
    save_sentences(layout, layer, cleaned_sents)
    atomic_write_text(out_dir / f"{layer}.txt", "".join(s.text + "\n" for s in cleaned_sents))

    if dropped:
        drop_tsv = out_dir / f"{layer}.sentence_dropped.tsv"
        lines = ["id\tpage\treason\ttext"] + [
            f"{i}\t{p}\t{r}\t{t}" for i, p, r, t in dropped
        ]
        atomic_write_text(drop_tsv, "\n".join(lines) + "\n")

    deps.record(layout, f"clean_sentence:{layer}", inputs, [out_xml])
    MetadataStore(layout).update(f"clean_sentence_{layer}", {
        "input": layout.rel(input_path),
        "output": layout.rel(out_xml),
        "sentences": len(cleaned_sents),
        "dropped": len(dropped),
    })
    log.info("[%s] Đã làm sạch %d câu cấp câu -> %s", layer, len(cleaned_sents), out_xml)
    return out_xml


def main(argv: list[str] | None = None) -> int:
    parser = create_parser()
    args = parse_args(parser, argv)
    try:
        run(args)
    except (OSError, ValueError, FileNotFoundError) as exc:
        log.error("%s", exc)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
