"""Stage 4: local market prices with a retrievable source for every figure.

Order of evidence for each figure:
  1. a retailer listing in the policyholder's country (shops.py: bookshops for
     books, IKEA comparables for other items) - no language model involved;
  2. a listing in another country, converted at the dated ECB rate and
     labelled converted=true;
  3. only if the API key has Google Search quota: Gemini with search
     grounding, where a price line is accepted only if the grounding metadata
     cites that exact line (an uncited line is treated as fabricated).
Anything still missing stays blank and goes to review. Totals are never
computed here; see packet.py."""

import asyncio
import datetime as dt
import logging
import re
import time

import httpx
from google.genai import types

from . import cache, config, shops
from .ratelimit import RateLimiter, map_threads
from .vision import client

log = logging.getLogger(__name__)
_limiter = RateLimiter(config.PRICING_RPM)
COUNTRY_NAMES = {"IN": "India", "US": "the United States", "GB": "the United Kingdom", "DE": "Germany", "AU": "Australia"}
LINE = re.compile(r"^\s*(BOOK|ITEM)\s+(\d+)\s*\|\s*(NEW|USED|LOW|HIGH)\s*\|\s*([\d.,]+|NONE)\s*\|\s*([A-Z]{3})?\s*\|?\s*([^|]*)\|?\s*(.*)$", re.I)
stats = {"calls": 0, "in_tokens": 0, "out_tokens": 0, "latency_s": 0.0}


def _resolve(uri: str) -> str:
    """Grounding chunks carry redirect links; follow them to the real page."""
    hit = cache.get("urls", uri)
    if hit:
        return hit
    try:
        r = httpx.get(uri, follow_redirects=True, timeout=10, headers={"User-Agent": "Mozilla/5.0"})
        final = str(r.url)
    except httpx.HTTPError:
        final = uri
    return cache.put("urls", uri, final)


def fx_rate(src: str, dst: str) -> tuple[float, str, str] | None:
    """(rate, date, source url) from the ECB reference rates via Frankfurter."""
    if src == dst:
        return 1.0, dt.date.today().isoformat(), ""
    key = f"{src}->{dst}|{dt.date.today()}"
    hit = cache.get("fx", key)
    if hit:
        return tuple(hit)
    for base in ("https://api.frankfurter.dev/v1/latest", "https://api.frankfurter.app/latest"):
        try:
            r = httpx.get(base, params={"base": src, "symbols": dst}, timeout=10)
            r.raise_for_status()
            j = r.json()
            return tuple(cache.put("fx", key, [j["rates"][dst], j["date"], str(r.url)]))
        except (httpx.HTTPError, KeyError):
            continue
    return None


async def _grounded(prompt: str) -> tuple[str, list[dict]]:
    """Run a search-grounded call. Returns text and [{start, end, urls, titles}] supports."""
    await _limiter.wait()
    started = time.monotonic()
    resp = await client().aio.models.generate_content(
        model=config.PRICING_MODEL, contents=prompt,
        config=types.GenerateContentConfig(tools=[types.Tool(google_search=types.GoogleSearch())], temperature=0))
    stats["calls"] += 1
    stats["latency_s"] += time.monotonic() - started
    u = resp.usage_metadata
    stats["in_tokens"] += getattr(u, "prompt_token_count", 0) or 0
    stats["out_tokens"] += (getattr(u, "candidates_token_count", 0) or 0) + (getattr(u, "thoughts_token_count", 0) or 0)
    text = resp.text or ""
    gm = resp.candidates[0].grounding_metadata if resp.candidates else None
    chunks = (gm.grounding_chunks or []) if gm else []
    supports = []
    for s in (gm.grounding_supports or []) if gm else []:
        webs = [chunks[i].web for i in (s.grounding_chunk_indices or []) if i < len(chunks) and chunks[i].web]
        if webs and s.segment:
            supports.append({"text": s.segment.text or "", "urls": [w.uri for w in webs],
                             "titles": [w.title or w.domain or "" for w in webs]})
    return text, supports


def _cited(line: str, supports: list[dict]) -> dict | None:
    """The support whose segment covers this output line, if any."""
    core = line.strip()
    for s in supports:
        seg = s["text"].strip()
        if seg and (seg in core or core in seg or (len(seg) > 12 and seg[:40] in core)):
            return s
    return None


def _parse(text: str, supports: list[dict]) -> list[dict]:
    rows = []
    for line in text.splitlines():
        m = LINE.match(line)
        if not m:
            continue
        kind, n, field, amount, cur, seller, condition = m.groups()
        if amount.upper() == "NONE":
            rows.append({"n": int(n), "field": field.upper(), "amount": None, "reason": "no price found"})
            continue
        cite = _cited(line, supports)
        if not cite:
            rows.append({"n": int(n), "field": field.upper(), "amount": None,
                         "reason": "price line had no grounding citation (treated as fabricated, dropped)"})
            continue
        rows.append({"n": int(n), "field": field.upper(), "amount": float(amount.replace(",", "")),
                     "currency": (cur or "").upper(), "seller": seller.strip(), "condition": condition.strip(),
                     "url": _resolve(cite["urls"][0]), "source": cite["titles"][0]})
    return rows


