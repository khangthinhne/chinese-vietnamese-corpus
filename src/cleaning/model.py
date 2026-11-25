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
HAN_TERMINATORS = "。！？；﹖﹕﹒﹤"
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

# Chỉ mục chú thích trong dòng: [1] ［1］ (1) （1） ① và chữ số mũ Unicode (OCR Qwen: "Sài Sơn ² đệ nhất phong").
# Chữ số mũ ăn luôn khoảng trắng đứng trước để không để lại hai dấu cách.
MARKER_RE = re.compile(r"\[\d{1,3}\]|［\d{1,3}］|\(\d{1,3}\)|（\d{1,3}）|[①-⑳㉑-㉟]"
                       r"|[ \t]*[⁰¹²³⁴⁵⁶⁷⁸⁹]{1,3}(?=[\s,.;:!?)\]]|$)")
# Chỉ mục khảo dị dạng CHỮ CÁI trong lớp tiếng Việt (am/nghia/tho): "tòng(b) lĩnh", "Tiên hoàng (a) uy",
# "triện^{(b)} ấn" (Qwen viết chỉ số trên kiểu LaTeX). Chỉ xoá khi đứng SAU một chữ (không ở đầu dòng: "(a) ..." đầu
# dòng là mục liệt kê) và trước khoảng trắng/dấu câu/cuối dòng. Đo trên CPO_18/19/20: 44/44 lần là chỉ mục thật.
LETTER_MARKER_RE = re.compile(r"(?<=\S)[ \t]*(?:\\\(\s*)?\^?\{?\(\^?[a-zđ]\)\}?(?:\s*\\\))?(?=[\s,.;:!?)\]]|$)")
LEADING_NUM_RE = re.compile(r"^\s*\d{1,3}\s*[·.．、﹒)]\s*")

# ký tự nhiễu OCR biết chắc: XOÁ khi làm sạch (rule noise_char:<ch>)
NOISE_CHARS = "®°Øø‡†§¤µ¶`^~¦©" 
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
# metadata cấp tài liệu (src/cleaning/metadata.py)
SECTION_MARKER = "section_marker"            # PHIÊN ÂM / DỊCH NGHĨA / CHÚ THÍCH...
FRONT_MATTER = "front_matter"                # lời dẫn trước marker (giữ audit, không vào corpus)
OCR_NOISE = "ocr_noise"                      # rác OCR / trang trí trước marker
METADATA_TITLE = "metadata_title"
METADATA_AUTHOR = "metadata_author"
METADATA_ATTRIBUTION = "metadata_attribution"
METADATA_UNCERTAIN = "metadata_uncertain"    # dòng metadata không chắc -> review, không xoá lặng lẽ
AFTER_SECTION_END = "after_section_end"      # phần sau marker kết thúc (CHÚ THÍCH...)


@dataclass
class RawLine:
    id: str
    page: int
    text: str
    is_last_on_page: bool = False
    raw_text: str = ""                       # bản OCR gốc, KHÔNG bao giờ bị sửa
    flags: list[str] = field(default_factory=list)   # "uncertain:<c>" | "review:<tok>" | "dehyphenated" | ...
    source_ids: list[str] = field(default_factory=list)
    document_id: str = ""


def has_flag(line: RawLine, name: str) -> bool:
    return any(f == name or f.startswith(name + ":") for f in line.flags)


@dataclass
class OCRCleaningConfig:
    """Quy tắc ký tự rác OCR theo từng lớp (mặc định: chỉ đánh dấu, không xoá mù quáng)."""
    remove_patterns: list[str] = field(default_factory=list)             # xoá khớp (tắt mặc định)
    replace_patterns: list[tuple[str, str]] = field(default_factory=list)  # (regex, thay thế)
    suspect_chars: str = SUSPECT_CHARS
    strip_markers: bool = True             # xoá chỉ mục chú thích [1], (1), ① trong dòng
    strip_letter_markers: bool = True      # lớp am/nghia/tho: xoá chỉ mục khảo dị (a), (b) sau chữ (LETTER_MARKER_RE)
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
    # Mục chú thích/khảo dị dạng "1. Côn Lôn: Tức dãy...", "1. 2. Bản A.603 chép là..." lọt vào lớp am/nghia/tho
    # khi OCR thiếu tiêu đề "CHÚ THÍCH". TUỲ CHỌN (mặc định tắt): bản dịch có thể có danh sách đánh số thật.
    note_entries: bool = False
    bib_patterns: list[str] = field(default_factory=lambda: [
        r"Q\.\s*\d", r"Quyển\s+\S", r"Quyên\s+\S", r"[集卷]", r"\btập\b",
        r"(三国|三國|六朝|全宋|全唐|弘明|高僧|文苑).*?[集卷文]",
        r"全[宋唐]文",
    ])


@dataclass
class MetadataConfig:
    """Trích xuất metadata cấp tài liệu (tiêu đề/tác giả/quy trách) khỏi dòng chữ chính.

    Không cấu hình gì (không markers, không patterns) -> bước này NO-OP hoàn toàn:
    thiếu cấu hình KHÔNG bao giờ dẫn tới xoá metadata phá huỷ.
    """
    enabled: bool = True
    exclude_from_main_text: bool = True         # False: vẫn nhận diện nhưng giữ trong dòng chính
    section_start: list[str] = field(default_factory=list)  # marker mở khối (PHIÊN ÂM / DỊCH NGHĨA...)
    section_end: list[str] = field(default_factory=list)    # marker đóng (CHÚ THÍCH...): sau đó bị loại
    title_patterns: list[str] = field(default_factory=list)  # regex ÉP phân loại tiêu đề
    author_patterns: list[str] = field(default_factory=list)
    attribution_patterns: list[str] = field(default_factory=list)  # vd r"^\*" dòng quy trách
    author_max_tokens: int = 4                  # "dạng tên người": tối đa bao nhiêu token
    author_max_len: int = 24                    # và độ dài ký tự tối đa (lớp Hán nên đặt nhỏ hơn)


@dataclass
class CleanConfig:
    layer: str
    split: str = "punct"
    split_colon: bool = False
    drop_patterns: list[str] = field(default_factory=lambda: list(DEFAULT_DROP))
    heading_heuristic: bool = True
    # Hán "short punctuation-free line = heading" is destructive for poetry, so it
    # is opt-in.  Explicit heading patterns always work.
    han_heading_heuristic: bool = False
    han_heading_patterns: list[str] = field(default_factory=list)
    # Script mismatches are retained and flagged instead of silently dropped.
    retain_script_mismatch: bool = True
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
    drop_recurring_single_lines: bool = True   # trang chỉ có 1 dòng, dòng đó lặp y hệt trên >= min_pages trang
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
    metadata: MetadataConfig = field(default_factory=MetadataConfig)

    def __post_init__(self) -> None:
        # chỉ mục chú thích: hai công tắc phải cùng bật
        self.ocr.strip_markers = self.ocr.strip_markers and self.strip_markers and \
            self.footnotes.remove_in_text_markers
