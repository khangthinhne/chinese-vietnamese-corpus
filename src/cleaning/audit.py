"""Audit: ghi lại MỌI xoá/thay/nối/đánh dấu với (work, layer, trang, ID, lý do, quy tắc).

- Audit.rows           -> cleaning_audit.jsonl (toàn bộ thao tác)
- Audit.reviews        -> review_required.tsv (trường hợp không chắc, giữ nguyên + trình người duyệt)
- Các file chuyên (footnotes.jsonl, duplicates.jsonl, reconstruction.jsonl) do pipeline
  ghi từ kết quả có cấu trúc của từng stage, không lọc lại từ audit.
"""

from __future__ import annotations

from src.common.sentences import atomic_write_text, write_jsonl


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


def write_audit_files(out_dir, audit: Audit, notes: list, duplicates: list,
                      reconstructions: list) -> None:
    """Ghi các file kiểm toán vào 03_cleaned (luôn ghi để tái lập được)."""
    write_jsonl(out_dir / "cleaning_audit.jsonl", audit.rows)
    write_jsonl(out_dir / "footnotes.jsonl", (n.to_record() for n in notes))
    write_jsonl(out_dir / "duplicates.jsonl", duplicates)
    write_jsonl(out_dir / "reconstruction.jsonl", reconstructions)
    tsv = "id\tpage\treason\trule\ttext\tnote\n" + "".join(
        f"{r['line_id']}\t{r['page']}\t{r['reason']}\t{r['rule']}\t"
        f"{r['text'].replace(chr(9), ' ').replace(chr(10), ' ')}\t{r['note']}\n"
        for r in audit.reviews)
    atomic_write_text(out_dir / "review_required.tsv", tsv)
