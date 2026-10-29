"""Check that everything needed for a sweep works, and pick models this key can use.

    python -m app.doctor

Prints one line per check with a plain-English fix for anything that fails.
Model names are not guessed: it lists the models the key can see, tries real
calls (the same kind the app makes) and saves the first working live, vision
and pricing models to data/models.json, which the app then uses."""

import asyncio
import json
import os
import re
import sys
import time

OK, WARN, FAIL = "[ OK ]", "[WARN]", "[FAIL]"
problems: list[str] = []


def say(status: str, text: str, fix: str = ""):
    print(f"{status} {text}")
    if fix:
        print(f"       -> {fix}")
    if status == FAIL:
        problems.append(fix or text)


def check_python() -> bool:
    v = sys.version_info
    if (3, 11) <= v[:2] <= (3, 13):
        say(OK, f"Python {v.major}.{v.minor}.{v.micro}")
        return True
    say(FAIL, f"Python {v.major}.{v.minor} is not supported", "Install Python 3.12 from https://www.python.org/downloads/ and run the setup script again.")
    return False


def check_packages() -> bool:
    missing = []
    for mod in ("cv2", "numpy", "fastapi", "uvicorn", "google.genai", "rapidfuzz", "jinja2", "cryptography", "httpx", "qrcode"):
        try:
            __import__(mod)
        except Exception:
            missing.append(mod)
    if missing:
        say(FAIL, f"Missing Python packages: {', '.join(missing)}", "Run the setup script again (it installs everything in requirements.txt).")
        return False
    say(OK, "All Python packages installed")
    return True


def _error_text(exc: Exception) -> str:
    code = getattr(exc, "code", "")
    status = getattr(exc, "status", "")
    message = getattr(exc, "message", "") or str(exc)
    text = f"{code} {status} {message}".strip()
    # asyncio timeouts carry no message; name them so the line is not blank.
    return (text or f"{type(exc).__name__} (no answer in time)")[:220]


def _version(name: str) -> tuple:
    m = re.search(r"gemini-(\d+)(?:\.(\d+))?", name)
    return (int(m.group(1)), int(m.group(2) or 0)) if m else (0, 0)


def _stable(name: str) -> bool:
    return not any(x in name for x in ("preview", "exp"))


def model_candidates(models) -> dict[str, list[str]]:
    from . import config
    names = {m.name.removeprefix("models/"): (m.supported_actions or []) for m in models}
    live = [n for n, a in names.items() if "bidiGenerateContent" in a]
    live.sort(key=lambda n: (_version(n), "native-audio" in n or "live" in n, _stable(n)), reverse=True)
    text = [n for n, a in names.items() if "generateContent" in a and "flash" in n
            and not any(x in n for x in ("image", "tts", "live", "audio", "embedding"))]
    text.sort(key=lambda n: (_version(n), "lite" not in n, _stable(n)), reverse=True)
    # A model named in .env is tried first.
    for env, lst in (("LIVE_MODEL", live), ("VISION_MODEL", text)):
        chosen = os.getenv(env)
        if chosen:
            lst.insert(0, chosen)
    fallback_live = [config.LIVE_MODEL, "gemini-live-2.5-flash-preview", "gemini-2.0-flash-live-001"]
    return {"live": list(dict.fromkeys(live + fallback_live)), "text": list(dict.fromkeys(text))}


def _test_image() -> bytes:
    import cv2
    import numpy as np
    img = np.full((360, 640, 3), 90, np.uint8)
    cv2.rectangle(img, (40, 60), (190, 272), (250, 250, 250), -1)
    for i, title in enumerate(["DUNE", "EMMA", "ULYSSES"]):
        x = 260 + i * 60
        cv2.rectangle(img, (x, 60), (x + 50, 300), (40 + 60 * i, 60, 150), -1)
        cv2.putText(img, title, (x + 8, 200), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (255, 255, 255), 1)
    return cv2.imencode(".jpg", img)[1].tobytes()


