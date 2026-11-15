"""
02_extraction/<layer>.xml -> làm sạch -> tách câu -> 03_cleaned/<layer>.{jsonl,txt,dropped.tsv}

INPUT
    --work-id CPO_18 --layer han|am|nghia|tho  [--data-root data]
    --split punct|line     punct: tách theo dấu câu (mặc định han/am/nghia); line: mỗi dòng = 1 đơn vị
                           (mặc định cho tho)
    --split-colon          Việt: coi ':' là ranh giới câu (mặc định tắt vì hay cắt nhầm lời dẫn)
    --drop-pattern REGEX   (lặp lại được) bỏ dòng khớp regex, vd tiêu đề chạy "^Phần II"
    --no-heading-heuristic tắt luật tự bỏ tiêu đề/tên tác giả ngắn không dấu câu
    --keep-markers         không xoá chỉ mục chú thích [1], (1), ①
    --abbrev tp,ts,...     thêm chữ viết tắt Việt không được coi là hết câu khi có dấu chấm

    --start-after REGEX   chỉ lấy nội dung SAU dòng khớp regex này (không gồm dòng khớp);
                          cắt theo TOÀN BỘ danh sách dòng, nên hoạt động cả khi nội dung
                          trải qua nhiều trang
    --end-before REGEX    ngừng lấy khi gặp dòng khớp regex này (không gồm dòng khớp)

OUTPUT (data/<WORK_ID>/03_cleaned/)
    <layer>.jsonl        mỗi dòng 1 Sentence: id, text, pages, src_ids, flags
    <layer>.txt          mỗi câu 1 dòng (bản "văn bản sạch" theo guideline)
    <layer>.dropped.tsv  mọi dòng bị loại: id, trang, lý do, nội dung  -> NGƯỜI PHẢI DUYỆT lại
                         (gồm cả dòng ngoài đoạn cắt bởi --start-after/--end-before)
    02_extraction/metadata.json -> layers["<layer>_cleaned"]

Các việc làm ở đây (đúng bước 5-6 của guideline):
    - bỏ chú thích cuối trang / chỉ mục chú thích / số trang / tiêu đề / dòng không đúng chữ viết
    - sửa dấu câu OCR (Hán: , . ; ? ! -> ， 。 ； ？ ！), bỏ ký tự rác, khoảng trắng thừa
    - GHÉP CÂU BỊ CẮT QUA TRANG: toàn bộ dòng giữ lại của lớp được nối thành MỘT dòng chảy
      rồi mới tách câu; câu nào trải trên >1 trang có pages=[205, 206] và cờ cross_page.
Không đổi nghĩa, không sửa tên riêng/niên hiệu/số theo nghĩa.

Ví dụ:
    python -m src.preprocessing.clean --work-id CPO_18 --layer han
    python -m src.preprocessing.clean --work-id CPO_18 --layer nghia --drop-pattern "^Phần II"
    python -m src.preprocessing.clean --work-id CPO_18 --layer am \
        --start-after "^Phiên âm" --end-before "^Dịch nghĩa"
"""

from __future__ import annotations

import logging
import re
import sys
import unicodedata
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path

from src.common.corpus import (LAYERS, DataLayout, MetadataStore, base_parser, cjk_ratio,
                               normalize_line, parse_args)
from src.common.sentences import (Sentence, atomic_write_text, cleaned_dir, save_sentences)

log = logging.getLogger("clean")

# --------------------------------------------------------------------------- #
# Cấu hình mặc định (đổi bằng CLI/config, không sửa logic)
# --------------------------------------------------------------------------- #
HAN_TERMINATORS = "。！？；﹖﹕﹒"
HAN_CLOSERS = "」』”’）)》〉"
HAN_PUNCT = "，。！？；：、"                 # dấu ngắt câu/ý; ngoặc, nháy, gạch KHÔNG tính khi nhận diện tiêu đề
ASCII_TO_HAN = {",": "，", ".": "。", ";": "；", ":": "：", "?": "？", "!": "！", "、": "、"}
HAN_JUNK = set("|｜*＊·•▪■□◇◆○●~`^_=+\\/<>[]{}")

VI_ABBREV = {"tp", "ts", "pgs", "gs", "ths", "bs", "ks", "mr", "mrs", "dr", "tr", "tt", "vv",
             "v.v", "vd", "tk", "tl", "nxb", "q", "p", "đ", "st", "sđd", "nt", "cn", "tcn", "scn"}
VI_OPENERS = "\"“‘'(«["
VI_CLOSERS = "\"”’')»]"

