"""Đo chất lượng segmentation (tài liệu -> lớp -> câu) của một work ở chế độ hai luồng han.xml/vie.xml.

    python scripts/audit_segmentation.py --work-id CPO_20 [--data-root data] [--config configs/CPO_20.json]
                                         [--json out.json] [--top 20] [--sample 5 --seed 20261126]

Chỉ ĐỌC: 03_ocr/ocr_raw/{han,vie}.xml, 04_segmentation/ (documents, layers, section_headings, metadata, review,
sentences), 05_cleaning/structural (chế độ tách câu), 06_alignment/han-am.xml (nếu có, proxy hạ nguồn). Không ghi
gì ngoài file --json.

Các phép đo:
- documents: số tài liệu, độ dài (trang, dòng), quá ngắn/quá dài, thiếu lớp, thứ tự lớp bất thường, lớp lặp lại,
  đối chiếu MỤC LỤC (nếu trang mục lục nằm trong phần đã OCR).
- accounting: mỗi dòng OCR đi tới đúng một nơi (lớp, tiêu đề mục, metadata, unassigned); dòng mất, dòng hai nơi.
- leakage: chữ Hán trong lớp Việt, Latin trong lớp Hán, tiêu đề mục/mục chú thích/tiêu đề chạy/số trang/tên người
  dịch trong thân lớp; chỉ mục chú thích dạng số còn trong câu Việt.
- han_am: số chữ Hán vs số âm tiết theo tài liệu (khớp / ±10% / lệch), thơ so từng dòng.
- tho: số âm tiết mỗi dòng, thể thơ, dòng bất thường. nghia: rỗng khi có tiêu đề, tỉ lệ độ dài bất thường.
- sentences: phân bố độ dài, câu ngoại lai, chế độ tách han/am, lệch số câu > 30%, câu vượt ranh giới.
- beads: tỉ lệ bead han-am hợp lệ theo tài liệu (chỉ để xếp hạng, không phải thước đo đúng/sai).
- suspects: xếp hạng tài liệu nghi lỗi; sample: mẫu phân tầng (ngẫu nhiên + nghi vấn) để rà bằng mắt.
"""

from __future__ import annotations

import argparse
import json
import random
import re
import statistics
import sys
import unicodedata
from collections import Counter, defaultdict
from difflib import SequenceMatcher
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.common.corpus import DataLayout, parse_pages  # noqa: E402
from src.common.sentences import find_alignment_file  # noqa: E402
from src.ocr.script_filter import CJK_IDEOGRAPH_RE, is_latin_letter  # noqa: E402
from src.segmentation import serialization as ser  # noqa: E402
from src.segmentation import streams  # noqa: E402

VIE_LAYERS = ("am", "nghia", "tho")
LAYER_ORDER = ("han", "am", "nghia", "tho")
SECTION_WORDS = ("phien am", "dich nghia", "dich tho", "khao di", "chu thich", "nguyen chu", "chu giai", "dich am")
NOTE_ENTRY_RE = re.compile(r"^\s*(?:\d{1,3}|[a-zđ])\s*[.)]\s+\S")
INLINE_NOTE_DIGIT_RE = re.compile(r"(?<=[^\W\d_])\s+\d{1,2}(?=[\s,.;:!?)]|$)")
PAGE_NUMBER_RE = re.compile(r"^\s*[-–—]?\s*\d{1,4}\s*[-–—]?\s*$")
SYLLABLE_RE = re.compile(r"[^\W\d_]+")
ABBREV_END_RE = re.compile(r"(?i)(?:\b(?:nxb|tr|tk|ông|bà|q|tp|ts|gs|pgs|v\.v)\.|\b[IVXLC]{1,5}\.|\b[A-ZĐ]\.)\s*$")
TOC_TITLE_RE = re.compile(r"m[uụ]c\s*l[uụ]c", re.I)
TOC_ENTRY_RE = re.compile(r"^(?P<title>.*?[^\W\d_].*?)[\s.…·_\-]*(?P<page>\d{1,4})\s*$")
DOC_LONG_FACTOR = 5
DOC_SHORT_LINES = 2
MISMATCH = 0.30
BEAD_MIN_SCORE = 0.5


# ----------------------------------------------------------------------------- văn bản
def fold(text: str) -> str:
    """Chữ thường, bỏ dấu tiếng Việt, gộp khoảng trắng (so khớp mờ tiêu đề)."""
    text = unicodedata.normalize("NFD", unicodedata.normalize("NFC", text).lower().replace("đ", "d"))
    text = "".join(c for c in text if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s]", " ", text)).strip()


def han_units(text: str) -> int:
    """Số chữ Hán (không tính dấu câu), chữ khuyết □/[MASK] tính 1."""
    return len(CJK_IDEOGRAPH_RE.findall(text)) + text.count("□") + text.count("[MASK]")


def syllables(text: str) -> int:
    return len(SYLLABLE_RE.findall(text))


def has_latin_outside_mask(text: str) -> bool:
    return any(is_latin_letter(c) for c in text.replace("[MASK]", ""))


def is_upper_title(text: str) -> bool:
    letters = [c for c in text if c.isalpha()]
    return len(letters) >= 3 and all(c.isupper() for c in letters)


