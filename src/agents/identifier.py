"""ID agent — produce a garment identity card from a cropped garment image.

Input:  path to a single-garment image
Output: identity card dict (see PROMPT for schema)
"""

import json
from pathlib import Path

from src import config
from src.utils.claude_client import ask_vision_json

PROMPT = """You are a resale listing expert. Identify this garment precisely. Return ONLY valid JSON:
{
  "type": "<garment type, e.g. jeans, blouse, dress>",
  "subtype": "<cut/style, e.g. straight, A-line, crewneck, or null>",
  "brand": "<brand name if visible on tags/logos, else null>",
  "style_name": "<specific product/style name if identifiable, else null>",
  "color": "<primary color description>",
  "color_secondary": "<secondary color or null>",
  "material_estimate": "<best guess at material, e.g. cotton/denim>",
  "condition_estimate": "<new|excellent|good|fair|poor>",
  "condition_notes": "<visible wear, stains, pilling, or 'No visible wear'>",
  "visible_size": "<size from tag if readable, else null>",
  "department": "<women|men|unisex|kids>",
  "keywords": ["<6-10 search keywords a resale buyer would use>"]
}
Base brand/style ONLY on visible evidence (tags, logos, distinctive design). Use null when not visible."""


def identify(crop_path: Path, enrich: bool = False) -> dict:
    card = ask_vision_json(Path(crop_path), PROMPT)
    card["source_image"] = str(crop_path)
    if enrich and card.get("brand") and card.get("style_name"):
        card["retail_info"] = _enrich(card["brand"], card["style_name"])
    return card


def _enrich(brand: str, style_name: str):
    """Best-effort retail lookup via DuckDuckGo instant answers. Returns None on any failure."""
    # ponytail: free no-key search endpoint; swap for a real search API when pricing matters
    import requests
    try:
        r = requests.get("https://api.duckduckgo.com/",
                         params={"q": f"{brand} {style_name}", "format": "json", "no_html": 1},
                         timeout=10)
        d = r.json()
        abstract = d.get("AbstractText") or None
        return {"query": f"{brand} {style_name}", "abstract": abstract,
                "source_url": d.get("AbstractURL") or None} if abstract else None
    except Exception:
        return None


if __name__ == "__main__":
    import sys
    src = Path(sys.argv[1]) if len(sys.argv) > 1 else config.WORK_DIR / "crops/multi_g3_shirt.jpg"
    card = identify(src, enrich=True)
    print(json.dumps(card, indent=2))
    assert card.get("type"), "identity card missing type"
    assert isinstance(card.get("keywords"), list) and card["keywords"], "missing keywords"
    print("\nidentity card OK")
