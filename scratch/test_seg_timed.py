import json, time, sys, traceback
from pathlib import Path
from src.common.corpus import DataLayout, parse_pages
from src.segmentation.documents import load_unified_stream, segment_documents_unified
from src.segmentation.layers import assign_document_unified
from src.segmentation import serialization
from src.cleaning.metadata import write_layer_metadata, aggregate_document_metadata

print("Step 1: Reading config...", flush=True)
cfg = json.loads(Path('configs/CPO_19.json').read_text(encoding='utf-8'))
pages = parse_pages(cfg['common']['pages'])
layout = DataLayout(Path('data'), 'CPO_19')
seg_dir = layout.segmentation_dir
seg = cfg.get('segmentation', {})
layer_cfg = seg.get('layers', {})
meta_cfg = cfg.get('metadata_extraction', {})

print("Step 2: Loading unified stream...", flush=True)
t0 = time.time()
lines = load_unified_stream(layout, pages)
print(f"Loaded {len(lines)} lines in {time.time() - t0:.2f}s", flush=True)

print("Step 3: Segmenting documents...", flush=True)
t0 = time.time()
docs, review = segment_documents_unified('CPO_19', lines, seg.get('document', {}), pages)
print(f"Segmented {len(docs)} docs, {len(review)} review items in {time.time() - t0:.2f}s", flush=True)

print("Step 4: Assigning layers...", flush=True)
t0 = time.time()
layer_rows = {name: [] for name in layer_cfg}
metadata_rows = {name: [] for name in layer_cfg}
headings = []
doc_rows = []

for doc in docs:
    assigned = assign_document_unified('CPO_19', doc, layer_cfg, meta_cfg)
    layer_rows.update({k: layer_rows.get(k, []) + v for k, v in assigned.lines.items()})
    headings.extend(assigned.headings)
    for row in assigned.metadata_rows:
        metadata_rows.setdefault(row["layer"], []).append(row)
    review.extend(doc.review)
    review.extend(assigned.review)
    if doc.heading:
        headings.append({"work_id": "CPO_19", "document_id": doc.id,
                         "layer": "document", "kind": "document_start", **doc.heading})
    doc_rows.append({"work_id": "CPO_19", "document_id": doc.id,
                     "pages": doc.pages, "heading": doc.heading,
                     "layers": {k: len(v) for k, v in assigned.lines.items()},
                     "status": "ok"})
print(f"Assigned layers in {time.time() - t0:.2f}s. Han lines: {len(layer_rows.get('han', []))}", flush=True)

print("Step 5: Writing XML outputs...", flush=True)
t0 = time.time()
layout.ensure(seg_dir, layout.segmentation_layers)
serialization.write_documents(seg_dir / "documents.xml", "CPO_19", doc_rows)
serialization.write_headings(seg_dir / "section_headings.xml", "CPO_19", headings)
for name in layer_cfg:
    serialization.write_layer(layout.segmentation_layer(name), "CPO_19", name,
                              [row.to_row() for row in layer_rows.get(name, [])])
    write_layer_metadata(seg_dir, name, metadata_rows.get(name, []))
aggregate_document_metadata(seg_dir, "CPO_19")
serialization.write_review(seg_dir / "review_required.xml", "CPO_19", review)
print(f"Written XML outputs in {time.time() - t0:.2f}s", flush=True)
