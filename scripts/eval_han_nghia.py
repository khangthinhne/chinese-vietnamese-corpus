"""So sánh các lần dóng han-nghia (nhiều cấu hình) và, nếu có, chấm theo bộ chuẩn dóng tay.

    python scripts/eval_han_nghia.py --work-id CPO_18,CPO_19,CPO_20 --data-root <bản sao> \
        --run labse=<...>/{work}/06_alignment/han-nghia.labse.xml --run bge=<...>.xml --run bge_bridge=<...>.xml \
        [--gold --tune] \
        [--review 30 --prefer bge_bridge --review-out review.tsv] [--json out.json]

Không có chuẩn: độ đồng thuận giữa từng cặp cấu hình, theo tài liệu:
    bead_f1   bead trùng khít (cùng tập ID Hán và ID nghĩa)
    link_f1   cặp (ID Hán, ID nghĩa) trùng (khớp lỏng)
    row_agree tỉ lệ câu Hán được gán cùng tập câu nghĩa (đúng thứ ảnh hưởng tới file nộp 4 cột)
Có chuẩn (xlsx dóng tay, sheet DONG_HANG_LAM_VIEC hoặc DONG_HANG, cột "Hán", "Dịch nghĩa", tuỳ chọn "Trang"):
chiếu bản chuẩn lên ID câu pipeline (xem build_gold), rồi chấm strict / loose / link / row_acc (xem
score_against_gold). --tune: chọn cấu hình tốt nhất theo strict F1 trên nửa tài liệu chẵn, báo trên nửa lẻ.
"""

from __future__ import annotations

import argparse
import glob
import json
import logging
import random
import re
import sys
import unicodedata
from collections import defaultdict
from difflib import SequenceMatcher
from itertools import combinations
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.alignment.serialization import read_alignments  # noqa: E402
from src.common.corpus import DataLayout  # noqa: E402
from src.common.sentences import find_alignment_file, load_sentences  # noqa: E402

log = logging.getLogger("eval_han_nghia")


def f1(a: set, b: set) -> float:
    if not a and not b:
        return 1.0
    inter = len(a & b)
    return 2 * inter / (len(a) + len(b)) if a or b else 1.0


def by_doc(beads: list[dict]) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = defaultdict(list)
    for b in beads:
        out[b.get("document_id", "")].append(b)
    return out


def bead_key(b: dict) -> tuple:
    return tuple(b["src_ids"]), tuple(b["tgt_ids"])


def links(beads: list[dict]) -> set:
    return {(s, t) for b in beads for s in b["src_ids"] for t in b["tgt_ids"]}


def row_map(beads: list[dict]) -> dict[str, tuple]:
    return {s: tuple(b["tgt_ids"]) for b in beads for s in b["src_ids"]}


def agreement(a: list[dict], b: list[dict]) -> dict:
    ra, rb = row_map(a), row_map(b)
    rows = set(ra) | set(rb)
    return {"bead_f1": f1({bead_key(x) for x in a}, {bead_key(x) for x in b}),
            "link_f1": f1(links(a), links(b)),
            "row_agree": (sum(ra.get(s) == rb.get(s) for s in rows) / len(rows)) if rows else 1.0}


def compare_runs(runs: dict[str, list[dict]]) -> dict:
    docs = sorted({b.get("document_id", "") for bs in runs.values() for b in bs})
    grouped = {name: by_doc(bs) for name, bs in runs.items()}
    out = {}
    for x, y in combinations(runs, 2):
        per_doc = {d: agreement(grouped[x].get(d, []), grouped[y].get(d, [])) for d in docs}
        mean = {k: sum(v[k] for v in per_doc.values()) / len(per_doc) for k in ("bead_f1", "link_f1", "row_agree")}
        mean["docs_identical"] = sum(v["bead_f1"] == 1.0 for v in per_doc.values())
        mean["docs"] = len(docs)
        out[f"{x}~{y}"] = {"mean": mean, "worst": sorted(per_doc.items(), key=lambda kv: kv[1]["bead_f1"])[:5]}
    return out


def run_stats(beads: list[dict]) -> dict:
    kinds = defaultdict(int)
    for b in beads:
        kinds[b["kind"]] += 1
    return {"beads": len(beads), "unaligned": sum("unaligned" in b["flags"] for b in beads),
            "kinds": dict(sorted(kinds.items(), key=lambda kv: -kv[1]))}


