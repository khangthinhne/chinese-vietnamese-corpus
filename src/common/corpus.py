"""Hợp đồng dùng chung cho pipeline dóng hàng Hán-Việt (extraction / OCR).
 
Mọi extractor (pdf_extraction, web_extraction, ocr) chỉ làm 3 việc:
    1. Đọc nguồn  ->  list[RawPage]
    2. build_document(...)  ->  Document      (ID, section, page, câu do module này sinh)
    3. finalize(...)  = validate + write_xml + MetadataStore.update
 
Cấu trúc thư mục:
    <data_root>/<WORK_ID>/01_sources/                bản nguồn gốc, KHÔNG sửa
    <data_root>/<WORK_ID>/02_extraction/<layer>.xml   output thô (han|am|nghia|tho)
    <data_root>/<WORK_ID>/02_extraction/metadata.json
    <data_root>/<WORK_ID>/02_extraction/01_page_image/page_205.png      (OCR)
    <data_root>/<WORK_ID>/02_extraction/02_ocr_raw/page_205.json        (OCR)
 
Muốn thêm lớp văn bản mới  -> thêm 1 dòng vào LAYERS.
Muốn đổi schema XML        -> chỉ sửa write_xml / build_document.
"""


from __future__ import annotations
 
import argparse
import hashlib
import json
import logging
import os
import re
import shutil
import tempfile
import unicodedata
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable
 
log = logging.getLogger("corpus")
 
 
# --------------------------------------------------------------------------- #
# 1. Registry các lớp văn bản
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class LayerSpec:
    name: str                  # tên lớp dùng trong CLI
    filename: str              # tên file output trong 02_extraction
    language: str              # <LANGUAGE>
    version: str               # <VERSION> mặc định
    unit_tag: str              # thẻ câu: STC | LINE
    id_prefix: str             # tiền tố ID câu: S | L
    ocr_lang: str              # mã ngôn ngữ OCR mặc định (tesseract)
    keep: str | None = None    # lọc chữ viết mặc định: "cjk" | "latin" | None
    script: str | None = None  # <SCRIPT> (chỉ lớp Hán)
    has_translator: bool = False
 
 
LAYERS: dict[str, LayerSpec] = {
    "han": LayerSpec("han", "han.xml", "Hán", "Original", "STC", "S", "chi_tra",
                     keep="cjk", script="Traditional Chinese"),
    "am": LayerSpec("am", "am.xml", "Hán-Việt", "Phonetic reading", "STC", "L", "vie",
                    keep="latin"),
    "nghia": LayerSpec("nghia", "nghia.xml", "Vietnamese", "Semantic translation",
                       "LINE", "L", "vie", keep="latin", has_translator=True),
    "tho": LayerSpec("tho", "tho.xml", "Vietnamese", "Poetic translation",
                     "LINE", "L", "vie", keep="latin", has_translator=True),
}
 
 
# --------------------------------------------------------------------------- #
# 2. Đường dẫn
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class DataLayout:
    root: Path
    work_id: str
 
    @property
    def work_dir(self) -> Path: return Path(self.root) / self.work_id
    @property
    def sources(self) -> Path: return self.work_dir / "01_sources"
    @property
    def extraction(self) -> Path: return self.work_dir / "02_extraction"
    @property
    def page_images(self) -> Path: return self.extraction / "01_page_image"
    @property
    def ocr_raw(self) -> Path: return self.extraction / "02_ocr_raw"
    @property
    def metadata_path(self) -> Path: return self.extraction / "metadata.json"
 
    def layer_xml(self, layer: str) -> Path:
        return self.extraction / LAYERS[layer].filename
 
    def ensure(self, *dirs: Path) -> None:
        for d in (dirs or (self.sources, self.extraction)):
            d.mkdir(parents=True, exist_ok=True)
 
    def rel(self, p: Path) -> str:
        return Path(p).resolve().relative_to(self.work_dir.resolve()).as_posix()
 
 
