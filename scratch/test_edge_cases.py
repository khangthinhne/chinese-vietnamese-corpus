"""Detailed test script for state-machine edge cases."""

import json
from src.segmentation.model import OcrLine, Document
from src.segmentation.documents import segment_documents_unified
from src.segmentation.layers import assign_document_unified
from src.common.corpus import DataLayout

# Load CPO_19 config
cfg = json.loads(open("configs/CPO_19.json", encoding="utf-8").read())
seg_cfg = cfg["segmentation"]
doc_cfg = seg_cfg["document"]
layer_cfg = seg_cfg["layers"]
meta_cfg = cfg.get("metadata_extraction", {})

def make_line(page, index, text):
    return OcrLine(page=page, index=index, text=text, bbox=(100, 100 * index, 500, 100 * index + 30),
                   confidence=95.0, source_id=f"P{page:04d}.L{index:03d}")

print("=== 1. Test 其一 and 其二 (Multiple documents on one page or across pages) ===")
lines_qi = [
    make_line(15, 1, "其一"),
    make_line(15, 2, "夫道至虚，四大理，而有法门。"),
    make_line(15, 3, "PHIÊN ÂM"),
    make_line(15, 4, "Phù Đạo chí hư, tứ đại lý thường."),
    make_line(15, 5, "DỊCH NGHĨA"),
    make_line(15, 6, "Đạo vốn rất hư không, bốn cõi thường có phép mầu."),
    make_line(15, 7, "其二"),
    make_line(15, 8, "妙群域，度脱无量。"),
    make_line(15, 9, "PHIÊN ÂM"),
    make_line(15, 10, "Diệu xuất quần vực, độ thoát vô lượng."),
    make_line(15, 11, "DỊCH NGHĨA"),
    make_line(15, 12, "Mầu khắp cõi, cứu độ khôn xiết."),
]
docs, rev = segment_documents_unified("CPO_19", lines_qi, doc_cfg, [15])
print(f"Docs found: {[d.id for d in docs]}, review count: {len(rev)}")
for d in docs:
    assigned = assign_document_unified("CPO_19", d, layer_cfg, meta_cfg)
    print(f"  Doc {d.id}: layers={list(assigned.lines.keys())}, headings={len(assigned.headings)}, review={len(assigned.review)}")

print("\n=== 2. Test One document across multiple pages ===")
lines_multi_page = [
    make_line(15, 1, "1. 其一"),
    make_line(15, 2, "夫道至虚，四大理。"),
    make_line(15, 3, "PHIÊN ÂM"),
    make_line(15, 4, "Phù Đạo chí hư."),
    make_line(16, 1, "DỊCH NGHĨA"),
    make_line(16, 2, "Đạo vốn chí hư."),
    make_line(16, 3, "DỊCH THƠ"),
    make_line(16, 4, "Đạo lớn vốn hư không."),
]
docs_mp, rev_mp = segment_documents_unified("CPO_19", lines_multi_page, doc_cfg, [15, 16])
print(f"Multi-page docs: {[d.id for d in docs_mp]}, pages: {[d.pages for d in docs_mp]}")
assigned_mp = assign_document_unified("CPO_19", docs_mp[0], layer_cfg, meta_cfg)
print(f"  Layers: {list(assigned_mp.lines.keys())}, headings: {[h['text'] for h in assigned_mp.headings]}")

print("\n=== 3. Test Numbered footnotes that resemble document headings ===")
lines_fn = [
    make_line(15, 1, "1. 其一"),
    make_line(15, 2, "夫道至虚。"),
    make_line(15, 3, "PHIÊN ÂM"),
    make_line(15, 4, "Phù Đạo chí hư (1)."),
    make_line(15, 5, "DỊCH NGHĨA"),
    make_line(15, 6, "Đạo lớn hư không."),
    make_line(15, 7, "1. Chú thích: Bốn cõi gồm đất, nước, gió, lửa."),
    make_line(15, 8, "2. Chú thích tiếp theo."),
]
docs_fn, rev_fn = segment_documents_unified("CPO_19", lines_fn, doc_cfg, [15])
print(f"Footnote test docs: {[d.id for d in docs_fn]}")
assigned_fn = assign_document_unified("CPO_19", docs_fn[0], layer_cfg, meta_cfg)
print(f"  Doc 1 layers: {list(assigned_fn.lines.keys())}, review: {[r['reason'] for r in assigned_fn.review]}")