# --------------------------------------------------------------------------- #
# Bộ chuẩn
# --------------------------------------------------------------------------- #
# File dóng tay (assignment_submission/align50/<work>/*.xlsx) đã sửa OCR và tách câu lại, nên không khớp nguyên văn
# với câu pipeline. Ta chiếu nó lên ID câu pipeline: dóng token (chữ Hán / âm tiết Việt) giữa chuỗi chuẩn và chuỗi câu
# pipeline, nối mỗi câu pipeline với các hàng chuẩn chứa phần đáng kể của nó, rồi gộp các hàng chuẩn dính chung một
# câu pipeline (ở bất kỳ phía nào) thành một bead chuẩn. Đó là bead mịn nhất biểu diễn được bằng cách tách câu hiện có.
GOLD_SHEETS = ("DONG_HANG_LAM_VIEC", "DONG_HANG")
# Bộ chuẩn trong repo: eval/gold/<work>_p<trang đầu>-<trang cuối>_<người dóng>.xlsx (chép nguyên từ
# assignment_submission/align50/, chỉ đổi tên). Đường dẫn tương đối theo gốc repo.
REPO = Path(__file__).resolve().parents[1]
DEFAULT_GOLD = str(REPO / "eval" / "gold" / "{work}_*.xlsx")


def rel_to_repo(path: Path) -> str:
    try:
        return Path(path).resolve().relative_to(REPO).as_posix()
    except ValueError:
        return Path(path).as_posix()
_HAN_CHAR = re.compile(r"[㐀-䶿一-鿿豈-﫿\U00020000-\U0003ffff]")


def gold_tokens(text: str, layer: str) -> list[str]:
    text = unicodedata.normalize("NFC", text or "")
    if layer == "han":
        return _HAN_CHAR.findall(text)
    return re.findall(r"[^\W_]+", text.lower())


def read_gold_rows(path: Path) -> list[dict]:
    """xlsx -> [{row, page, han, nghia}] theo thứ tự; bỏ hàng không có chữ Hán."""
    import openpyxl
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    ws = next((wb[n] for n in GOLD_SHEETS if n in wb.sheetnames), wb.worksheets[0])
    rows = list(ws.iter_rows(values_only=True))
    header_idx = next(k for k, r in enumerate(rows) if r and "Hán" in [str(c).strip() for c in r if c])
    header = [str(c).strip() if c else "" for c in rows[header_idx]]
    col = {name: header.index(name) for name in ("ID", "Trang", "Hán", "Dịch nghĩa") if name in header}

    def cell(r, name):
        k = col.get(name)
        return r[k] if k is not None and k < len(r) else None

    out = []
    for k, r in enumerate(rows[header_idx + 1:], header_idx + 2):
        han = str(cell(r, "Hán") or "").strip()
        if not han:
            continue
        page = str(cell(r, "Trang") or "").strip()
        out.append({"row": str(cell(r, "ID") or f"{path.stem}:{k}"), "page": int(page) if page.isdigit() else None,
                    "han": han, "nghia": str(cell(r, "Dịch nghĩa") or "").strip()})
    wb.close()
    return out


def project(texts: list[str], sents: list, layer: str, min_share: float = 0.2) -> tuple[dict, dict, list[float]]:
    """Dóng token chuẩn ↔ câu pipeline. Trả (links: câu -> {hàng: số token}, cover: câu -> tỉ lệ token khớp,
    tỉ lệ token khớp của từng hàng chuẩn). Một câu nối với một hàng khi phần khớp có >= 2 token và chiếm >= min_share
    số token khớp của câu, hoặc >= nửa số token của hàng (hàng ngắn nằm lọt trong một câu dài)."""
    g_tok, g_own, s_tok, s_own = [], [], [], []
    for k, t in enumerate(texts):
        tk = gold_tokens(t, layer)
        g_tok += tk
        g_own += [k] * len(tk)
    for s in sents:
        tk = gold_tokens(s.text, layer)
        s_tok += tk
        s_own += [s.id] * len(tk)
    votes: dict[str, dict[int, int]] = defaultdict(lambda: defaultdict(int))
    g_hit = [0] * len(texts)
    for a, b, n in SequenceMatcher(None, g_tok, s_tok, autojunk=False).get_matching_blocks():
        for i in range(n):
            votes[s_own[b + i]][g_own[a + i]] += 1
            g_hit[g_own[a + i]] += 1
    s_len = defaultdict(int)
    for sid in s_own:
        s_len[sid] += 1
    g_len = defaultdict(int)
    for k in g_own:
        g_len[k] += 1
    links, cover = {}, {}
    for sid, c in votes.items():
        total = sum(c.values())
        cover[sid] = total / s_len[sid]
        links[sid] = {k: n for k, n in c.items() if n >= 2 and (n >= min_share * total or n >= 0.5 * g_len[k])} \
            or {max(c, key=c.get): max(c.values())}
    return links, cover, [g_hit[k] / g_len[k] if g_len[k] else 1.0 for k in range(len(texts))]


