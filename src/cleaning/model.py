"""Mô hình dữ liệu dùng chung cho bộ làm sạch: RawLine, cấu hình, hằng số.

Mọi stage nhận list[RawLine] và chỉ ĐỌC raw_text (bản OCR gốc, bất biến);
ln.text là bản đang xử lý. Flags gắn trên dòng để các stage sau/review dùng.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# --------------------------------------------------------------------------- #
# Hằng số dấu câu / ký tự (tách từ clean.py cũ, giữ nguyên hành vi)
# --------------------------------------------------------------------------- #
HAN_TERMINATORS = "。！？；﹖﹕﹒"
HAN_CLOSERS = "」』”’）)》〉"
HAN_PUNCT = "，。！？；：、"        # dấu ngắt câu/ý; ngoặc, nháy, gạch KHÔNG tính khi nhận diện tiêu đề
ASCII_TO_HAN = {",": "，", ".": "。", ";": "；", ":": "：", "?": "？", "!": "！", "、": "、"}
HAN_JUNK = set("|｜*＊·•▪■□◇◆○●~`^_=+\\/<>[]{}")

VI_ABBREV = {"tp", "ts", "pgs", "gs", "ths", "bs", "ks", "mr", "mrs", "dr", "tr", "tt", "vv",
             "v.v", "vd", "tk", "tl", "nxb", "q", "p", "đ", "st", "sđd", "nt", "cn", "tcn", "scn"}
VI_OPENERS = "\"“‘'(«["
VI_CLOSERS = "\"”’')»]"

# số trang đứng một mình: "205", "- 205 -"
DEFAULT_DROP = [r"^[\-–—\s]*\d{1,4}[\-–—\s]*$"]

MARKER_RE = re.compile(r"\[\d{1,3}\]|［\d{1,3}］|\(\d{1,3}\)|（\d{1,3}）|[①-⑳㉑-㉟]")
LEADING_NUM_RE = re.compile(r"^\s*\d{1,3}\s*[·.．、﹒)]\s*")

# ký tự nhiễu OCR biết chắc: XOÁ khi làm sạch (rule noise_char:<ch>)
NOISE_CHARS = "®°Øø‡†§¤µ¶`^~¦"
# ký tự nghi ngờ: CHỈ ĐÁNH DẤU review, không xoá
SUSPECT_CHARS = "\""

# mã lý do dùng chung (dropped.tsv / audit)
EMPTY_LINE = "empty"
DROP_PATTERN = "configured_drop_pattern"
PAGE_HEADER = "page_header"
PAGE_FOOTER = "page_footer"
IRRELEVANT_PAGE = "irrelevant_page"
BLANK_PAGE = "blank_page"
DUPLICATE_LINE = "duplicate_line"
HEADING = "heading"
FOOTNOTE = "footnote"
BIB_NOTE = "bibliographic_note"
EDITORIAL_NOTE = "editorial_note"
UNCERTAIN_NOTE = "uncertain_note"
OCR_ARTIFACT = "ocr_artifact"
UNCERTAIN = "uncertain"


@dataclass
class RawLine:
    id: str
    page: int
    text: str
    is_last_on_page: bool = False
    raw_text: str = ""                       # bản OCR gốc, KHÔNG bao giờ bị sửa
    flags: list[str] = field(default_factory=list)   # "uncertain:<c>" | "review:<tok>" | "dehyphenated" | ...


def has_flag(line: RawLine, name: str) -> bool:
    return any(f == name or f.startswith(name + ":") for f in line.flags)


@dataclass
class OCRCleaningConfig:
    """Quy tắc ký tự rác OCR theo từng lớp (mặc định: chỉ đánh dấu, không xoá mù quáng)."""
    remove_patterns: list[str] = field(default_factory=list)             # xoá khớp (tắt mặc định)
    replace_patterns: list[tuple[str, str]] = field(default_factory=list)  # (regex, thay thế)
    suspect_chars: str = SUSPECT_CHARS
    strip_markers: bool = True             # xoá chỉ mục chú thích [1], (1), ① trong dòng
    noise_chars: str = NOISE_CHARS          


@dataclass
class FootnoteConfig:
    enabled: bool = True
    policy: str = "exclude"                # exclude | preserve | review
    detect_parentheses: bool = True        # chú thích ngoặc trọn/kéo nhiều dòng, cả qua trang
    detect_bibliographic: bool = True      # nhận diện nguồn/tài liệu trong chú thích
    remove_in_text_markers: bool = True    # xoá [1], (1)... (nhờ artifacts.strip_markers)
    item_patterns: list[str] = field(default_factory=lambda: [
        r"^\(\d{1,3}\)\s", r"^\（\d{1,3}）\s", r"^\[\d{1,3}\]\s", r"^［\d{1,3}］\s"])
    bib_patterns: list[str] = field(default_factory=lambda: [
        r"Q\.\s*\d", r"Quyển\s+\S", r"Quyên\s+\S", r"[集卷]", r"\btập\b"])


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
    # --- trang / trùng lặp / nối trang ---
    excluded_pages: list[int] = field(default_factory=list)   # loại trang thủ công theo config
    remove_headers: bool = True          # dòng đầu trang lặp lại >= header_footer_min_pages trang
    remove_footers: bool = True
    header_footer_min_pages: int = 3
    remove_duplicates: bool = True       # dòng OCR trùng/near-dup
    duplicate_threshold: float = 0.9     # tỉ lệ giống tối thiểu (difflib) tính là near-dup
    duplicate_min_len: int = 20          # cụm ngắn hơn KHÔNG bao giờ bị coi là trùng (câu lặp hợp lệ)
    duplicate_context: int = 5           # vùng chồng lấn: C dòng cuối trang N vs C dòng đầu trang N+1
    reconstruct_page_boundaries: bool = True
    # --- nhãn tiêu đề kiểu "CHÚ THÍCH :" (case-insensitive, chỉ dòng thuần tiêu đề) ---
    heading_patterns: list[str] = field(default_factory=lambda: [
        r"^CHÚ THÍCH\s*:?$", r"^GHI CHÚ\s*:?$", r"^MỤC LỤC\s*:?$", r"^PHỤ LỤC\s*:?$"])
    # --- cấu hình con ---
    ocr: OCRCleaningConfig = field(default_factory=OCRCleaningConfig)
    footnotes: FootnoteConfig = field(default_factory=FootnoteConfig)

    def __post_init__(self) -> None:
        # chỉ mục chú thích: hai công tắc phải cùng bật
        self.ocr.strip_markers = self.ocr.strip_markers and self.strip_markers and \
            self.footnotes.remove_in_text_markers
