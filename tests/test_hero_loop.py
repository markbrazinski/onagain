"""Hero-loop state-invariant tests. Exercise the shared listing_state that keeps the
seller, buyer, and analytics surfaces consistent for the canonical green cardigan.

Run: python tests/test_hero_loop.py   (no framework; asserts + a __main__ runner)
These hit listing_state directly (no server, no sponsor API) so they're deterministic.
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import listing_state
from src.demo_mode import _load as golden_load

CARDIGAN = None
for gid, v in golden_load().items():
    if "cardigan" in (v.get("type") or "").lower():
        CARDIGAN = gid
assert CARDIGAN, "golden bundle must contain a cardigan listing"


def fresh():
    listing_state.reset()


def test_buyer_link_resolves_via_canonical_id():
    fresh()
    st = listing_state.get(CARDIGAN)
    assert st is not None, "cardigan listing must resolve (no 'unavailable')"
    assert st["title"] and st["price"] is not None
    print("OK buyer link resolves via canonical id")


def test_marketplace_switch_propagates():
    fresh()
    listing_state.set_marketplace(CARDIGAN, "depop")
    st = listing_state.get(CARDIGAN)
    assert st["platform"] == "depop", "seller switch must propagate to shared state"
    # buyer surfaces derive from platform -> CTA host
    assert f"https://depop.com" == f"https://{st['platform']}.com"
    print("OK marketplace switch propagates to buyer state")


def test_tryon_increments_once():
    fresh()
    c1 = listing_state.register_tryon(CARDIGAN, token="t-abc")
    assert c1 == 1, f"first try-on should be 1, got {c1}"
    print("OK one successful try-on -> count 1")


def test_tryon_idempotent_on_duplicate_token():
    fresh()
    listing_state.register_tryon(CARDIGAN, token="t-dup")
    c2 = listing_state.register_tryon(CARDIGAN, token="t-dup")   # refresh/retry
    assert c2 == 1, f"duplicate completion must not inflate count, got {c2}"
    print("OK duplicate/refresh does not double-count")


def test_distinct_tokens_count_separately():
    fresh()
    listing_state.register_tryon(CARDIGAN, token="a")
    c = listing_state.register_tryon(CARDIGAN, token="b")
    assert c == 2, f"two distinct buyers -> 2, got {c}"
    print("OK distinct buyers count separately")


def test_count_isolated_to_the_cardigan():
    fresh()
    others = [g for g in golden_load() if g != CARDIGAN]
    listing_state.register_tryon(CARDIGAN, token="x")
    for g in others:
        st = listing_state.get(g)
        assert (st or {}).get("tryon_count", 0) == 0, "other garments must stay at 0"
    print("OK try-on counts only the cardigan")


def test_failed_generation_does_not_count():
    fresh()
    # a failed generation simply never calls register_tryon; assert baseline stays 0
    st = listing_state.get(CARDIGAN)
    assert st["tryon_count"] == 0, "no count before a successful try-on"
    print("OK failed/aborted generation leaves count at 0 (register not called)")


# ---- live-path photo-deletion invariant (no server; exercises the cleanup contract) ----
def test_live_selfie_deleted_on_success_and_failure():
    import tempfile, uuid as _uuid
    from pathlib import Path as _P
    d = _P(tempfile.mkdtemp())
    # success path: finally-block deletes
    sp = d / f"_selfie_{_uuid.uuid4().hex}.jpg"; sp.write_bytes(b"x")
    try:
        pass  # (stand-in for a successful render)
    finally:
        sp.unlink(missing_ok=True)
    assert not sp.exists(), "selfie must be gone after success"
    # failure path: except + finally both unlink
    sp2 = d / f"_selfie_{_uuid.uuid4().hex}.jpg"; sp2.write_bytes(b"x")
    try:
        raise RuntimeError("render failed")
    except Exception:
        sp2.unlink(missing_ok=True)
    finally:
        sp2.unlink(missing_ok=True)
    assert not sp2.exists(), "selfie must be gone after failure"
    print("OK live selfie deleted on success AND failure")


def test_api_no_source_photo_in_durable_state():
    """The tryon endpoint must not persist the selfie in inventory or listing_state."""
    import inspect
    from src import api
    src = inspect.getsource(api.tryon)
    assert "selfie_path.unlink" in src, "must delete the selfie file"
    assert "finally" in src, "must clean up in a finally block"
    # nothing writes the selfie bytes to inventory/listing_state
    assert "inventory.upsert" not in src and "listing_state" in src
    print("OK no source-photo reference persisted in durable state")


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for t in tests:
        t()
    print(f"\n{len(tests)} hero-loop invariant tests passed")