def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()
 
 
def register_source(layout: DataLayout, src: Path | str) -> dict:
    """Đảm bảo nguồn nằm trong 01_sources (copy nếu ở ngoài) và trả thông tin kiểm kê."""
    src = Path(src).resolve()
    if not src.exists():
        raise FileNotFoundError(src)
    layout.ensure()
    sources = layout.sources.resolve()
    if sources not in src.parents and src != sources:
        dst = sources / src.name
        if not dst.exists():
            shutil.copytree(src, dst) if src.is_dir() else shutil.copy2(src, dst)
            log.info("Đã copy nguồn vào %s", dst)
        src = dst
    info = {"path": layout.rel(src)}
    if src.is_file():
        info.update(sha256=sha256_file(src), bytes=src.stat().st_size)
    return info
 
 
# --------------------------------------------------------------------------- #
# 3. Xử lý text mức tối thiểu (làm sạch sâu thuộc bước 03_cleaned, KHÔNG làm ở đây)
# --------------------------------------------------------------------------- #
_CJK = re.compile("[\u2E80-\u2FDF\u3400-\u4DBF\u4E00-\u9FFF\uF900-\uFAFF\U00020000-\U0002FFFF]")
_CTRL = re.compile("[\u0000-\u0008\u000b-\u001f\u007f\u200b-\u200d\u2060\ufeff]")
_CJK_GAP = re.compile("(?<=[\u2E80-\u9FFF\uF900-\uFAFF])\\s+(?=[\u2E80-\u9FFF\uF900-\uFAFF])")
 
 
def normalize_line(text: str, han: bool = False) -> str:
    """NFC, bỏ ký tự điều khiển/zero-width, gộp khoảng trắng. Không đụng dấu câu/nghĩa."""
    text = unicodedata.normalize("NFC", text)
    text = _CTRL.sub("", text).replace("\t", " ")
    text = re.sub(r"\s+", " ", text).strip()
    return _CJK_GAP.sub("", text) if han else text
 
 
def cjk_ratio(text: str) -> float:
    chars = [c for c in text if not c.isspace()]
    return sum(bool(_CJK.match(c)) for c in chars) / len(chars) if chars else 0.0
 
 
def filter_lines(lines: Iterable[str], keep: str | None) -> list[str]:
    """Giữ dòng theo chữ viết: 'cjk' (dòng Hán) hoặc 'latin' (dòng Việt) — dùng khi Hán/Việt
    nằm chung một trang/file."""
    out = []
    for ln in lines:
        r = cjk_ratio(ln)
        if keep == "cjk" and r < 0.5:
            continue
        if keep == "latin" and (r > 0.1 or not any(c.isalnum() for c in ln)):
            continue
        out.append(ln)
    return out
 
 
def parse_pages(spec: str | None, total: int | None = None) -> list[int]:
    """'205-210,212' -> [205..210, 212]. spec rỗng -> 1..total."""
    if not spec:
        if total is None:
            raise ValueError("Cần --pages khi không biết tổng số trang")
        return list(range(1, total + 1))
    pages: list[int] = []
    for part in str(spec).split(","):
        a, _, b = part.strip().partition("-")
        pages.extend(range(int(a), int(b or a) + 1))
    if total is not None:
        bad = [p for p in pages if not 1 <= p <= total]
        if bad:
            raise ValueError(f"Trang ngoài phạm vi 1..{total}: {bad[:5]}")
    return sorted(set(pages))
 
 
def parse_region(spec: str | None) -> tuple[float, float, float, float] | None:
    """'0,0,0.5,1' = nửa trái trang (x0,y0,x1,y1 theo tỉ lệ 0..1)."""
    if not spec:
        return None
    x0, y0, x1, y1 = (float(v) for v in spec.split(","))
    if not (0 <= x0 < x1 <= 1 and 0 <= y0 < y1 <= 1):
        raise ValueError("--region phải dạng x0,y0,x1,y1 với 0<=x0<x1<=1, 0<=y0<y1<=1")
    return x0, y0, x1, y1
 
 
