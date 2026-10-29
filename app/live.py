"""The live voice agent: it directs the sweep and talks. It never measures,
counts or prices; those numbers come from the pipeline and are handed to it."""

from google.genai import types

from . import config

SYSTEM = """You are a friendly, brisk home-contents insurance agent on a live video call. The policyholder is
documenting their home library for a contents claim by walking the room ONCE with their phone camera.
You direct the sweep. Separate background systems read spines, measure and price; you never do those yourself.

Opening (keep it short):
1. Greet them. The default valuation locale is {country} / {currency}: ask them to confirm it or name another
   country. Call set_locale only AFTER they have answered, with what they said. Never assume a country.
2. Ask in one short question what you will be looking at: a room with walls and bookshelves, or books and
   items simply in front of them (on a desk, table, floor or in a pile). Explain the sweep in one or two
   sentences to fit their answer, and check the A4 reference sheet(s) are in place.

Adapt the sweep to what they have. Never insist on walls or shelves they do not have.
(Call the tool BEFORE asking them to start each part.)
- Walls, only if they are in a room whose walls they can show: start_wall("1"), then ask them to stand against
  the opposite wall and show the whole wall, floor to ceiling, corner to corner, with its A4 sheet taped on it.
  Then walls 2, 3, 4 going round the room in order.
  If they say there are no walls to show (or they do not want to), call record_fact(kind="no_walls",
  target="room") and skip the walls; the room size will simply be reported as not measured.
- Shelves, only if they have bookshelves: for each shelving unit (A, B, ...) and each shelf from the top
  (1, 2, ...), call start_shelf("A1") and ask them to pan slowly left to right along that one shelf, close enough
  to read the spines, with an A4 sheet standing upright at the shelf end.
- No shelves (books on a desk, table, floor or in a pile): call start_front_view, then ask them to point the
  camera at the books in front of them, stacked or lined up with the spines facing the camera, with the A4 sheet
  next to the books facing the camera, and to pan slowly across all of them. Call start_front_view again for
  each further group of books.
- Anything else: start_room_view() and ask for a slow pan over remaining furniture, lamps, art and other items
  (or, without a room, over the items in front of them).
Target under three minutes in total. Keep each instruction to one short sentence.

While they walk:
- Messages starting with [CAPTURE MONITOR] come from the camera checks. Act on them immediately in one short
  sentence (slow down, step closer, tilt to remove glare, pan back, show the A4 sheet).
- Messages starting with [INVENTORY] tell you what has been logged. Mention progress briefly now and then
  ("logging the top shelf, about twenty books so far"). Never invent titles, counts or prices; only repeat what
  [INVENTORY] gave you.
- Ask short questions you cannot answer from video, for example whether a portrait is an original or a print.
  Record every such answer and every correction with record_fact:
  "that is a first edition" -> first_edition (target "last" or the title they name);
  "it is signed" -> signed; "skip that shelf, not mine" -> exclude_shelf (target the shelf label or "current");
  "that's just a print" -> art_print (target the item id or description); "it's an original" -> art_original;
  "that lamp isn't mine" -> not_mine_item.
- Never ask them to pull books out, scan barcodes, type ISBNs or photograph items one by one.
- They can talk over you at any time; you are cut off automatically. After an interruption, answer what they
  just said. Do not restart or repeat the sentence you were cut off in unless they ask.

Ending: when everything is covered (or they say they are finished), call end_sweep. If end_sweep answers with a
warning about shelves that may have been missed, ask them about it; call end_sweep again if they want to finish
anyway. When a message
starting with [PACKET READY] arrives, read that summary back in plain words, using exactly the numbers it gives,
and tell them the claim packet is ready to download. Never estimate or round differently.

After the packet: the policyholder may disagree or give their own details ("this book is X", "its price is Y",
"do it again with my numbers"). For each one, call add_customer_correction with exactly what they said: target
is the book id from the packet (like A1-02), its title, an item id (like item-03), or "new" for a book the sweep
missed; comment is their own words. If the tool says it cannot find the book, ask which one they mean.
Tell them briefly that their values go into a separate "customer-advised" packet, labelled as stated by them,
and that the verified packet stays unchanged. Never invent, adjust or suggest values yourself.
Ask "anything else?"; when they are done, call build_customer_packet and read back the summary it returns."""

TOOL_NAMES = ["set_locale", "start_wall", "start_shelf", "start_front_view", "start_room_view", "record_fact", "get_progress",
              "end_sweep", "add_customer_correction", "build_customer_packet"]


def customer_summary(p: dict) -> str:
    """Spoken summary of the customer-advised packet, built in code."""
    t, v, cur = p["totals"], p["based_on"]["verified_totals"], p["totals"]["currency"]
    applied = [c for c in p["customer_corrections"] if c.get("applied")]
    skipped = len(p["customer_corrections"]) - len(applied)
    return (f'[CUSTOMER PACKET READY] Customer-advised packet version {p["customer_version"]} with {len(applied)} of your '
            f'corrections{f" ({skipped} could not be matched)" if skipped else ""}. Books replacement cost '
            f'{t["books_replacement_cost"]:,.0f} {cur} (verified packet: {v["books_replacement_cost"]:,.0f} {cur}), '
            f'used value {t["books_used_value"]:,.0f} {cur} (verified: {v["books_used_value"]:,.0f} {cur}). '
            "Your values are labelled as stated by you; the verified packet is unchanged.")


def _s(desc: str, enum: list[str] | None = None) -> types.Schema:
    return types.Schema(type=types.Type.STRING, description=desc, enum=enum)


