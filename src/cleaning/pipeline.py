"""Điều phối bộ làm sạch (bước 1..10) + CLI + ghi output/audit.

Thứ tự thực thi (khác đề xuất ở 2 điểm, lý do trong docstring từng module):
    1 unicode -> 2 page_filter -> 3 section -> 4 duplicates -> 5 footnotes
    -> 6 artifacts -> 7 reconstruction -> 8 normalization -> 9 filtering
    -> 10 segmentation
- section (3) chạy TRƯỚC duplicates/footnotes: là cắt cấp cao theo config, tránh rác
  OCR chưa chọn nuốt c state machine chú thích.
- footnotes (5) chạy TRƯỚC artifacts (6): chỉ mục "(1)" vừa là marker trong dòng vừa là
  đầu một chú thích nhiều dòng; xoá marker trước sẽ mất manh mối.
"""

from __future__ import annotations

import argparse
import logging
import sys
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path

from src.common import deps
from src.common.corpus import DataLayout, MetadataStore, base_parser, parse_args, parse_pages
from src.common.sentences import Sentence, atomic_write_text, cleaned_dir, save_sentences

from src.cleaning.model import (DEFAULT_DROP, DUPLICATE_LINE, UNCERTAIN, VI_ABBREV, CleanConfig,
                                FootnoteConfig, MetadataConfig, OCRCleaningConfig, RawLine)
from src.cleaning.audit import Audit, write_audit_files
from src.cleaning.unicode import normalize_unicode
from src.cleaning.page_filter import filter_pages
from src.cleaning.section import SectionInfo, extract_section
from src.cleaning.duplicates import detect_duplicates
from src.cleaning.footnotes import detect_footnotes
from src.cleaning.artifacts import clean_lines
from src.cleaning.reconstruction import reconstruct_page_boundaries
from src.cleaning.normalization import normalize_lines
from src.cleaning.filtering import filter_invalid_lines
from src.cleaning.segmentation import make_sentences
from src.cleaning.metadata import (aggregate_document_metadata, aggregate_file_metadata,
                                   extract_metadata, write_layer_metadata)

log = logging.getLogger("clean")


# --------------------------------------------------------------------------- #
# Đọc XML thô (giữ hành vi clean.py cũ)
# --------------------------------------------------------------------------- #
def read_raw_lines(xml_path: Path) -> list[RawLine]:
    if not xml_path.exists():
        raise FileNotFoundError(f"Chưa có {xml_path}; chạy extraction/OCR cho lớp này trước")
    root = ET.parse(xml_path).getroot()
    out: list[RawLine] = []
    for page in root.iter("PAGE"):
        page_no = int(page.get("SOURCE_PAGE", "0"))
        units = [u for u in page if u.get("ID")]
        for i, u in enumerate(units):
            uid = u.get("ID", "")
            out.append(RawLine(uid, page_no, u.text or "", is_last_on_page=(i == len(units) - 1),
                               source_ids=[uid] if uid else []))
    return out


def read_segmentation_lines(path: Path) -> list[RawLine]:
    """04_segmentation/layers/<layer>.xml -> RawLine (đã cắt tài liệu/lớp ở bước trước)."""
    if not path.exists():
        raise FileNotFoundError(f"Chưa có {path}; chạy bước segment trước")
    from src.segmentation.serialization import read_layer

    return [RawLine(row["id"], int(row["page"]), row.get("text", ""),
                    raw_text=row.get("text", ""), flags=list(row.get("flags") or []),
                    source_ids=list(row.get("source_ids") or []),
                    document_id=row.get("document_id") or "")
            for row in read_layer(path)]


# --------------------------------------------------------------------------- #
# Điều phối
# --------------------------------------------------------------------------- #
@dataclass
class StructuralCleanOutput:
    kept: list[RawLine]
    dropped_rows: list[tuple[str, int, str, str]]
    stats: dict
    audit: Audit
    section: SectionInfo
    notes: list
    duplicates: list[dict]
    reconstructions: list[dict]
    pages: list[dict]
    metadata_docs: list[dict]


@dataclass
class CleanOutput:
    sentences: list[Sentence]
    dropped_rows: list[tuple[str, int, str, str]]
    stats: dict
    audit: Audit
    section: SectionInfo
    notes: list
    duplicates: list[dict]
    reconstructions: list[dict]
    pages: list[dict]
    metadata_docs: list[dict]


