"""Dóng chữ Hán <-> âm tiết (dịch âm) bằng tool của Lab (SN-QN-alignment), theo spec_han_am_lab_tool.md.

    python -m src.alignment.lab_han_am --work-id CPO_19 [--data-root data] [--tool-dir third_party/SN-QN-alignment]
                                       [--quick N] [--force] [--dry-run] [--python PATH]
    python -m src.alignment.lab_han_am --input-csv third_party/SN-QN-alignment/data/sample-data.csv --out-dir X

Luồng: 06_alignment/han-am.xml (bead đã ghép cặp câu bằng aligner 'levenshtein')
  -> 06_alignment/lab/han-am_input.csv (ID,HanNom,DichAm,Fallback,Bbox) + han-am_input_original.csv
  -> config YAML sinh tự động (ĐƯỜNG DẪN TUYỆT ĐỐI) -> align_texts.py của Lab (subprocess, KHÔNG sửa code tool)
  -> 06_alignment/lab/out/ (.tsv, .xlsx, _color.xlsx, statistic.json) -> review, gợi ý OCR, bàn giao 07_export/han-am/

Aligner 'levenshtein' của repo chỉ GHÉP CẶP CÂU; kết quả dóng chữ là của tool Lab. Không bao giờ ghi đè
han.xml / 05_cleaning: gợi ý sửa OCR chỉ là danh sách để người rà soát quyết định.
"""

from __future__ import annotations

import argparse
import ast
import csv
import hashlib
import json
import logging
import re
import shutil
import subprocess
import sys
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger("lab_han_am")

DEFAULT_TOOL_DIR = Path("third_party/SN-QN-alignment")
TRANSLATION_DICT = Path("dict/QuocNgu_SinoNom_TongHop3.csv")
SIMILARITY_DICT = Path("dict/SinoNom_Similar_Dic_v2.csv")
SAMPLE_CONFIG = Path("configs/alignment_configs/sample-config.yaml")
MASK = "[MASK]"
HAN_PUNCT = "，。、；：！？「」『』（）《》〈〉·"
LATIN_PUNCT = ",.!?;:\"'()[]"
DASHES = "-‐‑‒–—―"
EXTRA_PUNCT = "“”‘’«»…"                  # nháy cong / chấm lửng trong bản phiên âm
UNALIGNED_LIMIT = 0.30                    # tiền kiểm: > 30% bead unaligned -> dừng
LENGTH_WARN = 0.20                        # lệch số chữ Hán / số âm tiết > 20% -> cảnh báo
MISMATCH_WARN = 0.10                      # mismatch_rate toàn tài liệu > 10% -> cảnh báo
ROW_ERROR_WARN = 0.30                     # dòng có > 30% vị trí lỗi -> cảnh báo
OUTPUT_KEYS = ("tsv", "xlsx", "color", "stats")

_HAN_RE = re.compile(r"[㐀-鿿豈-﫿\U00020000-\U0002ebef]")
_STRIP_HAN = re.compile("[" + re.escape(HAN_PUNCT + LATIN_PUNCT + DASHES + EXTRA_PUNCT) + r"\s]")
_STRIP_AM = re.compile("[" + re.escape(HAN_PUNCT + LATIN_PUNCT + DASHES + EXTRA_PUNCT) + "]")


# --------------------------------------------------------------------------- #
# Làm sạch & chuẩn hoá (hàm thuần)
# --------------------------------------------------------------------------- #
def clean_hannom(text: str) -> str:
    """Chuỗi Hán liền: bỏ khoảng trắng và dấu câu không tham gia đối sánh; giữ nguyên [MASK]."""
    return MASK.join(_STRIP_HAN.sub("", part) for part in unicodedata.normalize("NFC", text).split(MASK))


def clean_dicham(text: str) -> str:
    """Âm tiết cách nhau đúng một dấu cách, bỏ dấu câu, NFC; giữ số/viết tắt như nguồn."""
    text = _STRIP_AM.sub(" ", unicodedata.normalize("NFC", text))
    return " ".join(text.split())


def hannom_units(text: str) -> int:
    """Số đơn vị Hán tool sẽ đối sánh: mỗi chữ = 1, mỗi [MASK] = 1."""
    return text.count(MASK) + len(_HAN_RE.findall(text.replace(MASK, "")))


