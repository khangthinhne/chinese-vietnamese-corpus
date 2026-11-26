"""Phân đoạn cấp câu (Sentence Segmentation) từ các đoạn/dòng đã phân đoạn lớp hoặc làm sạch cấu trúc.

Đầu vào:
    05_cleaning/structural/<layer>.xml (nếu đã qua bước làm sạch cấu trúc)
    hoặc 04_segmentation/layers/<layer>.xml (đoạn/dòng phân đoạn lớp)
    hoặc 02_extraction/<layer>.xml (dữ liệu cũ)

Đầu ra:
    04_segmentation/sentences/<layer>.xml (bản ghi câu phân đoạn độc lập, KHÔNG ghi đè paragraph-level XML)
    05_cleaning/<layer>.xml (bản câu đã làm sạch câu)

Yêu cầu:
- Tách câu Hán bằng dấu câu Hán phù hợp.
- Tách câu Việt / phiên âm bằng dấu câu phù hợp, xử lý viết tắt/số/ngoặc.
- Không coi mỗi dòng OCR là một câu.
- Giữ nguyên document_id, paragraph_id / segment_id, source_id, số trang, text gốc.
- Hỗ trợ câu trải qua nhiều dòng OCR và nhiều trang.
- Giữ nguyên khả năng truy vết từ câu về segment OCR gốc.
- Đánh cờ các câu không chắc hoặc quá dài thay vì âm thầm sửa đổi.
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import sys
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from src.cleaning.model import (
    HAN_CLOSERS,
    HAN_TERMINATORS,
    VI_ABBREV,
    VI_CLOSERS,
    VI_OPENERS,
    CleanConfig,
    RawLine,
    has_flag,
)
from src.common import deps
from src.common.corpus import (
    DataLayout,
    MetadataStore,
    base_parser,
    cjk_ratio,
    parse_args,
)
from src.common.sentences import Sentence, atomic_write_text, save_sentences
from src.segmentation import serialization

log = logging.getLogger("sentence_segmentation")


@dataclass
class SentenceConfig:
    layer: str = "han"
    split: str = "punct"                 # "punct" | "line" | "auto" (auto chỉ có nghĩa với lớp han)
    split_colon: bool = False
    abbrev: set[str] = field(default_factory=lambda: set(VI_ABBREV))
    max_han_len: int = 200
    max_vi_len: int = 600


def build_stream(lines: list[RawLine], layer: str) -> tuple[str, list[int]]:
    """Nối các dòng giữ lại. Trả về (text, owner) với owner[k] = chỉ số dòng của ký tự k (-1 = dấu nối)."""
    sep = "" if layer == "han" else " "
    chars: list[str] = []
    owner: list[int] = []
    for idx, ln in enumerate(lines):
        if idx and sep and not has_flag(ln, "dehyphenated"):
            # Nếu dòng trước kết thúc bằng dấu nối từ dehyphenated, không thêm cách
            prev = lines[idx - 1]
            if not has_flag(prev, "dehyphenated"):
                chars.append(sep)
                owner.append(-1)
        chars.extend(ln.text)
        owner.extend([idx] * len(ln.text))
    return "".join(chars), owner


def split_han(text: str) -> list[tuple[int, int]]:
    """Tách câu Hán dựa trên HAN_TERMINATORS và HAN_CLOSERS."""
    spans: list[tuple[int, int]] = []
    start = 0
    i = 0
    n = len(text)
    while i < n:
        if text[i] in HAN_TERMINATORS:
            j = i + 1
            while j < n and text[j] in HAN_CLOSERS:
                j += 1
            spans.append((start, j))
            start, i = j, j
        else:
            i += 1
    if start < n:
        spans.append((start, n))
    return spans


def _token_before(text: str, pos: int) -> str:
    j = pos
    while j > 0 and not text[j - 1].isspace():
        j -= 1
    return text[j:pos].lstrip(VI_OPENERS)


def split_vi(text: str, cfg: SentenceConfig | CleanConfig) -> list[tuple[int, int]]:
    """Tách câu tiếng Việt / âm dựa trên dấu câu, bảo vệ viết tắt và số."""
    spans: list[tuple[int, int]] = []
    start = 0
    n = len(text)
    i = 0
    abbrevs = getattr(cfg, "abbrev", VI_ABBREV) or VI_ABBREV
    split_colon = getattr(cfg, "split_colon", False)
    while i < n:
        c = text[i]
        cut = False
        if c == ";" or (c == ":" and split_colon):
            cut = True
        elif c in ".?!…":
            j = i + 1
            while j < n and text[j] in ".?!…":          # "..." / "?!"
                j += 1
            k = j
            while k < n and text[k] in VI_CLOSERS:
                k += 1
            m = k
            while m < n and text[m].isspace():
                m += 1
            at_end = m >= n
            next_ok = at_end or text[m].isupper() or text[m] in VI_OPENERS
            if c == "." and j == i + 1:                    # dấu chấm đơn: kiểm tra viết tắt/số thứ tự
                tok = _token_before(text, i).lower()
                if (tok in abbrevs or tok.isdigit() or (len(tok) == 1 and tok.isalpha())
                        or "." in tok):
                    next_ok = False
                elif not next_ok and tok in {"lễ", "nam", "thư", "bạch", "thấu"}:
                    pre_phrase = text[max(0, i - 25):i].lower()
                    if any(w in pre_phrase for w in ("kính lễ", "hòa nam", "kính bạch", "kính thư", "kính cẩn")):
                        next_ok = True
            if next_ok:
                i = k - 1
                cut = True
        if cut:
            spans.append((start, i + 1))
            start = i + 1
            while start < n and text[start].isspace():    # dấu cách giữa hai câu: bỏ ở cả hai vế
                start += 1
        i += 1
    if start < n:
        spans.append((start, n))
    return spans


def _document_id(line_id: str) -> str:
    parts = line_id.rsplit(".", 2)
    return parts[0] if len(parts) == 3 else line_id


# Dấu kết câu dùng để quyết định chế độ tách của --split auto (lớp han).
AUTO_HAN_TERMINATORS = "。！？；"
AUTO_LINES_PER_TERMINATOR = 4            # < 1 dấu kết câu mỗi 4 dòng -> thơ không chấm câu -> tách theo dòng
HAN_AM_MISMATCH = 0.30                   # cảnh báo khi số câu han/am của một tài liệu lệch > 30%


AM_VERSE_MAX_SYLLABLES = 12            # dòng phiên âm thơ: <= 12 âm tiết
AM_VERSE_SHARE = 0.75                    # >= 75% dòng của tài liệu có dạng câu thơ -> tách theo dòng


def default_split(layer: str) -> str:
    return {"han": "auto", "am": "auto", "tho": "line"}.get(layer, "punct")


def choose_han_split(lines: list[RawLine]) -> str:
    """'line' nếu tài liệu có < 1 dấu kết câu (。！？；) mỗi 4 dòng, ngược lại 'punct'.

    Thơ Hán in không chấm câu: tách theo dấu câu sẽ gộp cả bài thành một câu, trong khi phiên âm
    được tách theo từng câu thơ -> dóng han-am hỏng. Văn xuôi có chấm câu thì tách theo dấu câu."""
    if not lines:
        return "punct"
    terminators = sum(ln.text.count(c) for ln in lines for c in AUTO_HAN_TERMINATORS)
    return "line" if terminators * AUTO_LINES_PER_TERMINATOR < len(lines) else "punct"


def choose_am_split(lines: list[RawLine]) -> str:
    """'line' nếu tài liệu phiên âm là văn vần: >= 75% dòng ngắn (<= 12 âm tiết) và kết thúc bằng dấu câu.

    Phiên âm thơ có sách đặt dấu phẩy sau câu lẻ, dấu chấm sau câu chẵn (CPO_20): tách theo dấu câu ra từng CẶP
    câu thơ, trong khi chữ Hán (không chấm câu) tách theo dòng -> số câu lệch đúng 2:1. Văn xuôi (CPO_18) giữ
    'punct'. Dòng tiêu đề viết hoa không tính."""
    body = [ln.text.strip() for ln in lines if ln.text.strip() and not ln.text.strip().isupper()]
    if not body:
        return "punct"
    verse = sum(1 for t in body if len(re.findall(r"[^\W\d_]+", t)) <= AM_VERSE_MAX_SYLLABLES
                and t[-1] in ",.;:!?")
    return "line" if verse >= AM_VERSE_SHARE * len(body) else "punct"


def sentence_count_mismatches(han: dict[str, int], am: dict[str, int],
                              threshold: float = HAN_AM_MISMATCH) -> list[tuple[str, int, int]]:
    """[(document_id, số câu han, số câu am)] cho tài liệu có cả hai lớp và lệch > threshold
    (|han - am| / max(han, am))."""
    out = []
    for doc in sorted(set(han) & set(am)):
        h, a = han[doc], am[doc]
        if max(h, a) and abs(h - a) / max(h, a) > threshold:
            out.append((doc, h, a))
    return out


def make_sentences(work_id: str, layer: str, lines: list[RawLine],
                   cfg: SentenceConfig | CleanConfig,
                   decisions: dict[str, str] | None = None,
                   ) -> tuple[list[Sentence], list[tuple[str, int, str, str]]]:
    """Sinh các câu Sentence độc lập từ danh sách dòng/segment.

    `decisions` (nếu truyền vào) nhận chế độ tách thực tế theo document_id khi split='auto'.
    Trả về (sentences, dropped_rows).
    """
    dropped: list[tuple[str, int, str, str]] = []
    sentences: list[Sentence] = []
    split_mode = getattr(cfg, "split", None) or default_split(layer)
    if split_mode == "auto" and layer not in ("han", "am"):
        split_mode = "punct"                 # auto chỉ quyết định cho chữ Hán và phiên âm
    max_len = 200 if layer == "han" else 600

    def emit(text: str, idxs: list[int]) -> None:
        text = text.strip()
        if not text:
            return
        if layer == "han":
            ok = sum(1 for c in text.replace("[MASK]", "口") if cjk_ratio(c) > 0) >= 2   # [MASK] = 1 chữ
        else:
            ok = any(ch.isalnum() for ch in text)
        owning_lines = [lines[k] for k in idxs]
        segment_ids = sorted({ln.id for ln in owning_lines})
        pages = sorted({ln.page for ln in owning_lines})

        # Thu thập source_ids (ID dòng OCR gốc)
        source_ids_set = set()
        for ln in owning_lines:
            sids = getattr(ln, "source_ids", None)
            if sids:
                source_ids_set.update(sids)
        source_ids = sorted(source_ids_set)
        flags: list[str] = []
        if not source_ids:
            flags.append("missing_provenance")

        raw_texts = [getattr(ln, "raw_text", "") or ln.text for ln in owning_lines]
        raw_joined = ("" if layer == "han" else " ").join(t for t in raw_texts if t)

        if not ok:
            dropped.append((",".join(segment_ids), pages[0] if pages else 0, "too_short", text))
            return
        if len(pages) > 1:
            flags.append("cross_page")
        if layer == "han" and text[-1] not in HAN_TERMINATORS + HAN_CLOSERS:
            flags.append("no_terminal_punct")
        if layer != "han" and not re.search(r"[.?!…;:\"”’')»\]]$", text):
            flags.append("no_terminal_punct")
        if len(text) > max_len:
            flags.append("long")

        # Kế thừa các flag từ các dòng thành phần (ví dụ: uncertain, review)
        for ln in owning_lines:
            for f in ln.flags:
                if f.startswith("uncertain") and "uncertain" not in flags:
                    flags.append("uncertain")
                elif f.startswith("review") and f not in flags:
                    flags.append(f)

        doc_ids = {_document_id(ln.id) for ln in owning_lines}
        document_id = next(iter(doc_ids)) if len(doc_ids) == 1 else ""

        sent_id = f"{work_id}.{layer}.{len(sentences) + 1:05d}"
        sentences.append(Sentence(
            id=sent_id,
            text=text,
            pages=pages,
            src_ids=segment_ids,
            flags=flags,
            document_id=document_id,
            segment_ids=segment_ids,
            source_ids=source_ids,
            raw_text=raw_joined,
        ))

    if split_mode == "line":
        for k, ln in enumerate(lines):
            emit(ln.text, [k])
        return sentences, dropped

    # Nhóm theo ranh giới tài liệu để không bao giờ nối câu vượt tài liệu
    runs: list[tuple[int, int]] = []
    start = 0
    for k in range(1, len(lines) + 1):
        if k == len(lines) or _document_id(lines[k].id) != _document_id(lines[start].id):
            runs.append((start, k))
            start = k

    for start, end in runs:
        group = lines[start:end]
        if split_mode == "auto":
            mode = choose_han_split(group) if layer == "han" else choose_am_split(group)
            if decisions is not None:
                decisions[_document_id(group[0].id)] = mode
            if mode == "line":
                for k in range(start, end):
                    emit(lines[k].text, [k])
                continue
        text, owner = build_stream(group, layer)
        spans = split_han(text) if layer == "han" else split_vi(text, cfg)
        for a, b in spans:
            idxs = sorted({start + o for o in owner[a:b] if o >= 0})
            if idxs:
                emit(text[a:b], idxs)

    return sentences, dropped


def read_segment_input(layout: DataLayout, layer: str) -> list[RawLine]:
    """Đọc dữ liệu đoạn/dòng để tách câu theo thứ tự ưu tiên:
    1. 05_cleaning/structural/<layer>.xml (đoạn đã làm sạch cấu trúc)
    2. 04_segmentation/layers/<layer>.xml (đoạn phân đoạn lớp)
    3. 02_extraction/<layer>.xml (dữ liệu thô trích xuất cũ)
    """
    struct_xml = layout.cleaning_structural(layer)
    if struct_xml.exists():
        rows = serialization.read_structural_layer(struct_xml)
        return [RawLine(r["id"], int(r["page"]), r.get("text", ""),
                        raw_text=r.get("raw_text") or r.get("text", ""),
                        flags=list(r.get("flags") or []),
                        source_ids=list(r.get("source_ids") or []),
                        document_id=r.get("document_id") or "")
                for r in rows]

    seg_xml = layout.segmentation_layer(layer)
    if seg_xml.exists():
        rows = serialization.read_layer(seg_xml)
        return [RawLine(r["id"], int(r["page"]), r.get("text", ""),
                        raw_text=r.get("text", ""),
                        flags=list(r.get("flags") or []),
                        source_ids=list(r.get("source_ids") or []),
                        document_id=r.get("document_id") or "")
                for r in rows]

    extract_xml = layout.layer_xml(layer)
    if extract_xml.exists():
        from src.cleaning.pipeline import read_raw_lines
        return read_raw_lines(extract_xml)

    raise FileNotFoundError(
        f"Chưa có dữ liệu đầu vào cho lớp '{layer}'; cần chạy 'segment' hoặc 'clean' trước "
        f"(tìm trong {struct_xml}, {seg_xml}, {extract_xml})"
    )


def han_am_mismatches(layout: DataLayout, layer: str,
                      sentences: list[Sentence]) -> list[tuple[str, int, int]]:
    """So số câu han/am theo tài liệu khi vừa tách xong một trong hai lớp và lớp kia đã có."""
    if layer not in ("han", "am"):
        return []
    other = "am" if layer == "han" else "han"
    path = layout.segmentation_sentence(other)
    if not path.exists():
        return []
    mine = Counter(s.document_id for s in sentences)
    theirs = Counter(r.get("document_id") or "" for r in serialization.read_sentences(path))
    han, am = (mine, theirs) if layer == "han" else (theirs, mine)
    return sentence_count_mismatches(dict(han), dict(am))


def create_parser():
    p = base_parser("Phân đoạn cấp câu độc lập -> 04_segmentation/sentences/<layer>.xml")
    p.add_argument("--split", choices=["auto", "punct", "line"],
                   help="Mặc định: auto cho han (theo từng tài liệu: < 1 dấu 。！？； mỗi 4 dòng thì tách "
                        "theo dòng, ngược lại theo dấu câu), auto cho am (văn vần: >= 75% dòng <= 12 âm tiết có dấu "
                        "câu cuối dòng thì tách theo dòng), line cho tho, punct cho lớp khác")
    p.add_argument("--split-colon", action="store_true",
                   help="Coi ':' là ranh giới câu (Việt)")
    p.add_argument("--abbrev", help="Viết tắt Việt bổ sung, phân cách dấu phẩy")
    return p


def run(args) -> Path:
    layer = args.layer
    layout = DataLayout(Path(args.data_root), args.work_id)
    sent_dir = layout.segmentation_sentences
    layout.ensure(sent_dir)
    out_xml = layout.segmentation_sentence(layer)

    cfg = SentenceConfig(
        layer=layer,
        split=args.split or default_split(layer),
        split_colon=getattr(args, "split_colon", False),
        abbrev=set(VI_ABBREV) | {a.strip().lower() for a in (getattr(args, "abbrev", "") or "").split(",") if a.strip()},
    )

    raw_lines = read_segment_input(layout, layer)
    inputs = [layout.cleaning_structural(layer) if layout.cleaning_structural(layer).exists()
              else layout.segmentation_layer(layer)]
    if getattr(args, "config", None):
        inputs.append(Path(args.config))

    deps.check_fresh(layout, f"sentence:{layer}", inputs, [out_xml], args.force,
                     f"{out_xml} đã tồn tại; dùng --force để chạy lại")

    decisions: dict[str, str] = {}
    sentences, dropped = make_sentences(args.work_id, layer, raw_lines, cfg, decisions)
    if decisions:
        modes = Counter(decisions.values())
        log.info("[%s] split=auto: %d tài liệu tách theo dòng, %d theo dấu câu", layer,
                 modes.get("line", 0), modes.get("punct", 0))

    # Ghi bản ghi câu vào 04_segmentation/sentences/<layer>.xml
    serialization.write_sentences(out_xml, args.work_id, layer, [
        {
            "id": s.id,
            "document_id": s.document_id,
            "pages": s.pages,
            "segment_ids": s.segment_ids,
            "source_ids": s.source_ids,
            "flags": s.flags,
            "raw_text": s.raw_text,
            "text": s.text,
        }
        for s in sentences
    ])

    # Chạy sentence-level cleaning để tạo 05_cleaning/<layer>.xml
    from src.cleaning.sentence_cleaner import clean_sentences
    cleaned_sents, dropped_clean = clean_sentences(sentences, layer)
    save_sentences(layout, layer, cleaned_sents)
    atomic_write_text(layout.cleaning_root / f"{layer}.txt", "".join(s.text + "\n" for s in cleaned_sents))

    deps.record(layout, f"sentence:{layer}", inputs, [out_xml, layout.cleaning_root / f"{layer}.xml"])

    stats = {
        "raw_lines": len(raw_lines),
        "sentences": len(sentences),
        "cleaned_sentences": len(cleaned_sents),
        "cross_page": sum("cross_page" in s.flags for s in sentences),
        "no_terminal_punct": sum("no_terminal_punct" in s.flags for s in sentences),
        "long": sum("long" in s.flags for s in sentences),
        "dropped": len(dropped) + len(dropped_clean),
    }
    if decisions:
        stats["split_auto"] = dict(Counter(decisions.values()))
    mismatches = han_am_mismatches(layout, layer, sentences)
    for doc, h, a in mismatches:
        log.warning("[%s] Tài liệu %s: số câu han=%d, am=%d lệch > %d%%; kiểm tra tách câu trước khi dóng",
                    layer, doc or "<trống>", h, a, round(HAN_AM_MISMATCH * 100))
    if layer in ("han", "am"):
        stats["han_am_mismatch_documents"] = [d for d, _, _ in mismatches]
    MetadataStore(layout).update(f"sentence_{layer}", {
        "output": layout.rel(out_xml),
        "cleaned_output": layout.rel(layout.cleaning_root / f"{layer}.xml"),
        "stats": stats,
    })
    log.info("[%s] Tách được %d câu -> %s (đã làm sạch cấp câu -> %s)",
             layer, len(sentences), out_xml, layout.cleaning_root / f"{layer}.xml")
    return out_xml


def main(argv: list[str] | None = None) -> int:
    parser = create_parser()
    args = parse_args(parser, argv)
    try:
        run(args)
    except deps.UpToDate as exc:          # đầu ra còn mới: bỏ qua, không phải lỗi
        log.info("%s", exc)
        return 0
    except (OSError, ValueError, FileNotFoundError) as exc:
        log.error("%s", exc)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
