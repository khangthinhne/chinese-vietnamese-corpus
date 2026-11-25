"""JSON OCR thô -> hai luồng theo hệ chữ: 03_ocr/ocr_raw/{han,vie}.xml + ocr_raw_text/{han,vie}/NNNN.txt.

    python -m src.ocr.build_layers --config configs/CPO_20.json
    python -m src.ocr.build_layers --config configs/CPO_20.json --layers vie --pages 5-60
    python -m src.ocr.build_layers --config configs/CPO_20.json --report-only

- Hán lấy từ engine `paddlevl`, Việt lấy từ engine `qwen` (LAYER_ENGINES). Mỗi trang dùng đúng MỘT run
  của engine đó; không bỏ phiếu theo dòng, không trộn hai engine trong một file.
- Chỉ ĐỌC JSON thô (raw/NNNN.json cũ, raw/<engine>/NNNN.json, raw/<chi_tra|vie>/page_NNNN.json).
  Không import engine nào nên không cần torch/transformers: chạy được ở cả `my_nlp` lẫn `.venv-paddle`.
- Schema XML sao chép raw.xml (FILE/OCR/PAGE/LINE, ID dòng PNNNN.LNNN đánh lại sau lọc), thêm SRC_LINE
  (số thứ tự dòng gốc của engine, 1-based) cho mỗi LINE. RUN chỉ giữ provenance (khoá, tham số), không
  chép lại dòng/payload gốc chưa lọc.
- TXT chỉ tạo cho trang có ít nhất một dòng sau lọc. Không đụng raw.xml và ocr_raw_text/NNNN.txt.
"""

from __future__ import annotations

import json
import logging
import os
import re
import sys
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path

from src.common import deps
from src.common.corpus import DataLayout, base_parser, parse_args, parse_pages, write_json
from src.common.xmlstore import add, bbox_attr, file_element, read_root, write_file_root, write_root
from src.ocr import raw_store
from src.ocr.raw_xml import _add_engine, _add_parameters
from src.ocr.run_quality import run_is_valid
from src.ocr.script_filter import (HAN_MIN_CJK_SHARE, HAN_MIN_CJK_WITH_LATIN, LAYERS, PageFilterResult,
                                   count_cjk, filter_page, has_cjk)

log = logging.getLogger(__name__)

LAYER_ENGINES = {"han": "paddlevl", "vie": "qwen"}
# Kho hệ chữ cũ (raw/<ws>/page_NNNN.json, vd CPO_18) mà mỗi luồng được phép đọc.
LAYER_WRITING_SYSTEMS = {"han": ("chi_tra",), "vie": ("vie",)}
ENGINE_ENVS = {
    "paddlevl": ".venv-paddle (transformers>=5, torch>=2.6)",
    "qwen": "my_nlp (transformers 4.x)",
}
LEGACY_TEXT_RE = re.compile(r"^\d{3}\.txt$")       # TXT của chế độ hệ chữ cũ (NNN.txt)
PAGE_TXT_RE = re.compile(r"^\d{4}\.txt$")
LINE_ID_RE = re.compile(r"^P(\d{4})\.L(\d{3})$")
VIE_DROP_WARN = 0.30
VIE_DROP_MIN_LINES = 2               # mất đúng 1 dòng (thường là số trang) thì không cảnh báo


# --------------------------------------------------------------------------- #
# Chọn run
# --------------------------------------------------------------------------- #
def run_engine(run: dict) -> str | None:
    engine = (run.get("parameters") or {}).get("engine")
    if isinstance(engine, dict):
        return engine.get("name")
    return engine if isinstance(engine, str) else run.get("engine")