def run_structural_cleaning(work_id: str, layer: str, raw_lines: list[RawLine], cfg: CleanConfig) -> StructuralCleanOutput:
    """Làm sạch cấu trúc (bước 1..9): unicode, trang, metadata, đoạn, trùng lặp,
    chú thích, nhiễu OCR, nối trang, chuẩn hoá, lọc dòng.
    
    KHÔNG tách câu ở giai đoạn này.
    """
    audit = Audit(work_id, layer)

    # 1. Unicode: giữ nguyên bản gốc trong raw_text
    lines: list[RawLine] = []
    for ln in raw_lines:
        lines.append(RawLine(ln.id, ln.page, normalize_unicode(ln.text), ln.is_last_on_page,
                             ln.raw_text or ln.text, list(ln.flags),
                             list(ln.source_ids), ln.document_id))

    # 2. Trang: loại thủ công / trắng / header-footer lặp
    lines, page_records = filter_pages(lines, cfg, audit)

    # 2.5 Metadata cấp tài liệu: tiêu đề/tác giả/quy trách + ranh giới PHIÊN ÂM/DỊCH NGHĨA/CHÚ THÍCH.
    #     Không cấu hình gì -> no-op (không xoá gì); dòng trích được KHÔNG đi vào corpus chính.
    meta_lines, meta = extract_metadata(lines, cfg.metadata, layer)
    meta_rows: list[tuple[str, int, str, str]] = []
    meta_removed = meta.excluded + meta.uncertain if cfg.metadata.exclude_from_main_text else []
    for ln, reason in meta_removed:
        audit.add("remove_line", reason, rule="metadata", line=ln)
        meta_rows.append((ln.id, ln.page, reason, ln.raw_text or ln.text))
    for ln, reason in meta.uncertain:
        audit.flag_review(ln, UNCERTAIN, rule="metadata",
                          note="dòng trong khối metadata không xếp loại chắc chắn")
    for doc in meta.documents:
        for occ in doc.fields:
            for ln in occ.lines:
                audit.add("extract_metadata", f"metadata_{occ.field}", rule=f"metadata:{occ.field}",
                          line=ln, note=occ.text)
            if occ.confidence == "ambiguous":
                for ln in occ.lines:
                    audit.flag_review(ln, UNCERTAIN, rule=f"metadata:{occ.field}",
                                      note="phân loại tiêu đề/tác giả mơ hồ theo dạng dòng")
    lines = meta_lines if cfg.metadata.exclude_from_main_text else lines

    # 3. Cắt đoạn theo ranh giới config (nếu không cấu hình -> giữ nguyên, info unbounded)
    lines, sec_info = extract_section(lines, cfg.start_after, cfg.end_before)
    for ln, reason in sec_info.excluded:
        audit.add("remove_line", f"section_{reason}", rule="boundary", line=ln)

    # 4. Trùng lặp OCR
    lines, dup_records = detect_duplicates(lines, cfg, audit)

    # 5. Chú thích / trích dẫn nguồn / mục chú thích đánh số
    fn_res = detect_footnotes(lines, cfg.footnotes, layer)
    fn_rows: list[tuple[str, int, str, str]] = []
    if fn_res.excluded_ids:
        lines = [ln for ln in lines if ln.id not in fn_res.excluded_ids]
        for fn in fn_res.notes:
            if fn.action == "exclude":
                for ln in fn.lines:
                    fn_rows.append((ln.id, ln.page, fn.category, ln.raw_text or ln.text))
                    audit.add("remove_line", fn.category, rule=f"note:{fn.note_id}", line=ln)
            elif fn.action == "review":
                for ln in fn.lines:
                    audit.flag_review(ln, "uncertain", rule=f"note:{fn.note_id}",
                                      note=f"chú thích không chắc ({fn.reason})")

    # 6. Nhiễu OCR + chỉ mục chú thích trong dòng + cờ ký tự không chắc
    lines = clean_lines(lines, cfg.ocr, audit, layer)

    # 7. Nối văn bản qua ranh giới trang
    lines, recon_records = reconstruct_page_boundaries(lines, cfg, audit, layer)

    # 8. Chuẩn hoá theo lớp
    lines = normalize_lines(lines, cfg, layer)

    # 9. Lọc dòng (heuristic cũ + ngữ cảnh)
    kept, dropped_rows = filter_invalid_lines(lines, cfg, audit)
    dup_rows = [(r["removed_id"], r["page"], DUPLICATE_LINE, r["text"]) for r in dup_records]

    dropped_all = [(ln.id, ln.page, reason, ln.raw_text or ln.text)
                   for ln, reason in meta_removed] + \
        [(ln.id, ln.page, f"section_{reason}", ln.raw_text or ln.text)
         for ln, reason in sec_info.excluded] + fn_rows + dup_rows + dropped_rows

    stats = {
        "raw_lines": len(raw_lines), "kept_lines": len(kept),
        "dropped_lines": len(dropped_all),
        "section_lines": sec_info.n_selected,
        "pages_removed": len(page_records),
        "duplicates": len(dup_records),
        "metadata_fields": {f: sum(1 for d in meta.documents for o in d.fields if o.field == f)
                            for f in ("title", "author", "attribution")
                            if any(o.field == f for d in meta.documents for o in d.fields)},
        "metadata_excluded": len(meta_removed),
        "notes": {cat: sum(1 for n in fn_res.notes if n.category == cat)
                  for cat in ("footnote", "bibliographic_note", "editorial_note", "uncertain_note")
                  if any(n.category == cat for n in fn_res.notes)},
        "reconstructions": {t: sum(1 for r in recon_records if r["type"] == t)
                            for t in {r["type"] for r in recon_records}},
        "review_flags": len(audit.reviews),
    }
    metadata_docs = [meta.doc_row(work_id, d) for d in meta.documents]
    return StructuralCleanOutput(kept, dropped_all, stats, audit, sec_info,
                                 fn_res.notes, dup_records, recon_records, page_records, metadata_docs)


