"""OnAgain core pipeline: Gate -> Parser -> ID -> VTO.

Run: python -m src.pipeline <photo> [--model <base model photo>] [--single]
  --single skips the parser (photo is one garment already)
"""

import json
import sys
import time
from pathlib import Path

from src import config
from src.agents import gate, identifier, parser, vto


def run(photo: Path, model_photo: Path, single: bool = False,
        renders_per_garment: int = vto.RENDERS_PER_GARMENT) -> dict:
    photo, model_photo = Path(photo), Path(model_photo)
    t0 = time.time()
    result = {"input": str(photo), "model": str(model_photo)}

    # 1. Gate
    print("[1/4] Gate check ...")
    g = gate.check(photo)
    result["gate"] = g
    if not g["pass"]:
        result["status"] = "rejected"
        print(f"  ✗ rejected: {g['reason']}")
        return result
    print(f"  ✓ {g['reason'][:80]}")

    # 2. Parse (or treat whole photo as one garment)
    if single:
        print("[2/4] Single-garment mode — skipping parser")
        garments = [{"garment_number": 1, "type": "auto", "crop_path": str(photo)}]
    else:
        print("[2/4] Parsing garments ...")
        garments = parser.parse_garments(photo)
        garments = [gm for gm in garments if gm.get("crop_path")]
        print(f"  ✓ {len(garments)} garment(s) found")
    result["garments"] = garments

    # 3+4. Identify and render each garment
    out_dir = config.WORK_DIR / "renders"
    for gm in garments:
        crop = Path(gm["crop_path"])
        print(f"[3/4] Identifying garment {gm['garment_number']} ({gm.get('type')}) ...")
        try:
            gm["identity"] = identifier.identify(crop)
            gtype = gm["identity"].get("type", gm.get("type", "auto"))
            print(f"  ✓ {gtype} — {gm['identity'].get('color')}")
        except Exception as e:
            gm["identity"] = {"error": str(e)}
            gtype = gm.get("type", "auto")
            print(f"  ✗ identify failed: {e}")

        print(f"[4/4] Rendering garment {gm['garment_number']} ({renders_per_garment}x VTO) ...")
        gm["vto"] = vto.render_garment(crop, model_photo, out_dir, gtype,
                                       n_renders=renders_per_garment)
        if gm["vto"]["best"]:
            print(f"  ✓ best: {gm['vto']['best']} ({gm['vto']['ranking_reason']})")
        else:
            print(f"  ✗ {gm['vto']['ranking_reason']}")

    result["status"] = "ok"
    result["wall_clock_s"] = round(time.time() - t0, 1)
    return result


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    single = "--single" in sys.argv
    photo = Path(args[0]) if args else config.REPO_ROOT / "smoke-test/inputs/multi.png"
    model_photo = (Path(args[args.index("--model") + 1]) if "--model" in args
                   else config.REPO_ROOT / "smoke-test/inputs/model.jpg")
    if "--model" in sys.argv:
        model_photo = Path(sys.argv[sys.argv.index("--model") + 1])
    out = run(photo, model_photo, single=single)
    print("\n=== PIPELINE RESULT ===")
    print(json.dumps(out, indent=2, default=str))
