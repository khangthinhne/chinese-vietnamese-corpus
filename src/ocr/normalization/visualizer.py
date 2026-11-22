"""Visualization tool for Normalized OCR Bounding Boxes.

Overlays line bounding boxes from normalized OCR engine outputs (KimHanNom, Qwen, Tesseract)
on top of the original page extraction images, with engine-specific color schemes, line IDs,
and side-by-side comparison panels.
"""

from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont

log = logging.getLogger("ocr.visualizer")

ENGINE_COLORS: dict[str, tuple[int, int, int]] = {
    "kimhannom": (229, 57, 53),    # Crimson Red
    "qwen": (30, 136, 229),         # Cobalt Blue
    "tesseract": (46, 125, 50),     # Emerald Green
    "paddleocr": (251, 140, 0),     # Amber Orange
}

ENGINE_SHORT_PREFIX: dict[str, str] = {
    "kimhannom": "K",
    "qwen": "Q",
    "tesseract": "T",
    "paddleocr": "P",
}


def get_default_font(size: int = 24) -> ImageFont.ImageFont:
    """Loads a readable TrueType font or falls back to PIL default."""
    font_candidates = [
        "arial.ttf",
        "seguisym.ttf",
        "DejaVuSans.ttf",
        "C:/Windows/Fonts/arial.ttf",
        "C:/Windows/Fonts/segoeui.ttf",
    ]
    for candidate in font_candidates:
        try:
            return ImageFont.truetype(candidate, size=size)
        except OSError:
            continue
    return ImageFont.load_default()


def draw_bounding_boxes(
    base_image: Image.Image,
    lines: list[dict[str, Any]],
    engine_name: str,
    color: tuple[int, int, int],
    line_width: int = 4,
    show_labels: bool = True,
    short_label: bool = True,
    font_size: int = 22,
) -> Image.Image:
    """Draws bounding boxes and line ID badges onto a copy of the base image."""
    canvas = base_image.convert("RGBA")
    draw = ImageDraw.Draw(canvas)
    font = get_default_font(size=font_size)
    prefix = ENGINE_SHORT_PREFIX.get(engine_name, engine_name[:1].upper())

    for line in lines:
        bbox = line.get("bbox")
        if not bbox or len(bbox) != 4:
            continue

        x0, y0, x1, y1 = [int(v) for v in bbox]
        if x0 >= x1 or y0 >= y1:
            continue

        # Draw rectangle outline
        draw.rectangle([x0, y0, x1, y1], outline=color + (240,), width=line_width)

        # Draw line ID badge
        if show_labels:
            raw_id = line.get("line_id", "")
            if short_label:
                # E.g. kimhannom.L012 -> K.12
                clean_num = raw_id.split(".")[-1].lstrip("L0") or "0"
                label_text = f"{prefix}.{clean_num}"
            else:
                label_text = raw_id

            # Calculate text size using font bbox
            tb = font.getbbox(label_text)
            tw, th = tb[2] - tb[0], tb[3] - tb[1]
            pad = 4

            badge_x0 = max(0, x0)
            badge_y0 = max(0, y0 - th - pad * 2)
            badge_x1 = badge_x0 + tw + pad * 2
            badge_y1 = badge_y0 + th + pad * 2

            # Badge background
            draw.rectangle([badge_x0, badge_y0, badge_x1, badge_y1], fill=color + (220,))
            # Badge text (white)
            draw.text((badge_x0 + pad, badge_y0 + pad), label_text, fill=(255, 255, 255, 255), font=font)

    return canvas.convert("RGB")


def add_legend_banner(
    image: Image.Image,
    title: str,
    active_engines: list[str],
    banner_height: int = 90,
) -> Image.Image:
    """Prepends a header banner with title and color-coded engine legend."""
    w, h = image.size
    banner = Image.new("RGB", (w, banner_height), color=(248, 249, 250))
    draw = ImageDraw.Draw(banner)
    title_font = get_default_font(size=32)
    legend_font = get_default_font(size=26)

    # Draw title
    draw.text((30, 26), title, fill=(33, 37, 41), font=title_font)

    # Draw legend badges on the right
    curr_x = w - 40
    for eng in reversed(active_engines):
        color = ENGINE_COLORS.get(eng, (100, 100, 100))
        text = f"■ {eng.upper()}"
        tb = legend_font.getbbox(text)
        tw = tb[2] - tb[0]
        curr_x -= (tw + 30)
        draw.text((curr_x, 28), text, fill=color, font=legend_font)

    # Border line below banner
    draw.line([(0, banner_height - 1), (w, banner_height - 1)], fill=(200, 200, 200), width=2)

    combined = Image.new("RGB", (w, h + banner_height))
    combined.paste(banner, (0, 0))
    combined.paste(image, (0, banner_height))
    return combined