def load_lab_chars(tool_dir: Path) -> set[str]:
    """Tập chữ có trong từ điển dịch của Lab (cột SinoNom)."""
    with open(tool_dir / TRANSLATION_DICT, encoding="utf-8-sig", newline="") as fh:
        return {row["SinoNom"] for row in csv.DictReader(fh) if row.get("SinoNom")}


def to_traditional_hannom(text: str, known: set[str] | None = None, converter=None
                          ) -> tuple[str, list[tuple[int, str, str]]]:
    """Giản -> phồn (OpenCC s2t) TỪNG CHỮ, chỉ cho cột HanNom đưa vào tool. Trả về (text mới, [(vị trí, cũ, mới)]).

    Có `known` (chữ của từ điển Lab): chỉ đổi chữ KHÔNG có trong từ điển mà dạng phồn thể của nó có (tránh đổi
    chữ hợp lệ như 于 -> 於). [MASK] không bị đụng tới."""
    if converter is None:
        from src.alignment.aligners import to_traditional as converter
    out, changes, i = [], [], 0
    while i < len(text):
        if text.startswith(MASK, i):
            out.append(MASK)
            i += len(MASK)
            continue
        ch = text[i]
        new = converter(ch) if _HAN_RE.match(ch) else ch
        if new != ch and len(new) == 1 and (known is None or (ch not in known and new in known)):
            changes.append((i, ch, new))
            out.append(new)
        else:
            out.append(ch)
        i += 1
    return "".join(out), changes


# --------------------------------------------------------------------------- #
# Bead -> CSV
# --------------------------------------------------------------------------- #
@dataclass
class InputRows:
    rows: list[dict] = field(default_factory=list)        # ID, HanNom, DichAm, Fallback, Bbox
    originals: list[dict] = field(default_factory=list)   # truy vết/hoàn nguyên
    unaligned: int = 0
    total: int = 0
    length_warnings: list[str] = field(default_factory=list)


def precheck(beads: list[dict]) -> None:
    """Dừng nếu han-am.xml rỗng hoặc > 30% bead unaligned (gần như chắc chắn do tách câu Hán sai ở bước trước)."""
    if not beads:
        raise ValueError("han-am.xml rỗng: không có bead nào để đưa vào tool Lab")
    unaligned = sum(1 for b in beads if "unaligned" in b.get("flags", []))
    if unaligned / len(beads) > UNALIGNED_LIMIT:
        raise ValueError(f"{unaligned}/{len(beads)} bead han-am unaligned (> {UNALIGNED_LIMIT:.0%}): xem lại "
                         "tách câu Hán/ghép cặp trước khi chạy tool Lab")


def beads_to_rows(beads: list[dict], known: set[str] | None = None, converter=None) -> InputRows:
    """Mỗi bead có cả hai phía -> một dòng CSV (ID = id bead, duy nhất, ổn định). Bead unaligned bị loại và đếm."""
    out = InputRows(total=len(beads))
    seen: set[str] = set()
    for bead in beads:
        if "unaligned" in bead.get("flags", []) or not bead.get("src_ids") or not bead.get("tgt_ids"):
            out.unaligned += 1
            continue
        bid = bead["id"]
        if bid in seen:
            raise ValueError(f"ID bead trùng: {bid}")
        seen.add(bid)
        han_raw = bead.get("src_text", "")             # bead n-m: align.py đã nối Hán liền, âm cách một dấu cách
        am = clean_dicham(bead.get("tgt_text", ""))
        han_clean = clean_hannom(han_raw)
        han, changes = to_traditional_hannom(han_clean, known, converter)
        n_han, n_am = hannom_units(han), len(am.split())
        if max(n_han, n_am) and abs(n_han - n_am) / max(n_han, n_am) > LENGTH_WARN:
            out.length_warnings.append(f"{bid}: {n_han} chữ Hán / {n_am} âm tiết")
        out.rows.append({"ID": bid, "HanNom": han, "DichAm": am, "Fallback": "", "Bbox": ""})
        out.originals.append({"ID": bid, "HanNom_original": han_raw, "HanNom_cleaned": han_clean,
                              "HanNom_normalized": han,
                              "changed_chars": ";".join(f"{i}:{a}>{b}" for i, a, b in changes),
                              "DichAm_original": bead.get("tgt_text", ""), "kind": bead.get("kind", ""),
                              "score": bead.get("score", ""), "document_id": bead.get("document_id", ""),
                              "pages": json.dumps(bead.get("pages", {}), ensure_ascii=False),
                              "src_ids": ",".join(bead.get("src_ids", [])),
                              "tgt_ids": ",".join(bead.get("tgt_ids", []))})
    return out


