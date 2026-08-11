"""Comps agent — find comparable sold prices for a garment via web search.

Input:  identity card dict (from identifier agent)
Output: {"comps": [...], "suggested_low/mid/high": int|None, "comp_count": int, "reasoning": str}

ponytail: search = DuckDuckGo HTML endpoint (no key, no scraping infra) +
Claude text extraction from snippets. Max 2 searches per garment.
"""

import json
import re
from typing import Optional

import requests

from src import config

MAX_SEARCHES = 2


def _web_search(query: str) -> str:
    """DDG HTML fallback (no key) — only used if Vertex grounding is unavailable.
    Best-effort; DDG rate-limits headless hits. Primary path is _vertex_grounded()."""
    try:
        r = requests.post(
            "https://html.duckduckgo.com/html/",
            data={"q": query},
            # plain browser UA — a custom suffix gets DDG bot-flagged (empty shell)
            headers={"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                                   "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"},
            timeout=15,
        )
        r.raise_for_status()
        text = re.sub(r"<[^>]+>", " ", r.text)
        text = re.sub(r"\s+", " ", text)
        return text[:8000]
    except Exception:
        return ""


def _ask_text(prompt: str, max_tokens: int = 1000) -> str:
    """Text-only Claude call (no image)."""
    messages = [{"role": "user", "content": prompt}]
    if config.ANTHROPIC_API_KEY:
        r = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={"x-api-key": config.ANTHROPIC_API_KEY,
                     "anthropic-version": "2023-06-01",
                     "content-type": "application/json"},
            json={"model": config.ANTHROPIC_MODEL, "max_tokens": max_tokens,
                  "messages": messages},
            timeout=60,
        )
        r.raise_for_status()
        return r.json()["content"][0]["text"]
    import boto3
    client = boto3.client("bedrock-runtime", region_name=config.AWS_REGION)
    resp = client.invoke_model(
        modelId=config.BEDROCK_MODEL,
        body=json.dumps({"anthropic_version": "bedrock-2023-05-31",
                         "max_tokens": max_tokens, "messages": messages}),
    )
    return json.loads(resp["body"].read())["content"][0]["text"]


EXTRACT_PROMPT = """From these web search results about resale/sold prices, extract up to 5 comparable SOLD listing prices for: {item}.

Search results text:
{results}

Return ONLY valid JSON:
{{"comps": [{{"source": "<site name>", "title": "<listing title>", "sold_price": <number>, "date": null, "url": null}}],
  "notes": "<one sentence on data quality>"}}
Only include entries with a clearly stated price plausibly for this item ($3-$500 for typical apparel). If nothing usable, return {{"comps": [], "notes": "..."}}."""


GROUND_PROMPT = """Search the web for recent SOLD/asking resale prices for this second-hand item: {item}.
Check eBay, Poshmark, Depop, Mercari. Return ONLY valid JSON (no prose, no markdown):
{{"comps": [{{"source": "<site>", "title": "<what sold>", "sold_price": <number USD>}}],
  "notes": "<one sentence on data quality>"}}
Include 3-6 real comparable prices you found ($3-$500 typical apparel). If truly nothing, return {{"comps": [], "notes": "..."}}."""


def _gcp_token() -> Optional[str]:
    """Access token for Vertex via google-auth Application Default Credentials.

    Works in a container (GOOGLE_APPLICATION_CREDENTIALS=service-account.json) AND
    locally (`gcloud auth application-default login`). No gcloud binary needed at
    runtime — that's why this replaced the old `gcloud print-access-token` subprocess.
    """
    global _CREDS
    try:
        import google.auth
        from google.auth.transport.requests import Request as GAuthRequest
        if _CREDS is None:
            _CREDS, _ = google.auth.default(
                scopes=["https://www.googleapis.com/auth/cloud-platform"])
        if not _CREDS.valid:
            _CREDS.refresh(GAuthRequest())
        return _CREDS.token
    except Exception:
        return None


_CREDS = None  # cached google-auth credentials (module-level, refreshed on expiry)