def _money(row: dict, currency: str, today: str) -> dict:
    """Convert to the policy currency if needed, labelling the conversion."""
    out = {"amount": row["amount"], "source": f'{row["seller"] or row["source"]} (via Google Search)',
           "url": row["url"], "retrieved_at": today, "converted": False}
    if row["currency"] and row["currency"] != currency:
        fx = fx_rate(row["currency"], currency)
        if not fx:
            return {"amount": None, "reason": f'found only a {row["currency"]} price and no FX rate'}
        out.update(amount=round(row["amount"] * fx[0], 2), converted=True,
                   source=out["source"] + f' converted from {row["currency"]} {row["amount"]} at ECB {fx[1]} rate {fx[0]}',
                   fx_url=fx[2])
    return out


async def _grounded_books(books: list[dict], country: str, currency: str) -> dict[int, dict]:
    """books: [{title, author, isbn}]. Returns {index: {replacement_cost, used_value, review}}."""
    today = dt.date.today().isoformat()
    where = COUNTRY_NAMES.get(country, country)
    results: dict[int, dict] = {}
    todo = []
    for i, b in enumerate(books):
        hit = cache.get("prices", f'{country}|{b["title"]}|{b["author"]}')
        if hit:
            results[i] = hit
        else:
            todo.append(i)
    for start in range(0, len(todo), config.PRICING_BATCH):
        batch = todo[start:start + config.PRICING_BATCH]
        listing = "\n".join(f'BOOK {k + 1}: "{books[i]["title"]}" by {books[i]["author"] or "unknown"}'
                            + (f' (ISBN {books[i]["isbn"]})' if books[i]["isbn"] else "") for k, i in enumerate(batch))
        prompt = f"""Search the web for the current retail prices of these books for a buyer in {where}.
{listing}

For each book give exactly two lines in this format and nothing else:
BOOK <n> | NEW | <price> | <currency code> | <retailer> | new
BOOK <n> | USED | <price> | <currency code> | <seller or marketplace> | <condition as listed>
Prefer sellers that ship within {where}, priced in {currency}. Use only prices you actually found on a page in
this search (paperback unless the book only exists in hardcover). If you did not find a price, write NONE as the
price. Never estimate or recall a price from memory."""
        try:
            text, supports = await _grounded(prompt)
        except Exception as exc:  # network / quota: leave blank, never invent
            log.warning("Pricing call failed: %s", exc)
            text, supports = "", []
        rows = _parse(text, supports)
        for k, i in enumerate(batch):
            entry = {"replacement_cost": {}, "used_value": {}, "review": []}
            for field, target in (("NEW", "replacement_cost"), ("USED", "used_value")):
                row = next((r for r in rows if r["n"] == k + 1 and r["field"] == field), None)
                if row is None:
                    entry["review"].append(f"{target}: no answer returned")
                elif row["amount"] is None:
                    entry["review"].append(f'{target}: {row["reason"]}')
                else:
                    money = _money(row, currency, today)
                    if money.get("amount") is None:
                        entry["review"].append(f'{target}: {money["reason"]}')
                    else:
                        if target == "used_value":
                            money["condition_assumed"] = row["condition"] or "used, condition not stated (assumed good)"
                        entry[target] = money
            results[i] = cache.put("prices", f'{country}|{books[i]["title"]}|{books[i]["author"]}', entry)
    return results


async def _grounded_items(items: list[dict], country: str, currency: str) -> dict[int, dict]:
    """items: [{category, description, brand_model}] -> {index: {low, high, source, url, retrieved_at, review}}."""
    today = dt.date.today().isoformat()
    where = COUNTRY_NAMES.get(country, country)
    results: dict[int, dict] = {}
    todo = []
    for i, it in enumerate(items):
        hit = cache.get("item_prices", f'{country}|{it["category"]}|{it["description"]}|{it["brand_model"]}')
        if hit:
            results[i] = hit
        else:
            todo.append(i)
    for start in range(0, len(todo), config.PRICING_BATCH):
        batch = todo[start:start + config.PRICING_BATCH]
        listing = "\n".join(f'ITEM {k + 1}: {items[i]["brand_model"] or "unbranded"} {items[i]["description"]} ({items[i]["category"]})'
                            for k, i in enumerate(batch))
        prompt = f"""Search the web for what it costs to buy each of these household items new today in {where}.
{listing}

For each item give exactly two lines in this format and nothing else:
ITEM <n> | LOW | <price> | <currency code> | <retailer> | <product name>
ITEM <n> | HIGH | <price> | <currency code> | <retailer> | <product name>
If a brand/model is given, LOW and HIGH are the cheapest and dearest listings of that model. If unbranded, use a
comparable product range. Use only prices found on a page in this search, in {currency} where possible. If none
found, write NONE. Never estimate or recall a price from memory."""
        try:
            text, supports = await _grounded(prompt)
        except Exception as exc:
            log.warning("Item pricing call failed: %s", exc)
            text, supports = "", []
        rows = _parse(text, supports)
        for k, i in enumerate(batch):
            entry = {"review": []}
            for field in ("LOW", "HIGH"):
                row = next((r for r in rows if r["n"] == k + 1 and r["field"] == field), None)
                if not row or row["amount"] is None:
                    entry["review"].append(f'{field.lower()}: {row["reason"] if row else "no answer returned"}')
                    continue
                money = _money(row, currency, today)
                if money.get("amount") is None:
                    entry["review"].append(f'{field.lower()}: {money["reason"]}')
                    continue
                entry[field.lower()] = money
            results[i] = cache.put("item_prices", f'{country}|{items[i]["category"]}|{items[i]["description"]}|{items[i]["brand_model"]}', entry)
    return results


