"""Retailer listings as price evidence: real products, real prices, real URLs.

No language model is involved. Books come from bookshops running Shopify,
whose public storefront search (/search/suggest.json) returns each product's
title, price, availability and link. Non-book items come from IKEA's public
product search, which gives a sourced range of comparable products.

Shops are listed per country, so locale is a setting: adding a country means
adding its shops here, not changing code."""

import datetime as dt
import re
from dataclasses import dataclass
from html import unescape

import httpx
from rapidfuzz import fuzz

from . import cache

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"}


@dataclass(frozen=True)
class Shop:
    name: str
    base: str
    currency: str
    condition: str   # "new" or "used" (as sold by this shop)


BOOK_SHOPS = {
    "IN": [Shop("Crossword", "https://www.crossword.in", "INR", "new"),
           Shop("Kitabay", "https://kitabay.com", "INR", "used")],
    "US": [Shop("Book Outlet", "https://bookoutlet.com", "USD", "new"),
           Shop("SecondSale", "https://www.secondsale.com", "USD", "used")],
}
# IKEA market code, language and currency per country.
IKEA = {"IN": ("in", "en", "INR"), "US": ("us", "en", "USD"), "GB": ("gb", "en", "GBP"), "DE": ("de", "de", "EUR")}

# Listings that are a different product from the book on the shelf.
VARIANTS = ("audio", "audiobook", "hindi", "kannada", "tamil", "telugu", "marathi", "bengali", "gujarati", "malayalam",
            "punjabi", "urdu", "odia", "oriya", "assamese", "sanskrit", "nepali", "spanish", "french", "german", "italian",
            "portuguese", "chinese", "japanese", "arabic", "russian", "edition in", "translated",
            "box set", "boxset", "combo", "set of", "bundle", "mystery box", "workbook", "summary of", "journal", "planner")


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9 ]", " ", (s or "").lower()).strip()


def _today() -> str:
    return dt.date.today().isoformat()


def shopify_search(shop: Shop, query: str) -> dict:
    """Cached storefront search. Returns {"retrieved_at", "products": [...]}."""
    key = f"{shop.base}|{query.lower()}"
    hit = cache.get("shops", key)
    if hit is not None:
        return hit
    r = httpx.get(f"{shop.base}/search/suggest.json", headers=UA, timeout=15, follow_redirects=True,
                  params={"q": query, "resources[type]": "product", "resources[limit]": 10})
    r.raise_for_status()
    products = r.json().get("resources", {}).get("results", {}).get("products", [])
    keep = [{"title": p.get("title", ""), "handle": p.get("handle", ""), "price": float(p.get("price") or 0),
             "available": bool(p.get("available")), "url": shop.base + (p.get("url") or "").split("?")[0],
             "text": _norm(unescape(re.sub(r"<[^>]+>", " ", p.get("body") or ""))[:600] + " " + " ".join(p.get("tags") or []))}
            for p in products]
    return cache.put("shops", key, {"retrieved_at": _today(), "products": keep})


def _title_score(read: str, candidate: str) -> float:
    a, b = _norm(read), _norm(candidate)
    if not a or not b:
        return 0.0
    words = b.split()
    got = set(a.split())
    coverage = sum(any(fuzz.ratio(w, g) >= 80 for g in got) for w in words) / len(words)
    if coverage < 0.5:
        return 0.0
    return (fuzz.token_sort_ratio(a, b) + fuzz.token_set_ratio(a, b)) / 2


def match_book(products: list[dict], title: str, author: str = "", isbn: str = "") -> list[dict]:
    """Listings of this book that are in stock, best first.
    A listing must match the title, must not be a different product (audio,
    translation, box set...), and must mention the author's surname if known."""
    surname = _norm(author.split(",")[0]).split()[-1:] if author else []
    wanted_variants = [v for v in VARIANTS if v in _norm(title)]
    out = []
    for p in products:
        if not p["available"] or p["price"] <= 0:
            continue
        haystack = _norm(p["title"] + " " + p["handle"].replace("-", " ")) + " " + p["text"]
        if any(v in haystack[:200] for v in VARIANTS if v not in wanted_variants):
            continue
        score = _title_score(title, p["title"])
        if score < 85:
            continue
        if surname and surname[0] not in haystack:
            continue
        isbn_match = bool(isbn) and isbn in p["handle"].replace("-", "")
        out.append({**p, "score": score, "isbn_match": isbn_match})
    # ISBN match first, then the closest title (5-point bands), then the cheapest copy.
    return sorted(out, key=lambda p: (not p["isbn_match"], -round(p["score"] / 5), p["price"]))


