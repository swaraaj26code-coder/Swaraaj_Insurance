"""All tunables in one place. Everything comes from the environment so locale,
models and limits are settings, not code."""

import hashlib
import json
import os
from pathlib import Path

from dotenv import load_dotenv

from . import net

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")
net.apply()

GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY", "").strip()
# Optional free Google Cloud key with the Books API enabled; keyless calls share a
# per-IP daily quota that is often exhausted behind corporate proxies.
GOOGLE_BOOKS_API_KEY = os.getenv("GOOGLE_BOOKS_API_KEY", "")
DATA_DIR = ROOT / "data"
DATA_DIR.mkdir(exist_ok=True)


def has_key() -> bool:
    return bool(GOOGLE_API_KEY) and GOOGLE_API_KEY != "replace-me"


def key_fingerprint() -> str:
    return hashlib.sha256(GOOGLE_API_KEY.encode()).hexdigest()[:12]


def _tested_models() -> dict:
    """Models that `python -m app.doctor` verified for this key (data/models.json)."""
    path = DATA_DIR / "models.json"
    try:
        saved = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return saved if saved.get("key") == key_fingerprint() else {}


_tested = _tested_models()

# Models. The live model only talks and directs; the vision model does the reading.
# Order of precedence: explicit .env value > model the doctor tested for this key > default.
LIVE_MODEL = os.getenv("LIVE_MODEL") or _tested.get("live") or "gemini-3.8-live"
LIVE_VOICE = os.getenv("LIVE_VOICE", "Kore")
VISION_MODEL = os.getenv("VISION_MODEL") or _tested.get("vision") or "gemini-3.8-flash"
PRICING_MODEL = os.getenv("PRICING_MODEL") or _tested.get("pricing") or VISION_MODEL
# Search-grounded pricing needs Google Search quota, which free keys often lack.
# The doctor records whether it worked; retailer listings are used either way.
PRICING_SEARCH = bool(os.getenv("PRICING_MODEL")) or (_tested.get("pricing") is not None if _tested else True)

# Locale. The second locale is used to re-price a 10-book sample.
COUNTRY = os.getenv("COUNTRY", "IN")
CURRENCY = os.getenv("CURRENCY", "INR")
SECOND_COUNTRY = os.getenv("SECOND_COUNTRY", "US")
SECOND_CURRENCY = os.getenv("SECOND_CURRENCY", "USD")

# Anything priced above this (in the policy currency) goes to human appraisal.
APPRAISAL_THRESHOLD = float(os.getenv("APPRAISAL_THRESHOLD", "5000"))

# Free-tier friendly rate limits (requests per minute per model family).
VISION_RPM = int(os.getenv("VISION_RPM", "10"))
PRICING_RPM = int(os.getenv("PRICING_RPM", "10"))
PRICING_BATCH = int(os.getenv("PRICING_BATCH", "5"))

# Capture quality thresholds (tuned on our own frames, see docs/FAILURES.md).
BLUR_MIN = float(os.getenv("BLUR_MIN", "60"))        # variance of Laplacian
GLARE_MAX = float(os.getenv("GLARE_MAX", "0.04"))    # fraction of blown-out pixels
DARK_MAX = float(os.getenv("DARK_MAX", "45"))        # mean grey level

# Identification thresholds on a 0-100 fuzzy match score.
ID_ACCEPT = float(os.getenv("ID_ACCEPT", "85"))
ID_REVIEW = float(os.getenv("ID_REVIEW", "65"))

# Optional list prices (USD) to turn token counts into a cost-per-sweep figure.
COST_IN_PER_M = float(os.getenv("COST_IN_PER_M", "0"))
COST_OUT_PER_M = float(os.getenv("COST_OUT_PER_M", "0"))
COST_PER_GROUNDED_CALL = float(os.getenv("COST_PER_GROUNDED_CALL", "0"))
COST_LIVE_PER_MIN = float(os.getenv("COST_LIVE_PER_MIN", "0"))

# The reference object: a plain A4 sheet.
REF_LONG_CM = 29.7
REF_SHORT_CM = 21.0

SWEEPS_DIR = DATA_DIR / "sweeps"
CACHE_DIR = DATA_DIR / "cache"
for d in (SWEEPS_DIR, CACHE_DIR):
    d.mkdir(parents=True, exist_ok=True)
