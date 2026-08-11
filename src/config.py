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

# In a container we can't run `gcloud auth application-default login`. The SA key JSON
# is passed base64-encoded (App Runner env vars reject raw multi-line values); decode
# it to a file and point google-auth at it.
_sa_b64 = os.environ.get("GCP_SA_KEY_B64")
if _sa_b64 and not os.environ.get("GOOGLE_APPLICATION_CREDENTIALS"):
    import base64
    _sa_path = Path("/tmp/vertex-sa.json")
    _sa_path.write_bytes(base64.b64decode(_sa_b64))
    os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = str(_sa_path)

YOUCAM_API_KEY = os.environ.get("YOUCAM_API_KEY", "")
YOUCAM_API_BASE = "https://yce-api-01.makeupar.com/s2s/v2.0"

# Public base URL for buyer try-on links shared into marketplace listings. Defaults to
# the deployed App Runner service; override with PUBLIC_BASE_URL for a different host.
PUBLIC_BASE_URL = os.environ.get(
    "PUBLIC_BASE_URL", "https://cyb423piaw.us-east-1.awsapprunner.com")

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")

# Comps agent search. Primary: Gemini + Google Search grounding on Vertex (uses gcloud
# creds — no key/CSE/console). Falls back to keyless DDG scrape if project unset.
GCP_PROJECT = os.environ.get("GCP_PROJECT", "preflight-hackathon")
VERTEX_MODEL = os.environ.get("VERTEX_MODEL", "gemini-2.5-flash")

# Bedrock region — explicit so it works without a configured AWS profile (App Runner
# instance role has creds but no default region). Local dev's onagain profile sets it too.
AWS_REGION = os.environ.get("AWS_REGION", "us-east-1")

# Vision model selection: Anthropic API (Sonnet) if key present, else Bedrock Haiku.
# ponytail: this account's Bedrock access is Haiku-only; bump when Sonnet enabled.
BEDROCK_MODEL = "us.anthropic.claude-haiku-4-5-20251001-v1:0"
ANTHROPIC_MODEL = "claude-sonnet-5"
CHEAP_ANTHROPIC_MODEL = "claude-haiku-4-5-20251001"