# --------------------------------------------------------------------------- #
# 4. Mô hình dữ liệu + dựng Document
# --------------------------------------------------------------------------- #
@dataclass
class RawPage:
    """Đầu ra trung gian của mọi extractor."""
    source_page: int                  # số trang PDF/ảnh, hoặc số thứ tự đoạn (web)
    lines: list[str]
    heading: str | None = None        # có giá trị -> bắt đầu SECT mới
    id_from_source: bool = True       # True: PAGE ID = số trang; False: đánh số tự động
 
 
@dataclass
class Unit:
    id: str
    text: str
 
 
@dataclass
class Page:
    id: str
    source_page: int
    units: list[Unit] = field(default_factory=list)
 
 
@dataclass
class Section:
    id: str
    name: str
    pages: list[Page] = field(default_factory=list)
 
 
@dataclass
class Document:
    work_id: str
    layer: str
    meta: dict[str, str]
    sections: list[Section] = field(default_factory=list)
 
    def units(self) -> Iterable[Unit]:
        for s in self.sections:
            for p in s.pages:
                yield from p.units
 
    @property
    def n_units(self) -> int:
        return sum(1 for _ in self.units())
 
 
def make_meta(layer: str, *, title: str = "", source: str = "", method: str = "",
              version: str | None = None, translator: str | None = None,
              status: str = "Raw extraction, not cleaned") -> dict[str, str]:
    spec = LAYERS[layer]
    meta = {"TITLE": title, "LANGUAGE": spec.language}
    if spec.script:
        meta["SCRIPT"] = spec.script
    meta["VERSION"] = version or spec.version
    if spec.has_translator:
        meta["TRANSLATOR"] = translator or ""
    meta.update(SOURCE=source, EXTRACTION_METHOD=method, STATUS=status)
    return meta
 
 
def build_document(work_id: str, layer: str, meta: dict[str, str],
                   pages: Iterable[RawPage]) -> Document:
    spec = LAYERS[layer]
    doc = Document(work_id, layer, meta)
    section: Section | None = None
    last_heading: str | None = None
    for rp in pages:
        lines = [normalize_line(x, han=(layer == "han")) for x in rp.lines]
        lines = [x for x in lines if x]
        if not lines:
            continue
        if section is None or (rp.heading and rp.heading != last_heading):
            n = len(doc.sections) + 1
            section = Section(f"{work_id}.{n:03d}", rp.heading or f"Section {n}")
            doc.sections.append(section)
        last_heading = rp.heading or last_heading
        pno = rp.source_page if rp.id_from_source else len(section.pages) + 1
        page = Page(f"{section.id}.P{pno:03d}", rp.source_page)
        page.units = [Unit(f"{page.id}.{spec.id_prefix}{i:03d}", t)
                      for i, t in enumerate(lines, 1)]
        section.pages.append(page)
    return doc
 
 
def validate_document(doc: Document) -> list[str]:
    """Trả về danh sách lỗi (rỗng = hợp lệ)."""
    errors, seen = [], set()
    if doc.n_units == 0:
        errors.append("Document rỗng: không trích được câu nào")
    for u in doc.units():
        if u.id in seen:
            errors.append(f"ID trùng: {u.id}")
        seen.add(u.id)
        if not u.text.strip():
            errors.append(f"Câu rỗng: {u.id}")
        if "\t" in u.text or "\n" in u.text:
            errors.append(f"Có tab/xuống dòng trong câu: {u.id}")
    return errors
 
 
# --------------------------------------------------------------------------- #
# 5. Ghi file (atomic) + metadata
# --------------------------------------------------------------------------- #
def _atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
 
 
def write_json(path: Path, obj) -> None:
    _atomic_write(Path(path), json.dumps(obj, ensure_ascii=False, indent=2).encode("utf-8"))
 
 
