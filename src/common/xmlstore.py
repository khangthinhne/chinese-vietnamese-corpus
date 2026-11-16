"""Tiện ích XML dùng chung cho mọi artifact trung gian của pipeline.

Quy ước:
    - Mỗi file có <root><FILE ID="<work_id>">...</FILE></root>.
    - Văn bản tự do là text của phần tử; metadata có cấu trúc là thuộc tính.
    - Danh sách ID/trang/cờ nối bằng dấu phẩy (giá trị không chứa dấu phẩy).
    - Thuộc tính None KHÔNG được ghi; phần tử vắng mặt đọc lại là None.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Iterable

from src.common.corpus import _atomic_write


def write_root(path: Path, root: ET.Element) -> Path:
    ET.indent(root, space="    ")
    _atomic_write(Path(path), ET.tostring(root, encoding="utf-8", xml_declaration=True))
    return Path(path)


def write_file_root(path: Path, work_id: str, tag: str = "root") -> ET.Element:
    root = ET.Element(tag)
    ET.SubElement(root, "FILE", ID=work_id)
    return root


def read_root(path: Path) -> ET.Element:
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(path)
    return ET.parse(path).getroot()


def file_element(root: ET.Element) -> ET.Element:
    el = root.find("FILE")
    if el is None:
        raise ValueError("XML thiếu phần tử <FILE>")
    return el


def add(parent: ET.Element, tag: str, text: str | None = None, **attrs) -> ET.Element:
    el = ET.SubElement(parent, tag)
    set_attrs(el, **attrs)
    if text is not None:
        el.text = str(text)
    return el


def set_attrs(el: ET.Element, **attrs) -> ET.Element:
    for key, value in attrs.items():
        if value is None:
            continue
        if isinstance(value, bool):
            value = "true" if value else "false"
        el.set(key, str(value))
    return el


def optional_text(parent: ET.Element, tag: str, value: str | None) -> ET.Element | None:
    if value is None:
        return None
    return add(parent, tag, value)


def text_of(el: ET.Element | None, default: str = "") -> str:
    return default if el is None or el.text is None else el.text


def opt_text_of(el: ET.Element | None) -> str | None:
    return None if el is None else (el.text or "")


def list_attr(el: ET.Element, name: str) -> list[str]:
    raw = el.get(name, "")
    return [x for x in raw.split(",") if x]


def int_list_attr(el: ET.Element, name: str) -> list[int]:
    return [int(x) for x in list_attr(el, name)]


def opt_int(el: ET.Element, name: str) -> int | None:
    value = el.get(name)
    return None if value in (None, "") else int(value)


def opt_float(el: ET.Element, name: str) -> float | None:
    value = el.get(name)
    return None if value in (None, "") else float(value)


def opt_bool(el: ET.Element, name: str) -> bool | None:
    value = el.get(name)
    return None if value is None else value == "true"


def bbox_attr(bbox: Iterable[int] | None) -> str | None:
    return None if bbox is None else ",".join(str(int(v)) for v in bbox)


def bbox_from_attr(value: str | None) -> tuple[int, int, int, int] | None:
    if not value:
        return None
    parts = tuple(int(x) for x in value.split(","))
    return parts if len(parts) == 4 else None
