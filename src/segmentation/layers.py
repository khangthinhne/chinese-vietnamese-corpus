"""Gán dòng của MỘT tài liệu vào các lớp (han/am/nghia) bằng state machine cấu hình.

- `segmentation.layers.<layer>.source`: writing system nguồn (chi_tra|vie).
- `start_patterns` / `end_patterns`: heading mở/đóng đoạn; heading KHÔNG vào dòng chính
  nhưng được giữ trong `section_headings` (metadata) cùng source_ids/pages.
- Không cấu hình pattern cho system chỉ có một lớp -> toàn bộ system thuộc lớp đó
  (`han` mặc định chi_tra; `am`/`nghia` mặc định vie).
- Dòng không gán được, thiếu heading, chồng lấn -> review; không đoán lặng lẽ.
- Metadata tiêu đề/tác giả/quy trách chạy SAU khi gán lớp, trên dòng đầu của từng lớp,
  dùng lại `src.cleaning.metadata.classify_leading_block` (một nguồn logic duy nhất).
"""

from __future__ import annotations

import re
import unicodedata

from src.cleaning.metadata import classify_leading_block
from src.cleaning.model import MetadataConfig, RawLine
from src.segmentation.documents import review_row
from src.segmentation.model import Document, DocumentLayers, LayerLine, OcrLine
from src.segmentation.patterns import (
    DEFAULT_HEADING_PREFIX,
    Matcher,
    compile_matcher,
    compile_patterns,
    matched_labels,
    matches,
)

DEFAULT_SOURCE = {"han": "chi_tra", "am": "vie", "nghia": "vie", "tho": "vie"}

# Mục chú thích đầu dòng: "1. Nguyên dẫn: ...", "2) Y đài: ..." (số, dấu chấm/ngoặc, thuật ngữ ngắn, dấu hai chấm).
NOTE_ENTRY_RE = re.compile(r"^\s*\d{1,2}\s*[.)]\s*[^\d\s][^:：]{0,40}[:：]\s*\S")


# Tiêu đề khối ghi chú kết thúc lớp nghia, so khớp mờ theo lỗi OCR thường gặp: KHẢO DỊ / KHÁO DỊ / KHAO DI /
# KHẢO DỊC (CPO_19), NGUYÊN CHÚ, CHÚ THÍCH; phải theo sau là ':' hoặc hết dòng, được phép có nội dung cùng dòng
# ("KHẢO DỊC: a. Bản A.404: ..."). Tắt bằng `segmentation.layers.nghia.note_heading_end: false`.
NOTE_HEADING_RE = re.compile(
    r"^(?:[lI|]\s+)?(?:KH[ẢAÁÃẠÀ]O\s*D[ỊIÍÌĨ]C?|NGUY[ÊEỀẾ]N\s+CH[ÚUÙỦ]|CH[ÚUÙ]\s+TH[ÍIÌ]CH)\s*(?:[:：]|$)",
    re.IGNORECASE)


class _NoteHeadingMatcher(Matcher):
    """end_patterns của lớp nghia + NOTE_HEADING_RE (không đòi heading đứng một mình)."""

    def __init__(self, base: Matcher):
        self.base = base
        self.patterns = list(base.patterns) + [NOTE_HEADING_RE]
        self.heading, self.prefix = base.heading, base.prefix

    def __len__(self) -> int:              # "có end_patterns cấu hình không" giữ nguyên nghĩa cũ (luật review)
        return len(self.base)

    def matches_probe(self, probe: str) -> bool:
        return (self.base.matches_probe(probe) or bool(NOTE_HEADING_RE.match(probe))
                or fuzzy_note_heading(probe))


