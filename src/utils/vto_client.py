"""YouCam VTO client — file upload + cloth task + poll, per V2 S2S docs.

Same API pattern proven in smoke-test/run_smoke_test.py:
  POST /file/cloth  -> file_id + presigned PUT (bytes uploaded separately)
  POST /task/cloth  -> task_id  (src=person, ref=garment, garment_category)
  GET  /task/cloth/{id} -> task_status, results.url
"""

import json
import time
from pathlib import Path

import requests

from src import config

POLL_INTERVAL = 3
POLL_TIMEOUT = 180


def _headers():
    return {"Authorization": f"Bearer {config.YOUCAM_API_KEY}",
            "Content-Type": "application/json"}


def upload_file(path: Path) -> str:
    data = path.read_bytes()
    ct = "image/png" if path.suffix.lower() == ".png" else "image/jpg"
    r = requests.post(
        f"{config.YOUCAM_API_BASE}/file/cloth",
        headers=_headers(),
        json={"files": [{"content_type": ct, "file_name": path.name, "file_size": len(data)}]},
        timeout=30,
    )
    r.raise_for_status()
    finfo = r.json()["data"]["files"][0]
    for req in finfo["requests"]:
        put = requests.request(req.get("method", "PUT"), req["url"],
                               headers=req.get("headers", {}), data=data, timeout=60)
        put.raise_for_status()
    return finfo["file_id"]


def run_vto(person_file_id: str, garment_file_id: str, category: str = "auto") -> dict:
    """Create cloth task and poll to completion. Returns {url, latency, task_id}."""
    t0 = time.time()
    r = requests.post(
        f"{config.YOUCAM_API_BASE}/task/cloth",
        headers=_headers(),
        json={"src_file_id": person_file_id, "ref_file_id": garment_file_id,
              "garment_category": category},
        timeout=30,
    )
    r.raise_for_status()
    task_id = r.json()["data"]["task_id"]
    while True:
        if time.time() - t0 > POLL_TIMEOUT:
            raise TimeoutError(f"task {task_id} exceeded {POLL_TIMEOUT}s")
        time.sleep(POLL_INTERVAL)
        p = requests.get(f"{config.YOUCAM_API_BASE}/task/cloth/{task_id}",
                         headers=_headers(), timeout=30)
        p.raise_for_status()
        d = p.json()["data"]
        if d["task_status"] == "success":
            results = d.get("results")
            url = results.get("url") if isinstance(results, dict) else _first_url(results)
            return {"url": url, "latency": time.time() - t0, "task_id": task_id}
        if d["task_status"] == "error":
            raise RuntimeError(f"task {task_id} failed: {json.dumps(d.get('error'))}")


def _first_url(obj):
    if isinstance(obj, dict):
        if "url" in obj:
            return obj["url"]
        for v in obj.values():
            u = _first_url(v)
            if u:
                return u
    elif isinstance(obj, list):
        for v in obj:
            u = _first_url(v)
            if u:
                return u
    return None


def render(person_path: Path, garment_path: Path, out_path: Path, category: str = "auto") -> dict:
    """Full pipeline: upload both images, run VTO, download render to out_path."""
    person_id = upload_file(person_path)
    garment_id = upload_file(garment_path)
    res = run_vto(person_id, garment_id, category)
    img = requests.get(res["url"], timeout=60)
    img.raise_for_status()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes(img.content)
    res["output"] = str(out_path)
    return res