def select_run(runs: list[dict], engine: str, preferred_keys: list[str | None],
               layer: str | None = None) -> tuple[dict | None, str]:
    """Một run của `engine` cho một trang, xác định hoàn toàn từ dữ liệu:

    1. khoá do manifest chỉ định (manifest OCR luồng, rồi manifest unified);
    2. nếu biết `layer`: chỉ xét run HỢP LỆ cho luồng đó (`run_quality.run_is_valid`) khi có ít nhất một;
    3. run mới nhất theo `created_at` (hoà thì run đọc sau).

    Trả về (run, lý do): manifest | only | latest_of_many | none."""
    mine = [r for r in runs if run_engine(r) == engine]
    for key in preferred_keys:
        hits = [r for r in mine if key and r.get("key") == key]
        if hits:
            return hits[-1], "manifest"
    if not mine:
        return None, "none"
    reason = "only" if len({r.get("key") for r in mine}) == 1 else "latest_of_many"
    if layer is not None:
        valid = [r for r in mine if run_is_valid(r, layer)]
        mine = valid or mine
    latest = max(range(len(mine)), key=lambda i: (mine[i].get("created_at") or "", i))
    return mine[latest], reason


def layer_manifest_paths(layout: DataLayout, layer: str) -> list[Path]:
    """manifests/ocr_<layer>.json (gộp, của máy này) rồi ocr_<layer>_<đầu>-<cuối>.json (theo khoảng trang, có thể từ
    gói của máy khác), khoảng cập nhật gần nhất trước."""
    merged = layout.ocr_layer_manifest_path("ocr", layer)
    ranges = []
    for path in sorted(layout.ocr_manifest_dir.glob(f"ocr_{layer}_*-*.json")) if layout.ocr_manifest_dir.is_dir() else []:
        if re.fullmatch(rf"ocr_{layer}_\d{{4}}-\d{{4}}", path.stem):
            try:
                updated = json.loads(path.read_text(encoding="utf-8")).get("updated_at") or ""
            except (OSError, ValueError):
                log.warning("Bỏ qua manifest hỏng %s", path)
                continue
            ranges.append((updated, path))
    ranges.sort(key=lambda x: x[0], reverse=True)
    return ([merged] if merged.exists() else []) + [p for _, p in ranges]


def preferred_keys(layout: DataLayout, layer: str, engine: str) -> dict[int, list[str]]:
    """Khoá run do manifest chỉ định theo trang: manifest OCR luồng trước, manifest unified sau."""
    out: dict[int, list[str]] = {}
    for path in layer_manifest_paths(layout, layer):
        for rec in json.loads(path.read_text(encoding="utf-8")).get("pages", []):
            if rec.get("engine") == engine and rec.get("key") and rec["key"] not in out.get(rec["page"], []):
                out.setdefault(rec["page"], []).append(rec["key"])
    unified = raw_store.load_unified_manifest(layout)
    for rec in (unified or {}).get("pages", []):
        if rec.get("selected_engine") == engine and rec.get("selected_key"):
            out.setdefault(rec["page"], []).append(rec["selected_key"])
    return out


# --------------------------------------------------------------------------- #
# Dựng một luồng
# --------------------------------------------------------------------------- #
@dataclass
class PageOut:
    page: int
    run: dict | None
    reason: str
    filtered: PageFilterResult
    src_lines: list[dict] = field(default_factory=list)
    merged: list[dict] = field(default_factory=list)     # dòng một chữ đã gộp (merge_char_split_units)

    @property
    def status(self) -> str:
        if self.run is None:
            return "no_run"
        return "ok" if self.filtered.kept else "empty"


def split_source_lines(lines: list[dict]) -> list[dict]:
    """Một "dòng" engine có thể chứa ký tự xuống dòng (run Qwen cũ trả cả trang trong một dòng).
    Tách thành từng mảnh; mọi mảnh giữ `src_line` (số thứ tự dòng gốc, 1-based), bbox và confidence."""
    out = []
    for index, line in enumerate(lines, 1):
        for piece in str(line.get("text") or "").splitlines() or [""]:
            out.append({**line, "text": piece, "src_line": index})
    return out


