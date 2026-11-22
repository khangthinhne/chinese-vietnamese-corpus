"""Behavioral tests; no OCR binaries or model downloads required."""
from copy import deepcopy
import json
from pathlib import Path
import random
import subprocess
import sys

import pytest

from src.ocr.voting import main, vote_page


def page(texts, weights=None):
    weights = weights or {e: 1 for e in texts}
    return {"page_id": "CPO_19_page_001", "engine_weights": weights,
            "engine_results": [{"engine": e, "lines": [
                {"line_id": "line_001", "text": text, "confidence": None}]}
                               for e, text in texts.items()]}


def line(payload):
    return vote_page(payload)["lines"][0]


def test_original_example_tie_and_provenance():
    payload = page({"paddleocr": "天地玄黃", "qwen": "天地玄黄", "tesseract": "天地玄黃"},
                   {"paddleocr": .25, "qwen": .5, "tesseract": .25})
    for result, bbox, confidence in zip(payload["engine_results"],
            [[100, 120, 400, 160], [102, 121, 402, 161], [99, 119, 401, 160]],
            [.96, None, .89]):
        result["lines"][0].update(bbox=bbox, confidence=confidence)
    original = deepcopy(payload)
    out = line(payload)
    assert out["text"] == "天地玄黄"
    assert out["agreement"] == .875 and out["coverage"] == 1
    assert out["columns"][3]["tie"] and out["columns"][3]["support"] == .5
    assert out["review_reasons"] == ["disagreement", "tie"]
    assert out["bbox"] == [102, 121, 402, 161] and out["bbox_engine"] == "qwen"
    assert {s["engine"]: s["confidence"] for s in out["sources"]} == {
        "paddleocr": .96, "qwen": None, "tesseract": .89}
    assert "confidence" not in out
    assert payload == original
    out["sources"][0]["bbox"][0] = -1
    assert payload == original


def test_agreement_and_weighted_majority():
    out = line(page({"a": "甲乙", "b": "甲乙"}))
    assert out["text"] == "甲乙" and out["agreement"] == 1
    assert not out["needs_review"]
    assert line(page({"a": "A", "b": "B", "c": "B"},
                     {"a": 6, "b": 2, "c": 2}))["text"] == "A"


@pytest.mark.parametrize("minority", ["XAB", "AXB", "ABX", "A", "B", ""])
def test_insertions_and_deletions_do_not_shift_remaining_votes(minority):
    out = line(page({"a": "AB", "b": "AB", "c": minority}))
    assert out["text"] == "AB"
    assert out["needs_review"]


def test_multiple_insertions_are_aligned_not_padded():
    out = line(page({"a": "AB", "b": "AXYB", "c": "AYB"},
                    {"a": .4, "b": .35, "c": .25}))
    assert out["text"] == "AYB"
    y = next(c for c in out["columns"] if c["selected"] == "Y")
    assert y["support"] == .6
    assert next(c for c in y["candidates"] if c["token"] == "Y")["engines"] == ["b", "c"]


def test_gap_tie_counts_toward_agreement():
    out = line(page({"a": "AB", "b": "AXB", "c": "AXB"},
                    {"a": .5, "b": .3, "c": .2}))
    assert out["text"] == "AB"
    gap = out["columns"][1]
    assert gap["selected"] is None and gap["output_index"] is None and gap["tie"]
    assert out["agreement"] == pytest.approx(2.5 / 3)


def test_decimal_tie_and_engine_name_tiebreak():
    out = line(page({"z": "A", "b": "B", "c": "B"},
                    {"z": .3, "b": .1, "c": .2}))
    assert out["text"] == "A" and out["columns"][0]["tie"]
    assert line(page({"z": "A", "a": "B"}))["text"] == "B"


def test_large_finite_weights_still_produce_valid_json():
    out = vote_page(page({"a": "A", "b": "A"}, {"a": 1e308, "b": 1e308}))
    json.dumps(out, allow_nan=False)
    assert out["lines"][0]["agreement"] == 1


def test_confidence_is_metadata_not_vote_strength():
    payload = page({"a": "A", "b": "B"}, {"a": 2, "b": 1})
    payload["engine_results"][0]["lines"][0]["confidence"] = .01
    payload["engine_results"][1]["lines"][0]["confidence"] = 99
    assert line(payload)["text"] == "A"


def test_missing_line_abstains_and_missing_engine_reduces_coverage():
    payload = page({"a": "甲", "b": "甲"}, {"a": .25, "b": .25, "qwen": .5})
    out = line(payload)
    assert out["text"] == "甲" and out["agreement"] == 1 and out["coverage"] == .5
    payload["engine_results"].append({"engine": "qwen", "lines": []})
    assert line(payload) == out
    payload["engine_results"][-1]["lines"] = [{"line_id": "line_001", "text": ""}]
    assert line(payload)["text"] == ""  # Explicit empty string votes GAP, wins tie.


def test_unicode_whitespace_punctuation_and_literal_gap_character():
    text = "  Tie\u0302\u0301ng Việt，黃黄-\t"
    out = line(page({"a": text, "b": "  Tiếng Việt，黃黄-\t"}))
    assert out["text"] == "  Tiếng Việt，黃黄-\t" and out["agreement"] == 1


