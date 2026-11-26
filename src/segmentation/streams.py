"""Nối hai luồng OCR theo hệ chữ vào segmentation: han.xml (paddlevl) + vie.xml (qwen).

Bật bằng `segmentation.ocr_streams = "han_vie"` trong config (hoặc `--ocr-streams han_vie`). Mặc định tắt: các
work cũ (raw.xml unified, chi_tra.xml/vi.xml của CPO_18) chạy y như trước.

Cách làm:
1. Đọc dòng của từng luồng (`03_ocr/ocr_raw/{han,vie}.xml`). source_id = "<luồng>.<ID dòng>", vd
   "han.P0015.L003", để không trùng ID giữa hai file.
2. Trộn hai luồng theo TRANG rồi theo MÉP TRÊN bbox, giữ nguyên thứ tự nội bộ của mỗi luồng (merge ổn định).
   Dùng mép trên chứ không dùng tâm: Qwen hay gộp nhiều dòng in vào một dòng (bbox cao vài trăm px), tâm của nó
   rơi xuống dưới tiêu đề Hán đứng sau. Kết quả thay cho raw.xml ở chế độ unified: máy trạng thái chạy y như cũ.
3. Ranh giới tài liệu: chỉ dòng ĐẦU của một khối Hán liên tiếp được mở tài liệu (bỏ qua tiêu đề chạy lặp lại ở
   đầu/chân trang). Mặc định (`document.start_on_han_block`, true) MỌI khối Hán đứng sau phần Việt mở tài liệu
   mới, không cần khớp start_patterns: mẫu tiêu đề dễ vỡ vì lỗi OCR ("4 谒關..." mất dấu chấm, "5. 其二" bị đọc
   thành rác), còn cấu trúc "khối Hán -> các khối Việt" của mỗi bài thì ổn định. Đặt false để chỉ dùng
   start_patterns (vẫn giới hạn ở dòng đầu khối Hán: câu thơ không chấm câu khớp mẫu kiểu `^[Hán]{2,12}$`).
4. Sau khi gán lớp: lớp han chỉ giữ dòng từ luồng han; am/nghia/tho chỉ giữ dòng từ luồng vie. Chữ Hán trong
   khảo dị/chú thích nằm trong khối Việt nên không mở khối Hán mới và bị loại khỏi lớp Việt (bản Việt của dòng đó
   đã có trong vie.xml, không còn chữ Hán).
"""

from __future__ import annotations

import logging
import re
import unicodedata
from collections import Counter, defaultdict

from src.common.corpus import DataLayout
from src.common.xmlstore import bbox_from_attr, file_element, opt_float, read_root, text_of
from src.ocr.script_filter import has_latin
from src.segmentation.model import OcrLine

log = logging.getLogger("segmentation.streams")

