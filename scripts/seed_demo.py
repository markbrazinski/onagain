"""Seed the inventory with 3 previously-"listed" items for the demo.

Reuses existing VTO renders as hero photos (no API units spent). Writes to the
same local inventory store + stable listing hero paths the API serves from.

Run: python scripts/seed_demo.py
"""

import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import config
from src.utils import inventory

LISTINGS_DIR = config.WORK_DIR / "listings"
OUT = config.REPO_ROOT / "smoke-test" / "outputs"

DEMO = [
    {"garment_id": "demo_001", "title": "Vintage Levi's Denim Jacket", "brand": "Levi's",
     "price": 55, "platform": "eBay", "tryon_count": 4, "created_at": "2026-07-20",
     "hero_src": OUT / "phase1_top_on_model.jpg"},
    {"garment_id": "demo_002", "title": "Zara Cream Blazer Size M", "brand": "Zara",
     "price": 28, "platform": "Poshmark", "tryon_count": 1, "created_at": "2026-07-19",
     "hero_src": OUT / "phase1_blouse_on_model.jpg"},
    {"garment_id": "demo_003", "title": "Free People Floral Maxi Dress NWT", "brand": "Free People",
     "price": 45, "platform": "Poshmark", "tryon_count": 0, "created_at": "2026-07-21",
     "hero_src": OUT / "phase1_dress_on_model.jpg"},
]

BUY = {"poshmark": "https://poshmark.com", "ebay": "https://ebay.com",
       "depop": "https://depop.com", "vinted": "https://vinted.com"}


def main():
    LISTINGS_DIR.mkdir(parents=True, exist_ok=True)
    for d in DEMO:
        gid = d["garment_id"]
        hero_rel = None
        src = d.pop("hero_src")
        if src.exists():
            shutil.copyfile(src, LISTINGS_DIR / f"{gid}.jpg")
            hero_rel = f"/api/listing/{gid}/hero"
        else:
            print(f"  warn: hero source missing for {gid}: {src}")
        inventory.upsert({
            **d, "status": "listed", "hero_photo": hero_rel,
            "buy_url": BUY.get(d["platform"].lower(), "https://onagain.app"),
        })
        print(f"  seeded {gid}: {d['title']} ({d['tryon_count']} tried on)")
    print(f"\nInventory now has {len(inventory.all_listed())} listed items.")


if __name__ == "__main__":
    main()