# số trang đứng một mình: "205", "- 205 -"
DEFAULT_DROP = [r"^[\-–—\s]*\d{1,4}[\-–—\s]*$"]

MARKER_RE = re.compile(r"\[\d{1,3}\]|［\d{1,3}］|\(\d{1,3}\)|（\d{1,3}）|[①-⑳㉑-㉟]")
LEADING_NUM_RE = re.compile(r"^\s*\d{1,3}\s*[·.．、)]\s*")


@dataclass
class CleanConfig:
    layer: str
    split: str = "punct"
    split_colon: bool = False
    drop_patterns: list[str] = field(default_factory=lambda: list(DEFAULT_DROP))
    heading_heuristic: bool = True
    strip_markers: bool = True
    min_cjk: float = 0.5               # tỉ lệ chữ Hán tối thiểu của một dòng Hán
    max_heading_han: int = 24          # dòng Hán không dấu câu, ngắn hơn ngưỡng này = tiêu đề/tên người viết
    max_heading_vi: int = 90
    abbrev: set[str] = field(default_factory=lambda: set(VI_ABBREV))
    han_strip_latin: bool = True
    start_after: str | None = None     # regex ranh giới đầu đoạn: chỉ lấy nội dung SAU dòng khớp
    end_before: str | None = None      # regex ranh giới cuối đoạn: ngừng lấy trước dòng khớp


@dataclass
class RawLine:
    id: str
    page: int
    text: str
    is_last_on_page: bool = False


# --------------------------------------------------------------------------- #
# 0. Cắt đoạn theo ranh giới tiêu đề (start_after / end_before)
# --------------------------------------------------------------------------- #
@dataclass
class SectionInfo:
    """Kết quả cắt đoạn: báo cho metadata + các dòng bị loại kèm lý do."""
    status: str                                  # unbounded | ok | incomplete_end
    start_pattern: str | None = None
    end_pattern: str | None = None
    start_found: bool = False
    end_found: bool = False
    first_page: int | None = None                # trang của dòng được chọn đầu/cuối
    last_page: int | None = None
    n_selected: int = 0
    excluded: list[tuple[RawLine, str]] = field(default_factory=list)  # (dòng, before_start|after_end)

    def as_dict(self) -> dict:
        d = {k: v for k, v in self.__dict__.items() if k != "excluded"}
        d["n_excluded"] = len(self.excluded)
        return d


def compile_boundary(pattern: str | None, name: str) -> re.Pattern | None:
    """Biên dịch regex ranh giới một lần; regex sai = lỗi cấu hình, không nuốt lặng lẽ."""
    if pattern is None:
        return None
    try:
        return re.compile(pattern, re.IGNORECASE | re.UNICODE)
    except re.error as exc:
        raise ValueError(f"Regex {name} không hợp lệ: {pattern!r} ({exc})") from exc


def extract_section(lines: list[RawLine], start_after: str | None = None,
                    end_before: str | None = None
                    ) -> tuple[list[RawLine], SectionInfo]:
    """Cắt đoạn theo ranh giới tiêu đề, chạy trên TOÀN BỘ danh sách dòng (qua trang mượt).

    Trạng thái: trước start -> thu thập -> gặp end thì dừng. Dòng khớp ranh giới KHÔNG
    được chọn; phần bị loại trước start / từ end trở đi trả về trong info.excluded.
    Không hiệu chỉnh text, không đổi ID/trang/is_last_on_page.
    """
    start_re = compile_boundary(start_after, "start_after")
    end_re = compile_boundary(end_before, "end_before")

    print(f"extract_section: start_after={start_after!r}, end_before={end_before!r}, ")
    info = SectionInfo(status="unbounded", start_pattern=start_after, end_pattern=end_before)
    if start_re is None and end_re is None:
        info.n_selected = len(lines)
        if lines:
            info.first_page, info.last_page = lines[0].page, lines[-1].page
        return list(lines), info

    selected: list[RawLine] = []
    collecting = start_re is None          # không có start = thu thập từ dòng đầu
    info.start_found = start_re is None
    end_idx: int | None = None
    for i, ln in enumerate(lines):
        probe = unicodedata.normalize("NFC", ln.text).strip()
        # print(f"line {i}: {probe!r} (page {ln.page})")
        if not collecting:
            if start_re.search(probe):     # dòng tiêu đề start: ghi nhận, lấy từ dòng kế
                print(f"Probe start_after match: {probe!r} (page {ln.page})")
                collecting = True
                info.start_found = True
                info.excluded.append((ln, "start_heading"))
            else:
                info.excluded.append((ln, "before_start"))
            continue
        if end_re is not None and end_re.search(probe):
            info.end_found = True          # dòng tiêu đề end: dừng tại đây
            info.excluded.append((ln, "end_heading"))
            end_idx = i
            break
        selected.append(ln)
    if not info.start_found:
        raise ValueError(
            f"start_after={start_after!r} không khớp dòng nào trong {len(lines)} dòng; "
            f"kiểm tra regex/trang OCR (trang: "
            f"{sorted({ln.page for ln in lines})[:5]}...)")
    info.status = "ok"
    if end_re is not None and not info.end_found:
        info.status = "incomplete_end"     # cảnh báo lớn, không nuốt lặng lẽ
        log.warning("end_before=%r không khớp dòng nào; lấy tới hết dữ liệu "
                    "(kiểm tra regex/phạm vi trang OCR)", end_before)
    elif end_idx is not None:
        info.excluded.extend((ln, "after_end") for ln in lines[end_idx + 1:])
    info.n_selected = len(selected)
    if selected:
        info.first_page, info.last_page = selected[0].page, selected[-1].page
    if not selected:
        raise ValueError(f"Đoạn cắt rỗng (start_after={start_after!r}, end_before={end_before!r}): "
                         "không còn dòng nào giữa hai ranh giới")
    return selected, info


