"""Cấu hình OCR hai luồng theo work (ocr.streams)."""
import json
from pathlib import Path

import pytest

from src.common.corpus import DataLayout
from src.ocr import work_config as wc
from src.pipeline import is_unified_ocr_config, ocr_block, validate_config


def test_missing_block_gives_backward_compatible_defaults():
    s = wc.stream_settings({})
    assert s["pages"] is None and s["paddlevl_orientation"] == "auto"
    assert s["engines"] == {"han": "paddlevl", "vie": "qwen"}
    assert s["check"] == wc.CHECK_DEFAULTS


def test_settings_validate_values():
    cfg = {"ocr": {"streams": {"pages": "5-9", "paddlevl_orientation": "Horizontal",
                               "check": {"qwen_low_conf": 50}}}}
    s = wc.stream_settings(cfg)
    assert s["pages"] == "5-9" and s["paddlevl_orientation"] == "horizontal"
    assert s["check"]["qwen_low_conf"] == 50 and s["check"]["blank_ink"] == wc.CHECK_DEFAULTS["blank_ink"]
    for bad in ({"paddlevl_orientation": "diagonal"}, {"engines": {"han": "qwen"}}, {"pages": "x-y"},
                {"check": {"nope": 1}}, {"orientation": "horizontal"}):
        with pytest.raises(ValueError):
            wc.stream_settings({"ocr": {"streams": bad}})


def test_env_wins_over_config_and_config_over_default():
    s = wc.stream_settings({"ocr": {"streams": {"paddlevl_orientation": "horizontal"}}})
    env = {}
    assert wc.apply_engine_env(s, "paddlevl", env) == {"PADDLEVL_ORIENTATION": "config=horizontal"}
    assert env == {"PADDLEVL_ORIENTATION": "horizontal"}
    env = {"PADDLEVL_ORIENTATION": "vertical"}
    assert wc.apply_engine_env(s, "paddlevl", env)["PADDLEVL_ORIENTATION"] == "env=vertical"
    assert env["PADDLEVL_ORIENTATION"] == "vertical"
    env = {}
    assert wc.apply_engine_env(wc.stream_settings({}), "paddlevl", env) == {"PADDLEVL_ORIENTATION": "default=auto"}
    assert env == {}
    assert wc.apply_engine_env(s, "qwen", env) == {}


def test_resolve_pages_from_config_or_images(tmp_path):
    layout = DataLayout(tmp_path, "W")
    layout.pages_dir.mkdir(parents=True)
    for p in (5, 6, 7, 9):
        (layout.pages_dir / f"page_{p:03d}.png").write_bytes(b"")
    assert wc.resolve_pages({"pages": "1-3"}, layout) == "1-3"
    assert wc.resolve_pages({"pages": None}, layout) == "5-7,9"
    with pytest.raises(ValueError):
        wc.resolve_pages({"pages": None}, DataLayout(tmp_path, "EMPTY"))


def test_pipeline_ignores_streams_key():
    legacy = {"common": {"work_id": "W"}, "ocr": {"streams": {"pages": "1-2"}, "chi_tra": {"engines": "qwen"},
                                                   "vie": {"engines": "qwen"}}}
    assert not is_unified_ocr_config(legacy)
    assert set(ocr_block(legacy)) == {"chi_tra", "vie"}
    validate_config(legacy)
    unified = {"common": {"work_id": "W"}, "ocr": {"streams": {}, "engine": "qwen"}}
    assert is_unified_ocr_config(unified)
    assert not is_unified_ocr_config({"ocr": {"streams": {}}})


@pytest.mark.parametrize("work", ["CPO_18", "CPO_19", "CPO_20"])
def test_repo_configs_declare_streams(work):
    cfg = json.loads(Path(f"configs/{work}.json").read_text(encoding="utf-8"))
    s = wc.stream_settings(cfg)
    assert s["pages"] and s["paddlevl_orientation"] == "horizontal"