def _vertex_grounded(item_desc: str) -> Optional[dict]:
    """Gemini + Google Search grounding via ADC. Returns parsed comps dict, or None.

    ponytail: grounding does search AND price-extraction server-side in one call.
    No CSE/cx, no console needed.
    """
    if not config.GCP_PROJECT:
        return None
    try:
        token = _gcp_token()
        if not token:
            return None
        url = (f"https://us-central1-aiplatform.googleapis.com/v1/projects/"
               f"{config.GCP_PROJECT}/locations/us-central1/publishers/google/"
               f"models/{config.VERTEX_MODEL}:generateContent")
        r = requests.post(url, headers={"Authorization": f"Bearer {token}"},
                          json={"contents": [{"role": "user",
                                              "parts": [{"text": GROUND_PROMPT.format(item=item_desc)}]}],
                                "tools": [{"googleSearch": {}}]},
                          timeout=45)
        r.raise_for_status()
        cand = r.json().get("candidates", [{}])[0]
        text = "".join(p.get("text", "") for p in cand.get("content", {}).get("parts", []))
        if text.strip().startswith("```"):
            text = text.split("```")[1].lstrip("json").strip()
        # grounded answers sometimes wrap JSON in prose; grab the object
        m = re.search(r"\{.*\}", text, re.DOTALL)
        return json.loads(m.group(0)) if m else None
    except Exception:
        return None


def _query_from_card(card: dict, broad: bool = False) -> str:
    parts = [card.get("brand"), card.get("type")]
    if not broad:
        parts += [card.get("visible_size"), card.get("color")]
    parts = [p for p in parts if p]
    return " ".join(parts) + " sold price resale"


def research(card: dict) -> dict:
    item_desc = " ".join(str(card.get(k) or "") for k in ("brand", "type", "color", "visible_size")).strip()
    comps, notes, searches = [], "", 0

    # primary: Gemini + Google Search grounding (real cited prices, no CSE/console)
    grounded = _vertex_grounded(item_desc)
    if grounded and grounded.get("comps"):
        comps = grounded["comps"]
        notes = grounded.get("notes", "")

    for broad in (False, True):
        if comps:
            break
        if searches >= MAX_SEARCHES or comps:
            break
        q = _query_from_card(card, broad=broad)
        results = _web_search(f"ebay {q}")
        searches += 1
        if not results:
            continue
        try:
            text = _ask_text(EXTRACT_PROMPT.format(item=item_desc, results=results)).strip()
            if text.startswith("```"):
                text = text.split("```")[1].lstrip("json").strip()
            parsed = json.loads(text)
            comps = parsed.get("comps", [])
            notes = parsed.get("notes", "")
            if comps and broad:
                notes = "broad comps — verify pricing manually. " + notes
        except Exception as e:
            notes = f"extraction error: {e}"

    if not comps:
        return {"comps": [], "suggested_low": None, "suggested_mid": None,
                "suggested_high": None, "comp_count": 0,
                "reasoning": "No comparable sold listings found. Research pricing manually."}

    prices = sorted(c["sold_price"] for c in comps if isinstance(c.get("sold_price"), (int, float)))
    # ponytail: drop outliers >3x / <1/3 median — "resale range" snippets poison the spread
    med = prices[len(prices) // 2]
    trimmed = [p for p in prices if med / 3 <= p <= med * 3]
    prices = trimmed or prices
    low = int(prices[0])
    high = int(prices[-1])
    mid = int(prices[len(prices) // 2])
    reasoning = f"{len(comps)} comps found. {notes}".strip()
    if len(comps) <= 2:
        reasoning += " Limited data — price accordingly."
    cond = card.get("condition_estimate", "")
    if cond in ("new", "excellent"):
        reasoning += f" {cond.capitalize()} condition supports mid-high range."
    return {"comps": comps, "suggested_low": low, "suggested_mid": mid,
            "suggested_high": high, "comp_count": len(comps), "reasoning": reasoning}


if __name__ == "__main__":
    card = {"brand": "Levi's", "type": "jeans", "visible_size": "30x32",
            "color": "dark wash", "condition_estimate": "excellent"}
    out = research(card)
    print(json.dumps(out, indent=2))
    assert "comps" in out and "reasoning" in out
    print("\ncomps agent OK")
