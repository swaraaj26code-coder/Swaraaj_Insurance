# Failure log

> Draft. Sections marked **TODO (real sweep)** are filled in from the recorded demo sweep and its ground truth. Everything below is measured, not estimated.

## Fixed during development (measured)

### 1. The A4 reference sheet was rejected as "glare", so no frame ever had metric scale
- **Symptom:** in the synthetic 110-book pan test, every spine was measured with the standard-format fallback. Median height came out at 19.8 cm against a true 21.7 cm (−8.8%), and shelf run at 3.25 m against 3.56 m (−8.7%).
- **Root cause:** the glare check counted any pixel ≥ 250 as blown out. A white A4 sheet fills about 5% of a frame, above the 4% glare limit. Every frame containing the sheet was therefore marked unusable, never became a keyframe, and never reached the scale stage.
- **Fix:** bright regions that are quadrilaterals (sheet-shaped) are excluded from the glare measure (`quality._glare`). There's a regression test with an A4 sheet (not glare) and a round hotspot (glare).
- **After:** median height 21.6 cm against 21.7 cm (−0.5%), shelf run 3.55 m against 3.56 m (−0.3%), and count 111 of 110 (+0.9%).

### 2. A spine fragment resolved to a longer title
- **Symptom:** the spine text "War" was identified (as a review item) as *War and Peace*. That's a guessed title, which the brief penalises.
- **Root cause:** `token_set_ratio` scores a subset as a perfect match.
- **Fix:** a reading must cover at least half the catalogue title's words before it can be identified at all (`identify._coverage`). There's a test for this.

### 3. Featureless views turned every frame into a keyframe
- **Symptom:** when ORB tracking broke (on a blank wall in the synthetic scene), each frame started a new chain and was sent to vision. 12 of 16 frames were analysed, which would exhaust the free-tier quota during a real sweep.
- **Fix:** while tracking is lost, at most one keyframe per 2 s. The same synthetic pan now uses 4 of 11 frames.

### 4. Corporate network: every lookup hung or failed
- **Root causes:** a TLS-inspecting proxy whose root CA is trusted by Windows but not by Python's `certifi` bundle; blackholed IPv6 (Python tries every IPv6 address before IPv4); Open Library blocked by the proxy; and the keyless Google Books daily quota exhausted on the shared egress IP.
- **Fix:** `USE_OS_TRUST` / `FORCE_IPV4` (`app/net.py`, on by default), optional `GOOGLE_BOOKS_API_KEY`, and a cited web-search fallback for identification.

### 5. The first proxy fix silently broke phone mode
- **Symptom:** with HTTPS on (needed for the phone camera), the server completed the TLS handshake and then dropped every request without a log line.
- **Root cause:** the first version of fix 4 used `truststore.inject_into_ssl()`, which replaces `ssl.SSLContext` process-wide with a client-only implementation. Server-side TLS stopped working.
- **Fix:** build `data/ca-bundle.pem` (certifi plus the OS certificate store) and point `SSL_CERT_FILE` at it. httpx and the Gemini SDK honour that variable for outgoing calls only. Verified on this office network: the HTTPS server answers on localhost and on the LAN address; Google Books, FX, Gemini REST and Gemini Live all complete TLS through the proxy.

### 6. A beginner saw only "Call disconnected"
- **Symptom:** the first real try in a browser failed with no explanation.
- **Root cause:** no API key was configured. The server sent an error, but the page immediately overwrote it with "Call disconnected", and frame uploads carried on into failing vision calls.
- **Fix:**
  - The page checks setup on load and disables Start with a plain-English reason (missing or invalid key, no HTTPS on a phone).
  - Errors stay on screen.
  - Capture starts only after the voice agent confirms it's connected.
  - API errors are translated by `app/explain.py`.
  - `app/doctor.py` tests real calls and picks models the key can use, instead of trusting hard-coded model names.

### 7. The free API key has no Google Search quota, so no prices at all
- **Symptom:** the doctor's real-key test returned `429 RESOURCE_EXHAUSTED` for search-grounded calls on every model, while the same models answered plain vision calls.
- **Root cause:** the free tier gives this key no Google Search grounding quota.
- **Fix:** retailer listings became the primary price source (`app/shops.py`): Shopify bookshops per country and IKEA comparables for items. This is stronger evidence than grounding: the price is the shop's own product data, not model output. Measured on live data: *Atomic Habits* ₹900 (Crossword paperback), *Sapiens* ₹549, *The God of Small Things* ₹450, with US used prices from SecondSale ($4.45 / $5.88 / $3.49). A made-up title got no price and went to review.
- **First version picked the wrong edition:** the Punjabi translation (₹449) instead of the English paperback (₹900). Translations and other variants are now excluded, and the closest title is preferred before the cheapest. There's a regression test for this.