def gold_region(rows: list[dict], han: list, nghia: list) -> tuple[list, list]:
    """Câu pipeline thuộc các tài liệu có câu Hán nằm trong khoảng trang của file chuẩn."""
    pages = [r["page"] for r in rows if r["page"] is not None]
    if not pages:
        return han, nghia
    lo, hi = min(pages), max(pages)
    docs = {s.document_id for s in han if any(lo <= p <= hi for p in s.pages)}
    return [s for s in han if s.document_id in docs], [s for s in nghia if s.document_id in docs]


def build_gold(rows: list[dict], han: list, nghia: list, min_cover: float = 0.3) -> dict:
    """Bead chuẩn theo ID câu pipeline: [{src_ids, tgt_ids, rows, document_id}] + thống kê chiếu."""
    han, nghia = gold_region(rows, han, nghia)
    h_links, h_cover, h_rows = project([r["han"] for r in rows], han, "han")
    n_links, n_cover, n_rows = project([r["nghia"] for r in rows], nghia, "nghia")
    parent = list(range(len(rows)))

    def find(k):
        while parent[k] != k:
            parent[k] = parent[parent[k]]
            k = parent[k]
        return k

    side = {}
    for links, cover, name in ((h_links, h_cover, "src"), (n_links, n_cover, "tgt")):
        for sid, ks in links.items():
            if cover[sid] < min_cover:
                continue
            ks = sorted(ks)
            for k in ks[1:]:
                parent[find(k)] = find(ks[0])
            side[sid] = (name, ks[0])
    comps: dict[int, dict] = {}
    for k, r in enumerate(rows):
        comps.setdefault(find(k), {"src_ids": [], "tgt_ids": [], "rows": []})["rows"].append(r["row"])
    order = {s.id: i for i, s in enumerate(han + nghia)}
    doc_of = {s.id: s.document_id for s in han + nghia}
    for sid, (name, k) in sorted(side.items(), key=lambda kv: order[kv[0]]):
        comps[find(k)][f"{name}_ids"].append(sid)
    has_nghia = {r["row"] for r in rows if gold_tokens(r["nghia"], "nghia")}
    beads, unreachable = [], []
    for c in comps.values():
        if not c["src_ids"]:
            continue    # hàng chuẩn không chiếu được lên câu Hán nào (OCR/tách lớp làm mất)
        c["document_id"] = doc_of[c["src_ids"][0]]
        if not c["tgt_ids"] and any(r in has_nghia for r in c["rows"]):
            unreachable.append(c)   # chuẩn có Dịch nghĩa nhưng lớp nghĩa pipeline không có: lỗi tách lớp, không chấm
            continue
        beads.append(c)
    beads.sort(key=lambda c: order[c["src_ids"][0]])
    lost = [r["row"] for k, r in enumerate(rows) if h_rows[k] < 0.5]
    stats = {"rows": len(rows), "beads": len(beads),
             "han_token_cover": sum(h_rows) / len(rows) if rows else 0.0,
             "nghia_token_cover": sum(n_rows[k] for k, r in enumerate(rows) if r["nghia"]) / max(1, sum(bool(r["nghia"]) for r in rows)),
             "rows_han_lost": lost, "rows_nghia_unreachable": [r for c in unreachable for r in c["rows"]],
             "han_sents": sum(len(c["src_ids"]) for c in beads),
             "nghia_sents": sum(len(c["tgt_ids"]) for c in beads),
             "kinds": {}}
    for c in beads:
        kind = f"{len(c['src_ids'])}-{len(c['tgt_ids'])}"
        stats["kinds"][kind] = stats["kinds"].get(kind, 0) + 1
    stats["kinds"] = dict(sorted(stats["kinds"].items(), key=lambda kv: -kv[1]))
    return {"beads": beads, "stats": stats}