def create_side_by_side_panel(
    images_with_titles: list[tuple[str, Image.Image]],
    target_height: int = 1400,
) -> Image.Image:
    """Stitches multiple panel images horizontally with a title header for each."""
    resized_panels = []
    header_h = 50
    font = get_default_font(size=26)

    for title, img in images_with_titles:
        scale = target_height / img.height
        new_w = int(img.width * scale)
        res = img.resize((new_w, target_height), Image.Resampling.LANCZOS)

        panel = Image.new("RGB", (new_w, target_height + header_h), color=(240, 242, 245))
        draw = ImageDraw.Draw(panel)
        tb = font.getbbox(title)
        tw = tb[2] - tb[0]
        tx = max(10, (new_w - tw) // 2)
        draw.text((tx, 10), title, fill=(20, 20, 20), font=font)
        panel.paste(res, (0, header_h))
        resized_panels.append(panel)

    total_w = sum(p.width for p in resized_panels) + (len(resized_panels) - 1) * 8
    total_h = target_height + header_h
    combined = Image.new("RGB", (total_w, total_h), color=(220, 224, 230))

    offset_x = 0
    for p in resized_panels:
        combined.paste(p, (offset_x, 0))
        offset_x += p.width + 8

    return combined


def visualize_page(
    normalized_path: Path,
    image_path: Path,
    output_dir: Path,
    generate_side_by_side: bool = True,
) -> list[Path]:
    """Generates all visualization artifacts for a single normalized page JSON."""
    if not normalized_path.is_file():
        raise FileNotFoundError(f"Normalized JSON not found: {normalized_path}")
    if not image_path.is_file():
        raise FileNotFoundError(f"Source image not found: {image_path}")

    output_dir.mkdir(parents=True, exist_ok=True)
    with open(normalized_path, encoding="utf-8") as f:
        data = json.load(f)

    doc_id = data.get("document_id", "WORK")
    ws = data.get("writing_system", "ws")
    page_num = data.get("page", {}).get("page_number", 0)
    page_stem = f"{doc_id}_{ws}_p{page_num:04d}"

    base_image = Image.open(image_path).convert("RGB")
    engines = data.get("engines", {})
    generated_files: list[Path] = []

    per_engine_images: dict[str, Image.Image] = {}

    # 1. Generate individual engine overlays
    for eng_name, eng_data in engines.items():
        if eng_data.get("status") != "success":
            continue
        lines = eng_data.get("lines", [])
        color = ENGINE_COLORS.get(eng_name, (128, 128, 128))
        overlay = draw_bounding_boxes(
            base_image,
            lines,
            eng_name,
            color,
            line_width=4,
            show_labels=True,
            short_label=True,
        )
        overlay_banner = add_legend_banner(
            overlay,
            f"{doc_id} Page {page_num} [{ws.upper()}] — {eng_name.upper()} ({len(lines)} lines)",
            [eng_name],
        )
        eng_file = output_dir / f"{page_stem}_{eng_name}.png"
        overlay_banner.save(eng_file, quality=95)
        generated_files.append(eng_file)
        per_engine_images[eng_name] = overlay

    # 2. Generate composite multi-engine overlay
    composite = base_image.copy()
    active_names = []
    # Order: Qwen (large blocks background), Tesseract (lines), KimHanNom (chars foreground)
    draw_order = ["qwen", "tesseract", "kimhannom"]
    for eng_name in draw_order:
        if eng_name in engines and engines[eng_name].get("status") == "success":
            lines = engines[eng_name].get("lines", [])
            color = ENGINE_COLORS.get(eng_name, (128, 128, 128))
            composite = draw_bounding_boxes(
                composite,
                lines,
                eng_name,
                color,
                line_width=3,
                show_labels=True,
                short_label=True,
                font_size=20,
            )
            active_names.append(eng_name)

    composite_banner = add_legend_banner(
        composite,
        f"{doc_id} Page {page_num} [{ws.upper()}] — All Engines BBox Overlay",
        active_names,
    )
    comp_file = output_dir / f"{page_stem}_overlay_all.png"
    composite_banner.save(comp_file, quality=95)
    generated_files.append(comp_file)

    # 3. Generate side-by-side comparison
    if generate_side_by_side and len(per_engine_images) >= 2:
        panels = [("Original Image", base_image)]
        for eng in ["kimhannom", "qwen", "tesseract"]:
            if eng in per_engine_images:
                line_cnt = len(engines[eng].get("lines", []))
                panels.append((f"{eng.upper()} ({line_cnt} lines)", per_engine_images[eng]))
        sbs = create_side_by_side_panel(panels, target_height=1400)
        sbs_file = output_dir / f"{page_stem}_side_by_side.png"
        sbs.save(sbs_file, quality=92)
        generated_files.append(sbs_file)

    log.info("Generated %d visualizations for %s in %s", len(generated_files), page_stem, output_dir)
    return generated_files


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    parser = argparse.ArgumentParser(description="Visualize Normalized OCR Bounding Boxes")
    parser.add_argument("--work-id", default="CPO_18", help="Work/Document ID")
    parser.add_argument("--pages", default="205,206", help="Comma-separated page numbers")
    parser.add_argument("--writing-systems", default="chi_tra,vie", help="Writing systems")
    parser.add_argument(
        "--output-dir",
        default="reports/ocr_normalization_review",
        help="Target output directory for visualizations",
    )
    args = parser.parse_args()

    pages = [int(p.strip()) for p in args.pages.split(",") if p.strip()]
    ws_list = [w.strip() for w in args.writing_systems.split(",") if w.strip()]
    out_dir = Path(args.output_dir)

    all_generated: list[Path] = []
    for ws in ws_list:
        for p in pages:
            norm_path = Path(f"data/{args.work_id}/03_ocr/normalized/{ws}/page_{p:04d}.json")
            img_path = Path(f"data/{args.work_id}/02_extraction/pages/page_{p}.png")
            if not norm_path.exists():
                log.warning("Normalized file not found: %s", norm_path)
                continue
            if not img_path.exists():
                log.warning("Source image not found: %s", img_path)
                continue
            gen = visualize_page(norm_path, img_path, out_dir)
            all_generated.extend(gen)

    print(f"\nSuccessfully generated {len(all_generated)} visualization files in {out_dir}:")
    for f in all_generated:
        print(f"  - {f.as_posix()}")


if __name__ == "__main__":
    main()
