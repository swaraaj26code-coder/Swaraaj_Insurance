"""Stage 6: assemble claim_packet.json. Totals are computed here, in code, from
the lines. No language model writes any number in this file."""

import re
from statistics import median

from rapidfuzz import fuzz

from . import config, pricing
from .identify import norm


def _fact_hits(facts, kind, *texts) -> bool:
    for f in facts:
        if f["kind"] != kind or not f["target"]:
            continue
        if any(t and fuzz.partial_ratio(norm(f["target"]), norm(t)) >= 80 for t in texts):
            return True
    return False


def book_preview(b: dict, prices: dict) -> dict:
    ident = b.get("ident") or {}
    p = prices.get(f'{ident.get("title")}|{ident.get("author")}', {}) if ident.get("status") == "identified" else {}
    return {"shelf": b["shelf"], "position": b["position"], "status": ident.get("status", "reading"),
            "title": ident.get("title", ""), "author": ident.get("author", ""),
            "read": b["reading"]["title_text"], "h": b.get("spine_height_cm"), "t": b.get("spine_thickness_cm"),
            "price": (p.get("replacement_cost") or {}).get("amount"), "frame_ref": b["frame_ref"]}


def item_preview(i: dict) -> dict:
    return {k: i[k] for k in ("id", "category", "description", "brand_model", "frame_ref")}


def _empty_money(used=False):
    m = {"amount": None, "source": "", "url": "", "retrieved_at": ""}
    m.update({"condition_assumed": ""} if used else {"converted": False})
    return m


def _year(published: str) -> int | None:
    m = re.match(r"(\d{4})", published or "")
    return int(m.group(1)) if m else None


def compute_totals(books: list, items: list, currency: str, shelf_run_m: float, excluded_by_policyholder: int) -> dict:
    """Totals are sums over the lines, in code. Used by both the verified and the customer-advised packet."""
    def amt(m):
        return m["amount"] if m and m.get("amount") is not None else 0.0

    return {
        "book_count": len(books),
        "books_identified": sum(b["status"] == "identified" for b in books),
        "books_unidentified": sum(b["status"] == "unidentified" for b in books),
        "books_needs_appraisal": sum(b["status"] == "needs_appraisal" for b in books),
        "shelf_run_m": shelf_run_m,
        "books_replacement_cost": round(sum(amt(b["replacement_cost"]) for b in books), 2),
        "books_used_value": round(sum(amt(b["used_value"]) for b in books), 2),
        "items_replacement_cost_low": round(sum(i["replacement_cost"]["low"] or 0 for i in items), 2),
        "items_replacement_cost_high": round(sum(i["replacement_cost"]["high"] or 0 for i in items), 2),
        "excluded_from_totals": sum(b["replacement_cost"]["amount"] is None for b in books)
                                + sum(i["replacement_cost"]["low"] is None for i in items),
        "excluded_by_policyholder": excluded_by_policyholder,
        "currency": currency,
    }


CUSTOMER_SOURCE = "Stated by the policyholder (customer-advised, not independently verified)"


def find_line(packet: dict, target: str) -> tuple[str, dict] | None:
    """Resolve what the policyholder is talking about: a book id (A1-02), an item id
    (item-03 / #03), or a title/description. Returns ("book"|"item", line) or None."""
    t = (target or "").strip()
    tl = t.lower().replace("#", "item-") if t.startswith("#") else t.lower()
    for b in packet["books"]:
        if b["id"].lower() == tl:
            return "book", b
    for i in packet["items"]:
        if i["id"].lower() == tl:
            return "item", i
    best, best_score = None, 0
    for b in packet["books"]:
        for text in (b["title"], b["spine_text_read"]["title_text"]):
            score = fuzz.token_set_ratio(norm(t), norm(text)) if text else 0
            if score > best_score:
                best, best_score = ("book", b), score
    for i in packet["items"]:
        score = fuzz.token_set_ratio(norm(t), norm(i["description"]))
        if score > best_score:
            best, best_score = ("item", i), score
    return best if best_score >= 80 else None


