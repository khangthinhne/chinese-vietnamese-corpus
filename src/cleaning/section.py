"""Bước 3: cắt đoạn theo ranh giới tiêu đề (start_after / end_before) — từ clean.py cũ.

Chạy TRƯỚC phát hiện chú thích/phát hiện trùng: ranh giới là quyết định cấp cao của
config, cắt xong các bước sau chỉ phân tích nội dung được chọn. Nhờ vậy rác OCR trước
ranh giới (vd "(2503%3#+—-…" không đóng ngoặc) không thể nuốt cả đoạn thành "chú thích".
"""

from __future__ import annotations

import logging
import re
import unicodedata
from dataclasses import dataclass, field

from src.cleaning.model import RawLine

log = logging.getLogger("clean.section")


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
    excluded: list[tuple[RawLine, str]] = field(default_factory=list)  # (dòng, lý do)

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
        if not collecting:
            if start_re.search(probe):     # dòng tiêu đề start: ghi nhận, lấy từ dòng kế
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
            f"kiểm tra regex/trang OCR (trang: {sorted({ln.page for ln in lines})[:5]}...)")
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