print("\n=== 4. Test Missing PHIÊN ÂM heading ===")
lines_no_am = [
    make_line(15, 1, "其一"),
    make_line(15, 2, "夫道至虚，四大理。"),
    make_line(15, 3, "Phù Đạo chí hư, tứ đại lý thường."),  # missing PHIÊN ÂM heading!
    make_line(15, 4, "DỊCH NGHĨA"),
    make_line(15, 5, "Đạo vốn chí hư."),
]
docs_no_am, _ = segment_documents_unified("CPO_19", lines_no_am, doc_cfg, [15])
assigned_no_am = assign_document_unified("CPO_19", docs_no_am[0], layer_cfg, meta_cfg)
print(f"  Missing PHIÊN ÂM layers: {list(assigned_no_am.lines.keys())}")
print(f"  Missing PHIÊN ÂM review: {[r['reason'] for r in assigned_no_am.review]}")

print("\n=== 5. Test OCR-corrupted heading (DỊCH THO) ===")
lines_corrupt = [
    make_line(15, 1, "其一"),
    make_line(15, 2, "夫道至虚。"),
    make_line(15, 3, "PHIÊN ÂM"),
    make_line(15, 4, "Phù Đạo chí hư."),
    make_line(15, 5, "DỊCH NGHĨA"),
    make_line(15, 6, "Đạo vốn chí hư."),
    make_line(15, 7, "DỊCH THO"),  # 'O' instead of 'Ơ'
    make_line(15, 8, "Đạo lớn vốn hư không."),
]
docs_c, _ = segment_documents_unified("CPO_19", lines_corrupt, doc_cfg, [15])
assigned_c = assign_document_unified("CPO_19", docs_c[0], layer_cfg, meta_cfg)
print(f"  Corrupted DỊCH THO layers: {list(assigned_c.lines.keys())}")
print(f"  Headings recognized: {[h['text'] for h in assigned_c.headings]}")

print("\n=== 6. Test Missing optional layers (e.g. tho) ===")
lines_no_tho = [
    make_line(15, 1, "其一"),
    make_line(15, 2, "夫道至虚。"),
    make_line(15, 3, "PHIÊN ÂM"),
    make_line(15, 4, "Phù Đạo chí hư."),
    make_line(15, 5, "DỊCH NGHĨA"),
    make_line(15, 6, "Đạo vốn chí hư."),
]
docs_nt, _ = segment_documents_unified("CPO_19", lines_no_tho, doc_cfg, [15])
assigned_nt = assign_document_unified("CPO_19", docs_nt[0], layer_cfg, meta_cfg)
print(f"  No tho layers: {list(assigned_nt.lines.keys())}")
print(f"  No tho review reasons: {[r['reason'] for r in assigned_nt.review]}")

print("\n=== 7. Test Repeated headings ===")
lines_rep = [
    make_line(15, 1, "其一"),
    make_line(15, 2, "夫道至虚。"),
    make_line(15, 3, "PHIÊN ÂM"),
    make_line(15, 4, "Phù Đạo chí hư."),
    make_line(15, 5, "PHIÊN ÂM"),  # repeated heading
    make_line(15, 6, "Tứ đại lý thường."),
    make_line(15, 7, "DỊCH NGHĨA"),
    make_line(15, 8, "Đạo vốn chí hư."),
]
docs_rep, _ = segment_documents_unified("CPO_19", lines_rep, doc_cfg, [15])
assigned_rep = assign_document_unified("CPO_19", docs_rep[0], layer_cfg, meta_cfg)
print(f"  Repeated headings: {[h['text'] for h in assigned_rep.headings]}")
print(f"  Repeated am lines count: {len(assigned_rep.lines.get('am', []))}")

print("\n=== 8. Test Lines between sections ===")
lines_btw = [
    make_line(15, 1, "其一"),
    make_line(15, 2, "夫道至虚。"),
    make_line(15, 3, "PHIÊN ÂM"),
    make_line(15, 4, "Phù Đạo chí hư."),
    make_line(15, 5, "KHẢO DỊ"),  # end marker for nghia or separator
    make_line(15, 6, "Bản A chép khác bản B."),
    make_line(15, 7, "DỊCH NGHĨA"),
    make_line(15, 8, "Đạo vốn chí hư."),
]
docs_btw, _ = segment_documents_unified("CPO_19", lines_btw, doc_cfg, [15])
assigned_btw = assign_document_unified("CPO_19", docs_btw[0], layer_cfg, meta_cfg)
print(f"  Between sections layers: {list(assigned_btw.lines.keys())}")
print(f"  Between sections review: {[r['reason'] for r in assigned_btw.review]}")

print("\n=== 9. Test Empty documents and empty pages ===")
docs_empty, rev_empty = segment_documents_unified("CPO_19", [], doc_cfg, [15])
print(f"  Empty stream docs: {docs_empty}, review: {[r['reason'] for r in rev_empty]}")