def merge_char_split_units(run: dict | None) -> tuple[list[dict], list[dict]]:
    """paddlevl chế độ ngang: mỗi đơn vị (dải) là MỘT dòng in. Đôi khi model xuất từng chữ trên một dòng
    (vd "玄\n麻\n...\n來" cho dòng 玄麻蛋副璽書來), làm một câu thơ thành 7 "dòng" một chữ.

    Chỉ gộp khi MỌI dòng của đơn vị đều đúng 1 ký tự (không đoán gì khác). Trả về (dòng, nhật ký gộp).
    Dòng gộp giữ `src_line` của dòng đầu; bbox = hộp bao các dòng con."""
    lines = list((run or {}).get("lines") or [])
    raw = (run or {}).get("raw") or {}
    units = raw.get("bands") or []
    engine = run_engine(run or {})
    if (engine != "paddlevl" or raw.get("orientation") != "horizontal" or not units
            or sum(int(u.get("n_lines") or 0) for u in units) != len(lines)):
        return [{**x, "src_line": i} for i, x in enumerate(lines, 1)], []
    out, merged, pos = [], [], 0
    for unit in units:
        n = int(unit.get("n_lines") or 0)
        group = [{**lines[pos + k], "src_line": pos + k + 1} for k in range(n)]
        pos += n
        texts = [str(x.get("text") or "").strip() for x in group]
        if n >= 2 and all(len(t) == 1 for t in texts):
            boxes = [x["bbox"] for x in group if x.get("bbox")]
            bbox = ([min(b[0] for b in boxes), min(b[1] for b in boxes), max(b[2] for b in boxes),
                     max(b[3] for b in boxes)] if boxes else None)
            out.append({**group[0], "text": "".join(texts), "bbox": bbox})
            merged.append({"src_lines": [x["src_line"] for x in group], "text": "".join(texts)})
        else:
            out.extend(group)
    return out, merged


def build_page(layer: str, page: int, run: dict | None, reason: str, *,
               han_min_cjk_share: float = HAN_MIN_CJK_SHARE,
               han_min_cjk_with_latin: int = HAN_MIN_CJK_WITH_LATIN) -> PageOut:
    lines, merged = merge_char_split_units(run)
    src_lines = []
    for line in lines:                         # tách dòng có ký tự xuống dòng, giữ src_line gốc
        for piece in str(line.get("text") or "").splitlines() or [""]:
            src_lines.append({**line, "text": piece})
    filtered = filter_page(layer, [x["text"] for x in src_lines], han_min_cjk_share=han_min_cjk_share,
                           han_min_cjk_with_latin=han_min_cjk_with_latin)
    out = PageOut(page, run, reason, filtered, src_lines)
    out.merged = merged
    return out


def mean_confidence(lines: list[tuple[str, float | None]]) -> float | None:
    weighted = [(conf, len(text)) for text, conf in lines if conf is not None and text]
    total = sum(w for _, w in weighted)
    return round(sum(c * w for c, w in weighted) / total, 2) if total else None


def check_target(layout: DataLayout, layer: str, force: bool) -> None:
    """Không ghi đè han.xml của chế độ hệ chữ cũ (chi_tra -> han.xml) nếu không có --force."""
    path = layout.ocr_layer_xml_path(layer)
    if not path.exists():
        return
    try:
        ocr = file_element(read_root(path)).find("OCR")
    except (ET.ParseError, ValueError):
        ocr = None
    legacy = ocr is None or ocr.get("MODE") != "layer"
    if legacy and not force:
        raise FileExistsError(f"{path} đã tồn tại và không phải output của build_layers (có thể là "
                              f"XML của chế độ hệ chữ cũ). Dùng --force để ghi đè.")
    text_dir = layout.ocr_layer_text_dir(layer)
    if text_dir.is_dir() and any(LEGACY_TEXT_RE.match(p.name) for p in text_dir.iterdir()):
        log.warning("[%s] %s còn TXT 3 chữ số của chế độ cũ; build_layers không xoá chúng", layer, text_dir)


