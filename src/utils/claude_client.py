"""Claude vision client — Anthropic API when configured, Bedrock Haiku fallback."""

import base64
import json
import random
import time
from pathlib import Path

import requests

from src import config


def _with_retry(fn, tries=5, base=1.0):
    """Retry on Bedrock/HTTP throttling with exponential backoff + jitter.

    Parallel garment processing bursts many Claude calls at once; Bedrock Haiku
    throttles them. Retrying self-heals instead of failing the whole step.
    """
    for i in range(tries):
        try:
            return fn()
        except Exception as e:
            msg = str(e).lower()
            throttled = ("throttl" in msg or "too many" in msg or "rate" in msg
                         or "429" in msg or "503" in msg or "serviceunavailable" in msg)
            if not throttled or i == tries - 1:
                raise
            time.sleep(base * (2 ** i) + random.uniform(0, 0.5))


MAX_VISION_PX = 1568  # cap longest edge; large phone photos exceed model input limits

def _image_block(image_path: Path) -> dict:
    raw = image_path.read_bytes()
    from PIL import Image
    import io
    im = Image.open(io.BytesIO(raw))
    if max(im.size) > MAX_VISION_PX:
        # downscale oversized images (24MP phone shots blow past Bedrock's input limit)
        scale = MAX_VISION_PX / max(im.size)
        im = im.convert("RGB").resize((int(im.width * scale), int(im.height * scale)))
        buf = io.BytesIO(); im.save(buf, "JPEG", quality=90)
        return {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg",
                                            "data": base64.standard_b64encode(buf.getvalue()).decode()}}
    media = "image/png" if image_path.suffix.lower() == ".png" else "image/jpeg"
    return {"type": "image", "source": {"type": "base64", "media_type": media,
                                        "data": base64.standard_b64encode(raw).decode()}}


def ask_vision(image_path: Path, prompt: str, cheap: bool = False, max_tokens: int = 1500) -> str:
    """Send one image + prompt to Claude, return raw text response.

    cheap=True selects the smallest model (gate checks); otherwise the best
    vision model available (Sonnet on Anthropic API if keyed, else Bedrock Haiku).
    """
    content = [_image_block(image_path), {"type": "text", "text": prompt}]
    messages = [{"role": "user", "content": content}]

    if config.ANTHROPIC_API_KEY:
        model = config.CHEAP_ANTHROPIC_MODEL if cheap else config.ANTHROPIC_MODEL
        def call():
            r = requests.post(
                "https://api.anthropic.com/v1/messages",
                headers={"x-api-key": config.ANTHROPIC_API_KEY,
                         "anthropic-version": "2023-06-01",
                         "content-type": "application/json"},
                json={"model": model, "max_tokens": max_tokens, "messages": messages},
                timeout=90,
            )
            r.raise_for_status()
            return r.json()["content"][0]["text"]
        return _with_retry(call)

    import boto3  # ponytail: lazy import; only needed on the Bedrock path
    client = boto3.client("bedrock-runtime")
    def call():
        resp = client.invoke_model(
            modelId=config.BEDROCK_MODEL,
            body=json.dumps({"anthropic_version": "bedrock-2023-05-31",
                             "max_tokens": max_tokens, "messages": messages}),
        )
        return json.loads(resp["body"].read())["content"][0]["text"]
    return _with_retry(call)


def ask_vision_json(image_path: Path, prompt: str, cheap: bool = False, max_tokens: int = 1500):
    """ask_vision + parse the response as JSON (strips markdown fences)."""
    text = ask_vision(image_path, prompt, cheap=cheap, max_tokens=max_tokens).strip()
    if text.startswith("```"):
        text = text.split("```")[1]
        if text.startswith("json"):
            text = text[4:]
    return json.loads(text.strip())