# So khớp mờ tiêu đề mục (OCR hay sai: "DỊCH THOẠI:", "DỊCH NHĨA:", "Phần âm:", "CHÚ TÍCH:"): sau khi bỏ dấu, khoảng
# cách sửa tới tiêu đề chuẩn <= 2 (tiêu đề >= 8 chữ cái) hoặc <= 1. Chỉ xét dòng <= 3 từ và kết thúc bằng ":" hoặc viết
# hoa toàn bộ, để câu thân văn ngắn ("Đích thị") không bị coi là tiêu đề. "Dịch phú" là tiêu đề hợp lệ của lớp tho.
SECTION_HEADINGS = {"am": ("phien am",), "nghia": ("dich nghia",), "tho": ("dich tho", "dich phu")}
NOTE_HEADINGS = ("chu thich", "khao di", "nguyen chu")
FUZZY_HEADING_MAX_WORDS = 3


def _fold_heading(text: str) -> str | None:
    t = unicodedata.normalize("NFC", text).strip()
    colon = t.endswith((":", "："))
    t = t.rstrip(":： .")
    letters = [c for c in t if c.isalpha()]
    if not letters or len(t.split()) > FUZZY_HEADING_MAX_WORDS or not (colon or all(c.isupper() for c in letters)):
        return None
    t = unicodedata.normalize("NFD", t.lower().replace("đ", "d"))
    t = "".join(c for c in t if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", re.sub(r"[^a-z ]", " ", t)).strip() or None


def _edit_distance(a: str, b: str) -> int:
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def _fuzzy_match(text: str, canon: dict[str, tuple[str, ...]]) -> str | None:
    folded = _fold_heading(text)
    if not folded:
        return None
    scored = sorted((_edit_distance(folded, c), key) for key, cs in canon.items() for c in cs
                    if _edit_distance(folded, c) <= (2 if len(c.replace(" ", "")) >= 7 else 1))
    if not scored or (len(scored) > 1 and scored[0][0] == scored[1][0] and scored[0][1] != scored[1][1]):
        return None
    return scored[0][1]


def fuzzy_section_layer(text: str, layers) -> str | None:
    """Lớp (am/nghia/tho) mà dòng là tiêu đề mục mở lớp, theo so khớp mờ; None nếu không phải."""
    canon = {k: v for k, v in SECTION_HEADINGS.items() if k in layers}
    return _fuzzy_match(text, canon) if canon else None


def fuzzy_note_heading(text: str) -> bool:
    """Dòng là tiêu đề khối ghi chú (CHÚ THÍCH/KHẢO DỊ/NGUYÊN CHÚ) theo so khớp mờ."""
    return _fuzzy_match(text, {"note": NOTE_HEADINGS}) is not None


def _compile_layer_matcher(layer_cfg: dict, kind: str, name: str):
    matcher = _compile_layer_matcher_base(layer_cfg, kind, name)
    if kind == "end_patterns" and name.endswith(".nghia.end_patterns") and layer_cfg.get("note_heading_end", True):
        return _NoteHeadingMatcher(matcher)
    return matcher


def _compile_layer_matcher_base(layer_cfg: dict, kind: str, name: str):
    case_sensitive = bool(layer_cfg.get("case_sensitive", False))
    mode = layer_cfg.get("heading_mode")
    heading = bool(mode) if not isinstance(mode, dict) else mode.get("enabled", True)
    if not heading:
        return compile_matcher(layer_cfg.get(kind), name, case_sensitive)
    prefix = layer_cfg.get("heading_prefix_pattern", DEFAULT_HEADING_PREFIX)
    if isinstance(mode, dict) and "prefix_pattern" in mode:
        prefix = mode["prefix_pattern"]
    return compile_matcher(layer_cfg.get(kind), name, case_sensitive,
                           heading=True, prefix_pattern=prefix or None)


def metadata_config(layer: str, layer_cfg: dict, meta_cfg: dict) -> MetadataConfig:
    merged = {k: v for k, v in (meta_cfg or {}).items()
              if k not in ("enabled", "exclude_from_main_text", "layers")}
    merged = {**merged, **(meta_cfg or {}).get("layers", {}).get(layer, {})}
    local = layer_cfg.get("metadata") if isinstance(layer_cfg.get("metadata"), dict) else {}
    merged = {**merged, **local}
    for key in ("title_patterns", "author_patterns", "attribution_patterns",
                "author_max_tokens", "author_max_len", "exclude_from_main_text"):
        if key in layer_cfg:
            merged[key] = layer_cfg[key]
    enabled = (meta_cfg or {}).get("enabled", True) and layer_cfg.get("enabled", True)
    enabled = enabled and layer_cfg.get("metadata", True) is not False
    return MetadataConfig(
        enabled=bool(enabled),
        exclude_from_main_text=merged.get("exclude_from_main_text",
                                          (meta_cfg or {}).get("exclude_from_main_text", True)),
        title_patterns=list(merged.get("title_patterns") or []),
        author_patterns=list(merged.get("author_patterns") or []),
        attribution_patterns=list(merged.get("attribution_patterns") or []),
        author_max_tokens=int(merged.get("author_max_tokens", 4)),
        author_max_len=int(merged.get("author_max_len", 24)),
    )


def _heading(work_id: str, doc: Document, layer: str, kind: str, line: OcrLine) -> dict:
    return {"work_id": work_id, "document_id": doc.id, "layer": layer, "kind": kind,
            "text": line.text, "page": line.page, "bbox": line.bbox,
            "writing_system": line.writing_system,
            "source_ids": [line.source_id]}


def assign_document(work_id: str, doc: Document, layer_cfg: dict, meta_cfg: dict
                    ) -> DocumentLayers:
    review: list[dict] = []
    headings: list[dict] = []
    grouped: dict[str, list[OcrLine]] = {}
    sources = {name: (lc.get("source") or DEFAULT_SOURCE.get(name, "vie"), lc)
               for name, lc in layer_cfg.items()}
    by_system: dict[str, list[str]] = {}
    for name, (ws, _) in sources.items():
        by_system.setdefault(ws, []).append(name)

    for ws, names in by_system.items():
        lines = doc.lines.get(ws, [])
        if not lines:
            continue
        starts = {n: _compile_layer_matcher(
            layer_cfg[n], "start_patterns", f"segmentation.layers.{n}.start_patterns")
                  for n in names}
        ends = {n: _compile_layer_matcher(
            layer_cfg[n], "end_patterns", f"segmentation.layers.{n}.end_patterns")
                for n in names}
        default = names[0] if len(names) == 1 and not starts[names[0]] else None
        current: str | None = None
        started: set[str] = set()
        for ln in lines:
            starts_hit = matched_labels(starts, ln.text)
            ends_hit = matched_labels(ends, ln.text)
            if len(starts_hit) > 1:
                review.append(review_row(work_id, doc.id, ws, "layer_overlap",
                                         [ln.page], ln.text, [ln.source_id]))
            if starts_hit:
                target = starts_hit[0]
                if ends_hit:
                    for n in ends_hit:
                        if n != target:
                            headings.append(_heading(work_id, doc, n, "end", ln))
                    if current not in (None, target) and current not in ends_hit:
                        review.append(review_row(work_id, doc.id, current,
                                                 "missing_end_heading", [ln.page], ln.text,
                                                 [ln.source_id]))
                elif current and current != target:
                    review.append(review_row(work_id, doc.id, current,
                                             "missing_end_heading", [ln.page], ln.text,
                                             [ln.source_id]))
                current = target
                started.add(target)
                headings.append(_heading(work_id, doc, target, "start", ln))
                continue
            if ends_hit:
                target = current if current in ends_hit else ends_hit[0]
                if current is None:
                    review.append(review_row(work_id, doc.id, target,
                                             "missing_start_heading", [ln.page], ln.text,
                                             [ln.source_id]))
                headings.append(_heading(work_id, doc, target, "end", ln))
                if current in ends_hit:
                    current = None
                continue
            target = current or default
            if target is None:
                review.append(review_row(work_id, doc.id, ws, "unassigned_line",
                                         [ln.page], ln.text, [ln.source_id]))
                continue
            grouped.setdefault(target, []).append(ln)
        for n in names:
            optional = bool(layer_cfg[n].get("optional"))
            if starts[n] and n not in started and not optional:
                review.append(review_row(work_id, doc.id, n, "missing_heading",
                                         doc.pages, ""))
            if ends[n] and n in started and current == n and not optional:
                review.append(review_row(work_id, doc.id, n, "missing_end_heading",
                                         doc.pages, ""))

    for name, (ws, _) in sources.items():
        optional = bool(layer_cfg[name].get("optional"))
        if ws not in doc.lines:
            if not optional:
                review.append(review_row(work_id, doc.id, name, "missing_source", doc.pages, ws))
        elif name not in grouped and not optional:
            review.append(review_row(work_id, doc.id, name, "missing_layer", doc.pages, ""))

    lines_by_layer: dict[str, list[LayerLine]] = {}
    for layer, items in grouped.items():
        prefix = "S" if layer == "han" else "L"
        ordered = sorted(items, key=lambda x: (x.page, x.index))
        rows = []
        for seq, ln in enumerate(ordered, 1):
            rows.append(LayerLine(
                id=f"{doc.id}.P{ln.page:03d}.{prefix}{seq:03d}", document_id=doc.id,
                layer=layer, page=ln.page, text=ln.text, bbox=ln.bbox,
                confidence=ln.confidence, writing_system=ln.writing_system,
                source_ids=[ln.source_id]))
        lines_by_layer[layer] = rows

    metadata_rows: list[dict] = []
    uncertain: list[dict] = []
    for layer, rows in list(lines_by_layer.items()):
        cfg = metadata_config(layer, layer_cfg[layer], meta_cfg or {})
        raw = [RawLine(id=x.id, page=x.page, text=x.text, raw_text=x.text,
                       source_ids=list(x.source_ids), document_id=x.document_id) for x in rows]
        kept, md = classify_leading_block(raw, cfg, layer, doc.id)
        kept_ids = {r.id for r in kept}
        lines_by_layer[layer] = [x for x in rows if x.id in kept_ids]
        if not lines_by_layer[layer]:
            del lines_by_layer[layer]
        row = md.to_row(work_id, layer)
        metadata_rows.append(row)
        for u in row.get("uncertain", []):
            uncertain.append(u)
            review.append(review_row(work_id, doc.id, layer,
                                     "metadata_uncertain", u.get("pages", []),
                                     u.get("text", ""), u.get("source_ids", [])))

    return DocumentLayers(lines=lines_by_layer, headings=headings,
                          metadata_rows=metadata_rows, uncertain=uncertain, review=review)


# --- UNIFIED (Single-stream) LAYER SEGMENTATION ---

_TITLE_SUFFIX_RE = re.compile(r"\s*[\(\[]\s*\w{1,3}\s*[\)\]]\s*$")


def is_upper_title(text: str) -> bool:
    """Dòng tiêu đề Việt viết hoa toàn bộ (vd "LÊN NÚI MẪU TỬ (1)", "THỜ MÙNG MƯA"): >= 2 chữ cái Latin, không
    chữ thường, <= 12 từ, không kết thúc bằng dấu phẩy/chấm phẩy (câu thơ viết hoa nhầm)."""
    probe = _TITLE_SUFFIX_RE.sub("", text.strip())
    letters = [c for c in probe if c.isalpha() and unicodedata.name(c, "").startswith("LATIN")]
    if len(letters) < 2 or any(c.islower() for c in letters):
        return False
    return len(probe.split()) <= 12 and not probe.endswith((",", ";"))


def assign_document_unified(work_id: str, doc: Document, layer_cfg: dict, meta_cfg: dict,
                            keep_line=None, dropped: dict[str, int] | None = None,
                            implicit_next: dict[str, str] | None = None,
                            is_header=None, title_next: dict[str, str] | None = None,
                            notes_after_attribution: set[str] | frozenset[str] = frozenset(),
                            note_entry_ends: set[str] | frozenset[str] = frozenset(),
                            am_detector=None, overrides=()) -> DocumentLayers:
    """Gán dòng của MỘT tài liệu vào các lớp (han/am/nghia/tho) từ stream OCR duy nhất.

    - Không phụ thuộc vào 'source' (chi_tra/vie).
    - Duyệt tuần tự các dòng theo thứ tự đọc ban đầu bằng state machine.
    - Tách riêng dòng heading vào `section_headings`.
    - Hỗ trợ các lớp tuỳ chọn (optional: true) và bắt lỗi dòng không gán được vào review.
    - `keep_line(layer, line) -> bool` (tuỳ chọn): bỏ dòng đã gán lớp nhưng không thuộc lớp đó (vd chữ Hán trong
      khảo dị khi chạy hai luồng han/vie), TRƯỚC khi trích metadata; số dòng bỏ cộng vào `dropped[layer]`.
    - `implicit_next` (tuỳ chọn, cần `keep_line`): vd {"han": "am"}. Đang ở lớp han mà gặp dòng không thuộc
      han nhưng thuộc am, và am CHƯA bắt đầu trong tài liệu -> coi như mở lớp am không có tiêu đề mục (sách in
      phần phiên âm ngay sau chữ Hán, không có chữ "PHIÊN ÂM"). Ghi review `implicit_layer_start`.
    - `title_next` (tuỳ chọn), vd {"am": "nghia"}: đang ở am (hoặc vừa rời am để vào chú thích), am đã có ít nhất
      một dòng nội dung KHÔNG viết hoa, gặp dòng tiêu đề viết hoa toàn bộ -> mở lớp kế tiếp (sách in tiêu đề dịch
      "LÊN NÚI MẪU TỬ" mà OCR làm mất chữ "DỊCH NGHĨA"). Review `implicit_layer_start`.
    - `notes_after_attribution` (tuỳ chọn), vd {"am"}: trong lớp đó, sau dòng khớp `attribution_patterns` (dòng
      nguồn "Trích ..."), các dòng tiếp theo là chú thích: không gán lớp cho tới tiêu đề/tiêu đề viết hoa kế tiếp.
    - `note_entry_ends` (tuỳ chọn), vd {"am", "nghia", "tho"}: trong các lớp đó, dòng mục chú thích "1. Thuật ngữ: ..."
      (`NOTE_ENTRY_RE`) mở vùng chú thích cuối trang: nó và các dòng sau trên CÙNG TRANG không gán lớp; sang trang
      mới mà chưa gặp tiêu đề thì lớp cũ chạy tiếp (CPO_20 in chú thích cuối trang không có tiêu đề CHÚ THÍCH,
      audit 2026-11-26).
    - Tiêu đề mục tường minh đặt lại số dòng nội dung của lớp đó; dòng `is_header` (tiêu đề chạy, dòng Việt nhiễu)
      không tính là dòng nội dung (CPO_18.005: rác + tiêu đề chạy làm "PHIÊN ÂM:" rồi tiêu đề bài mở nhầm nghia).
    - Dòng bị `keep_line` loại được ghi review `dropped_cross_stream` (từng dòng), không mất dấu.
    - `am_detector` (tuỳ chọn, `src.segmentation.am_detect.AmDetector`): lớp am được MỞ NGẦM (không có tiêu đề
      PHIÊN ÂM) mà không khớp cách đọc Hán–Việt của chữ Hán -> chuyển sang nghia, review `am_reassigned_to_nghia`.
    - `overrides` (tuỳ chọn): ghi đè của config cho tài liệu này (`src.segmentation.overrides`), áp dụng sau cùng.
    """
    review: list[dict] = []
    headings: list[dict] = []
    grouped: dict[str, list[OcrLine]] = {}

    all_lines = doc.all_lines if hasattr(doc, "all_lines") else (
        doc.lines if isinstance(doc.lines, list) else
        sorted([ln for lns in doc.lines.values() for ln in lns], key=lambda x: (x.page, x.index))
    )

    if not all_lines:
        return DocumentLayers(lines={}, headings=[], metadata_rows=[], uncertain=[], review=[])

    names = list(layer_cfg.keys())
    starts = {n: _compile_layer_matcher(
        layer_cfg[n], "start_patterns", f"segmentation.layers.{n}.start_patterns")
        for n in names}
    ends = {n: _compile_layer_matcher(
        layer_cfg[n], "end_patterns", f"segmentation.layers.{n}.end_patterns")
        for n in names}

    # Lớp khởi đầu mặc định: lớp đầu tiên không có start_patterns bắt buộc (thường là 'han')
    default_start = None
    for n in names:
        if not starts[n]:
            default_start = n
            break
    if default_start is None and names:
        default_start = names[0]

    current: str | None = default_start
    started: set[str] = {default_start} if default_start else set()
    body_lines: dict[str, int] = {}              # số dòng nội dung không viết hoa đã gán cho mỗi lớp
    notes_page: int | None = None                # trang đang có chú thích cuối trang (note_entry_ends)
    implicit_started: set[str] = set()           # lớp được mở ngầm sau khối Hán (không có tiêu đề mục)
    am_implicit_run = False                      # lớp am hiện đang chạy từ lần mở ngầm (chưa gặp tiêu đề PHIÊN ÂM)
    implicit_am: list[OcrLine] = []              # dòng am gán trong lúc đó (bộ nhận diện chỉ xét/chuyển các dòng này)
    last_layer: str | None = current             # lớp nội dung gần nhất (để thoát khỏi đoạn chú thích)
    attribution = {n: compile_patterns(layer_cfg[n].get("attribution_patterns"),
                                       f"segmentation.layers.{n}.attribution_patterns", False)
                   for n in notes_after_attribution if n in layer_cfg}

    heading_layers = [n for n in names if layer_cfg.get(n, {}).get("heading_mode")]
    for ln in all_lines:
        starts_hit = matched_labels(starts, ln.text)
        ends_hit = matched_labels(ends, ln.text)
        if not starts_hit and heading_layers:
            fuzzy = fuzzy_section_layer(ln.text, heading_layers)
            if fuzzy:
                starts_hit = [fuzzy]
                review.append(review_row(work_id, doc.id, fuzzy, "fuzzy_section_heading",
                                         [ln.page], ln.text, [ln.source_id]))

        if len(starts_hit) > 1:
            review.append(review_row(work_id, doc.id, "", "layer_overlap",
                                     [ln.page], ln.text, [ln.source_id]))

        if starts_hit:
            target = starts_hit[0]
            is_heading = bool(layer_cfg.get(target, {}).get("heading_mode", False))
            if is_heading:
                if current and current != target:
                    for n in ends_hit:
                        if n != target:
                            h_info = _heading(work_id, doc, n, "end", ln)
                            h_info["heading_id"] = f"{doc.id}.P{ln.page:04d}.H{len(headings) + 1:03d}"
                            headings.append(h_info)
                current = target
                started.add(target)
                body_lines[target] = 0           # tiêu đề tường minh: nội dung của lớp tính lại từ đây
                if target == "am":
                    am_implicit_run = False
                h_info = _heading(work_id, doc, target, "start", ln)
                h_info["heading_id"] = f"{doc.id}.P{ln.page:04d}.H{len(headings) + 1:03d}"
                headings.append(h_info)
                continue
            else:
                current = target
                started.add(target)
                # Dòng nội dung (như tiêu đề thơ Hán không dùng heading_mode): giữ trong lớp
                grouped.setdefault(target, []).append(ln)
                continue

        if ends_hit:
            target = current if current in ends_hit else ends_hit[0]
            if current is None:
                review.append(review_row(work_id, doc.id, target,
                                         "missing_start_heading", [ln.page], ln.text,
                                         [ln.source_id]))
            h_info = _heading(work_id, doc, target, "end", ln)
            h_info["heading_id"] = f"{doc.id}.P{ln.page:04d}.H{len(headings) + 1:03d}"
            headings.append(h_info)
            if current in ends_hit:
                current = None
            continue

        if (implicit_next and keep_line is not None and current in implicit_next
                and not keep_line(current, ln) and not (is_header and is_header(ln))):
            nxt = implicit_next[current]
            if nxt not in started and keep_line(nxt, ln):
                review.append(review_row(work_id, doc.id, nxt, "implicit_layer_start",
                                         [ln.page], ln.text, [ln.source_id]))
                current = nxt
                started.add(nxt)
                implicit_started.add(nxt)
                am_implicit_run = am_implicit_run or nxt == "am"

        if title_next and is_upper_title(ln.text) and not (is_header and is_header(ln)):
            prev = current or last_layer
            nxt = title_next.get(prev or "")
            if (nxt and nxt not in started and body_lines.get(prev, 0) > 0
                    and (keep_line is None or keep_line(nxt, ln))):
                review.append(review_row(work_id, doc.id, nxt, "implicit_layer_start",
                                         [ln.page], ln.text, [ln.source_id]))
                current = nxt
                started.add(nxt)

        if notes_page is not None and ln.page != notes_page:
            if current is None and last_layer:   # chú thích cuối trang đã hết: nội dung lớp chạy tiếp sang trang mới
                current = last_layer
            notes_page = None
        if (current in note_entry_ends and NOTE_ENTRY_RE.match(ln.text)
                and (keep_line is None or keep_line(current, ln))):
            last_layer = current
            current = None                       # mục chú thích "1. Thuật ngữ: ...": hết lớp tới cuối trang
            notes_page = ln.page
        target = current
        if target is None:
            review.append(review_row(work_id, doc.id, "", "unassigned_line",
                                     [ln.page], ln.text, [ln.source_id]))
            continue
        grouped.setdefault(target, []).append(ln)
        if target == "am" and am_implicit_run:
            implicit_am.append(ln)
        last_layer = target
        # dòng nội dung = có chữ thường (tiêu đề viết hoa nhiều dòng, kể cả dòng kết thúc bằng dấu phẩy, không tính)
        if (any(c.islower() for c in ln.text) and (keep_line is None or keep_line(target, ln))
                and not (is_header and is_header(ln))):
            body_lines[target] = body_lines.get(target, 0) + 1
        if (target in attribution and body_lines.get(target, 0) > 0
                and matches(attribution[target], ln.text)):
            current = None                       # sau dòng nguồn: chú thích, không gán lớp

    if keep_line is not None:
        for layer in list(grouped):
            kept = [ln for ln in grouped[layer] if keep_line(layer, ln)]
            if dropped is not None and len(kept) != len(grouped[layer]):
                dropped[layer] = dropped.get(layer, 0) + len(grouped[layer]) - len(kept)
            for ln in grouped[layer]:
                if not keep_line(layer, ln):
                    review.append(review_row(work_id, doc.id, layer, "dropped_cross_stream",
                                             [ln.page], ln.text, [ln.source_id]))
            if kept:
                grouped[layer] = kept
            else:
                del grouped[layer]

    kept_ids = {id(x) for x in grouped.get("am", [])}
    implicit_am = [x for x in implicit_am if id(x) in kept_ids]       # bỏ dòng đã bị loại khác luồng
    if am_detector is not None and implicit_am and grouped.get("han"):
        # Chỉ phần am MỞ NGẦM; phần sau tiêu đề PHIÊN ÂM tường minh giữ nguyên (CPO_20.053: tựa dịch nghĩa mở
        # ngầm, rồi "Phiên âm:" một bài chiếu Nôm ở tr.126-134).
        move, why = am_detector.decide(min(doc.pages) if doc.pages else 0,
                                       [x.text for x in grouped["han"]], [x.text for x in implicit_am])
        if move:
            moved = {id(x) for x in implicit_am}
            am_lines = implicit_am
            rest = [x for x in grouped.pop("am") if id(x) not in moved]
            if rest:
                grouped["am"] = rest
            grouped["nghia"] = am_lines + grouped.get("nghia", [])
            started.add("nghia")
            review.append(review_row(work_id, doc.id, "nghia", "am_reassigned_to_nghia",
                                     sorted({x.page for x in am_lines}), why, [am_lines[0].source_id]))

    whole_excluded = False
    if overrides:
        from src.segmentation.overrides import apply_overrides
        review.extend(apply_overrides(work_id, doc.id, grouped, implicit_am, list(overrides)))
        whole_excluded = not grouped        # cả tài liệu bị gỡ: không báo thiếu lớp

    for n in names:
        optional = bool(layer_cfg[n].get("optional")) or whole_excluded
        if starts[n] and n not in started and not optional:
            review.append(review_row(work_id, doc.id, n, "missing_heading",
                                     doc.pages, ""))
        elif n not in grouped and not optional:
            review.append(review_row(work_id, doc.id, n, "missing_layer", doc.pages, ""))

    lines_by_layer: dict[str, list[LayerLine]] = {}
    for layer, items in grouped.items():
        prefix = "S" if layer == "han" else "L"
        ordered = sorted(items, key=lambda x: (x.page, x.index))
        rows = []
        for seq, ln in enumerate(ordered, 1):
            rows.append(LayerLine(
                id=f"{doc.id}.P{ln.page:04d}.{prefix}{seq:03d}", document_id=doc.id,
                layer=layer, page=ln.page, text=ln.text, bbox=ln.bbox,
                confidence=ln.confidence, writing_system=ln.writing_system,
                source_ids=[ln.source_id]))
        lines_by_layer[layer] = rows

    metadata_rows: list[dict] = []
    uncertain: list[dict] = []
    for layer, rows in list(lines_by_layer.items()):
        cfg_meta = metadata_config(layer, layer_cfg.get(layer, {}), meta_cfg or {})
        raw = [RawLine(id=x.id, page=x.page, text=x.text, raw_text=x.text,
                       source_ids=list(x.source_ids), document_id=x.document_id) for x in rows]
        kept, md = classify_leading_block(raw, cfg_meta, layer, doc.id)
        kept_ids = {r.id for r in kept}
        lines_by_layer[layer] = [x for x in rows if x.id in kept_ids]
        if not lines_by_layer[layer]:
            del lines_by_layer[layer]
        row = md.to_row(work_id, layer)
        metadata_rows.append(row)
        for u in row.get("uncertain", []):
            uncertain.append(u)
            review.append(review_row(work_id, doc.id, layer,
                                     "metadata_uncertain", u.get("pages", []),
                                     u.get("text", ""), u.get("source_ids", [])))
        by_id = {x.id: x for x in rows}
        for ln, _ in md.restored:
            src = by_id.get(ln.id)
            review.append(review_row(work_id, doc.id, layer, "han_title_line_restored", [ln.page], ln.text,
                                     list(src.source_ids) if src else []))
    suspect = han_title_suspect(metadata_rows)
    if suspect:
        review.append(review_row(work_id, doc.id, "han", "han_title_suspect", suspect["pages"], suspect["text"]))

    return DocumentLayers(lines=lines_by_layer, headings=headings,
                          metadata_rows=metadata_rows, uncertain=uncertain, review=review)


HAN_TITLE_SUSPECT_RATIO = 0.5


def han_title_suspect(metadata_rows: list[dict]) -> dict | None:
    """Tiêu đề Hán nghi là rác OCR (tiêu đề chữ to): số chữ Hán lệch số âm tiết của tiêu đề âm > 50% (và > 2).
    Chỉ cảnh báo review, không sửa dữ liệu."""
    def first_title(layer):
        for row in metadata_rows:
            if row.get("layer") == layer:
                for f in row.get("fields", []):
                    if f.get("field") == "title":
                        return f
        return None
    han, am = first_title("han"), first_title("am")
    if not han or not am:
        return None
    n_han = sum(1 for c in han["text"] if "㐀" <= c <= "鿿" or "豈" <= c <= "﫿")
    n_am = len(re.findall(r"[^\W\d_]+", am["text"]))
    if not n_han or not n_am:
        return None
    gap = abs(n_han - n_am)
    if gap > 2 and gap > HAN_TITLE_SUSPECT_RATIO * max(n_han, n_am):
        return {"text": f"{han['text']} ({n_han} chữ) / {am['text']} ({n_am} âm tiết)", "pages": han.get("pages", [])}
    return None