# --------------------------------------------------------------------------- #
# 1. Đọc XML thô
# --------------------------------------------------------------------------- #
def read_raw_lines(xml_path: Path) -> list[RawLine]:
    if not xml_path.exists():
        raise FileNotFoundError(f"Chưa có {xml_path}; chạy extraction/OCR cho lớp này trước")
    root = ET.parse(xml_path).getroot()
    out: list[RawLine] = []
    for page in root.iter("PAGE"):
        page_no = int(page.get("SOURCE_PAGE", "0"))
        units = [u for u in page if u.get("ID")]
        for i, u in enumerate(units):
            out.append(RawLine(u.get("ID"), page_no, u.text or "", is_last_on_page=(i == len(units) - 1)))
    return out


# --------------------------------------------------------------------------- #
# 2. Chuẩn hoá từng dòng
# --------------------------------------------------------------------------- #
def clean_han_line(text: str, cfg: CleanConfig) -> str:
    text = unicodedata.normalize("NFC", text)
    text = LEADING_NUM_RE.sub("", text)
    if cfg.strip_markers:
        text = MARKER_RE.sub("", text)
    text = "".join(ASCII_TO_HAN.get(c, c) for c in text)
    kept = []
    for c in text:
        if c.isspace() or c in HAN_JUNK:
            continue
        if cfg.han_strip_latin and c.isascii():          # chữ/số Latin lẫn trong dòng Hán = rác OCR
            continue
        kept.append(c)
    text = "".join(kept)
    text = re.sub(r"([，。！？；：、])\1+", r"\1", text)   # dấu lặp do OCR
    return text.strip("，、：—–…")                         # dấu ngắt/gạch ở biên dòng vô nghĩa


def clean_vi_line(text: str, cfg: CleanConfig) -> str:
    text = normalize_line(text)
    if cfg.strip_markers:
        text = MARKER_RE.sub("", text)
    text = text.replace("|", " ").replace("¦", " ")
    text = re.sub(r"\s+([,.;:?!])", r"\1", text)            # bỏ khoảng trắng trước dấu câu
    text = re.sub(r"\s{2,}", " ", text)
    return text.strip()


def is_parenthesized(text: str) -> bool:
    pairs = {"(": ")", "（": "）", "[": "]", "［": "］", "(":"﹚", " ( ":" )"}
    t = text.strip()
    return len(t) > 2 and t[0] in pairs and t[-1] == pairs[t[0]]


def drop_reason(raw: str, cleaned: str, line: RawLine, cfg: CleanConfig, prev_complete: bool = True) -> str | None:
    if not cleaned:
        return "empty"
    for pat in cfg.drop_patterns:
        if re.search(pat, raw) or re.search(pat, cleaned):
            return f"pattern:{pat}"
    if cfg.layer == "han":
        if cjk_ratio(cleaned) < cfg.min_cjk:
            return "not_han"
        if is_parenthesized(raw.strip()):
            return "footnote"
        # tiêu đề/tên tác giả: ngắn, không dấu câu nào, đứng SAU một câu đã trọn (hoặc đầu văn bản),
        # và KHÔNG phải dòng cuối trang (có thể là đầu một câu còn dang dở sang trang sau)
        if (cfg.heading_heuristic and prev_complete and not line.is_last_on_page
                and len(cleaned) <= cfg.max_heading_han
                and not re.search(f"[{re.escape(HAN_PUNCT)}]", cleaned)):
            return "heading"
    else:
        if not any(c.isalnum() for c in cleaned):
            return "no_text"
        if cjk_ratio(cleaned) > 0.1:
            return "not_vi"
        if raw.lstrip().startswith(("*", "＊")) or re.match(r"^\s*\[\d+\]", raw) or is_parenthesized(cleaned):
            return "footnote"
        if (cfg.heading_heuristic and re.match(r"^\d{1,3}\s*[.)]\s+\S", cleaned)
                and not re.search(r"[.?!;…]$", cleaned) and len(cleaned) <= cfg.max_heading_vi):
            return "heading"
    return None