def numbered_title(text: str) -> bool:
    return re.match(r"^\s*\d{1,3}\s*[.．、]\s*\S", text) is not None


def norm_line(text: str) -> str:
    return re.sub(r"\s+", " ", unicodedata.normalize("NFC", text)).strip()


def match_key(text: str) -> str:
    """Khoá so dòng metadata với dòng OCR: bỏ dấu, chữ số, chỉ số trên (metadata đã bỏ chỉ mục chú thích "¹")."""
    t = unicodedata.normalize("NFKC", text)
    return re.sub(r"[\d\s]+", "", fold(t)) or norm_line(text)


ROMAN_RE = re.compile(r"^\s*\(?[IVXLC]{1,6}\)?[.:]?\s*$")


def is_body_line(text: str, headers=None) -> bool:
    """Dòng nội dung thật của lớp (không phải mục chú thích, dòng lặp nhiều trang, tiêu đề viết hoa, số La Mã)."""
    return not (is_upper_title(text) or NOTE_ENTRY_RE.match(text) or ROMAN_RE.match(text)
                or (headers is not None and headers(text)))


# ----------------------------------------------------------------------------- đọc dữ liệu
def load_work(layout: DataLayout, pages: list[int] | None) -> dict:
    seg = layout.segmentation_dir
    han = streams.load_stream(layout, "han", pages)
    vie = streams.load_stream(layout, "vie", pages)
    merged = streams.merge_streams(list(han), list(vie))
    data = {
        "lines": {ln.source_id: {"page": ln.page, "stream": ln.writing_system, "text": ln.text} for ln in merged},
        "order": {ln.source_id: i for i, ln in enumerate(merged)},
        "documents": ser.read_documents(seg / "documents.xml") if (seg / "documents.xml").exists() else [],
        "layers": {}, "headings": [], "metadata": [], "review": [], "sentences": {}, "structural": {},
        "beads": [],
    }
    for layer in LAYER_ORDER:
        path = layout.segmentation_layer(layer)
        data["layers"][layer] = ser.read_layer(path) if path.exists() else []
        spath = seg / "sentences" / f"{layer}.xml"
        data["sentences"][layer] = ser.read_sentences(spath) if spath.exists() else []
        struct = layout.cleaning_structural(layer)
        data["structural"][layer] = ser.read_structural_layer(struct) if struct.exists() else []
    if (seg / "section_headings.xml").exists():
        data["headings"] = ser.read_headings(seg / "section_headings.xml")
    if (seg / "document_metadata.xml").exists():
        data["metadata"] = ser.read_document_metadata(seg / "document_metadata.xml")
    if (seg / "review_required.xml").exists():
        data["review"] = ser.read_review(seg / "review_required.xml")
    align = find_alignment_file(layout, "han-am")
    if align.exists() and align.suffix == ".xml":
        text = align.read_text(encoding="utf-8")
        data["beads"] = [{"document_id": m["doc"], "kind": m["kind"], "score": float(m["score"] or 0)}
                         for m in re.finditer(r'<ALIGNMENT [^>]*DOCUMENT_ID="(?P<doc>[^"]*)" KIND="(?P<kind>[^"]*)"'
                                              r'(?: SCORE="(?P<score>[^"]*)")?', text)]
    return data


# ----------------------------------------------------------------------------- 3. kế toán dòng
def metadata_source_ids(metadata: list[dict], lines: dict[str, dict]) -> dict[str, str]:
    """Metadata ghi ID dòng NỘI BỘ của tài liệu, không phải ID luồng: ánh xạ bằng (trang, văn bản)."""
    by_page: dict[int, dict[str, list[str]]] = defaultdict(lambda: defaultdict(list))
    for sid, ln in lines.items():
        by_page[ln["page"]][match_key(ln["text"])].append(sid)
    out: dict[str, str] = {}
    for doc in metadata:
        groups = list(doc["metadata"].items()) + [("uncertain", doc["uncertain"])]
        for name, per_layer in groups:
            for occs in per_layer.values():
                for occ in occs:
                    key = match_key(occ["text"])
                    for page in occ.get("pages") or []:
                        # một trường metadata có thể ghép nhiều dòng liên tiếp ("TIÊU ĐỀ DÒNG 1 DÒNG 2")
                        for line_key, sids in by_page.get(page, {}).items():
                            if line_key == key or (len(line_key) >= 2 and line_key in key):
                                for sid in sids:
                                    out.setdefault(sid, f"metadata:{name}")
    return out


