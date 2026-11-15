"""Regex cấu hình cho phân đoạn tài liệu/lớp (NFC, không phân biệt hoa thường mặc định)."""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable

# Tiền tố OCR thường gặp trước heading: "l PHIÊN ÂM : |", "I DỊCH NGHĨA".
# Chỉ một ký tự nhiễu + khoảng trắng; có thể thay bằng heading_prefix_pattern.
DEFAULT_HEADING_PREFIX = r"^(?:[lI|]\s+)?"
_DEFAULT_PREFIX = object()


def norm_text(text: str) -> str:
    return " ".join(unicodedata.normalize("NFC", text).split())


def compile_patterns(patterns: Iterable[str] | None, name: str,
                     case_sensitive: bool = False) -> list[re.Pattern]:
    flags = 0 if case_sensitive else re.IGNORECASE
    compiled = []
    for i, pattern in enumerate(patterns or []):
        text = str(pattern)
        try:
            compiled.append(re.compile(text, flags))
        except re.error as exc:
            raise ValueError(f"Regex không hợp lệ ({name}[{i}]): {text!r}: {exc}") from exc
    return compiled


class Matcher:
    """Bộ khớp pattern; heading mode kiểm tra cả cấu trúc dòng.

    Chế độ thường giữ nguyên hành vi cũ (`search`). Heading mode yêu cầu pattern khớp
    ở ĐẦU dòng (sau tiền tố OCR hợp lệ) và phần còn lại chỉ gồm khoảng trắng/dấu câu/
    ký hiệu, nên câu văn chứa "phiên âm" không bị nhận nhầm là heading.
    """

    def __init__(self, patterns: Iterable[str] | None, name: str,
                 case_sensitive: bool = False, heading: bool = False,
                 prefix_pattern: str | None | object = _DEFAULT_PREFIX):
        self.patterns = compile_patterns(patterns, name, case_sensitive)
        self.heading = heading
        flags = 0 if case_sensitive else re.IGNORECASE
        self.prefix = None
        if heading and prefix_pattern is _DEFAULT_PREFIX:
            prefix_pattern = DEFAULT_HEADING_PREFIX
        if heading and prefix_pattern:
            try:
                self.prefix = re.compile(prefix_pattern, flags)
            except re.error as exc:
                raise ValueError(
                    f"Regex tiền tố heading không hợp lệ ({name}): "
                    f"{prefix_pattern!r}: {exc}") from exc

    def __len__(self) -> int:
        return len(self.patterns)

    def matches(self, text: str) -> bool:
        return self.matches_probe(norm_text(text))

    def matches_probe(self, probe: str) -> bool:
        if not self.heading:
            return any(p.search(probe) for p in self.patterns)
        if self.prefix:
            prefix = self.prefix.match(probe)
            if not prefix:
                return False
            probe = probe[prefix.end():]
        for pattern in self.patterns:
            match = pattern.match(probe)
            if match and _heading_tail_ok(probe[match.end():]):
                return True
        return False


def _heading_tail_ok(tail: str) -> bool:
    """Đuôi heading chỉ được có khoảng trắng, dấu câu hoặc ký hiệu OCR."""
    for ch in tail:
        if ch.isspace():
            continue
        if unicodedata.category(ch)[0] in ("P", "S"):
            continue
        return False
    return True


def compile_matcher(patterns: Iterable[str] | None, name: str,
                    case_sensitive: bool = False, heading: bool = False,
                    prefix_pattern: str | None | object = _DEFAULT_PREFIX) -> Matcher:
    return Matcher(patterns, name, case_sensitive, heading, prefix_pattern)


def matches(patterns: list[re.Pattern], text: str) -> bool:
    probe = norm_text(text)
    return any(p.search(probe) for p in patterns)


def matched_labels(groups: dict[str, Matcher | list[re.Pattern]], text: str) -> list[str]:
    probe = norm_text(text)
    out = []
    for name, group in groups.items():
        if isinstance(group, Matcher):
            hit = group.matches_probe(probe)
        else:
            hit = any(p.search(probe) for p in group)
        if hit:
            out.append(name)
    return out
