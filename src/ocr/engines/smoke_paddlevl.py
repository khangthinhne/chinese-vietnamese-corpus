import sys, json, logging
from PIL import Image
from src.ocr.engines.paddle_ocr import PaddleVLEngine

logging.basicConfig(level=logging.INFO)
print(PaddleVLEngine.describe("all"))
img = Image.open(sys.argv[1]).convert("RGB")
if len(sys.argv) > 2:                      # cắt theo crop_box của lần chạy Qwen cũ
    box = json.load(open(sys.argv[2], encoding="utf-8"))["runs"][0]["crop_box"]
    img = img.crop(tuple(box))
res = PaddleVLEngine().recognize(img, lang="all", psm=6, dpi=300)
print("hướng:", res.raw["orientation"], "| any_degenerate:", res.raw["any_degenerate"])
for l in res.lines:
    print(l.confidence, l.bbox, l.text)