TEMPORARY = ("RESOURCE_EXHAUSTED", "UNAVAILABLE", " 429", " 503", "TimeoutError")
fell_back = False  # a better model failed only for a temporary reason (quota / busy)


async def pick_vision(cands: list[str]) -> str | None:
    global fell_back
    from . import config, vision
    jpeg = _test_image()
    for name in cands[:6]:
        config.VISION_MODEL = name
        try:
            out = await asyncio.wait_for(vision.analyse(jpeg, "shelf"), 90)
            if isinstance(out.get("spines"), list):
                say(OK, f"Vision model works: {name} ({len(out['spines'])} spines found on the test image)")
                if fell_back:
                    say(OK, "A better model was busy or out of free quota; it will be tried again next start")
                return name
        except Exception as exc:
            if any(t in _error_text(exc) for t in TEMPORARY):
                fell_back = True
            say(WARN, f"Vision model {name} not usable: {_error_text(exc)}")
    return None


async def pick_pricing(cands: list[str], vision_model: str | None) -> str | None:
    from . import config, pricing
    # Prefer a different model from vision so the two stages use separate free-tier quotas.
    order = [c for c in cands if c != vision_model] + ([vision_model] if vision_model else [])
    prompt = ('Search the web for the current new paperback price of "Atomic Habits" by James Clear in India. '
              "Answer in one line: BOOK 1 | NEW | <price> | <currency code> | <retailer> | new")
    for name in order[:2]:
        config.PRICING_MODEL = name
        try:
            text, supports = await asyncio.wait_for(pricing._grounded(prompt), 90)
            if supports:
                say(OK, f"Extra: web-search pricing with citations works ({name})")
                return name
        except Exception as exc:
            if "RESOURCE_EXHAUSTED" in _error_text(exc):
                break  # free keys usually have no search quota at all; no point trying more models
    say(OK, "Web-search pricing is not available on this key (normal for free keys); prices come from shop listings")
    return None


async def pick_live(cands: list[str]) -> str | None:
    from google import genai

    from . import config, live
    client = genai.Client(api_key=config.GOOGLE_API_KEY)
    for name in cands[:5]:
        try:
            async def attempt():
                async with client.aio.live.connect(model=name, config=live.connect_config()) as session:
                    await session.send_realtime_input(text="Say the single word: ready.")
                    async for resp in session.receive():
                        c = resp.server_content
                        if c and c.model_turn and any(p.inline_data for p in c.model_turn.parts or []):
                            return True
                        if c and c.turn_complete:
                            return True
                return False
            if await asyncio.wait_for(attempt(), 30):
                say(OK, f"Live voice model works: {name}")
                return name
            say(WARN, f"Live model {name} connected but did not answer")
        except Exception as exc:
            say(WARN, f"Live model {name} not usable: {_error_text(exc)}")
    return None


def check_services():
    import httpx

    from . import config
    try:
        params = {"q": "intitle:dune", "maxResults": 1}
        if config.GOOGLE_BOOKS_API_KEY:
            params["key"] = config.GOOGLE_BOOKS_API_KEY
        r = httpx.get("https://www.googleapis.com/books/v1/volumes", params=params, timeout=15)
        if r.status_code == 200:
            say(OK, "Google Books catalogue reachable")
        elif r.status_code == 429:
            say(WARN, "Google Books daily free quota used up on this internet connection",
                "Optional: add a free GOOGLE_BOOKS_API_KEY to .env. Without it, books are matched against bookshop listings instead (no edition or ISBN claimed).")
        else:
            say(WARN, f"Google Books returned {r.status_code}")
    except Exception as exc:
        say(WARN, f"Google Books not reachable: {exc}")
    try:
        r = httpx.get("https://openlibrary.org/search.json", params={"title": "dune", "limit": 1},
                      headers={"User-Agent": "library-claim-agent/1.0"}, timeout=15)
        say(OK if r.status_code == 200 else WARN, "Open Library catalogue " + ("reachable" if r.status_code == 200 else f"returned {r.status_code} (blocked on this network?)"))
    except Exception as exc:
        say(WARN, f"Open Library not reachable: {exc}")
    from . import shops
    for shop in shops.BOOK_SHOPS.get(config.COUNTRY, []) + shops.BOOK_SHOPS.get(config.SECOND_COUNTRY, []):
        try:
            shops.shopify_search(shop, "atomic habits")
            say(OK, f"Price source reachable: {shop.name} ({shop.condition} books, {shop.currency})")
        except Exception as exc:
            say(WARN, f"Price source {shop.name} not reachable: {str(exc)[:80]}", "Books it sells will be priced from the other shops or left blank.")
    if shops.ikea_range("floor lamp", config.COUNTRY):
        say(OK, f"Price source reachable: IKEA {config.COUNTRY} (furniture, lamps, rugs, decor)")
    else:
        say(WARN, f"IKEA {config.COUNTRY} not reachable", "Non-book items will be left unpriced and sent to review.")
    from .pricing import fx_rate
    fx = fx_rate("USD", config.CURRENCY)
    if fx:
        say(OK, f"Exchange rates reachable (1 USD = {fx[0]} {config.CURRENCY} on {fx[1]})")
    else:
        say(WARN, "Exchange-rate service not reachable", "Prices found in another currency will be left blank instead of converted.")


