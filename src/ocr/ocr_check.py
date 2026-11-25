"""Kiểm tra OCR thô hai luồng đã "đủ" chưa (chỉ đọc; không cần GPU; không sửa dữ liệu).

Dùng qua `scripts/check_ocr_complete.py`. Mỗi vấn đề là một dòng `Issue(page, level, code, detail)`:
- `error`: phải xử lý trước khi coi OCR là xong (thiếu ảnh/run, run hỏng ở phần chữ Việt, XML/TXT lệch...).
- `warning`: cần người xem (trang Hán bất thường, conf thấp, dòng giống tiếng Anh...).
- `info`: ghi nhận, hợp lệ (trang chỉ có Hán, trang trắng, chữ giản thể...).

Run của một trang được chọn đúng như `build_layers` (manifest -> run hợp lệ -> mới nhất), nên kết quả khớp với
han.xml/vie.xml khi build còn mới; build cũ hơn raw thì báo `stale_build`.
"""

from __future__ import annotations

import json
import random
import re
import statistics
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass
from pathlib import Path

from src.common.corpus import DataLayout, parse_pages
from src.common.xmlstore import file_element
from src.ocr import raw_store
from src.ocr.build_layers import (LAYER_ENGINES, LAYER_WRITING_SYSTEMS, PAGE_TXT_RE, preferred_keys,
                                  select_run, verify_layer)
from src.ocr.run_quality import bad_units, run_units
from src.ocr.script_filter import CJK_IDEOGRAPH_RE, LAYERS, count_cjk
from src.ocr.work_config import CHECK_DEFAULTS, compact, image_pages

LEVELS = ("error", "warning", "info")
CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f\u200b-\u200f\ufeff]")
VIE_SPECIFIC_RE = re.compile(r"[À-ỹĐđ]")
ENGLISH_WORD_RE = re.compile(r"\b(?:the|and|of|is|are|this|that|with|from|which|have|text|image|words|written)\b",
                             re.IGNORECASE)


@dataclass(frozen=True)
class Issue:
    page: int | None
    level: str
    code: str
    detail: str = ""


# --------------------------------------------------------------------------- #
# Hàm thuần trên một run
# --------------------------------------------------------------------------- #
def mean_conf(run: dict) -> float | None:
    vals = [(float(x["confidence"]), len(str(x.get("text") or ""))) for x in (run or {}).get("lines", [])
            if x.get("confidence") is not None and str(x.get("text") or "").strip()]
    total = sum(w for _, w in vals)
    return round(sum(c * w for c, w in vals) / total, 2) if total else None


def english_like(text: str) -> bool:
    """Dòng (không có chữ Việt có dấu) chứa >= 3 từ tiếng Anh thông dụng: nghi mô hình dịch/giải thích."""
    return not VIE_SPECIFIC_RE.search(text) and len(ENGLISH_WORD_RE.findall(text)) >= 3


def han_page_anomalies(lines: list[str], unit_flags: list[list[str]], median_lines: float,
                       check: dict) -> list[tuple[str, str]]:
    """Dấu hiệu trang Hán bị cắt/đọc sai hướng. Trả về [(code, chi tiết)]."""
    out = []
    n = len(lines)
    if n and median_lines and n > check["han_line_factor"] * median_lines and n >= 10:
        out.append(("han_many_lines", f"{n} dòng > {check['han_line_factor']} x trung vị {median_lines:g}"))
    long_lines = [x for x in lines if len(CJK_IDEOGRAPH_RE.findall(x)) > check["han_max_line_chars"]]
    if long_lines:
        out.append(("han_long_line", f"{len(long_lines)} dòng > {check['han_max_line_chars']:g} chữ, "
                                     f"vd {long_lines[0][:20]}..."))
    single = [x for x in lines if len(CJK_IDEOGRAPH_RE.findall(x)) == 1]
    if n >= 6 and len(single) / n > check["han_single_char_share"]:
        out.append(("han_single_char_lines", f"{len(single)}/{n} dòng chỉ một chữ (nghi đọc theo cột)"))
    if unit_flags:
        gap = sum(1 for f in unit_flags if "han_gap_removed" in f)
        if len(unit_flags) >= 4 and gap / len(unit_flags) > check["han_gap_unit_share"]:
            out.append(("han_gap_units", f"{gap}/{len(unit_flags)} đơn vị có cờ han_gap_removed"))
    return out


