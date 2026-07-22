"""Channel agent — recommend the best resale platform for a garment.

Input:  identity card + comps dict
Output: {"primary", "primary_reasoning", "secondary", "secondary_reasoning",
         "avoid", "avoid_reasoning", "flags": [...]}

ponytail: pure rules, no Claude call — the heuristics ARE the spec; add an LLM
tiebreaker only if rule collisions show up in real use.
"""

import json

LUXURY = {"gucci", "prada", "chanel", "louis vuitton", "burberry", "hermes", "hermès",
          "dior", "fendi", "balenciaga", "saint laurent", "ysl", "bottega veneta",
          "celine", "givenchy", "valentino", "versace", "loewe"}
ATHLETIC = {"nike", "adidas", "lululemon", "under armour", "puma", "new balance",
            "athleta", "gymshark", "on running", "hoka"}
EURO = {"zara", "mango", "h&m", "cos", "uniqlo", "massimo dutti", "asos", "monki",
        "& other stories", "arket", "bershka", "stradivarius", "primark"}
AESTHETIC_TAGS = {"y2k", "vintage", "streetwear", "retro", "grunge", "cottagecore",
                  "coquette", "boho"}


def recommend(card: dict, comps: dict) -> dict:
    brand = str(card.get("brand") or "").lower().strip()
    dept = str(card.get("department") or "").lower()
    cond = str(card.get("condition_estimate") or "").lower()
    keywords = {str(k).lower() for k in (card.get("keywords") or [])}
    mid = comps.get("suggested_mid")
    ebay_comps = comps.get("comp_count", 0) >= 3

    flags = []
    primary = secondary = avoid = None
    p_why = s_why = a_why = ""

    if brand in LUXURY:
        primary, p_why = "therealreal", "Luxury brand — authentication and premium buyers matter most."
        secondary, s_why = "vestiaire", "Second luxury-focused marketplace with authentication."
        avoid, a_why = "depop", "Luxury pricing exceeds Depop's budget-conscious audience."
    elif keywords & AESTHETIC_TAGS and (mid is None or mid < 50):
        primary, p_why = "depop", "Aesthetic-driven item under $50 — Gen Z audience, streetwear/vintage focus."
        secondary, s_why = "poshmark", "Backup reach for fashion buyers."
    elif dept == "women" and mid is not None and 15 <= mid <= 100:
        primary, p_why = "poshmark", "Mid-range women's fashion — Poshmark's core demographic."
        secondary, s_why = ("ebay", "Broad reach with validated comp data.") if ebay_comps \
            else ("vinted", "Growing women's resale audience.")
        if mid > 60:
            avoid, a_why = "depop", "Price point high for Depop's primarily budget-conscious audience."
    elif brand in ATHLETIC:
        primary, p_why = "ebay", "Strong athletic wear market with comp-driven buyers."
        secondary, s_why = "mercari", "Secondary athletic resale channel."
    elif mid is not None and mid < 10:
        primary, p_why = "depop", "Low-cost items move faster with a younger audience."
        flags.append("Consider bundling low-value items")
    elif not brand:
        primary, p_why = "depop", "Unbranded — aesthetic-driven discovery beats brand search."
        flags.append("Consider bundling unbranded items")
        secondary, s_why = "vinted", "Simple listing flow for unbranded basics."
    else:
        primary, p_why = "ebay", "Broadest reach, strongest comp data, fits most categories."
        secondary, s_why = "poshmark", "Secondary fashion-focused audience."

    if cond in ("new", "new with tags", "nwt") and primary != "poshmark":
        if not secondary or secondary != "poshmark":
            secondary, s_why = "poshmark", "NWT badge/filter is a major discovery tool on Poshmark."
        flags.append("NWT — flag prominently")
    if brand in EURO and secondary != "vinted" and primary != "vinted":
        flags.append("European brand — Vinted worth cross-listing")

    return {"primary": primary, "primary_reasoning": p_why,
            "secondary": secondary, "secondary_reasoning": s_why,
            "avoid": avoid, "avoid_reasoning": a_why,
            "flags": flags}


if __name__ == "__main__":
    tests = [
        ({"brand": "Gucci", "department": "women"}, {"suggested_mid": 400, "comp_count": 4}, "therealreal"),
        ({"brand": "Zara", "department": "women"}, {"suggested_mid": 24, "comp_count": 3}, "poshmark"),
        ({"brand": "Nike", "department": "men"}, {"suggested_mid": 40, "comp_count": 2}, "ebay"),
        ({"brand": None, "department": "women", "keywords": ["y2k", "top"]}, {"suggested_mid": 12, "comp_count": 0}, "depop"),
        ({"brand": "Carhartt", "department": "men"}, {"suggested_mid": 55, "comp_count": 5}, "ebay"),
    ]
    for card, comps, expect in tests:
        r = recommend(card, comps)
        status = "OK" if r["primary"] == expect else f"UNEXPECTED (got {r['primary']})"
        print(f"{card.get('brand') or 'unbranded'}: {r['primary']} -> {status}")
        assert r["primary"] == expect
    print(json.dumps(recommend(tests[1][0], tests[1][1]), indent=2))
    print("\nchannel agent OK")
