"""OnAgain API — FastAPI layer over the agent pipeline for the seller UI.

Run: uvicorn src.api:app --reload --port 8000
UI served at http://localhost:8000/
"""

import shutil
import threading
import uuid
from pathlib import Path
from typing import List, Optional

from fastapi import FastAPI, UploadFile, HTTPException, Form, File
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from src import config
from src.agents import channel, comps, gate, identifier, parser, vto
from src.agents import copy as copy_agent
from src.utils import inventory

app = FastAPI(title="OnAgain")

BATCHES_DIR = config.WORK_DIR / "batches"
LISTINGS_DIR = config.WORK_DIR / "listings"   # stable per-listing hero photos + json
ASSETS_DIR = config.REPO_ROOT / "assets" / "bases"
BATCHES: dict = {}  # ponytail: in-memory batch state; move to sqlite when multi-user matters

# ponytail: real buy URLs need per-seller marketplace accounts; demo links to platform home
BUY_URLS = {"poshmark": "https://poshmark.com", "depop": "https://depop.com",
            "ebay": "https://ebay.com", "vinted": "https://vinted.com"}

STEPS = ["identify", "vto", "price", "copy"]


# ---------------------------------------------------------------- parse

@app.post("/api/parse")
async def parse_photo(photo: UploadFile):
    batch_id = uuid.uuid4().hex[:10]
    bdir = BATCHES_DIR / batch_id
    bdir.mkdir(parents=True, exist_ok=True)
    suffix = Path(photo.filename or "upload.jpg").suffix or ".jpg"
    src = bdir / f"source{suffix}"
    src.write_bytes(await photo.read())

    g = gate.check(src)
    if not g["pass"]:
        return {"batch_id": batch_id, "gate": g, "garments": []}

    garments = parser.parse_garments(src, work_dir=bdir / "crops")
    garments = [gm for gm in garments if gm.get("crop_path")]
    BATCHES[batch_id] = {
        "id": batch_id, "dir": str(bdir), "source": str(src),
        "garments": garments, "status": "parsed", "listings": [],
        "progress": {},
    }
    return {
        "batch_id": batch_id, "gate": g,
        "source_url": f"/api/batch/{batch_id}/source",
        "garments": [{
            "garment_number": gm["garment_number"], "type": gm.get("type"),
            "dominant_color": gm.get("dominant_color"), "brand_text": gm.get("brand_text"),
            "box_pct": gm.get("box_pct"), "refine_issue": (gm.get("refine") or {}).get("issue"),
            "crop_url": f"/api/batch/{batch_id}/crop/{gm['garment_number']}",
        } for gm in garments],
    }


# ---------------------------------------------------------------- generate

class GenerateReq(BaseModel):
    base: str = "mannequin"          # asset name in assets/bases/
    garment_numbers: Optional[List[int]] = None  # None = all


def _base_photo(name: str) -> Path:
    p = ASSETS_DIR / f"{name}.jpg"
    if not p.exists():
        raise HTTPException(400, f"unknown base '{name}'; available: "
                            + ", ".join(f.stem for f in ASSETS_DIR.glob("*.jpg")))
    return p


def _process_garment(batch: dict, gm: dict, base: Path):
    gid = gm["garment_number"]
    prog = batch["progress"][gid]
    crop = Path(gm["crop_path"])
    try:
        prog["identify"] = "active"
        gm["identity"] = identifier.identify(crop)
        prog["identify"] = "done"
    except Exception as e:
        gm["identity"] = {"error": str(e), "type": gm.get("type")}
        prog["identify"] = "failed"

    try:
        prog["vto"] = "active"
        gtype = gm["identity"].get("type") or gm.get("type") or "auto"
        gm["vto"] = vto.render_garment(crop, base, Path(batch["dir"]) / "renders", gtype)
        prog["vto"] = "done" if gm["vto"]["best"] else "failed"
    except Exception as e:
        gm["vto"] = {"renders": [], "best": None, "ranking_reason": str(e)}
        prog["vto"] = "failed"

    try:
        prog["price"] = "active"
        gm["comps"] = comps.research(gm["identity"])
        prog["price"] = "done"
    except Exception as e:
        gm["comps"] = {"comps": [], "suggested_low": None, "suggested_mid": None,
                       "suggested_high": None, "comp_count": 0, "reasoning": str(e)}
        prog["price"] = "failed"

    try:
        prog["copy"] = "active"
        gm["channel"] = channel.recommend(gm["identity"], gm["comps"])
        gm["copy"] = copy_agent.generate(gm["identity"], gm["comps"], gm["channel"]["primary"])
        prog["copy"] = "done"
    except Exception as e:
        gm["channel"] = gm.get("channel") or {"primary": "ebay", "primary_reasoning": ""}
        gm["copy"] = {"variants": [], "flags": {}, "error": str(e)}
        prog["copy"] = "failed"