### 8. The voice agent invented the policyholder's country
- **Symptom:** in the first real live test, the agent called `set_locale(US, USD)` while greeting, before the user had said anything.
- **Fix:**
  - The prompt now states the configured default and tells the agent to ask for confirmation.
  - The server refuses `set_locale` until the policyholder has spoken.
- **Re-test:** the agent asked "can you confirm the valuation is for India in Rupees?" and set IN/INR only after the answer.

### 9. Self-audit against the brief: two coaching signals were missing
- **Gap:** the brief asks the agent to notice "blur, glare, occlusion and missed shelves". Only blur, glare and darkness had been built.
- **Fix:**
  - The vision call now reports `view_blocked` (occlusion → spoken coaching).
  - Wall shots count the shelves per bookcase, and `end_sweep` refuses once when fewer shelves were swept.
- **Also corrected:** the README overstated how far the reference app had been run. It said we ran it, but without a key no live intake went through it.

### 10. The agent did not record "no walls" when told so
- **Symptom:** in a live test, the user said "no walls or shelves, just books on my desk". The agent correctly switched to the front view, but never called `record_fact(no_walls)`, so the packet would have called the room a failed measurement.
- **Fix:** the packet decides this deterministically. A sweep with front views and no wall shots is reported as "not measured: front-view sweep, no walls captured". The agent's behaviour is no longer relied on for that fact.

### 11. Barge-in fired on small sounds and made the agent stammer
- **Symptom (user report):** a small sound stopped the agent, then it stammered.
- **Root causes:**
  1. The phone detector fired after 0.2 s above a fixed 0.04 level, and loudspeaker echo of the agent's own voice is louder than that.
  2. An unconfirmed pause resumed playback and re-armed at once, so the same sound paused it again: pause, resume, pause.
  3. The model's own voice detection (set to HIGH sensitivity) also cut the agent off on short sounds. Measured live: a 0.2 s burst interrupted it even at LOW sensitivity.
- **Fix:**
  - A phone-side gate sends the model silence while the agent speaks.
  - It learns the echo level and needs 0.32 s of clearly louder sound.
  - The agent's volume dips instead of pausing.
  - After a false alarm, the bar rises to that level, followed by a 1.5 s cool-down.
  - Model detection is back to LOW.
- **Measured (detector simulation, false interruptions old → new):** 6 s of echo 28 → 0, a cough 1 → 0, 5 s of steady noise 23 → 1.
- **Live chain:** a 0.2 s burst never reached the model; real speech interrupted the agent 1.08 s after it began.

### 12. The first keyframe of the first sweep was lost after a normal start
- **Found by:** the final end-to-end test on a fresh install from the ZIP.
- **Symptom:** `vision_error: Event loop is closed` on frame 0; later frames worked.
- **Root cause:** `START_HERE` runs the doctor in its own `asyncio.run()` before the server starts. The doctor's vision test created the shared Gemini client, whose async connection pool was bound to that loop. When the doctor's loop closed, the server's first vision call reused the dead pool.
- **Fix:** `vision.client()` recreates the client when the running event loop changes.
- **Related:** the doctor had settled on `gemini-3.5-flash-lite` because the better models were over their free daily quota or busy (429/503) that day. `models.json` now records `retry_better`, and the next start re-tests, so a temporary outage doesn't pin the app to a weaker model.

### 13. A network blip failed the whole first-time install
- **Found by:** the final fresh-install test.
- **Symptom:** mid-download, `pypi.org` reset the connection and then stopped resolving. pip's own 5 quick retries ran out within seconds, and setup stopped. A minute later the network was fine again.
- **Fix:** `windows_setup.bat` waits 10 s and retries the whole install once before showing the "check your internet" message. Tested both ways: a retry that succeeds continues, and a second failure still stops with the friendly message.

