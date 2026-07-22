"""OnAgain pipeline: Gate -> Parser -> ID -> (VTO + Comps) -> Channel -> Copy -> listing package.

Run: python -m src.pipeline <photo> [--model <base model photo>] [--single]
  --single skips the parser (photo is one garment already)
"""

import json
import sys
import time
from pathlib import Path

from src import config
from src.agents import channel, comps, gate, identifier, parser, vto
from src.agents import copy as copy_agent


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

    # per-garment: ID -> VTO + Comps -> Channel -> Copy -> package
    out_dir = config.WORK_DIR / "renders"
    listings_dir = config.WORK_DIR / "listings"
    listings_dir.mkdir(parents=True, exist_ok=True)
    packages = []

    for idx, gm in enumerate(garments, 1):
        gid = f"og_{idx:03d}"
        crop = Path(gm["crop_path"])
        print(f"[3/7] Identifying garment {idx} ({gm.get('type')}) ...")
        try:
            gm["identity"] = identifier.identify(crop)
            gtype = gm["identity"].get("type", gm.get("type", "auto"))
            print(f"  ✓ {gtype} — {gm['identity'].get('color')}")
        except Exception as e:
            gm["identity"] = {"error": str(e)}
            gtype = gm.get("type", "auto")
            print(f"  ✗ identify failed: {e}")

        print(f"[4/7] Rendering garment {idx} ({renders_per_garment}x VTO) ...")
        gm["vto"] = vto.render_garment(crop, model_photo, out_dir, gtype,
                                       n_renders=renders_per_garment)
        if gm["vto"]["best"]:
            print(f"  ✓ best: {Path(gm['vto']['best']).name} ({gm['vto']['ranking_reason'][:60]})")
        else:
            print(f"  ✗ {gm['vto']['ranking_reason']}")

        print(f"[5/7] Comps research for garment {idx} ...")
        try:
            gm["comps"] = comps.research(gm["identity"])
            print(f"  ✓ {gm['comps']['comp_count']} comps, mid ${gm['comps'].get('suggested_mid')}")
        except Exception as e:
            gm["comps"] = {"comps": [], "suggested_low": None, "suggested_mid": None,
                           "suggested_high": None, "comp_count": 0,
                           "reasoning": f"comps error: {e}"}
            print(f"  ✗ comps failed: {e}")

        print(f"[6/7] Channel recommendation for garment {idx} ...")
        gm["channel"] = channel.recommend(gm["identity"], gm["comps"])
        print(f"  ✓ {gm['channel']['primary']} — {gm['channel']['primary_reasoning'][:60]}")

        print(f"[7/7] Listing copy for garment {idx} ({gm['channel']['primary']}) ...")
        try:
            gm["copy"] = copy_agent.generate(gm["identity"], gm["comps"], gm["channel"]["primary"])
            print(f"  ✓ \"{gm['copy']['variants'][0]['title'][:60]}\"")
        except Exception as e:
            gm["copy"] = {"variants": [], "flags": {}, "error": str(e)}
            print(f"  ✗ copy failed: {e}")

        renders_ok = [r for r in gm["vto"]["renders"] if r.get("path")]
        variants = {v["style"]: {"title": v["title"], "description": v["description"],
                                 "hashtags": v.get("hashtags", [])}
                    for v in gm["copy"].get("variants", [])}
        package = {
            "garment_id": gid,
            "identity": gm["identity"],
            "photos": {
                "original": str(crop),
                "vto_hero": gm["vto"]["best"],
                "vto_alt": next((r["path"] for r in renders_ok if r["path"] != gm["vto"]["best"]), None),
                "tryon_link": None,
            },
            "pricing": gm["comps"],
            "copy": variants,
            "channel": gm["channel"],
            "flags": gm["copy"].get("flags", {}),
        }
        (listings_dir / f"{gid}.json").write_text(json.dumps(package, indent=2, default=str))
        packages.append(package)

    result["listings"] = packages
    result["status"] = "ok"
    result["wall_clock_s"] = round(time.time() - t0, 1)
    return result


def print_summary(result: dict):
    print("\nONAGAIN SESSION RESULTS")
    print("═" * 23)
    garments = result.get("garments", [])
    listings = result.get("listings", [])
    ok = [p for p in listings if p["photos"]["vto_hero"]]
    failed = [p for p in listings if not p["photos"]["vto_hero"]]
    print(f"Garments found: {len(garments)}")
    print(f"Listings generated: {len(ok)}" +
          (f" ({len(failed)} failed VTO)" if failed else ""))
    print()
    for p in listings:
        ident = p["identity"]
        name = " ".join(str(x) for x in [ident.get("color", ""), ident.get("type", "")] if x).title()
        brand = ident.get("brand") or "No brand"
        size = ident.get("visible_size") or "size n/a"
        pr = p["pricing"]
        price = (f"${pr['suggested_low']}-{pr['suggested_high']} ({pr['comp_count']} comps)"
                 if pr.get("suggested_mid") else "no comp data")
        kw = p["copy"].get("keyword", {})
        if p["photos"]["vto_hero"]:
            hero = Path(p["photos"]["vto_hero"]).name
            print(f"#{p['garment_id'][-1]}  {name} ({brand}, {size})")
            print(f"    Hero: {hero}")
            print(f"    Price: {price}")
            print(f"    Platform: {p['channel']['primary'].title()}")
            print(f"    Title: \"{kw.get('title', 'n/a')}\"")
        else:
            print(f"❌  {name} — VTO failed")
            print(f"    Suggestion: Re-photograph with more spacing between garments")
        print()


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
    print_summary(out)