def _run_batch(batch_id: str, base: Path, numbers):
    batch = BATCHES[batch_id]
    batch["status"] = "processing"
    targets = [gm for gm in batch["garments"]
               if not numbers or gm["garment_number"] in numbers]
    # garments are independent — process them concurrently
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=min(4, len(targets) or 1)) as ex:
        list(ex.map(lambda gm: _process_garment(batch, gm, base), targets))
    batch["status"] = "done"


@app.post("/api/batch/{batch_id}/generate")
def generate(batch_id: str, req: GenerateReq):
    batch = BATCHES.get(batch_id)
    if not batch:
        raise HTTPException(404, "unknown batch")
    base = _base_photo(req.base)
    targets = req.garment_numbers
    for gm in batch["garments"]:
        if targets and gm["garment_number"] not in targets:
            continue
        batch["progress"][gm["garment_number"]] = {s: "wait" for s in STEPS}
    threading.Thread(target=_run_batch, args=(batch_id, base, targets), daemon=True).start()
    return {"status": "started", "base": req.base}


# ---------------------------------------------------------------- status / assets

@app.get("/api/batch/{batch_id}")
def batch_status(batch_id: str):
    batch = BATCHES.get(batch_id)
    if not batch:
        raise HTTPException(404, "unknown batch")
    out = {"batch_id": batch_id, "status": batch["status"], "garments": []}
    for gm in batch["garments"]:
        n = gm["garment_number"]
        entry = {
            "garment_number": n, "type": gm.get("type"),
            "progress": batch["progress"].get(n, {}),
            "crop_url": f"/api/batch/{batch_id}/crop/{n}",
        }
        if gm.get("identity"):
            entry["identity"] = gm["identity"]
        if gm.get("vto"):
            entry["vto"] = {
                "best_url": f"/api/batch/{batch_id}/render/{n}" if gm["vto"].get("best") else None,
                "ranking_reason": gm["vto"].get("ranking_reason"),
            }
        if gm.get("comps"):
            entry["pricing"] = {k: gm["comps"].get(k) for k in
                                ("suggested_low", "suggested_mid", "suggested_high",
                                 "comp_count", "reasoning")}
        if gm.get("channel"):
            entry["channel"] = gm["channel"]
        if gm.get("copy"):
            entry["copy"] = gm["copy"]
        out["garments"].append(entry)
    return out


class RegenReq(BaseModel):
    facts: dict          # edited identity-card fields (merged over existing)
    platform: str


@app.post("/api/batch/{batch_id}/garment/{n}/regen_copy")
def regen_copy(batch_id: str, n: int, req: RegenReq):
    batch = BATCHES.get(batch_id)
    if not batch:
        raise HTTPException(404, "unknown batch")
    gm = next((g for g in batch["garments"] if g["garment_number"] == n), None)
    if not gm or not gm.get("identity"):
        raise HTTPException(404, "unknown garment")
    gm["identity"].update({k: v for k, v in req.facts.items() if v is not None})
    gm["copy"] = copy_agent.generate(gm["identity"], gm.get("comps") or {}, req.platform)
    if req.platform:
        gm["channel"] = gm.get("channel") or {}
        gm["channel"]["primary"] = req.platform
    return {"copy": gm["copy"], "identity": gm["identity"]}


class RegenImageReq(BaseModel):
    base: str = "mannequin"


