"""Xuất/đọc XML cho kho OCR thô theo hệ chữ.

Đầu ra: data/<WORK_ID>/03_ocr/ocr_raw/han.xml | vi.xml


XML giữ:
    - cấu trúc FILE -> OCR(writing system) -> PAGE -> LINE (dòng đã chọn, đã lọc),
    - toàn bộ provenance/run OCR (engine, tham số, ảnh, bbox, confidence, payload gốc),
    - KHÔNG có document_id (document được gán ở bước phân đoạn).
"""

from __future__ import annotations
import os
import re
import json
from pathlib import Path

import xml.etree.ElementTree as ET

from src.common.corpus import DataLayout
from src.common.xmlstore import (add, bbox_attr, bbox_from_attr, file_element, int_list_attr,
                                 list_attr, opt_float, opt_int, opt_text_of, read_root,
                                 set_attrs, text_of, write_file_root, write_root)
from src.ocr import raw_store
from src.segmentation.model import OcrLine


def _add_engine(parent: ET.Element, engine: dict | None) -> None:
    if not engine:
        return
    el = add(parent, "ENGINE")
    for key, value in engine.items():
        if isinstance(value, (str, int, float, bool)) or value is None:
            set_attrs(el, **{key.upper(): value})
        else:
            add(el, key.upper(), json.dumps(value, ensure_ascii=False, sort_keys=True))


def _add_parameters(parent: ET.Element, parameters: dict | None) -> None:
    if not parameters:
        return
    box = add(parent, "PARAMETERS")
    for key, value in parameters.items():
        if key == "engine" and isinstance(value, dict):
            _add_engine(box, value)
        elif isinstance(value, (str, int, float, bool)) or value is None:
            add(box, "PARAM", None if value is None else str(value), NAME=key)
        else:
            add(box, "PARAM", json.dumps(value, ensure_ascii=False, sort_keys=True), NAME=key)


def _add_run(parent: ET.Element, run: dict) -> None:
    el = add(parent, "RUN", KEY=run.get("key"), CREATED_AT=run.get("created_at"),
             IMAGE=run.get("image"), IMAGE_WIDTH=(run.get("image_size") or [None, None])[0],
             IMAGE_HEIGHT=(run.get("image_size") or [None, None])[1],
             CROP_BOX=",".join(str(x) for x in run.get("crop_box", [])) or None)
    _add_parameters(el, run.get("parameters"))
    for index, line in enumerate(run.get("lines", []), 1):
        add(el, "LINE", line.get("text", ""), NO=index,
            BBOX=bbox_attr(line.get("bbox")), CONFIDENCE=line.get("confidence"))
    if run.get("raw") is not None:
        add(el, "RAW", json.dumps(run["raw"], ensure_ascii=False, sort_keys=True))


def write_writing_system(layout: DataLayout, writing_system: str,
                         pages: list[int] | None = None) -> Path:
    manifest = raw_store.load_manifest(layout, writing_system)
    if manifest is None:
        raise ValueError(f"Chưa có manifest OCR cho {writing_system}")
    wanted = set(pages) if pages else None
    root = write_file_root(layout.ocr_raw_xml_path(writing_system), layout.work_id)
    ocr = add(file_element(root), "OCR", WRITING_SYSTEM=writing_system,
              LANG=manifest.get("lang"), KEEP=manifest.get("keep"),
              SCHEMA_VERSION=manifest.get("schema_version"))
    source = manifest.get("source")
    if isinstance(source, dict):
        add(ocr, "SOURCE", **{k.upper(): v for k, v in source.items()})
    engines = add(ocr, "ENGINES")
    for name, desc in (manifest.get("engines") or {}).items():
        _add_engine(engines, {"name": name, **(desc or {})})
    _add_parameters(ocr, manifest.get("parameters"))

    for rec in manifest.get("pages", []):
        page = rec["page"]
        if wanted is not None and page not in wanted:
            continue
        page_el = add(ocr, "PAGE", NUMBER=page, IMAGE=rec.get("image"),
                      IMAGE_SHA256=rec.get("image_sha256"), RAW=rec.get("raw"),
                      SELECTED_ENGINE=rec.get("selected_engine"),
                      SELECTED_KEY=rec.get("selected_key"),
                      CONFIDENCE=rec.get("confidence"), EMPTY=rec.get("empty"),
                      N_LINES=rec.get("n_lines"))
        for index, line in enumerate(rec.get("lines", []), 1):
            add(page_el, "LINE", line.get("text", ""), NO=index,
                BBOX=bbox_attr(line.get("bbox")), CONFIDENCE=line.get("confidence"))
        store = raw_store.load_page(layout, writing_system, page, layout.work_id)
        runs = add(page_el, "RUNS")
        for run in store.get("runs", []):
            _add_run(runs, run)
    
    xml_path = write_root(layout.ocr_raw_xml_path(writing_system), root)
    write_page_texts(layout, writing_system)
    return xml_path


