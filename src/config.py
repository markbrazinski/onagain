"""OnAgain config — reads .env at repo root, exposes keys and model selection."""

import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
WORK_DIR = REPO_ROOT / "work"          # crops, renders, intermediate artifacts

# ponytail: minimal .env loader, no python-dotenv dep
_env_path = REPO_ROOT / ".env"
if _env_path.exists():
    for _line in _env_path.read_text().splitlines():
        _line = _line.strip()
        if _line and not _line.startswith("#") and "=" in _line:
            _k, _, _v = _line.partition("=")
            os.environ.setdefault(_k.strip(), _v.strip())

YOUCAM_API_KEY = os.environ.get("YOUCAM_API_KEY", "")
YOUCAM_API_BASE = "https://yce-api-01.makeupar.com/s2s/v2.0"

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")

# Vision model selection: Anthropic API (Sonnet) if key present, else Bedrock Haiku.
# ponytail: this account's Bedrock access is Haiku-only; bump when Sonnet enabled.
BEDROCK_MODEL = "us.anthropic.claude-haiku-4-5-20251001-v1:0"
ANTHROPIC_MODEL = "claude-sonnet-5"
CHEAP_ANTHROPIC_MODEL = "claude-haiku-4-5-20251001"