def account_lines(lines: dict[str, dict], destinations: dict[str, list[str]]) -> dict:
    """destinations: source_id -> danh sách nơi đến ("layer:am", "heading", "metadata:title", "unassigned").
    Dòng mất = không có nơi đến; dòng hai nơi = >= 2 nơi đến khác nhau (một dòng tiêu đề mục ghi 2 lần end/start
    vẫn tính là 1 nơi)."""
    lost = [sid for sid in lines if not destinations.get(sid)]
    double = {sid: sorted(set(d)) for sid, d in destinations.items() if sid in lines and len(set(d)) > 1}
    kinds = Counter()
    for sid in lines:
        dests = set(destinations.get(sid, []))
        kinds["lost" if not dests else ("double" if len(dests) > 1 else next(iter(dests)).split(":")[0])] += 1
    by_stream = Counter(lines[sid]["stream"] for sid in lost)
    return {"total": len(lines), "kinds": dict(kinds), "lost": len(lost), "lost_by_stream": dict(by_stream),
            "double": len(double), "lost_examples": [(sid, lines[sid]["text"][:60]) for sid in lost[:15]],
            "double_examples": [(sid, d, lines[sid]["text"][:60]) for sid, d in list(double.items())[:10]],
            "lost_ids": lost}


def build_destinations(data: dict) -> dict[str, list[str]]:
    dest: dict[str, list[str]] = defaultdict(list)
    for layer, rows in data["layers"].items():
        for r in rows:
            for sid in r["source_ids"]:
                dest[sid].append(f"layer:{layer}")
    for h in data["headings"]:
        for sid in h["source_ids"]:
            if h["kind"] != "document_start":          # mốc mở tài liệu trỏ vào dòng Hán đã nằm trong lớp han
                dest[sid].append("heading")
    for sid, what in metadata_source_ids(data["metadata"], data["lines"]).items():
        dest[sid].append(what)
    for r in data["review"]:
        if r["reason"] in ("unassigned_line", "dropped_cross_stream"):
            for sid in r["source_ids"]:
                dest[sid].append("unassigned" if r["reason"] == "unassigned_line" else "dropped_cross_stream")
    return dest


# ----------------------------------------------------------------------------- 3. rò rỉ
def leakage(layer_rows: dict[str, list[dict]], *, headers=None, translator_names=()) -> dict:
    """Đếm dòng rò rỉ trong thân lớp (04_segmentation/layers)."""
    out: dict[str, Counter] = {layer: Counter() for layer in layer_rows}
    examples: dict[str, list] = defaultdict(list)
    names = {fold(n) for n in translator_names}

    def hit(layer, kind, row):
        out[layer][kind] += 1
        if len(examples[f"{layer}:{kind}"]) < 5:
            examples[f"{layer}:{kind}"].append((row["id"], row["page"], row["text"][:70]))

    for layer, rows in layer_rows.items():
        for row in rows:
            text = row["text"]
            f = fold(text)
            if layer == "han":
                if has_latin_outside_mask(text):
                    hit(layer, "latin", row)
            else:
                if CJK_IDEOGRAPH_RE.search(text):
                    hit(layer, "cjk", row)
                if NOTE_ENTRY_RE.match(text) and not is_upper_title(text):
                    hit(layer, "note_entry", row)
                if INLINE_NOTE_DIGIT_RE.search(text):
                    hit(layer, "inline_note_digit", row)
            if any(f.startswith(w) for w in SECTION_WORDS) and len(f.split()) <= 4:
                hit(layer, "section_heading", row)
            if PAGE_NUMBER_RE.match(text):
                hit(layer, "page_number", row)
            if headers is not None and headers(text):
                hit(layer, "repeated_line", row)       # lặp >= 3 trang: tiêu đề chạy, "X dịch", "Trích ..."
            if f in names:
                hit(layer, "translator_name", row)
    return {"counts": {k: dict(v) for k, v in out.items()}, "examples": dict(examples)}


# ----------------------------------------------------------------------------- 3. Hán <-> âm, thơ, nghĩa
def by_doc(rows: list[dict]) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        out[r["document_id"]].append(r)
    return out


def han_am_consistency(han_rows: list[dict], am_rows: list[dict], headers=None) -> dict:
    """Theo tài liệu: chữ Hán vs âm tiết (bỏ dòng tiêu đề có số ở lớp han, tiêu đề VIẾT HOA ở lớp am vì tiêu đề
    thường đi vào metadata ở một lớp mà không ở lớp kia). Thơ (dòng Hán trung bình <= 8 chữ, số dòng bằng nhau):
    so từng dòng."""
    han_d, am_d = by_doc(han_rows), by_doc(am_rows)
    docs = []
    for doc in sorted(set(han_d) & set(am_d)):
        h = [r for r in han_d[doc] if not numbered_title(r["text"])]
        a = [r for r in am_d[doc] if is_body_line(r["text"], headers)]
        hc = sum(han_units(r["text"]) for r in h)
        ac = sum(syllables(r["text"]) for r in a)
        if not hc or not ac:
            continue
        diff = abs(hc - ac) / max(hc, ac)
        if h and han_units(h[0]["text"]) <= 12 and hc > han_units(h[0]["text"]):
            # tiêu đề Hán không số nằm trong lớp han, tiêu đề âm đã sang metadata: thử bỏ dòng Hán đầu
            hc2 = hc - han_units(h[0]["text"])
            diff2 = abs(hc2 - ac) / max(hc2, ac)
            if diff2 < diff:
                h, hc, diff = h[1:], hc2, diff2
        verse = None
        if h and len(h) == len(a) and hc / len(h) <= 8:
            same = sum(1 for x, y in zip(h, a) if han_units(x["text"]) == syllables(y["text"]))
            verse = same / len(h)
        docs.append({"document_id": doc, "han_chars": hc, "am_syllables": ac, "diff": round(diff, 3),
                     "verse_line_match": None if verse is None else round(verse, 3),
                     "pages": sorted({r["page"] for r in han_d[doc] + am_d[doc]}),
                     "example": (h[0]["text"][:30] if h else "", a[0]["text"][:50] if a else "")})
    n = len(docs) or 1
    exact = sum(d["diff"] == 0 for d in docs)
    near = sum(0 < d["diff"] <= 0.10 for d in docs)
    far = [d for d in docs if d["diff"] > 0.10]
    verse = [d for d in docs if d["verse_line_match"] is not None]
    return {"docs": len(docs), "exact": exact, "within_10pct": near, "off": len(far),
            "exact_share": round(exact / n, 3), "within_10pct_share": round((exact + near) / n, 3),
            "verse_docs": len(verse),
            "verse_all_lines_match": sum(d["verse_line_match"] == 1 for d in verse),
            "worst": sorted(far, key=lambda d: -d["diff"])[:20], "per_doc": {d["document_id"]: d for d in docs}}


