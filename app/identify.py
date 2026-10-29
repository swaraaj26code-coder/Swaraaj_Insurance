"""Stage 3: resolve literal spine text to a real work via public catalogues.

No language model is involved. A spine is "identified" only when the text read
from it fuzzy-matches a catalogue record above ID_ACCEPT; between ID_REVIEW and
ID_ACCEPT it is identified but sent to review; below that it stays unidentified
and keeps no title."""

import re

import httpx
from rapidfuzz import fuzz

from . import cache, config, shops
from .ratelimit import SyncRateLimiter

GOOGLE_BOOKS = "https://www.googleapis.com/books/v1/volumes"
OPEN_LIBRARY = "https://openlibrary.org/search.json"


def norm(s: str) -> str:
    return re.sub(r"[^a-z0-9 ]", " ", (s or "").lower()).strip()


def _title_score(read: str, candidate: str) -> float:
    a, b = norm(read), norm(candidate)
    if not a or not b:
        return 0.0
    # token_set alone scores "War" vs "War and Peace" as 100; averaging with
    # token_sort penalises spines that only cover a fragment of the title.
    return (fuzz.token_sort_ratio(a, b) + fuzz.token_set_ratio(a, b)) / 2


def _coverage(read: str, title: str) -> float:
    """Share of the catalogue title's words that were actually read on the spine."""
    words = norm(title).split()
    got = set(norm(read).split())
    return sum(any(fuzz.ratio(w, g) >= 80 for g in got) for w in words) / len(words) if words else 0.0


def _score(spine: dict, title: str, subtitle: str, authors: list[str]) -> float:
    # A fragment ("War") must not resolve to a longer title ("War and Peace").
    if _coverage(spine["title_text"], title) < 0.5:
        return 0.0
    t = max(_title_score(spine["title_text"], title),
            _title_score(spine["title_text"], f"{title} {subtitle}") if subtitle else 0)
    if spine.get("author_text"):
        a = max((fuzz.token_set_ratio(norm(spine["author_text"]), norm(x)) for x in authors), default=0)
        return 0.7 * t + 0.3 * a
    # Title-only reads are weaker evidence; a one-word title alone never auto-accepts.
    if len(norm(spine["title_text"]).split()) < 2:
        return min(t, config.ID_ACCEPT - 1)
    return 0.9 * t


def _cm(value: str | None) -> float | None:
    m = re.match(r"([\d.]+)\s*cm", value or "")
    return float(m.group(1)) if m else None


def _google(spine: dict) -> list[dict]:
    q = f'intitle:{spine["title_text"]}' + (f' inauthor:{spine["author_text"]}' if spine.get("author_text") else "")
    key = f"gb|{q}"
    data = cache.get("books", key)
    if data is None:
        params = {"q": q, "maxResults": 8, "printType": "books"}
        if config.GOOGLE_BOOKS_API_KEY:
            params["key"] = config.GOOGLE_BOOKS_API_KEY
        r = httpx.get(GOOGLE_BOOKS, params=params, timeout=15)
        r.raise_for_status()
        data = cache.put("books", key, r.json())
    out = []
    for item in data.get("items", []):
        v = item.get("volumeInfo", {})
        isbn = next((i["identifier"] for i in v.get("industryIdentifiers", []) if i["type"] == "ISBN_13"), "")
        dims = v.get("dimensions", {})
        out.append({"title": v.get("title", ""), "subtitle": v.get("subtitle", ""), "authors": v.get("authors", []),
                    "publisher": v.get("publisher", ""), "published": v.get("publishedDate", ""), "isbn": isbn,
                    "url": v.get("canonicalVolumeLink") or v.get("infoLink", ""), "source": "Google Books",
                    "height_cm": _cm(dims.get("height")), "thickness_cm": _cm(dims.get("thickness"))})
    return out


def _open_library(spine: dict) -> list[dict]:
    params = {"title": spine["title_text"], "limit": 8, "fields": "title,subtitle,author_name,publisher,first_publish_year,isbn,key"}
    if spine.get("author_text"):
        params["author"] = spine["author_text"]
    key = f"ol|{sorted(params.items())}"
    data = cache.get("books", key)
    if data is None:
        r = httpx.get(OPEN_LIBRARY, params=params, timeout=15)
        r.raise_for_status()
        data = cache.put("books", key, r.json())
    return [{"title": d.get("title", ""), "subtitle": d.get("subtitle", ""), "authors": d.get("author_name", []),
             "publisher": (d.get("publisher") or [""])[0], "published": str(d.get("first_publish_year", "")),
             "isbn": next((i for i in d.get("isbn", []) if len(i) == 13), ""),
             "url": f"https://openlibrary.org{d['key']}", "source": "Open Library",
             "height_cm": None, "thickness_cm": None} for d in data.get("docs", [])]


