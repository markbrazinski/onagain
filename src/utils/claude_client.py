"""Claude vision client — Anthropic API when configured, Bedrock Haiku fallback."""

import base64
import json
from pathlib import Path

import requests

from src import config


def _image_block(image_path: Path) -> dict:
    media = "image/png" if image_path.suffix.lower() == ".png" else "image/jpeg"
    data = base64.standard_b64encode(image_path.read_bytes()).decode()
    return {"type": "image", "source": {"type": "base64", "media_type": media, "data": data}}


def ask_vision(image_path: Path, prompt: str, cheap: bool = False, max_tokens: int = 1500) -> str:
    """Send one image + prompt to Claude, return raw text response.

    cheap=True selects the smallest model (gate checks); otherwise the best
    vision model available (Sonnet on Anthropic API if keyed, else Bedrock Haiku).
    """
    content = [_image_block(image_path), {"type": "text", "text": prompt}]
    messages = [{"role": "user", "content": content}]

    if config.ANTHROPIC_API_KEY:
        model = config.CHEAP_ANTHROPIC_MODEL if cheap else config.ANTHROPIC_MODEL
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

    import boto3  # ponytail: lazy import; only needed on the Bedrock path
    client = boto3.client("bedrock-runtime")
    resp = client.invoke_model(
        modelId=config.BEDROCK_MODEL,
        body=json.dumps({"anthropic_version": "bedrock-2023-05-31",
                         "max_tokens": max_tokens, "messages": messages}),
    )
    return json.loads(resp["body"].read())["content"][0]["text"]


def ask_vision_json(image_path: Path, prompt: str, cheap: bool = False, max_tokens: int = 1500):
    """ask_vision + parse the response as JSON (strips markdown fences)."""
    text = ask_vision(image_path, prompt, cheap=cheap, max_tokens=max_tokens).strip()
    if text.startswith("```"):
        text = text.split("```")[1]
        if text.startswith("json"):
            text = text[4:]
    return json.loads(text.strip())
