"""Kiểm thử adapter PaddleOCR bằng model GIẢ (không cần cài paddle): parse 3.x/2.x, đổi lang, và
chạy qua đúng đường OCR theo hệ chữ (cache, validate_result, selector, manifest).

Lần chạy thật với Paddle cần: python -m pip install paddlepaddle paddleocr  (chưa được kiểm thử ở đây).
"""

from __future__ import annotations

import argparse
import json
import sys
import threading
from importlib import metadata, util
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.common.corpus import DataLayout                      # noqa: E402
from src.ocr import engines, ocr                              # noqa: E402

SIZE = (400, 300)


def v3_output():
    return [{
        "rec_texts": ["夫道處清虛", "   ", "域。若稱其功能", "x"],
        "rec_scores": np.array([0.93, 0.5, 0.88, 0.7]),
        "rec_polys": [np.array([[10.4, 10.2], [200.7, 10.1], [200.3, 40.9], [10.2, 40.6]]),
                      np.array([[0, 50], [10, 50], [10, 60], [0, 60]]),
                      np.array([[-5, 60], [420, 60], [420, 90], [-5, 90]]),      # vượt khung: phải kẹp lại
                      np.array([[30, 100], [30, 100], [30, 100], [30, 100]])],    # suy biến: bỏ
    }]


def v2_output():
    return [[[[[10, 10], [100, 10], [100, 30], [10, 30]], ("今正就尋西方根源", 0.91)],
             [[[10, 40], [100, 40], [100, 60], [10, 60]], ("伏願大和尚", 0.80)]]]


def test_parse_v3_normalizes_bbox_conf_and_skips_bad_lines():
    lines, raw = engines.parse_paddle_output(v3_output(), SIZE)
    assert [l.text for l in lines] == ["夫道處清虛", "域。若稱其功能"]
    assert lines[0].bbox == (10, 10, 201, 41) and all(isinstance(c, int) for c in lines[0].bbox)
    assert lines[1].bbox == (0, 60, 400, 90)                       # kẹp trong ảnh
    assert lines[0].confidence == pytest.approx(93.0) and 0 <= lines[1].confidence <= 100
    json.dumps(raw, allow_nan=False)                               # raw phải là JSON thuần


def test_parse_v3_wrapped_in_res_and_rec_boxes():
    out = [{"res": {"rec_texts": ["甲乙"], "rec_scores": [0.9], "rec_boxes": [[5, 6, 50, 60]]}}]
    lines, _ = engines.parse_paddle_output(out, SIZE)
    assert lines[0].bbox == (5, 6, 50, 60)


def test_parse_v2():
    lines, _ = engines.parse_paddle_output(v2_output(), SIZE)
    assert [l.text for l in lines] == ["今正就尋西方根源", "伏願大和尚"]
    assert lines[1].confidence == pytest.approx(80.0)
    assert engines.parse_paddle_output([None], SIZE) == ([], [])    # 2.x trả [None] khi trang trống


def test_lang_mapping_and_unsupported():
    assert engines.PaddleOcrEngine.paddle_lang("chi_tra") == "chinese_cht"
    with pytest.raises(ValueError):
        engines.PaddleOcrEngine.paddle_lang("chi_tra+vie")


class FakeModel:
    def __init__(self, output):
        self.output = output

    def predict(self, array):
        assert array.shape == (SIZE[1], SIZE[0], 3) and array.flags["C_CONTIGUOUS"]
        return self.output

    def ocr(self, array, cls=False):
        return self.output


@pytest.fixture()
def fake_paddle(monkeypatch):
    versions = {"paddleocr": "3.1.0", "paddlepaddle": "3.0.0"}
    monkeypatch.setattr(metadata, "version", lambda name: versions[name])
    monkeypatch.setattr(util, "find_spec", lambda name: object() if name == "paddle" else None)

    def install(major, output):
        versions["paddleocr"] = f"{major}.0.0"
        monkeypatch.setattr(engines.PaddleOcrEngine, "_new_model",
                            lambda self, code, m: FakeModel(output))
    install(3, v3_output())
    return install


def test_describe_missing_package(monkeypatch):
    def boom(name):
        raise metadata.PackageNotFoundError(name)
    monkeypatch.setattr(metadata, "version", boom)
    with pytest.raises(RuntimeError, match="paddlepaddle paddleocr"):
        engines.PaddleOcrEngine.describe("chi_tra")


def test_engine_is_serial_and_registered(fake_paddle):
    assert engines.ENGINES["paddle"] is engines.PaddleOcrEngine
    assert engines.PaddleOcrEngine.max_workers == 1
    d = engines.PaddleOcrEngine.describe("chi_tra")
    assert d["lang"] == "chinese_cht" and d["paddleocr_version"] == "3.0.0"


def run_page(tmp_path, names, select="first"):
    img = tmp_path / "page_205.png"
    Image.new("RGB", SIZE, "white").save(img)
    layout = DataLayout(tmp_path / "data", "T")
    layout.ensure()
    args = argparse.Namespace(work_id="T", region=None, psm=6, dpi=300, engines=names, select=select)
    desc = {n: engines.ENGINES[n].describe("chi_tra") for n in names}
    return ocr.process_writing_system_page(205, img, layout, args, "chi_tra", "chi_tra", "cjk",
                                           desc, threading.local()), layout


@pytest.mark.parametrize("major,output", [(3, v3_output()), (2, v2_output())])
def test_paddle_through_ocr_pipeline(tmp_path, fake_paddle, major, output):
    fake_paddle(major, output)
    rec, layout = run_page(tmp_path, ["paddle"])
    assert rec["selected_engine"] == "paddle" and rec["n_lines"] >= 2
    assert rec["confidence"] > 60                      # thang 0..100: không bị gắn low_conf oan
    again, _ = run_page(tmp_path, ["paddle"])          # lần 2 dùng cache
    assert again["engines"][0]["cache_hit"] is True


def test_best_conf_now_comparable_with_tesseract_scale(tmp_path, fake_paddle):
    class Tess(engines.OcrEngine):
        name = "tess_fake"
        max_workers = 1
        @classmethod
        def describe(cls, lang): return {"name": cls.name}
        def recognize(self, image, *, lang, psm, dpi):
            return engines.OcrResult(self.name, [engines.OcrLine("夫道", (1, 1, 50, 20), 50.0)], {})
    engines.ENGINES["tess_fake"] = Tess
    try:
        rec, _ = run_page(tmp_path, ["tess_fake", "paddle"], select="best_conf")
    finally:
        del engines.ENGINES["tess_fake"]
    assert rec["selected_engine"] == "paddle"          # 90 > 50; nếu quên nhân 100 thì Paddle không bao giờ thắng
