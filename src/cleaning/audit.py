"""Audit: ghi lại MỌI xoá/thay/nối/đánh dấu với (work, layer, trang, ID, lý do, quy tắc).

- Audit.rows           -> cleaning_audit.xml (toàn bộ thao tác)
- Audit.reviews        -> review_required.xml + .tsv (trường hợp không chắc)
- Các file chuyên (footnotes.xml, duplicates.xml, reconstruction.xml, review_required.xml)
  do pipeline ghi từ kết quả có cấu trúc của từng stage, không lọc lại từ audit.
"""

from __future__ import annotations

from src.common.sentences import atomic_write_text
from src.common.xmlstore import (add, file_element, opt_text_of, read_root, text_of,
                                 write_file_root, write_root)


def _merged(path, work_id: str, layer: str):
    """Gộp theo lớp: thay các phần tử của lớp hiện tại, giữ nguyên lớp khác."""
    if path.exists():
        root = read_root(path)
        file_el = file_element(root)
        for child in list(file_el):
            if child.get("LAYER") == layer:
                file_el.remove(child)
    else:
        root = write_file_root(path, work_id)
        file_el = file_element(root)
    return root, file_el


class Audit:
    def __init__(self, work_id: str, layer: str):
        self.work_id = work_id
        self.layer = layer
        self.rows: list[dict] = []
        self.reviews: list[dict] = []

    def add(self, action: str, reason: str, rule: str = "", line=None,
            original: str | None = None, processed: str | None = None, note: str = "") -> dict:
        row = {
            "work_id": self.work_id, "layer": self.layer, "action": action, "reason": reason,
            "rule": rule,
            "page": line.page if line is not None else None,
            "line_id": line.id if line is not None else None,
            "original": original if original is not None else (line.text if line is not None else None),
            "processed": processed,
            "note": note,
        }
        self.rows.append(row)
        return row

    def flag_review(self, line, reason: str, note: str = "", rule: str = "") -> dict:
        row = {"work_id": self.work_id, "layer": self.layer, "reason": reason, "rule": rule,
               "page": line.page, "line_id": line.id,
               "original": getattr(line, "raw_text", "") or line.text, "text": line.text,
               "note": note}
        self.reviews.append(row)
        return row

    @property
    def n_reason(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for r in self.rows:
            out[r["reason"]] = out.get(r["reason"], 0) + 1
        return out


def _write_audit(path, audit: Audit) -> None:
    root, file_el = _merged(path, audit.work_id, audit.layer)
    for row in audit.rows:
        el = add(file_el, "RECORD", LAYER=audit.layer, ACTION=row.get("action"),
                 REASON=row.get("reason"), RULE=row.get("rule") or None,
                 PAGE=row.get("page"), LINE_ID=row.get("line_id"),
                 NOTE=row.get("note") or None)
        if row.get("original") is not None:
            add(el, "ORIGINAL", row["original"])
        if row.get("processed") is not None:
            add(el, "PROCESSED", row["processed"])
    write_root(path, root)


def _write_footnotes(path, work_id: str, layer: str, notes: list) -> None:
    root, file_el = _merged(path, work_id, layer)
    for note in notes:
        rec = note.to_record()
        el = add(file_el, "NOTE", LAYER=layer, ID=rec.get("note_id"),
                 CATEGORY=rec.get("category"), ACTION=rec.get("action"),
                 REASON=rec.get("reason"),
                 PAGES=",".join(str(p) for p in rec.get("pages", [])) or None,
                 LINE_IDS=",".join(rec.get("line_ids", [])) or None,
                 CONFIDENCE=rec.get("confidence"))
        for line in rec.get("lines", []):
            add(el, "LINE", line.get("text", ""), ID=line.get("id"), PAGE=line.get("page"))
    write_root(path, root)


def _write_duplicates(path, work_id: str, layer: str, rows: list[dict]) -> None:
    root, file_el = _merged(path, work_id, layer)
    for row in rows:
        add(file_el, "DUPLICATE", row.get("text", ""), LAYER=layer,
            KEPT_ID=row.get("kept_id"), REMOVED_ID=row.get("removed_id"),
            PAGE=row.get("page"), SIMILARITY=row.get("similarity"))
    write_root(path, root)


def _write_reconstruction(path, work_id: str, layer: str, rows: list[dict]) -> None:
    root, file_el = _merged(path, work_id, layer)
    for row in rows:
        el = add(file_el, "OP", LAYER=layer, TYPE=row.get("type"), FROM_ID=row.get("from_id"),
                 TO_ID=row.get("to_id"), FROM_PAGE=row.get("from_page"),
                 TO_PAGE=row.get("to_page"), CONFIDENCE=row.get("confidence"))
        if row.get("before") is not None:
            add(el, "BEFORE", row["before"])
        if row.get("after") is not None:
            add(el, "AFTER", row["after"])
    write_root(path, root)


def _write_reviews(path, audit: Audit) -> None:
    root, file_el = _merged(path, audit.work_id, audit.layer)
    for row in audit.reviews:
        el = add(file_el, "REVIEW", REASON=row.get("reason"), RULE=row.get("rule") or None,
                 LAYER=audit.layer, PAGE=row.get("page"), LINE_ID=row.get("line_id"),
                 NOTE=row.get("note") or None)
        if row.get("original") is not None:
            add(el, "ORIGINAL", row["original"])
        if row.get("text") is not None:
            add(el, "TEXT", row["text"])
    write_root(path, root)


def _write_review_tsv(path, root) -> None:
    rows = []
    for el in file_element(root).findall("REVIEW"):
        text = opt_text_of(el.find("TEXT")) or ""
        note = el.get("NOTE") or ""
        rows.append(
            f"{el.get('LINE_ID', '')}\t{el.get('PAGE', '')}\t{el.get('REASON', '')}\t"
            f"{el.get('RULE', '')}\t{text.replace(chr(9), ' ').replace(chr(10), ' ')}\t"
            f"{note}\t{el.get('LAYER', '')}\n")
    atomic_write_text(path, "id\tpage\treason\trule\ttext\tnote\tlayer\n" + "".join(rows))


def write_audit_files(out_dir, audit: Audit, notes: list, duplicates: list,
                      reconstructions: list) -> None:
    """Ghi file kiểm toán theo lớp; các lớp khác trong cùng file được giữ nguyên."""
    _write_audit(out_dir / "cleaning_audit.xml", audit)
    _write_footnotes(out_dir / "footnotes.xml", audit.work_id, audit.layer, notes)
    _write_duplicates(out_dir / "duplicates.xml", audit.work_id, audit.layer, duplicates)
    _write_reconstruction(out_dir / "reconstruction.xml", audit.work_id, audit.layer,
                          reconstructions)
    reviews = out_dir / "review_required.xml"
    _write_reviews(reviews, audit)
    _write_review_tsv(out_dir / "review_required.tsv", read_root(reviews))
