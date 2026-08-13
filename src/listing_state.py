"""Shared listing state — the single source of truth that keeps the seller, the buyer
try-on page, the result CTA, and the try-on counter consistent for one canonical listing.

The hero loop requires that when the seller switches the green cardigan from Poshmark to
Depop, the buyer's landing page, result CTA, and marketplace metadata all change too, and
that a completed try-on increments THIS listing's counter exactly once. Both live and
replay listings resolve through here so the two modes share identical state transitions.

ponytail: in-memory dict keyed by canonical listing id, one module-level lock. It seeds
lazily from the golden bundle / inventory so a buyer link works even before the seller has
touched the listing. Swap _load/_save for a table when multi-user matters.
"""

import threading
from typing import Optional

_lock = threading.Lock()
_STATE: dict = {}   # canonical_id -> {platform, price, title, hero_url, size, tryon_count, seen_tokens:set}


def _seed(canonical_id: str) -> Optional[dict]:
    """Lazily build state for a listing from the golden bundle, historical starters, then
    live inventory."""
    from src.demo_mode import _load as golden_load, GOLDEN
    g = golden_load().get(canonical_id)
    if g:
        return {"platform": g.get("platform", "depop"), "price": g.get("price"),
                "title": g.get("title"), "size": g.get("size"),
                "hero_url": f"/api/replay/render/{canonical_id}",
                "buyer_url": f"/api/replay/buyer/{canonical_id}",
                "origin": "replay", "tryon_count": 0, "seen_tokens": set()}
    # historical starter listings (sundress, Disneyland sweatshirt, linen blouse)
    import json
    sf = GOLDEN / "starters.json"
    if sf.exists():
        for s in json.loads(sf.read_text()):
            if s.get("garment_id") == canonical_id:
                return {"platform": s.get("platform", "poshmark"), "price": s.get("price"),
                        "title": s.get("title"), "size": s.get("size"),
                        "hero_url": f"/api/replay/starter-hero/{canonical_id}",
                        "buyer_url": None,   # no pre-baked buyer render for starters
                        "origin": "starter", "tryon_count": s.get("tryon_count", 0),
                        "seen_tokens": set()}
    from src.utils import inventory
    inv = inventory.get(canonical_id)
    if inv:
        return {"platform": inv.get("platform", "depop"), "price": inv.get("price"),
                "title": inv.get("title"), "size": inv.get("size"),
                "hero_url": inv.get("hero_photo"), "buyer_url": None,
                "origin": "live", "tryon_count": inv.get("tryon_count", 0), "seen_tokens": set()}
    return None


def get(canonical_id: str) -> Optional[dict]:
    """Current state for a listing (seeds on first access). None if unknown."""
    with _lock:
        st = _STATE.get(canonical_id)
        if st is None:
            st = _seed(canonical_id)
            if st is not None:
                _STATE[canonical_id] = st
        return dict(st) if st else None   # copy without the internal seen_tokens set


def set_marketplace(canonical_id: str, platform: str, price=None):
    """Seller changed the listing's marketplace (and optionally price). One write that
    every downstream surface (buyer link, CTA, inventory) reads back. Marks the listing
    'saved' so it appears in inventory across navigations (survives page reloads)."""
    with _lock:
        st = _STATE.get(canonical_id) or _seed(canonical_id)
        if st is None:
            return None
        st["platform"] = platform
        st["saved"] = True
        if price is not None:
            st["price"] = price
        _STATE[canonical_id] = st
        return dict(st)


def saved_ids() -> list:
    """Canonical ids the seller has saved this session (for the inventory list)."""
    with _lock:
        return [k for k, v in _STATE.items() if v.get("saved")]


def register_tryon(canonical_id: str, token: str) -> Optional[int]:
    """Idempotently record ONE completed try-on. `token` de-dupes refreshes/retries of the
    same completion — a repeated token does not inflate the count. Returns the new count,
    or None for an unknown listing. Call this ONLY after a generation actually succeeded."""
    with _lock:
        st = _STATE.get(canonical_id) or _seed(canonical_id)
        if st is None:
            return None
        _STATE[canonical_id] = st
        if token and token in st["seen_tokens"]:
            return st["tryon_count"]          # duplicate completion — no double count
        if token:
            st["seen_tokens"].add(token)
        st["tryon_count"] += 1
        # mirror into the durable inventory store if this listing lives there
        _mirror_inventory(canonical_id, st["tryon_count"])
        return st["tryon_count"]


def _mirror_inventory(canonical_id: str, count: int):
    from src.utils import inventory
    if inventory.get(canonical_id):
        inventory.set_tryon(canonical_id, count)


def reset():
    """Test hook — clear all in-memory listing state."""
    with _lock:
        _STATE.clear()
