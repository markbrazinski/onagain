"""Bake a deterministic 'golden' demo bundle: run the real pipeline ONCE, save every
artifact + the timings, so verified replay can replay the whole seller+buyer flow with zero
API spend.

Produces demo/golden/:
  source.jpg                       the hero photo
  crops/{gid}.jpg                  per-garment crop (gid = sha1(crop)[:12], STABLE link id)
  renders/{gid}.jpg                best mannequin render (best-of-3)
  buyer/{gid}.jpg                  best buyer try-on render (best-of-4 on the stock person)
  listings.json                    {gid: {title, description, price, platform, hashtags, hero, tryon}}
  timings.json                     recorded durations for the replay animations

Run: python scripts/bake_golden.py <photo> [--person <stock person photo>]

ponytail: one script, reuses the real agents — no parallel 'fake pipeline' to drift.
The best-of-N selection reuses vto.render_garment's own Claude ranking.
"""

import hashlib
import json
import shutil
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import config
from src.agents import channel, comps, gate, identifier, parser, vto
from src.agents import copy as copy_agent
from src.utils import vto_client

GOLDEN = config.REPO_ROOT / "demo" / "golden"
MANNEQUIN = config.REPO_ROOT / "assets" / "bases" / "mannequin.jpg"
BUYER_RENDERS = 4          # buyer side: run 3-4, keep best
GEN_RENDERS = 3            # seller side: first good, then 2 more, keep best


def gid_for(crop_path: Path) -> str:
    """Stable per-article id = sha1 of the crop bytes. Same garment -> same /tryon link."""
    return hashlib.sha1(Path(crop_path).read_bytes()).hexdigest()[:12]


def _best_of(crop: Path, base: Path, out_dir: Path, gtype: str, n: int) -> dict:
    """Render n times, Claude-rank, return vto.render_garment's best. Reuses the ranker."""
    return vto.render_garment(crop, base, out_dir, gtype, n_renders=n)