STREAM_LAYERS = {"han": ("han",), "vie": ("am", "nghia", "tho")}   # luồng -> các lớp được nhận
# Phần Việt đứng ngay sau khối Hán mà không có tiêu đề mục là phiên âm (thứ tự in Hán -> âm -> nghĩa -> thơ).
# Đổi/tắt bằng segmentation.implicit_after_han ("" để tắt).
IMPLICIT_AFTER_HAN = "am"
# Đang ở am mà gặp tiêu đề viết hoa (vd "LÊN NÚI MẪU TỬ") khi OCR mất chữ "DỊCH NGHĨA" -> mở nghia.
# Đổi bằng segmentation.implicit_title_next ({} để tắt).
IMPLICIT_TITLE_NEXT = {"am": "nghia"}
# Sau dòng nguồn ("Trích Dật thi lược toản") trong lớp am là chú thích -> không gán lớp. Phụ thuộc cách in của
# từng work (CPO_18 dùng "*" cho chú thích tác giả ở ĐẦU bài), nên mặc định TẮT; bật bằng
# segmentation.notes_after_attribution = ["am"] (CPO_20).
NOTES_AFTER_ATTRIBUTION: tuple[str, ...] = ()
MODE = "han_vie"
HEADER_MIN_PAGES = 3                     # dòng lặp lại trên >= 3 trang = tiêu đề chạy (đầu/chân trang)
# Tiêu đề chạy bị OCR sai dấu/1 chữ ("HOÀNG HOA ĐÔ PHẢ" ~ "ĐỒ", "NGÔ THÌ NHÂN" ~ "NHẬM", CPO_19 tr.190/284/457)
# vẫn là tiêu đề chạy: so sau khi bỏ dấu, độ giống >= HEADER_FUZZY_RATIO, chỉ với khoá dài >= HEADER_FUZZY_MIN_LEN.
HEADER_FUZZY_RATIO = 0.88
HEADER_FUZZY_MIN_LEN = 8
# Dòng Hán kế tiếp cách dòng trước <= HAN_BLOCK_GAP x bước dòng Hán (trung vị) của trang thì dòng Việt có mép trên
# lọt vào giữa (bbox Qwen lệch, dòng rác 1 chữ) được đặt SAU dòng Hán đó, không cắt đôi khối Hán.
HAN_BLOCK_GAP = 1.5


def stream_of_layer(layer: str) -> str | None:
    return next((s for s, layers in STREAM_LAYERS.items() if layer in layers), None)


def load_stream(layout: DataLayout, stream: str, pages: list[int] | None = None) -> list[OcrLine]:
    """Dòng của han.xml / vie.xml. Lớp han: nếu dòng gốc của engine không có chữ cái Latin thì dùng dòng gốc
    (giữ "1." của tiêu đề có đánh số, dấu câu ASCII) giống đầu vào raw.xml trước đây; ngược lại dùng dòng đã lọc."""
    path = layout.ocr_layer_xml_path(stream)
    if not path.exists():
        raise FileNotFoundError(f"Chưa có {path}; chạy python -m src.ocr.build_layers trước")
    file_el = file_element(read_root(path))
    ocr = file_el.find("OCR")
    if ocr is None or ocr.get("MODE") != "layer" or ocr.get("LAYER") != stream:
        raise ValueError(f"{path} không phải output của src.ocr.build_layers (thiếu <OCR MODE='layer'>)")
    wanted = set(pages) if pages else None
    out: list[OcrLine] = []
    for page_el in file_el.findall("PAGE"):
        page = int(page_el.get("NUMBER", "0"))
        if wanted is not None and page not in wanted:
            continue
        for line in page_el.findall("LINE"):
            text = text_of(line)
            raw = line.get("RAW_TEXT") or text
            use = raw.strip() if stream == "han" and not has_latin(raw) else text
            out.append(OcrLine(page=page, index=int(line.get("ORDER", "0")), text=use,
                               bbox=bbox_from_attr(line.get("BBOX")),
                               confidence=opt_float(line, "CONFIDENCE"),
                               source_id=f"{stream}.{line.get('ID')}", writing_system=stream,
                               engine=page_el.get("SELECTED_ENGINE", ""),
                               cache_key=page_el.get("SELECTED_KEY", ""), image=page_el.get("IMAGE", ""),
                               original_text=raw))
    return out


def _y_top(line: OcrLine) -> float | None:
    return float(line.bbox[1]) if line.bbox else None


