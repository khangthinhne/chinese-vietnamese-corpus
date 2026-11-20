import torch
from PIL import Image
from transformers import (
    AutoProcessor,
    Qwen2_5_VLForConditionalGeneration,
    BitsAndBytesConfig,
)
from qwen_vl_utils import process_vision_info

MODEL_ID = "Qwen/Qwen2.5-VL-3B-Instruct"

# RTX 3050 6GB VRAM + 32GB RAM: conservative starting settings
IMAGE_PATH = "data/CPO_18/02_extraction/pages/page_205.png"
MIN_PIXELS = 256 * 28 * 28
MAX_PIXELS = 384 * 28 * 28
MAX_NEW_TOKENS = 512

quant_config = BitsAndBytesConfig(
    load_in_4bit=True,
    bnb_4bit_quant_type="nf4",
    bnb_4bit_compute_dtype=torch.float16,
    bnb_4bit_use_double_quant=True,
)

print("Loading processor...")
processor = AutoProcessor.from_pretrained(
    MODEL_ID,
    min_pixels=MIN_PIXELS,
    max_pixels=MAX_PIXELS,
)

print("Loading model...")
model = Qwen2_5_VLForConditionalGeneration.from_pretrained(
    MODEL_ID,
    quantization_config=quant_config,
    device_map="auto",
    max_memory={0: "4GiB", "cpu": "24GiB"},
    dtype=torch.float16,
    attn_implementation="sdpa",
    low_cpu_mem_usage=True,
)
model.eval()

print("Model device map:")
print(model.hf_device_map)


def print_gpu_memory(label: str) -> None:
    if not torch.cuda.is_available():
        print("CUDA is not available.")
        return

    torch.cuda.synchronize()
    allocated = torch.cuda.memory_allocated(0) / 1024**3
    reserved = torch.cuda.memory_reserved(0) / 1024**3
    free, total = torch.cuda.mem_get_info(0)

    print(f"\n[{label}]")
    print(f"Allocated: {allocated:.2f} GB")
    print(f"Reserved:  {reserved:.2f} GB")
    print(f"Free:      {free / 1024**3:.2f} GB")
    print(f"Total:     {total / 1024**3:.2f} GB")


print("Loading image...")
image = Image.open(IMAGE_PATH).convert("RGB")

prompt = """
You are an OCR system for historical Chinese literature.

Transcribe all visible Chinese text in the image.
Requirements:
1. Preserve the original Chinese characters. Do not translate.
2. Preserve punctuation and the original reading order.
3. Do not summarize, explain, or add interpretations.
4. Do not invent missing or illegible characters.
5. Mark uncertain characters as [UNCLEAR].
6. Keep line breaks where possible.

Return only the transcribed text.
"""

messages = [
    {
        "role": "user",
        "content": [
            {"type": "image", "image": image},
            {"type": "text", "text": prompt},
        ],
    }
]

text = processor.apply_chat_template(
    messages,
    tokenize=False,
    add_generation_prompt=True,
)
image_inputs, video_inputs = process_vision_info(messages)

inputs = processor(
    text=[text],
    images=image_inputs,
    videos=video_inputs,
    padding=True,
    return_tensors="pt",
)

# Place inputs on the model's input embedding device.
input_device = model.get_input_embeddings().weight.device
inputs = inputs.to(input_device)

print_gpu_memory("Before inference")
print("Running OCR...")

with torch.inference_mode():
    generated_ids = model.generate(
        **inputs,
        max_new_tokens=MAX_NEW_TOKENS,
        do_sample=False,
    )

print_gpu_memory("After inference")

generated_ids_trimmed = [
    output_ids[len(input_ids):]
    for input_ids, output_ids in zip(inputs.input_ids, generated_ids)
]

result = processor.batch_decode(
    generated_ids_trimmed,
    skip_special_tokens=True,
    clean_up_tokenization_spaces=False,
)[0]

print("\n===== OCR RESULT =====\n")
print(result)

print(
    "\nGPU memory allocated:",
    round(torch.cuda.memory_allocated(0) / 1024**3, 2),
    "GB",
)

output_path = "qwen_ocr_result.txt"
with open(output_path, "w", encoding="utf-8") as f:
    f.write(result)

print(f"\nSaved OCR result to: {output_path}")