def simplified_chars(text: str, converter) -> list[str]:
    """Chữ có dạng phồn thể khác nó (OpenCC s2t) = chữ giản thể (hoặc dị thể) lẫn vào luồng Hán."""
    return [c for c in dict.fromkeys(CJK_IDEOGRAPH_RE.findall(text)) if converter(c) != c]


def ink_ratio(image_path: Path) -> float:
    import numpy as np
    from PIL import Image
    with Image.open(image_path) as im:
        im = im.convert("L")
        im.thumbnail((800, 800))
        a = np.asarray(im)
    return float((a < 128).mean())


# --------------------------------------------------------------------------- #
# Đọc XML/TXT luồng
# --------------------------------------------------------------------------- #
def layer_lines(layout: DataLayout, layer: str) -> dict[int, list[str]]:
    path = layout.ocr_layer_xml_path(layer)
    if not path.exists():
        return {}
    root = file_element(ET.parse(path).getroot())
    return {int(p.get("NUMBER")): [x.text or "" for x in p.findall("LINE")] for p in root.findall("PAGE")}


def built_keys(layout: DataLayout, layer: str) -> dict[int, str | None]:
    path = layout.ocr_layer_manifest_path("build", layer)
    if not path.exists():
        return {}
    return {r["page"]: r.get("key") for r in json.loads(path.read_text(encoding="utf-8")).get("pages", [])}


def image_paths(layout: DataLayout) -> dict[int, Path]:
    out = {}
    for path in layout.pages_dir.iterdir() if layout.pages_dir.is_dir() else []:
        m = re.search(r"(\d+)$", path.stem)
        if m and path.is_file():
            out[int(m[1])] = path
    return out