async def main(skip_models: bool = False) -> bool:
    print("\nChecking your setup...\n")
    if not (check_python() and check_packages()):
        return finish(False)
    from google import genai

    from . import config
    import httpx
    try:
        httpx.get("https://generativelanguage.googleapis.com/", timeout=15)
        say(OK, "Internet connection to Google works")
    except Exception as exc:
        say(FAIL, f"Cannot reach Google's AI service: {exc}", "Check your internet connection (or VPN / office proxy) and try again.")
        return finish(False)
    if not config.has_key():
        say(FAIL, "No Google API key in .env", "Get a free key at https://aistudio.google.com/apikey and paste it after GOOGLE_API_KEY= in the .env file.")
        return finish(False)
    try:
        client = genai.Client(api_key=config.GOOGLE_API_KEY)  # keep a reference while the pager is read
        models = list(client.models.list())
        say(OK, f"API key accepted ({len(models)} models available)")
    except Exception as exc:
        say(FAIL, f"API key rejected: {_error_text(exc)}", "Copy the key again from https://aistudio.google.com/apikey (no spaces) into .env.")
        return finish(False)
    check_services()
    if skip_models:
        return finish(True)
    cands = model_candidates(models)
    print("\nTrying models (this takes about a minute)...")
    vision_model = await pick_vision(cands["text"])
    if not vision_model:
        say(FAIL, "No vision model works with this key", "Your key may have no free quota left today. Wait, or try another Google account's key.")
    pricing_model = await pick_pricing(cands["text"], vision_model)
    live_model = await pick_live(cands["live"])
    if not live_model:
        say(FAIL, "No live voice model works with this key", "The voice agent cannot start. Check https://aistudio.google.com/ for Live API access.")
    if vision_model and live_model:
        config.VISION_MODEL, config.LIVE_MODEL = vision_model, live_model
        config.PRICING_MODEL, config.PRICING_SEARCH = pricing_model or vision_model, pricing_model is not None
        saved = {"key": config.key_fingerprint(), "live": live_model, "vision": vision_model, "pricing": pricing_model,
                 "tested_at": time.strftime("%Y-%m-%d %H:%M"), "retry_better": fell_back}
        (config.DATA_DIR / "models.json").write_text(json.dumps(saved, indent=1), encoding="utf-8")
        config._tested = saved
        say(OK, "Saved working models to data/models.json")
    return finish(not problems)


def finish(ready: bool) -> bool:
    print()
    if ready:
        print("READY: everything needed for a sweep works.")
    else:
        print("NOT READY. Fix these, then run the start script again:")
        for i, p in enumerate(problems, 1):
            print(f"  {i}. {p}")
    print()
    return ready


if __name__ == "__main__":
    sys.exit(0 if asyncio.run(main("--quick" in sys.argv)) else 1)
