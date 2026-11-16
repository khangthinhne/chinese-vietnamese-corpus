"""Trích xuất metadata cấp TÀI LIỆU (tiêu đề / tác giả / quy trách...) khỏi dòng chữ chính.

Chạy NGAY SAU chuẩn hoá unicode + lọc trang, TRƯỚC mọi bước làm sạch nội dung:

    front_matter / ocr_noise  ->  [section_start marker]  ->  title | author | attribution
                                                                ->  body (dòng chính)

- Chỉ dòng CHÍNH đi tiếp các bước làm sạch; metadata được giữ lại CÓ CẤU TRÚC
  (xuất <layer>.metadata.jsonl + gộp thành document_metadata.jsonl kèm source_ids/pages).
- Bản OCR gốc (02_extraction XML) không bao giờ bị sửa: trích xuất = phân loại + loại
  khỏi dòng chính, không xoá dữ liệu.
- Dòng không xếp loại được trong khối metadata -> metadata_uncertain: vẫn bị loại khỏi
  dòng chính nhưng được trình review (không bao giờ xoá lặng lẽ).
- KHÔNG cấu hình gì (không markers, không patterns) -> bước này NO-OP hoàn toàn:
  thiếu cấu hình không bao giờ dẫn tới xoá metadata phá huỷ.
- Nhiều SECT trong một FILE được xử lý ĐỘC LẬP theo document_id (tiền tố ID), metadata
  của các tài liệu không bao giờ trộn lẫn.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path

from src.common.corpus import LAYERS, DataLayout, cjk_ratio, write_json
from src.common.sentences import cleaned_dir
from src.cleaning.model import (AFTER_SECTION_END, FRONT_MATTER, METADATA_ATTRIBUTION,
                                METADATA_AUTHOR, METADATA_TITLE, METADATA_UNCERTAIN, OCR_NOISE,
                                SECTION_MARKER, MetadataConfig, RawLine)
from src.cleaning.section import compile_boundary

_HAN_BODY_PUNCT = "，。！？；：、"
_VI_NAME = r"[A-Za-zÀ-ỹĐđ]"
_MAX_TITLE_VI = 90            # dòng hoa không dấu kết câu dài hơn ngưỡng này không còn "kiểu tiêu đề"
_MAX_TITLE_HAN = 40


# --------------------------------------------------------------------------- #
# Mô hình kết quả
# --------------------------------------------------------------------------- #
@dataclass
class MetadataOccurrence:
    """Một giá trị metadata của MỘT lớp cho MỘT trường (title/author/attribution)."""
    field: str                                  # title | author | attribution
    text: str                                   # các dòng nối theo quy ước lớp (han: "", vi: " ")
    source_ids: list[str]
    pages: list[int]
    confidence: str = "high"                    # high | ambiguous (đoán theo dạng dòng)
    lines: list[RawLine] = field(default_factory=list, repr=False)

    def to_row(self) -> dict:
        return {"field": self.field, "text": self.text, "source_ids": list(self.source_ids),
                "pages": list(self.pages), "confidence": self.confidence}


@dataclass
class MetadataUncertain:
    """Dòng trong khối metadata không xếp loại chắc chắn -> review, giữ nội dung gốc."""
    text: str
    source_ids: list[str]
    pages: list[int]
    reason: str
    lines: list[RawLine] = field(default_factory=list, repr=False)

    def to_row(self) -> dict:
        return {"text": self.text, "source_ids": list(self.source_ids),
                "pages": list(self.pages), "reason": self.reason}


@dataclass
class DocumentMetadata:
    """Metadata của MỘT document (SECT) trong MỘT lớp."""
    document_id: str
    fields: list[MetadataOccurrence] = field(default_factory=list)
    uncertain: list[MetadataUncertain] = field(default_factory=list)
    excluded: list[tuple[RawLine, str]] = field(default_factory=list, repr=False)  # (dòng, lý do)
    status: str = "ok"                           # ok | no_metadata (không tìm thấy khối nào)

    def to_row(self, work_id: str, layer: str) -> dict:
        return {"work_id": work_id, "layer": layer, "document_id": self.document_id,
                "status": self.status,
                "fields": [f.to_row() for f in self.fields],
                "uncertain": [u.to_row() for u in self.uncertain]}


@dataclass
class MetadataResult:
    layer: str
    status: str = "no_op"                        # no_op (không cấu hình) | ok
    documents: list[DocumentMetadata] = field(default_factory=list)

    @property
    def excluded(self) -> list[tuple[RawLine, str]]:
        return [x for d in self.documents for x in d.excluded]

    @property
    def uncertain(self) -> list[tuple[RawLine, str]]:
        return [(ln, METADATA_UNCERTAIN) for d in self.documents for u in d.uncertain for ln in u.lines]

    def doc_row(self, work_id: str, document: DocumentMetadata) -> dict:
        return document.to_row(work_id, self.layer)


# --------------------------------------------------------------------------- #
# Heuristic phân loại dòng
# --------------------------------------------------------------------------- #
def _is_ocr_noise(text: str) -> bool:
    """Dòng rác OCR: tỉ lệ ký tự chữ/số quá thấp (toàn ký hiệu, dấu cách, trang trí)."""
    chars = [c for c in text if not c.isspace()]
    if not chars:
        return False
    meaningful = sum(1 for c in chars if c.isalnum())
    return meaningful / len(chars) < 0.55


def _vi_metadata_like(text: str) -> bool:
    """Dòng kiểu tiêu đề/tác giả của lớp Việt: CHỮ HOA, không dấu kết câu, ngắn."""
    probe = text.strip()
    if not probe or len(probe) > _MAX_TITLE_VI:
        return False
    if not re.search(_VI_NAME, probe):
        return False
    if any(c.islower() for c in probe):          # có chữ thường = dòng văn chính
        return False
    return not re.search(r"[.?!…;]$", probe)


def _vi_author_shape(text: str, cfg: MetadataConfig) -> bool:
    probe = text.strip()
    tokens = [t for t in probe.split() if re.search(_VI_NAME, t)]
    return (bool(probe) and not any(c.islower() for c in probe)
            and 1 <= len(tokens) <= cfg.author_max_tokens
            and len(probe) <= cfg.author_max_len)


def _han_metadata_like(text: str) -> bool:
    """Dòng kiểu tiêu đề/tác giả của lớp Hán: chủ yếu chữ Hán, không dấu ngắt câu."""
    probe = text.strip()
    if not probe or len(probe) > _MAX_TITLE_HAN:
        return False
    if re.search(f"[{re.escape(_HAN_BODY_PUNCT)}]", probe):
        return False
    return cjk_ratio(probe) >= 0.5


def _han_author_shape(text: str, cfg: MetadataConfig) -> bool:
    probe = text.strip()
    return (bool(probe) and len(probe) <= cfg.author_max_len
            and cjk_ratio(probe) >= 0.5
            and not re.search(f"[{re.escape(_HAN_BODY_PUNCT)}{re.escape('。！？；﹖﹕﹒')}]", probe))


def _body_start(text: str, han: bool) -> bool:
    """Dòng đầu của NỘI DUNG CHÍNH: Hán = có dấu ngắt câu; Việt = có chữ thường."""
    if han:
        return bool(re.search(f"[{re.escape(_HAN_BODY_PUNCT)}]", text))
    return any(c.islower() for c in text)


def document_id_of(line: RawLine) -> str:
    """ID dòng 'CPO_18.001.P205.S001' -> document 'CPO_18.001' (SECT)."""
    parts = line.id.rsplit(".", 2)
    return parts[0] if len(parts) == 3 else line.id


# --------------------------------------------------------------------------- #
# Trích xuất theo từng tài liệu
# --------------------------------------------------------------------------- #
def _flush_block(doc: DocumentMetadata, block: list[tuple[RawLine, bool]],
                 explicit_author: list[RawLine], attr_lines: list[RawLine],
                 cfg: MetadataConfig, han: bool) -> None:
    """Chốt khối dòng metadata thu được trước khi thân bài bắt đầu.

    block = [(dòng, là_tiêu_đề_tường minh)]. Quy tắc tách tiêu đề / tác giả: dòng CUỐI
    khối là tác giả nếu đúng 'dạng tên người' (ngắn, ít token) và khối có >= 2 dòng;
    luật author_patterns tường minh luôn thắng. Chỉ có MỘT dòng metadata không có luật
    tường minh -> phân loại mơ hồ (title confidence = ambiguous, được trình review).
    """
    pool = [ln for ln, _ in block]
    author_lines: list[RawLine] = []
    if explicit_author:
        author_lines = list(explicit_author)
    elif len(pool) >= 2 and ((_han_author_shape if han else _vi_author_shape)(pool[-1].text, cfg)):
        author_lines = [pool[-1]]
    author_ids = {id(ln) for ln in author_lines}
    title_lines = [ln for ln, _ in block if id(ln) not in author_ids]
    has_explicit_title = any(explicit for _, explicit in block)
    title_conf = "high" if (has_explicit_title or len(pool) >= 2) else "ambiguous"
    joiner = "" if han else " "

    def _emit(field: str, lns: list[RawLine], reason: str, confidence: str) -> None:
        if not lns:
            return
        doc.fields.append(MetadataOccurrence(
            field=field, text=joiner.join(ln.raw_text or ln.text for ln in lns).strip(),
            source_ids=[ln.id for ln in lns], pages=sorted({ln.page for ln in lns}),
            confidence=confidence, lines=list(lns)))
        doc.excluded.extend((ln, reason) for ln in lns)

    _emit("attribution", attr_lines, METADATA_ATTRIBUTION, "high")
    _emit("author", author_lines, METADATA_AUTHOR, "high")
    _emit("title", title_lines, METADATA_TITLE, title_conf)


def _classify_document(doc_id: str, lines: list[RawLine], cfg: MetadataConfig, han: bool,
                       start_res: list[re.Pattern], end_res: list[re.Pattern],
                       title_res: list[re.Pattern], author_res: list[re.Pattern],
                       attr_res: list[re.Pattern]) -> tuple[list[RawLine], DocumentMetadata]:
    doc = DocumentMetadata(document_id=doc_id)
    kept: list[RawLine] = []
    state = "front" if start_res else "meta"     # lớp Hán thường không có marker -> meta từ đầu
    block: list[tuple[RawLine, bool]] = []       # (dòng, là tiêu đề tường minh) chờ chốt
    explicit_author: list[RawLine] = []
    attr_lines: list[RawLine] = []

    def flush() -> None:
        _flush_block(doc, block, explicit_author, attr_lines, cfg, han)
        block.clear()
        explicit_author.clear()
        attr_lines.clear()

    for ln in lines:
        probe = (ln.raw_text or ln.text).strip()
        if state == "front":
            if any(r.search(probe) for r in start_res):
                doc.excluded.append((ln, SECTION_MARKER))
                state = "meta"
            elif _is_ocr_noise(ln.text):
                doc.excluded.append((ln, OCR_NOISE))
            else:
                doc.excluded.append((ln, FRONT_MATTER))
            continue
        if state == "after":
            doc.excluded.append((ln, AFTER_SECTION_END))
            continue
        # state meta / body
        if any(r.search(probe) for r in end_res):
            flush()
            doc.excluded.append((ln, SECTION_MARKER))
            state = "after"
            continue
        if state == "body":
            kept.append(ln)
            continue
        # ---- state "meta": phân loại khối metadata ----
        if any(r.search(probe) for r in attr_res):
            attr_lines.append(ln)
            continue
        if any(r.search(probe) for r in title_res):
            block.append((ln, True))
            continue
        if any(r.search(probe) for r in author_res):
            explicit_author.append(ln)
            continue
        if (_han_metadata_like if han else _vi_metadata_like)(ln.text):
            block.append((ln, False))
            continue
        if _body_start(ln.text, han):
            flush()
            state = "body"
            kept.append(ln)
            continue
        doc.uncertain.append(MetadataUncertain(
            text=probe, source_ids=[ln.id], pages=[ln.page],
            reason="dòng trong khối metadata không xếp loại được", lines=[ln]))
        doc.excluded.append((ln, METADATA_UNCERTAIN))
    if state == "meta":
        flush()
        if not doc.fields and not doc.uncertain:
            doc.status = "no_metadata"
    elif state == "body" and (block or explicit_author or attr_lines):
        flush()                                  # đề phòng: khối chưa chốt khi hết dòng
    return kept, doc


def extract_metadata(lines: list[RawLine], cfg: MetadataConfig, layer: str
                     ) -> tuple[list[RawLine], MetadataResult]:
    """Phân loại dòng theo tài liệu; trả về (dòng chính đi tiếp, kết quả metadata).

    cfg.exclude_from_main_text=False -> dòng metadata vẫn được nhận diện nhưng GIỮ trong
    dòng chính (chế độ tương thích / đối chiếu).
    """
    if not cfg.enabled or not (cfg.section_start or cfg.section_end or cfg.title_patterns
                               or cfg.author_patterns or cfg.attribution_patterns):
        return list(lines), MetadataResult(layer=layer)

    start_res = [compile_boundary(p, f"metadata.section_start[{i}]")
                 for i, p in enumerate(cfg.section_start)]
    end_res = [compile_boundary(p, f"metadata.section_end[{i}]")
               for i, p in enumerate(cfg.section_end)]
    title_res = [compile_boundary(p, f"metadata.title_patterns[{i}]")
                 for i, p in enumerate(cfg.title_patterns)]
    author_res = [compile_boundary(p, f"metadata.author_patterns[{i}]")
                  for i, p in enumerate(cfg.author_patterns)]
    attr_res = [compile_boundary(p, f"metadata.attribution_patterns[{i}]")
                for i, p in enumerate(cfg.attribution_patterns)]

    han = layer == "han"
    docs: list[DocumentMetadata] = []
    kept: list[RawLine] = []
    # gom theo document (SECT) nhưng GIỮ nguyên thứ tự dòng
    order: list[str] = []
    groups: dict[str, list[RawLine]] = {}
    for ln in lines:
        did = document_id_of(ln)
        if did not in groups:
            groups[did] = []
            order.append(did)
        groups[did].append(ln)
    for did in order:
        dk, doc = _classify_document(did, groups[did], cfg, han, start_res, end_res,
                                     title_res, author_res, attr_res)
        kept.extend(dk if cfg.exclude_from_main_text else groups[did])
        docs.append(doc)
    return kept, MetadataResult(layer=layer, status="ok", documents=docs)


def classify_leading_block(lines: list[RawLine], cfg: MetadataConfig, layer: str,
                           document_id: str = "") -> tuple[list[RawLine], DocumentMetadata]:
    """Dùng cho bước segmentation: dòng đã thuộc ĐÚNG một tài liệu + lớp.

    Không có ranh giới mục (section_start/end) vì segmentation đã cắt xong; chỉ phân loại
    tiêu đề / tác giả / quy trách ở đầu khối. Không cấu hình pattern -> giữ nguyên.
    """
    doc_id = document_id or (document_id_of(lines[0]) if lines else "")
    if not cfg.enabled or not (cfg.title_patterns or cfg.author_patterns or cfg.attribution_patterns):
        return list(lines), DocumentMetadata(document_id=doc_id)
    title_res = [compile_boundary(p, f"metadata.title_patterns[{i}]")
                 for i, p in enumerate(cfg.title_patterns)]
    author_res = [compile_boundary(p, f"metadata.author_patterns[{i}]")
                  for i, p in enumerate(cfg.author_patterns)]
    attr_res = [compile_boundary(p, f"metadata.attribution_patterns[{i}]")
                for i, p in enumerate(cfg.attribution_patterns)]
    kept, doc = _classify_document(doc_id, lines, cfg, layer == "han", [], [],
                                   title_res, author_res, attr_res)
    if not cfg.exclude_from_main_text:
        return list(lines), doc
    return kept, doc


# --------------------------------------------------------------------------- #
# Ghi file: <layer>.metadata.xml + gộp document_metadata.xml + file_metadata.json
# --------------------------------------------------------------------------- #
def write_layer_metadata(out_dir: Path, layer: str, rows: list[dict]) -> Path:
    """Ghi metadata per-document của lớp vừa clean (luôn ghi, kể cả rỗng,
    để bước gộp không đọc phải dữ liệu cũ của lần chạy trước)."""
    from src.segmentation.serialization import write_layer_metadata as write_xml

    out = out_dir / f"{layer}.metadata.xml"
    return write_xml(out, rows[0]["work_id"] if rows else
                     _work_id_from_out_dir(out_dir), layer, rows)


def _work_id_from_out_dir(out_dir: Path) -> str:
    return Path(out_dir).parent.name


def aggregate_document_metadata(out_dir: Path, work_id: str) -> Path:
    """Gộp các <layer>.metadata.xml đang có -> document_metadata.xml (mỗi SECT một dòng).

    Lớp chưa chạy/không có metadata không chặn lớp khác; không bao giờ trộn 2 tài liệu.
    """
    from src.segmentation.serialization import read_layer_metadata, write_document_metadata

    merged: dict[str, dict] = {}
    for path in sorted(out_dir.glob("*.metadata.xml")):
        layer = path.name[: -len(".metadata.xml")]
        if layer not in LAYERS:
            continue
        for rec in read_layer_metadata(path):
            did = rec.get("document_id", "")
            row = merged.setdefault(did, {"work_id": work_id, "document_id": did,
                                          "metadata": {}, "uncertain": {}})
            for f in rec.get("fields", []):
                slot = row["metadata"].setdefault(f["field"], {})
                slot[layer] = slot.get(layer, []) + [
                    {"text": f["text"], "source_ids": f["source_ids"], "pages": f["pages"],
                     "confidence": f.get("confidence", "high")}]
            if rec.get("uncertain"):
                row["uncertain"][layer] = row["uncertain"].get(layer, []) + rec["uncertain"]
    rows = []
    for did in sorted(merged):
        row = merged[did]
        pages: set[int] = set()
        for per_layer in row["metadata"].values():
            for occ in per_layer.values():
                for o in occ:
                    pages.update(o.get("pages", []))
        for per_layer in row["uncertain"].values():
            for u in per_layer:
                pages.update(u.get("pages", []))
        row["source_pages"] = sorted(pages)
        rows.append(row)
    out = out_dir / "document_metadata.xml"
    write_document_metadata(out, work_id, rows)
    return out


def read_file_meta(xml_path: Path) -> dict:
    """<FILE><meta> của một lớp (work-level: TITLE, LANGUAGE, SOURCE, OCR...)."""
    if not Path(xml_path).exists():
        return {}
    root = ET.parse(xml_path).getroot()
    file_el = root.find("FILE")
    meta = file_el.find("meta") if file_el is not None else None
    if meta is None:
        return {}
    return {child.tag: (child.text or "") for child in meta}


def aggregate_file_metadata(layout: DataLayout, out_dir: Path | None = None) -> Path:
    """Gộp <meta> của mọi lớp đang có XML -> 03_cleaned/file_metadata.json (bảo toàn gốc)."""
    out_dir = out_dir or cleaned_dir(layout)
    files = {}
    for name in sorted(LAYERS):
        xml_path = layout.layer_xml(name)
        if xml_path.exists():
            files[name] = read_file_meta(xml_path)
    out = out_dir / "file_metadata.json"
    write_json(out, {"work_id": layout.work_id, "files": files})
    return out