# --------------------------------------------------------------------------- #
# Kiểm tra một work
# --------------------------------------------------------------------------- #
def check_work(layout: DataLayout, pages_spec: str | None, check: dict | None = None,
               converter=None) -> dict:
    check = {**CHECK_DEFAULTS, **(check or {})}
    issues: list[Issue] = []
    images = image_paths(layout)
    pages = parse_pages(pages_spec) if pages_spec else image_pages(layout)
    for p in pages:
        if p not in images:
            issues.append(Issue(p, "error", "missing_image", f"không có ảnh trong {layout.rel(layout.pages_dir)}"))
    for name in (images[p].name for p in images):
        if not re.fullmatch(r"page_\d{3,4}\.(png|jpg)", name):
            issues.append(Issue(None, "warning", "image_name", name))
    extra_images = sorted(set(images) - set(pages))

    # --- run được chọn cho mỗi trang, mỗi luồng
    selected: dict[str, dict[int, dict | None]] = {}
    reasons: dict[str, dict[int, str]] = {}
    ocr_errors: dict[str, set[int]] = {}
    for layer in LAYERS:
        engine = LAYER_ENGINES[layer]
        prefs = preferred_keys(layout, layer, engine)
        selected[layer], reasons[layer] = {}, {}
        for p in pages:
            runs = raw_store.page_runs(layout, engine, p, layout.work_id, LAYER_WRITING_SYSTEMS[layer])
            run, why = select_run(runs, engine, prefs.get(p, []), layer=layer)
            selected[layer][p], reasons[layer][p] = run, why
            if why == "latest_of_many":
                issues.append(Issue(p, "info", f"{layer}_multi_run",
                                    f"nhiều run {engine} khác khoá, không có manifest chỉ định; chọn theo quy tắc"))
        mpath = layout.ocr_layer_manifest_path("ocr", layer)
        manifest = json.loads(mpath.read_text(encoding="utf-8")) if mpath.exists() else {}
        ocr_errors[layer] = set(manifest.get("error_pages", []))

    # --- XML/TXT
    xml_lines: dict[str, dict[int, list[str]]] = {}
    for layer in LAYERS:
        try:
            xml_lines[layer] = layer_lines(layout, layer)
            errors, _ = verify_layer(layout, layer)
        except (ValueError, ET.ParseError, OSError) as exc:          # XML hỏng: báo lỗi, không dừng kiểm tra
            xml_lines[layer] = {}
            errors = [f"không đọc được {layout.ocr_layer_xml_path(layer).name}: {exc}"]
        for e in errors:
            issues.append(Issue(None, "error", f"{layer}_xml", e))
        text_dir = layout.ocr_layer_text_dir(layer)
        if text_dir.is_dir():
            for f in text_dir.iterdir():
                if f.suffix == ".txt" and not PAGE_TXT_RE.match(f.name):
                    issues.append(Issue(None, "error", f"{layer}_txt_name", f.name))
        built = built_keys(layout, layer)
        for p in pages:
            run = selected[layer][p]
            key = (run or {}).get("key")
            if run is not None and not built.get(p):
                issues.append(Issue(p, "error", "stale_build", f"{layer}: có run nhưng build_layers chưa dùng"))
            elif run is not None and built.get(p) != key:
                issues.append(Issue(p, "error", "stale_build",
                                    f"{layer}: build dùng khoá {str(built.get(p))[:8]}, run chọn {key[:8]}"))
        for p, lines in xml_lines[layer].items():
            for i, text in enumerate(lines, 1):
                if not text.strip():
                    issues.append(Issue(p, "error", f"{layer}_empty_line", f"L{i:03d}"))
                if CONTROL_RE.search(text):
                    issues.append(Issue(p, "error", f"{layer}_control_char", f"L{i:03d}: {text[:30]!r}"))
                if layer == "vie" and count_cjk(text):
                    issues.append(Issue(p, "error", "vie_has_cjk", f"L{i:03d}"))
                if layer == "vie" and english_like(text):
                    issues.append(Issue(p, "warning", "vie_english_like", f"L{i:03d}: {text[:70]}"))

    # --- từng trang
    han_counts = [len(xml_lines["han"].get(p, [])) for p in pages if xml_lines["han"].get(p)]
    median_lines = statistics.median(han_counts) if han_counts else 0
    ink_cache: dict[int, float] = {}

    def ink(p: int) -> float | None:
        if p not in images:
            return None
        if p not in ink_cache:
            ink_cache[p] = ink_ratio(images[p])
        return ink_cache[p]

    page_rows = []
    for p in pages:
        han_run, vie_run = selected["han"][p], selected["vie"][p]
        han_lines, vie_lines = xml_lines["han"].get(p, []), xml_lines["vie"].get(p, [])
        row = {"page": p, "han_run": bool(han_run), "vie_run": bool(vie_run),
               "han_lines": len(han_lines), "vie_lines": len(vie_lines),
               "han_selection": reasons["han"][p], "vie_selection": reasons["vie"][p],
               "vie_conf": mean_conf(vie_run) if vie_run else None, "class": ""}
        for layer, run in (("han", han_run), ("vie", vie_run)):
            engine = LAYER_ENGINES[layer]
            if run is None:
                level = "error"
                detail = f"chưa có run {engine}"
                if p in ocr_errors[layer]:
                    detail += " (manifest ghi trang lỗi OCR)"
                issues.append(Issue(p, level, f"no_{layer}_run", detail))
                continue
            for unit in bad_units(run, layer):
                issues.append(Issue(p, "error", f"{layer}_degenerate",
                                    f"đơn vị {unit.get('band', unit.get('unit'))} {unit.get('flags')}: "
                                    f"{str(unit.get('generated_text') or '')[:40]!r}"))
            if not run.get("lines"):
                issues.append(Issue(p, "warning", f"{layer}_run_no_lines", f"run {engine} không có dòng nào"))
        if han_run and vie_run:
            if not han_lines and not vie_lines:
                r = ink(p)
                if r is not None and r < check["blank_ink"]:
                    row["class"] = "blank"
                    issues.append(Issue(p, "info", "blank_page", f"mực {r:.4f}"))
                else:
                    issues.append(Issue(p, "error", "no_text", f"không ra dòng Hán lẫn Việt, mực {r}"))
            elif han_lines and not vie_lines:
                row["class"] = "han_only"
                issues.append(Issue(p, "info", "han_only_page", f"{len(han_lines)} dòng Hán, không có chữ Việt"))
            elif vie_lines and not han_lines:
                row["class"] = "vie_only"
            else:
                row["class"] = "bilingual"
            if not han_lines and len(vie_lines) < check["vie_min_lines"]:
                r = ink(p)
                if r is not None and r >= check["vie_min_lines_ink"] and vie_lines:
                    issues.append(Issue(p, "error", "vie_too_few_lines",
                                        f"{len(vie_lines)} dòng Việt, không có Hán, mực {r:.3f}"))
        if vie_run and row["vie_conf"] is not None and vie_lines and row["vie_conf"] < check["qwen_low_conf"]:
            issues.append(Issue(p, "warning", "vie_low_conf", f"conf {row['vie_conf']}"))
        if han_run and han_lines:
            flags = [list(u.get("flags") or []) for u in run_units(han_run)]
            for code, detail in han_page_anomalies(han_lines, flags, median_lines, check):
                issues.append(Issue(p, "warning", code, detail))
            if converter is not None:
                simp = simplified_chars("".join(han_lines), converter)
                if simp:
                    issues.append(Issue(p, "info", "han_simplified", "".join(simp[:20])))
        page_rows.append(row)

    counts: dict[str, dict[str, int]] = {lv: {} for lv in LEVELS}
    for i in issues:
        counts[i.level][i.code] = counts[i.level].get(i.code, 0) + 1
    totals = {
        "pages": len(pages), "range": compact(pages),
        "images": sum(1 for p in pages if p in images), "extra_images": compact(extra_images),
        "han_run": sum(1 for r in page_rows if r["han_run"]), "vie_run": sum(1 for r in page_rows if r["vie_run"]),
        "han_pages": sum(1 for r in page_rows if r["han_lines"]), "vie_pages": sum(1 for r in page_rows if r["vie_lines"]),
        "han_lines": sum(r["han_lines"] for r in page_rows), "vie_lines": sum(r["vie_lines"] for r in page_rows),
        "classes": {c: sum(1 for r in page_rows if r["class"] == c)
                    for c in ("bilingual", "han_only", "vie_only", "blank", "")},
        "han_median_lines": median_lines,
        "errors": sum(counts["error"].values()), "warnings": sum(counts["warning"].values()),
        "infos": sum(counts["info"].values()),
    }
    return {"work_id": layout.work_id, "check": check, "totals": totals, "counts": counts,
            "complete": totals["errors"] == 0, "issues": [asdict(i) for i in issues], "pages": page_rows}


