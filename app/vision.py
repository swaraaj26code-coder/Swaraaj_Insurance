"""Stage 1: per-keyframe perception with a vision model.

The model returns boxes and the text it can literally read. It is told never to
complete or correct a title from memory; identification happens later against
a real catalogue, so a hallucinated title would fail to match there too."""

import asyncio
import hashlib
import json
import logging
import time

from google import genai
from google.genai import types

from . import cache, config
from .ratelimit import RateLimiter

log = logging.getLogger(__name__)
_limiter = RateLimiter(config.VISION_RPM)
_client: genai.Client | None = None
_client_loop = None

CATEGORIES = ["shelving", "furniture", "lighting", "art", "rug", "electronics", "appliance", "decor", "plant", "other"]

BOX = {"type": "array", "items": {"type": "integer"}, "description": "[ymin, xmin, ymax, xmax] normalised 0-1000"}
POINT = {"type": "array", "items": {"type": "integer"}, "description": "[y, x] normalised 0-1000, empty if not visible"}

SCHEMA = {
    "type": "object",
    "properties": {
        "reference_sheet": {"type": "object", "properties": {"present": {"type": "boolean"}, "box_2d": BOX},
                            "required": ["present"]},
        "spines": {"type": "array", "items": {"type": "object", "properties": {
            "box_2d": BOX,
            "orientation": {"type": "string", "enum": ["upright", "flat"]},
            "legibility": {"type": "string", "enum": ["clear", "partial", "unreadable"]},
            "title_text": {"type": "string"}, "author_text": {"type": "string"}, "publisher_text": {"type": "string"},
            "colour": {"type": "string"},
        }, "required": ["box_2d", "orientation", "legibility", "title_text", "author_text", "publisher_text", "colour"]}},
        "items": {"type": "array", "items": {"type": "object", "properties": {
            "box_2d": BOX,
            "category": {"type": "string", "enum": CATEGORIES},
            "description": {"type": "string"}, "material": {"type": "string"},
            "brand_model": {"type": "string"},
            "search_term": {"type": "string", "description": "generic product type in 1-3 words, as a shop would call it, e.g. 'floor lamp', 'bookcase', 'rug'"},
        }, "required": ["box_2d", "category", "description", "material", "brand_model", "search_term"]}},
        "wall": {"type": "object", "properties": {
            "fully_visible": {"type": "boolean"},
            "ceiling_left": POINT, "ceiling_right": POINT, "floor_left": POINT, "floor_right": POINT,
            "shelving_units": {"type": "array", "items": {"type": "object", "properties": {
                "box_2d": BOX, "shelf_count": {"type": "integer", "description": "number of shelves holding books"}},
                "required": ["box_2d", "shelf_count"]}},
            "non_rectangular_note": {"type": "string"},
        }, "required": ["fully_visible"]},
        "view_blocked": {"type": "object", "properties": {
            "blocked": {"type": "boolean"}, "by": {"type": "string"}},
            "required": ["blocked"], "description": "true if something (a hand, a person, furniture, a door) hides part of what should be captured"},
    },
    "required": ["reference_sheet", "spines", "items", "view_blocked"],
}

RULES = """You are the measuring eye of a home-contents insurance inventory. Report only what is visible.
- reference_sheet: a plain white A4 sheet of paper placed in the scene as a scale reference. Box it if present.
- items: every object that is NOT a book (shelving units, furniture, lamps, framed art/portraits, rugs, electronics,
  appliances such as coffee machines, decor). brand_model only if a brand or model is legibly printed; otherwise "".
  Never box the A4 reference sheet as an item.
- Copy text EXACTLY as printed. Never complete, correct or guess a title or author from memory. If you can only read
  part of a word, give only the part you can read. Unreadable means empty strings."""

SHELF_TASK = """This frame is a close pan along ONE bookshelf. List every book spine on the shelf row that runs
through the middle of the frame, left to right, including books lying flat or stacked (one entry per book, each
with its own tight box around the visible spine). Include spines cut off at the frame edge. Ignore other rows."""

