"""Parser agent — find garments in a multi-garment photo, crop each via
Claude-provided pixel bounding boxes.

Input:  path to a photo containing one or more garments
Output: list of dicts: {garment_number, type, dominant_color, brand_text,
                        bounding_box, crop_path}
"""

import json
from pathlib import Path

from PIL import Image

from src import config
from src.utils.claude_client import ask_vision_json

# ponytail: downscale before asking for coords — vision models ground boxes far
# more reliably at moderate resolution, and we scale back up for the real crop.
ASK_WIDTH = 1000
PADDING = 0.10  # 10% padding per side; edge-cut garments fail VTO

PROMPT = """Identify each distinct garment in this image. For each garment, return:
- garment_number (1-indexed)
- type (e.g., pants, shirt, dress, jacket, shorts)
- dominant_color
- brand_text (any visible brand name/logo, or null)
- bounding_box: [x, y, width, height] in pixels, where x,y is the top-left corner

Return ONLY a valid JSON array. The image dimensions are {width}x{height} pixels. \
Coordinates must be within these bounds. Bounding boxes must tightly enclose the \
ENTIRE garment including sleeves and hems. Do not include non-garment items \
(luggage, accessories, shoes)."""


def parse_garments(image_path: Path, work_dir: Path = None) -> list:
    image_path = Path(image_path)
    work_dir = Path(work_dir) if work_dir else config.WORK_DIR / "crops"
    work_dir.mkdir(parents=True, exist_ok=True)

    im = Image.open(image_path)
    full_w, full_h = im.size

    # downscale copy for the vision call
    scale = min(1.0, ASK_WIDTH / full_w)
    ask_w, ask_h = int(full_w * scale), int(full_h * scale)
    ask_path = work_dir / f"_ask_{image_path.stem}.jpg"
    im.resize((ask_w, ask_h)).convert("RGB").save(ask_path, quality=90)

    garments = ask_vision_json(ask_path, PROMPT.format(width=ask_w, height=ask_h))

    results = []
    for g in garments:
        box = g.get("bounding_box")
        if not box or len(box) != 4:
            g["crop_path"] = None
            results.append(g)
            continue
        x, y, w, h = [v / scale for v in box]  # back to full-res coords
        # 10% padding, clamped to image bounds
        px, py = w * PADDING, h * PADDING
        left = max(0, int(x - px))
        top = max(0, int(y - py))
        right = min(full_w, int(x + w + px))
        bottom = min(full_h, int(y + h + py))
        gtype = str(g.get("type", "garment")).replace("/", "-").replace(" ", "_")
        crop_path = work_dir / f"{image_path.stem}_g{g.get('garment_number', len(results)+1)}_{gtype}.jpg"
        im.crop((left, top, right, bottom)).convert("RGB").save(crop_path, quality=92)
        g["bounding_box"] = [left, top, right - left, bottom - top]
        g["crop_path"] = str(crop_path)
        results.append(g)
    return results


if __name__ == "__main__":
    import sys
    src = Path(sys.argv[1]) if len(sys.argv) > 1 else config.REPO_ROOT / "smoke-test/inputs/multi.png"
    out = parse_garments(src)
    print(json.dumps(out, indent=2))
    assert isinstance(out, list) and out, "parser returned no garments"
    crops = [g for g in out if g.get("crop_path")]
    assert crops, "no crops produced"
    print(f"\n{len(crops)}/{len(out)} garments cropped OK")
