"""Ghi đè tách lớp theo tài liệu trong config (src/segmentation/overrides.py)."""
import pytest

from src.segmentation.overrides import overrides_for, parse_overrides
from tests.test_segmentation_audit_fixes import assign

TEXTS = [("han", "天氣和暢雖六陰節候盛陽常自如稼穡"), ("vie", "(6) VK: để trống 2 chữ"),
         ("vie", "(7) Sau chữ này, có thể thêm chữ, như từng thấy trong câu ở thượng văn."), ("vie", "PHIÊN ÂM:"),
         ("vie", "Duy Trinh Nguyên thập tứ niên, tuế thứ Mậu Dần."), ("vie", "DỊCH NGHĨA:"),
         ("vie", "Vào ngày Canh Tuất, năm Trinh Nguyên thứ 14.")]


def test_parse_validates_action_reason_and_keys():
    with pytest.raises(ValueError):
        parse_overrides([{"start_page": 1, "action": "drop", "reason": "x"}])
    with pytest.raises(ValueError):
        parse_overrides([{"start_page": 1, "action": "exclude", "reason": " "}])
    with pytest.raises(ValueError):
        parse_overrides([{"start_page": 1, "action": "exclude", "reason": "x", "page": 2}])
    ov = parse_overrides([{"start_page": 339, "action": "notes", "layers": ["am"], "implicit_am_only": True,
                           "reason": "khảo dị"}])
    assert ov[0].layers == ("am",) and ov[0].implicit_am_only


def test_overrides_match_by_start_page_and_optional_han_prefix():
    ov = parse_overrides([{"start_page": 5, "action": "exclude", "reason": "a"},
                          {"start_page": 5, "action": "notes", "reason": "b", "han_prefix": "擬求"}])
    assert [o.reason for o in overrides_for(5, "天氣", ov)] == ["a"]
    assert [o.reason for o in overrides_for(5, "擬求救瘴", ov)] == ["a", "b"]
    assert overrides_for(6, "擬求救瘴", ov) == []


def test_exclude_whole_document_keeps_trace_and_no_missing_layer_noise():
    ov = parse_overrides([{"start_page": 1, "action": "exclude", "reason": "rác Qwen"}])
    res = assign(TEXTS, overrides=ov)
    assert res.lines == {}
    reasons = [r["reason"] for r in res.review]
    assert reasons.count("override_excluded") == 5                # 1 Hán + 4 dòng Việt nội dung
    assert "document_override" in reasons and "missing_layer" not in reasons
    summary = next(r for r in res.review if r["reason"] == "document_override")
    assert "rác Qwen" in summary["text"] and "5 dòng" in summary["text"]


def test_notes_on_implicit_am_only_keeps_explicit_phien_am():
    ov = parse_overrides([{"start_page": 1, "action": "notes", "layers": ["am"], "implicit_am_only": True,
                           "reason": "khảo dị trước PHIÊN ÂM"}])
    res = assign(TEXTS, overrides=ov)
    assert [x.text for x in res.lines["am"]] == ["Duy Trinh Nguyên thập tứ niên, tuế thứ Mậu Dần."]
    assert len(res.lines["han"]) == 1 and len(res.lines["nghia"]) == 1
    notes = [r["text"] for r in res.review if r["reason"] == "override_note"]
    assert notes[0].startswith("(6) VK") and len(notes) == 2
    base = assign(TEXTS)
    assert len(base.lines["am"]) == 3