def run_cleaning(work_id: str, layer: str, raw_lines: list[RawLine], cfg: CleanConfig) -> CleanOutput:
    """Toàn bộ pipeline làm sạch cấu trúc + tách câu (giữ tương thích ngược)."""
    struct = run_structural_cleaning(work_id, layer, raw_lines, cfg)

    # 10. Tách câu
    from src.segmentation.sentences import make_sentences
    from src.cleaning.sentence_cleaner import clean_sentences
    raw_sentences, dropped_sent = make_sentences(work_id, layer, struct.kept, cfg)
    sentences, dropped_clean = clean_sentences(raw_sentences, layer, cfg)

    dropped_all = list(struct.dropped_rows) + dropped_sent + dropped_clean
    stats = dict(struct.stats)
    stats.update({
        "dropped_lines": len(dropped_all),
        "sentences": len(sentences),
        "cross_page": sum("cross_page" in s.flags for s in sentences),
        "no_terminal_punct": sum("no_terminal_punct" in s.flags for s in sentences),
        "long": sum("long" in s.flags for s in sentences),
    })
    return CleanOutput(sentences, dropped_all, stats, struct.audit, struct.section,
                       struct.notes, struct.duplicates, struct.reconstructions,
                       struct.pages, struct.metadata_docs)


def clean_layer(work_id: str, layer: str, raw_lines: list[RawLine], cfg: CleanConfig):
    """Giao diện tương thích clean.py cũ: (sentences, dropped_rows, stats)."""
    out = run_cleaning(work_id, layer, raw_lines, cfg)
    return out.sentences, out.dropped_rows, out.stats


def _parse_replace(spec: str) -> tuple[str, str]:
    pat, sep, repl = spec.partition("=>")
    if not sep:
        raise ValueError(f"--ocr-replace phải dạng 'REGEX=>THAY_THẾ', nhận: {spec!r}")
    return pat, repl