def write_layer_xml(layout: DataLayout, layer: str, engine: str, pages: list[PageOut],
                    filter_options: dict | None = None) -> Path:
    xml_path = layout.ocr_layer_xml_path(layer)
    root = write_file_root(xml_path, layout.work_id)
    file_el = file_element(root)
    file_el.set("SCHEMA_VERSION", "1.0")
    ocr_meta = add(file_el, "OCR", MODE="layer", LAYER=layer, ENGINE=engine, SCHEMA_VERSION="1")
    engines = add(ocr_meta, "ENGINES")
    seen: list[dict] = []
    for out in pages:
        desc = (out.run or {}).get("parameters", {}).get("engine")
        if isinstance(desc, dict) and desc not in seen:
            seen.append(desc)
            _add_engine(engines, desc)
    add(ocr_meta, "FILTER", MODULE="src.ocr.script_filter", LAYER=layer, NORMALIZATION="NFC",
        **({k.upper(): v for k, v in (filter_options or {}).items()} if layer == "han" else {}))

    for out in pages:
        if out.status != "ok":
            continue
        run, kept = out.run, out.filtered.kept
        confs = [(text, out.src_lines[src - 1].get("confidence")) for src, text in kept]
        page_el = add(file_el, "PAGE", NUMBER=out.page, IMAGE=run.get("image"),
                      IMAGE_SHA256=(run.get("parameters") or {}).get("image_sha256"),
                      RAW=run.get("_source"), SELECTED_ENGINE=engine, SELECTED_KEY=run.get("key"),
                      CONFIDENCE=mean_confidence(confs), EMPTY=False, N_LINES=len(kept),
                      N_SRC_LINES=out.filtered.n_src, N_DROPPED=out.filtered.n_dropped,
                      CJK_REMOVED=out.filtered.cjk_removed if layer == "vie" else None)
        for order, (src, text) in enumerate(kept, 1):
            src_line = out.src_lines[src - 1]
            add(page_el, "LINE", text, ID=f"P{out.page:04d}.L{order:03d}", ORDER=order, NO=order,
                SRC_LINE=src_line["src_line"], BBOX=bbox_attr(src_line.get("bbox")), CONFIDENCE=src_line.get("confidence"),
                # han: giữ dòng gốc (số thứ tự "1.", dấu ASCII) để segmentation nhận tiêu đề; vie thì không,
                # vì dòng gốc của vie có chữ Hán.
                RAW_TEXT=src_line["text"] if layer == "han" and src_line["text"] != text else None)
        runs = add(page_el, "RUNS")
        size = run.get("image_size") or [None, None]
        run_el = add(runs, "RUN", KEY=run.get("key"), CREATED_AT=run.get("created_at"),
                     IMAGE=run.get("image"), IMAGE_WIDTH=size[0], IMAGE_HEIGHT=size[1],
                     CROP_BOX=",".join(str(x) for x in run.get("crop_box", [])) or None,
                     SOURCE=run.get("_source"), SELECTION=out.reason)
        _add_parameters(run_el, run.get("parameters"))
    return write_root(xml_path, root)


def write_layer_texts(layout: DataLayout, layer: str, pages: list[PageOut]) -> list[Path]:
    out_dir = layout.ocr_layer_text_dir(layer)
    out_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for out in pages:
        if out.status != "ok":
            continue
        path = layout.ocr_layer_text_path(layer, out.page)
        tmp = path.with_name(path.name + ".tmp")
        with open(tmp, "w", encoding="utf-8", newline="") as fh:
            fh.write("\n".join(text for _, text in out.filtered.kept) + "\n")
        os.replace(tmp, path)
        written.append(path)
    keep = set(written)
    for entry in out_dir.iterdir():               # trang không còn nội dung: xoá TXT cũ của chính bước này
        if entry.is_file() and not entry.is_symlink() and PAGE_TXT_RE.match(entry.name) and entry not in keep:
            entry.unlink()
    return written


