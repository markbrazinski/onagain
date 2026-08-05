"""VTO agent — render a garment onto the base model, N attempts, Claude-ranked.

Input:  garment image path (+ identity card for category hint)
Output: {"renders": [...], "best": <path>, "ranking_reason": str}
"""

import json
from pathlib import Path

from src import config
from src.utils import vto_client
from src.utils.claude_client import ask_vision

RENDERS_PER_GARMENT = 2  # ponytail: hard unit budget — 2 renders max per garment

# profile-key -> VTO category. The ID agent's raw type is first normalized via
# identifier.TYPE_MAP (shared vocabulary) so the two never drift.
KEY_TO_CATEGORY = {
    "pants": "lower_body", "jeans": "lower_body", "shorts": "lower_body",
    "skirts": "lower_body", "trousers": "lower_body", "leggings": "lower_body",
    "joggers": "lower_body",
    "shirts": "upper_body", "blouses": "upper_body", "tops": "upper_body",
    "t-shirts": "upper_body", "tank_tops": "upper_body", "sweaters": "upper_body",
    "jackets": "upper_body", "coats": "upper_body", "hoodies": "upper_body",
    "dresses": "full_body", "jumpsuits": "full_body", "rompers": "full_body",
    "shoes": "shoes", "boots": "shoes",
}
# direct fallbacks for raw types not in the profile vocabulary
RAW_TO_CATEGORY = {
    "dress": "full_body", "gown": "full_body", "jumpsuit": "full_body", "romper": "full_body",
    "skirt": "lower_body", "sneakers": "shoes",
}

RANK_PROMPT = ("This is render #{n} of a virtual try-on of the same garment. "
               "Rate 1-10 for: garment fidelity (pattern/color/shape preserved), "
               "realistic fit/drape, absence of artifacts. "
               'Respond ONLY JSON: {{"score": <1-10>, "notes": "<one sentence>"}}')


def category_for(garment_type: str) -> str:
    """Map an ID-agent garment type to a VTO category, via the shared TYPE_MAP
    vocabulary so it stays in sync with sizing. Falls back to 'auto' only when
    truly unknown (VTO then guesses — last resort)."""
    from src.agents.identifier import TYPE_MAP
    t = str(garment_type).lower().strip()
    key = TYPE_MAP.get(t)                     # raw type -> profile key
    if key and key in KEY_TO_CATEGORY:
        return KEY_TO_CATEGORY[key]
    if t in RAW_TO_CATEGORY:
        return RAW_TO_CATEGORY[t]
    return "auto"


def render_garment(garment_path: Path, model_path: Path, out_dir: Path,
                   garment_type: str = "auto", n_renders: int = RENDERS_PER_GARMENT) -> dict:
    garment_path, model_path = Path(garment_path), Path(model_path)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    category = category_for(garment_type)

    renders = []
    for i in range(n_renders):
        out = out_dir / f"{garment_path.stem}_render{i+1}.jpg"
        try:
            res = vto_client.render(model_path, garment_path, out, category)
            renders.append({"path": str(out), "latency": round(res["latency"], 1),
                            "task_id": res["task_id"]})
        except Exception as e:
            renders.append({"path": None, "error": str(e)})

    ok = [r for r in renders if r.get("path")]
    if not ok:
        # surface the specific failure so the UI can guide the user (NSFW filter,
        # editing failure, overlap) instead of a generic "all renders failed"
        err = (renders[0].get("error") if renders else "") or ""
        if "nsfw" in err.lower():
            reason = "Render blocked by content filter — the crop caught skin/background. Try Regenerate or re-crop."
        elif "region_mismatch" in err or "editing_failed" in err:
            reason = "Couldn't fit this garment — the crop may include other items. Try Regenerate or re-photograph with spacing."
        else:
            reason = f"Render failed: {err[:120]}" if err else "All renders failed"
        return {"renders": renders, "best": None, "ranking_reason": reason}
    if len(ok) == 1:
        return {"renders": renders, "best": ok[0]["path"], "ranking_reason": "single successful render"}

    # rank with Claude: score each render independently, pick the max
    best, best_score, reason = ok[0], -1, ""
    for i, r in enumerate(ok):
        try:
            text = ask_vision(Path(r["path"]), RANK_PROMPT.format(n=i + 1), max_tokens=150).strip()
            if text.startswith("```"):
                text = text.split("```")[1].lstrip("json").strip()
            j = json.loads(text)
            r["score"] = j.get("score", 0)
            r["notes"] = j.get("notes", "")
        except Exception as e:
            r["score"], r["notes"] = 0, f"rank error: {e}"
        if r["score"] > best_score:
            best, best_score, reason = r, r["score"], r["notes"]
    return {"renders": renders, "best": best["path"],
            "ranking_reason": f"score {best_score}/10 — {reason}"}