def poem_form(counts: list[int]) -> str:
    if not counts:
        return "empty"
    if all(c == 7 for c in counts):
        return "that_ngon"
    if all(c == 5 for c in counts):
        return "ngu_ngon"
    if len(counts) >= 2 and all(c == (6 if i % 2 == 0 else 8) for i, c in enumerate(counts)):
        return "luc_bat"
    if len(counts) >= 4 and all(c == (7, 7, 6, 8)[i % 4] for i, c in enumerate(counts)):
        return "song_that_luc_bat"
    return "mixed"


def tho_lines(tho_rows: list[dict], headers=None) -> dict:
    """Số âm tiết mỗi dòng thơ dịch; dòng ngoài {4..8} hoặc lệch thể của tài liệu là nghi lẫn/dính dòng."""
    dist = Counter()
    forms = Counter()
    odd = []
    for doc, rows in by_doc(tho_rows).items():
        body = [r for r in rows if is_body_line(r["text"], headers)]
        counts = [syllables(r["text"]) for r in body]
        dist.update(counts)
        form = poem_form(counts)
        forms[form] += 1
        expected = {"that_ngon": {7}, "ngu_ngon": {5}, "luc_bat": {6, 8}, "song_that_luc_bat": {6, 7, 8}}.get(form)
        for r, c in zip(body, counts):
            if c < 4 or c > 8 or (expected and c not in expected):
                odd.append({"document_id": doc, "id": r["id"], "page": r["page"], "syllables": c,
                            "text": r["text"][:70]})
    return {"syllable_dist": dict(sorted(dist.items())), "forms": dict(forms), "odd_lines": len(odd),
            "odd_examples": odd[:25]}