def write_csv(path: Path, rows: list[dict], fields: list[str]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    return path


# --------------------------------------------------------------------------- #
# Config + chạy tool
# --------------------------------------------------------------------------- #
def build_config(tool_dir: Path, input_csv: Path, out_dir: Path, filename: str,
                 quick: int | None = None, fallback_column: str | None = None) -> dict:
    """Config từ mẫu của Lab; MỌI đường dẫn tuyệt đối (tool hiểu đường dẫn tương đối theo thư mục CHA của nó)."""
    import yaml
    tool_dir = tool_dir.resolve()
    cfg = yaml.safe_load((tool_dir / SAMPLE_CONFIG).read_text(encoding="utf-8"))
    cfg["dataset"]["name"] = Path(filename).stem
    cfg["dataset"]["input_file"] = Path(input_csv).resolve().as_posix()
    cfg["dataset"]["columns"].update({"encoding": "utf-8-sig", "sinonom_column": "HanNom",
                                      "quocngu_column": "DichAm", "fallback_column": fallback_column,
                                      "id_column": "ID"})
    cfg["dictionary"].update({"translation_dict": (tool_dir / TRANSLATION_DICT).as_posix(),
                              "similarity_dict": (tool_dir / SIMILARITY_DICT).as_posix(),
                              "encoding": "utf-8-sig"})
    cfg["alignment"]["damaged_tokens"] = [MASK]
    cfg["output"].update({"directory": Path(out_dir).resolve().as_posix(), "filename": filename,
                          "statistics_filename": "statistic.json"})
    cfg.setdefault("processing", {}).update({"verbose": True, "show_progress": False})
    cfg["quick_test"] = bool(quick)
    if quick:
        cfg["test_limit"] = int(quick)
    return cfg


def output_paths(out_dir: Path, filename: str) -> dict[str, Path]:
    stem = Path(filename).stem
    return {"tsv": out_dir / f"{stem}.tsv", "xlsx": out_dir / f"{stem}.xlsx",
            "color": out_dir / f"{stem}_color.xlsx", "stats": out_dir / "statistic.json"}


def check_tool(tool_dir: Path) -> list[str]:
    """Danh sách file của tool/từ điển còn thiếu (rỗng = đủ)."""
    need = [tool_dir / "align_texts.py", tool_dir / SAMPLE_CONFIG, tool_dir / TRANSLATION_DICT,
            tool_dir / SIMILARITY_DICT]
    return [str(p) for p in need if not p.exists()]


def run_tool(tool_dir: Path, config_path: Path, out: dict[str, Path], log_path: Path,
             python: str = sys.executable) -> None:
    cmd = [python, "align_texts.py", str(Path(config_path).resolve())]
    proc = subprocess.run(cmd, cwd=tool_dir, capture_output=True, text=True, encoding="utf-8",
                          errors="replace", env={**__import__("os").environ, "PYTHONUTF8": "1"})
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text(f"$ {' '.join(cmd)}\n--- stdout ---\n{proc.stdout}\n--- stderr ---\n{proc.stderr}",
                        encoding="utf-8")
    if proc.returncode != 0:
        raise RuntimeError(f"align_texts.py thoát mã {proc.returncode}; xem {log_path}")
    missing = [str(p) for p in out.values() if not p.exists()]
    if missing:
        raise RuntimeError(f"Tool Lab không sinh đủ output: thiếu {missing}; xem {log_path}")


# --------------------------------------------------------------------------- #
# Hậu xử lý
# --------------------------------------------------------------------------- #
def quality_warnings(stats: dict, n_input: int) -> list[str]:
    s = stats["summary"]
    warns = []
    if s.get("mismatch_rate", 0) > MISMATCH_WARN:
        warns.append(f"mismatch_rate toàn tài liệu {s['mismatch_rate']:.1%} > {MISMATCH_WARN:.0%}")
    for row in stats.get("rows", []):
        if row.get("status") != "processed":
            warns.append(f"dòng {row.get('ID')} bị tool bỏ qua ({row.get('status')})")
        elif row.get("mismatch_rate", 0) > ROW_ERROR_WARN:
            warns.append(f"dòng {row.get('ID')} có {row['mismatch_rate']:.0%} vị trí lỗi")
    if s.get("processed_rows") != n_input and not s.get("not_processed_rows"):
        warns.append(f"đưa vào {n_input} dòng, tool xử lý {s.get('processed_rows')}")
    return warns


def read_tool_tsv(path: Path) -> list[dict]:
    with open(path, encoding="utf-8-sig", newline="") as fh:
        return list(csv.DictReader(fh, delimiter="\t"))


def load_lab_dicts(tool_dir: Path) -> tuple[dict[str, set[str]], dict[str, list[str]]]:
    """(chữ -> tập âm) từ TongHop3, (chữ -> chữ tương tự) từ Similar_Dic (parse ast.literal_eval từng dòng)."""
    readings: dict[str, set[str]] = {}
    with open(tool_dir / TRANSLATION_DICT, encoding="utf-8-sig", newline="") as fh:
        for row in csv.DictReader(fh):
            ch, qn = row.get("SinoNom"), row.get("QuocNgu")
            if ch and qn:
                readings.setdefault(ch, set()).add(unicodedata.normalize("NFC", qn.lower()))
    similar: dict[str, list[str]] = {}
    with open(tool_dir / SIMILARITY_DICT, encoding="utf-8-sig", newline="") as fh:
        for row in csv.DictReader(fh):
            try:
                similar[row["Input Character"]] = list(ast.literal_eval(row["Top 20 Similar Characters"]))
            except (ValueError, SyntaxError, KeyError):
                continue
    return readings, similar


def color_positions(color_xlsx: Path, hannom_col: str = "HanNom") -> dict[str, list[tuple[int, str, str]]]:
    """ID dòng -> [(chỉ số vị trí, chữ, màu)] cho chữ KHÔNG đen trong _color.xlsx (đọc rich text)."""
    from openpyxl import load_workbook
    wb = load_workbook(color_xlsx, rich_text=True)
    ws = wb.active
    header = [c.value for c in ws[1]]
    if hannom_col not in header or "ID" not in header:
        return {}
    ci, ii = header.index(hannom_col), header.index("ID")
    out: dict[str, list[tuple[int, str, str]]] = {}
    for row in ws.iter_rows(min_row=2):
        cell, rid = row[ci], row[ii].value
        value = cell.value
        if value is None or rid is None:
            continue
        pieces = []
        if hasattr(value, "__iter__") and not isinstance(value, str):    # CellRichText
            for block in value:
                text = getattr(block, "text", block)
                color = getattr(getattr(getattr(block, "font", None), "color", None), "rgb", None)
                pieces.append((str(text), (color or "FF000000")[-6:].upper()))
        else:
            color = getattr(getattr(cell.font, "color", None), "rgb", None)
            pieces.append((str(value), (color or "FF000000")[-6:].upper() if isinstance(color, str) else "000000"))
        pos, found = 0, []
        for text, color in pieces:
            for unit in _units(text):
                if color != "000000":
                    found.append((pos, unit, color))
                pos += 1
        out[str(rid)] = found
    return out


def _units(text: str) -> list[str]:
    out, i = [], 0
    while i < len(text):
        if text.startswith(MASK, i):
            out.append(MASK)
            i += len(MASK)
        else:
            out.append(text[i])
            i += 1
    return out


def ocr_suggestions(color: dict, tool_rows: list[dict], originals: dict[str, dict],
                    readings: dict[str, set[str]], similar: dict[str, list[str]]) -> list[dict]:
    """Vị trí tool thay bằng chữ tương tự (xanh dương 0000FF) hoặc Fallback (xanh lá 008000): ID dòng, chỉ số,
    chữ OCR, chữ đề xuất (chữ tương tự có âm khớp), âm, trang, src_ids. Chỉ là gợi ý cho người rà soát."""
    by_id = {r.get("ID"): r for r in tool_rows}
    out = []
    for rid, items in color.items():
        row = by_id.get(rid)
        if not row:
            continue
        syllables = (row.get("DichAm") or "").split()
        for pos, ch, colour in items:
            if colour not in ("0000FF", "008000"):
                continue
            syl = syllables[pos] if pos < len(syllables) else ""
            cands = [c for c in similar.get(ch, []) if syl and syl.lower() in readings.get(c, set())]
            orig = originals.get(rid, {})
            out.append({"ID": rid, "index": pos, "ocr_char": ch, "suggested": "".join(cands[:3]),
                        "syllable": syl, "kind": "similarity" if colour == "0000FF" else "fallback",
                        "pages": orig.get("pages", ""), "src_ids": orig.get("src_ids", "")})
    return out


def write_xlsx(path: Path, sheet: str, header: list[str], rows: list[list]) -> Path:
    from openpyxl import Workbook
    wb = Workbook()
    ws = wb.active
    ws.title = sheet
    ws.append(header)
    for row in rows:
        ws.append(row)
    path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(path)
    return path


def review_rows(stats: dict, tool_rows: list[dict], originals: dict[str, dict]) -> list[list]:
    """Mọi dòng có '_' hoặc lỗi, kèm text gốc, trang, score, src/tgt ids; sắp theo mismatch_rate giảm dần."""
    by_id = {r.get("ID"): r for r in tool_rows}
    rows = []
    for st in stats.get("rows", []):
        rid = st.get("ID")
        tool = by_id.get(rid, {})
        bad = st.get("hannom_error_count") or "_" in (tool.get("HanNom") or "") or "_" in (tool.get("DichAm") or "")
        if st.get("status") == "processed" and not bad:
            continue
        o = originals.get(rid, {})
        rows.append([rid, st.get("mismatch_rate", 1.0), st.get("hannom_error_count"), tool.get("HanNom"),
                     tool.get("DichAm"), o.get("HanNom_original", ""), o.get("DichAm_original", ""),
                     o.get("pages", ""), o.get("score", ""), o.get("src_ids", ""), o.get("tgt_ids", ""),
                     st.get("status")])
    rows.sort(key=lambda r: -(r[1] or 0))
    return rows


def sha256(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def deliver(out: dict[str, Path], export_dir: Path, work_id: str, review_xlsx: Path | None) -> dict[str, str]:
    """Sao kết quả sang 07_export/han-am/ (không đụng export Hán–Việt). xlsx bàn giao = sheet DONG_HANG, cùng nội dung
    TSV (TSV: một TAB giữa các cột, một cặp mỗi dòng vật lý)."""
    export_dir.mkdir(parents=True, exist_ok=True)
    rows = read_tool_tsv(out["tsv"])
    header = list(rows[0].keys()) if rows else ["ID", "HanNom", "DichAm"]
    for r in rows:
        for k, v in r.items():
            if v and ("\t" in v or "\n" in v or "\r" in v):
                raise ValueError(f"Ô chứa TAB/xuống dòng ở dòng {r.get('ID')}, cột {k}")
    tsv = export_dir / f"{work_id}_parallel.tsv"
    with open(tsv, "w", encoding="utf-8", newline="") as fh:
        fh.write("\t".join(header) + "\n")
        for r in rows:
            fh.write("\t".join(r.get(h) or "" for h in header) + "\n")
    xlsx = write_xlsx(export_dir / f"{work_id}_parallel.xlsx", "DONG_HANG", header,
                      [[r.get(h) or "" for h in header] for r in rows])
    files = {"tsv": tsv, "xlsx": xlsx,
             "color": shutil.copy2(out["color"], export_dir / f"{work_id}_parallel_color.xlsx"),
             "stats": shutil.copy2(out["stats"], export_dir / "statistic.json")}
    if review_xlsx and review_xlsx.exists():
        files["review"] = shutil.copy2(review_xlsx, export_dir / review_xlsx.name)
    return {k: str(v) for k, v in files.items()}


# --------------------------------------------------------------------------- #
# Điều phối
# --------------------------------------------------------------------------- #
def run_on_csv(input_csv: Path, out_dir: Path, filename: str, tool_dir: Path, quick: int | None = None,
               python: str = sys.executable) -> dict:
    """Chạy tool trên một CSV có sẵn (dùng cho hồi quy với data/sample-data.csv). Trả về statistic.json."""
    import yaml
    missing = check_tool(tool_dir)
    if missing:
        raise FileNotFoundError(f"Thiếu file của tool Lab: {missing}")
    out_dir.mkdir(parents=True, exist_ok=True)
    cfg_path = out_dir / "config.yaml"
    with open(input_csv, encoding="utf-8-sig", newline="") as fh:
        rows = list(csv.DictReader(fh))
    # CSV có cột Fallback có dữ liệu (vd sample-data của Lab) -> dùng như chạy tay; corpus không có -> null
    fallback = "Fallback" if rows and any((r.get("Fallback") or "").strip() for r in rows) else None
    cfg_path.write_text(yaml.safe_dump(build_config(tool_dir, input_csv, out_dir, filename, quick, fallback),
                                       allow_unicode=True, sort_keys=False), encoding="utf-8")
    out = output_paths(out_dir, filename)
    run_tool(tool_dir, cfg_path, out, out_dir / "tool.log", python)
    return json.loads(out["stats"].read_text(encoding="utf-8"))


def run(args) -> dict:
    import yaml
    from src.alignment.serialization import read_alignments
    from src.common import deps
    from src.common.corpus import DataLayout, MetadataStore
    from src.common.sentences import alignment_dir, export_dir, find_alignment_file

    tool_dir = Path(args.tool_dir)
    missing = check_tool(tool_dir)
    if missing:
        raise FileNotFoundError("Thiếu file của tool Lab (đặt đúng chỗ, agent không tự tải): " + ", ".join(missing))
    layout = DataLayout(Path(args.data_root), args.work_id)
    src = find_alignment_file(layout, "han-am")
    if not src.exists():
        raise FileNotFoundError(f"Chưa có {src}; chạy python -m src.alignment.align --pair han-am trước")
    beads = read_alignments(src)
    precheck(beads)
    lab_dir = alignment_dir(layout) / "lab"
    out_dir = lab_dir / "out"
    filename = f"{args.work_id}_parallel.tsv"
    out = output_paths(out_dir, filename)

    known = load_lab_chars(tool_dir)
    data = beads_to_rows(beads, known)
    for w in data.length_warnings:
        log.warning("Lệch độ dài (> %d%%) có thể do ghép cặp sai: %s", round(LENGTH_WARN * 100), w)
    input_csv = write_csv(lab_dir / "han-am_input.csv", data.rows, ["ID", "HanNom", "DichAm", "Fallback", "Bbox"])
    write_csv(lab_dir / "han-am_input_original.csv", data.originals, list(data.originals[0].keys())
              if data.originals else ["ID"])
    cfg = build_config(tool_dir, input_csv, out_dir, filename, args.quick)
    cfg_path = lab_dir / "han-am_config.yaml"
    cfg_path.write_text(yaml.safe_dump(cfg, allow_unicode=True, sort_keys=False), encoding="utf-8")
    cmd = f"{args.python} align_texts.py {cfg_path.resolve()}  (cwd={tool_dir.resolve()})"
    log.info("%d bead, %d dòng CSV, %d unaligned bị loại -> %s", data.total, len(data.rows), data.unaligned, input_csv)
    if args.dry_run:
        print(f"$ {cmd}")
        return {"dry_run": True, "rows": len(data.rows), "unaligned": data.unaligned, "total": data.total}

    inputs = [input_csv, cfg_path, tool_dir / "align_texts.py", tool_dir / TRANSLATION_DICT, tool_dir / SIMILARITY_DICT]
    recorded = deps.recorded(layout, "lab_han_am")
    if (not args.force and all(p.exists() for p in out.values()) and recorded
            and recorded.get("fingerprint") == deps.fingerprint(inputs)):
        log.info("Input không đổi và output đã có -> bỏ qua (dùng --force để chạy lại)")
        stats = json.loads(out["stats"].read_text(encoding="utf-8"))
    else:
        run_tool(tool_dir, cfg_path, out, lab_dir / "tool.log", args.python)
        deps.record(layout, "lab_han_am", inputs, list(out.values()))
        stats = json.loads(out["stats"].read_text(encoding="utf-8"))

    warns = quality_warnings(stats, len(data.rows) if not args.quick else min(len(data.rows), args.quick))
    for w in warns:
        log.warning("%s", w)
    tool_rows = read_tool_tsv(out["tsv"])
    originals = {o["ID"]: o for o in data.originals}
    review = review_rows(stats, tool_rows, originals)
    review_xlsx = write_xlsx(lab_dir / "han-am_review.xlsx", "REVIEW",
                             ["ID", "mismatch_rate", "hannom_error_count", "HanNom_aligned", "DichAm_aligned",
                              "HanNom_original", "DichAm_original", "pages", "bead_score", "src_ids", "tgt_ids",
                              "status"], review)
    readings, similar = load_lab_dicts(tool_dir)
    sugg = ocr_suggestions(color_positions(out["color"]), tool_rows, originals, readings, similar)
    write_xlsx(lab_dir / "han-am_ocr_suggestions.xlsx", "SUGGESTIONS",
               ["ID", "index", "ocr_char", "suggested", "syllable", "kind", "pages", "src_ids"],
               [[s[k] for k in ("ID", "index", "ocr_char", "suggested", "syllable", "kind", "pages", "src_ids")]
                for s in sugg])
    delivered = deliver(out, export_dir(layout) / "han-am", args.work_id, review_xlsx)
    summary = {k: stats["summary"].get(k) for k in ("input_rows", "processed_rows", "alignment_positions",
                                                    "match_count", "match_rate", "mismatch_rate",
                                                    "fallback_count", "similarity_count", "hannom_error_count")}
    result = {"beads": data.total, "csv_rows": len(data.rows), "unaligned": data.unaligned, **summary,
              "review_rows": len(review), "ocr_suggestions": len(sugg), "warnings": warns,
              "length_warnings": data.length_warnings}
    MetadataStore(layout).update("han-am_lab_alignment", {
        "output": {k: layout.rel(v) for k, v in out.items()}, "delivered": delivered,
        "tool": {"align_texts_sha256": sha256(tool_dir / "align_texts.py"),
                 "translation_dict_sha256": sha256(tool_dir / TRANSLATION_DICT),
                 "similarity_dict_sha256": sha256(tool_dir / SIMILARITY_DICT)},
        "summary": stats["summary"], "result": {k: v for k, v in result.items() if k != "warnings"},
        "review_required": True})
    log.info("Tool Lab: %s", {k: result[k] for k in ("csv_rows", "alignment_positions", "match_rate",
                                                     "mismatch_rate", "review_rows")})
    return result


def create_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Dóng chữ Hán <-> âm tiết bằng tool SN-QN-alignment của Lab",
                                formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    p.add_argument("--work-id")
    p.add_argument("--data-root", default="data")
    p.add_argument("--tool-dir", default=str(DEFAULT_TOOL_DIR))
    p.add_argument("--quick", type=int, help="Chỉ xử lý N dòng đầu (quick_test/test_limit của tool)")
    p.add_argument("--force", action="store_true", help="Chạy lại tool dù input không đổi")
    p.add_argument("--dry-run", action="store_true", help="Chỉ sinh CSV + config rồi in lệnh")
    p.add_argument("--python", default=sys.executable, help="Python chạy align_texts.py (cần pandas, numpy, "
                                                            "PyYAML, XlsxWriter)")
    p.add_argument("--input-csv", help="Chạy tool trên CSV có sẵn (hồi quy), cần --out-dir")
    p.add_argument("--out-dir")
    p.add_argument("-v", "--verbose", action="store_true")
    return p


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError, OSError):
            pass
    args = create_parser().parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    try:
        if args.input_csv:
            if not args.out_dir:
                raise ValueError("--input-csv cần --out-dir")
            stats = run_on_csv(Path(args.input_csv), Path(args.out_dir), Path(args.input_csv).stem + "_align.tsv",
                               Path(args.tool_dir), args.quick, args.python)
            print(json.dumps(stats["summary"], ensure_ascii=False))
        else:
            if not args.work_id:
                raise ValueError("Cần --work-id (hoặc --input-csv)")
            print(json.dumps(run(args), ensure_ascii=False, indent=1))
    except (OSError, ValueError, RuntimeError, KeyError) as exc:
        log.error("%s", exc)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