# --------------------------------------------------------------------------- #
# 3. Tách câu trên dòng chảy đã nối (xử lý luôn câu qua trang)
# --------------------------------------------------------------------------- #
def build_stream(lines: list[RawLine], layer: str) -> tuple[str, list[int]]:
    """Nối các dòng giữ lại. Trả về (text, owner) với owner[k] = chỉ số dòng của ký tự k (-1 = dấu nối)."""
    sep = "" if layer == "han" else " "
    chars: list[str] = []
    owner: list[int] = []
    for idx, ln in enumerate(lines):
        if idx and sep:
            chars.append(sep)
            owner.append(-1)
        chars.extend(ln.text)
        owner.extend([idx] * len(ln.text))
    return "".join(chars), owner


def split_han(text: str) -> list[tuple[int, int]]:
    spans, start, i, n = [], 0, 0, len(text)
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


def split_vi(text: str, cfg: CleanConfig) -> list[tuple[int, int]]:
    spans, start, n = [], 0, len(text)
    i = 0
    while i < n:
        c = text[i]
        cut = False
        if c == ";" or (c == ":" and cfg.split_colon):
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
                if (tok in cfg.abbrev or tok.isdigit() or (len(tok) == 1 and tok.isalpha())
                        or "." in tok):
                    next_ok = False
            if next_ok:
                i = k - 1
                cut = True
        if cut:
            spans.append((start, i + 1))
            start = i + 1
        i += 1
    if start < n:
        spans.append((start, n))
    return spans


def make_sentences(work_id: str, layer: str, lines: list[RawLine], cfg: CleanConfig
                   ) -> tuple[list[Sentence], list[tuple[str, int, str, str]]]:
    """Trả về (sentences, dropped_sentence_rows)."""
    dropped: list[tuple[str, int, str, str]] = []
    sentences: list[Sentence] = []

    def emit(text: str, idxs: list[int]) -> None:
        text = text.strip()
        if layer == "han":
            ok = sum(1 for c in text if cjk_ratio(c) > 0) >= 2
        else:
            ok = any(ch.isalnum() for ch in text)
        ids = sorted({lines[k].id for k in idxs})
        pages = sorted({lines[k].page for k in idxs})
        if not ok:
            dropped.append((",".join(ids), pages[0] if pages else 0, "too_short", text))
            return
        flags = []
        if len(pages) > 1:
            flags.append("cross_page")
        if layer == "han" and text[-1] not in HAN_TERMINATORS + HAN_CLOSERS:
            flags.append("no_terminal_punct")
        if layer != "han" and not re.search(r"[.?!…;:\"”’')»\]]$", text):
            flags.append("no_terminal_punct")
        if len(text) > (200 if layer == "han" else 600):
            flags.append("long")
        sentences.append(Sentence(f"{work_id}.{layer}.{len(sentences) + 1:05d}", text, pages, ids, flags))

    if cfg.split == "line":
        for k, ln in enumerate(lines):
            emit(ln.text, [k])
        return sentences, dropped

    text, owner = build_stream(lines, layer)
    spans = split_han(text) if layer == "han" else split_vi(text, cfg)
    for a, b in spans:
        idxs = sorted({o for o in owner[a:b] if o >= 0})
        if idxs:
            emit(text[a:b], idxs)
    return sentences, dropped