_PAGE_TXT_RE = re.compile(r"^\d{3,}\.txt$")

_TEXT_DIR_NAMES = {
    "han": "han", "chi_tra": "han", "chi_trad": "han",
    "vi": "vi", "vie": "vi",
}


def text_dir_name(writing_system: str) -> str:
    """Tên thư mục TXT: han | vie (writing system lạ thì giữ nguyên tên)."""
    return _TEXT_DIR_NAMES.get(writing_system.lower(), writing_system)


def ocr_raw_text_dir(layout: DataLayout, writing_system: str) -> Path:
    """data/<WORK_ID>/03_ocr/ocr_raw_text/<han|vie>/"""
    return (layout.ocr_raw_xml_path(writing_system).parent.parent
            / "ocr_raw_text" / text_dir_name(writing_system))

def write_page_texts(layout: DataLayout, writing_system: str) -> list[Path]:
    """Sinh mỗi trang một file TXT (NNN.txt) từ <PAGE>/<LINE> cấp trang của XML.

    - Tên file = số trang PDF gốc (NUMBER), ví dụ 005.txt.
    - Mỗi dòng OCR một dòng TXT, giữ nguyên văn (không strip/normalize).
    - Chỉ lấy LINE là con trực tiếp của PAGE, bỏ qua RUNS/RUN/LINE.
    - Trang không có dòng chữ nào (hoặc toàn dòng trắng) -> không tạo file.
    - Xóa file NNN.txt cũ không còn trong XML; file/thư mục khác được giữ nguyên.
    - Toàn bộ XML được kiểm tra trước khi ghi, XML lỗi thì không đụng tới file cũ.
    """
    xml_path = layout.ocr_raw_xml_path(writing_system)
    if not xml_path.is_file():
        raise FileNotFoundError(f"Chưa có XML OCR cho {writing_system}: {xml_path}")
    try:
        root = read_root(xml_path)
    except ET.ParseError as exc:
        raise ValueError(f"XML OCR bị hỏng: {xml_path}: {exc}") from exc
    ocr = file_element(root).find("OCR")
    if ocr is None:
        raise ValueError(f"XML OCR thiếu <OCR>: {xml_path}")

    pages: dict[int, list[str]] = {}
    for page_el in ocr.findall("PAGE"):
        try:
            number = int(page_el.get("NUMBER", ""))
        except ValueError:
            raise ValueError(f"<PAGE> thiếu hoặc sai NUMBER trong {xml_path}") from None
        if number < 1:
            raise ValueError(f"NUMBER không hợp lệ ({number}) trong {xml_path}")
        if number in pages:
            raise ValueError(f"Trùng số trang {number} trong {xml_path}")
        pages[number] = [line.text or "" for line in page_el.findall("LINE")]

    out_dir = ocr_raw_text_dir(layout, writing_system)
    out_dir.mkdir(parents=True, exist_ok=True)

    written: list[Path] = []
    for number, lines in pages.items():
        if not any(line.strip() for line in lines):
            continue 
        path = out_dir / f"{number:03d}.txt"
        text = "\n".join(lines) + ("\n" if lines else "")
        tmp = path.with_name(path.name + ".tmp")
        with open(tmp, "w", encoding="utf-8", newline="") as fh:
            fh.write(text)
        os.replace(tmp, path)
        written.append(path)

    keep = set(written)
    for entry in out_dir.iterdir():
        if (entry.is_file() and not entry.is_symlink()
                and _PAGE_TXT_RE.match(entry.name) and entry not in keep):
            entry.unlink()
    return written

def load_writing_system(layout: DataLayout, writing_system: str,
                        pages: list[int]) -> list[OcrLine]:
    path = layout.ocr_raw_xml_path(writing_system)
    root = read_root(path)
    ocr = file_element(root).find("OCR")
    if ocr is None:
        raise ValueError(f"XML OCR thiếu <OCR>: {path}")
    wanted = set(pages)
    out: list[OcrLine] = []
    for page_el in ocr.findall("PAGE"):
        page = int(page_el.get("NUMBER", "0"))
        if page not in wanted:
            continue
        for line in page_el.findall("LINE"):
            index = int(line.get("NO", "0"))
            out.append(OcrLine(
                page=page, index=index, text=text_of(line),
                bbox=bbox_from_attr(line.get("BBOX")),
                confidence=opt_float(line, "CONFIDENCE"),
                writing_system=writing_system,
                engine=page_el.get("SELECTED_ENGINE", ""),
                cache_key=page_el.get("SELECTED_KEY", ""),
                image=page_el.get("IMAGE", "")))
    return out
