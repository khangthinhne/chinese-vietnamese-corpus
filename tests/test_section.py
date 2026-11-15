"""Unit test cho extract_section (start_after / end_before) trên RawLine GIẢ LẬP.

Chạy từ thư mục gốc repo:  python -m pytest tests/test_section.py -q
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.preprocessing.clean import RawLine, extract_section, SectionInfo  # noqa: E402

WORK = "T_01"


def rl(i: int, page: int, text: str, last: bool = False) -> RawLine:
    return RawLine(f"{WORK}.am.{page:03d}.L{i:03d}", page, text, last)


def ids(lines: list[RawLine]) -> list[str]:
    return [ln.id for ln in lines]


def pages(lines: list[RawLine]) -> list[int]:
    return [ln.page for ln in lines]


# 1. Cả hai ranh giới cùng một trang
def test_both_boundaries_same_page():
    lines = [rl(1, 205, "PHIÊN ÂM:"), rl(2, 205, "phù đạo xứ thanh hư"), rl(3, 205, "chân hình ư thế?"),
             rl(4, 205, "DỊCH NGHĨA:"), rl(5, 205, "Đạo vốn ở nơi thanh tịnh")]
    sel, info = extract_section(lines, r"^Phiên âm", r"^Dịch nghĩa")
    assert ids(sel) == [lines[1].id, lines[2].id]
    assert info.status == "ok" and info.start_found and info.end_found
    assert info.n_selected == 2 and info.first_page == info.last_page == 205
    assert [(ln.id, r) for ln, r in info.excluded] == [
        (lines[0].id, "start_heading"), (lines[3].id, "end_heading"), (lines[4].id, "after_end")]


# 2. Ranh giới trải qua nhiều trang (start trang 1, end trang 2)
def test_boundaries_across_pages():
    lines = [rl(1, 207, "PHIÊN ÂM:"), rl(2, 207, "nội dung trang 207"),
             rl(3, 208, "tiếp tục trang 208"), rl(4, 208, "DỊCH NGHĨA:")]
    sel, info = extract_section(lines, r"^PHIÊN ÂM", r"^Dịch nghĩa")
    assert ids(sel) == [lines[1].id, lines[2].id]
    assert pages(sel) == [207, 208] and info.status == "ok" and info.end_found
    assert info.first_page == 207 and info.last_page == 208


# 3. Start ở trang 1, end ở trang 3
def test_start_page1_end_page3():
    lines = [rl(1, 1, "PHIÊN ÂM:"), rl(2, 1, "a"), rl(3, 2, "b"), rl(4, 3, "c"),
             rl(5, 3, "DỊCH NGHĨA:"), rl(6, 3, "d")]
    sel, info = extract_section(lines, r"^Phiên âm", r"^Dịch nghĩa")
    assert ids(sel) == ["T_01.am.001.L002", "T_01.am.002.L003", "T_01.am.003.L004"]
    assert pages(sel) == [1, 2, 3] and info.n_selected == 3


# 4. Chỉ start_after
def test_only_start_after():
    lines = [rl(1, 1, "PHIÊN ÂM:"), rl(2, 1, "nội dung"), rl(3, 2, "DỊCH NGHĨA:"), rl(4, 2, "sau cùng")]
    sel, info = extract_section(lines, r"^Phiên âm")
    assert ids(sel) == [lines[1].id, lines[2].id, lines[3].id]
    assert info.status == "ok"
    assert info.end_pattern is None and info.end_found is False


# 5. Chỉ end_before
def test_only_end_before():
    lines = [rl(1, 1, "Đầu sách"), rl(2, 1, "nội dung"), rl(3, 2, "CHÚ THÍCH:"), rl(4, 2, "chú 1")]
    sel, info = extract_section(lines, None, r"^Chú thích")
    assert ids(sel) == [lines[0].id, lines[1].id]
    assert info.start_found is True and info.end_found is True and info.status == "ok"


# 6. Không cấu hình ranh giới nào -> giữ nguyên hành vi cũ
def test_no_boundaries_returns_input_unchanged():
    lines = [rl(1, 1, "abc"), rl(2, 2, "def", last=True)]
    sel, info = extract_section(lines)
    assert sel == lines and info.status == "unbounded"
    assert info.n_selected == 2 and info.excluded == []


# 7. Khớp không phân biệt hoa/thường + dấu câu cuối
def test_case_insensitive():
    lines = [rl(1, 1, "Phiên Âm :"), rl(2, 1, "x"), rl(3, 1, "dịch NGHĨA :"), rl(4, 1, "y")]
    sel, _ = extract_section(lines, r"^Phiên âm", r"^Dịch nghĩa")
    assert ids(sel) == [lines[1].id]


# 8. Khoảng trắng quanh tiêu đề
def test_whitespace_around_headings():
    lines = [rl(1, 1, "   \u00a0 PHIÊN ÂM:  "), rl(2, 1, "x"), rl(3, 1, "  DỊCH NGHĨA:\t"), rl(4, 1, "y")]
    sel, info = extract_section(lines, r"^\s*PHIÊN ÂM", r"^\s*DỊCH NGHĨA")
    assert ids(sel) == [lines[1].id] and info.status == "ok"


# 9. Thiếu tiêu đề start -> lỗi rõ ràng
def test_missing_start_heading_raises():
    lines = [rl(1, 1, "abc"), rl(2, 1, "PHIÊN ÂM:")]
    with pytest.raises(ValueError, match="start_after"):
        extract_section(lines, r"^MỤC LỤC")


# 10. Thiếu tiêu đề end -> đánh dấu incomplete, không lỗi chết
def test_missing_end_heading_marked_incomplete():
    lines = [rl(1, 1, "PHIÊN ÂM:"), rl(2, 1, "nội dung")]
    sel, info = extract_section(lines, r"^Phiên âm", r"^Dịch nghĩa")
    assert ids(sel) == [lines[1].id] and info.status == "incomplete_end"
    assert info.start_found and not info.end_found


# 11. Regex sai -> lỗi cấu hình
def test_invalid_regex_raises():
    lines = [rl(1, 1, "abc")]
    with pytest.raises(ValueError, match=r"start_after.*'\(unclosed'"):
        extract_section(lines, r"(unclosed")
    with pytest.raises(ValueError, match=r"end_before.*'\[bad'"):
        extract_section(lines, None, r"[bad")


# 12. Đoạn cắt rỗng -> lỗi
def test_empty_section_raises():
    lines = [rl(1, 1, "PHIÊN ÂM:"), rl(2, 1, "DỊCH NGHĨA:")]
    with pytest.raises(ValueError, match="rỗng"):
        extract_section(lines, r"^Phiên âm", r"^Dịch nghĩa")
    with pytest.raises(ValueError, match="rỗng"):
        extract_section([rl(1, 1, "CHÚ THÍCH:")], None, r"^Chú thích")


# 13. Giữ nguyên ID/trang/is_last_on_page, không hiệu chỉnh text
def test_preserves_ids_pages_text():
    lines = [rl(1, 205, "PHIÊN ÂM:"), rl(2, 205, "  giữ nguyên text  ", last=True),
             rl(3, 206, "DỊCH NGHĨA:")]
    sel, _ = extract_section(lines, r"^Phiên âm", r"^Dịch nghĩa")
    assert len(sel) == 1
    assert sel[0].id == lines[1].id and sel[0].page == 205 and sel[0].is_last_on_page
    assert sel[0].text == lines[1].text          # không đụng text thô


# 14a. Tương tác với chuỗi làm sạch: chủ yêu -> raw -> clean -> câu giữ đúng nguồn
def test_with_clean_and_split(tmp_path):
    from src.common.corpus import DataLayout, RawPage, build_document, make_meta, write_xml
    from src.common.sentences import load_sentences
    from src.preprocessing import clean

    layout = DataLayout(tmp_path, WORK)
    layout.ensure()
    doc = build_document(WORK, "nghia", make_meta("nghia", title="t", method="m"),
                         [RawPage(1, ["PHẦN II: mục lục", "DỊCH NGHĨA:", "Đạo vốn ở nơi thanh tịnh hư vô.",
                                      "Bốn đại có lẽ thường.", "CHÚ THÍCH:", "chú 1", "chú 2"])])
    write_xml(doc, layout.layer_xml("nghia"), force=True)
    argv = ["--work-id", WORK, "--layer", "nghia", "--data-root", str(tmp_path), "--force",
            "--start-after", r"^Dịch nghĩa", "--end-before", r"^Chú thích"]
    assert clean.main(argv) == 0
    sents = load_sentences(layout, "nghia")
    assert [s.text for s in sents] == ["Đạo vốn ở nơi thanh tịnh hư vô.", "Bốn đại có lẽ thường."]
    all_ids = [i for s in sents for i in s.src_ids]
    assert all(i.endswith(("L003", "L004")) for i in all_ids)   # không dính dòng ngoài đoạn
    dropped = (tmp_path / WORK / "03_cleaned" / "nghia.dropped.tsv").read_text(encoding="utf-8")
    assert "section_before_start" in dropped and "section_after_end" in dropped


# 14b. SectionInfo.as_dict phục vụ metadata
def test_section_info_dict():
    info = SectionInfo(status="ok", start_pattern="a", end_pattern="b",
                       start_found=True, end_found=True, first_page=1, last_page=2, n_selected=3)
    d = info.as_dict()
    assert d["status"] == "ok" and d["n_excluded"] == 0 and "excluded" not in d
