# Developer Guide

How the Library Contents Claim Agent is built, file by file and function by function. Read this after the [README](README.md) (how to run and use it) and alongside [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) (design decisions) and [docs/FAILURES.md](docs/FAILURES.md) (what broke and why).

**Contents**

1. [Mental model](#1-mental-model)
2. [Developer setup and commands](#2-developer-setup-and-commands)
3. [File structure](#3-file-structure)
4. [Runtime data layout](#4-runtime-data-layout)
5. [Configuration](#5-configuration)
6. [End-to-end flow](#6-end-to-end-flow)
7. [HTTP and WebSocket API](#7-http-and-websocket-api)
8. [Module reference: back end (`app/`)](#8-module-reference-back-end-app)
9. [Module reference: front end (`web/`)](#9-module-reference-front-end-web)
10. [Scripts, evaluation and packaging](#10-scripts-evaluation-and-packaging)
11. [Data contracts](#11-data-contracts)
12. [Rules the code must keep](#12-rules-the-code-must-keep)
13. [Tests](#13-tests)
14. [How to extend](#14-how-to-extend)
15. [Debugging](#15-debugging)

---

## 1. Mental model

Two loops run side by side during a sweep.

```
               ┌───────────────────────── LIVE CALL LOOP (talking) ─────────────────────────┐
 Phone page    │ mic PCM16 16 kHz + 1 small frame/s ──WebSocket──► server.live_ws ──► Gemini Live │
 (web/)        │ ◄── agent audio 24 kHz, transcripts, tool calls ────────────────────────────── │
               └────────────────────────────────────────────────────────────────────────────────┘
                     ▲ coaching / inventory text ([CAPTURE MONITOR], [INVENTORY], [PACKET READY])
                     │
               ┌───────────────────────── MEASURING LOOP (counting) ─────────────────────────┐
 Phone page    │ 2 high-res frames/s ──HTTP POST──► Sweep.add_frame                             │
               │   quality ► save ► tracker ► keyframe? ► vision (Gemini Flash) ► scale (A4)    │
               │   ► merge (one per book) ► identify (catalogues) ► price (shops) ► inventory   │
               │ ◄── inventory / coach / frame events (WebSocket broadcast) ─────────────────── │
               └────────────────────────────────────────────────────────────────────────────────┘
 End of sweep: Sweep.finish ► room ► packet.build (totals in code) ► claim_packet.json + report.html
 After: corrections ► packet.build_customer ► customer_advised_claim_packet.json + report
```

- **The live model talks; it never measures, counts or prices.** Numbers come from the measuring loop and are handed to it as text.
- **One `Sweep` object** (`app/sweep.py`) holds all state for one walk-through. The server keeps sweeps in memory, keyed by id; every sweep also writes everything to `data/sweeps/<id>/`.

## 2. Developer setup and commands

```bash
python -m venv .venv && .venv\Scripts\activate      # macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
copy .env.example .env                              # set GOOGLE_API_KEY
python -m app.doctor                                # verify setup, pick models -> data/models.json
python -m app.launch                                # HTTP :8000 (computer) + HTTPS :8443 (phone)
python -m app.launch --phone                        # same, prints phone URL + QR instead of opening a browser
python -m uvicorn app.server:app --port 8000        # plain server only (no phone HTTPS, no doctor)
python -m pytest -q                                 # all tests, offline (Node needed for the barge-in test)
node tests/bargein.test.cjs                         # barge-in gate unit checks only
python -m app.replay data/sweeps/<id>               # re-run the pipeline on a recorded sweep
python -m eval.score <packet.json> <ground_truth_dir>   # pass/fail per assignment bar
python scripts/make_zip.py                          # clean ZIP without .env/.venv/data
```

Python 3.11–3.13 (developed on 3.12). Node.js is optional (only for the barge-in unit checks).

## 3. File structure

```
library-claim-agent/
├── README.md                  beginner guide: setup, screen tour, sweep, results, troubleshooting
├── DEVELOPER_GUIDE.md         this file
├── requirements.txt           pinned Python dependencies
├── .env.example               all settings with comments (copied to .env on first start)
├── .gitignore                 keeps .env, .venv, data/ caches, sweeps, certificates out of git
├── START_HERE.bat             Windows: install if needed, then run (opens the browser)
├── START_PHONE.bat            Windows: same, but prints the phone address + QR in the console
├── RUN_TESTS.bat              Windows: install if needed, then run the test suite
├── MAKE_ZIP.bat               Windows: build a clean ZIP to copy to another computer
├── PUBLISH_TO_GITHUB.bat      Windows: commit and push to your private GitHub repo (blocks secrets)
├── .gitattributes             keeps .bat files CRLF and start.sh LF after a git clone
├── start.sh                   macOS/Linux equivalent of START_HERE / START_PHONE (--phone)
│
├── app/                       back end (Python package)
│   ├── __init__.py            marks the package (empty)
│   ├── config.py              every setting, read from .env; tested models from data/models.json
│   ├── net.py                 office-network fixes: OS certificate bundle, IPv4-first DNS
│   ├── cache.py               tiny JSON disk cache (data/cache/<namespace>/)
│   ├── ratelimit.py           requests-per-minute limiters (async and thread versions)
│   ├── explain.py             API errors → plain-English messages
│   ├── quality.py             per-frame blur / glare / darkness check
│   ├── tracker.py             ORB pan tracking per shelf, keyframe choice, shelf coordinates
│   ├── vision.py              Gemini Flash structured perception per keyframe (cached)
│   ├── scale.py               A4 sheet detection and plane homography → centimetres / metres
│   ├── merge.py               per-frame spine detections → one entry per physical book
│   ├── identify.py            spine text → catalogue record (or "unidentified"), no model guessing
│   ├── shops.py               retailer listings: Shopify bookshops per country, IKEA comparables
│   ├── pricing.py             prices with sources: shops first, converted foreign, optional grounded search
│   ├── room.py                wall measurements → room length/width/height and areas
│   ├── packet.py              claim_packet.json builder, totals in code, customer-advised packet
│   ├── report.py              HTML report rendered from a packet (Jinja2)
│   ├── sweep.py               the Sweep: orchestrates every stage for one walk-through
│   ├── live.py                the voice agent: system prompt, tool declarations, spoken summaries
│   ├── server.py              FastAPI app: pages, REST API, WebSocket bridge to Gemini Live
│   ├── doctor.py              setup checks; tries real calls and saves working models
│   ├── launch.py              beginner launcher: key prompt, doctor, HTTP + HTTPS servers, browser
│   ├── cert.py                self-signed HTTPS certificate for the phone (LAN IP)
│   └── replay.py              re-run the pipeline on a recorded sweep folder
│
├── web/                       front end (plain HTML/CSS/JS, no build step)
│   ├── index.html             layout + all CSS (design tokens, glass cards) + Lucide icon sprite
│   ├── app.js                 the call: camera, mic, agent audio, barge-in wiring, server events
│   ├── ui.js                  dashboard: nav, menus, notifications, activity, KPIs, chart, search
│   ├── avatar.js              the agent's 3D face, lip-synced to its voice (TalkingHead + HeadAudio)
│   └── bargein.js             barge-in gate logic (pure, Node-testable)
│
├── eval/
│   ├── score.py               scores a packet against hand-collected ground truth
│   └── ground_truth_template/ books.csv, items.csv, room.json templates
│
├── scripts/
│   ├── windows_setup.bat      finds/installs Python, creates .venv, installs requirements
│   └── make_zip.py            builds the clean ZIP
│
├── tests/
│   ├── test_pipeline.py       unit tests: scale, glare, merge, room, pricing rules, identify, packets
│   ├── test_sweep_integration.py  synthetic 110-book pan through the whole Sweep (shelf + front)
│   ├── test_bargein.py        runs bargein.test.cjs under Node
│   └── bargein.test.cjs       barge-in gate scenarios (echo, cough, noise, stammer)
│
├── docs/
│   ├── ARCHITECTURE_NOTE.md   one-page architecture note (deliverable 5)
│   ├── FAILURE_LOG.md         one-page failure log with cost and latency (deliverable 6)
│   ├── ARCHITECTURE.md        pipeline diagram, models, scale, prices, barge-in, customer packet
│   └── FAILURES.md            failure log with root causes and measured fixes
│
└── data/                      created at runtime (not in git, not in the ZIP), see section 4
```

## 4. Runtime data layout

```
data/
├── app.log                    full server log (INFO); the console shows warnings only
├── models.json                models the doctor verified for this key {key, live, vision, pricing, tested_at}
├── ca-bundle.pem              certifi + OS certificates (written by net.py at start)
├── cert.pem, key.pem          self-signed HTTPS certificate for the phone (cert.py)
├── cache/                     disk cache, safe to delete (costs API calls to rebuild)
│   ├── vision/                vision results keyed by image + prompt + model
│   ├── books/                 Google Books / Open Library / web-search candidates
│   ├── shops/                 Shopify and IKEA search results (with retrieval date)
│   ├── prices/, item_prices/  grounded-search price results (only if search quota)
│   ├── urls/                  resolved redirect URLs
│   └── fx/                    daily exchange rates
└── sweeps/<YYYYMMDD-HHMMSS-xxxx>/
    ├── frames/00000.jpg …     every uploaded frame (evidence, referenced by frame_ref)
    ├── frames/00012.vision.json   raw vision output for each analysed keyframe
    ├── frames.jsonl           one line per frame: idx, t, ref, segment, kind, quality
    ├── events.jsonl           timeline: start, segment, locale, fact, coach, vision_error,
    │                          barge_in, end, customer_correction, customer_packet
    ├── claim_packet.json      the verified packet
    ├── report.html            human-readable report of the verified packet
    ├── customer_advised_claim_packet.json   only if corrections were given
    └── customer_advised_report.html
```

`frames.jsonl` and `events.jsonl` are enough to replay a sweep (`app/replay.py`).

## 5. Configuration

All settings live in `.env` (template: `.env.example`) and are read once by `app/config.py`.

| Variable | Default | Used by | Meaning |
|---|---|---|---|
| `GOOGLE_API_KEY` | – | everything Gemini | AI Studio key (required) |
| `GOOGLE_BOOKS_API_KEY` | empty | identify | optional key for Google Books (keyless quota is per IP) |
| `LIVE_MODEL`, `VISION_MODEL`, `PRICING_MODEL` | auto | live, vision, pricing | force a model; normally picked by the doctor |
| `LIVE_VOICE` | `Kore` | live | prebuilt voice |
| `COUNTRY`, `CURRENCY` | `IN`, `INR` | pricing, prompt | valuation locale (agent confirms it) |
| `SECOND_COUNTRY`, `SECOND_CURRENCY` | `US`, `USD` | sweep.finish | 10-book locale comparison |
| `APPRAISAL_THRESHOLD` | `5000` | packet | above this a book goes to human appraisal |
| `VISION_RPM`, `PRICING_RPM`, `PRICING_BATCH` | 10, 10, 5 | ratelimit, pricing | free-tier pacing |
| `LOOKUP_CONCURRENCY` | 4 | identify, pricing | catalogue and shop lookups at a time |
| `FINISH_STALL_S`, `FINISH_VISION_WAIT_S` | 60, 180 | sweep | how long the packet waits for queued frames (no progress / in all) |
| `USE_OS_TRUST`, `FORCE_IPV4` | 1, 1 | net | office-proxy and IPv6 fixes |
| `COST_IN_PER_M`, `COST_OUT_PER_M`, `COST_PER_GROUNDED_CALL`, `COST_LIVE_PER_MIN` | 0 | packet metrics | optional USD prices for a cost estimate |

Tuning constants that are not in `.env` but in `config.py`: `BLUR_MIN` 60, `GLARE_MAX` 0.04, `DARK_MAX` 45 (quality), `ID_ACCEPT` 85, `ID_REVIEW` 65 (identification), `REF_LONG_CM` 29.7, `REF_SHORT_CM` 21.0 (A4).

**Model precedence:** an explicit `.env` value, then `data/models.json` (only if its key fingerprint matches the current key), then the built-in default. `PRICING_SEARCH` (grounded search on/off) is true only if the doctor found a model with search quota.

## 6. End-to-end flow

### 6.1 Start-up (`START_HERE.bat` → `app.launch`)

1. `scripts/windows_setup.bat`: finds Python (or installs 3.12 with winget), creates `.venv`, installs `requirements.txt` (skipped if unchanged) and copies `.env.example` to `.env`.
2. `launch.ensure_key()` asks for the key if `.env` has none.
3. `doctor.main()` runs. The full run (first time or new key) tries real calls and writes `data/models.json`; later runs are quick checks.
4. `cert.main()` makes an HTTPS certificate for the LAN IP and sets `PHONE_URL`.
5. Two Uvicorn servers start in one process, sharing the same FastAPI `app` and sweeps: HTTP `127.0.0.1:8000` and HTTPS `0.0.0.0:8443`.
6. The browser opens; `server.check_key` probes the key in the background, and `/api/health` reports it.

### 6.2 A sweep

1. **Start.** The page (`app.js start()`) gets camera and mic, then calls `POST /api/sweeps`, which creates a `Sweep`, starts 3 vision workers and the background pricing loop, and returns an id.
2. **Live call.** The page opens `WS /ws/{id}`. `server.live_ws` connects to Gemini Live with `live.connect_config()` (prompt, tools, voice activity settings) and relays audio, frames and text both ways.
3. **Measuring frames.** Every 500 ms the page `POST`s a 1280 px JPEG to `/api/sweeps/{id}/frames`, which runs `Sweep.add_frame` (quality, save, tracking, keyframe decision, coaching).
4. **Keyframes.** Vision workers call `vision.analyse`, then `Sweep._ingest`:
   - A4 sheet → homography;
   - spines → detections with shelf coordinates and direct cm;
   - items → de-duplicated item list;
   - wall → size, shelving units and shelf counts.
5. **Inventory.** `Sweep.publish_inventory` runs:
   - `books()`: `merge.cluster`, then positions, then `_measure`;
   - `identify_new()`: catalogue lookups in threads, `LOOKUP_CONCURRENCY` at a time;
   - then broadcasts an `inventory` event to the page and (throttled) an `[INVENTORY]` note to the agent.
6. **Coaching.** `Sweep.coach` sends tips: blur, glare, dark, lost tracking, unreadable spines, missing A4 sheet, view blocked, wall not fully visible, and art (original or print?). They go to the page and to the agent as `[CAPTURE MONITOR] …`.
7. **Agent tools.** The agent calls tools (`set_locale`, `start_shelf`, …), handled by `server._run_tool`.
8. **End.** The agent's `end_sweep` (or the page's End button, `POST /finish`) runs `server._finish`, then `Sweep.finish()`:
   - drain the vision queue while vision makes progress (stops after `FINISH_STALL_S` without a result or `FINISH_VISION_WAIT_S` in all; frames left over go to the review queue);
   - identify;
   - price books and items, plus the second-locale sample, side by side;
   - `room.compute`, then `packet.build`;
   - write the JSON and the report.

   Then `live.packet_summary` is sent to the page (`packet` event) and to the agent (`[PACKET READY] …`).
9. **Corrections (optional).**
   - `add_customer_correction` (tool) or `POST /corrections` calls `Sweep.add_correction`.
   - `build_customer_packet` (tool) or `POST /customer-packet` calls `Sweep.build_customer_packet`, then `packet.build_customer`, and writes the customer-advised files.

### 6.3 Barge-in (talking over the agent)

`web/bargein.js` gates the microphone while the agent's audio plays:
- **Gate shut:** the page sends silence.
- **Sustained speech opens it:** at least 320 ms, above the learned echo level. The page then ducks the agent's volume, sends the last ~0.4 s of audio first, and tells the server `barge_in`.
- **Confirmed:** Gemini's own voice detection (`START_OF_ACTIVITY_INTERRUPTS`, LOW sensitivity) sends `interrupted`, and the page drops the rest of the reply.
- **Not confirmed within 2 s:** `reject()` restores the volume and raises the bar.

## 7. HTTP and WebSocket API

### 7.1 HTTP (`app/server.py`)

| Method and path | Handler | Purpose |
|---|---|---|
| `GET /` | `index` | `web/index.html` |
| `GET /app.js`, `/ui.js`, `/bargein.js`, `/avatar.js` | `js`, `ui_js`, `bargein_js`, `avatar_js` | front-end scripts |
| `GET /favicon.ico` | `favicon` | 204 (no icon) |
| `GET /api/health` | `health` | key state, models, locale, `phone_url` |
| `GET /api/phone-qr.svg` | `phone_qr` | QR code of `PHONE_URL` |
| `POST /api/sweeps` | `create_sweep` | new `Sweep`; body `{device}` → `{id, country, currency}` |
| `POST /api/sweeps/{id}/frames` | `upload_frame` | raw JPEG body (≤ 4 MB) → quality dict |
| `POST /api/sweeps/{id}/segment` | `segment` | `{kind, label}` manual step (buttons) |
| `POST /api/sweeps/{id}/finish` | `finish` | end the sweep, build the packet → `{summary, report}` |
| `POST /api/sweeps/{id}/corrections` | `add_correction` | `{target, title, author, replacement_cost, used_value, comment}` |
| `POST /api/sweeps/{id}/customer-packet` | `customer_packet` | build the customer-advised packet |
| `GET /api/sweeps/{id}/packet` | `get_packet` | the verified packet JSON |
| `GET /sweeps/{id}/…` | static mount | frames, reports and JSON files of a sweep |

### 7.2 WebSocket `GET /ws/{id}` (`server.live_ws`)

**Page → server** (JSON):

| `type` | Fields | Effect |
|---|---|---|
| `audio` | `data` (base64 PCM16 16 kHz) | forwarded to Gemini Live (silence while the barge-in gate is shut) |
| `video` | `data` (base64 JPEG ~640 px) | forwarded to Gemini Live (about 1 per second) |
| `text` | `text` | typed message to the agent; marks the user as having spoken |
| `barge_in` | – | counted in `sweep.barge_ins["local"]`, logged |
| `close` | – | ends the upstream loop |

**Server → page:**

| `type` | Source | Fields |
|---|---|---|
| `ready` | live_ws | `model` |
| `audio` | Gemini | `data` (base64 PCM16 24 kHz) |
| `transcript` | Gemini | `speaker` (`you` or `agent`), `text` |
| `interrupted`, `turn_complete` | Gemini | – |
| `tool` | `_run_tool` | `name`, `args`, `result` |
| `error` | live_ws | `message` (plain English from `explain`) |
| `frame` | `Sweep.add_frame` | `idx`, `t`, `quality`, `keyframe`, `queue` |
| `coach` | `Sweep.coach` | `text` |
| `inventory` | `Sweep.publish_inventory` | `books`, `items`, `walls`, `warnings`, `counts` |
| `status`, `warning` | Sweep | `text` |
| `packet` | `_finish` | `url`, `json`, `summary`, `time_to_packet_s` |
| `corrections` | `_add_correction` | `corrections` (list) |
| `customer_packet` | `_build_customer` | `url`, `json`, `summary`, `version` |

**Messages injected into the live agent** (`send_realtime_input(text=…)`):
- `[CAPTURE MONITOR] …`: coaching to say now;
- `[INVENTORY] …`: progress, every 20 s at most;
- `[PACKET READY] …`: summary to read back;
- `[CUSTOMER PACKET READY] …`.

### 7.3 Agent tools (`app/live.py tools()`, handled in `server._run_tool`)

| Tool | Arguments | Server action |
|---|---|---|
| `set_locale` | `country_code`, `currency_code` | `Sweep.set_locale`; **refused until the user has spoken** |
| `start_wall` | `label` | segment `wall` |
| `start_shelf` | `label` (e.g. `A1`) | segment `shelf` |
| `start_front_view` | – | segment `front` (labels F1, F2…) |
| `start_room_view` | – | segment `room` |
| `record_fact` | `kind`, `target`, `note` | `Sweep.add_fact`. Kinds: first_edition, signed, exclude_shelf, not_mine_book, art_print, art_original, not_mine_item, no_walls |
| `get_progress` | – | counts, walls, `shelf_progress()`, warnings, queue |
| `end_sweep` | – | refuses once if wall shots show unswept shelves; otherwise `_finish` in the background |
| `add_customer_correction` | `target`, `title`, `author`, `replacement_cost`, `used_value`, `comment` | `Sweep.add_correction` |
| `build_customer_packet` | – | `Sweep.build_customer_packet` |

## 8. Module reference: back end (`app/`)

Imports are grouped as **stdlib**, **third-party** and **internal**.

### `app/config.py`: settings

- **Imports.** Stdlib: `hashlib`, `json`, `os`, `pathlib.Path`. Third-party: `dotenv.load_dotenv`. Internal: `net`.
- **On import:** loads `.env`, calls `net.apply()`, creates `data/`, `data/sweeps/` and `data/cache/`, and resolves the models.

| Name | What it does |
|---|---|
| `has_key()` | True if a real key is set (not empty and not `replace-me`). |
| `key_fingerprint()` | First 12 hex characters of the key's SHA-256; binds `models.json` to one key without storing the key. |
| `_tested_models()` | Reads `data/models.json` and returns it only if the fingerprint matches. |
| Constants | All settings from section 5, plus `ROOT`, `DATA_DIR`, `SWEEPS_DIR`, `CACHE_DIR`. |

### `app/net.py`: network compatibility

- **Imports.** Stdlib: `os`, `socket`, `ssl`, `pathlib.Path`. Third-party: `certifi` (inside the function).

| Name | What it does |
|---|---|
| `_os_trust_bundle()` | Writes `data/ca-bundle.pem` = certifi + Windows certificate store (`ssl.enum_certificates`, server-auth only) or the Linux system bundle. |
| `apply()` | If `USE_OS_TRUST`: sets `SSL_CERT_FILE` to that bundle, which httpx and google-genai honour for **outgoing** TLS only. If `FORCE_IPV4`: wraps `socket.getaddrinfo` to return IPv4 first. |

Why not `truststore.inject_into_ssl()`: it replaces `ssl.SSLContext` process-wide and breaks the HTTPS server (FAILURES #5).

### `app/cache.py`: disk cache

- **Imports.** Stdlib: `hashlib`, `json`. Internal: `config`.

| Name | What it does |
|---|---|
| `_path(namespace, key)` | `data/cache/<namespace>/<sha1(key)[:16]>.json` |
| `get(namespace, key)` | Cached value or `None`. |
| `put(namespace, key, value)` | Writes the value and returns it (so calls can be chained). |

### `app/ratelimit.py`: pacing for free-tier limits

- **Imports.** Stdlib: `asyncio`, `threading`, `time`.

| Name | What it does |
|---|---|
| `RateLimiter(per_minute)` / `.wait()` | Async: spaces calls `60/per_minute` seconds apart (vision, pricing). |
| `SyncRateLimiter(per_minute)` / `.wait()` | Same for code in worker threads (identification web search). |

### `app/explain.py`: friendly errors

| Name | What it does |
|---|---|
| `explain(exc)` | Maps API errors to actions: invalid key, quota used up (429), model not found (404), permission (403), other. Used by the server and the sweep. |

### `app/quality.py`: per-frame capture check

- **Imports.** Third-party: `cv2`, `numpy`. Internal: `config`.

| Name | What it does |
|---|---|
| `decode(jpeg)` | JPEG bytes → BGR image. |
| `_glare(grey)` | Share of pixels ≥ 250, **excluding quadrilateral bright patches** (the A4 sheet must not count as glare, FAILURES #1). |
| `assess(img)` | Returns `{blur, glare, brightness, problems[], ok}`. Blur = variance of the Laplacian on a 640 px copy. |

### `app/tracker.py`: following the camera along a shelf

- **Imports.** Stdlib: `dataclasses`. Third-party: `cv2`, `numpy`.
- **Constants:** `WORK_W` 640 (tracking width), `MIN_INLIERS` 25, `NEW_TERRITORY` 0.45.

| Name | What it does |
|---|---|
| `Placement` | Per-frame result: `chain`, `H` (3×3 frame → shelf coordinates), `keyframe`, `lost`, `scale`. |
| `ShelfTracker._features(img)` | ORB keypoints on a 640 px greyscale copy. |
| `ShelfTracker.add(img, usable)` | Matches against the previous frame (`estimateAffinePartial2D` + RANSAC) and composes the transform. On failure it starts a new **chain** and sets `lost`. A frame becomes a keyframe if it is usable and at least 45% of its span is new shelf. |
| `to_shelf(placement, box_norm, w, h)` | Vision box (0–1000) → shelf-coordinate box `(x0, y0, x1, y1)`. |
| `sheet_to_shelf_scale(placement, corners_px, area_cm2)` | cm per shelf unit, from the A4 corners seen in that frame. |

### `app/vision.py`: perception per keyframe

- **Imports.** Stdlib: `hashlib`, `json`, `logging`, `time`. Third-party: `google.genai`. Internal: `cache`, `config`, `ratelimit.RateLimiter`.

| Name | What it does |
|---|---|
| `SCHEMA` | JSON schema the model must fill: `reference_sheet`, `spines[]` (box, orientation, legibility, verbatim title/author/publisher text, colour), `items[]` (box, category, description, material, brand_model, search_term), `wall` (four corners, shelving units with `shelf_count`, non-rectangular note), `view_blocked`. |
| `RULES` | Shared instructions, including **copy text exactly, never complete a title from memory**. |
| `SHELF_TASK`, `FRONT_TASK`, `WALL_TASK`, `ROOM_TASK`, `TASKS` | Task text per segment kind. |
| `SPINE_KINDS` | `("shelf", "front")`: kinds whose spines are kept. |
| `client()` | Shared `genai.Client`, recreated when the running event loop changes (the doctor runs in its own loop before the server; FAILURES #12). |
| `analyse(jpeg, kind)` | Cache lookup keyed by image + prompt + schema + model, then rate limit, then `generate_content` (2 retries with back-off on 503/429/time-out, FAILURES #15) (JSON mode, temperature 0, high media resolution), then parse. Adds `_meta` (latency, tokens) and drops spines for non-spine kinds. |

### `app/scale.py`: metric scale from an A4 sheet

- **Imports.** Third-party: `cv2`, `numpy`. Internal: `config`.

| Name | What it does |
|---|---|
| `_order(pts)` | Orders four points as top-left, top-right, bottom-right, bottom-left. |
| `find_sheet(img, box_norm)` | Inside the model's rough box (+15% padding): Otsu threshold, largest convex quadrilateral, `cornerSubPix`, A4-proportion check. Returns precise corners or `None`. |
| `plane_homography(corners)` | Homography from image px to cm on the sheet's plane (orientation picked from side lengths). |
| `to_plane(Hcm, pts)` | Applies the homography to points. |
| `box_px(box_norm, w, h)` | 0–1000 box → pixel corners. |
| `measure_box(Hcm, box_norm, w, h)` | (width_cm, height_cm) of a box lying in the sheet's plane. |
| `cm_per_px(Hcm, corners)` | Average scale at the sheet (approximate item sizes). |
| `wall_size(Hcm, points_norm, w, h)` | Wall width and height in metres from its four corners, on the wall plane. |

### `app/merge.py`: one entry per physical book

- **Imports.** Stdlib: `statistics.median`. Third-party: `rapidfuzz.fuzz`. Internal: `identify.norm`.

| Name | What it does |
|---|---|
| `iou(a, b)` | Intersection-over-union of two shelf boxes. |
| `_box(cluster)`, `_text(cluster)`, `_best_reading(dets)` | Cluster's median box, normalised text, and most legible reading. |
| `cluster(dets)` | Groups detections per (shelf, tracking chain) by IoU ≥ 0.3, **never merging two detections from the same frame**. It joins broken chains only through matching legible text and returns warnings when a chain broke. |

### `app/identify.py`: spine text → real work

- **Imports.** Stdlib: `re`. Third-party: `httpx`, `rapidfuzz.fuzz`. Internal: `cache`, `config`, `shops`, `ratelimit.SyncRateLimiter`. Also `google.genai.types`, `pricing._cited` and `vision.client`, imported inside `_web_search`.

| Name | What it does |
|---|---|
| `norm(s)` | Lower-case alphanumerics and spaces. |
| `_title_score(read, candidate)` | Mean of token_sort and token_set similarity. |
| `_coverage(read, title)` | Share of the catalogue title's words actually read. **Below 0.5 means no match**, so "War" never becomes *War and Peace*. |
| `_score(spine, title, subtitle, authors)` | Title score combined with the author score. Title-only reads are capped; a one-word title can't auto-accept. |
| `_cm(value)` | Parses "19.80 cm". |
| `_google(spine)` | Google Books search (optional key), cached. |
| `_open_library(spine)` | Open Library search, cached. |
| `_web_search(spine)` | Gemini + search grounding fallback (only if `PRICING_SEARCH`); a candidate must be cited. |
| `identify(spine)` | Tries Google Books, Open Library, web search, then bookshop listings. Accepts at ≥ 85, review at 65–85, otherwise **unidentified with an empty title**. Edition and ISBN only if the spine's publisher matches. |

### `app/shops.py`: retailer listings (no language model)

- **Imports.** Stdlib: `datetime`, `re`, `dataclasses`, `html.unescape`. Third-party: `httpx`, `rapidfuzz.fuzz`. Internal: `cache`.

| Name | What it does |
|---|---|
| `Shop(name, base, currency, condition)` | A Shopify bookshop. |
| `BOOK_SHOPS` | Shops per country: IN has Crossword (new) and Kitabay (used); US has Book Outlet (new) and SecondSale (used). |
| `IKEA` | IKEA market code, language and currency per country. |
| `VARIANTS` | Listing words meaning "a different product": audio, translations, box sets… |
| `shopify_search(shop, query)` | `/search/suggest.json`, cached with a retrieval date. |
| `match_book(products, title, author, isbn)` | Keeps listings that are in stock, priced, not a variant, match the title (coverage ≥ 0.5, score ≥ 85) and mention the author's surname. Sorts by ISBN match, then title closeness, then price. |
| `book_listing(shop, title, author, isbn)` | Best listing at one shop, as a money dict with URL and date. |
| `ikea_range(query, country)` | IKEA product search, filtered by product type; returns the 25th–75th percentile comparables as `low`/`high`, each a real linked product. |
| `catalogue_candidates(title_text, author_text, country)` | Bookshop listings used as last-resort identification evidence; skips translation, audio and box-set listings unless the spine says so. |

### `app/pricing.py`: prices with evidence

- **Imports.** Stdlib: `asyncio`, `datetime`, `logging`, `re`, `time`. Third-party: `httpx`, `google.genai.types`. Internal: `cache`, `config`, `shops`, `ratelimit.RateLimiter`, `vision.client`.

| Name | What it does |
|---|---|
| `fx_rate(src, dst)` | (rate, date, source URL) from ECB rates via Frankfurter, cached per day. |
| `price_books(books, country, currency)` | Main entry: `_book_from_shops` for each book, then grounded search for missing figures if `PRICING_SEARCH`. Returns `{i: {replacement_cost, used_value, review[]}}`. |
| `_book_from_shops(book, country, currency)` | Local shops by condition (new → replacement, used → used value), then other countries' shops (converted). |
| `_convert(listing, currency)` | Listing → packet money field. A foreign currency is converted with `converted: true` and the rate and date. |
| `price_items(items, country, currency)` | IKEA comparable range (`exact: false`). Exact grounded price for brand/model items only if `PRICING_SEARCH`. |
| `_grounded(prompt)` | Gemini call with the Google Search tool. Returns text plus grounding supports and updates `stats`. |
| `_cited(line, supports)` | The support whose segment covers this output line. |
| `_parse(text, supports)` | Parses `BOOK n \| NEW \| price \| CUR \| seller \| condition` lines. **Uncited lines are dropped as fabricated.** |
| `_money(row, currency, today)` | Grounded row → money field (with conversion). |
| `_grounded_books`, `_grounded_items` | Batched grounded lookups (5 per call), cached. |
| `_resolve(uri)` | Follows grounding redirect links to the real page. |

### `app/room.py`: room size

- **Imports.** Stdlib: `statistics.mean`, `statistics.median`.

| Name | What it does |
|---|---|
| `compute(walls)` | Assumes a rectangle. Length = mean of walls 1 and 3, width = mean of walls 2 and 4, height = median. Gives floor area, gross wall area (openings not subtracted) and shelved wall area, in m² and ft². Confidence comes from the wall count and opposite-wall agreement. Fields are `None` plus notes when a direction is missing. |

### `app/packet.py`: the claim packet (totals in code)

- **Imports.** Stdlib: `re`, `statistics.median`, plus `copy` and `datetime` inside `build_customer`. Third-party: `rapidfuzz.fuzz`. Internal: `config`, `pricing`, `identify.norm`.

| Name | What it does |
|---|---|
| `build(sweep, books, warnings, item_prices, sample, second, room_result)` | Builds the verified packet. Applies policyholder facts (exclusions, first edition, signed, art print or original). Sets appraisal flags (signed, first edition, published before 1950, above threshold; these are never priced). Collects review-queue reasons, shelf run, totals, the locale comparison, metrics (latency per stage, tokens, barge-ins, cost) and the method notes. |
| `compute_totals(books, items, currency, shelf_run_m, excluded)` | **The only place totals are computed**; shared by both packets. |
| `find_line(packet, target)` | Resolves a book id, an item id (`#03`) or a title/description (fuzzy ≥ 80). |
| `build_customer(original, corrections)` | Deep-copies the verified packet and applies corrections. Each changed line gets `customer_advised`, `user_comment`, `system_values` and the policyholder source. Missed books are added as `C-nn`. Adds review entries, recomputes totals, and records `based_on.verified_totals`. **Never modifies the original.** |
| `book_preview(b, prices)`, `item_preview(i)` | Compact rows for the live inventory event. |
| `_fact_hits`, `_empty_money`, `_year` | Helpers. |
| `CUSTOMER_SOURCE` | "Stated by the policyholder (customer-advised, not independently verified)". |

### `app/report.py`: HTML report

- **Imports.** Third-party: `jinja2.Environment`.

| Name | What it does |
|---|---|
| `TEMPLATE` | One self-contained HTML page: KPIs, room, books, items, review queue, exclusions, locale check, method, latency and cost. Customer-advised packets get an amber banner, the verified totals, and `customer-advised` tags with the user's comment. |
| `render(packet)` | Renders the template (autoescaped). |

### `app/sweep.py`: the orchestrator

- **Imports.** Stdlib: `asyncio`, `datetime`, `json`, `logging`, `re`, `time`, `uuid`, `collections.defaultdict`, `statistics.median`. Third-party: `rapidfuzz.fuzz`. Internal: `config`, `identify`, `merge`, `packet`, `pricing`, `quality`, `report`, `room`, `scale`, `vision`, `explain.explain`, `tracker.{ShelfTracker, sheet_to_shelf_scale, to_shelf}`.
- **Constants:** `A4_AREA`, `STANDARD_HEIGHT_CM` 19.8 (last-resort scale prior), `VISION_WORKERS` 3.

| Method | What it does |
|---|---|
| `Sweep(notify, country, currency, device)` | Creates the sweep folder, state, 3 vision workers and the pricing loop. `notify(event)` is the server's broadcaster. |
| `start_segment(kind, label)` | Wall, shelf, front (auto-labelled F1, F2…) or room. **The same step asked for again** (no frames yet, or under 8 s old) returns the current segment, so it never splits one group into two (FAILURES #14). New spine labels get a `ShelfTracker`; a label seen before keeps its tracker. |
| `set_locale(country, currency)` | Changes the valuation locale. |
| `add_fact(kind, target, note)` | Records a policyholder statement ("last" resolves to the last book read; "current" to the current shelf). |
| `add_frame(jpeg)` | Decode, quality, save, `frames.jsonl`, tracking or keyframe rules, coaching, queue the keyframe, `frame` event. |
| `shelf_progress()` | Shelves swept vs shelves counted in the wall shots (missed-shelf check). |
| `where(seg)` | Human wording for coaching ("shelf A2" or "the books in front of the camera"). |
| `coach(key, text, every_s)` | Throttled tip to the page and the agent; logged. |
| `_vision_worker()` | Queue consumer: `vision.analyse`, saves `.vision.json`, calls `_ingest`; errors become a friendly warning. |
| `_ingest(frame, seg, placement, img, result)` | Sheet → homography; view-blocked coaching; spines → detections (direct cm); unreadable / no-sheet coaching; items; wall size, units and shelf counts; then `publish_inventory`. |
| `_add_item(frame, it, img, corners, Hcm)` | De-duplicates items (same category, description ≥ 75). Approximate size at the sheet's depth. Art triggers the "original or print?" question. |
| `books()` | `merge.cluster` → positions → book dicts with identification → `_measure`; warnings include `_duplicate_segments` (two segments sharing 3+ spine readings = possible double count). |
| `_measure(books)` | Spine cm from, in order: the sheet in the same frame, the sheet elsewhere on that shelf, catalogue heights of identified books, then the 19.8 cm prior (flagged). |
| `identify_new(books)` | Identifies new readings in threads (`LOOKUP_CONCURRENCY` at a time) and caches by reading key. |
| `publish_inventory()` | Identify, then the `inventory` event. |
| `_pricing_loop()` / `price_pending(minimum)` | Prices identified books in the background, in batches. |
| `finish(vision_wait_s)` | Drain (`_drain_vision`: waits only while vision makes progress, returns the frames left unanalysed), identify, then price books, items and the second-locale sample side by side, room, `packet.build`, write the JSON and report, time to packet. Runs once: a second caller awaits the first. |
| `add_correction(...)` | Validates the target against the packet, parses amounts ("₹1,250" → 1250) and records the correction. |
| `build_customer_packet()` | Builds and writes the customer-advised JSON and report, with a version number. |
| `_log_event(e)` | Appends to `events.jsonl`. |

### `app/live.py`: the voice agent

- **Imports.** Third-party: `google.genai.types`. Internal: `config`.

| Name | What it does |
|---|---|
| `SYSTEM` | System prompt: confirm the locale (never assume it), adapt to walls, shelves or front view, act on `[CAPTURE MONITOR]`, repeat only `[INVENTORY]` numbers, record facts and corrections, end and read the packet summary exactly. |
| `TOOL_NAMES`, `tools()`, `_s()` | Function declarations (section 7.3). |
| `connect_config()` | Audio output, voice, transcriptions, tools, and voice activity detection (LOW sensitivity, 200 ms padding, 800 ms silence, interrupts on speech). |
| `packet_summary(p)` | `[PACKET READY] …` spoken summary built in code (no-walls aware). |
| `customer_summary(p)` | `[CUSTOMER PACKET READY] …` with customer vs verified totals. |

### `app/server.py`: web server and live bridge

- **Imports.** Stdlib: `asyncio`, `base64`, `contextlib`, `json`, `logging`, `os`. Third-party: `fastapi` (`FastAPI`, `HTTPException`, `Request`, `WebSocket`, `WebSocketDisconnect`, responses, `StaticFiles`), `google.genai`, `google.genai.types`. Internal: `config`, `live`, `explain`, `sweep.Sweep`, plus `qrcode` (inside `phone_qr`).
- **Globals:** `app`, `SESSIONS` (sweep id → `{sweep, ui: set[WebSocket], live, user_spoke, …}`), `KEY_STATUS`.
- **Logging:** the console shows warnings; `data/app.log` gets everything.

| Name | What it does |
|---|---|
| `start_key_check()` / `check_key()` | Background key probe at start-up, so `/api/health` reports `checking`, `ok`, `missing`, `invalid` or `unknown`. |
| `_session(id)` | Looks up a session or returns 404. |
| `_broadcast(id, event)` | Sends to every page of the sweep. Coaching goes to the agent; inventory goes to it at most every 20 s. |
| `_tell_agent(s, text)` | `send_realtime_input(text=…)` to the live session. |
| `create_sweep`, `upload_frame`, `segment`, `finish`, `_finish` | REST endpoints, plus the packet broadcast and the summary to the agent. |
| `_add_correction`, `_build_customer`, `add_correction`, `customer_packet` | Customer-advised flow (shared by the tools and the page). |
| `_run_tool(s, fc)` | Tool dispatcher (section 7.3), including the locale guard and the missed-shelf check. |
| `live_ws(ws, id)` | Connects Gemini Live and runs `upstream()` (page → model) and `downstream()` (model → page). Tool calls run as tasks. Audio arriving with an interruption is dropped. Errors are explained. |
| `health`, `phone_qr`, `get_packet`, `index`, `js`, `ui_js`, `bargein_js`, `avatar_js`, `favicon` | Small GET handlers. |

### `app/doctor.py`: setup checks and model selection

- **Imports.** Stdlib: `asyncio`, `json`, `os`, `re`, `sys`, `time`; heavy imports happen inside functions.

| Name | What it does |
|---|---|
| `say(status, text, fix)` | Prints `[ OK ]`, `[WARN]` or `[FAIL]` with a fix line; failures are collected. |
| `check_python()`, `check_packages()` | Python 3.11–3.13 and the required imports. |
| `model_candidates(models)` | Live models (`bidiGenerateContent`) and Flash text models, newest first; `.env` choices go first. |
| `pick_vision`, `pick_pricing`, `pick_live` | Real test calls: vision on a synthetic image, grounded pricing (at most 2 tries; stops at the first quota error), a live session that must answer. |
| `check_services()` | Google Books, Open Library, each price shop, IKEA and exchange rates. |
| `main(skip_models)` | Runs everything, writes `data/models.json` (including `retry_better` when a better model was only temporarily unavailable) and sets the active config. |
| `finish(ready)` | Prints READY or a numbered fix list. |

### `app/launch.py`: the beginner launcher

- **Imports.** Stdlib: `os`, `re`, `socket`, `sys`, `threading`, `time`, `webbrowser`, `pathlib.Path`. Internal (inside `main`): `config`, `doctor`, `cert`, `server.app`. Third-party: `uvicorn`, `httpx`, `qrcode`.

| Name | What it does |
|---|---|
| `ensure_key()` / `read_key()` / `write_key()` | Creates `.env` and asks for the key if it's missing. |
| `free_port(preferred)` | First free port from the preferred one. |
| `open_when_ready(url, health)` | Opens the browser once `/api/health` answers. |
| `show_phone_instructions(url)` | Phone address plus a console QR code. |
| `main()` | Key, doctor (full model test if there is no `models.json` or it says `retry_better`), certificate, then two Uvicorn servers (HTTP 8000 local, HTTPS 8443 LAN) on the same app via `asyncio.gather`. Sets `PHONE_URL`. |
| `pause_and_exit(code)` | Keeps the console open on errors. |

### `app/cert.py`: HTTPS certificate for the phone

- **Imports.** Stdlib: `datetime`, `ipaddress`, `socket`. Third-party: `cryptography`. Internal: `config`.

| Name | What it does |
|---|---|
| `lan_ip()` | The computer's LAN address (UDP connect trick; no packet sent). |
| `main(quiet)` | 30-day self-signed RSA certificate for localhost, 127.0.0.1 and the LAN IP, written to `data/cert.pem` and `data/key.pem`. |

### `app/replay.py`: offline re-run

- **Imports.** Stdlib: `asyncio`, `json`, `sys`, `pathlib.Path`. Internal: `sweep.Sweep`.

| Name | What it does |
|---|---|
| `replay(src)` | Replays frames, segments, locale and facts from `frames.jsonl` and `events.jsonl` into a new `Sweep`, then `finish()`. Vision and lookups come from the cache, so it's free unless prompts changed. |

## 9. Module reference: front end (`web/`)

Plain HTML, CSS and JS with no framework and no bundler. `index.html` loads `bargein.js`, then `ui.js`, then `app.js`, then the module `avatar.js`. The only external JS is the avatar's (three.js, TalkingHead, HeadAudio from jsDelivr, pinned in the import map and in `avatar.js`); the app works without it.

### `web/index.html`: layout and design system

- **Design tokens** on `:root`:
  - brand `#6366F1`, secondary `#10B981`, danger `#EF4444`, warning `#F59E0B`;
  - background `#0F172A`, surface `rgba(30,41,59,.85)`, text `#F8FAFC` / `#94A3B8`;
  - lighter `--*-text` variants keep small text at WCAG AA contrast.

  `:root[data-theme="light"]` overrides them for light mode.
- **Fonts and icons:** Inter from Google Fonts (falls back to the system font). Lucide icons are inlined once as an SVG `<symbol>` sprite and used with `<svg><use href="#i-…">`.
- **Layout:**
  - fixed sidebar (`.sidebar`; off-canvas below 1024 px with `body.nav-open`), sticky top bar (search, bell panel, profile panel);
  - `main` with the page head, alerts and `.flow`: `.kpis` (3 cards) plus `.grid` (camera, conversation, chart, items/walls, books table, activity table, packets, corrections, settings);
  - below 760 px, `.grid` becomes `display: contents`, so the camera is first, then the KPIs (a swipeable row), then the rest.
- **State classes on `<body>`:**
  - `live`: call connected; shows the step buttons and End.
  - `done`: packet ready; hides the live controls and unlocks corrections.
  - `nav-open`: drawer open.
- **Interaction:** hover lifts 2 px, active scales to 0.95, focus shows a 2 px indigo ring, and `prefers-reduced-motion` disables animation.

### `web/app.js`: the call

| Function | What it does |
|---|---|
| `checkSetup()` | Reads `/api/health`: key state (banner if missing or invalid), models, locale, phone link. Enables Start. |
| `start()` | Camera and mic permissions, `POST /api/sweeps`, two AudioContexts (mic, and playback with a `GainNode` for ducking), then the WebSocket. Playback uses the avatar's context and feeds its input when `AgentAvatar.ready` resolves within 4 s; otherwise a 24 kHz context straight to the speakers. The mic `ScriptProcessor` (2048 samples, ~43 ms) feeds `barge.update`. While the gate is shut it sends silence and keeps a 10-chunk pre-roll. |
| `onReady(model)` | Starts the frame timers (`sendHires` every 500 ms, `sendLive` every 1 s) and sets `body.live`. |
| `grab`, `sendHires`, `sendLive`, `widest` | Canvas capture (1280 px to the pipeline, 640 px to the agent) and zoom-out on phones. |
| `segment(kind)` | Manual step buttons → `POST /segment`. |
| `handle(m)` | Dispatches every server event (section 7.2) to rendering, `UI.log` and `UI.alert`. |
| `inventory(m)` | Renders the books table (status pills, `data-text` for search), items and walls, then `UI.metrics`. |
| `transcript(speaker, text)` | Chat bubbles. |
| `play(data)` | Schedules 24 kHz PCM into the playback context. |
| `agentSpeaking()`, `setVolume()`, `showTalk()` | Speaking state, ducking and the talk indicator. |
| `localBargeIn()` | Ducks the agent's voice, tells the server, and starts the 2 s confirm timer (`reject()` on timeout). |
| `confirmBargeIn()` / `stopPlayback()` | On `interrupted`: drop the queued audio and mark the transcript. |
| `loadPacketLines`, `renderCorrections`, `#corrForm` / `#buildCustomer` handlers | Corrections UI. |
| `#say` handler | Typed message to the agent. |
| `toolActivity(m)`, `setLocaleText`, `showSegment`, `money`, `esc`, `resample`, `b64` | Helpers. |

### `web/ui.js`: dashboard behaviour (`UI` module)

| Function | What it does |
|---|---|
| `log(who, action, status)` | Adds a row to the Recent activity table (Agent, You or System; Completed, Pending or Failed; sweep clock). |
| `alert(kind, text)` | Adds a notification and updates the bell count. |
| `metrics(m, money)` | KPI cards (value, books, unreadable count, identification rate) and the value history behind the trend badge and sparkline. Logs newly identified, priced or unreadable books and new items and walls once each. Calls `chart()`. |
| `spark()` | SVG sparkline of replacement value. |
| `chart()` | Value-by-shelf bars (`--h` CSS height), with a Value/Books toggle and accessible labels and tooltips. |
| `applyFilter()` | Search box → hides non-matching book rows and item cards. |
| `setNav`, `toggle`, `setTheme`, `init` | Drawer, dropdown panels (Esc and outside-click close), theme (`localStorage` with try/catch), scroll-spy (`IntersectionObserver` highlights the current section). |
| `startClock()` | Sweep clock for activity times. |

### `web/avatar.js`: the agent's face (module)

A 3D avatar ([TalkingHead](https://github.com/met4citizen/TalkingHead) 1.7.0 on three.js 0.180.0) with lips driven by [HeadAudio](https://github.com/met4citizen/HeadAudio), which detects mouth shapes from the sound itself. That is what lets it follow the live model's audio stream, which comes with no text timings. Everything runs in the browser; nothing is sent anywhere.

| Member | What it does |
|---|---|
| `window.AgentAvatar.ready` | Resolves to `{ctx, input}` (the avatar's AudioContext and the node to play the agent's voice into) or `null` if the avatar could not load (no WebGL, CDN not reachable); the face is then hidden. |
| `load()` | Builds the head, loads the GLB, registers the HeadAudio worklet and model, connects speech → HeadAudio, and delays the voice by `LIPSYNC_DELAY_S` (80 ms) to match HeadAudio's lag. On each new reply the avatar looks at the camera and, on the computer, gestures. |
| `place()` | Computer: top of the Conversation card (`#faceSlot`, `upper` view). Below 760 px: a bubble on the camera (`#stage.has-face`, `head` view). Re-run on width changes. `FRAMING` holds the camera offsets for each view. |

The avatar is TalkingHead's Ready Player Me sample (`brunette.glb`), licensed CC BY-NC 4.0: **non-commercial use only**. For production, set `AVATAR_URL` to your own GLB with a Mixamo-compatible rig plus ARKit and Oculus viseme blend shapes.

### `web/bargein.js`: barge-in gate (pure logic)

| Member | What it does |
|---|---|
| `BargeIn.update(rms, ms, agentSpeaking)` | States `idle`, `guarding` and `open`. While the agent speaks: learns the echo for the first 600 ms. After that, fires `"barge"` once when sound stays above `max(0.05, 2.5 × echo, 6 × noise)` for 320 ms (gaps over 130 ms reset the count). Background noise is learned only while the agent is silent (fast down, slow up). |
| `micOpen` | False while guarding, which tells the page to send silence. |
| `reject()` | The model didn't confirm: close the gate, raise the echo level to the sound's peak, and wait 1.5 s before firing again. |
| `rmsOf(samples)` | Loudness of a chunk. |

## 10. Scripts, evaluation and packaging

| File | What it does |
|---|---|
| `scripts/windows_setup.bat` | `:find_python` (py launcher 3.12/3.13/3.11, the user install path, or `python`), winget install if missing, `.venv`, pip install (skipped when `requirements.txt` equals `.venv\requirements.installed`), `.env` from the example. Friendly failure messages. |
| `START_HERE.bat` / `START_PHONE.bat` | `call windows_setup.bat`, then `python -m app.launch` (`--phone`). Sets `PYTHONUTF8=1` for the QR code and accents. |
| `RUN_TESTS.bat` | Setup, then `pytest -q`. |
| `MAKE_ZIP.bat` / `scripts/make_zip.py` | ZIP without `.venv`, `.env`, caches, logs, certificates, `models.json`, `ca-bundle.pem` and sweeps (`--with-sweeps` includes them). Keeps `start.sh` executable. |
| `start.sh` | Same flow on macOS/Linux (`cmp` instead of `fc`). |
| `eval/score.py` | `score(packet, gt_dir)`: count, titles (correct vs confidently wrong), spine dimensions (MAPE), book prices (MAPE), item recall, floor and wall area, time to packet. Each bar gets pass/fail plus the interpretation. `to_markdown` writes `results.md` and `results.json` next to the packet. |

## 11. Data contracts

### 11.1 Inventory event (`Sweep.publish_inventory` → page)

```json
{"type":"inventory",
 "books":[{"shelf":"A1","position":2,"status":"identified|unidentified|reading","title":"","author":"",
           "read":"verbatim spine title","h":19.8,"t":3.1,"price":549,"frame_ref":"frames/00012.jpg"}],
 "items":[{"id":"item-01","category":"lighting","description":"","brand_model":"","frame_ref":""}],
 "walls":[{"label":"1","width_m":3.62,"height_m":2.71, "...":"..."}],
 "warnings":["shelf A2: tracking broke 1 time(s); …"],
 "counts":{"books":10,"identified":8,"items":4,"walls":2}}
```

### 11.2 `claim_packet.json`

Contains every field required by the assignment's contract, plus these additions:

- **`sweep`:** `frames_saved`, `keyframes_analysed`, `capture_modes`.
- **`room`:** `*_ft2`, `shape_handling`, `walls_measured`, `frame_refs`, `notes`.
- **`books[]`:**
  - evidence: `frame_t_s`, `frame_box_2d`, `spine_text_read`, `orientation`, `sightings`, `scale_method`, `catalogue{source,url}`;
  - money fields can carry `fx_url`;
  - customer packet only: `customer_advised`, `user_comment`, `system_values`, `identified_by`.
- **`items[]`:** `material`, `frame_t_s`, `frame_box_2d`, `sightings`, `dimensions_method`, `replacement_cost{high_source, high_url, converted}`.
- **`totals`:** `books_needs_appraisal`, `excluded_by_policyholder`, `currency`.
- **Top level:**
  - `excluded`, `policyholder_statements`, `locale_comparison{note, books[]}`;
  - `metrics{latency_s, vision, pricing, barge_ins, estimated_cost_usd, cost_note, time_to_packet_s, frames_not_analysed}`;
  - `method{…}`, `packet_type` (`verified` or `customer_advised`), `title`;
  - customer packet only: `based_on{packet, sweep_id, verified_totals}`, `customer_corrections[]`, `customer_version`.

### 11.3 Correction object

```json
{"target":"A1-03 | item-02 | a title | new","title":"","author":"","replacement_cost":300.0,
 "used_value":null,"comment":"policyholder's words","t":312.4}
```

## 12. Rules the code must keep

These are what the assignment scores. Changes must not break them.

1. **No model-written facts with money attached.** Prices come from shop listings or cited grounding lines (`pricing._parse` drops uncited lines). Titles come from catalogue or shop records that match the verbatim spine text (`identify`).
2. **Empty when unknown.** An unidentified book has an empty title, a missing price stays `null` and goes to review, and an unmeasured room stays `None` with a reason.
3. **Totals only in `packet.compute_totals`.** The page's KPI value is labelled a live estimate.
4. **Everything traceable:** a `frame_ref` for each book and item, plus a `url` and `retrieved_at` for each price; conversions are labelled.
5. **Appraisal, not pricing,** for signed, first-edition, pre-1950 and above-threshold books, and for art unless it's stated to be a print.
6. **The verified packet is never modified.** Customer values live only in the customer-advised packet, labelled and reviewed.
7. **The agent can't assert facts about the policyholder** (locale) before they speak. The server enforces this.

## 13. Tests

Run `python -m pytest -q` (or `RUN_TESTS.bat`). No API key is needed; the Node-based barge-in test is skipped if Node is missing.

| Test | Covers |
|---|---|
| `test_a4_homography_measures_spine_under_perspective` | A4 corners + homography measure a 20 cm spine under perspective |
| `test_sheet_rejected_when_not_a4_shaped` | Non-A4 shapes are not used as a reference |
| `test_a4_sheet_is_not_glare_but_a_highlight_blob_is` | Glare check ignores the sheet but catches hotspots |
| `test_merge_counts_each_book_once_across_overlapping_frames` | De-duplication across frames |
| `test_merge_never_joins_two_detections_from_the_same_frame` | Same-frame rule |
| `test_merge_joins_broken_chains_only_through_matching_text` | Broken tracking chains plus a warning |
| `test_room_rectangle_and_areas`, `test_room_unknown_without_both_directions` | Room maths and the "unknown" case |
| `test_price_without_grounding_citation_is_dropped` | Fabricated-price rule |
| `test_shop_matching_skips_translations_out_of_stock_and_wrong_author` | Shop listing rules (Punjabi edition, audio, author, fragment) |
| `test_identify_refuses_weak_matches` | "War" ≠ *War and Peace*; edition only with publisher match |
| `test_packet_totals_are_sums_of_lines_and_rare_books_are_not_priced` | Totals and appraisal flags |
| `test_no_walls_statement_explains_missing_room_and_front_labels` | Front labels, missed-shelf count, no-walls reason |
| `test_front_only_sweep_says_why_room_is_missing` | Front-only sweep wording in the packet and summary |
| `test_customer_packet_is_separate_labelled_and_recomputed` | Original untouched, labels, totals, missed book, report |
| `test_sweep_correction_validates_target_and_parses_amounts` | Unknown target refused; "₹1,250" parsed; files written |
| `test_vision_client_is_recreated_for_a_new_event_loop` | Regression for FAILURES #12 (first keyframe lost after start-up) |
| `test_repeated_step_reuses_the_segment_instead_of_double_counting` | FAILURES #14: repeated step reuses the segment, shelf tracker kept, real new group still allowed |
| `test_same_books_in_two_segments_are_flagged_as_possible_double_count` | Double-count safety net |
| `test_bookshop_fallback_ignores_translated_editions` | "Ikigai - Tamil" is not used for a spine reading "IKIGAI" |
| `test_synthetic_pan_counts_each_book_once[shelf/front]` | Whole `Sweep` on a synthetic 110-book pan: count within 5%, keyframes reduced, height within 10% |
| `test_bargein_detector` → `bargein.test.cjs` | 9 scenarios: echo, cough, click, real speech, no stammer, warm-up, re-arm, noisy room |

## 14. How to extend

- **New valuation country:**
  1. Add its Shopify bookshops to `shops.BOOK_SHOPS[...]` (check `/search/suggest.json` works).
  2. Add its IKEA market to `shops.IKEA`.
  3. Add a display name to `pricing.COUNTRY_NAMES` and `web/app.js COUNTRY_NAMES`.
  4. Set `COUNTRY` and `CURRENCY` in `.env`.
- **New agent tool:**
  1. Declare it in `live.tools()` and add it to `TOOL_NAMES`.
  2. Describe when to use it in `SYSTEM`.
  3. Handle it in `server._run_tool`.
  4. Optionally add an activity line in `app.js toolActivity`.
- **New coaching rule:** call `self.coach(key, text, every_s)` in `Sweep.add_frame` (per frame) or `Sweep._ingest` (per keyframe).
- **New vision field:**
  1. Add it to `vision.SCHEMA` (and the task text).
  2. Read it in `Sweep._ingest`.

  Changing the prompt or schema invalidates the vision cache automatically.
- **New packet field:**
  1. Add it in `packet.build` (and `build_customer` if relevant).
  2. Show it in `report.TEMPLATE`.
  3. Never compute totals anywhere but `compute_totals`.
- **New dashboard section:**
  1. Add a `.card glass` article in `index.html` (give it an `id` and a nav link for scroll-spy).
  2. Render it from `app.js handle()` or `ui.js`.

## 15. Debugging

- **Server log:** `data/app.log` (full detail). The console shows warnings only.
- **Per sweep:** `events.jsonl` (timeline, including coaching and vision errors), `frames.jsonl` (quality per frame), and `frames/*.vision.json` (raw model output for each keyframe).
- **Re-run after a code change:** `python -m app.replay data/sweeps/<id>`, then `python -m eval.score …`.
- **Model or key problems:** `python -m app.doctor` (delete `data/models.json` to force re-selection).
- **Network:** with `USE_OS_TRUST=1` and `FORCE_IPV4=1` (defaults), outgoing TLS trusts the OS store and DNS prefers IPv4. If shops or catalogues fail, the doctor shows which one.
- **Front end:** the browser DevTools console. The page needs `http://127.0.0.1` or HTTPS for camera access (`isSecureContext`).
