"""Gate agent — cheap pass/fail check that a photo contains resellable apparel.

Input:  path to a photo
Output: {"pass": bool, "reason": str}
"""

import json
from pathlib import Path

from src.utils.claude_client import ask_vision_json

PROMPT = ('Is this a photo of one or more clothing/apparel items suitable for resale? '
          'The photo must be clear enough to identify the garment (reject blurry or '
          'unrecognizable photos). Respond with ONLY JSON: {"pass": true/false, "reason": "string"}')


def check(image_path: Path) -> dict:
    result = ask_vision_json(Path(image_path), PROMPT, cheap=True, max_tokens=200)
    return {"pass": bool(result.get("pass")), "reason": str(result.get("reason", ""))}


if __name__ == "__main__":
    import sys, tempfile
    from PIL import Image, ImageFilter
    from src import config

    if len(sys.argv) > 1:
        print(json.dumps(check(Path(sys.argv[1])), indent=2))
        sys.exit()

    inputs = config.REPO_ROOT / "smoke-test/inputs"
    garment = inputs / "blouse.jpg"

    # non-garment test image: the pillow mis-crop from parser testing
    non_garment = config.WORK_DIR / "crops/multi_g4_pants.jpg"

    # blurry test image: blur the blouse beyond recognition
    blurry = Path(tempfile.gettempdir()) / "onagain_blurry.jpg"
    Image.open(garment).filter(ImageFilter.GaussianBlur(40)).save(blurry)

    for label, p, expect in [("garment", garment, True),
                             ("non-garment (pillow)", non_garment, False),
                             ("blurry", blurry, False)]:
        r = check(p)
        status = "OK" if r["pass"] == expect else "UNEXPECTED"
        print(f"{label}: pass={r['pass']} ({r['reason']}) -> {status}")
        assert r["pass"] == expect, f"gate gave wrong answer for {label}"
    print("\ngate OK")