def _convert(listing: dict, currency: str) -> tuple[dict | None, str]:
    """A shop listing as a packet money field, converted (and labelled) if needed."""
    out = {"amount": listing["amount"], "source": listing["source"], "url": listing["url"],
           "retrieved_at": listing["retrieved_at"], "converted": False}
    if listing["currency"] != currency:
        fx = fx_rate(listing["currency"], currency)
        if not fx:
            return None, f'found only a {listing["currency"]} price and no exchange rate'
        out.update(amount=round(listing["amount"] * fx[0], 2), converted=True, fx_url=fx[2],
                   source=out["source"] + f' converted from {listing["currency"]} {listing["amount"]} at the ECB {fx[1]} rate {fx[0]}')
    return out, ""


def _book_from_shops(book: dict, country: str, currency: str) -> dict:
    entry = {"replacement_cost": {}, "used_value": {}, "review": []}
    local = shops.BOOK_SHOPS.get(country, [])
    foreign = [s for c, ss in shops.BOOK_SHOPS.items() if c != country for s in ss]
    for condition, target in (("new", "replacement_cost"), ("used", "used_value")):
        tried = [s for s in local if s.condition == condition] + [s for s in foreign if s.condition == condition]
        listing = None
        for shop in tried:
            listing = shops.book_listing(shop, book["title"], book["author"], book["isbn"])
            if listing:
                break
        if not listing:
            entry["review"].append(f'{target}: no {condition} listing found ({", ".join(s.name for s in tried)})')
            continue
        money, why = _convert(listing, currency)
        if not money:
            entry["review"].append(f"{target}: {why}")
            continue
        if target == "used_value":
            money["condition_assumed"] = f'used, as sold by {listing["shop"]} (condition not stated in the listing; assumed good)'
        entry[target] = money
    return entry


async def price_books(books: list[dict], country: str, currency: str) -> dict[int, dict]:
    """books: [{title, author, isbn}] -> {index: {replacement_cost, used_value, review}}."""
    started = time.monotonic()
    found = await map_threads(_book_from_shops, [(b, country, currency) for b in books], config.LOOKUP_CONCURRENCY)
    results = dict(enumerate(found))
    stats["shop_lookups_s"] = stats.get("shop_lookups_s", 0.0) + time.monotonic() - started
    missing = [i for i, e in results.items() if not e["replacement_cost"] or not e["used_value"]]
    if missing and config.PRICING_SEARCH:
        grounded = await _grounded_books([books[i] for i in missing], country, currency)
        for k, i in enumerate(missing):
            g = grounded.get(k, {})
            for target in ("replacement_cost", "used_value"):
                if not results[i][target] and g.get(target):
                    results[i][target] = g[target]
                    results[i]["review"] = [r for r in results[i]["review"] if not r.startswith(target)]
    return results


def _item_from_ikea(it: dict, country: str, currency: str) -> dict:
    entry = {"review": [], "exact": False}
    query = it.get("search_term") or it["description"]
    rng = shops.ikea_range(query, country)
    if rng:
        low, why_low = _convert(rng["low"], currency)
        high, why_high = _convert(rng["high"], currency)
        if low and high:
            entry.update(low=low, high=high, comparables=rng["comparables"])
        else:
            entry["review"].append(why_low or why_high)
    else:
        entry["review"].append(f'no comparable product for "{query}" at IKEA {country}')
    return entry


async def price_items(items: list[dict], country: str, currency: str) -> dict[int, dict]:
    """items: [{category, description, brand_model, search_term}] -> {index: {low, high, exact, review}}.
    IKEA comparables give a sourced range; an exact brand/model price needs web search."""
    found = await map_threads(_item_from_ikea, [(it, country, currency) for it in items], config.LOOKUP_CONCURRENCY)
    results = dict(enumerate(found))
    exact_wanted = [i for i, it in enumerate(items) if it.get("brand_model")]
    if exact_wanted and config.PRICING_SEARCH:
        grounded = await _grounded_items([items[i] for i in exact_wanted], country, currency)
        for k, i in enumerate(exact_wanted):
            g = grounded.get(k, {})
            if g.get("low") and g.get("high"):
                results[i].update(low=g["low"], high=g["high"], exact=True, review=[])
    return results