def build_layer(layout: DataLayout, layer: str, pages: list[int], *, force: bool = False,
                han_min_cjk_share: float = HAN_MIN_CJK_SHARE,
                han_min_cjk_with_latin: int = HAN_MIN_CJK_WITH_LATIN) -> dict:
    engine = LAYER_ENGINES[layer]
    check_target(layout, layer, force)
    prefs = preferred_keys(layout, layer, engine)
    outs: list[PageOut] = []
    for page in pages:
        runs = raw_store.page_runs(layout, engine, page, layout.work_id, LAYER_WRITING_SYSTEMS[layer])
        run, reason = select_run(runs, engine, prefs.get(page, []), layer=layer)
        if reason == "latest_of_many":
            log.warning("[%s] trang %04d có nhiều run %s khác khoá; dùng run mới nhất %s",
                        layer, page, engine, run.get("key", "")[:12])
        out = build_page(layer, page, run, reason, han_min_cjk_share=han_min_cjk_share,
                         han_min_cjk_with_latin=han_min_cjk_with_latin)
        outs.append(out)
        for src, why, text in out.filtered.rejected:
            log.info("[%s] P%04d loại dòng %d (%s): %s", layer, page,
                     out.src_lines[src - 1]["src_line"], why, text[:80])
        for item in out.merged:
            log.info("[%s] P%04d gộp %d dòng một chữ (dòng %s) thành một dòng", layer, page,
                     len(item["src_lines"]), ",".join(map(str, item["src_lines"])))
        if run is not None:
            s = out.filtered.stats()
            log.info("[%s] P%04d nguồn=%d giữ=%d bỏ=%d cjk_xoá=%d", layer, page, s["n_src"],
                     s["n_kept"], s["n_dropped"], s["cjk_removed"])
    xml_path = write_layer_xml(layout, layer, engine, outs, {
        "han_min_cjk_share": han_min_cjk_share, "han_min_cjk_with_latin": han_min_cjk_with_latin})
    texts = write_layer_texts(layout, layer, outs)
    records = [{"page": o.page, "status": o.status, "selection": o.reason,
                "key": (o.run or {}).get("key"), "raw": (o.run or {}).get("_source"),
                **o.filtered.stats(), **non_han_counts(o),
                "rejected": [{"src_line": o.src_lines[i - 1]["src_line"], "reason": why, "text": t}
                             for i, why, t in o.filtered.rejected],
                "merged": o.merged} for o in outs]
    with_run = [r for r in records if r["status"] != "no_run"]
    summary = {
        "schema_version": 1, "work_id": layout.work_id, "layer": layer, "engine": engine,
        "built_at": raw_store.now(), "xml": layout.rel(xml_path),
        "text_dir": layout.rel(layout.ocr_layer_text_dir(layer)),
        "totals": {"pages_requested": len(pages), "pages_with_run": len(with_run),
                   "pages_with_lines": len(texts),
                   "n_src": sum(r["n_src"] for r in with_run), "n_kept": sum(r["n_kept"] for r in with_run),
                   "n_dropped": sum(r["n_dropped"] for r in with_run),
                   "cjk_removed": sum(r["cjk_removed"] for r in with_run),
                   "n_rejected": sum(r["n_rejected"] for r in with_run),
                   "n_merged_units": sum(len(r["merged"]) for r in with_run)},
        "filter": {"han_min_cjk_share": han_min_cjk_share, "han_min_cjk_with_latin": han_min_cjk_with_latin},
        "warnings": layer_warnings(layer, records),
        "pages": records,
    }
    write_json(layout.ocr_layer_manifest_path("build", layer), summary)
    log.info("[%s] %s: %d trang có run %s, %d trang có dòng -> %s", layout.work_id, layer,
             len(with_run), engine, len(texts), xml_path)
    return summary


def non_han_counts(out: PageOut) -> dict[str, int]:
    """Dòng nguồn KHÔNG chứa chữ Hán và số dòng loại đó bị bỏ sau lọc.

    Dòng có chữ Latin không bao giờ bị lọc vie bỏ, nên tỉ lệ "dòng mất / dòng nguồn" chỉ đo mức song ngữ
    của trang. Mất mát đáng ngờ là dòng không phải Hán bị bỏ (số, dấu câu, rác OCR)."""
    kept = {src for src, _ in out.filtered.kept}
    non_han = [i for i, x in enumerate(out.src_lines, 1) if not has_cjk(x["text"])]
    return {"n_src_non_han": len(non_han), "n_dropped_non_han": sum(1 for i in non_han if i not in kept)}