# --------------------------------------------------------------------------- #
# 4. Điều phối một lớp
# --------------------------------------------------------------------------- #
def clean_layer(work_id: str, layer: str, raw_lines: list[RawLine], cfg: CleanConfig):
    kept: list[RawLine] = []
    dropped_rows: list[tuple[str, int, str, str]] = []
    for ln in raw_lines:
        cleaned = clean_han_line(ln.text, cfg) if layer == "han" else clean_vi_line(ln.text, cfg)
        prev_complete = not kept or kept[-1].text[-1] in HAN_TERMINATORS + HAN_CLOSERS
        reason = drop_reason(ln.text, cleaned, ln, cfg, prev_complete)
        if reason:
            if reason != "empty":
                dropped_rows.append((ln.id, ln.page, reason, ln.text))
            continue
        kept.append(RawLine(ln.id, ln.page, cleaned, ln.is_last_on_page))
    sentences, dropped_sent = make_sentences(work_id, layer, kept, cfg)
    stats = {
        "raw_lines": len(raw_lines), "kept_lines": len(kept),
        "dropped_lines": len(dropped_rows) + len(dropped_sent), "sentences": len(sentences),
        "cross_page": sum("cross_page" in s.flags for s in sentences),
        "no_terminal_punct": sum("no_terminal_punct" in s.flags for s in sentences),
        "long": sum("long" in s.flags for s in sentences),
    }
    return sentences, dropped_rows + dropped_sent, stats


def run(args) -> Path:
    layer = args.layer
    layout = DataLayout(Path(args.data_root), args.work_id)
    out_dir = cleaned_dir(layout)
    out_jsonl = out_dir / f"{layer}.jsonl"
    if out_jsonl.exists() and not args.force:
        raise FileExistsError(f"{out_jsonl} đã tồn tại; dùng --force để ghi đè (hoặc lưu bản cũ: v01, v02...)")

    cfg = CleanConfig(
        layer=layer,
        split=args.split or ("line" if layer == "tho" else "punct"),
        split_colon=args.split_colon,
        drop_patterns=list(DEFAULT_DROP) + list(args.drop_pattern or []),
        heading_heuristic=not args.no_heading_heuristic,
        strip_markers=not args.keep_markers,
        abbrev=set(VI_ABBREV) | {a.strip().lower() for a in (args.abbrev or "").split(",") if a.strip()},
        start_after=args.start_after,
        end_before=args.end_before,
    )
    xml_path = layout.layer_xml(layer)
    raw = read_raw_lines(xml_path)
    selected, sec_info = extract_section(raw, cfg.start_after, cfg.end_before)
    sentences, dropped, stats = clean_layer(args.work_id, layer, selected, cfg)
    dropped = [(ln.id, ln.page, f"section_{reason}", ln.text) for ln, reason in sec_info.excluded] + dropped
    stats["section_lines"] = sec_info.n_selected
    if not sentences:
        raise ValueError(f"Không còn câu nào sau khi làm sạch {xml_path}; xem lại --drop-pattern/--keep")

    save_sentences(layout, layer, sentences)
    atomic_write_text(out_dir / f"{layer}.txt", "".join(s.text + "\n" for s in sentences))
    atomic_write_text(out_dir / f"{layer}.dropped.tsv", "id\tpage\treason\ttext\n" + "".join(
        f"{i}\t{p}\t{r}\t{t.replace(chr(9), ' ').replace(chr(10), ' ')}\n" for i, p, r, t in dropped))
    MetadataStore(layout).update(f"{layer}_cleaned", {
        "input": layout.rel(xml_path), "output": layout.rel(out_jsonl), "stats": stats,
        "parameters": {"split": cfg.split, "split_colon": cfg.split_colon,
                       "drop_patterns": cfg.drop_patterns, "heading_heuristic": cfg.heading_heuristic,
                       "strip_markers": cfg.strip_markers},
        "section": sec_info.as_dict(),
        "review_required": True})
    log.info("[%s] %s", layer, stats)
    if stats["dropped_lines"]:
        log.info("[%s] Xem lại các dòng bị loại: %s", layer, out_dir / f"{layer}.dropped.tsv")
    return out_jsonl


def create_parser():
    p = base_parser("02_extraction XML -> làm sạch + tách câu -> 03_cleaned")
    p.add_argument("--split", choices=["punct", "line"], help="Mặc định: line cho tho, punct cho lớp khác")
    p.add_argument("--split-colon", action="store_true")
    p.add_argument("--drop-pattern", action="append", help="Regex dòng cần bỏ (lặp lại được)")
    p.add_argument("--start-after", help="Regex ranh giới đầu đoạn: lấy nội dung SAU dòng khớp (không gồm dòng khớp)")
    p.add_argument("--end-before", help="Regex ranh giới cuối đoạn: ngừng lấy trước dòng khớp (không gồm dòng khớp)")
    p.add_argument("--no-heading-heuristic", action="store_true")
    p.add_argument("--keep-markers", action="store_true")
    p.add_argument("--abbrev", help="Viết tắt Việt thêm, phân cách dấu phẩy")
    return p


def main(argv: list[str] | None = None) -> int:
    parser = create_parser()
    args = parse_args(parser, argv)
    try:
        run(args)
    except (OSError, ValueError) as exc:
        log.error("%s", exc)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