### 14. The same books were counted twice, and one was matched to its Tamil edition
- **Found by:** the final end-to-end test (fresh install, real models, 9 books on a desk). It counted **18** books and identified "IKIGAI" as **"Ikigai - Tamil"**.
- **Root cause 1:** within one second, the agent called `start_front_view`, the Front button was pressed, and the agent called it again. That made three segments for the same group of books. The first frame landed in F2 and the rest in F3, and de-duplication works only within a segment, so all 9 books appeared in both.
- **Root cause 2:** the bookshop fallback for identification (used when the catalogues are blocked) accepted a translation listing. The read "IKIGAI" covers half of "Ikigai Tamil", which met the coverage rule.
- **Fix:**
  - Repeating the same step while the current segment has no frames, or is under 8 s old, keeps filming into the current segment.
  - Re-starting an existing shelf label keeps its tracker (a reset would mix coordinate systems).
  - Safety net: two segments sharing 3 or more spine readings get a "possible double count" warning in the inventory and the review queue.
  - The identification fallback skips translated, audio and box-set listings, as pricing already did.
  - Regression tests for all three. The E2E check now requires exact titles and an exact count.

### 15. Room walls were lost to one busy moment, and plain walls looked "blurry"
- **Found by:** a room test with the real vision model: four walls of a 4.0 × 3.2 × 2.7 m room, each with an A4 sheet; two had 0.8 × 1.8 m bookcases.
- **Symptom 1:** wall 1 was never measured. One Google `503 high demand` answer lost the only keyframe, because vision calls had no retry.
- **Symptom 2:** wall 4 was never analysed, and the agent was told "frames are blurry, hold the phone steady". A sharp photo of a plain painted wall has almost no fine detail, so its blur score is low and every frame was rejected.
- **Fix:**
  - Vision retries twice with a 3 s / 6 s wait on 503, 429 and time-outs.
  - The blur check applies only to spine views (shelf, front). Walls and room views are judged on glare and darkness. Regression test added.
- **After (measured):**
  - Walls 1–3: 3.92 × 2.68, 3.15 × 2.67 and 3.93 × 2.68 m, against 4.00 / 3.20 / 4.00 × 2.70 m.
  - Floor area 12.35 m² vs 12.80 (−3.5%, bar 10%); wall area 37.86 vs 38.88 m² (−2.6%, bar 15%); shelved wall area 2.85 vs 2.88 m².
  - Both bookcases measured at 80 × 180 cm with 5 shelves.
  - Wall 4 then failed only because the free daily quota was fully used (still 429 after the retries). It was reported as such, not guessed.

### 16. The claim packet took over 5 minutes after "end sweep"
- **Found by:** the policyholder, on a 100 s front-view sweep (`20261030-124814-abf7`): `time_to_packet_s` **323.4**, of which building the packet (identify, price, room) was **15 s**.
- **Root cause 1 (≈ 308 s):** `finish()` waited on `queue.join()` for **every** queued keyframe. Google's free tier was overloaded: calls hung until the 60 s time-out, then 503, then the daily quota ran out (429). Each frame went through 3 attempts (up to ~190 s), 3 workers at a time. Even the successful calls in the logs had a median of **148 s** (min 7 s) for the same reason.
- **Root cause 2:** a per-**day** 429 was retried like a per-minute one, and every later frame repeated the same 3 failing calls.
- **Root cause 3:** shop pricing and catalogue lookups ran one after another: 10 books took **52.8 s** with a cold cache. Identification also ran inside the vision worker, so a shelf of new spines held up vision during the sweep.
- **Root cause 4:** the agent's `end_sweep` and the End button both called `finish()`, which built the packet twice (`calls: 2` in every stage).
- **Fix:**
  - `finish()` waits for queued frames only while vision makes progress: it stops after `FINISH_STALL_S` (60 s, one request time-out) without a result, or `FINISH_VISION_WAIT_S` (180 s) in all. Frames left over go into the review queue by number, with the `app.replay` command that analyses them later (replay waits for all of them).
  - A per-day 429 is not retried, and vision skips the call for later frames until the quota resets.
  - Shop pricing, item pricing and identification run `LOOKUP_CONCURRENCY` (4) lookups at a time. The three finish-time pricing stages run side by side.
  - `finish()` builds once; a second caller waits for the first, and the packet is announced once.
  - Regression tests for the stall cut-off, the single build and the daily-quota cut-off.
- **After (measured):** cold-cache pricing of the same 10 books 52.8 → 25.8 s and of the 7 items 8.2 → 6.4 s, with identical results. For the sweep above, the new rules stop waiting 60 s after the end instead of 308 s. That packet would have reported its 15 unanalysed frames for replay rather than the 7 items the last 2 frames found after ~77 s; `FINISH_STALL_S` sets that trade-off.

## Three worst errors in the demo run — TODO (real sweep)

For each one: what happened (with packet line ids and frames), root cause, and what we'd change.

## Cost per sweep and latency per stage — TODO (real sweep)

Taken from `metrics` in `claim_packet.json`.

| Stage | Calls | Total s | Mean s |
|---|---|---|---|

## With two more weeks

TODO
