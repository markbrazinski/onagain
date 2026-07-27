"""Inventory + try-on counter store.

ponytail: local JSON, not DynamoDB — the prompt explicitly allows this for dev.
Swap for DynamoDB by replacing _load/_save with table get/put when deploying.
One process, so a module-level lock is enough; no cross-process concurrency here.
"""

import json
import threading
from typing import Optional

from src import config

STORE = config.WORK_DIR / "inventory.json"
_lock = threading.Lock()


def _load() -> dict:
    if STORE.exists():
        return json.loads(STORE.read_text())
    return {}


def _save(data: dict):
    STORE.parent.mkdir(parents=True, exist_ok=True)
    STORE.write_text(json.dumps(data, indent=2))


def upsert(listing: dict):
    """Insert/replace a listing keyed by garment_id."""
    with _lock:
        data = _load()
        data[listing["garment_id"]] = listing
        _save(data)


def get(garment_id: str) -> Optional[dict]:
    return _load().get(garment_id)


def all_listed() -> list:
    """Listings shown on the inventory dashboard, newest first."""
    items = [v for v in _load().values() if v.get("status") == "listed"]
    return sorted(items, key=lambda x: x.get("created_at", ""), reverse=True)


def increment_tryon(garment_id: str) -> int:
    """Bump the try-on counter; returns the new count (0 if unknown garment)."""
    with _lock:
        data = _load()
        item = data.get(garment_id)
        if not item:
            return 0
        item["tryon_count"] = item.get("tryon_count", 0) + 1
        _save(data)
        return item["tryon_count"]