def build_customer(original: dict, corrections: list[dict]) -> dict:
    """A separate, clearly labelled packet with the policyholder's own corrections applied.
    The verified packet is never modified. Every changed figure is marked customer-advised,
    keeps the system's value next to it, carries the policyholder's words, and goes to review."""
    import copy
    import datetime as dt
    p = copy.deepcopy(original)
    today = dt.date.today().isoformat()
    review = list(p["review_queue"])
    applied = []
    for n, c in enumerate(corrections, 1):
        comment = c.get("comment", "")
        if c.get("target", "").lower() == "new":
            line = {"id": f"C-{n:02d}", "shelf": "", "position": 0, "frame_ref": "", "status": "identified",
                    "title": c.get("title", ""), "author": c.get("author", ""), "edition": "", "isbn": "",
                    "spine_height_cm": None, "spine_thickness_cm": None, "id_confidence": 0.0,
                    "replacement_cost": {"amount": None, "source": "", "url": "", "retrieved_at": "", "converted": False},
                    "used_value": {"amount": None, "source": "", "url": "", "retrieved_at": "", "condition_assumed": ""},
                    "identified_by": "policyholder", "frame_t_s": None, "frame_box_2d": None, "orientation": "",
                    "sightings": 0, "scale_method": "", "catalogue": {"source": "", "url": ""},
                    "spine_text_read": {"title_text": "", "author_text": "", "publisher_text": "", "legibility": ""}}
            p["books"].append(line)
            kind = "book"
            review.append({"ref_id": line["id"], "reason": "book added by the policyholder; not seen in the sweep (no frame)"})
        else:
            found = find_line(p, c.get("target", ""))
            if not found:
                applied.append({**c, "applied": False, "reason": "could not find that book or item in the packet"})
                continue
            kind, line = found
        changed = []
        line.setdefault("system_values", {})
        if kind == "book":
            for field in ("title", "author"):
                if c.get(field):
                    line["system_values"].setdefault(field, line[field])
                    line[field] = c[field]
                    changed.append(field)
            if c.get("title") and line["status"] == "unidentified":
                line["system_values"].setdefault("status", "unidentified")
                line["status"] = "identified"
                line["identified_by"] = "policyholder"
            for field, money_field in (("replacement_cost", "replacement_cost"), ("used_value", "used_value")):
                if c.get(field) is not None:
                    line["system_values"].setdefault(money_field, dict(line[money_field]))
                    line[money_field] = {"amount": float(c[field]), "source": CUSTOMER_SOURCE, "url": "",
                                         "retrieved_at": today, "converted": False,
                                         **({"condition_assumed": "as stated by the policyholder"} if field == "used_value" else {})}
                    changed.append(field)
            if line["status"] == "needs_appraisal" and (c.get("replacement_cost") is not None or c.get("used_value") is not None):
                review.append({"ref_id": line["id"], "reason": "policyholder stated a value for a line flagged for human appraisal"})
        else:
            if c.get("replacement_cost") is not None:
                line["system_values"].setdefault("replacement_cost", dict(line["replacement_cost"]))
                v = float(c["replacement_cost"])
                line["replacement_cost"] = {"low": v, "high": v, "source": CUSTOMER_SOURCE, "url": "",
                                            "high_source": CUSTOMER_SOURCE, "high_url": "", "retrieved_at": today, "converted": False}
                changed.append("replacement_cost")
            if c.get("title"):
                line["system_values"].setdefault("description", line["description"])
                line["description"] = c["title"]
                changed.append("description")
        line["customer_advised"] = True
        line["user_comment"] = comment
        review.append({"ref_id": line["id"], "reason": f'customer-advised ({", ".join(changed) or "comment only"}): '
                                                       f'"{comment}" (stated by the policyholder, not independently verified)'})
        applied.append({**c, "applied": True, "ref_id": line["id"], "changed": changed})

    p["review_queue"] = review
    p["totals"] = compute_totals(p["books"], p["items"], p["totals"]["currency"], p["totals"]["shelf_run_m"],
                                 p["totals"].get("excluded_by_policyholder", 0))
    p["packet_type"] = "customer_advised"
    p["title"] = "Customer-advised claim packet"
    p["based_on"] = {"packet": "claim_packet.json", "sweep_id": original["sweep"]["id"],
                     "verified_totals": original["totals"]}
    p["customer_corrections"] = applied
    p["method"] = {**p["method"], "customer_advised": "policyholder corrections applied on top of the verified packet; "
                   "changed lines are marked customer_advised with the system's values kept in system_values"}
    return p