def write_xml(doc: Document, path: Path, force: bool = False) -> Path:
    path = Path(path)
    if path.exists() and not force:
        raise FileExistsError(
            f"{path} đã tồn tại. Dùng --force để ghi đè (hoặc đổi tên bản cũ: v01, v02...).")
    spec = LAYERS[doc.layer]
    root = ET.Element("root")
    f = ET.SubElement(root, "FILE", ID=doc.work_id)
    meta = ET.SubElement(f, "meta")
    for k, v in doc.meta.items():
        ET.SubElement(meta, k).text = v
    for s in doc.sections:
        se = ET.SubElement(f, "SECT", ID=s.id, NAME=s.name)
        for p in s.pages:
            pe = ET.SubElement(se, "PAGE", ID=p.id, SOURCE_PAGE=str(p.source_page))
            for u in p.units:
                ET.SubElement(pe, spec.unit_tag, ID=u.id).text = u.text
    ET.indent(root, space="    ")
    _atomic_write(path, ET.tostring(root, encoding="utf-8", xml_declaration=True))
    return path
 
 
class MetadataStore:
    """metadata.json: kiểm kê nguồn + nhật ký chạy từng lớp (phục vụ báo cáo & tái lập)."""
 
    def __init__(self, layout: DataLayout):
        self.layout = layout
 
    def load(self) -> dict:
        p = self.layout.metadata_path
        if p.exists():
            data = json.loads(p.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                raise ValueError("metadata.json phải là JSON object")
            data.setdefault("work_id", self.layout.work_id)
            data.setdefault("layers", {})
            if not isinstance(data["layers"], dict):
                raise ValueError("metadata.json: layers phải là JSON object")
            return data
        return {"work_id": self.layout.work_id, "layers": {}}
 
    def update(self, layer: str, record: dict) -> None:
        data = self.load()
        record = {**record, "run_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
        data["layers"][layer] = record
        write_json(self.layout.metadata_path, data)
 
 
def finalize(doc: Document, layout: DataLayout, source_info: dict, run_info: dict,
             force: bool) -> Path:
    """Validate -> ghi XML -> ghi metadata. Dùng chung cho cả 3 extractor."""
    errors = validate_document(doc)
    if errors:
        raise ValueError("Document không hợp lệ:\n  " + "\n  ".join(errors[:10]))
    out = write_xml(doc, layout.layer_xml(doc.layer), force=force)
    MetadataStore(layout).update(doc.layer, {
        "output": layout.rel(out), "source": source_info,
        "counts": {"sections": len(doc.sections),
                   "pages": sum(len(s.pages) for s in doc.sections),
                   "units": doc.n_units},
        "status": doc.meta["STATUS"], **run_info})
    log.info("[%s] %d câu -> %s", doc.layer, doc.n_units, out)
    return out
 
 
# --------------------------------------------------------------------------- #
# 6. CLI dùng chung: tham số + file config JSON
# --------------------------------------------------------------------------- #
def base_parser(description: str) -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=description,
                                formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    p.add_argument("--config", help="JSON: {'common': {...}, 'layers': {'han': {...}}}")
    p.add_argument("--layer", required=True, choices=sorted(LAYERS))
    p.add_argument("--work-id", help="Mã tác phẩm, vd CPO_18")
    p.add_argument("--data-root", default="data")
    p.add_argument("--title", default="")
    p.add_argument("--version", help="Ghi đè <VERSION>")
    p.add_argument("--translator", help="<TRANSLATOR> (lớp nghĩa/thơ)")
    p.add_argument("--source-label", help="<SOURCE>: URL/trích dẫn; mặc định = URL hoặc tên file")
    p.add_argument("--force", action="store_true", help="Ghi đè output đã có")
    p.add_argument("-v", "--verbose", action="store_true")
    return p
 
 
def parse_args(parser: argparse.ArgumentParser, argv: list[str] | None = None):
    """Ưu tiên: mặc định < config.common < config.layers[layer] < CLI."""
    pre, _ = parser.parse_known_args(argv)
    if pre.config:
        cfg = json.loads(Path(pre.config).read_text(encoding="utf-8"))
        merged = {**cfg.get("common", {}), **cfg.get("layers", {}).get(pre.layer, {})}
        known = {a.dest for a in parser._actions}
        # Một config dùng chung cho cả 3 tool: khoá không thuộc tool này thì bỏ qua.
        parser.set_defaults(**{k: v for k, v in merged.items() if k in known})
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    if not args.work_id:
        parser.error("Thiếu --work-id (CLI hoặc config)")
    return args
 