def build_config(args, layer: str) -> CleanConfig:
    return CleanConfig(
        layer=layer,
        split=args.split or ("line" if layer == "tho" else "punct"),
        split_colon=args.split_colon,
        drop_patterns=list(DEFAULT_DROP) + list(args.drop_pattern or []),
        heading_heuristic=not args.no_heading_heuristic,
        strip_markers=not args.keep_markers,
        abbrev=set(VI_ABBREV) | {a.strip().lower() for a in (args.abbrev or "").split(",") if a.strip()},
        start_after=args.start_after,
        end_before=args.end_before,
        excluded_pages=parse_pages(args.exclude_pages) if args.exclude_pages else [],
        remove_headers=not args.no_headers,
        remove_footers=not args.no_footers,
        header_footer_min_pages=args.header_footer_min_pages,
        remove_duplicates=not args.no_duplicates,
        duplicate_threshold=args.dup_threshold,
        duplicate_min_len=args.dup_min_len,
        reconstruct_page_boundaries=not args.no_reconstruct,
        ocr=OCRCleaningConfig(
            remove_patterns=list(args.ocr_remove or []),
            replace_patterns=[_parse_replace(r) for r in (args.ocr_replace or [])],
        ),
        footnotes=FootnoteConfig(),
        metadata=MetadataConfig(
            enabled=not args.no_metadata,
            exclude_from_main_text=not args.metadata_keep,
            section_start=list(args.meta_section_start or []),
            section_end=list(args.meta_section_end or []),
            title_patterns=list(args.meta_title_pattern or []),
            author_patterns=list(args.meta_author_pattern or []),
            attribution_patterns=list(args.meta_attribution_pattern or []),
            author_max_tokens=args.meta_author_max_tokens,
            author_max_len=args.meta_author_max_len,
        ),
    )


SHARED_CLEANING_OUTPUTS = ("review_required.xml", "cleaning_audit.xml")


def layer_outputs(outputs: list[Path]) -> list[Path]:
    """Bỏ các file kiểm toán dùng chung giữa các lớp khỏi danh sách dùng để chặn chạy lại."""
    return [p for p in outputs if Path(p).name not in SHARED_CLEANING_OUTPUTS]


