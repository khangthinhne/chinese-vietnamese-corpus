"""paddlevl adapter 6: chẻ dải nhiều hàng theo bước hàng + chỉnh nghiêng (văn xuôi Hán in lưới ngang).

CPO_20 tr.663/270: kẽ giữa các hàng có dấu câu/hạt nhiễu nên không "trắng"; split_rows gộp 3-8 hàng vào một
đơn vị và model đọc lưới đó THEO CỘT. Trang thơ đã tách đúng (mỗi dải một hàng) không được đổi kết quả.
"""
import inspect

import numpy as np
from PIL import Image

from src.ocr.engines import paddle_ocr as P

PITCH, CHAR_H, CHAR_W, STEP = 60, 44, 40, 60


def grid_page(rows: int, *, bridge: bool, width: int = 1000, top: int = 100) -> np.ndarray:
    """Trang trắng có `rows` hàng "chữ" (ô đen); bridge=True thêm dấu câu nằm vắt qua kẽ hàng."""
    g = np.full((top * 2 + rows * PITCH, width), 255, dtype=np.uint8)
    for r in range(rows):
        y = top + r * PITCH
        for x in range(60, width - 60, STEP):
            g[y:y + CHAR_H, x:x + CHAR_W] = 0
        if bridge and r < rows - 1:
            for x in range(80, width - 80, 80):            # dấu câu vắt qua kẽ: kẽ không còn "trắng"
                g[y + CHAR_H - 2:y + PITCH + 4, x:x + 8] = 0
    return g


def mask_of(gray: np.ndarray):
    return P.strip_rules(P.ink_mask(gray))


def test_bridged_grid_split_one_unit_per_row():
    m = mask_of(grid_page(8, bridge=True))
    spans = P.split_rows(m)
    assert len(spans) == 8
    assert all(b - a < 1.6 * PITCH for a, b in spans)


def test_clean_rows_unchanged_by_pitch_split():
    m = mask_of(grid_page(8, bridge=False))
    prof = m.sum(axis=1)
    orig = P.split_by_pitch
    try:
        P.split_by_pitch = lambda spans, prof, **k: spans          # hành vi adapter 5
        before = P.split_rows(m)
    finally:
        P.split_by_pitch = orig
    assert P.split_rows(m) == before
    assert P.split_by_pitch(before, prof) == before


def test_solid_tall_block_not_chopped():
    """Khối mực liền cao ~2 bước hàng (chữ tiêu đề cỡ lớn): không có khe sâu nên split_by_pitch không cắt."""
    g = grid_page(6, bridge=False, top=300)
    g[100:220, 300:380] = 0
    g[100:220, 420:500] = 0
    prof = mask_of(g).sum(axis=1)
    rows = [(300 + r * PITCH, 300 + r * PITCH + CHAR_H) for r in range(6)]
    assert P.split_by_pitch([(100, 220), *rows], prof) == [(100, 220), *rows]


def test_skewed_grid_is_deskewed():
    gray = grid_page(8, bridge=True, width=1400)
    img = Image.fromarray(gray).convert("RGB").rotate(-1.5, resample=Image.BICUBIC, fillcolor=(255, 255, 255))
    page_image, orientation, regions, _, deg = P.plan_units(img, "horizontal")
    assert orientation == "horizontal"
    assert abs(deg - 1.5) <= 0.3
    assert len(regions) == 8
    assert page_image.size == img.size


def test_rotate_box_back_maps_to_page_coordinates():
    a = np.full((600, 800), 255, dtype=np.uint8)
    a[100:110, 650:660] = 0
    for deg in (1.5, -2.0):
        r = np.asarray(Image.fromarray(a).rotate(deg, resample=Image.BICUBIC, fillcolor=255))
        ys, xs = np.nonzero(r < 128)
        x0, y0, x1, y1 = P.rotate_box_back((xs.min(), ys.min(), xs.max() + 1, ys.max() + 1), deg, (800, 600))
        assert abs(x0 - 650) <= 2 and abs(y0 - 100) <= 2 and abs(x1 - 660) <= 2 and abs(y1 - 110) <= 2


def test_no_deskew_when_rows_already_split():
    img = Image.fromarray(grid_page(8, bridge=False)).convert("RGB")
    _, _, regions, _, deg = P.plan_units(img, "horizontal")
    assert deg == 0.0 and len(regions) == 8


def test_adapter_version_and_cache_key():
    assert P.ADAPTER_VERSION >= 6
    src = inspect.getsource(P.PaddleVLEngine.describe)
    assert "row_pitch_split" in src and "deskew" in src


def test_char_per_line_joined_signature():
    """CPO_20 tr.14 đv.7 / tr.268 đv.3: một hàng in thưa bị model xuống dòng sau từng chữ."""
    assert P.char_per_line(["玄", "麻", "蛋", "副", "璽", "書", "來"])
    assert P.char_per_line(["崇祠", "案案", "瞰", "蛟", "宫"])
    assert not P.char_per_line(["一", "二"])                              # quá ít dòng
    assert not P.char_per_line(["風而厚顏", "頃有一人從山邊來"])           # dòng thật nhiều chữ
    assert not P.char_per_line(["山", "Phiên âm:", "水"])                  # có chữ Latin
    src = inspect.getsource(P.PaddleVLEngine.describe)
    assert "char_lines_join" in src