def bake(photo: Path, person: Path, parts: list = None):
    GOLDEN.mkdir(parents=True, exist_ok=True)
    for sub in ("crops", "renders", "buyer"):
        (GOLDEN / sub).mkdir(parents=True, exist_ok=True)
    timings = {}
    shutil.copy(photo, GOLDEN / "source.jpg")

    # 1. Gate + Parse (timed — drives the "splitting" animation)
    print("[gate] ...")
    g = gate.check(photo)
    assert g["pass"], f"gate rejected: {g['reason']}"
    if parts:
        # deterministic bundle: pre-cropped single-garment images, skip the parser lottery.
        # Time the (still-real) parse of the full photo so the split animation is honest,
        # but use the hand-cropped parts as the actual garments.
        print(f"[parse] using {len(parts)} pre-cropped parts (parser timed for animation)")
        t0 = time.time()
        _ = parser.parse_garments(photo)     # real parse, timed — result discarded
        timings["parse_s"] = round(time.time() - t0, 1)
        garments = [{"garment_number": i + 1, "type": "auto", "crop_path": str(p)}
                    for i, p in enumerate(parts)]
    else:
        print("[parse] ...")
        t0 = time.time()
        garments = [gm for gm in parser.parse_garments(photo) if gm.get("crop_path")]
        timings["parse_s"] = round(time.time() - t0, 1)
    print(f"  {len(garments)} garments in {timings['parse_s']}s")

    listings = {}
    timings["per_garment"] = []

    for gm in garments:
        crop_src = Path(gm["crop_path"])
        gid = gid_for(crop_src)                      # STABLE id
        shutil.copy(crop_src, GOLDEN / "crops" / f"{gid}.jpg")
        gt0 = time.time()
        steps = {}                                   # per-step wall-clock (drives the 3 circles)

        # ID
        _s = time.time()
        ident = identifier.identify(crop_src)
        gtype = ident.get("type", gm.get("type", "auto"))
        steps["identify"] = round(time.time() - _s, 1)

        # Seller render: best-of-3 on the mannequin
        print(f"[render {gid}] {gtype} best-of-{GEN_RENDERS} ...")
        _s = time.time()
        res = _best_of(crop_src, MANNEQUIN, config.WORK_DIR / "renders", gtype, GEN_RENDERS)
        steps["vto"] = round(time.time() - _s, 1)
        if not res.get("best"):
            print(f"  ! render failed ({res.get('ranking_reason')}) — skipping {gid}")
            continue
        shutil.copy(res["best"], GOLDEN / "renders" / f"{gid}.jpg")

        # Comps + channel + copy
        _s = time.time()
        cm = comps.research(ident)
        steps["price"] = round(time.time() - _s, 1)
        ch = channel.recommend(ident, cm)
        primary = ch.get("primary", "depop")
        # Bake copy for the primary + the common alternates so the platform-swap in replay
        # shows real, differently-voiced copy (not a static string).
        plats = list(dict.fromkeys([primary, "poshmark", "depop", "ebay"]))
        _s = time.time()
        copy_by_platform = {}
        for p in plats:
            try:
                copy_by_platform[p] = copy_agent.generate(ident, cm, p)
            except Exception as e:
                print(f"  ! copy failed for {p}: {e}")
        steps["copy"] = round(time.time() - _s, 1)
        cp = copy_by_platform.get(primary) or next(iter(copy_by_platform.values()), {"variants": []})
        variants = cp.get("variants", [])
        v0 = variants[0] if variants else {}

        # Buyer render: best-of-4 on the stock person. Match the REAL /api/tryon path
        # exactly: render_garment(garment_hero, person) — garment ref = the listing hero
        # (mannequin render), base = the person's photo.
        print(f"[buyer {gid}] best-of-{BUYER_RENDERS} on stock person ...")
        hero_render = GOLDEN / "renders" / f"{gid}.jpg"
        bres = _best_of(hero_render, person, config.WORK_DIR / "buyer", gtype, BUYER_RENDERS)
        if bres.get("best"):
            shutil.copy(bres["best"], GOLDEN / "buyer" / f"{gid}.jpg")

        listings[gid] = {
            "garment_id": gid,
            "title": v0.get("title") or f"{ident.get('color','')} {gtype}".strip().title(),
            "description": v0.get("description", ""),
            "hashtags": v0.get("hashtags", []),
            "price": cm.get("suggested_mid"),
            "price_low": cm.get("suggested_low"),
            "price_high": cm.get("suggested_high"),
            "comp_count": cm.get("comp_count", 0),
            "platform": ch.get("primary", "depop"),
            "type": gtype,
            "brand": ident.get("brand"),
            "size": ident.get("visible_size") or ident.get("size"),
            "hero": f"renders/{gid}.jpg",
            "buyer": f"buyer/{gid}.jpg" if bres.get("best") else None,
            "tryon": f"/tryon/{gid}",
            # copy variants per platform -> {platform: {title, description, hashtags}} so the
            # replay's platform swap regenerates real, differently-voiced copy.
            "copy_by_platform": {
                p: {"title": (c.get("variants") or [{}])[0].get("title", ""),
                    "description": (c.get("variants") or [{}])[0].get("description", ""),
                    "hashtags": (c.get("variants") or [{}])[0].get("hashtags", [])}
                for p, c in copy_by_platform.items()
            },
        }
        secs = round(time.time() - gt0, 1)
        timings["per_garment"].append({"gid": gid, "seconds": secs, "steps": steps})
        print(f"  done {gid} in {secs}s (steps {steps}) -> ${listings[gid]['price']}")

    # approval -> live sequence timing (sum of per-garment, for the posting animation)
    timings["approve_to_live_s"] = round(sum(x["seconds"] for x in timings["per_garment"]), 1)

    (GOLDEN / "listings.json").write_text(json.dumps(listings, indent=2, default=str))
    (GOLDEN / "timings.json").write_text(json.dumps(timings, indent=2))
    print(f"\nBAKED {len(listings)} listings -> {GOLDEN}")
    print(f"timings: {timings}")
    return listings, timings


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    photo = Path(args[0]) if args else config.REPO_ROOT / "smoke-test/inputs/image-8.jpg"
    person = (Path(sys.argv[sys.argv.index("--person") + 1]) if "--person" in sys.argv
              else config.REPO_ROOT / "smoke-test/inputs/buyer.jpg")
    # --parts <dir>: deterministic bundle from pre-cropped single-garment images in <dir>
    parts = None
    if "--parts" in sys.argv:
        pdir = Path(sys.argv[sys.argv.index("--parts") + 1])
        parts = sorted(pdir.glob("*.jpg"))
        assert parts, f"no *.jpg in {pdir}"
    assert photo.exists(), f"photo not found: {photo}"
    assert person.exists(), f"stock person not found: {person} (pass --person <path>)"
    bake(photo, person, parts=parts)
