"""Verified replay: serve the pre-baked golden bundle so a full seller+buyer demo
runs with ZERO API spend, deterministically, every time.

The bundle lives in demo/golden/ (see scripts/bake_golden.py). Routes here mirror the
real ones under /api/replay/* and read only from disk — no gate/parser/vto/comps/copy.

ponytail: separate router, real endpoints untouched. Frontend flips to these when ?replay=1.
"""

import json
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from src import config

GOLDEN = config.REPO_ROOT / "demo" / "golden"

router = APIRouter(prefix="/api/replay")


def _load() -> dict:
    f = GOLDEN / "listings.json"
    return json.loads(f.read_text()) if f.exists() else {}


def _timings() -> dict:
    f = GOLDEN / "timings.json"
    return json.loads(f.read_text()) if f.exists() else {}


@router.get("/timings")
def timings():
    """Recorded durations that drive the replay animations."""
    return _timings()


@router.get("/parse")
def parse_replay():
    """Replay the split: the source photo + the garments it produced (with crop urls)."""
    listings = _load()
    garments = [{"garment_number": i + 1, "garment_id": gid, "type": v.get("type"),
                 "crop_url": f"/api/replay/crop/{gid}"}
                for i, (gid, v) in enumerate(listings.items())]
    return {"batch_id": "golden", "gate": {"pass": True, "reason": "Demo replay"},
            "source_url": "/api/replay/source", "garments": garments,
            "timings": _timings()}


@router.get("/listings")
def listings():
    """All baked, ready-to-post listings (title, price, platform, hero, tryon link)."""
    return {"listings": list(_load().values())}


@router.get("/saved")
def saved():
    """Golden listings the seller has saved this session, with CURRENT marketplace + try-on
    count from shared listing_state — so the inventory payoff survives page navigations and
    reflects a completed buyer try-on. Keeps the seller->buyer->seller loop coherent."""
    from src import listing_state
    bundle = _load()
    out = []
    for gid in listing_state.saved_ids():
        v = bundle.get(gid)
        if not v:
            continue
        st = listing_state.get(gid) or {}
        out.append({**v, "garment_id": gid, "status": "listed",
                    "hero_photo": f"/api/replay/render/{gid}",
                    "platform": st.get("platform", v.get("platform")),
                    "tryon_count": st.get("tryon_count", 0),
                    "buy_url": f"https://{st.get('platform', v.get('platform'))}.com"})
    return {"listings": out}


@router.get("/starters")
def starters():
    """Pre-existing inventory listings shown on first load (historical garments:
    sundress, Disneyland sweatshirt, linen blouse), each with its own try-on count."""
    f = GOLDEN / "starters.json"
    if f.exists():
        return {"listings": json.loads(f.read_text())}
    # back-compat: single starter.json
    s = GOLDEN / "starter.json"
    return {"listings": [json.loads(s.read_text())] if s.exists() else []}


@router.get("/starter-hero/{name}")
def starter_hero(name: str):
    # name is a bundle key -> starters/<name>.jpg (reject path escapes)
    if not name.replace("_", "").isalnum():
        raise HTTPException(400, "bad id")
    p = GOLDEN / "starters" / f"{name}.jpg"
    if not p.exists():
        p = GOLDEN / "starter.jpg"   # fallback to the legacy single hero
    if not p.exists():
        raise HTTPException(404, "no starter hero")
    return FileResponse(p)


@router.get("/listing/{gid}")
def listing(gid: str):
    item = _load().get(gid)
    if not item:
        raise HTTPException(404, "unknown demo listing")
    return item


@router.get("/source")
def source():
    p = GOLDEN / "source.jpg"
    if not p.exists():
        raise HTTPException(404, "no golden bundle — run scripts/bake_golden.py")
    return FileResponse(p)


@router.get("/crop/{gid}")
def crop(gid: str):
    return _file("crops", gid)


@router.get("/render/{gid}")
def render(gid: str):
    return _file("renders", gid)


@router.get("/buyer/{gid}")
def buyer(gid: str):
    """Pre-baked buyer try-on render (garment on the stock person)."""
    return _file("buyer", gid)


def _file(sub: str, gid: str) -> FileResponse:
    # ponytail: gid is a 12-hex bundle key; reject anything else so it can't escape the dir
    if not (len(gid) <= 16 and all(c in "0123456789abcdef" for c in gid)):
        raise HTTPException(400, "bad id")
    p = GOLDEN / sub / f"{gid}.jpg"
    if not p.exists():
        raise HTTPException(404, "not in bundle")
    return FileResponse(p)
