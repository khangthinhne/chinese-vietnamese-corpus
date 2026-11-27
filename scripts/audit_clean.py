"""Kiểm toán bước làm sạch (structural + câu) của một work. Chỉ đọc; chạy lại bao nhiêu lần cũng được.

    python scripts/audit_clean.py --work-id CPO_19 [--data-root data] [--json out.json] [--strict]
    python scripts/audit_clean.py --work-id CPO_19 --snapshot snap.json      # băm output để so idempotent
    python scripts/audit_clean.py --compare snap1.json snap2.json

Kiểm tra (theo lớp han/am/nghia/tho):
  leftovers     số trang trơ trọi, tiêu đề chạy lặp nhiều trang, chỉ mục chú thích (1) [1] ¹, (chữ Hán) trong lớp han
  script        chữ Hán trong lớp am/nghia/tho, chữ cái Latin trong lớp han (và câu đó có bị gắn cờ không)
  duplicates    câu trùng liên tiếp; gạch nối cuối dòng chưa ghép ("chữ-" / "- ")
  loss          tổng ký tự (bỏ khoảng trắng) qua các bước layers -> structural -> sentences -> cleaned;
                phần mất KHÔNG có bản ghi (dropped.tsv / cleaning_audit.xml) > 10% bị đánh dấu
  provenance    SOURCE_IDS của mọi câu đã làm sạch phải trỏ tới ID dòng OCR có thật
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import sys
import unicodedata
import xml.etree.ElementTree as ET
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.common.corpus import DataLayout  # noqa: E402
from src.ocr.script_filter import CJK_IDEOGRAPH_RE, is_latin_letter  # noqa: E402

LAYERS = ("han", "am", "nghia", "tho")
LOSS_LIMIT = 0.10
PAGE_NUMBER_RE = re.compile(r"^\W*\d{1,4}\W*$")
FOOTNOTE_RE = re.compile(r"\(\s*\d{1,3}\s*\)|\[\s*\d{1,3}\s*\]|[¹²³⁴⁵⁶⁷⁸⁹⁰]+|\\\(\^\{[^}]*\}\\\)")
# chú thích kiểu (奇華海口門) trong câu Hán; KHÔNG tính hậu tố tiêu đề như （二首） "hai bài"
HAN_PAREN_RE = re.compile(r"[（(]\s*(?![一二三四五六七八九十]+\s*首)[㐀-鿿豈-﫿]{2,}\s*[)）]")
HYPHEN_RE = re.compile(r"\w-\s*$|\w-\s+\w")
LETTER_MARKER_RE = re.compile(r"\([a-zđ]\)")                 # chỉ mục khảo dị dạng chữ cái: (a), (b)
FULLWIDTH_PUNCT_RE = re.compile(r"[，。：；！？（）「」『』、]")
SPACE_BEFORE_PUNCT_RE = re.compile(r"\S\s+[,.;:!?)](?!\.)")


def chars(text: str) -> int:
    return sum(1 for c in text if not c.isspace())


def _units(path: Path, tag: str) -> list[ET.Element]:
    if not path.exists():
        return []
    return list(ET.parse(path).getroot().iter(tag))


def _ids(el: ET.Element, attr: str) -> list[str]:
    return [x for x in (el.get(attr) or "").split(",") if x]


def known_ocr_ids(layout: DataLayout) -> set[str]:
    """Mọi ID dòng OCR có thể xuất hiện trong SOURCE_IDS."""
    ids: set[str] = set()
    raw = layout.ocr_unified_xml_path
    for el in _units(raw, "LINE"):
        if el.get("ID"):
            ids.add(el.get("ID"))
    for stream in ("han", "vie"):                        # luồng mới: han.P0015.L001
        path = layout.ocr_layer_xml_path(stream)
        if path.exists():
            root = ET.parse(path).getroot()
            ocr = root.find("FILE/OCR")
            if ocr is not None and ocr.get("MODE") == "layer":
                for page in root.iter("PAGE"):
                    for line in page.findall("LINE"):
                        ids.add(f"{stream}.{line.get('ID')}")
    for ws in ("chi_tra", "vie"):                        # chế độ hệ chữ cũ: chi_tra.P205.L004
        path = layout.ocr_raw_xml_path(ws)
        if not path.exists():
            continue
        root = ET.parse(path).getroot()
        ocr = root.find("FILE/OCR")
        if ocr is None or ocr.get("MODE") == "layer":
            continue
        for page in ocr.findall("PAGE"):
            number = int(page.get("NUMBER", "0"))
            for line in page.findall("LINE"):
                ids.add(f"{ws}.P{number:03d}.L{int(line.get('NO', '0')):03d}")
    return ids


def flagged_chars(layout: DataLayout, layer: str) -> int:
    """Ký tự đã bị loại CÓ bản ghi: <layer>.dropped.tsv + cleaning_audit.xml (ORIGINAL của lớp)."""
    total = 0
    dropped = layout.cleaning_root / f"{layer}.dropped.tsv"
    if dropped.exists():
        with open(dropped, encoding="utf-8") as fh:
            for row in csv.DictReader(fh, delimiter="\t"):
                total += chars(row.get("text") or "")
    for rec in _units(layout.cleaning_root / "cleaning_audit.xml", "RECORD"):
        if rec.get("LAYER") == layer and rec.get("ACTION") in ("remove_line", "drop", "remove"):
            total += chars((rec.findtext("ORIGINAL") or ""))
    return total


def audit_layer(layout: DataLayout, layer: str, ocr_ids: set[str]) -> dict:
    seg = [u.text or "" for u in _units(layout.segmentation_layer(layer), "SEGMENT")]
    struct_el = _units(layout.cleaning_structural(layer), "SEGMENT")
    struct = [u.text or "" for u in struct_el]
    sent = [u.text or "" for u in _units(layout.segmentation_sentence(layer), "SENTENCE")]
    clean_el = _units(layout.cleaning_root / f"{layer}.xml", "SENTENCE")
    clean = [u.text or "" for u in clean_el]
    out: dict = {"counts": {"layers": len(seg), "structural": len(struct), "sentences": len(sent),
                            "cleaned": len(clean)}}
    if not (seg or struct or clean):
        return out

    # leftovers
    pages_by_text: dict[str, set[str]] = defaultdict(set)
    for u in struct_el:
        key = re.sub(r"[\d\W_]+", "", unicodedata.normalize("NFC", u.text or "").lower())
        if key:
            pages_by_text[key].add(u.get("PAGE", ""))
    out["leftovers"] = {
        "page_numbers": [t for t in clean if PAGE_NUMBER_RE.match(t)],
        "running_headers": sorted(k for k, v in pages_by_text.items() if len(v) >= 3),
        "footnote_markers": [t for t in clean if FOOTNOTE_RE.search(t)][:20],
        "han_paren_notes": [t for t in clean if layer == "han" and HAN_PAREN_RE.search(t)][:20],
    }
    # script mismatch (phải được gắn cờ, không xoá âm thầm)
    wrong = []
    for el in clean_el:
        text = el.text or ""
        bad = (CJK_IDEOGRAPH_RE.search(text) if layer != "han"
               else next((c for c in text if is_latin_letter(c)), None))
        if bad:
            wrong.append({"id": el.get("ID"), "flags": el.get("FLAGS", ""), "text": text[:80]})
    out["script"] = {"mismatched": len(wrong), "unflagged": sum(1 for w in wrong if not w["flags"]),
                     "examples": wrong[:10]}
    # duplicates / hyphen
    dups = [clean[i] for i in range(1, len(clean)) if clean[i] == clean[i - 1] and chars(clean[i]) > 3]
    out["duplicates"] = {"consecutive_sentences": len(dups), "examples": dups[:5],
                         "unjoined_hyphen": [t for t in clean if layer != "han" and HYPHEN_RE.search(t)][:10]}
    # loss
    c_seg, c_struct = sum(map(chars, seg)), sum(map(chars, struct))
    c_sent, c_clean = sum(map(chars, sent)), sum(map(chars, clean))
    flagged = flagged_chars(layout, layer)
    loss = {"chars": {"layers": c_seg, "structural": c_struct, "sentences": c_sent, "cleaned": c_clean},
            "flagged_dropped_chars": flagged, "alerts": []}
    for name, before, after, explained in (("layers->structural", c_seg, c_struct, flagged),
                                           ("structural->sentences", c_struct, c_sent, 0),
                                           ("sentences->cleaned", c_sent, c_clean, 0)):
        if before:
            lost = before - after
            unexplained = max(0, lost - explained)
            loss[name] = round(lost / before, 4)
            if unexplained / before > LOSS_LIMIT:
                loss["alerts"].append(f"{name}: mất {lost}/{before} ký tự ({lost / before:.0%}), "
                                      f"không có bản ghi {unexplained}")
    out["loss"] = loss
    # provenance
    missing, empty = [], 0
    for el in clean_el:
        ids = _ids(el, "SOURCE_IDS")
        if not ids:
            empty += 1
        missing += [i for i in ids if i not in ocr_ids]
    out["provenance"] = {"sentences_without_source_ids": empty, "unknown_source_ids": len(missing),
                         "examples": missing[:10], "known_ocr_ids": len(ocr_ids)}
    if layer != "han":
        out["letter_markers"] = [{"id": el.get("ID"), "pages": el.get("PAGES"), "text": (el.text or "")[:90]}
                                 for el in clean_el if LETTER_MARKER_RE.search(el.text or "")]
        out["punct"] = punct_issues(clean_el)
    out["numbers"] = number_loss(seg, clean)
    out["page_loss"] = page_loss(_units(layout.segmentation_layer(layer), "SEGMENT"), struct_el)
    return out


def punct_issues(clean_el: list[ET.Element]) -> dict:
    """Dấu câu trong lớp tiếng Việt: full-width, khoảng trắng trước dấu câu, ngoặc/ngoặc kép lệch."""
    full, space, unbalanced = [], [], []
    for el in clean_el:
        t = el.text or ""
        if FULLWIDTH_PUNCT_RE.search(t):
            full.append({"id": el.get("ID"), "text": t[:80]})
        if SPACE_BEFORE_PUNCT_RE.search(t):
            space.append({"id": el.get("ID"), "text": t[:80]})
        if t.count("(") != t.count(")") or t.count("“") != t.count("”") or t.count('"') % 2:
            unbalanced.append({"id": el.get("ID"), "text": t[:80]})
    return {"fullwidth": len(full), "space_before": len(space), "unbalanced": len(unbalanced),
            "examples": {"fullwidth": full[:5], "space_before": space[:5], "unbalanced": unbalanced[:5]}}


def number_loss(seg: list[str], clean: list[str]) -> dict:
    """Số (chuỗi chữ số) có ở lớp gốc mà mất ở bản sạch, KHÔNG tính số nằm trong chỉ mục chú thích/số trang."""
    def numbers(texts, strip):
        out: dict[str, int] = defaultdict(int)
        for t in texts:
            if strip:
                if PAGE_NUMBER_RE.match(t):
                    continue
                t = FOOTNOTE_RE.sub(" ", t)
            for n in re.findall(r"\d+", t):
                out[n] += 1
        return out
    before, after = numbers(seg, True), numbers(clean, False)
    lost = {n: c - after.get(n, 0) for n, c in before.items() if c > after.get(n, 0)}
    return {"before": sum(before.values()), "after": sum(after.values()), "lost": sum(lost.values()),
            "lost_examples": sorted(lost)[:15]}


def page_loss(seg_el: list[ET.Element], struct_el: list[ET.Element]) -> dict:
    """Tỉ lệ ký tự bỏ ở bước clean theo trang; trang bất thường = > max(3 x trung vị, 20%) và >= 30 ký tự."""
    before: dict[str, int] = defaultdict(int)
    after: dict[str, int] = defaultdict(int)
    for el in seg_el:
        before[el.get("PAGE", "")] += chars(el.text or "")
    for el in struct_el:
        after[el.get("PAGE", "")] += chars(el.text or "")
    ratios = {p: (b - after.get(p, 0)) / b for p, b in before.items() if b}
    if not ratios:
        return {"median": 0.0, "outliers": []}
    vals = sorted(ratios.values())
    median = vals[len(vals) // 2]
    limit = max(3 * median, 0.20)
    outliers = sorted(((p, round(r, 3), before[p] - after.get(p, 0)) for p, r in ratios.items()
                       if r > limit and before[p] - after.get(p, 0) >= 30), key=lambda x: -x[1])
    return {"median": round(median, 4), "limit": round(limit, 3),
            "outliers": [{"page": p, "ratio": r, "chars_lost": n} for p, r, n in outliers[:20]]}


def problems(report: dict) -> list[str]:
    msgs = []
    for layer, r in report["layers"].items():
        if "loss" not in r:
            continue
        lo = r["leftovers"]
        if lo["page_numbers"]:
            msgs.append(f"{layer}: {len(lo['page_numbers'])} câu chỉ là số trang")
        if lo["running_headers"]:
            msgs.append(f"{layer}: tiêu đề chạy còn sót {lo['running_headers'][:3]}")
        if lo["footnote_markers"]:
            msgs.append(f"{layer}: {len(lo['footnote_markers'])} câu còn chỉ mục chú thích")
        if lo["han_paren_notes"]:
            msgs.append(f"{layer}: {len(lo['han_paren_notes'])} câu Hán còn chú thích trong ngoặc")
        if r["script"]["unflagged"]:
            msgs.append(f"{layer}: {r['script']['unflagged']} câu lệch hệ chữ không gắn cờ")
        if r["duplicates"]["consecutive_sentences"]:
            msgs.append(f"{layer}: {r['duplicates']['consecutive_sentences']} câu trùng liên tiếp")
        if r["duplicates"]["unjoined_hyphen"]:
            msgs.append(f"{layer}: {len(r['duplicates']['unjoined_hyphen'])} câu có gạch nối chưa ghép")
        msgs += [f"{layer}: {a}" for a in r["loss"]["alerts"]]
        if r.get("letter_markers"):
            msgs.append(f"{layer}: {len(r['letter_markers'])} câu còn chỉ mục chữ cái (a)/(b)")
        pu = r.get("punct") or {}
        if pu.get("fullwidth") or pu.get("space_before") or pu.get("unbalanced"):
            msgs.append(f"{layer}: dấu câu: full-width {pu['fullwidth']}, khoảng trắng trước dấu {pu['space_before']}, "
                        f"ngoặc lệch {pu['unbalanced']}")
        if r["numbers"]["lost"]:
            msgs.append(f"{layer}: {r['numbers']['lost']} số bị mất (vd {r['numbers']['lost_examples'][:5]})")
        if r["page_loss"]["outliers"]:
            msgs.append(f"{layer}: {len(r['page_loss']['outliers'])} trang mất ký tự bất thường "
                        f"(vd {[o['page'] for o in r['page_loss']['outliers'][:5]]})")
        pv = r["provenance"]
        if pv["sentences_without_source_ids"] or pv["unknown_source_ids"]:
            msgs.append(f"{layer}: truy vết hỏng: {pv['sentences_without_source_ids']} câu không có SOURCE_IDS, "
                        f"{pv['unknown_source_ids']} ID không tồn tại (vd {pv['examples'][:3]})")
    return msgs


def audit(layout: DataLayout) -> dict:
    ocr_ids = known_ocr_ids(layout)
    report = {"work_id": layout.work_id, "layers": {l: audit_layer(layout, l, ocr_ids) for l in LAYERS}}
    report["problems"] = problems(report)
    return report


def snapshot(layout: DataLayout) -> dict[str, str]:
    files = {}
    for folder in (layout.segmentation_dir, layout.cleaning_root):
        for path in sorted(folder.rglob("*")) if folder.exists() else []:
            if path.is_file():
                files[path.relative_to(layout.work_dir).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    return files


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--work-id")
    p.add_argument("--data-root", default="data")
    p.add_argument("--json", help="Ghi báo cáo JSON")
    p.add_argument("--snapshot", help="Ghi băm SHA-256 của 04_segmentation/ và 05_cleaning/")
    p.add_argument("--compare", nargs=2, metavar=("A", "B"), help="So hai snapshot")
    p.add_argument("--strict", action="store_true", help="Mã thoát 1 nếu có vấn đề")
    args = p.parse_args(argv)
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError, OSError):
            pass
    if args.compare:
        a, b = (json.loads(Path(x).read_text(encoding="utf-8")) for x in args.compare)
        changed = sorted(k for k in a.keys() | b.keys() if a.get(k) != b.get(k))
        print(f"{len(changed)} file khác nhau" + (": " + ", ".join(changed[:20]) if changed else ""))
        return 1 if changed and args.strict else 0
    if not args.work_id:
        p.error("cần --work-id")
    layout = DataLayout(Path(args.data_root), args.work_id)
    if args.snapshot:
        Path(args.snapshot).write_text(json.dumps(snapshot(layout), indent=1), encoding="utf-8")
        print(f"snapshot -> {args.snapshot}")
        return 0
    report = audit(layout)
    if args.json:
        Path(args.json).write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    for layer, r in report["layers"].items():
        if "loss" in r:
            print(f"[{args.work_id}] {layer}: {r['counts']} ký tự {r['loss']['chars']}")
    for msg in report["problems"]:
        print(f"[{args.work_id}] VẤN ĐỀ {msg}")
    if not report["problems"]:
        print(f"[{args.work_id}] không phát hiện vấn đề")
    return 1 if report["problems"] and args.strict else 0


if __name__ == "__main__":
    raise SystemExit(main())
