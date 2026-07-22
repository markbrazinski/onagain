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

TYPE_TO_CATEGORY = {
    "pants": "lower_body", "jeans": "lower_body", "shorts": "lower_body",
    "skirt": "lower_body", "trousers": "lower_body", "leggings": "lower_body",
    "shirt": "upper_body", "blouse": "upper_body", "top": "upper_body",
    "t-shirt": "upper_body", "sweater": "upper_body", "jacket": "upper_body",
    "coat": "upper_body", "hoodie": "upper_body", "cardigan": "upper_body",
    "dress": "full_body", "jumpsuit": "full_body", "romper": "full_body",
    "gown": "full_body", "shoes": "shoes", "sneakers": "shoes", "boots": "shoes",
}

RANK_PROMPT = ("This is render #{n} of a virtual try-on of the same garment. "
               "Rate 1-10 for: garment fidelity (pattern/color/shape preserved), "
               "realistic fit/drape, absence of artifacts. "
               'Respond ONLY JSON: {{"score": <1-10>, "notes": "<one sentence>"}}')


def category_for(garment_type: str) -> str:
    return TYPE_TO_CATEGORY.get(str(garment_type).lower().strip(), "auto")


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
        return {"renders": renders, "best": None, "ranking_reason": "all renders failed"}
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