def merge_streams(han: list[OcrLine], vie: list[OcrLine]) -> list[OcrLine]:
    """Trộn theo trang, rồi theo mép trên bbox; mỗi luồng giữ nguyên thứ tự nội bộ. Dòng thiếu bbox đi theo dòng
    trước nó trong cùng luồng. `index` được đánh lại theo thứ tự đã trộn trong trang (dùng để sắp xếp lớp)."""
    by_page: dict[int, tuple[list[OcrLine], list[OcrLine]]] = defaultdict(lambda: ([], []))
    for ln in han:
        by_page[ln.page][0].append(ln)
    for ln in vie:
        by_page[ln.page][1].append(ln)
    merged: list[OcrLine] = []
    for page in sorted(by_page):
        a, b = by_page[page]
        ka, kb = _keys(a), _keys(b)
        pitch = _han_pitch(ka)
        i = j = 0
        page_out = []
        while i < len(a) or j < len(b):
            # dòng Việt lọt vào kẽ giữa hai dòng Hán liền nhau -> để sau dòng Hán kế tiếp (khối Hán không bị cắt)
            glued = (pitch is not None and 0 < i < len(a) and page_out and page_out[-1] is a[i - 1]
                     and ka[i] - ka[i - 1] <= HAN_BLOCK_GAP * pitch)
            if j >= len(b) or (i < len(a) and (ka[i] <= kb[j] or glued)):
                page_out.append(a[i]); i += 1
            else:
                page_out.append(b[j]); j += 1
        for order, ln in enumerate(page_out, 1):
            ln.index = order
        merged.extend(page_out)
    return merged


def _keys(lines: list[OcrLine]) -> list[float]:
    """Khoá sắp xếp không giảm trong một luồng: mép trên bbox, lấp chỗ thiếu bằng khoá của dòng trước."""
    keys, last = [], float("-inf")
    for ln in lines:
        y = _y_top(ln)
        last = max(last, y) if y is not None else last
        keys.append(last)
    return keys


