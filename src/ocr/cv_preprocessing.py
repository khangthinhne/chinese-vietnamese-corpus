
import cv2
import numpy as np

def preprocess_for_ocr_using_contrast(input_path, output_path):
    # 1. Read image
    img = cv2.imread(input_path)

    # 2. Convert to grayscale
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)

    # 3. Increase local contrast using CLAHE
    clahe = cv2.createCLAHE(
        clipLimit=2.0,
        tileGridSize=(1, 1)
    )
    enhanced = clahe.apply(gray)

    # 4. Reduce noise
    denoised = cv2.GaussianBlur(
        enhanced, (3, 3), 0
    )

    # 5. Adaptive Thresholding
    binary = cv2.adaptiveThreshold(
        denoised,
        255,
        cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
        cv2.THRESH_BINARY,
        31,
        10
    )

    # 6. Save result
    cv2.imwrite(output_path, binary)

def preproccess_for_ocr_using_bright(input_path, output_path):
    img = cv2.imread(input_path)
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)

    # Adjust contrast and brightness
    # alpha: contrast
    # beta: brightness
    enhanced = cv2.convertScaleAbs(
        gray,
        alpha=1.2,  # Increase contrast
        beta=-40
    )
    cv2.imwrite(output_path, enhanced)


def main(argv=None):
    import argparse
    import os
    ap = argparse.ArgumentParser(description="Tiền xử lý ảnh trước OCR (chưa nối vào src/ocr/ocr.py)")
    ap.add_argument("input")
    ap.add_argument("output")
    ap.add_argument("--method", choices=["bright", "contrast"], default="bright")
    args = ap.parse_args(argv)
    if cv2.imread(args.input) is None:
        raise SystemExit(f"Không đọc được ảnh: {args.input}")
    os.makedirs(os.path.dirname(os.path.abspath(args.output)), exist_ok=True)
    fn = preproccess_for_ocr_using_bright if args.method == "bright" else preprocess_for_ocr_using_contrast
    fn(args.input, args.output)


if __name__ == "__main__":
    main()