def prf(tp_p: int, n_p: int, tp_g: int, n_g: int) -> dict:
    p = tp_p / n_p if n_p else 0.0
    r = tp_g / n_g if n_g else 0.0
    return {"precision": p, "recall": r, "f1": 2 * p * r / (p + r) if p + r else 0.0}


def score_against_gold(pred: list[dict], gold: list[dict], docs: set[str] | None = None) -> dict:
    """strict: bead trùng khít (cùng tập câu Hán và câu nghĩa); loose: bead giao nhau ở cả hai phía (hoặc cùng không
    có nghĩa); link: cặp (Hán, nghĩa); row_acc: tỉ lệ câu Hán được gán đúng tập câu nghĩa (= đúng ô Dịch nghĩa của
    file nộp). Câu Hán ngoài bộ chuẩn (tiêu đề, rác OCR) bị bỏ khỏi bead dự đoán; câu nghĩa ngoài bộ chuẩn (chú thích,
    khảo dị) vẫn giữ, nên gắn chúng vào câu Hán bị tính là sai."""
    gold = [g for g in gold if docs is None or g["document_id"] in docs]
    H = {h for g in gold for h in g["src_ids"]}
    gb = [(frozenset(g["src_ids"]), frozenset(g["tgt_ids"])) for g in gold]
    pb = []
    for b in pred:
        src = frozenset(s for s in b["src_ids"] if s in H)
        if src:
            pb.append((src, frozenset(b["tgt_ids"])))

    def loose(x, y):
        return bool(x[0] & y[0]) and (bool(x[1] & y[1]) or (not x[1] and not y[1]))

    gset, pset = set(gb), set(pb)
    pl = {(h, t) for s, ts in pb for h in s for t in ts}
    gl = {(h, t) for s, ts in gb for h in s for t in ts}
    prow = {h: ts for s, ts in pb for h in s}
    grow = {h: ts for s, ts in gb for h in s}
    return {"strict": prf(sum(x in gset for x in pb), len(pb), sum(x in pset for x in gb), len(gb)),
            "loose": prf(sum(any(loose(x, y) for y in gb) for x in pb), len(pb),
                         sum(any(loose(y, x) for x in pb) for y in gb), len(gb)),
            "link": prf(len(pl & gl), len(pl), len(pl & gl), len(gl)),
            "row_acc": sum(prow.get(h) == ts for h, ts in grow.items()) / len(grow) if grow else 0.0,
            "gold_beads": len(gb), "pred_beads": len(pb)}