def build(sweep, books, warnings, item_prices, sample, second, room_result) -> dict:
    review, excluded = [], []
    facts = sweep.facts
    excluded_shelves = {f["target"] for f in facts if f["kind"] == "exclude_shelf"}

    out_books = []
    for b in books:
        ident = b["ident"] or {}
        bid = f'{b["shelf"]}-{b["position"]:02d}'
        if b["shelf"] in excluded_shelves or _fact_hits(facts, "not_mine_book", b["reading"]["title_text"], ident.get("title")):
            excluded.append({"ref_id": bid, "reason": "policyholder said these are not theirs / skip this shelf"})
            continue
        status = ident.get("status", "unidentified")
        price = sweep.prices.get(f'{ident.get("title")}|{ident.get("author")}', {}) if status == "identified" else {}
        repl = {**_empty_money(), **(price.get("replacement_cost") or {})}
        used = {**_empty_money(True), **(price.get("used_value") or {})}
        appraisal = []
        if _fact_hits(facts, "first_edition", b["reading"]["title_text"], ident.get("title")):
            appraisal.append("policyholder states first edition")
        if _fact_hits(facts, "signed", b["reading"]["title_text"], ident.get("title")):
            appraisal.append("policyholder states signed copy")
        year = _year(ident.get("published", ""))
        if status == "identified" and year and year < 1950:
            appraisal.append(f"possibly antiquarian (first published {year})")
        if repl["amount"] and repl["amount"] > config.APPRAISAL_THRESHOLD:
            appraisal.append(f'replacement price {repl["amount"]:.0f} {sweep.currency} above appraisal threshold {config.APPRAISAL_THRESHOLD:.0f}')
        if appraisal:
            status = "needs_appraisal"
            # Never auto-price these: keep the evidence in review, blank the figures.
            repl, used = _empty_money(), _empty_money(True)
            review.append({"ref_id": bid, "reason": "human appraisal: " + "; ".join(appraisal)})
        if ident.get("review"):
            review.append({"ref_id": bid, "reason": ident["review"]})
        if status == "identified":
            for r in price.get("review", []):
                review.append({"ref_id": bid, "reason": "price: " + r})
        if b["spine_height_cm"] is None:
            review.append({"ref_id": bid, "reason": "no metric scale for this spine"})
        elif "prior" in b["scale_method"]:
            review.append({"ref_id": bid, "reason": "dimensions from standard-format prior, not measured scale"})
        out_books.append({
            "id": bid, "shelf": b["shelf"], "position": b["position"], "frame_ref": b["frame_ref"],
            "frame_t_s": b["t"], "frame_box_2d": b["frame_box_2d"], "status": status,
            "title": ident.get("title", "") if status != "unidentified" else "",
            "author": ident.get("author", "") if status != "unidentified" else "",
            "edition": ident.get("edition", ""), "isbn": ident.get("isbn", ""),
            "spine_text_read": b["reading"], "orientation": b["orientation"], "sightings": b["sightings"],
            "spine_height_cm": b["spine_height_cm"], "spine_thickness_cm": b["spine_thickness_cm"],
            "scale_method": b["scale_method"], "id_confidence": ident.get("id_confidence", 0.0),
            "catalogue": {"source": ident.get("catalogue_source", ""), "url": ident.get("catalogue_url", "")},
            "replacement_cost": repl, "used_value": used,
        })

    out_items = []
    for i, it in enumerate(sweep.items):
        print_ = _fact_hits(facts, "art_print", it["id"], it["description"])
        if _fact_hits(facts, "not_mine_item", it["id"], it["description"]):
            excluded.append({"ref_id": it["id"], "reason": "policyholder said this item is not theirs"})
            continue
        p = item_prices.get(i, {})
        low, high = p.get("low"), p.get("high")
        if it["category"] == "art" and not print_:
            status = "needs_appraisal"
            low = high = None
            review.append({"ref_id": it["id"], "reason": "art defaults to appraisal (policyholder did not state it is a print)"
                           + ("; policyholder states original" if _fact_hits(facts, "art_original", it["id"], it["description"]) else "")})
        elif low and high:
            # "priced" only for an exact brand/model price; comparables are a sourced range.
            status = "priced" if p.get("exact") else "range"
        else:
            status = "range"
            low = high = None
            review.append({"ref_id": it["id"], "reason": "no sourced price found; excluded from totals"})
        for r in p.get("review", []) if status != "needs_appraisal" else []:
            review.append({"ref_id": it["id"], "reason": "price: " + r})
        if low and high and low["amount"] > high["amount"]:
            low, high = high, low
        out_items.append({
            "id": it["id"], "category": it["category"], "description": it["description"], "material": it["material"],
            "brand_model": it["brand_model"], "frame_ref": it["frame_ref"], "frame_t_s": it["t"],
            "frame_box_2d": it["frame_box_2d"], "sightings": len(it["sightings"]),
            "dimensions_cm": it["dimensions_cm"] or {"w": None, "h": None, "d": None},
            "dimensions_method": "approximate, scaled at the A4 sheet's depth" if it["dimensions_cm"] else "no reference in frame",
            "status": status,
            "replacement_cost": {"low": low["amount"] if low else None, "high": high["amount"] if high else None,
                                 "source": low["source"] if low else "", "url": low["url"] if low else "",
                                 "high_source": high["source"] if high else "", "high_url": high["url"] if high else "",
                                 "retrieved_at": low["retrieved_at"] if low else "",
                                 "converted": bool((low or {}).get("converted") or (high or {}).get("converted"))},
            "confidence": 0.8 if it["brand_model"] else 0.6,
        })

    for w in warnings:
        review.append({"ref_id": "inventory", "reason": w})
    kinds = {s["kind"] for s in getattr(sweep, "segments", [])}
    no_walls = any(f["kind"] == "no_walls" for f in facts)
    front_only = "front" in kinds and "wall" not in kinds
    if room_result["floor_area_m2"] is None:
        if no_walls:
            room_result["notes"] = ["not measured: the policyholder said there were no walls to capture"] + room_result["notes"]
        elif front_only:
            room_result["notes"] = ["not measured: front-view sweep (books in front of the camera), no walls were captured"] + room_result["notes"]
        review.append({"ref_id": "room", "reason": "; ".join(room_result["notes"]) or "room not measured"})
    elif room_result["confidence"] < 0.5:
        review.append({"ref_id": "room", "reason": "low-confidence room measurement: " + "; ".join(room_result["notes"])})

    # Shelf run: occupied length per shelf, using each shelf's spine scale.
    run_cm = 0.0
    for key in {(b["shelf"], b["chain"]) for b in books if b["shelf"] not in excluded_shelves}:
        group = [b for b in books if (b["shelf"], b["chain"]) == key]
        ks = [b["spine_height_cm"] / (b["shelf_box"][3] - b["shelf_box"][1]) for b in group
              if b["spine_height_cm"] and b["orientation"] == "upright" and b["shelf_box"][3] > b["shelf_box"][1]]
        if ks:
            run_cm += (max(b["shelf_box"][2] for b in group) - min(b["shelf_box"][0] for b in group)) * median(ks)

    totals = compute_totals(out_books, out_items, sweep.currency, round(run_cm / 100, 2), len(excluded))

    comparison = []
    for k, s in enumerate(sample):
        local = sweep.prices.get(f'{s["title"]}|{s["author"]}', {})
        other = second.get(k, {})
        comparison.append({"title": s["title"], "author": s["author"],
                           sweep.country: {"replacement_cost": local.get("replacement_cost") or None, "used_value": local.get("used_value") or None},
                           config.SECOND_COUNTRY: {"replacement_cost": other.get("replacement_cost") or None, "used_value": other.get("used_value") or None}})

    duration = round((sweep.ended_at or 0) - sweep.t0, 1) if sweep.ended_at else sweep.now()
    p_stats = {k: pricing.stats[k] - sweep.pricing_stats0.get(k, 0) for k in pricing.stats}
    cost = None
    if config.COST_IN_PER_M or config.COST_OUT_PER_M:
        tokens_in = sweep.vision_usage["in_tokens"] + p_stats["in_tokens"]
        tokens_out = sweep.vision_usage["out_tokens"] + p_stats["out_tokens"]
        cost = round(tokens_in / 1e6 * config.COST_IN_PER_M + tokens_out / 1e6 * config.COST_OUT_PER_M
                     + p_stats["calls"] * config.COST_PER_GROUNDED_CALL + duration / 60 * config.COST_LIVE_PER_MIN, 4)

    return {
        "sweep": {"id": sweep.id, "captured_at": sweep.captured_at, "device": sweep.device, "duration_s": duration,
                  "country": sweep.country, "currency": sweep.currency,
                  "frames_saved": len(sweep.frames), "keyframes_analysed": sweep.vision_usage["calls"],
                  "capture_modes": sorted({s["kind"] for s in getattr(sweep, "segments", [])})},
        "room": room_result,
        "books": out_books,
        "items": out_items,
        "totals": totals,
        "review_queue": review,
        "excluded": excluded,
        "policyholder_statements": facts,
        "locale_comparison": {"note": f"First 10 identified books re-priced for {config.SECOND_COUNTRY} ({config.SECOND_CURRENCY}) by changing only the locale setting.",
                              "books": comparison},
        "metrics": {
            "latency_s": {k: {"calls": len(v), "total": round(sum(v), 2), "mean": round(sum(v) / len(v), 2)} for k, v in sweep.timings.items() if v},
            "vision": sweep.vision_usage, "pricing": {k: round(v, 2) for k, v in p_stats.items()},
            "barge_ins": getattr(sweep, "barge_ins", {}),
            "estimated_cost_usd": cost,
            "cost_note": "computed from token counts and the list prices set in .env" if cost is not None
                         else "free-tier key: no charge; set COST_* in .env to estimate paid cost from the token counts above",
        },
        "method": {
            "scale": "plain A4 sheet (29.7 x 21.0 cm) detected with OpenCV; plane homography for spines and walls",
            "identification": "verbatim spine text (vision model) fuzzy-matched to Google Books / Open Library; no model-written titles",
            "pricing": "retailer listings (Shopify bookshops, IKEA) with URL and date; Gemini + Google Search grounding only if the key has quota, kept only when cited",
            "totals": "summed in code from the lines above",
        },
        "packet_type": "verified",
        "title": "Claim packet",
    }
