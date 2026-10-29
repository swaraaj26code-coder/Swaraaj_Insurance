# Failure log (one page)

> **Source of these errors:** the end-to-end runs on 29 Oct 2026. Each was a fresh install from the ZIP with real Gemini models, using a rendered desk of 9 books (7 titled, 2 blank) and a rendered 4.0 × 3.2 × 2.7 m room. **After the real demo sweep, replace this section with that run's three worst errors.** The full log of 15 issues is in [FAILURES.md](FAILURES.md).

## The three worst errors

**1. Every book counted twice: 18 books for 9 (book-count bar: 5%).**
- **Root cause:** within one second, the agent called `start_front_view`, the Front button was pressed, and the agent called it again. That gave three segments for one group of books. De-duplication works within a segment, so frames split across F2 and F3 counted each book twice.
- **Change:** repeating the same step (no frames yet, or under 8 s old) keeps the current segment. Re-capturing a shelf keeps its tracker. A safety net warns when two segments share 3 or more spine readings.
- **Measured fix:** 18 → **9 of 9**. A regression test covers it.

**2. Confidently wrong edition: "IKIGAI" identified as "Ikigai - Tamil" (wrong-title bar: 3%).**
- **Root cause:** with the catalogues blocked on this network, identification fell back to bookshop listings and accepted a translation listing, because the read covered half of the listing's words.
- **Change:** translation, audio and box-set listings are never identification evidence unless the spine says so.
- **Measured fix:** "Ikigai" correct, 0 confidently wrong. A weak read ("Wings of Fire", score 56) is now left unidentified rather than guessed.

**3. Measurements silently lost: the first keyframe after start-up, a whole wall on one busy answer, and plain walls rejected as "blurry".**
- **Root causes:**
  - the doctor's event loop left a dead async client behind;
  - vision had no retry for 503/429;
  - the blur score measures fine detail, which a plain wall lacks.
- **Change:** the client is recreated per event loop; 2 retries with back-off; the blur check applies to spine views only.
- **Measured fix:** room test from 2 to 3 of 4 walls measured. The 4th failed only because the free daily quota was exhausted. Floor area −3.5% (bar 10%), wall area −2.6% (bar 15%), shelved area 2.85 vs 2.88 m².

## Cost per sweep and latency per stage (9-book end-to-end run, 89.6 s sweep, 6 frames, 2 keyframes)

| Stage | Calls | Total s | Mean s | Note |
|---|---|---|---|---|
| Vision (Gemini 3.5 Flash-Lite, fallback) | 2 | 86.4 | 43.2 | Google under load that day; typically 5–15 s |
| Identification | 2 | 56.1 | 28.1 | Catalogues blocked or out of quota on the office network, so the bookshop fallback was used |
| Book pricing (shops) | 1 | 17.4 | 17.4 | 9 books, local then foreign shops |
| Second-locale pricing (US) | 1 | 14.5 | 14.5 | 10-book comparison |
| Item pricing, room | 1 / 1 | 0.0 | – | no items or walls in this scene |
| **End of sweep → packet** | 1 | **31.8** | – | bar: under 300 s |

- **Tokens:** vision 2,628 in / 1,746 out; no paid search calls (prices come from shop listings). The live voice session for the length of the sweep comes on top.
- **Cost:** on the free AI Studio key the actual cost is **₹0**. To estimate a paid cost, enter Google's list prices for the chosen models in `.env` (`COST_*`), and the packet computes `estimated_cost_usd` from these token counts. We don't quote a price we haven't looked up.

## What we'd do with two more weeks

1. **Real metric depth.** Use ARCore depth or LiDAR on the phone so walls wider than one camera shot, and non-rectangular rooms, can be measured, and so the A4 sheets become optional.
2. **Spine detection.** A dedicated spine detector (fine-tuned open-vocabulary model) for thin and stacked spines, with the vision LLM used only for reading text.
3. **Prices.** More price sources per country (Amazon PA-API, AbeBooks) and a hand-checked price set to calibrate used-value conditions.
4. **Independent checker.** A second agent that re-reads low-confidence spines from the saved frames and reports disagreements (a stretch goal).
5. **Live robustness.** Paid-tier quotas, plus automatic model fail-over mid-sweep instead of only at start-up.