# --------------------------------------------------------------------------- #
# Rà bằng mắt
# --------------------------------------------------------------------------- #
def review_sample(runs: dict[str, list[dict]], prefer: str, n: int, seed: int,
                  am_of: dict[str, str], tgt_text: dict[str, str] | None = None) -> list[dict]:
    others = {name: {bead_key(b) for b in bs} for name, bs in runs.items() if name != prefer}
    rmaps = {name: row_map(bs) for name, bs in runs.items() if name != prefer}
    if tgt_text is None:
        tgt_text = {t: b["tgt_text"] for bs in runs.values() for b in bs if len(b["tgt_ids"]) == 1 for t in b["tgt_ids"]}
    pool = runs[prefer]
    dis = [b for b in pool if any(bead_key(b) not in keys for keys in others.values())]
    agree = [b for b in pool if b not in dis]
    rng = random.Random(seed)
    pick = rng.sample(dis, min(n, len(dis)))
    if len(pick) < n:
        pick += rng.sample(agree, min(n - len(pick), len(agree)))
    rows = []
    for b in sorted(pick, key=lambda b: b["id"]):
        alt = {}
        for name, rm in rmaps.items():
            ids = []
            for s in b["src_ids"]:
                for t in rm.get(s, ()):
                    if t not in ids:
                        ids.append(t)
            alt[name] = " ".join(tgt_text.get(t, t) for t in ids)
        rows.append({"bead": b["id"], "document_id": b.get("document_id", ""), "kind": b["kind"],
                     "score": b["score"], "disagree": b in dis, "src_ids": ",".join(b["src_ids"]),
                     "han": b["src_text"], "am": " ".join(dict.fromkeys(am_of.get(s, "") for s in b["src_ids"])),
                     "nghia": b["tgt_text"], **{f"alt_{k}": v for k, v in alt.items()}, "label": "", "note": ""})
    return rows


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--work-id", required=True, help="một work hoặc danh sách cách bằng dấu phẩy (gộp số liệu)")
    p.add_argument("--data-root", default="data")
    p.add_argument("--run", action="append", required=True,
                   help="TÊN=đường_dẫn.xml (lặp lại); '{work}' trong đường dẫn được thay bằng từng work")
    p.add_argument("--gold", nargs="?", const=DEFAULT_GOLD,
                   help=f"xlsx dóng tay; '{{work}}' được thay, cho phép glob (bỏ file khoá ~$ của Excel). "
                        f"Chỉ ghi --gold: dùng {DEFAULT_GOLD}")
    p.add_argument("--tune", action="store_true", help="chọn cấu hình theo strict F1 trên tài liệu chẵn, báo trên lẻ")
    p.add_argument("--tune-among", help="danh sách TÊN cấu hình được chọn khi --tune (mặc định: mọi --run)")
    p.add_argument("--review", type=int, default=0)
    p.add_argument("--prefer")
    p.add_argument("--review-out")
    p.add_argument("--seed", type=int, default=13)
    p.add_argument("--json")
    args = p.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    works = [w.strip() for w in args.work_id.split(",") if w.strip()]
    runs: dict[str, list[dict]] = defaultdict(list)
    for item in args.run:
        name, _, path = item.partition("=")
        for w in works:
            runs[name] += list(read_alignments(Path(path.replace("{work}", w))))
    runs = dict(runs)
    report = {"work_id": args.work_id, "runs": {k: run_stats(v) for k, v in runs.items()},
              "agreement": compare_runs(runs) if len(runs) > 1 else {}}
    if args.gold:
        gold, report["gold_projection"] = [], {}
        for w in works:
            pattern = args.gold.replace("{work}", w)
            files = sorted(Path(f) for f in glob.glob(pattern) if not Path(f).name.startswith("~$")) \
                if any(ch in pattern for ch in "*?[") else [Path(pattern)]
            if not files:
                log.warning("%s: không có file chuẩn (%s)", w, pattern)
                continue
            layout = DataLayout(Path(args.data_root), w)
            han, nghia = load_sentences(layout, "han"), load_sentences(layout, "nghia")
            rows = [r for f in files for r in read_gold_rows(f)]
            g = build_gold(rows, han, nghia)
            gold += g["beads"]
            report["gold_projection"][w] = {"files": [rel_to_repo(f) for f in files], **g["stats"]}
        report["gold"] = {name: score_against_gold(bs, gold) for name, bs in runs.items()}
        if args.tune:
            gdocs = sorted({g["document_id"] for g in gold})
            dev, test = set(gdocs[0::2]), set(gdocs[1::2])
            among = args.tune_among.split(",") if args.tune_among else list(runs)
            dev_scores = {name: score_against_gold(runs[name], gold, dev) for name in among}
            best = max(among, key=lambda n: (dev_scores[n]["strict"]["f1"], dev_scores[n]["row_acc"]))
            report["tune"] = {"dev_docs": sorted(dev), "test_docs": sorted(test), "among": among,
                              "dev": dev_scores, "chosen": best,
                              "test": {name: score_against_gold(bs, gold, test) for name, bs in runs.items()}}
    if args.review:
        prefer = args.prefer or next(iter(runs))
        am_of, tgt_text = {}, {}
        for w in works:
            layout = DataLayout(Path(args.data_root), w)
            am_path = find_alignment_file(layout, "han-am")
            if am_path.exists():
                for b in read_alignments(am_path):
                    for s in b["src_ids"]:
                        am_of[s] = b["tgt_text"]
            tgt_text.update({s.id: s.text for s in load_sentences(layout, "nghia")})
        rows = review_sample(runs, prefer, args.review, args.seed, am_of, tgt_text)
        if args.review_out:
            import csv
            with open(args.review_out, "w", encoding="utf-8", newline="") as fh:
                w = csv.DictWriter(fh, fieldnames=list(rows[0]) if rows else ["bead"], delimiter="\t")
                w.writeheader()
                w.writerows(rows)
        report["review_sampled"] = len(rows)
    text = json.dumps(report, ensure_ascii=False, indent=2)
    if args.json:
        Path(args.json).write_text(text, encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