@app.post("/api/batch/{batch_id}/garment/{n}/regen_image")
def regen_image(batch_id: str, n: int, req: RegenImageReq):
    """Re-run VTO for one garment (new render, may differ on fit/drape failures)."""
    batch = BATCHES.get(batch_id)
    if not batch:
        raise HTTPException(404, "unknown batch")
    gm = next((g for g in batch["garments"] if g["garment_number"] == n), None)
    if not gm or not gm.get("crop_path"):
        raise HTTPException(404, "unknown garment")
    base = _base_photo(req.base)
    gtype = (gm.get("identity") or {}).get("type") or gm.get("type") or "auto"
    batch["progress"].setdefault(n, {})["vto"] = "active"
    gm["vto"] = vto.render_garment(Path(gm["crop_path"]), base,
                                   Path(batch["dir"]) / "renders", gtype)
    batch["progress"][n]["vto"] = "done" if gm["vto"]["best"] else "failed"
    return {"vto": {"best_url": f"/api/batch/{batch_id}/render/{n}" if gm["vto"].get("best") else None,
                    "ranking_reason": gm["vto"].get("ranking_reason")}}


@app.get("/api/bases")
def list_bases():
    return {"bases": [{"name": f.stem, "url": f"/api/base/{f.stem}"}
                      for f in sorted(ASSETS_DIR.glob("*.jpg"))]}


@app.get("/api/base/{name}")
def base_image(name: str):
    return FileResponse(_base_photo(name))


@app.get("/api/batch/{batch_id}/source")
def source_image(batch_id: str):
    batch = BATCHES.get(batch_id)
    if not batch:
        raise HTTPException(404, "unknown batch")
    return FileResponse(batch["source"])


@app.get("/api/batch/{batch_id}/crop/{n}")
def crop_image(batch_id: str, n: int):
    batch = BATCHES.get(batch_id)
    gm = batch and next((g for g in batch["garments"] if g["garment_number"] == n), None)
    if not gm or not gm.get("crop_path"):
        raise HTTPException(404, "no crop")
    return FileResponse(gm["crop_path"])


@app.get("/api/batch/{batch_id}/render/{n}")
def render_image(batch_id: str, n: int, download: bool = False):
    batch = BATCHES.get(batch_id)
    gm = batch and next((g for g in batch["garments"] if g["garment_number"] == n), None)
    if not gm or not (gm.get("vto") or {}).get("best"):
        raise HTTPException(404, "no render")
    headers = {"Content-Disposition": f'attachment; filename="onagain_{n}.jpg"'} if download else None
    return FileResponse(gm["vto"]["best"], headers=headers)


# ---------------------------------------------------------------- approve + inventory

class ApproveReq(BaseModel):
    garment_id: str
    title: str
    price: str
    platform: str
    brand: Optional[str] = None
    created_at: Optional[str] = None   # caller (or seed) supplies; ISO date


@app.post("/api/batch/{batch_id}/garment/{n}/approve")
def approve(batch_id: str, n: int, req: ApproveReq):
    """Persist a finished listing to inventory + write the buyer-page garment JSON."""
    batch = BATCHES.get(batch_id)
    gm = batch and next((g for g in batch["garments"] if g["garment_number"] == n), None)
    if not gm:
        raise HTTPException(404, "unknown garment")
    LISTINGS_DIR.mkdir(parents=True, exist_ok=True)
    # copy hero render to a stable location keyed by garment_id
    hero_rel = None
    best = (gm.get("vto") or {}).get("best")
    if best and Path(best).exists():
        hero = LISTINGS_DIR / f"{req.garment_id}.jpg"
        shutil.copyfile(best, hero)
        hero_rel = f"/api/listing/{req.garment_id}/hero"
    listing = {
        "garment_id": req.garment_id, "title": req.title, "price": req.price,
        "brand": req.brand, "platform": req.platform, "status": "listed",
        "tryon_count": 0, "hero_photo": hero_rel,
        "buy_url": BUY_URLS.get(req.platform.lower(), "https://onagain.app"),
        "created_at": req.created_at or _today(),
    }
    inventory.upsert(listing)
    return {"status": "listed", "garment_id": req.garment_id,
            "tryon_url": f"/tryon/{req.garment_id}"}