def run(args) -> Path:
    layer = args.layer
    layout = DataLayout(Path(args.data_root), args.work_id)
    out_dir = cleaned_dir(layout)
    layout.ensure(out_dir)
    structural_only = getattr(args, "structural_only", False)

    xml_path = layout.layer_xml(layer)
    seg_path = layout.segmentation_layer(layer)
    segmented = seg_path.exists()
    input_path = seg_path if segmented else xml_path
    raw = read_segmentation_lines(seg_path) if segmented else read_raw_lines(xml_path)
    inputs = [input_path]
    if getattr(args, "config", None):
        inputs.append(Path(args.config))

    cfg = build_config(args, layer)
    cfg.footnotes.enabled = not args.no_footnotes
    cfg.footnotes.policy = args.footnote_policy
    cfg.footnotes.detect_parentheses = not args.no_paren_notes
    cfg.footnotes.detect_bibliographic = not args.no_bib_notes
    cfg.footnotes.remove_in_text_markers = not args.keep_markers

    if structural_only:
        layout.ensure(layout.cleaning_structural_dir)
        struct_xml = layout.cleaning_structural(layer)
        outputs = [struct_xml, out_dir / f"{layer}.dropped.tsv",
                   out_dir / "review_required.xml", out_dir / "cleaning_audit.xml"]
        # Chỉ output RIÊNG của lớp này mới chặn chạy lại. review_required.xml / cleaning_audit.xml dùng chung
        # giữa các lớp (ghi gộp theo lớp), nên lớp chạy trước không được chặn lớp chạy sau.
        deps.check_fresh(layout, f"clean_structural:{layer}", inputs, layer_outputs(outputs), args.force,
                         f"{struct_xml} đã tồn tại; dùng --force để ghi đè")
        struct = run_structural_cleaning(args.work_id, layer, raw, cfg)
        from src.segmentation import serialization
        serialization.write_structural_layer(
            struct_xml, args.work_id, layer,
            [{"id": ln.id, "document_id": ln.document_id or ln.id.rsplit(".", 2)[0],
              "page": ln.page, "text": ln.text, "raw_text": ln.raw_text,
              "flags": ln.flags, "source_ids": list(ln.source_ids)}
             for ln in struct.kept])
        if not segmented:
            write_layer_metadata(out_dir, layer, struct.metadata_docs)
            aggregate_document_metadata(out_dir, args.work_id)
            aggregate_file_metadata(layout, out_dir)
        atomic_write_text(out_dir / f"{layer}.dropped.tsv", "id\tpage\treason\ttext\n" + "".join(
            f"{i}\t{p}\t{r}\t{t.replace(chr(9), ' ').replace(chr(10), ' ')}\n" for i, p, r, t in struct.dropped_rows))
        write_audit_files(out_dir, struct.audit, struct.notes, struct.duplicates, struct.reconstructions)
        deps.record(layout, f"clean_structural:{layer}", inputs, outputs)
        MetadataStore(layout).update(f"{layer}_structural_cleaned", {
            "input": layout.rel(input_path), "output": layout.rel(struct_xml), "stats": struct.stats,
            "section": struct.section.as_dict(),
            "pages_removed": struct.pages,
            "review_required": True})
        log.info("[%s] Làm sạch cấu trúc: %d dòng giữ lại -> %s", layer, len(struct.kept), struct_xml)
        return struct_xml

    out_xml = out_dir / f"{layer}.xml"
    outputs = [out_xml, out_dir / f"{layer}.txt", out_dir / f"{layer}.dropped.tsv",
               out_dir / "review_required.xml", out_dir / "cleaning_audit.xml"]
    deps.check_fresh(layout, f"clean:{layer}", inputs, layer_outputs(outputs), args.force,
                     f"{out_xml} đã tồn tại; dùng --force để ghi đè (hoặc lưu bản cũ: v01, v02...)")
    out = run_cleaning(args.work_id, layer, raw, cfg)
    if not out.sentences and raw:
        raise ValueError(f"Không còn câu nào sau khi làm sạch {input_path}; xem lại --drop-pattern/--start-after")

    save_sentences(layout, layer, out.sentences)
    atomic_write_text(out_dir / f"{layer}.txt", "".join(s.text + "\n" for s in out.sentences))
    if not segmented:
        write_layer_metadata(out_dir, layer, out.metadata_docs)  # <layer>.metadata.xml
        aggregate_document_metadata(out_dir, args.work_id)  # gộp mọi lớp -> document_metadata.xml
        aggregate_file_metadata(layout, out_dir)            # <FILE><meta> mọi lớp -> file_metadata.json
    atomic_write_text(out_dir / f"{layer}.dropped.tsv", "id\tpage\treason\ttext\n" + "".join(
        f"{i}\t{p}\t{r}\t{t.replace(chr(9), ' ').replace(chr(10), ' ')}\n" for i, p, r, t in out.dropped_rows))
    write_audit_files(out_dir, out.audit, out.notes, out.duplicates, out.reconstructions)
    deps.record(layout, f"clean:{layer}", inputs, outputs)
    MetadataStore(layout).update(f"{layer}_cleaned", {
        "input": layout.rel(input_path), "output": layout.rel(out_xml), "stats": out.stats,
        "parameters": {"split": cfg.split, "split_colon": cfg.split_colon,
                       "drop_patterns": cfg.drop_patterns, "heading_heuristic": cfg.heading_heuristic,
                       "strip_markers": cfg.strip_markers,
                       "excluded_pages": cfg.excluded_pages,
                       "remove_headers": cfg.remove_headers, "remove_footers": cfg.remove_footers,
                       "remove_duplicates": cfg.remove_duplicates,
                       "reconstruct_page_boundaries": cfg.reconstruct_page_boundaries,
                       "footnotes": {"enabled": cfg.footnotes.enabled, "policy": cfg.footnotes.policy},
                       "metadata": {"enabled": cfg.metadata.enabled,
                                    "exclude_from_main_text": cfg.metadata.exclude_from_main_text,
                                    "section_start": cfg.metadata.section_start,
                                    "section_end": cfg.metadata.section_end,
                                    "title_patterns": cfg.metadata.title_patterns,
                                    "author_patterns": cfg.metadata.author_patterns,
                                    "attribution_patterns": cfg.metadata.attribution_patterns}},
        "section": out.section.as_dict(),
        "pages_removed": out.pages,
        "review_required": True})
    log.info("[%s] %s", layer, out.stats)
    if out.stats["dropped_lines"]:
        log.info("[%s] Xem lại các dòng bị loại: %s", layer, out_dir / f"{layer}.dropped.tsv")
    if out.stats["review_flags"]:
        log.info("[%s] %d trường hợp không chắc: %s", layer, out.stats["review_flags"],
                 out_dir / "review_required.xml")
    return out_xml