def layer_warnings(layer: str, records: list[dict]) -> list[str]:
    warnings = []
    for r in records:
        if r["status"] == "empty" and layer == "han":
            warnings.append(f"P{r['page']:04d}: trang có run paddlevl nhưng không ra dòng Hán nào")
        if layer == "vie" and r["status"] != "no_run" and r.get("n_src_non_han"):
            ratio = r["n_dropped_non_han"] / r["n_src_non_han"]
            if ratio > VIE_DROP_WARN and r["n_dropped_non_han"] >= VIE_DROP_MIN_LINES:
                warnings.append(f"P{r['page']:04d}: vie mất {ratio:.0%} dòng không phải Hán sau lọc "
                                f"({r['n_dropped_non_han']}/{r['n_src_non_han']})")
    return warnings


# --------------------------------------------------------------------------- #
# Kiểm tra output (dùng cho nghiệm thu và sau mỗi lần dựng)
# --------------------------------------------------------------------------- #
def verify_layer(layout: DataLayout, layer: str) -> tuple[list[str], dict]:
    """Kiểm tra han.xml/vie.xml + TXT. Trả về (lỗi, số liệu)."""
    errors: list[str] = []
    xml_path = layout.ocr_layer_xml_path(layer)
    if not xml_path.exists():
        return [f"Thiếu {xml_path}"], {}
    raw_text = xml_path.read_text(encoding="utf-8")
    if layer == "vie" and count_cjk(raw_text):
        errors.append(f"vie.xml còn {count_cjk(raw_text)} ký tự CJK")
    file_el = file_element(ET.fromstring(raw_text))
    ocr = file_el.find("OCR")
    if ocr is None or ocr.get("MODE") != "layer" or ocr.get("LAYER") != layer:
        errors.append(f"{xml_path.name}: thiếu <OCR MODE='layer' LAYER='{layer}'>")
    engines = {p.get("SELECTED_ENGINE") for p in file_el.findall("PAGE")}
    if engines - {LAYER_ENGINES[layer]}:
        errors.append(f"{xml_path.name}: trộn engine {sorted(engines)}")
    ids: set[str] = set()
    xml_pages: dict[int, list[str]] = {}
    for page_el in file_el.findall("PAGE"):
        number = int(page_el.get("NUMBER", "0"))
        lines = page_el.findall("LINE")
        if not lines:
            errors.append(f"P{number:04d}: PAGE không có LINE")
        for order, line in enumerate(lines, 1):
            line_id, text = line.get("ID", ""), line.text or ""
            match = LINE_ID_RE.match(line_id)
            if not match or int(match[1]) != number or int(match[2]) != order:
                errors.append(f"ID sai định dạng/thứ tự: {line_id!r} (trang {number}, dòng {order})")
            if line_id in ids:
                errors.append(f"ID trùng: {line_id}")
            ids.add(line_id)
            if not line.get("SRC_LINE", "").isdigit():
                errors.append(f"{line_id}: thiếu SRC_LINE")
            if layer == "han" and not has_cjk(text):
                errors.append(f"{line_id}: dòng han không có chữ Hán")
        xml_pages[number] = [line.text or "" for line in lines]
    text_dir = layout.ocr_layer_text_dir(layer)
    txt_files = {int(p.stem): p for p in text_dir.iterdir() if PAGE_TXT_RE.match(p.name)} \
        if text_dir.is_dir() else {}
    if set(txt_files) != set(xml_pages):
        errors.append(f"TXT và XML lệch trang: chỉ TXT {sorted(set(txt_files) - set(xml_pages))}, "
                      f"chỉ XML {sorted(set(xml_pages) - set(txt_files))}")
    txt_lines = 0
    for number, path in txt_files.items():
        lines = path.read_text(encoding="utf-8").split("\n")
        if lines and lines[-1] == "":
            lines.pop()
        txt_lines += len(lines)
        if not any(x.strip() for x in lines):
            errors.append(f"{path.name}: TXT rỗng")
        if number in xml_pages and lines != xml_pages[number]:
            errors.append(f"{path.name}: nội dung khác XML")
    xml_lines = sum(len(v) for v in xml_pages.values())
    if xml_lines != txt_lines:
        errors.append(f"Tổng dòng XML {xml_lines} != tổng dòng TXT {txt_lines}")
    return errors, {"pages": len(xml_pages), "lines": xml_lines, "txt_files": len(txt_files),
                    "txt_lines": txt_lines, "cjk_in_file": count_cjk(raw_text)}


