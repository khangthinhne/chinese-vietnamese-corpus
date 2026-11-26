"""Bước 6: làm sạch nhiễu OCR + xoá chỉ mục chú thích trong dòng + đánh dấu ký tự không chắc.

Nguyên tắc:
- CHỈ xoá/thay theo quy tắc cấu hình (mặc định rỗng = không xoá mù quáng gì cả).
- Chỉ mục chú thích [1], (1), ［1］, （1）, ①... được xoá khi config bật (mặc định bật,
  đúng như clean.py cũ) — chỉ nhận CHỮ SỐ nên [Phật], [muôn], [người] được giữ nguyên.
- Ký tự nhiễu biết chắc (NOISE_CHARS: ®, °, Ø, ‡...) được XOÁ (rule noise_char:<ch>).
- Ký tự nghi vấn còn lại (`, chữ Latin đứng một mình...) CHỈ ĐÁNH DẤU để review,
  KHÔNG xoá — mỗi cờ được lưu vào review_required.tsv qua Audit.
- Không chắc thì không xoá; mọi thao tác đều ghi Audit.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from src.cleaning.audit import Audit
from src.cleaning.model import AM_DIGIT_MARKER_RE, LETTER_MARKER_RE, MARKER_RE, OCRCleaningConfig, OCR_ARTIFACT, RawLine, UNCERTAIN

# chữ Latin đơn lẻ / chữ+cục số không thuộc từ: "lợi N cho", "nước ta. M4"
_ISOLATED_LATIN = re.compile(r"(?<![A-Za-zÀ-ỹđĐ])([A-Za-z]|[A-Za-z]\d)(?![A-Za-zÀ-ỹđĐ])")


@dataclass
class CleaningResult:
    text: str
    removed: list[tuple[str, str]] = field(default_factory=list)        # (rule, đoạn bị xoá)
    replaced: list[tuple[str, str, str]] = field(default_factory=list)  # (rule, trước, sau)
    uncertain: list[str] = field(default_factory=list)                  # ký/token được cờ


VIET_LAYERS = ("am", "nghia", "tho")


def clean_ocr_artifacts(text: str, config: OCRCleaningConfig,
                        audit: Audit | None = None, line: RawLine | None = None,
                        layer: str | None = None) -> CleaningResult:
    res = CleaningResult(text=text)
    out = text
    if config.strip_markers:
        out, n = MARKER_RE.subn("", out)
        for m in MARKER_RE.finditer(text):
            res.removed.append(("in_text_marker", m.group(0)))
        if n and audit is not None and line is not None:
            audit.add("remove_marker", "in_text_marker", rule="MARKER_RE", line=line,
                      original=text, processed=out)
    if config.strip_markers and config.strip_letter_markers and layer in VIET_LAYERS:
        before = out
        found = [m.group(0) for m in LETTER_MARKER_RE.finditer(out)]
        out = LETTER_MARKER_RE.sub("", out)
        res.removed.extend(("letter_marker", m) for m in found)
        if found and audit is not None and line is not None:
            audit.add("remove_marker", "in_text_marker", rule="LETTER_MARKER_RE", line=line,
                      original=before, processed=out)
    if config.strip_markers and layer == "am":
        before = out
        found = [m.group(0).strip() for m in AM_DIGIT_MARKER_RE.finditer(out)]
        out = AM_DIGIT_MARKER_RE.sub("", out)
        res.removed.extend(("am_digit_marker", m) for m in found)
        if found and audit is not None and line is not None:
            audit.add("remove_marker", "in_text_marker", rule="AM_DIGIT_MARKER_RE", line=line,
                      original=before, processed=out)

    for pat in config.remove_patterns:
        rx = re.compile(pat)
        out, n = rx.subn("", out)
        if n:
            # print(f"remove {n} match(es) of {pat!r} from {text!r} -> {out!r}")
            res.removed.extend((pat, m.group(0)) for m in rx.finditer(text))
            if audit is not None and line is not None:
                audit.add("remove_artifact", "ocr_artifact", rule=pat, line=line,
                          original=text, processed=out)

    for pat, repl in config.replace_patterns:
        rx = re.compile(pat)
        if rx.search(out):
            before = out
            out = rx.sub(repl, out)
            res.replaced.append((pat, before, out))
            if audit is not None and line is not None:
                audit.add("replace_artifact", "ocr_artifact", rule=f"{pat}=>{repl}",
                          line=line, original=before, processed=out)

    # ký tự nghi nhiễu: chỉ cờ review, giữ nguyên văn
    for ch in config.suspect_chars:
        if ch in out:
            res.uncertain.append(ch)

    # ký tự nhiễu OCR biết chắc (NOISE_CHARS): xoá, mỗi ký tự bị xoá được ghi audit
    for ch in config.noise_chars:
        if ch in out:
            before = out
            out = out.replace(ch, "")
            res.removed.append(("noise_char", ch))
            if audit is not None and line is not None:
                audit.add("remove_artifact", OCR_ARTIFACT, rule=f"noise_char:{ch}",
                          line=line, original=before, processed=out)

    res.uncertain.extend(m.group(0) for m in _ISOLATED_LATIN.finditer(out))
    res.text = out
    return res


def clean_lines(lines: list[RawLine], cfg: OCRCleaningConfig, audit: Audit, layer: str) -> list[RawLine]:
    """Áp dụng cho từng dòng; cờ uncertain gắn lên RawLine.flags (han không cờ Latin)."""
    out: list[RawLine] = []
    for ln in lines:
        res = clean_ocr_artifacts(ln.text, cfg, audit, ln, layer=layer)
        new = RawLine(ln.id, ln.page, res.text, ln.is_last_on_page, ln.raw_text, list(ln.flags),
                      list(ln.source_ids), ln.document_id)
        for ch in res.uncertain:
            flag = f"uncertain:{ch}"
            if flag not in new.flags:
                new.flags.append(flag)
                if layer != "han":
                    audit.flag_review(new, UNCERTAIN, rule="suspect_char", note=f"ký tự {ch!r}")
        out.append(new)
    return out
