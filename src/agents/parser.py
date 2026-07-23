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
- brand_text (any visible brand name/logo text, or null)
- bounding_box: [left, top, right, bottom] as PERCENTAGES of image dimensions (0-100),
  e.g. a garment occupying the bottom-left quarter is [0, 50, 50, 100]

Return ONLY a valid JSON array. Bounding boxes must tightly enclose the ENTIRE \
garment including sleeves and hems. Each physical garment appears exactly once. \
Do not include non-garment items (luggage, accessories, shoes, bedding)."""


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

    garments = ask_vision_json(ask_path, PROMPT)

    results = []
    for g in garments:
        box = g.get("bounding_box")
        if not box or len(box) != 4:
            g["crop_path"] = None
            results.append(g)
            continue
        # percentage [left, top, right, bottom] -> full-res pixels
        l_pct, t_pct, r_pct, b_pct = box
        x = l_pct / 100 * full_w
        y = t_pct / 100 * full_h
        w = max(0, (r_pct - l_pct) / 100 * full_w)
        h = max(0, (b_pct - t_pct) / 100 * full_h)
        # 10% padding, clamped to image bounds
        px, py = w * PADDING, h * PADDING
        left = max(0, int(x - px))
        top = max(0, int(y - py))
        right = min(full_w, int(x + w + px))
        bottom = min(full_h, int(y + h + py))
        if right - left < 50 or bottom - top < 50:
            g["crop_path"] = None
            results.append(g)
            continue
        gtype = str(g.get("type", "garment")).replace("/", "-").replace(" ", "_")
        crop_path = work_dir / f"{image_path.stem}_g{g.get('garment_number', len(results)+1)}_{gtype}.jpg"
        im.crop((left, top, right, bottom)).convert("RGB").save(crop_path, quality=92)
        g["bounding_box"] = [left, top, right - left, bottom - top]
        g["crop_path"] = str(crop_path)
        results.append(g)

    # Pass 2 (parallel): verify each crop is exactly one complete garment; refine if not.
    from concurrent.futures import ThreadPoolExecutor
    to_refine = [g for g in results if g.get("crop_path")]
    if to_refine:
        with ThreadPoolExecutor(max_workers=min(6, len(to_refine))) as ex:
            list(ex.map(lambda g: _refine_crop(im, g, work_dir, image_path.stem), to_refine))

    # percentage boxes for UI overlay drawing at any display size
    for g in results:
        if g.get("bounding_box"):
            x, y, w, h = g["bounding_box"]
            g["box_pct"] = {"left": round(x / full_w * 100, 1), "top": round(y / full_h * 100, 1),
                            "width": round(w / full_w * 100, 1), "height": round(h / full_h * 100, 1)}
    return results


REFINE_PROMPT = """This image should show EXACTLY ONE complete garment of type "{gtype}" and nothing else.
Assess it and return ONLY valid JSON:
{{"ok": true/false,
  "issue": "<null if ok, else one of: 'contains_other_items', 'garment_cut_off', 'not_a_garment', 'physically_overlapping'>",
  "refined_box": <see below>}}

refined_box rules:
- ok=true or issue='not_a_garment': null
- issue='contains_other_items' where a tighter rectangle CAN isolate the {gtype}: [left, top, right, bottom] as PERCENTAGES (0-100) of THIS image, tightly around ONLY the {gtype}. Always attempt this.
- issue='physically_overlapping' (another garment lies ON TOP of the {gtype} so no rectangle can exclude it): null
- issue='garment_cut_off': null (the caller will expand the original box)
Minor background (bedsheet/floor) visible around the garment is fine — that is ok=true."""


def _refine_crop(im, g: dict, work_dir: Path, stem: str):
    """Pass 2: ask Claude to verify the crop; re-crop from the refined box if needed."""
    crop_path = Path(g["crop_path"])
    try:
        verdict = ask_vision_json(crop_path, REFINE_PROMPT.format(
            gtype=g.get("type", "garment")), max_tokens=300)
    except Exception as e:
        g["refine"] = {"ok": None, "issue": f"refine error: {e}"}
        return
    g["refine"] = {"ok": bool(verdict.get("ok")), "issue": verdict.get("issue")}

    if verdict.get("ok"):
        return
    if verdict.get("issue") == "not_a_garment":
        g["crop_path"] = None  # drop hallucinated items (pillows etc.)
        return
    if verdict.get("issue") == "garment_cut_off":
        # expand original box 20% each side and re-crop from the source image
        ox, oy, ow, oh = g["bounding_box"]
        ex, ey = int(ow * 0.2), int(oh * 0.2)
        left, top = max(0, ox - ex), max(0, oy - ey)
        right, bottom = min(im.size[0], ox + ow + ex), min(im.size[1], oy + oh + ey)
        expanded_path = work_dir / f"{crop_path.stem}_expanded.jpg"
        im.crop((left, top, right, bottom)).convert("RGB").save(expanded_path, quality=92)
        g["bounding_box"] = [left, top, right - left, bottom - top]
        g["crop_path"] = str(expanded_path)
        g["refine"]["refined"] = True
        return
    box = verdict.get("refined_box")
    if not box or len(box) != 4:
        return  # keep original crop; issue is recorded for the caller (e.g. physically_overlapping)
    # refined box is percentages of the CROP -> translate to full-image pixel coords
    ox, oy, ow, oh = g["bounding_box"]
    l_pct, t_pct, r_pct, b_pct = box
    left = max(0, int(ox + l_pct / 100 * ow))
    top = max(0, int(oy + t_pct / 100 * oh))
    right = min(im.size[0], int(ox + r_pct / 100 * ow))
    bottom = min(im.size[1], int(oy + b_pct / 100 * oh))
    if right - left < 50 or bottom - top < 50:
        return  # refusal to produce a degenerate crop
    # ponytail: a "refinement" discarding >60% of the crop is nearly always a
    # mis-grounding (low-contrast garments) — keep the original instead
    if (right - left) * (bottom - top) < 0.4 * ow * oh:
        g["refine"]["rejected_shrink"] = True
        return
    refined_path = work_dir / f"{crop_path.stem}_refined.jpg"
    im.crop((left, top, right, bottom)).convert("RGB").save(refined_path, quality=92)
    g["bounding_box"] = [left, top, right - left, bottom - top]
    g["crop_path"] = str(refined_path)
    g["refine"]["refined"] = True


if __name__ == "__main__":
    import sys
    src = Path(sys.argv[1]) if len(sys.argv) > 1 else config.REPO_ROOT / "smoke-test/inputs/multi.png"
    out = parse_garments(src)
    print(json.dumps(out, indent=2))
    assert isinstance(out, list) and out, "parser returned no garments"
    crops = [g for g in out if g.get("crop_path")]
    assert crops, "no crops produced"
    print(f"\n{len(crops)}/{len(out)} garments cropped OK")