def nghia_check(han_rows, nghia_rows, headings) -> dict:
    han_d, ng_d = by_doc(han_rows), by_doc(nghia_rows)
    started = {h["document_id"] for h in headings if h["layer"] == "nghia" and h["kind"] == "start"}
    empty = sorted(d for d in started if d not in ng_d)
    ratios = {}
    for doc in set(han_d) & set(ng_d):
        hc = sum(han_units(r["text"]) for r in han_d[doc])
        if hc:
            ratios[doc] = sum(syllables(r["text"]) for r in ng_d[doc]) / hc
    out = {"empty_with_heading": empty, "ratio_median": None, "outliers": []}
    if len(ratios) >= 4:
        vals = sorted(ratios.values())
        q1, q3 = vals[len(vals) // 4], vals[(3 * len(vals)) // 4]
        lo, hi = q1 - 1.5 * (q3 - q1), q3 + 1.5 * (q3 - q1)
        out["ratio_median"] = round(statistics.median(vals), 2)
        out["ratio_iqr"] = [round(q1, 2), round(q3, 2)]
        out["outliers"] = sorted(({"document_id": d, "ratio": round(r, 2)} for d, r in ratios.items()
                                  if r < lo or r > hi), key=lambda x: x["ratio"])
    return out


# ----------------------------------------------------------------------------- 2. tài liệu
def layer_sequence(doc_ids: list[str], data: dict) -> dict[str, list[str]]:
    """Chuỗi lớp theo thứ tự đọc (trộn luồng) của mỗi tài liệu."""
    seq: dict[str, list[tuple[int, str]]] = defaultdict(list)
    for layer, rows in data["layers"].items():
        for r in rows:
            idx = min((data["order"].get(s, 10 ** 9) for s in r["source_ids"]), default=10 ** 9)
            seq[r["document_id"]].append((idx, layer))
    return {d: [layer for _, layer in sorted(seq.get(d, []))] for d in doc_ids}


def runs(seq: list[str]) -> list[str]:
    out = []
    for x in seq:
        if not out or out[-1] != x:
            out.append(x)
    return out


def document_checks(data: dict, optional: set[str]) -> dict:
    docs = data["documents"]
    ids = [d["document_id"] for d in docs]
    counts = {d["document_id"]: d["layers"] for d in docs}
    sizes = {d: sum(c.values()) for d, c in counts.items()}
    med = statistics.median(sizes.values()) if sizes else 0
    seqs = layer_sequence(ids, data)
    missing, order, repeated = [], [], []
    for d in docs:
        c, doc = d["layers"], d["document_id"]
        has = {k for k, v in c.items() if v}
        if "han" in has and "am" not in has:
            missing.append({"document_id": doc, "problem": "han_without_am", "pages": d["pages"]})
        if "am" in has and "han" not in has:
            missing.append({"document_id": doc, "problem": "am_without_han", "pages": d["pages"]})
        for layer in ("nghia", "tho"):
            if layer not in has and "han" in has:
                missing.append({"document_id": doc, "problem": f"no_{layer}" + ("_optional" if layer in optional
                                                                                   else ""), "pages": d["pages"]})
        r = runs(seqs.get(doc, []))
        present = [x for x in LAYER_ORDER if x in r]
        if r and [x for x in r if x in present][:1] != present[:1]:
            order.append({"document_id": doc, "runs": r, "pages": d["pages"]})
        elif [x for x in dict.fromkeys(r)] != present:
            order.append({"document_id": doc, "runs": r, "pages": d["pages"]})
        dup = [x for x, n in Counter(r).items() if n > 1]
        if dup:
            repeated.append({"document_id": doc, "layers": dup, "runs": r, "pages": d["pages"]})
    short = [{"document_id": d, "lines": n} for d, n in sizes.items() if n <= DOC_SHORT_LINES]
    long_ = [{"document_id": d, "lines": n} for d, n in sizes.items() if med and n > DOC_LONG_FACTOR * med]
    pages = [len(d["pages"]) for d in docs]
    return {"count": len(docs), "lines_median": med, "lines_max": max(sizes.values(), default=0),
            "pages_median": statistics.median(pages) if pages else 0, "pages_max": max(pages, default=0),
            "short": short, "long": long_, "missing_layers": missing,
            "missing_counts": dict(Counter(m["problem"] for m in missing)),
            "order_anomalies": order, "repeated_layers": repeated}


# ----------------------------------------------------------------------------- 2. mục lục
def parse_toc(lines: dict[str, dict]) -> tuple[list[int], list[dict]]:
    """Trang mục lục: trang có dòng "MỤC LỤC" và >= 3 dòng kết thúc bằng số trang; lấy (tiêu đề, trang in)."""
    by_page: dict[int, list[str]] = defaultdict(list)
    for sid, ln in lines.items():
        if ln["stream"] == "vie":
            by_page[ln["page"]].append(ln["text"])
    toc_pages = []
    for page, texts in sorted(by_page.items()):
        entries = sum(1 for t in texts if TOC_ENTRY_RE.match(t.strip()))
        if (any(TOC_TITLE_RE.search(t) for t in texts) and entries >= 3) or \
                (toc_pages and page == toc_pages[-1] + 1 and entries >= 0.5 * max(len(texts), 1)):
            toc_pages.append(page)
    entries = []
    for page in toc_pages:
        for t in by_page[page]:
            m = TOC_ENTRY_RE.match(t.strip())
            if m and len(fold(m["title"])) >= 4 and not TOC_TITLE_RE.search(t):
                entries.append({"title": m["title"].strip(" .…"), "printed": int(m["page"]), "toc_page": page})
    return toc_pages, entries


def doc_titles(data: dict) -> dict[str, list[str]]:
    """Các chuỗi có thể là tiêu đề của tài liệu: metadata title, tiêu đề VIẾT HOA đầu lớp am/nghia."""
    out: dict[str, list[str]] = defaultdict(list)
    for doc in data["metadata"]:
        for occs in doc["metadata"].get("title", {}).values():
            out[doc["document_id"]] += [o["text"] for o in occs]
    for layer in ("am", "nghia"):
        for doc, rows in by_doc(data["layers"][layer]).items():
            out[doc] += [r["text"] for r in rows[:3] if is_upper_title(r["text"])]
    return out


def match_toc(entries: list[dict], docs: list[dict], titles: dict[str, list[str]], covered: set[int]) -> dict:
    if not entries:
        return {"entries": 0}
    start = {d["document_id"]: min(d["pages"]) for d in docs if d["pages"]}
    folded = {d: [fold(t) for t in ts if fold(t)] for d, ts in titles.items()}

    def best(entry):
        e = fold(entry["title"])
        scored = []
        for d, ts in folded.items():
            s = max((SequenceMatcher(None, e, t).ratio() if not (e in t or t in e) else 1.0 for t in ts), default=0)
            scored.append((s, d))
        return max(scored, default=(0, None))

    matched = []
    for e in entries:
        s, d = best(e)
        matched.append((e, d if s >= 0.75 else None, s))
    offsets = Counter(start[d] - e["printed"] for e, d, _ in matched if d in start)
    offset = offsets.most_common(1)[0][0] if offsets else None
    results, doc_hits = [], defaultdict(list)
    for e, d, s in matched:
        expected = e["printed"] + offset if offset is not None else None
        if d is None and expected is not None:
            near = [x for x, p in start.items() if abs(p - expected) <= 1]
            d = near[0] if len(near) == 1 else None
        in_scope = expected is None or expected in covered
        status = "found" if d else ("missing" if in_scope else "out_of_scope")
        if d:
            doc_hits[d].append(e["title"])
        results.append({**e, "expected_pdf_page": expected, "document_id": d, "score": round(s, 2),
                        "status": status})
    merged = {d: t for d, t in doc_hits.items() if len(t) > 1}
    hit_docs = set(doc_hits)
    extra = [d for d in start if d not in hit_docs]
    split = []
    found = sorted((r["expected_pdf_page"], r["document_id"]) for r in results if r["status"] == "found"
                   and r["expected_pdf_page"] is not None)
    for (p0, _), (p1, _) in zip(found, found[1:]):
        inside = [d for d, p in start.items() if p0 < p < p1]
        if len(inside) > 1:
            split.append({"between_pages": [p0, p1], "documents": inside})
    return {"entries": len(entries), "offset": offset, "found": sum(r["status"] == "found" for r in results),
            "missing": [r for r in results if r["status"] == "missing"],
            "out_of_scope": sum(r["status"] == "out_of_scope" for r in results),
            "merged": merged, "extra_documents": len(extra), "extra_examples": extra[:20],
            "possibly_split": split[:20]}


# ----------------------------------------------------------------------------- 4. câu
def sentence_stats(sentences: dict[str, list[dict]], structural: dict[str, list[dict]]) -> dict:
    from src.cleaning.model import RawLine
    from src.segmentation.sentences import choose_am_split, choose_han_split
    out: dict = {"layers": {}, "outliers": {}}
    for layer, rows in sentences.items():
        lens = [han_units(r["text"]) if layer == "han" else syllables(r["text"]) for r in rows]
        if not lens:
            continue
        q = statistics.quantiles(lens, n=20) if len(lens) >= 20 else [min(lens), max(lens)]
        out["layers"][layer] = {"count": len(lens), "median": statistics.median(lens), "p95": q[-1],
                                "max": max(lens)}
        odd = []
        med = statistics.median(lens)
        for r, n in zip(rows, lens):
            t = r["text"]
            why = None
            if layer == "han" and n > max(40, 3 * med):
                why = "han_very_long"
            elif layer == "han" and n == 1:
                why = "han_one_char"
            elif layer != "han" and n <= 2 and not is_upper_title(t):
                why = "vie_very_short"
            elif layer != "han" and ABBREV_END_RE.search(t):
                why = "ends_with_abbreviation"
            elif layer != "han" and (t.count('"') % 2 or t.count("“") != t.count("”")):
                why = "unbalanced_quotes"
            if why:
                odd.append({"id": r["id"], "document_id": r["document_id"], "pages": r["pages"], "why": why,
                            "len": n, "text": t[:80]})
        out["outliers"][layer] = {"counts": dict(Counter(o["why"] for o in odd)), "examples": odd[:12]}
    # chế độ tách han/am theo tài liệu (dựng lại bằng chính hàm của src.segmentation.sentences)
    modes: dict[str, dict[str, str]] = {"han": {}, "am": {}}
    for layer, chooser in (("han", choose_han_split), ("am", choose_am_split)):
        for doc, rows in by_doc(structural.get(layer, [])).items():
            modes[layer][doc] = chooser([RawLine(r["id"], int(r["page"]), r.get("text", "")) for r in rows])
    hc = Counter(r["document_id"] for r in sentences.get("han", []))
    ac = Counter(r["document_id"] for r in sentences.get("am", []))
    mism = []
    for doc in sorted(set(hc) & set(ac)):
        h, a = hc[doc], ac[doc]
        if abs(h - a) / max(h, a) > MISMATCH:
            mh, ma = modes["han"].get(doc), modes["am"].get(doc)
            ratio = max(h, a) / min(h, a)
            cause = ("mode_mismatch" if mh and ma and mh != ma else
                     "pair_vs_line_2to1" if 1.7 <= ratio <= 2.3 else "content_mismatch")
            mism.append({"document_id": doc, "han": h, "am": a, "han_mode": mh, "am_mode": ma, "cause": cause})
    out["modes"] = {layer: dict(Counter(m.values())) for layer, m in modes.items()}
    out["han_am_count_mismatch"] = {"docs_both": len(set(hc) & set(ac)), "count": len(mism),
                                    "causes": dict(Counter(m["cause"] for m in mism)), "examples": mism[:25]}
    cross_doc, cross_page = [], []
    for layer, rows in sentences.items():
        for r in rows:
            docs = {s.rsplit(".", 2)[0] for s in r["segment_ids"]}
            if len(docs) > 1:
                cross_doc.append({"id": r["id"], "docs": sorted(docs)})
            if len(r["pages"]) > 1:
                cross_page.append({"id": r["id"], "layer": layer, "pages": r["pages"], "text": r["text"][:80]})
    out["cross_document"] = {"count": len(cross_doc), "examples": cross_doc[:10]}
    out["cross_page"] = {"count": len(cross_page), "sample": random.Random(20261126).sample(
        cross_page, min(8, len(cross_page)))}
    out["_per_doc_counts"] = {"han": dict(hc), "am": dict(ac)}
    out["_mismatch_docs"] = {m["document_id"] for m in mism}
    return out


def bead_rates(beads: list[dict]) -> dict[str, dict]:
    per: dict[str, list] = defaultdict(list)
    for b in beads:
        per[b["document_id"]].append(b["kind"] not in ("0-1", "1-0") and b["score"] >= BEAD_MIN_SCORE)
    return {d: {"beads": len(v), "valid": sum(v), "rate": round(sum(v) / len(v), 3)} for d, v in per.items()}


# ----------------------------------------------------------------------------- xếp hạng + mẫu
def rank_suspects(docs: list[dict], doc_checks: dict, han_am: dict, sents: dict, beads: dict[str, dict],
                  leak_by_doc: Counter, top: int) -> list[dict]:
    missing = defaultdict(list)
    for m in doc_checks["missing_layers"]:
        if not m["problem"].endswith("_optional") and m["problem"] in ("han_without_am", "am_without_han"):
            missing[m["document_id"]].append(m["problem"])
    order = {o["document_id"] for o in doc_checks["order_anomalies"]} | \
        {o["document_id"] for o in doc_checks["repeated_layers"]}
    out = []
    for d in docs:
        doc = d["document_id"]
        score, why = 0.0, []
        if missing.get(doc):
            score += 3; why += missing[doc]
        if doc in order:
            score += 2; why.append("layer_order")
        ha = han_am["per_doc"].get(doc)
        if ha and ha["diff"] > 0.10:
            score += min(3.0, 6 * ha["diff"]); why.append(f"han_am_diff={ha['diff']}")
        if doc in sents.get("_mismatch_docs", set()):
            score += 1; why.append("sentence_count")
        b = beads.get(doc)
        if b and b["beads"] >= 3:
            score += 3 * (1 - b["rate"]); why.append(f"bead_rate={b['rate']}")
        if leak_by_doc.get(doc):
            score += min(2.0, 0.25 * leak_by_doc[doc]); why.append(f"leak={leak_by_doc[doc]}")
        if score:
            out.append({"document_id": doc, "score": round(score, 2), "why": why, "pages": d["pages"]})
    return sorted(out, key=lambda x: -x["score"])[:top]


def leak_per_doc(layer_rows: dict[str, list[dict]], headers) -> Counter:
    c = Counter()
    for layer, rows in layer_rows.items():
        for r in rows:
            t = r["text"]
            bad = (has_latin_outside_mask(t) if layer == "han" else
                   bool(CJK_IDEOGRAPH_RE.search(t) or NOTE_ENTRY_RE.match(t) or INLINE_NOTE_DIGIT_RE.search(t)))
            if bad or headers(t) or PAGE_NUMBER_RE.match(t):
                c[r["document_id"]] += 1
    return c


def stratified_sample(docs: list[dict], suspects: list[dict], n: int, seed: int) -> dict:
    sus = [s["document_id"] for s in suspects[:n]]
    pool = [d["document_id"] for d in docs if d["document_id"] not in sus]
    rnd = random.Random(seed).sample(pool, min(n, len(pool)))
    pages = {d["document_id"]: d["pages"] for d in docs}
    return {"seed": seed, "random": [{"document_id": d, "pages": pages[d]} for d in sorted(rnd)],
            "suspect": [{"document_id": d, "pages": pages[d]} for d in sus]}


# ----------------------------------------------------------------------------- main
def audit(layout: DataLayout, cfg: dict | None, top: int = 20, sample: int = 5, seed: int = 20261126) -> dict:
    cfg = cfg or {}
    pages = parse_pages(cfg.get("common", {}).get("pages")) if cfg.get("common", {}).get("pages") else None
    data = load_work(layout, pages)
    optional = {k for k, v in cfg.get("segmentation", {}).get("layers", {}).items() if v.get("optional")}
    translators = cfg.get("clean", {}).get("common", {}).get("translator_names", [])
    merged_lines = [type("L", (), {"text": v["text"], "page": v["page"]}) for v in data["lines"].values()]
    headers = streams.header_matcher(streams.running_headers(merged_lines))

    dest = build_destinations(data)
    acc = account_lines(data["lines"], dest)
    docs_chk = document_checks(data, optional)
    toc_pages, toc_entries = parse_toc(data["lines"])
    covered = {ln["page"] for ln in data["lines"].values()}
    toc = match_toc(toc_entries, data["documents"], doc_titles(data), covered)
    toc["toc_pages"] = toc_pages
    leak = leakage(data["layers"], headers=headers, translator_names=translators)
    ha = han_am_consistency(data["layers"]["han"], data["layers"]["am"], headers)
    ha_struct = han_am_consistency(data["structural"]["han"], data["structural"]["am"], headers)
    tho = tho_lines(data["layers"]["tho"], headers)
    leak_struct = leakage({k: [{"id": r["id"], "page": int(r["page"]), "text": r.get("text", ""),
                                "document_id": r.get("document_id", "")} for r in v]
                           for k, v in data["structural"].items()}, headers=headers, translator_names=translators)
    ng = nghia_check(data["layers"]["han"], data["layers"]["nghia"], data["headings"])
    sents = sentence_stats(data["sentences"], data["structural"])
    beads = bead_rates(data["beads"])
    suspects = rank_suspects(data["documents"], docs_chk, ha, sents, beads,
                             leak_per_doc(data["layers"], headers), top)
    valid_rates = [b["rate"] for b in beads.values()]
    report = {
        "work_id": layout.work_id,
        "coverage": {"pages": len(covered), "page_min": min(covered, default=None),
                     "page_max": max(covered, default=None), "config_pages": cfg.get("common", {}).get("pages"),
                     "han_lines": sum(1 for v in data["lines"].values() if v["stream"] == "han"),
                     "vie_lines": sum(1 for v in data["lines"].values() if v["stream"] == "vie")},
        "documents": docs_chk, "toc": toc, "accounting": {k: v for k, v in acc.items() if k != "lost_ids"},
        "leakage": leak, "leakage_after_structural_clean": leak_struct["counts"],
        "han_am": {k: v for k, v in ha.items() if k != "per_doc"},
        "han_am_after_structural_clean": {k: v for k, v in ha_struct.items() if k not in ("per_doc", "worst")},
        "tho": tho, "nghia": ng,
        "sentences": {k: v for k, v in sents.items() if not k.startswith("_")},
        "beads": {"docs": len(beads), "valid_beads": sum(b["valid"] for b in beads.values()),
                  "total_beads": sum(b["beads"] for b in beads.values()),
                  "doc_rate_median": statistics.median(valid_rates) if valid_rates else None},
        "suspects": suspects, "sample": stratified_sample(data["documents"], suspects, sample, seed),
    }
    return report


def summary(r: dict) -> str:
    d, a, h, s = r["documents"], r["accounting"], r["han_am"], r["sentences"]
    lk = {layer: sum(c.values()) for layer, c in r["leakage"]["counts"].items()}
    lines = [
        f"== {r['work_id']}: {r['coverage']['pages']} trang, han {r['coverage']['han_lines']} / vie "
        f"{r['coverage']['vie_lines']} dòng",
        f"   tài liệu {d['count']} (trung vị {d['lines_median']} dòng), ngắn {len(d['short'])}, dài {len(d['long'])}, "
        f"thiếu lớp {d['missing_counts']}, thứ tự lạ {len(d['order_anomalies'])}, lớp lặp {len(d['repeated_layers'])}",
        f"   mục lục: {r['toc'].get('entries', 0)} mục, khớp {r['toc'].get('found', 0)}, thiếu "
        f"{len(r['toc'].get('missing', []))}, độ lệch trang {r['toc'].get('offset')}",
        f"   kế toán dòng: {a['kinds']} mất {a['lost']} {a['lost_by_stream']} hai nơi {a['double']}",
        f"   rò rỉ (04 layers): {lk}; sau clean cấu trúc: "
        f"{ {k: sum(c.values()) for k, c in r['leakage_after_structural_clean'].items()} }",
        f"   Hán-âm: {h['docs']} tài liệu, khớp {h['exact']} ({h['exact_share']}), ±10% {h['within_10pct_share']}, "
        f"lệch {h['off']}; thơ khớp mọi dòng {h['verse_all_lines_match']}/{h['verse_docs']}; sau clean: khớp "
        f"{r['han_am_after_structural_clean']['exact_share']}, ±10% {r['han_am_after_structural_clean']['within_10pct_share']}",
        f"   thơ dịch: {r['tho']['forms']} dòng lạ {r['tho']['odd_lines']}; nghĩa rỗng có tiêu đề "
        f"{len(r['nghia']['empty_with_heading'])}, tỉ lệ ngoại lai {len(r['nghia']['outliers'])}",
        f"   câu: {s['layers']} chế độ {s['modes']} lệch số câu {s['han_am_count_mismatch']['count']} "
        f"{s['han_am_count_mismatch']['causes']} vượt tài liệu {s['cross_document']['count']}",
        f"   bead han-am hợp lệ {r['beads']['valid_beads']}/{r['beads']['total_beads']} "
        f"(trung vị theo tài liệu {r['beads']['doc_rate_median']})",
    ]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--work-id", required=True)
    p.add_argument("--data-root", default="data")
    p.add_argument("--config", help="Mặc định configs/<work-id>.json nếu có (phạm vi trang, lớp optional)")
    p.add_argument("--json", help="Ghi báo cáo đầy đủ ra file JSON")
    p.add_argument("--top", type=int, default=20)
    p.add_argument("--sample", type=int, default=5)
    p.add_argument("--seed", type=int, default=20261126)
    args = p.parse_args(argv)
    cfg_path = Path(args.config) if args.config else Path("configs") / f"{args.work_id}.json"
    cfg = json.loads(cfg_path.read_text(encoding="utf-8")) if cfg_path.exists() else {}
    layout = DataLayout(Path(args.data_root), args.work_id)
    report = audit(layout, cfg, args.top, args.sample, args.seed)
    print(summary(report))
    if args.json:
        Path(args.json).write_text(json.dumps(report, ensure_ascii=False, indent=1, default=list), encoding="utf-8")
        print(f"   -> {args.json}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
