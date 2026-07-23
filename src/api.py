"""OnAgain API — FastAPI layer over the agent pipeline for the seller UI.

Run: uvicorn src.api:app --reload --port 8000
UI served at http://localhost:8000/
"""

import threading
import uuid
from pathlib import Path
from typing import List, Optional

from fastapi import FastAPI, UploadFile, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from src import config
from src.agents import channel, comps, gate, identifier, parser, vto
from src.agents import copy as copy_agent

app = FastAPI(title="OnAgain")

BATCHES_DIR = config.WORK_DIR / "batches"
ASSETS_DIR = config.REPO_ROOT / "assets" / "bases"
BATCHES: dict = {}  # ponytail: in-memory batch state; move to sqlite when multi-user matters

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
    for gm in batch["garments"]:
        if numbers and gm["garment_number"] not in numbers:
            continue
        _process_garment(batch, gm, base)
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
def render_image(batch_id: str, n: int):
    batch = BATCHES.get(batch_id)
    gm = batch and next((g for g in batch["garments"] if g["garment_number"] == n), None)
    if not gm or not (gm.get("vto") or {}).get("best"):
        raise HTTPException(404, "no render")
    return FileResponse(gm["vto"]["best"])


# static UI — mounted last so /api/* wins
app.mount("/", StaticFiles(directory=str(config.REPO_ROOT / "web"), html=True), name="web")