def _today():
    # ponytail: date only, avoids importing datetime just for a demo stamp
    import datetime
    return datetime.date.today().isoformat()


@app.get("/api/inventory")
def get_inventory():
    return {"listings": inventory.all_listed()}


@app.get("/api/listing/{garment_id}")
def get_listing(garment_id: str):
    item = inventory.get(garment_id)
    if not item:
        raise HTTPException(404, "unknown listing")
    return item


@app.get("/api/listing/{garment_id}/hero")
def listing_hero(garment_id: str):
    p = LISTINGS_DIR / f"{garment_id}.jpg"
    if not p.exists():
        raise HTTPException(404, "no hero photo")
    return FileResponse(p)


# ---------------------------------------------------------------- buyer try-on

@app.post("/api/tryon")
async def tryon(garment_id: str = Form(...), selfie: UploadFile = File(...)):
    """Anonymous, ephemeral buyer try-on. Selfie is deleted immediately after VTO.

    Privacy contract (enforced here, S3-lifecycle-equivalent locally):
      - no cookies, no user id, nothing about the buyer persisted
      - selfie written to a temp path, deleted right after render (or on failure)
      - only the render is kept (24h-equivalent; local file)
    """
    item = inventory.get(garment_id) if garment_id else None
    if not item:
        raise HTTPException(400, "invalid garment_id")
    if not selfie:
        raise HTTPException(400, "no selfie")
    data = await selfie.read()
    if len(data) > 10 * 1024 * 1024:
        raise HTTPException(400, "image too large (max 10MB)")
    ct = (selfie.content_type or "").lower()
    if "jpeg" not in ct and "jpg" not in ct and "png" not in ct:
        raise HTTPException(400, "selfie must be JPEG or PNG")

    tryon_dir = config.WORK_DIR / "tryon"
    tryon_dir.mkdir(parents=True, exist_ok=True)
    selfie_path = tryon_dir / f"_selfie_{uuid.uuid4().hex}{Path(selfie.filename or '.jpg').suffix}"
    selfie_path.write_bytes(data)

    garment_hero = LISTINGS_DIR / f"{garment_id}.jpg"
    if not garment_hero.exists():
        selfie_path.unlink(missing_ok=True)
        raise HTTPException(400, "listing has no garment image")

    render_path = tryon_dir / f"{garment_id}_{uuid.uuid4().hex}.jpg"
    try:
        # buyer selfie = person, garment hero = reference garment
        res = vto.render_garment(garment_hero, selfie_path, tryon_dir, "auto", n_renders=1)
        # render_garment writes its own file; move best to our stable render_path
        if not res.get("best"):
            raise RuntimeError(res.get("ranking_reason") or "render failed")
        shutil.move(res["best"], render_path)
    except Exception as e:
        selfie_path.unlink(missing_ok=True)   # delete selfie even on failure
        raise HTTPException(500, "We couldn't generate a try-on preview this time.")
    finally:
        selfie_path.unlink(missing_ok=True)   # selfie gone regardless of outcome

    new_count = inventory.increment_tryon(garment_id)
    return {
        "render_url": f"/api/tryon/render/{render_path.name}",
        "garment_title": item.get("title"), "garment_price": item.get("price"),
        "buy_url": item.get("buy_url"), "platform": item.get("platform"),
        "tryon_count": new_count,
    }


@app.get("/api/tryon/render/{name}")
def tryon_render(name: str):
    p = config.WORK_DIR / "tryon" / name
    if not p.exists() or ".." in name or "/" in name:
        raise HTTPException(404, "render expired or not found")
    return FileResponse(p)


@app.get("/tryon/{garment_id}", response_class=HTMLResponse)
def tryon_page(garment_id: str):
    """Serve the standalone buyer try-on page (single self-contained HTML)."""
    html = (config.REPO_ROOT / "web" / "tryon.html").read_text()
    return HTMLResponse(html.replace("__GARMENT_ID__", garment_id))


# static UI — mounted last so /api/* and named routes win
app.mount("/", StaticFiles(directory=str(config.REPO_ROOT / "web"), html=True), name="web")