def summarize(layout: DataLayout) -> dict:
    """Bảng tổng hợp một work từ manifests/build_*.json và manifests/ocr_*.json."""
    row: dict = {"work_id": layout.work_id}
    built: dict[str, dict] = {}
    for layer in LAYERS:
        path = layout.ocr_layer_manifest_path("build", layer)
        if path.exists():
            built[layer] = json.loads(path.read_text(encoding="utf-8"))
    all_pages = sorted({r["page"] for b in built.values() for r in b["pages"]})
    row["pages"] = len(all_pages)
    ocr_errors: set[int] = set()
    for layer in LAYERS:
        totals = (built.get(layer) or {}).get("totals", {})
        row[f"{layer}_run_pages"] = totals.get("pages_with_run", 0)
        row[f"{layer}_pages"] = totals.get("pages_with_lines", 0)
        row[f"{layer}_lines"] = totals.get("n_kept", 0)
        row[f"{layer}_dropped"] = totals.get("n_dropped", 0)
        path = layout.ocr_layer_manifest_path("ocr", layer)
        if path.exists():
            ocr_errors |= set(json.loads(path.read_text(encoding="utf-8")).get("error_pages", []))
    row["vie_cjk_removed"] = (built.get("vie") or {}).get("totals", {}).get("cjk_removed", 0)
    row["ocr_error_pages"] = sorted(ocr_errors)
    status = {layer: {r["page"]: r["status"] for r in b["pages"]} for layer, b in built.items()}
    neither = [p for p in all_pages
               if all(status.get(layer, {}).get(p) != "ok" for layer in LAYERS)
               and any(status.get(layer, {}).get(p) in ("ok", "empty") for layer in LAYERS)]
    warnings = [f"P{p:04d}: không có dòng Hán lẫn dòng Việt ("
                + ", ".join(f"{layer}={status.get(layer, {}).get(p, 'no_run')}" for layer in LAYERS) + ")"
                for p in neither]
    for b in built.values():
        warnings += b.get("warnings", [])
    row["warnings"] = warnings
    return row


SUMMARY_COLUMNS = [
    ("work_id", "work"), ("pages", "trang"), ("han_run_pages", "trang có run Hán"),
    ("han_pages", "trang có Hán"), ("vie_run_pages", "trang có run Việt"), ("vie_pages", "trang có Việt"),
    ("han_lines", "dòng Hán"), ("vie_lines", "dòng Việt"), ("han_dropped", "Hán bị lọc"),
    ("vie_dropped", "Việt bị lọc"), ("vie_cjk_removed", "CJK xoá khỏi vie"),
]