CANDIDATE = re.compile(r"^\s*CANDIDATE\s*\|\s*([^|]+)\|\s*([^|]*)\|\s*([^|]*)\|\s*([^|]*)\|\s*([\dXx-]*)\s*$")
_search_limiter = SyncRateLimiter(config.PRICING_RPM)


def _web_search(spine: dict) -> list[dict]:
    """Fallback when the catalogue APIs are unreachable: candidates found by a
    search-grounded model. Each candidate line must be cited by the grounding
    metadata (a real page), and it still has to pass the same fuzzy match
    against the spine text as any catalogue record."""
    from google.genai import types

    from .pricing import _cited
    from .vision import client

    read = " / ".join(x for x in (spine["title_text"], spine.get("author_text"), spine.get("publisher_text")) if x)
    key = f"web|{read}"
    hit = cache.get("books", key)
    if hit is not None:
        return hit
    _search_limiter.wait()
    prompt = f"""Search the web for published books whose spine could read exactly: "{read}".
List up to 3 real books, one per line, in exactly this format and nothing else:
CANDIDATE | <title> | <author(s)> | <publisher> | <year> | <ISBN-13 or blank>
Only list books you found on a page in this search. If none match, write NONE."""
    resp = client().models.generate_content(
        model=config.PRICING_MODEL, contents=prompt,
        config=types.GenerateContentConfig(tools=[types.Tool(google_search=types.GoogleSearch())], temperature=0))
    gm = resp.candidates[0].grounding_metadata if resp.candidates else None
    chunks = (gm.grounding_chunks or []) if gm else []
    supports = [{"text": s.segment.text or "", "urls": [chunks[i].web.uri for i in s.grounding_chunk_indices or [] if i < len(chunks) and chunks[i].web],
                 "titles": [chunks[i].web.title or "" for i in s.grounding_chunk_indices or [] if i < len(chunks) and chunks[i].web]}
                for s in (gm.grounding_supports or []) if gm and s.segment] if gm else []
    out = []
    for line in (resp.text or "").splitlines():
        m = CANDIDATE.match(line)
        cite = _cited(line, [s for s in supports if s["urls"]]) if m else None
        if not m or not cite:
            continue
        title, authors, publisher, year, isbn = (g.strip() for g in m.groups())
        out.append({"title": title, "subtitle": "", "authors": [a.strip() for a in authors.split(",") if a.strip()],
                    "publisher": publisher, "published": year, "isbn": isbn.replace("-", "") if len(isbn.replace("-", "")) == 13 else "",
                    "url": cite["urls"][0], "source": f'web search ({cite["titles"][0]})', "height_cm": None, "thickness_cm": None})
    return cache.put("books", key, out)


def identify(spine: dict) -> dict:
    """spine: merged reading with title_text / author_text / publisher_text."""
    blank = {"status": "unidentified", "title": "", "author": "", "edition": "", "isbn": "", "id_confidence": 0.0,
             "catalogue_url": "", "catalogue_source": "", "published": "", "known_height_cm": None,
             "known_thickness_cm": None, "review": None}
    if len(norm(spine.get("title_text", ""))) < 3:
        return {**blank, "review": "spine text not legible" if spine.get("legibility") != "clear" else None}
    def _retailers(s):
        return shops.catalogue_candidates(s["title_text"], s.get("author_text", ""), config.COUNTRY)

    candidates = []
    lookups = (_google, _open_library) + ((_web_search,) if config.PRICING_SEARCH else ()) + (_retailers,)
    for lookup in lookups:
        try:
            candidates = lookup(spine)
        except Exception:  # unreachable / rate-limited source: try the next one
            candidates = []
        if candidates:
            break
    if not candidates:
        return {**blank, "review": f'no catalogue match for spine text "{spine["title_text"]}"'}
    scored = sorted(((_score(spine, c["title"], c["subtitle"], c["authors"]), c) for c in candidates),
                    key=lambda x: x[0], reverse=True)
    score, best = scored[0]
    if score < config.ID_REVIEW:
        return {**blank, "review": f'weak catalogue match ({score:.0f}) for "{spine["title_text"]}"; title not guessed'}
    # Edition is claimed only when the spine's publisher agrees with the record.
    pub_ok = spine.get("publisher_text") and fuzz.partial_ratio(norm(spine["publisher_text"]), norm(best["publisher"])) >= 80
    result = {**blank, "status": "identified", "title": best["title"], "author": ", ".join(best["authors"]),
              "edition": f'{best["publisher"]} {best["published"]}'.strip() if pub_ok else "",
              "isbn": best["isbn"] if pub_ok else "", "id_confidence": round(score / 100, 2),
              "catalogue_url": best["url"], "catalogue_source": best["source"], "published": best["published"],
              "known_height_cm": best["height_cm"] if pub_ok else None,
              "known_thickness_cm": best["thickness_cm"] if pub_ok else None}
    if score < config.ID_ACCEPT:
        result["review"] = f"low-confidence identification ({score:.0f}/100)"
    return result
