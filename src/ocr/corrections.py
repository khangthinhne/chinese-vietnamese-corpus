"""Sửa tay OCR: data/<W>/03_ocr/corrections/corrections.tsv (chỉ ghi thêm), áp ở build_layers.

    python -m src.ocr.corrections add --work-id CPO_18 --line-id vie.P0402.S004 --delete \
        --note "Qwen bịa từ mực thấm mặt sau" --editor "Claude (đối chiếu ảnh trang)"
    python -m src.ocr.corrections add --work-id CPO_18 --line-id vie.P0528.S002 --new "..." --note ... --editor ...
    python -m src.ocr.corrections check --work-id CPO_18      # định dạng + chỉ-ghi-thêm so với git HEAD

Cột TSV (UTF-8, tab, có hàng tiêu đề): line_id, text_old, text_new, note, editor, date.
- `line_id` = `<luồng>.P<trang 4 số>.S<SRC_LINE>`, ví dụ `vie.P0531.S009`. SRC_LINE là số thứ tự dòng gốc của
  engine (thuộc tính SRC_LINE trong han.xml/vie.xml). Không dùng ID `PNNNN.LNNN` vì ID đó được đánh lại sau lọc.
- `text_old` phải trùng text của dòng sau lọc (text trong XML trước khi sửa). Lệch thì KHÔNG áp, chỉ ghi cảnh báo
  (run OCR đổi, hoặc dòng đã được sửa khác).
- `text_new` rỗng = xoá dòng (dòng chuyển sang danh sách loại, lý do `correction_delete`).
- Áp theo thứ tự trong file; một dòng có thể sửa nối tiếp (hàng sau có text_old = text_new của hàng trước).
- File chỉ được ghi thêm: sửa sai thì thêm hàng mới, không xoá/sửa hàng cũ. JSON thô không bao giờ bị đụng.
"""

from __future__ import annotations

import argparse
import datetime as dt
import logging
import re
import subprocess
import sys
import unicodedata
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path

from src.common.corpus import DataLayout

log = logging.getLogger(__name__)