def tools() -> list[types.Tool]:
    fns = [
        types.FunctionDeclaration(name="set_locale", description="Set valuation country and currency.",
                                  parameters=types.Schema(type=types.Type.OBJECT, required=["country_code", "currency_code"],
                                                          properties={"country_code": _s("ISO 3166 alpha-2, e.g. IN"),
                                                                      "currency_code": _s("ISO 4217, e.g. INR")})),
        types.FunctionDeclaration(name="start_wall", description="Mark that the next frames show one whole wall.",
                                  parameters=types.Schema(type=types.Type.OBJECT, required=["label"],
                                                          properties={"label": _s("Wall number in order around the room: 1, 2, 3, 4")})),
        types.FunctionDeclaration(name="start_shelf", description="Mark that the next frames pan along one shelf.",
                                  parameters=types.Schema(type=types.Type.OBJECT, required=["label"],
                                                          properties={"label": _s("Unit letter + shelf number from the top, e.g. A1")})),
        types.FunctionDeclaration(name="start_front_view", description="Mark that the next frames show books (and items) directly in front of the camera, on a desk, table, floor or in a pile, when there are no shelves to sweep."),
        types.FunctionDeclaration(name="start_room_view", description="Mark that the next frames are a general room pan for non-book items."),
        types.FunctionDeclaration(name="record_fact", description="Record a policyholder statement or correction.",
                                  parameters=types.Schema(type=types.Type.OBJECT, required=["kind", "target"],
                                                          properties={"kind": _s("Statement type", ["first_edition", "signed", "exclude_shelf", "not_mine_book", "art_print", "art_original", "not_mine_item", "no_walls"]),
                                                                      "target": _s("'last' / 'current', a shelf label, an item id like item-03, or a title"),
                                                                      "note": _s("Their words, briefly")})),
        types.FunctionDeclaration(name="get_progress", description="Get counts so far, walls measured and shelves done."),
        types.FunctionDeclaration(name="end_sweep", description="End the sweep and start building the claim packet."),
        types.FunctionDeclaration(
            name="add_customer_correction",
            description="Record one correction the policyholder states about a book or item (for the separate customer-advised packet).",
            parameters=types.Schema(type=types.Type.OBJECT, required=["target", "comment"], properties={
                "target": _s("Book id like A1-02, item id like item-03, the title/description, or 'new' for a missed book"),
                "title": _s("Title (or item description) as the policyholder said it; empty if not given"),
                "author": _s("Author as the policyholder said it; empty if not given"),
                "replacement_cost": _s("New/replacement price they stated, number only; empty if not given"),
                "used_value": _s("Used value they stated, number only; empty if not given"),
                "comment": _s("The policyholder's own words, briefly")})),
        types.FunctionDeclaration(name="build_customer_packet",
                                  description="Build the separate customer-advised packet from the corrections recorded so far."),
    ]
    return [types.Tool(function_declarations=fns)]


def connect_config() -> types.LiveConnectConfig:
    return types.LiveConnectConfig(
        response_modalities=["AUDIO"],
        system_instruction=SYSTEM.replace("{country}", config.COUNTRY).replace("{currency}", config.CURRENCY),
        speech_config=types.SpeechConfig(voice_config=types.VoiceConfig(
            prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name=config.LIVE_VOICE))),
        input_audio_transcription=types.AudioTranscriptionConfig(),
        output_audio_transcription=types.AudioTranscriptionConfig(),
        # Barge-in: the policyholder's speech cuts the agent off. The phone sends silence while the
        # agent talks and opens the mic only for sustained speech (web/bargein.js), so the model's own
        # detection can stay at LOW sensitivity: small noises and echo do not count as a turn.
        realtime_input_config=types.RealtimeInputConfig(
            activity_handling=types.ActivityHandling.START_OF_ACTIVITY_INTERRUPTS,
            automatic_activity_detection=types.AutomaticActivityDetection(
                start_of_speech_sensitivity=types.StartSensitivity.START_SENSITIVITY_LOW,
                end_of_speech_sensitivity=types.EndSensitivity.END_SENSITIVITY_LOW,
                prefix_padding_ms=200,
                silence_duration_ms=800)),
        tools=tools(),
    )


def packet_summary(p: dict) -> str:
    """Spoken summary built in code from the packet, so every number is traceable."""
    t, r, cur = p["totals"], p["room"], p["totals"]["currency"]
    parts = [f'[PACKET READY] {t["book_count"]} books found: {t["books_identified"]} identified, '
             f'{t["books_unidentified"]} unidentified, {t["books_needs_appraisal"]} flagged for appraisal.',
             f'Books replacement cost {t["books_replacement_cost"]:,.0f} {cur}, used value {t["books_used_value"]:,.0f} {cur}.',
             f'{len(p["items"])} other items, replacement {t["items_replacement_cost_low"]:,.0f} to {t["items_replacement_cost_high"]:,.0f} {cur}.']
    if r["floor_area_m2"]:
        parts.append(f'Room {r["length_m"]} by {r["width_m"]} metres, floor area {r["floor_area_m2"]} square metres, '
                     f'wall area {r["wall_area_m2"]} square metres.')
    elif any(f["kind"] == "no_walls" for f in p.get("policyholder_statements", [])) or \
            ("front" in p["sweep"].get("capture_modes", []) and "wall" not in p["sweep"].get("capture_modes", [])):
        parts.append("No walls were captured, as agreed, so the room size is not part of this packet.")
    else:
        parts.append("The room could not be measured from this sweep.")
    parts.append(f'{t["excluded_from_totals"]} lines are not in the totals and {len(p["review_queue"])} entries need human review.')
    return " ".join(parts)
