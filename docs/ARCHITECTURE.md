# Architecture

```
 Phone (Chrome)                         Server (FastAPI)                                   Outside services
 ──────────────                         ────────────────                                   ────────────────
 mic PCM16 16k ─────── WebSocket ──────► Gemini Live bridge ◄──────────────────────────────► Gemini Live (voice agent)
 640px frame 1/s ──────────────────────►   tools: set_locale, start_wall/shelf/room,
 ◄── agent audio + transcripts ────────    record_fact, get_progress, end_sweep
                                              ▲  [CAPTURE MONITOR] / [INVENTORY] / [PACKET READY]
 1280px frame 2/s ──── HTTP POST ──────► quality.py ─ blur / glare / dark ──► coaching ──┘
                                          │  (every frame saved as evidence: frames/NNNNN.jpg)
                                          ▼
                                         tracker.py ─ ORB pan tracking per shelf → shelf coordinates,
                                          │            keyframe only when ≥45% new shelf in view
                                          ▼
                                         vision.py ─ one structured call per keyframe ─────────► Gemini Flash
                                          │  spines: box, verbatim text, legibility · items · A4 box · wall corners
                                          ▼
                       ┌──────────────── scale.py ─ A4 corners (OpenCV) → plane homography → cm
                       ▼
 merge.py (one entry per book) → identify.py (spine text ↔ catalogue) ───────────────────► Google Books / Open Library /
                       │                                                                    bookshop listings (last resort)
                       ▼
                    pricing.py + shops.py (listed local prices, ECB FX) ─────────────────► Crossword, Kitabay, Book Outlet,
                       │                                                                    SecondSale, IKEA, Frankfurter (ECB);
                       │                                                                    Gemini + Search only if key has quota
 room.py (walls → L, W, H, areas) ─┤
                       ▼
                    packet.py (totals in code, review queue) → claim_packet.json → report.py → report.html
```

## Capture coaching and capture modes

The live agent is told what to say by code, while the policyholder is still standing there:

- **Blur, glare, dark:** per-frame image statistics in `quality.py`.
- **Lost tracking:** ORB matching broke, so the agent asks them to pan back.
- **Unreadable spines:** more than half of a keyframe's spines are unreadable, so the agent asks them to step closer.
- **No A4 sheet seen:** the agent asks them to place one.
- **View blocked (occlusion):** the vision model flags a hand, person, door or furniture hiding the target.
- **Wall not fully in view:** the agent asks them to step back.
- **Missed shelves:** wall shots count the shelves on each bookcase. If `end_sweep` is called with fewer shelves swept, it refuses once and the agent asks about the rest.

The sweep adapts to the space:

- **Room with bookshelves:** walls, then shelves, then a room view.
- **No shelves:** a **front view** (`start_front_view`) for books stacked or lined up in front of the camera, processed like a shelf pan.
- **No walls:** the walls are skipped, and the room is reported as not measured, with the reason.

## Barge-in (talking over the agent)

The design is a **gate on the phone**. On its own, the model's voice detection cuts the agent off even for a 0.2 s burst of sound (measured, see FAILURES.md #11).

1. **Phone gate (`web/bargein.js`).** While the agent speaks, the page sends the model **silence** instead of mic audio, so echo and small noises can't interrupt.
   - During the first 0.6 s of each reply, it learns how loud the agent's own echo is.
   - The gate opens only for at least 0.32 s of sound clearly above that echo (×2.5), above the room background, and above a fixed floor.
   - When it opens, the last ~0.4 s of mic audio is sent first, so the user's first words aren't lost.
   - The agent's volume **dips** (ducking). Playback isn't paused, so there's no stop-start.
2. **Model** (Gemini Live, start sensitivity LOW, `START_OF_ACTIVITY_INTERRUPTS`) confirms with `interrupted`. The reply is then dropped, and any audio arriving with the interruption is discarded on the server.
3. **No confirmation within 2 s** means it wasn't speech. The volume comes back, the gate closes, the bar rises to that sound's level, and a 1.5 s cool-down stops the same noise from firing again. That cool-down is what removes the stammer.

