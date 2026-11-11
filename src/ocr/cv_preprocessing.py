
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
        alpha=1.35,
        beta=-100
    )
    cv2.imwrite(output_path, enhanced)


preproccess_for_ocr_using_bright(
    "test/data/input/page_001.png",
    "test/data/processed/processed.png"
)