def format_summary(rows: list[dict]) -> str:
    header = [title for _, title in SUMMARY_COLUMNS] + ["trang lỗi OCR"]
    body = [[str(r.get(key, "")) for key, _ in SUMMARY_COLUMNS] + [str(len(r.get("ocr_error_pages", [])))]
            for r in rows]
    widths = [max(len(h), *(len(b[i]) for b in body)) for i, h in enumerate(header)]
    lines = [" | ".join(h.ljust(w) for h, w in zip(header, widths)),
             "-+-".join("-" * w for w in widths)]
    lines += [" | ".join(c.rjust(w) for c, w in zip(b, widths)) for b in body]
    for r in rows:
        for warning in r.get("warnings", []):
            lines.append(f"  [cảnh báo {r['work_id']}] {warning}")
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def create_parser():
    parser = base_parser("JSON OCR thô -> 03_ocr/ocr_raw/{han,vie}.xml + ocr_raw_text/{han,vie}/NNNN.txt",
                         layer_required=False)
    # dest khác 'pages' để config.common.pages (phạm vi OCR) không tự giới hạn bước dựng
    parser.add_argument("--pages", dest="build_pages",
                        help="Mặc định: mọi trang có JSON thô. Ví dụ 5-60,62")
    parser.add_argument("--layers", default="han,vie", help="han,vie | han | vie")
    parser.add_argument("--han-min-cjk-share", type=float, default=HAN_MIN_CJK_SHARE,
                        help="Lớp han: loại dòng có chữ Latin mà chữ Hán/(chữ Hán + chữ Latin) nhỏ hơn mức này")
    parser.add_argument("--han-min-cjk-with-latin", type=int, default=HAN_MIN_CJK_WITH_LATIN,
                        help="Lớp han: dòng có chữ Latin phải có ít nhất ngần này chữ Hán")
    parser.add_argument("--report-only", action="store_true",
                        help="Không dựng lại; chỉ kiểm tra output hiện có và in bảng tổng hợp")
    return parser


def build_inputs(layout: DataLayout, layer: str) -> list[Path]:
    """Đầu vào của build_layer: toàn bộ raw/ (append-only) + manifest OCR của luồng (gộp, theo khoảng, unified)."""
    inputs = [layout.ocr_raw_root, *layer_manifest_paths(layout, layer)]
    unified = raw_store.unified_manifest_path(layout)
    return inputs + ([unified] if unified.exists() else [])


def run(args) -> dict:
    layers = [x.strip() for x in args.layers.split(",") if x.strip()]
    if not layers or any(x not in LAYERS for x in layers):
        raise ValueError(f"--layers phải thuộc {LAYERS}")
    layout = DataLayout(Path(args.data_root), args.work_id)
    if not args.report_only:
        pages = (parse_pages(args.build_pages) if args.build_pages
                 else raw_store.available_pages(layout, list(LAYER_ENGINES.values())))
        if not pages:
            raise ValueError(f"Không có JSON OCR thô nào trong {layout.ocr_raw_unified_dir}")
        for layer in layers:
            # Bỏ qua khi raw/ + manifest OCR + tham số lọc không đổi kể từ lần dựng trước (chạy lại pipeline êm).
            stage = (f"layers:{layer}:{args.build_pages or 'all'}:"
                     f"{args.han_min_cjk_share}:{args.han_min_cjk_with_latin}")
            inputs = build_inputs(layout, layer)
            outputs = [layout.ocr_layer_xml_path(layer), layout.ocr_layer_manifest_path("build", layer)]
            if not args.force and outputs[0].exists() and deps.is_fresh(layout, stage, inputs, outputs[1]):
                log.info("[%s] %s: %s đã có, đầu vào không đổi: bỏ qua", layout.work_id, layer, outputs[0].name)
                continue
            build_layer(layout, layer, pages, force=args.force, han_min_cjk_share=args.han_min_cjk_share,
                        han_min_cjk_with_latin=args.han_min_cjk_with_latin)
            deps.record(layout, stage, inputs, outputs)
    errors = []
    for layer in layers:
        layer_errors, stats = verify_layer(layout, layer)
        log.info("[%s] kiểm tra %s: %s, %d lỗi", layout.work_id, layer, stats, len(layer_errors))
        errors += [f"{layer}: {e}" for e in layer_errors]
    row = summarize(layout)
    print(format_summary([row]))
    if errors:
        raise ValueError(f"{len(errors)} lỗi kiểm tra output:\n  " + "\n  ".join(errors[:50]))
    return row


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):       # khi bị pipe trên Windows, mặc định cp1252
        try:
            if not stream.isatty():
                stream.reconfigure(encoding="utf-8")
        except (AttributeError, OSError, ValueError):
            pass
    parser = create_parser()
    try:
        run(parse_args(parser, argv))
    except (OSError, ValueError, KeyError, json.JSONDecodeError, ET.ParseError) as exc:
        log.error("%s", exc)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
