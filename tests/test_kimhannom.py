import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from src.ocr.engines.kimhannom import KimHanNomOCR


def main():
    engine = KimHanNomOCR(
        ocr_id=1,
        lang_type=1,
        reading_direction=1,
        epitaph=0,
    )

    image_path = ROOT_DIR / "data/CPO_18/02_extraction/pages/page_205.png"

    result = engine.predict_cached(
        image_path,
        out_dir=ROOT_DIR / "02_ocr_tho/han",
    )

    print(result["text"])


if __name__ == "__main__":
    main()