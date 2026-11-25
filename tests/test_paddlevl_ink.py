"""paddlevl: mặt nạ mực trên trang thưa mực (adapter 5)."""
import numpy as np

from src.ocr.engines import paddle_ocr


def page(ink_share: float, size=(400, 300)) -> np.ndarray:
    gray = np.full(size, 255, dtype=np.uint8)
    n = int(ink_share * gray.size)
    gray.reshape(-1)[:n] = 10
    return gray


def test_sparse_page_is_not_blank():
    gray = page(0.0125)                       # CPO_19 tr.41: ~1.3% điểm mực, phân vị 2 = giấy trắng
    mask = paddle_ocr.ink_mask(gray)
    assert abs(mask.mean() - 0.0125) < 1e-3


def test_blank_page_stays_blank():
    assert not paddle_ocr.ink_mask(page(0.0)).any()
    assert not paddle_ocr.ink_mask(np.full((50, 50), 200, dtype=np.uint8)).any()


def test_normal_page_threshold_unchanged():
    rng = np.random.default_rng(0)
    gray = rng.integers(150, 256, size=(200, 200)).astype(np.uint8)
    gray[:40] = 20                           # 20% mực
    lo, hi = np.percentile(gray, [2, 98])
    assert (paddle_ocr.ink_mask(gray) == (gray < (lo + hi) / 2)).all()


def test_adapter_version_bumped_for_ink_change():
    assert paddle_ocr.ADAPTER_VERSION >= 5