def _han_pitch(keys: list[float]) -> float | None:
    """Bước dòng Hán của trang: trung vị khoảng cách dương giữa hai mép trên liên tiếp (cần >= 2 khoảng)."""
    gaps = sorted(b - a for a, b in zip(keys, keys[1:]) if b > a)
    return gaps[len(gaps) // 2] if len(gaps) >= 2 else None


def _norm(text: str) -> str:
    text = unicodedata.normalize("NFC", text).lower()
    return re.sub(r"[\d\W_]+", "", text)


def _fold(key: str) -> str:
    """Khoá _norm bỏ dấu tiếng Việt (đ -> d) để so tiêu đề chạy bị OCR sai dấu."""
    key = key.replace("đ", "d")
    return "".join(c for c in unicodedata.normalize("NFD", key) if not unicodedata.combining(c))


def header_matcher(headers: set[str]):
    """Hàm text -> bool: khớp đúng một tiêu đề chạy, hoặc gần đúng (bỏ dấu, độ giống cao) với tiêu đề dài."""
    from difflib import SequenceMatcher
    folded = {_fold(h) for h in headers}
    long_ones = [h for h in folded if len(h) >= HEADER_FUZZY_MIN_LEN]

    def match(text: str) -> bool:
        key = _norm(text)
        if not key:
            return False
        if key in headers:
            return True
        f = _fold(key)
        if f in folded:
            return True
        return len(f) >= HEADER_FUZZY_MIN_LEN and any(
            abs(len(f) - len(h)) <= 3 and SequenceMatcher(None, f, h).ratio() >= HEADER_FUZZY_RATIO
            for h in long_ones)
    return match


def header_predicate(lines: list[OcrLine]):
    """Hàm ln -> bool: dòng KHÔNG mang cấu trúc: tiêu đề chạy (lặp trên nhiều trang, kể cả bản OCR sai dấu) hoặc
    dòng Việt nhiễu (`is_noise_vie`). Máy trạng thái không dùng các dòng này để ngầm mở lớp, mở lớp kế tiếp, hay
    đếm là dòng nội dung."""
    match = header_matcher(running_headers(lines))
    return lambda ln: match(ln.text) or (getattr(ln, "writing_system", "") == "vie" and is_noise_vie(ln.text))


def running_headers(lines: list[OcrLine], min_pages: int = HEADER_MIN_PAGES) -> set[str]:
    """Văn bản (đã chuẩn hoá, bỏ số) lặp lại trên >= min_pages trang: tiêu đề chạy, số trang có chữ..."""
    pages: dict[str, set[int]] = defaultdict(set)
    for ln in lines:
        key = _norm(ln.text)
        if key:
            pages[key].add(ln.page)
    return {k for k, v in pages.items() if len(v) >= min_pages}


MIN_HAN_BLOCK_LINES = 2                  # khối Hán 1 dòng (trích dẫn trong chú thích) không mở tài liệu
# Dòng Việt nhiễu (rác OCR của Qwen trên trang chữ Hán, chữ viết tay bên lề): không phải "phần Việt" của bài.
# Gặp ở audit 2026-11-26: "E", "**N**", "Điều" (CPO_18 tr.235: tách đôi bài, phiên âm gắn sang bài sau),
# "of contradiction!", "Vandana =", "Stone", "Dome", "cuer".
VIE_NOISE_MAX_LETTERS = 4                # <= 4 chữ cái: nhiễu
VIE_NOISE_FOREIGN_MAX_WORDS = 3          # hoặc <= 3 từ, không có chữ cái riêng của tiếng Việt và <= 20 chữ cái
VIE_NOISE_FOREIGN_MAX_LETTERS = 20
_VIE_ONLY_LETTERS = frozenset("đăâêôơư" "ảạẳẵặằắẩẫậầấẻẽẹểễệềếỉĩịỏõọổỗộồốởỡợờớủũụửữựừứỷỹỵỳý")


def is_noise_vie(text: str) -> bool:
    """Dòng Việt nhiễu: không làm đứt khối Hán, không ngầm mở/chuyển lớp, không tính là dòng nội dung."""
    letters = sum(c.isalpha() for c in text)
    if letters <= VIE_NOISE_MAX_LETTERS:
        return True
    lower = unicodedata.normalize("NFC", text).lower()
    return (letters <= VIE_NOISE_FOREIGN_MAX_LETTERS and len(text.split()) <= VIE_NOISE_FOREIGN_MAX_WORDS
            and not any(c in _VIE_ONLY_LETTERS for c in lower))


_is_noise_vie = is_noise_vie                       # tên cũ


def allowed_document_starts(merged: list[OcrLine], headers: set[str] | None = None,
                            min_block_lines: int = MIN_HAN_BLOCK_LINES) -> set[int]:
    """Chỉ số dòng được phép mở tài liệu: dòng Hán đầu tiên của một khối Hán liên tiếp có >= min_block_lines
    dòng. Dòng tiêu đề chạy (lặp trên nhiều trang) không làm đứt khối Hán, để bài thơ sang trang không bị tách
    đôi. Khối Hán 1 dòng giữa phần Việt thường là chữ Hán trích trong chú thích/khảo dị: không mở tài liệu."""
    is_header = header_matcher(headers if headers is not None else running_headers(merged))
    blocks: list[list[int]] = []
    previous = None                      # luồng của dòng nội dung gần nhất
    for i, ln in enumerate(merged):
        if is_header(ln.text) or (ln.writing_system == "vie" and _is_noise_vie(ln.text)):
            continue
        if ln.writing_system == "han":
            if previous != "han":
                blocks.append([])
            blocks[-1].append(i)
        previous = ln.writing_system
    return {b[0] for b in blocks if len(b) >= min_block_lines}


def document_config(doc_cfg: dict) -> dict:
    """Cấu hình tìm ranh giới tài liệu cho chế độ luồng (xem mục 3 ở đầu module)."""
    if doc_cfg.get("start_on_han_block", True):
        return {**doc_cfg, "start_patterns": [r"\S"], "numbered_heading": False, "end_patterns": []}
    return doc_cfg


def keep_line(layer: str, line: OcrLine) -> bool:
    """Lớp han chỉ nhận dòng luồng han; am/nghia/tho chỉ nhận dòng luồng vie; lớp khác nhận mọi dòng."""
    stream = stream_of_layer(layer)
    return stream is None or line.writing_system == stream


def filter_rows_by_stream(layer_rows: dict[str, list]) -> tuple[dict[str, list], dict[str, int]]:
    """Lớp han chỉ nhận dòng luồng han; am/nghia/tho chỉ nhận dòng luồng vie. Trả về (đã lọc, số dòng bỏ)."""
    kept: dict[str, list] = {}
    dropped: dict[str, int] = {}
    for layer, rows in layer_rows.items():
        stream = stream_of_layer(layer)
        if stream is None:
            kept[layer] = rows
            continue
        good = [r for r in rows if r.writing_system == stream]
        kept[layer] = good
        if len(good) != len(rows):
            dropped[layer] = len(rows) - len(good)
    return kept, dropped


def coverage_review(work_id: str, han: list[OcrLine], vie: list[OcrLine], pages: list[int]) -> list[dict]:
    """Cảnh báo khi hai luồng không phủ cùng phạm vi trang (trong các trang được yêu cầu)."""
    from src.segmentation.documents import review_row
    han_pages = {ln.page for ln in han}
    vie_pages = {ln.page for ln in vie}
    rows = []
    span = set(pages) if pages else han_pages | vie_pages
    only_vie = sorted((vie_pages - han_pages) & span)
    only_han = sorted((han_pages - vie_pages) & span)
    if han_pages and vie_pages and (min(han_pages) != min(vie_pages) or max(han_pages) != max(vie_pages)):
        log.warning("han.xml phủ trang %d-%d, vie.xml phủ trang %d-%d: hai luồng không cùng phạm vi",
                    min(han_pages), max(han_pages), min(vie_pages), max(vie_pages))
        rows.append(review_row(work_id, "", "", "stream_page_range_mismatch",
                               sorted(han_pages ^ vie_pages), f"han {min(han_pages)}-{max(han_pages)}, "
                               f"vie {min(vie_pages)}-{max(vie_pages)}"))
    if not han_pages:
        log.warning("Không có dòng Hán nào trong phạm vi trang: lớp han sẽ rỗng (chưa chạy paddlevl?)")
    log.info("Luồng han: %d trang, %d dòng; vie: %d trang, %d dòng; trang chỉ có Việt %d, chỉ có Hán %d",
             len(han_pages), len(han), len(vie_pages), len(vie), len(only_vie), len(only_han))
    return rows


def pairing_review(work_id: str, doc_rows: list[dict]) -> list[dict]:
    """Cảnh báo bài có Hán mà không có âm, hoặc có âm mà không có Hán."""
    from src.segmentation.documents import review_row
    rows = []
    for doc in doc_rows:
        counts = doc.get("layers") or {}
        han, am = counts.get("han", 0), counts.get("am", 0)
        if han and not am:
            reason = "han_without_am"
        elif am and not han:
            reason = "am_without_han"
        else:
            continue
        log.warning("Tài liệu %s: %s (han=%d, am=%d, trang %s)", doc["document_id"], reason, han, am,
                    doc.get("pages"))
        rows.append(review_row(work_id, doc["document_id"], "han" if reason == "am_without_han" else "am",
                               reason, doc.get("pages") or [], ""))
    return rows


def document_page_check(layer_rows: dict[str, list], doc_rows: list[dict]) -> list[str]:
    """Mỗi dòng lớp han phải nằm trong khoảng trang của tài liệu nó thuộc (documents.xml)."""
    pages = {d["document_id"]: set(d.get("pages") or []) for d in doc_rows}
    bad = []
    for row in layer_rows.get("han", []):
        doc_pages = pages.get(row.document_id)
        if doc_pages is None or row.page not in doc_pages:
            bad.append(row.id)
    return bad


def stream_stats(merged: list[OcrLine]) -> dict:
    return dict(Counter(ln.writing_system for ln in merged))
