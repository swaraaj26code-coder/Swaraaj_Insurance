# Architecture note (one page)

```
Phone (Chrome, HTTPS) ── mic 16 kHz + 1 small frame/s ──► FastAPI WebSocket ──► Gemini Live (voice agent: directs, asks, reads back)
   ▲  agent voice, transcript, live inventory, coaching          ▲ [CAPTURE MONITOR] tips · [INVENTORY] progress · [PACKET READY] summary
   └── 2 sharp frames/s (HTTP) ──► Sweep pipeline (separate, testable stages, each with its own output)
        quality (blur / glare / dark) → save frame (evidence) → ORB pan tracking (shelf coords, keyframe choice)
        → VISION: Gemini Flash, structured JSON per keyframe (spine boxes + verbatim text, items, A4 sheet, wall corners)
        → SCALE: A4 corners (OpenCV) → plane homography → cm / m      → MERGE: one entry per physical book
        → IDENTIFY: spine text ↔ Google Books / Open Library / bookshop listings (fuzzy, thresholds; never model-written)
        → PRICE: shop listings (Crossword, Kitabay, Book Outlet, SecondSale, IKEA) + ECB FX → ROOM: wall sizes → areas
        → PACKET: totals in code, review queue → claim_packet.json + report.html (+ customer-advised packet on request)
```

| Job | Model / tool | Why it cannot invent facts |
|---|---|---|
| Talk, direct the sweep, ask questions, take corrections | Gemini Live | Never asked for numbers; reads code-built summaries; cannot set the locale before the user speaks |
| See spines, items, A4 sheet, wall corners | Gemini Flash (picked per key by the doctor) | Boxes plus verbatim text only. "Copy text exactly, never complete a title" |
| Identify a book | No LLM: RapidFuzz vs catalogues and shop listings | The read must cover ≥ 50% of the title's words and score ≥ 85; else "unidentified" |
| Price | No LLM: retailer product data, with URL and date | Converted prices labelled; no listing means blank plus review. Grounded search only if the key has quota, and only cited lines |
| Totals | Python (`packet.compute_totals`) | Sums over the lines |

**Metric scale.** One reference object: a plain A4 sheet (29.7 × 21.0 cm) on each wall and at each shelf end. OpenCV finds its four corners; the homography maps pixels to centimetres on that plane, perspective included.
- **Spines:** measured directly when the sheet is in frame. Otherwise the scale is carried along the shelf by tracking. Fallbacks: catalogue heights of identified books, then a B-format prior (flagged).
- **Walls:** the four corners are mapped onto the wall's plane. Rectangle assumed; gross area.
- **Measured on test scenes:** spine height error 0.1–2%; floor area −3.5%, wall area −2.6%.

**Where prices come from.**
- **Books:** Shopify bookshop search, local first (IN: Crossword new, Kitabay used; US: Book Outlet, SecondSale), then the other country's shops converted at the dated ECB rate.
- **Items:** IKEA comparable products, 25th–75th percentile range, each end linked.
- **Not auto-priced:** art (unless the user says it's a print), signed copies, first editions, books published before 1950 and anything above ₹5,000. These go to appraisal.
- **Locale:** a setting. Ten books are re-priced in USD in every packet.

**Barge-in:** a phone-side gate (echo-aware, 0.32 s of speech) plus the model's own voice detection. **Details:** [ARCHITECTURE.md](ARCHITECTURE.md), [../DEVELOPER_GUIDE.md](../DEVELOPER_GUIDE.md).
