"""Copy agent — platform-formatted listing title + description, 2 variants.

Input:  identity card + comps dict + platform name
Output: {"variants": [keyword, lifestyle], "flags": {...}}
"""

import json

from src.agents.comps import _ask_text  # ponytail: reuse the one text-Claude helper

PLATFORM_RULES = {
    "poshmark": """POSHMARK — warm, casual, detailed (the friendliest of the three):
- Title: 80 chars max. First 22 chars visible on mobile. Lead with brand.
- Description: LONGEST of the three (4-6 short paragraphs). Conversational and inviting —
  describe the vibe, fabric feel, styling ideas, and condition warmly. It should read like
  a stylish friend recommending the piece. Mention retail price if known. Flag NWT prominently.
- End with style hashtags.""",
    "depop": """DEPOP — short, punchy, FUN (Gen Z, playful):
- Title: casual, a little playful.
- Description: SHORTEST of the three — 2-3 snappy lines max. Fun, high-energy, like texting a
  friend ("this cardigan? obsessed. goes with literally everything 🌿"). Emojis welcome. Get in,
  hype it, get out. No corporate tone, no long condition paragraphs.
- MAX 5 hashtags (Depop's hard limit) — aesthetic tags (#y2k, #vintage, #cottagecore, #thrifted).""",
    "ebay": """EBAY — terse, factual, TO THE POINT (no fluff):
- Title: 80 chars, keyword-dense. Brand + Type + Details + Size + Condition.
- Description: SHORT and dry. Bullet-style facts only — material, size, measurements, condition,
  flaws. NO styling talk, NO vibe, NO hype, NO emojis. A buyer scanning specs should get every
  fact in seconds. Plain and transactional.
- Include item specifics as structured fields (Brand, Style, Size, Color, Material, Department, Condition).""",
    "vinted": """VINTED:
- Title: Brand + Type + Key Detail + Size.
- Description: 3-5 sentences. Friendly, simple. Brand, size, condition, material, fit.
- Hashtags: category synonyms (#trainers and #sneakers, #jumper and #sweater).""",
}

PROMPT = """You are a resale listing copywriter. Write listing copy for this garment on {platform}.

Garment identity:
{card}

Pricing context:
{pricing}

Platform rules:
{rules}

Generate TWO variants:
1. "keyword" — optimized for search. Title: Brand + Type + Size + Color + Condition formula. Description structured, factual, detail-dense.
2. "lifestyle" — optimized for engagement. Natural/appealing title. Conversational description that suggests styling and evokes the item.

Hard rules:
- NEVER invent measurements or specific dimensions.
- NEVER claim authenticity.
- NEVER use trademarked slogans or brand taglines.
- Max 200 words per description.
- Only state facts present in the identity card.

Return ONLY valid JSON:
{{"variants": [
  {{"style": "keyword", "title": "...", "description": "...", "hashtags": ["#..."]}},
  {{"style": "lifestyle", "title": "...", "description": "...", "hashtags": ["#..."]}}
]}}"""


# platform hashtag limits (only Depop has a hard 5-tag cap that matters here)
HASHTAG_CAP = {"depop": 5}


def generate(card: dict, comps: dict, platform: str = "ebay") -> dict:
    rules = PLATFORM_RULES.get(platform.lower(), PLATFORM_RULES["ebay"])
    pricing = {k: comps.get(k) for k in ("suggested_low", "suggested_mid", "suggested_high", "reasoning")}
    text = _ask_text(PROMPT.format(platform=platform, card=json.dumps(card, default=str),
                                   pricing=json.dumps(pricing), rules=rules),
                     max_tokens=1500).strip()
    if text.startswith("```"):
        text = text.split("```")[1].lstrip("json").strip()
    out = json.loads(text)

    # hard hashtag caps per platform (Depop allows at most 5) — enforce even if the
    # model over-generates, so the copy is always platform-valid.
    cap = HASHTAG_CAP.get(platform.lower())
    if cap:
        for v in out.get("variants", []):
            if isinstance(v.get("hashtags"), list):
                v["hashtags"] = v["hashtags"][:cap]

    cond = str(card.get("condition_estimate", "")).lower()
    out["flags"] = {
        "needs_measurements": True,  # OnAgain can't measure from photos
        "needs_flaw_photos": cond not in ("new", "new with tags", "nwt"),
        "size_unconfirmed": not card.get("visible_size"),
    }
    return out


if __name__ == "__main__":
    card = {"type": "blouse", "brand": "Zara", "color": "cream", "visible_size": "M",
            "material_estimate": "cotton", "condition_estimate": "excellent",
            "condition_notes": "No visible wear", "department": "women",
            "keywords": ["zara", "blouse", "cream"]}
    comps = {"suggested_low": 18, "suggested_mid": 24, "suggested_high": 30,
             "reasoning": "3 eBay comps"}
    out = generate(card, comps, "poshmark")
    print(json.dumps(out, indent=2))
    assert len(out["variants"]) == 2
    assert all(len(v["description"].split()) <= 220 for v in out["variants"])
    assert out["flags"]["needs_measurements"] is True
    print("\ncopy agent OK")
