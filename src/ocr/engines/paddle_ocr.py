import paddle

print("Paddle:", paddle.__version__)
print("CUDA:", paddle.is_compiled_with_cuda())
print("Device count:", paddle.device.cuda.device_count())

from paddleocr import PaddleOCR

print("PaddleOCR import: OK")