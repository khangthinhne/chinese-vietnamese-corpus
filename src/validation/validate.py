"""Kiểm tra tính hợp lệ (Validation) của corpus sau phân đoạn và làm sạch.

Kiểm tra:
- Ranh giới tài liệu và tính duy nhất của ID tài liệu (04_segmentation/documents.xml).
- Phân đoạn lớp / đoạn (04_segmentation/layers/<layer>.xml): tính duy nhất của ID, nguồn truy vết (source_ids).
- Phân đoạn câu (04_segmentation/sentences/<layer>.xml): ID câu ổn định, document_id khớp, truy vết segment/source.
- Làm sạch cấp câu (05_cleaning/<layer>.xml): chuẩn hoá câu, cờ hợp lệ, câu quá dài, câu qua trang.
- Tính sẵn sàng cho dóng hàng (Alignment readiness): các cặp ngôn ngữ có đủ câu theo từng tài liệu.

Đầu ra:
    05_cleaning/validation_report.json
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path

from src.common import deps
from src.common.corpus import DataLayout, LAYERS, MetadataStore, base_parser, parse_args
from src.common.sentences import atomic_write_text, find_cleaned_file, load_sentences, parse_pair
from src.segmentation import serialization as segxml

log = logging.getLogger("validate")


@dataclass
class ValidationIssue:
    severity: str    # "error" | "warning" | "info"
    scope: str       # "document" | "layer" | "sentence" | "alignment"
    target: str      # layer name, pair name, or doc_id
    message: str
    details: dict = field(default_factory=dict)


@dataclass
class ValidationReport:
    work_id: str
    valid: bool
    issues: list[ValidationIssue] = field(default_factory=list)
    summary: dict = field(default_factory=dict)

    def add(self, severity: str, scope: str, target: str, message: str, **details) -> None:
        if severity == "error":
            self.valid = False
        self.issues.append(ValidationIssue(severity, scope, target, message, details))

    def as_dict(self) -> dict:
        return {
            "work_id": self.work_id,
            "valid": self.valid,
            "summary": self.summary,
            "issues": [asdict(i) for i in self.issues],
        }


def validate_corpus(layout: DataLayout, layers: list[str] | None = None,
                    pairs: list[str] | None = None) -> ValidationReport:
    report = ValidationReport(work_id=layout.work_id, valid=True)

    # 1. Kiểm tra tài liệu (04_segmentation/documents.xml)
    docs_xml = layout.segmentation_dir / "documents.xml"
    doc_ids = set()
    if docs_xml.exists():
        docs = segxml.read_documents(docs_xml)
        if not docs:
            report.add("warning", "document", "documents.xml", "Không có tài liệu nào trong documents.xml")
        for d in docs:
            did = d["document_id"]
            if not did:
                report.add("error", "document", "document_id", "Tài liệu thiếu document_id")
            elif did in doc_ids:
                report.add("error", "document", did, f"Trùng lặp document_id: {did}")
            doc_ids.add(did)
            if not d.get("pages"):
                report.add("warning", "document", did, f"Tài liệu {did} không có danh sách trang")
    else:
        report.add("info", "document", "documents.xml", "Chưa có 04_segmentation/documents.xml (chế độ cũ hoặc chưa segment)")

    # 1.5. Thu thập OCR line IDs nếu có
    raw_ocr_line_ids: set[str] = set()
    unified_ocr_path = layout.ocr_unified_xml_path
    if unified_ocr_path.exists():
        try:
            root = segxml.read_root(unified_ocr_path)
            file_el = segxml.file_element(root)
            for page_el in file_el.findall("PAGE"):
                p_no = page_el.get("NUMBER", "")
                for line_el in page_el.findall("LINE"):
                    l_id = line_el.get("ID", "")
                    if l_id:
                        raw_ocr_line_ids.add(l_id)
                    l_no = line_el.get("ORDER", line_el.get("NO", ""))
                    if p_no and l_no:
                        raw_ocr_line_ids.add(f"P{int(p_no):04d}.L{int(l_no):03d}")
                        raw_ocr_line_ids.add(f"P{int(p_no):03d}.L{int(l_no):03d}")
        except Exception:
            pass

    for ws in ("chi_tra", "vie"):
        ocr_path = layout.ocr_raw_xml_path(ws)
        if ocr_path.exists():
            try:
                root = segxml.read_root(ocr_path)
                ocr_el = segxml.file_element(root).find("OCR")
                if ocr_el is not None:
                    for page_el in ocr_el.findall("PAGE"):
                        p_no = page_el.get("NUMBER", "")
                        for line_el in page_el.findall("LINE"):
                            l_no = line_el.get("NO", "")
                            if p_no and l_no:
                                raw_ocr_line_ids.add(f"{ws}.P{int(p_no):03d}.L{int(l_no):03d}")
                                raw_ocr_line_ids.add(f"{ws}.P{int(p_no)}.L{int(l_no):03d}")
            except Exception:
                pass

    # 2. Kiểm tra phân đoạn đoạn / lớp (04_segmentation/layers/<layer>.xml)
    target_layers = layers or ["han", "am", "nghia"]
    known_segment_ids: dict[str, set[str]] = {}
    segment_source_map: dict[str, list[str]] = {}
    for l in target_layers:
        known_segment_ids[l] = set()
        seg_file = layout.segmentation_layer(l)
        if seg_file.exists():
            segments = segxml.read_layer(seg_file)
            for seg in segments:
                sid = seg["id"]
                if sid in known_segment_ids[l]:
                    report.add("error", "layer", l, f"Trùng lặp segment ID: {sid}")
                known_segment_ids[l].add(sid)
                if not seg.get("text", "").strip():
                    report.add("error", "layer", l, f"Segment rỗng: {sid}")
                sids = seg.get("source_ids") or []
                if not sids:
                    report.add("warning", "layer", l, f"Segment thiếu source_ids: {sid}")
                else:
                    segment_source_map[sid] = sids
                    if raw_ocr_line_ids:
                        for ocr_id in sids:
                            if ocr_id not in raw_ocr_line_ids:
                                report.add("warning", "layer", l,
                                           f"Segment {sid} tham chiếu source_id không tồn tại trong OCR: {ocr_id}")

    # 3. Kiểm tra phân đoạn câu (04_segmentation/sentences/<layer>.xml)
    sentence_segment_map: dict[str, set[str]] = {}
    for l in target_layers:
        sentence_segment_map[l] = set()
        sent_file = layout.segmentation_sentence(l)
        if sent_file.exists():
            sentences = segxml.read_sentences(sent_file)
            s_ids = set()
            for s in sentences:
                sid = s["id"]
                if sid in s_ids:
                    report.add("error", "sentence", l, f"Trùng lặp sentence ID trong sentences.xml: {sid}")
                s_ids.add(sid)
                sentence_segment_map[l].add(sid)
                if not s.get("text", "").strip():
                    report.add("error", "sentence", l, f"Câu rỗng: {sid}")
                if doc_ids and s.get("document_id") and s["document_id"] not in doc_ids:
                    report.add("warning", "sentence", l, f"Câu {sid} trỏ tới document_id không tồn tại: {s['document_id']}")

                # Kiểm tra segment_ids
                seg_ids = s.get("segment_ids") or []
                if not seg_ids:
                    report.add("error", "sentence", l, f"Câu {sid} không có segment_ids")
                else:
                    for seg_ref in seg_ids:
                        if known_segment_ids.get(l) and seg_ref not in known_segment_ids[l]:
                            report.add("error", "sentence", l,
                                       f"Câu {sid} tham chiếu segment_id không tồn tại trong {l}: {seg_ref}")

                # Kiểm tra source_ids (liên kết OCR dòng gốc)
                src_ids = s.get("source_ids") or []
                flags = s.get("flags") or []
                if not src_ids:
                    if "missing_provenance" not in flags:
                        report.add("warning", "sentence", l, f"Câu {sid} thiếu source_ids (mất liên kết OCR)")
                else:
                    for src_ref in src_ids:
                        # Kiểm tra xem có bị nhầm segment ID thành OCR source ID không
                        if known_segment_ids.get(l) and src_ref in known_segment_ids[l]:
                            report.add("error", "sentence", l,
                                       f"Câu {sid} sử dụng nhầm segment ID làm source_id: {src_ref}")
                        elif raw_ocr_line_ids and src_ref not in raw_ocr_line_ids:
                            report.add("warning", "sentence", l,
                                       f"Câu {sid} tham chiếu source_id không tồn tại trong OCR: {src_ref}")

    # 4. Kiểm tra câu đã làm sạch (05_cleaning/<layer>.xml)
    cleaned_counts = {}
    for l in target_layers:
        clean_file = layout.cleaning_root / f"{l}.xml"
        if clean_file.exists():
            try:
                sents = load_sentences(layout, l)
                cleaned_counts[l] = len(sents)
                s_ids = set()
                seen_texts: dict[str, dict[str, list[str]]] = {}

                for s in sents:
                    if s.id in s_ids:
                        report.add("error", "cleaning", l, f"Trùng lặp sentence ID trong {l}.xml: {s.id}")
                    s_ids.add(s.id)
                    if not s.text.strip():
                        report.add("error", "cleaning", l, f"Câu rỗng trong {l}.xml: {s.id}")
                    if "long" in s.flags:
                        report.add("info", "cleaning", l, f"Câu dài: {s.id} ({len(s.text)} ký tự)")
                    if "cross_page" in s.flags and len(s.pages) <= 1:
                        report.add("warning", "cleaning", l, f"Câu có cờ cross_page nhưng chỉ có {len(s.pages)} trang: {s.id}")

                    # Phát hiện câu trùng lặp trong cùng tài liệu
                    doc_key = s.document_id or "<default>"
                    norm_text = re.sub(r"[\s\W_]+", "", s.text).lower()
                    if len(norm_text) >= 6:
                        seen_texts.setdefault(doc_key, {}).setdefault(norm_text, []).append(s.id)

                for doc_key, text_map in seen_texts.items():
                    for norm_t, ids in text_map.items():
                        if len(ids) > 1:
                            report.add("warning", "cleaning", l,
                                       f"Phát hiện câu trùng lặp văn bản trong tài liệu {doc_key}: các ID {', '.join(ids)}",
                                       sentence_ids=ids)

            except Exception as exc:
                report.add("error", "cleaning", l, f"Lỗi đọc file làm sạch {clean_file}: {exc}")

    # 5. Kiểm tra sẵn sàng dóng hàng (pairs)
    if pairs:
        for pair in pairs:
            try:
                src_l, tgt_l = parse_pair(pair)
            except ValueError:
                continue
            src_n = cleaned_counts.get(src_l, 0)
            tgt_n = cleaned_counts.get(tgt_l, 0)
            if src_n == 0:
                report.add("error", "alignment", pair, f"Lớp nguồn {src_l} chưa có câu đã làm sạch")
            if tgt_n == 0:
                report.add("warning", "alignment", pair, f"Lớp đích {tgt_l} rỗng hoặc chưa có câu đã làm sạch")

    errors = sum(1 for i in report.issues if i.severity == "error")
    warnings = sum(1 for i in report.issues if i.severity == "warning")
    infos = sum(1 for i in report.issues if i.severity == "info")
    report.summary = {
        "errors": errors,
        "warnings": warnings,
        "infos": infos,
        "cleaned_sentences": cleaned_counts,
        "documents": len(doc_ids),
    }
    return report


def create_parser():
    p = base_parser("Kiểm tra tính hợp lệ (Validation) của corpus sau phân đoạn/làm sạch",
                    layer_required=False)
    p.add_argument("--layers", help="Danh sách lớp kiểm tra, phân cách dấu phẩy")
    p.add_argument("--pairs", help="Danh sách cặp kiểm tra dóng hàng, phân cách dấu phẩy")
    return p


def run(args) -> Path:
    layout = DataLayout(Path(args.data_root), args.work_id)
    layers = [l.strip() for l in args.layers.split(",")] if getattr(args, "layers", None) else None
    pairs = [p.strip() for p in args.pairs.split(",")] if getattr(args, "pairs", None) else None

    if getattr(args, "config", None):
        cfg = json.loads(Path(args.config).read_text(encoding="utf-8"))
        if not layers:
            layers = list(cfg.get("segmentation", {}).get("layers", {})) or None
        if not pairs:
            pairs = list(cfg.get("pairs", {})) or None

    report = validate_corpus(layout, layers, pairs)

    out_dir = layout.cleaning_root
    layout.ensure(out_dir)
    report_path = out_dir / "validation_report.json"
    atomic_write_text(report_path, json.dumps(report.as_dict(), ensure_ascii=False, indent=2))

    MetadataStore(layout).update("validation", {
        "valid": report.valid,
        "summary": report.summary,
        "report_path": layout.rel(report_path),
        "review_required": bool(report.summary["warnings"] or report.summary["errors"]),
    })

    log.info("Validation %s: %d errors, %d warnings, %d infos -> %s",
             "PASSED" if report.valid else "FAILED",
             report.summary["errors"], report.summary["warnings"], report.summary["infos"],
             report_path)

    if not report.valid:
        for issue in report.issues:
            if issue.severity == "error":
                log.error("[%s:%s] %s", issue.scope, issue.target, issue.message)
        raise ValueError(f"Corpus {args.work_id} không vượt qua kiểm tra hợp lệ ({report.summary['errors']} lỗi)")

    return report_path


def main(argv: list[str] | None = None) -> int:
    parser = create_parser()
    args = parse_args(parser, argv)
    try:
        run(args)
    except (OSError, ValueError, FileNotFoundError) as exc:
        log.error("%s", exc)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