COLUMNS = ("line_id", "text_old", "text_new", "note", "editor", "date")
LINE_ID_RE = re.compile(r"^(han|vie)\.P(\d{4})\.S(\d+)$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
DELETE_REASON = "correction_delete"


@dataclass(frozen=True)
class Correction:
    row: int            # số hàng trong file (1 = tiêu đề)
    line_id: str
    layer: str
    page: int
    src_line: int
    old: str
    new: str
    note: str
    editor: str
    date: str

    @property
    def is_delete(self) -> bool:
        return self.new == ""


def norm(text: str) -> str:
    return unicodedata.normalize("NFC", text or "").strip()


def parse_line_id(line_id: str) -> tuple[str, int, int]:
    m = LINE_ID_RE.match(line_id)
    if not m:
        raise ValueError(f"line_id sai dạng (cần <han|vie>.PNNNN.S<số>): {line_id!r}")
    return m.group(1), int(m.group(2)), int(m.group(3))


def parse_rows(text: str, source: str = "corrections.tsv") -> list[Correction]:
    lines = text.splitlines()
    if not lines:
        return []
    header = lines[0].lstrip("﻿").split("\t")
    if tuple(header) != COLUMNS:
        raise ValueError(f"{source}: tiêu đề phải là {COLUMNS}, có {tuple(header)}")
    out = []
    for k, line in enumerate(lines[1:], 2):
        if not line.strip():
            continue
        cells = line.split("\t")
        if len(cells) != len(COLUMNS):
            raise ValueError(f"{source}:{k}: cần {len(COLUMNS)} cột, có {len(cells)}")
        line_id, old, new, note, editor, date = (c.strip() for c in cells)
        layer, page, src_line = parse_line_id(line_id)
        if not old:
            raise ValueError(f"{source}:{k}: text_old rỗng")
        if not editor or not DATE_RE.match(date):
            raise ValueError(f"{source}:{k}: thiếu người sửa hoặc ngày (YYYY-MM-DD)")
        out.append(Correction(k, line_id, layer, page, src_line, norm(old), norm(new), note, editor, date))
    return out


def load_corrections(path: Path) -> list[Correction]:
    if not path.is_file():
        return []
    return parse_rows(path.read_text(encoding="utf-8"), str(path))


def by_page(corrections: list[Correction], layer: str) -> dict[int, list[Correction]]:
    out: dict[int, list[Correction]] = {}
    for c in corrections:
        if c.layer == layer:
            out.setdefault(c.page, []).append(c)
    return out


def apply_page(kept: list[tuple[int, str]], rejected: list[tuple[int, str, str]], src_lines: list[dict],
               corrections: list[Correction]) -> list[dict]:
    """Áp sửa cho MỘT trang, sửa tại chỗ `kept` [(chỉ số src_lines 1-based, text)] và `rejected`.
    Trả về nhật ký từng hàng: status = applied | deleted | text_mismatch | line_filtered | line_not_found."""
    log_rows = []
    for c in corrections:
        hits = [k for k, (src, _) in enumerate(kept) if src_lines[src - 1].get("src_line") == c.src_line]
        rec = {"row": c.row, "line_id": c.line_id, "editor": c.editor, "date": c.date}
        if not hits:
            dropped = any(src_lines[src - 1].get("src_line") == c.src_line for src, _, _ in rejected)
            log_rows.append({**rec, "status": "line_filtered" if dropped else "line_not_found"})
            continue
        match = next((k for k in hits if norm(kept[k][1]) == c.old), None)
        if match is None:
            log_rows.append({**rec, "status": "text_mismatch", "current": kept[hits[0]][1]})
            continue
        src, text = kept[match]
        if c.is_delete:
            del kept[match]
            rejected.append((src, DELETE_REASON, text))
            log_rows.append({**rec, "status": "deleted", "old": text})
        else:
            kept[match] = (src, c.new)
            log_rows.append({**rec, "status": "applied", "old": text, "new": c.new})
    return log_rows


# --------------------------------------------------------------------------- #
# Ghi thêm và kiểm tra
# --------------------------------------------------------------------------- #
def is_append_only(before: str, after: str) -> bool:
    """`after` giữ nguyên mọi hàng của `before` theo đúng thứ tự, chỉ thêm hàng ở cuối."""
    b = [x for x in before.replace("\r\n", "\n").split("\n") if x.strip()]
    a = [x for x in after.replace("\r\n", "\n").split("\n") if x.strip()]
    return a[:len(b)] == b


def clean_cell(value: str) -> str:
    if "\t" in value or "\n" in value or "\r" in value:
        raise ValueError(f"Ô không được chứa tab hoặc xuống dòng: {value!r}")
    return value


def append_rows(path: Path, rows: list[dict]) -> None:
    """Chỉ ghi thêm (mở 'a'); tạo file kèm tiêu đề nếu chưa có. Hàng được kiểm tra bằng parse_rows trước khi ghi."""
    new = not path.exists()
    body = "".join("\t".join(clean_cell(str(r.get(c, ""))) for c in COLUMNS) + "\n" for r in rows)
    parse_rows("\t".join(COLUMNS) + "\n" + body, str(path))
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8", newline="") as fh:
        if new:
            fh.write("\t".join(COLUMNS) + "\n")
        fh.write(body)


def current_line_text(layout: DataLayout, line_id: str) -> list[str]:
    """Text sau lọc của các dòng có SRC_LINE này trong han.xml/vie.xml hiện tại (một dòng engine có thể tách thành
    nhiều mảnh)."""
    layer, page, src_line = parse_line_id(line_id)
    path = layout.ocr_layer_xml_path(layer)
    root = ET.parse(path).getroot()
    out = []
    for page_el in root.iter("PAGE"):
        if page_el.get("NUMBER") != str(page):
            continue
        for line in page_el.iter("LINE"):
            if line.get("SRC_LINE") == str(src_line):
                out.append(line.text or "")
    return out


def git_head_text(path: Path) -> str | None:
    try:
        top = subprocess.run(["git", "rev-parse", "--show-toplevel"], cwd=path.parent, capture_output=True,
                             text=True, check=True).stdout.strip()
        rel = path.resolve().relative_to(Path(top).resolve()).as_posix()
        res = subprocess.run(["git", "show", f"HEAD:{rel}"], cwd=top, capture_output=True, check=False)
    except (OSError, subprocess.CalledProcessError, ValueError):
        return None
    return res.stdout.decode("utf-8") if res.returncode == 0 else None


def cmd_add(args) -> int:
    layout = DataLayout(Path(args.data_root), args.work_id)
    if args.delete == (args.new is not None):
        raise ValueError("Cần đúng một trong --new hoặc --delete")
    old = args.old
    if old is None:
        texts = current_line_text(layout, args.line_id)
        if len(texts) != 1:
            raise ValueError(f"{args.line_id}: tìm thấy {len(texts)} dòng trong XML hiện tại; ghi rõ --old")
        old = texts[0]
    row = {"line_id": args.line_id, "text_old": norm(old), "text_new": "" if args.delete else norm(args.new),
           "note": args.note, "editor": args.editor, "date": args.date or dt.date.today().isoformat()}
    append_rows(layout.ocr_corrections_path, [row])
    log.info("Đã thêm %s vào %s", args.line_id, layout.ocr_corrections_path)
    return 0


def cmd_check(args) -> int:
    layout = DataLayout(Path(args.data_root), args.work_id)
    path = layout.ocr_corrections_path
    rows = load_corrections(path)
    log.info("%s: %d hàng hợp lệ", path, len(rows))
    head = git_head_text(path) if path.exists() else None
    if head is not None and not is_append_only(head, path.read_text(encoding="utf-8")):
        log.error("%s đã bị sửa/xoá hàng so với git HEAD (chỉ được ghi thêm)", path)
        return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    for name in ("add", "check"):
        s = sub.add_parser(name)
        s.add_argument("--work-id", required=True)
        s.add_argument("--data-root", default="data")
        if name == "add":
            s.add_argument("--line-id", required=True)
            s.add_argument("--new")
            s.add_argument("--delete", action="store_true")
            s.add_argument("--old", help="mặc định: đọc từ han.xml/vie.xml hiện tại")
            s.add_argument("--note", required=True)
            s.add_argument("--editor", required=True)
            s.add_argument("--date")
    args = p.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    try:
        return cmd_add(args) if args.cmd == "add" else cmd_check(args)
    except (OSError, ValueError) as exc:
        log.error("%s", exc)
        return 1


if __name__ == "__main__":
    sys.exit(main())
