"""ID agent — produce a garment identity card from a cropped garment image.

Input:  path to a single-garment image
Output: identity card dict (see PROMPT for schema)
"""

import json
from functools import lru_cache
from pathlib import Path

from src import config
from src.utils.claude_client import ask_vision_json

PROFILE_PATH = Path(__file__).resolve().parent.parent / "config" / "profile.json"

# ID types -> profile.default_sizes keys
TYPE_MAP = {
    # Tops
    "shirt": "shirts", "blouse": "blouses", "top": "tops",
    "t-shirt": "t-shirts", "tee": "t-shirts", "tank": "tank_tops", "tank top": "tank_tops",
    "sweater": "sweaters", "hoodie": "hoodies", "cardigan": "sweaters", "sweatshirt": "sweaters",
    # Outerwear
    "jacket": "jackets", "blazer": "jackets", "coat": "coats",
    "vest": "jackets", "parka": "coats",
    # Bottoms
    "pants": "pants", "jeans": "jeans", "trousers": "trousers",
    "shorts": "shorts", "leggings": "leggings", "joggers": "joggers",
    "sweatpants": "joggers", "chinos": "pants", "slacks": "trousers",
    # Dresses/Skirts
    "dress": "dresses", "maxi": "dresses", "mini": "dresses",
    "skirt": "skirts", "jumpsuit": "jumpsuits", "romper": "rompers",
    # Other
    "activewear": "activewear", "swimsuit": "swimwear",
    "shoes": "shoes", "boots": "boots",
}


@lru_cache(maxsize=1)
def load_profile() -> dict:
    if PROFILE_PATH.exists():
        return json.loads(PROFILE_PATH.read_text())
    return {"default_sizes": {}}


def get_default_size(garment_type: str, profile: dict):
    """Return (size, 'profile_default') for a known type, else (None, None). No invented sizes."""
    key = TYPE_MAP.get(str(garment_type).lower().strip())
    sizes = profile.get("default_sizes", {})
    if key and key in sizes:
        return sizes[key], "profile_default"
    return None, None


def resolve_size(card: dict, profile: dict):
    """Priority: tag read > profile default > unknown. Sets size/size_source/size_confirmed."""
    tag = card.get("visible_size")
    if tag:
        return {"size": tag, "size_source": "tag", "size_confirmed": False}
    size, source = get_default_size(card.get("type", ""), profile)
    return {"size": size, "size_source": source, "size_confirmed": False}

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
    card.update(resolve_size(card, load_profile()))
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
    if sys.argv[1:2] == ["--size-test"]:
        p = load_profile()
        # tag wins
        assert resolve_size({"type": "blouse", "visible_size": "M"}, p) == \
            {"size": "M", "size_source": "tag", "size_confirmed": False}
        # profile default when no tag
        r = resolve_size({"type": "jeans", "visible_size": None}, p)
        assert r == {"size": "32x32", "size_source": "profile_default", "size_confirmed": False}, r
        # unknown type -> null, no invented size
        r = resolve_size({"type": "scarf", "visible_size": None}, p)
        assert r == {"size": None, "size_source": None, "size_confirmed": False}, r
        # blouse maps to profile "blouses"
        assert resolve_size({"type": "blouse", "visible_size": None}, p)["size"] == "L"
        print("size resolution OK")
        sys.exit()
    src = Path(sys.argv[1]) if len(sys.argv) > 1 else config.WORK_DIR / "crops/multi_g3_shirt.jpg"
    card = identify(src, enrich=True)
    print(json.dumps(card, indent=2))
    assert card.get("type"), "identity card missing type"
    assert isinstance(card.get("keywords"), list) and card["keywords"], "missing keywords"
    print("\nidentity card OK")