**Measured:**
- **Detector simulation, false interruptions, old vs new:** 6 s of loudspeaker echo 28 → 0; one cough 1 → 0; 5 s of steady noise 23 → 1.
- **Live chain against the real model** (the page's gate run in Node, real recorded speech): a 0.2 s burst never reached the model. Real speech opened the gate after 0.28 s, and the model interrupted 1.08 s after speech began.

Unit checks are in `tests/bargein.test.cjs`, and barge-in counts are recorded in `metrics.barge_ins`.

## Customer-advised packet

After the verified packet is ready, the policyholder can give their own details, by voice ("do it again, this book is X and cost Y") or with the form on the page.

- Each correction is stored as stated (`add_customer_correction`) and must point at a real line (book id, item id, title) or "new" for a missed book. An unknown target is refused, never guessed.
- `build_customer_packet` writes **`customer_advised_claim_packet.json`** and **`customer_advised_report.html`** next to the original, which is never modified.
- Every changed line is marked `customer_advised`. It carries the policyholder's words (`user_comment`), keeps the system's figures in `system_values`, gets source "Stated by the policyholder (not independently verified)", and is added to the review queue.
- Totals are recomputed in code by the same function as the verified packet, and the verified totals are kept in `based_on` for comparison.

## Which model does what

| Job | Model | Why it cannot invent facts |
|---|---|---|
| Talk, direct the sweep, ask questions, take corrections | Gemini Live | Never asked for numbers. Counts and the final summary are generated in code and handed to it to read. |
| Find spines, items, A4 sheet and wall corners in a keyframe | Gemini Flash (structured JSON) | Asked for boxes and verbatim spine text only. A title it invented would still have to match a real catalogue record. |
| Identify a book | No model: RapidFuzz against Google Books / Open Library (bookshop listings as a last resort) | A reading must cover at least half of the catalogue title's words and score at least 85 (65–85 goes to review). An edition or ISBN is claimed only when the publisher on the spine matches a catalogue record. |
| Price a book or item | No model: retailer listings (`app/shops.py`). Gemini + Google Search only if the key has search quota | Shop prices come straight from the shops' product data, with the product link. A grounded price line is kept only if the grounding metadata cites that exact line. |
| Totals | Plain Python | Sums over the lines in `packet.py`. |

## Metric scale

A plain A4 sheet (29.7 × 21.0 cm) is the only reference.

- The vision model gives a rough box around the sheet.
- OpenCV then finds its four corners precisely: an Otsu threshold, a convex quadrilateral, subpixel refinement and an A4-proportion check.
- Those corners define a homography from image pixels to centimetres on the sheet's plane. That corrects perspective, so the camera need not be square-on.

How each figure is measured:

- **Spines.** The sheet stands at the shelf end, flush with the spines.
  - When the sheet is in the same frame as a spine, the spine is measured directly through the homography.
  - Otherwise the sheet's scale carries along the shelf through ORB tracking: shelf coordinates, then cm per shelf unit.
  - Fallbacks, in order: catalogue heights of identified books on that shelf, then a standard-format prior (median spine = 19.8 cm B-format). The prior is flagged as low confidence and sent to review.
- **Walls.** A sheet is taped flat to each wall. The vision model marks the wall's four corners, which are mapped onto the wall plane to give width and height in metres. Walls are captured in order around the room: walls 1/3 give the length, walls 2/4 the width, and height is the median. The room is assumed rectangular, and wall area is gross (openings not subtracted). Both assumptions are stated in the packet.
- **Shelved wall area.** Shelving-unit boxes are measured in the wall shots on the same wall plane.
- **Item dimensions.** These are approximate: scaled at the sheet's depth when a sheet is in the frame, and left blank otherwise.

## Where prices come from

Prices come from real retailer listings. No language model is involved.

| Country | New books | Used books | Other items |
|---|---|---|---|
| India (INR) | Crossword.in | Kitabay | IKEA India |
| US (USD) | Book Outlet | SecondSale | IKEA US |

- **Books:** the bookshops run Shopify, whose public storefront search returns each product's title, price, availability and link.
  - A listing counts only if it's in stock, matches the identified title (same coverage rule as identification), and mentions the author's surname.
  - Translations, audio editions, box sets and similar variants are excluded.
  - An ISBN match wins. Otherwise the closest title, then the cheapest copy, is used as the replacement (new) or used value.
- **Items:** IKEA's public product search for the item's generic type (e.g. "floor lamp"). The range runs from the 25th- to the 75th-percentile comparable product, each end being a specific, linked product. Status is `range`. `priced` is reserved for an exact brand/model price.
- **No local listing:** the same book from the other country's shops is used, converted with that day's ECB reference rate (Frankfurter) and labelled `converted: true`, with the rate and date.
- **Optional web search:** if the API key has Google Search quota (free keys usually don't; the doctor checks), missing figures are looked up with Gemini plus search grounding. A line is kept only if the grounding metadata cites it.
- **Shop list per country:** the shops live in `app/shops.py`, so adding a country means adding its shops, not changing logic.
- **No price found:** the field is left blank, the line goes to review, and it is excluded from totals.
- **Appraisal instead of pricing:** signed or first-edition copies (the policyholder's statement), anything first published before 1950, anything above `APPRAISAL_THRESHOLD`, and all art unless the policyholder says it's a print. These are never auto-priced.
- **Locale is a setting:** the first 10 identified books are re-priced for `SECOND_COUNTRY` (default US / USD) by changing only the locale argument.
- **Caching:** all lookups are cached on disk with their original retrieval date, so replays are reproducible and free.