FRONT_TASK = """There is no bookshelf here: books and objects are directly in front of the camera, on a table,
desk, floor or in a pile. List every book whose spine is visible, one entry per book, including books lying flat
in a stack (each with its own tight box around the visible spine). Include spines cut off at the frame edge."""

WALL_TASK = """This frame should show one whole wall of the room. Report the wall's four corners where it meets the
floor, ceiling and neighbouring walls, and every bookshelving unit standing against this wall (its box and how
many shelves hold books). Set fully_visible=false if any corner is cut off. If the wall is not a simple
rectangle, describe it in non_rectangular_note. Do not list book spines for this frame."""

ROOM_TASK = """This frame is a general view of the room. Do not list book spines for this frame."""
TASKS = {"shelf": SHELF_TASK, "front": FRONT_TASK, "wall": WALL_TASK}
SPINE_KINDS = ("shelf", "front")


def client() -> genai.Client:
    """Shared client, recreated when the event loop changes: the doctor runs in its own
    asyncio.run() before the server starts, and an async client bound to that (closed)
    loop fails with "Event loop is closed" on the server's first call (FAILURES #12)."""
    global _client, _client_loop
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:  # called from a worker thread (sync API): any client works
        loop = None
    if _client is None or (loop is not None and loop is not _client_loop):
        _client = genai.Client(api_key=config.GOOGLE_API_KEY)
        _client_loop = loop
    return _client


async def analyse(jpeg: bytes, kind: str) -> dict:
    """kind: 'shelf' | 'front' | 'wall' | 'room'. Returns parsed JSON plus timing/usage."""
    task = TASKS.get(kind, ROOM_TASK)
    # Same image + same prompt + same model -> same answer; replays cost nothing.
    key = hashlib.sha1(jpeg + (config.VISION_MODEL + RULES + task + json.dumps(SCHEMA)).encode()).hexdigest()
    hit = cache.get("vision", key)
    if hit:
        hit["_meta"] = {**hit.get("_meta", {}), "latency_s": 0.0, "cached": True, "in_tokens": 0, "out_tokens": 0}
        return hit
    await _limiter.wait()
    started = time.monotonic()
    cfg = types.GenerateContentConfig(
        response_mime_type="application/json",
        response_json_schema=SCHEMA,
        temperature=0,
        media_resolution=types.MediaResolution.MEDIA_RESOLUTION_HIGH,
    )
    # Busy (503) / quota (429) / timeout errors are usually gone seconds later; one of them
    # used to lose a whole wall (FAILURES #15). Two retries with a short wait, then give up.
    for attempt in range(3):
        try:
            resp = await asyncio.wait_for(client().aio.models.generate_content(
                model=config.VISION_MODEL,
                contents=[types.Part.from_bytes(data=jpeg, mime_type="image/jpeg"), RULES + "\n\n" + task],
                config=cfg,
            ), 60)
            break
        except Exception as exc:
            text = f'{getattr(exc, "code", "")} {getattr(exc, "status", "")} {type(exc).__name__}'
            if attempt == 2 or not any(t in text for t in ("503", "429", "UNAVAILABLE", "RESOURCE_EXHAUSTED", "Timeout")):
                raise
            log.info("Vision temporary error (%s); retrying", text.strip())
            await asyncio.sleep(3 * (attempt + 1))
    try:
        out = json.loads(resp.text or "{}")
    except json.JSONDecodeError:
        log.warning("Vision returned non-JSON")
        out = {"reference_sheet": {"present": False}, "spines": [], "items": []}
    usage = resp.usage_metadata
    out["_meta"] = {"latency_s": round(time.monotonic() - started, 2), "model": config.VISION_MODEL,
                    "in_tokens": getattr(usage, "prompt_token_count", 0) or 0,
                    "out_tokens": (getattr(usage, "candidates_token_count", 0) or 0)
                    + (getattr(usage, "thoughts_token_count", 0) or 0)}
    if kind not in SPINE_KINDS:
        out["spines"] = []
    cache.put("vision", key, out)
    return out