def test_zero_weight_cannot_introduce_characters_or_lines():
    payload = page({"a": "AB", "b": "AB", "zero": "XYZ"},
                   {"a": 1, "b": 1, "zero": 0})
    payload["engine_results"][-1]["lines"].append({"line_id": "other", "text": "noise"})
    out = vote_page(payload)
    assert len(out["lines"]) == 1 and out["lines"][0]["text"] == "AB"
    assert not out["needs_review"]


def test_order_and_weight_scale_invariance():
    payload = page({"z": "AB", "b": "AXB", "a": "AYB"})
    for result in payload["engine_results"]:
        result["lines"].append({"line_id": "line_000", "text": "題"})
    expected = vote_page(payload)
    payload["engine_results"].reverse()
    for result in payload["engine_results"]:
        result["lines"].reverse()
    assert vote_page(payload) == expected
    payload["engine_weights"] = {e: 100 for e in payload["engine_weights"]}
    assert [r["text"] for r in vote_page(payload)["lines"]] == [r["text"] for r in expected["lines"]]
    assert expected["lines"][0]["line_id"] == "line_000"


def test_empty_page_and_empty_text():
    assert vote_page(page({}, {"a": 1}))["review_reasons"] == ["empty_page"]
    out = line(page({"a": "", "b": ""}))
    assert out["text"] == "" and out["agreement"] is None and out["coverage"] == 1
    assert out["review_reasons"] == ["empty_text"]
    assert "single_engine" in line(page({"a": "A"}))["review_reasons"]


def test_alignment_preserves_each_sources_characters_and_vote_mass():
    rng = random.Random(42)
    for _ in range(60):
        texts = {e: "".join(rng.choices("AAB甲- ", k=rng.randrange(9))) for e in "abc"}
        out = line(page(texts, {"a": 5, "b": 3, "c": 2}))
        recovered = dict.fromkeys(texts, "")
        for column in out["columns"]:
            engines = []
            assert sum(c["weight"] for c in column["candidates"]) == 10
            for candidate in column["candidates"]:
                for engine in candidate["engines"]:
                    engines.append(engine)
                    recovered[engine] += candidate["token"] or ""
            assert sorted(engines) == list("abc")
        assert recovered == texts


@pytest.mark.parametrize("weight", [-1, float("nan"), float("inf"), True, "0.5", None, 0])
def test_bad_weights(weight):
    with pytest.raises(ValueError, match="engine_weights"):
        vote_page(page({"a": "A"}, {"a": weight}))


@pytest.mark.parametrize("change,field", [
    (lambda p: p.update(page_id=""), "page_id"),
    (lambda p: p.update(engine_results={}), "engine_results"),
    (lambda p: p["engine_results"].append(deepcopy(p["engine_results"][0])), "engine"),
    (lambda p: p["engine_results"][0].update(engine="unknown"), "engine"),
    (lambda p: p["engine_results"][0]["lines"].append({"line_id": "line_001", "text": "A"}), "line_id"),
    (lambda p: p["engine_results"][0]["lines"][0].update(text=None), "text"),
    (lambda p: p["engine_results"][0]["lines"][0].update(bbox=[1, 0, 0, 2]), "bbox"),
    (lambda p: p["engine_results"][0]["lines"][0].update(bbox=[0, 1]), "bbox"),
    (lambda p: p["engine_results"][0]["lines"][0].update(confidence=float("nan")), "confidence"),
])
def test_invalid_fields(change, field):
    payload = page({"a": "A"})
    change(payload)
    with pytest.raises(ValueError, match=field):
        vote_page(payload)


def test_cli_roundtrip_force_and_errors(tmp_path, capsys):
    source, target = tmp_path / "input.json", tmp_path / "nested" / "voted.json"
    source.write_text(json.dumps(page({"a": "黃", "b": "黃"})), encoding="utf-8")
    args = ["--input", str(source), "--output", str(target)]
    assert main(args) == 0
    assert "黃" in target.read_text(encoding="utf-8")
    assert json.loads(target.read_text(encoding="utf-8"))["lines"][0]["text"] == "黃"
    before = target.read_bytes()
    assert main(args) == 1 and target.read_bytes() == before
    assert main([*args, "--force"]) == 0
    assert main(["--input", str(source), "--output", str(source), "--force"]) == 1
    for content in ['{', '{"page_id":"a","page_id":"b"}',
                    json.dumps(page({"a": "A"}, {"a": float("nan")}))]:
        source.write_text(content, encoding="utf-8")
        assert main([*args, "--force"]) == 1
        assert target.read_bytes() == before
    assert not list(target.parent.glob("*.tmp"))
    assert capsys.readouterr().err


def test_module_entrypoint(tmp_path):
    source, target = tmp_path / "in.json", tmp_path / "out.json"
    source.write_text(json.dumps(page({"new_engine": "文字"})), encoding="utf-8")
    proc = subprocess.run([sys.executable, "-m", "src.ocr.voting", "--input", str(source),
                           "--output", str(target)], capture_output=True, text=True,
                          cwd=Path(__file__).resolve().parents[1])
    assert proc.returncode == 0, proc.stderr
    assert json.loads(target.read_text(encoding="utf-8"))["lines"][0]["text"] == "文字"
