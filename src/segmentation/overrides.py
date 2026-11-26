"""Ghi đè tách lớp theo từng tài liệu, khai báo trong config (`segmentation.document_overrides`), có lý do.

Dùng cho các bài mà luật chung xử lý sai và người duyệt đã quyết định cách xử lý (CPO_18, audit 2026-11-27):

    "document_overrides": [
      {"start_page": 337, "action": "exclude", "reason": "lớp am chỉ là rác [UNCLEAR] của Qwen"},
      {"start_page": 339, "action": "notes", "layers": ["am"], "implicit_am_only": true,
       "reason": "khảo dị (6)-(47) trước PHIÊN ÂM mở ngầm lớp am"}
    ]

- Khoá là TRANG BẮT ĐẦU của tài liệu (ID tài liệu đổi khi segmentation đổi); `han_prefix` (tuỳ chọn) phân biệt
  hai tài liệu cùng trang bắt đầu: dòng Hán đầu tiên của tài liệu phải bắt đầu bằng chuỗi này.
- `action`: `exclude` (ra khỏi lớp dóng, review `override_excluded`) hoặc `notes` (vùng chú thích, review
  `override_note`). Cả hai đều gỡ dòng khỏi lớp; mỗi dòng bị gỡ có một dòng review (không mất dấu) và mỗi lần áp
  dụng có một dòng review `document_override` mang lý do.
- `layers` (mặc định: mọi lớp của tài liệu); `implicit_am_only`: với lớp am chỉ gỡ phần MỞ NGẦM (trước tiêu đề
  PHIÊN ÂM tường minh).
- Ghi đè không khớp tài liệu nào được báo ở log (cảnh báo) để không âm thầm mất hiệu lực khi ranh giới đổi.
"""

from __future__ import annotations

from dataclasses import dataclass

from src.segmentation.documents import review_row

ACTIONS = {"exclude": "override_excluded", "notes": "override_note"}


@dataclass(frozen=True)
class DocumentOverride:
    start_page: int
    action: str
    reason: str
    layers: tuple[str, ...] = ()
    implicit_am_only: bool = False
    han_prefix: str = ""


def parse_overrides(raw: list | None) -> list[DocumentOverride]:
    out = []
    for i, item in enumerate(raw or []):
        where = f"segmentation.document_overrides[{i}]"
        if not isinstance(item, dict):
            raise ValueError(f"{where}: phải là object")
        bad = set(item) - {"start_page", "action", "reason", "layers", "implicit_am_only", "han_prefix"}
        if bad:
            raise ValueError(f"{where}: khoá lạ {sorted(bad)}")
        if item.get("action") not in ACTIONS:
            raise ValueError(f"{where}: action phải là một trong {sorted(ACTIONS)}")
        if not str(item.get("reason") or "").strip():
            raise ValueError(f"{where}: thiếu reason")
        if not isinstance(item.get("start_page"), int):
            raise ValueError(f"{where}: start_page phải là số trang")
        out.append(DocumentOverride(int(item["start_page"]), item["action"], str(item["reason"]).strip(),
                                    tuple(item.get("layers") or ()), bool(item.get("implicit_am_only")),
                                    str(item.get("han_prefix") or "")))
    return out


def overrides_for(start_page: int, first_han: str, overrides: list[DocumentOverride]) -> list[DocumentOverride]:
    return [o for o in overrides
            if o.start_page == start_page and (not o.han_prefix or first_han.startswith(o.han_prefix))]


def apply_overrides(work_id: str, doc_id: str, grouped: dict[str, list], implicit_am: list,
                    overrides: list[DocumentOverride]) -> list[dict]:
    """Gỡ dòng khỏi `grouped` (sửa tại chỗ) theo các ghi đè; trả về các dòng review."""
    review: list[dict] = []
    for ov in overrides:
        code = ACTIONS[ov.action]
        removed = 0
        for layer in (ov.layers or tuple(grouped)):
            lines = grouped.get(layer) or []
            if ov.implicit_am_only and layer == "am":
                implicit_ids = {id(x) for x in implicit_am}
                target = [x for x in lines if id(x) in implicit_ids]
            else:
                target = list(lines)
            if not target:
                continue
            gone = {id(x) for x in target}
            rest = [x for x in lines if id(x) not in gone]
            if rest:
                grouped[layer] = rest
            else:
                grouped.pop(layer, None)
            for x in target:
                review.append(review_row(work_id, doc_id, layer, code, [x.page], x.text, [x.source_id]))
            removed += len(target)
        review.append(review_row(work_id, doc_id, ",".join(ov.layers) or "*", "document_override",
                                 [ov.start_page], f"{ov.action} ({removed} dòng): {ov.reason}"))
    return review