def book_listing(shop: Shop, title: str, author: str, isbn: str) -> dict | None:
    """Best listing at one shop: the ISBN match if present, else the cheapest matching copy."""
    query = f"{title} {author.split(',')[0]}".strip() if author else title
    try:
        found = shopify_search(shop, query)
        if not found["products"] and author:
            found = shopify_search(shop, title)
    except (httpx.HTTPError, ValueError):
        return None
    matches = match_book(found["products"], title, author, isbn)
    if not matches:
        return None
    best = matches[0]
    return {"amount": best["price"], "currency": shop.currency, "url": best["url"], "retrieved_at": found["retrieved_at"],
            "source": f'{shop.name} listing "{best["title"]}"' + (" (ISBN match)" if best["isbn_match"] else ""),
            "shop": shop.name, "condition": shop.condition}


def ikea_range(query: str, country: str) -> dict | None:
    """Sourced price range of comparable products at IKEA in this country."""
    market = IKEA.get(country)
    if not market or not query.strip():
        return None
    code, lang, currency = market
    key = f"ikea|{code}|{query.lower()}"
    found = cache.get("shops", key)
    if found is None:
        try:
            r = httpx.get(f"https://sik.search.blue.cdtapps.com/{code}/{lang}/search-result-page", headers=UA, timeout=15,
                          params={"q": query, "size": 12, "types": "PRODUCT"})
            r.raise_for_status()
            items = r.json()["searchResultPage"]["products"]["main"]["items"]
        except (httpx.HTTPError, KeyError, ValueError):
            return None
        found = cache.put("shops", key, {"retrieved_at": _today(), "products": [
            {"name": i["product"].get("name", ""), "type": i["product"].get("typeName", ""),
             "price": float(i["product"].get("salesPrice", {}).get("numeral") or 0), "url": i["product"].get("pipUrl", "")}
            for i in items if i.get("product")]})
    # Keep products whose type actually matches what was asked for ("floor lamp" -> "Floor/reading lamp").
    q = _norm(query)
    # IKEA types start with the noun ("Rug, low pile", "Floor/reading lamp"); token_sort (not token_set)
    # avoids matching any type that merely contains the word somewhere.
    relevant = [p for p in found["products"] if p["price"] > 0 and
                (_norm(p["type"]).startswith(q) or fuzz.token_sort_ratio(q, _norm(p["type"])) >= 60)]
    if not relevant:
        return None
    # Interquartile products, so one luxury set does not define the range; each end is still a real, linked product.
    ranked = sorted(relevant, key=lambda p: p["price"])
    low, high = (ranked[len(ranked) // 4], ranked[(3 * len(ranked)) // 4]) if len(ranked) >= 4 else (ranked[0], ranked[-1])
    def money(p):
        return {"amount": p["price"], "currency": currency, "url": p["url"], "retrieved_at": found["retrieved_at"],
                "source": f'IKEA {code.upper()} comparable product "{p["name"]} {p["type"]}"'}
    return {"low": money(low), "high": money(high), "comparables": len(relevant)}


def catalogue_candidates(title_text: str, author_text: str, country: str) -> list[dict]:
    """Last-resort identification evidence: bookshop listings whose title matches
    the spine. Used only when no catalogue is reachable. No edition is claimed."""
    out = []
    for shop in BOOK_SHOPS.get(country, []) + [s for c, shops in BOOK_SHOPS.items() if c != country for s in shops]:
        try:
            found = shopify_search(shop, f"{title_text} {author_text}".strip())
        except (httpx.HTTPError, ValueError):
            continue
        read = _norm(f"{title_text} {author_text}")
        for p in found["products"]:
            haystack = _norm(p["title"] + " " + p["handle"].replace("-", " ")) + " " + p["text"]
            # A translation / audio / box-set listing is a different edition from the spine read
            # ("Ikigai - Tamil" for a spine reading "IKIGAI", FAILURES #14).
            if any(v in _norm(p["title"] + " " + p["handle"].replace("-", " ")) for v in VARIANTS if v not in read):
                continue
            authors = [author_text] if author_text and _norm(author_text).split()[-1] in haystack else []
            out.append({"title": p["title"], "subtitle": "", "authors": authors, "publisher": "", "published": "",
                        "isbn": "", "url": p["url"], "source": f"{shop.name} listing", "height_cm": None, "thickness_cm": None})
        if out:
            break
    return out