def sample_pages(report: dict, n: int, seed: int) -> list[int]:
    """Trang ngẫu nhiên (seed cố định) trong số trang có cả dòng Hán lẫn Việt (ưu tiên) để kiểm tra bằng mắt."""
    rows = report["pages"]
    pool = [r["page"] for r in rows if r["class"] == "bilingual"] or [r["page"] for r in rows if r["class"]]
    rng = random.Random(seed)
    return sorted(rng.sample(pool, min(n, len(pool))))


def format_report(report: dict, max_issues: int = 40) -> str:
    t = report["totals"]
    lines = [f"== {report['work_id']}: trang {t['range']} ({t['pages']} trang, ảnh {t['images']})",
             f"   run Hán {t['han_run']}/{t['pages']}, run Việt {t['vie_run']}/{t['pages']}; "
             f"trang có dòng Hán {t['han_pages']}, có dòng Việt {t['vie_pages']}; "
             f"dòng Hán {t['han_lines']}, dòng Việt {t['vie_lines']}",
             f"   loại trang: {t['classes']}",
             f"   lỗi {t['errors']}, cảnh báo {t['warnings']}, ghi nhận {t['infos']} -> "
             + ("ĐỦ (0 lỗi)" if report["complete"] else "CHƯA ĐỦ")]
    for level in LEVELS:
        if report["counts"][level]:
            lines.append(f"   {level}: " + ", ".join(f"{k}={v}" for k, v in sorted(report["counts"][level].items())))
    shown = [i for i in report["issues"] if i["level"] != "info"][:max_issues]
    for i in shown:
        page = f"P{i['page']:04d}" if i["page"] is not None else "-----"
        lines.append(f"   [{i['level']}] {page} {i['code']}: {i['detail']}")
    rest = sum(1 for i in report["issues"] if i["level"] != "info") - len(shown)
    if rest > 0:
        lines.append(f"   ... và {rest} vấn đề khác (xem --json)")
    return "\n".join(lines)