def create_parser():
    p = base_parser("02_extraction XML -> làm sạch + tách câu -> 03_cleaned (+ audit)")
    p.add_argument("--structural-only", action="store_true",
                   help="Chỉ thực hiện làm sạch cấu trúc, không tách câu")
    p.add_argument("--split", choices=["punct", "line"], help="Mặc định: line cho tho, punct cho lớp khác")
    p.add_argument("--split-colon", action="store_true")
    p.add_argument("--drop-pattern", action="append", help="Regex dòng cần bỏ (lặp lại được)")
    p.add_argument("--start-after", help="Regex ranh giới đầu đoạn: lấy nội dung SAU dòng khớp (không gồm dòng khớp)")
    p.add_argument("--end-before", help="Regex ranh giới cuối đoạn: ngừng lấy trước dòng khớp (không gồm dòng khớp)")
    p.add_argument("--no-heading-heuristic", action="store_true")
    p.add_argument("--keep-markers", action="store_true")
    p.add_argument("--abbrev", help="Viết tắt Việt thêm, phân cách dấu phẩy")
    # trang / trùng / nối
    p.add_argument("--exclude-pages", help="Trang loại thủ công, vd '205,210-212'")
    p.add_argument("--no-headers", action="store_true", help="Tắt nhận diện header đầu trang lặp lại")
    p.add_argument("--no-footers", action="store_true", help="Tắt nhận diện footer cuối trang lặp lại")
    p.add_argument("--header-footer-min-pages", type=int, default=3)
    p.add_argument("--no-duplicates", action="store_true", help="Tắt phát hiện dòng OCR trùng")
    p.add_argument("--dup-threshold", type=float, default=0.9)
    p.add_argument("--dup-min-len", type=int, default=12)
    p.add_argument("--no-reconstruct", action="store_true", help="Tắt phân tích nối qua ranh giới trang")
    # chú thích
    p.add_argument("--no-footnotes", action="store_true", help="Tắt toàn bộ phát hiện chú thích")
    p.add_argument("--footnote-policy", choices=["exclude", "preserve", "review"], default="exclude")
    p.add_argument("--no-paren-notes", action="store_true", help="Tắt nhận diện chú thích ngoặc nhiều dòng")
    p.add_argument("--no-bib-notes", action="store_true", help="Tắt nhận diện trích dẫn nguồn")
    # metadata cấp tài liệu (tiêu đề / tác giả / quy trách)
    p.add_argument("--no-metadata", action="store_true",
                   help="Tắt trích xuất metadata (mặc định bật nhưng no-op khi không có luật)")
    p.add_argument("--metadata-keep", action="store_true",
                   help="Vẫn trích xuất metadata nhưng GIỮ các dòng trong corpus chính")
    p.add_argument("--meta-section-start", action="append", metavar="REGEX",
                   help="Marker mở khối metadata (vd '^PHIÊN ÂM\\s*:'); phần TRƯỚC marker = front_matter")
    p.add_argument("--meta-section-end", action="append", metavar="REGEX",
                   help="Marker đóng (vd '^CHÚ THÍCH\\s*:'); phần SAU marker bị loại khỏi corpus chính")
    p.add_argument("--meta-title-pattern", action="append", metavar="REGEX",
                   help="Regex ép phân loại dòng là TIÊU ĐỀ (lặp lại được)")
    p.add_argument("--meta-author-pattern", action="append", metavar="REGEX",
                   help="Regex ép phân loại dòng là TÁC GIẢ (lặp lại được)")
    p.add_argument("--meta-attribution-pattern", action="append", metavar="REGEX",
                   help="Regex nhận diện dòng quy trách/ghi chú tác giả (lặp lại được)")
    p.add_argument("--meta-author-max-tokens", type=int, default=4,
                   help="'Dạng tên người': số token tối đa để đoán dòng cuối khối là tác giả")
    p.add_argument("--meta-author-max-len", type=int, default=24,
                   help="'Dạng tên người': độ dài ký tự tối đa (lớp Hán nên đặt ~8)")
    # nhiễu OCR cấu hình được
    p.add_argument("--ocr-remove", action="append", help="Regex xoá (lặp lại được; tắt mặc định)")
    p.add_argument("--ocr-replace", action="append", help="'REGEX=>THAY_THẾ' (lặp lại được)")
    return p


def main(argv: list[str] | None = None) -> int:
    parser = create_parser()
    args = parse_args(parser, argv)
    try:
        run(args)
    except (OSError, ValueError) as exc:
        log.error("%s", exc)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
