"""Stage 2: turn per-frame spine detections into one entry per physical book.

Detections are clustered per shelf and tracking chain by overlap in shelf
coordinates. Two detections from the same frame are never merged (they are
different books by definition). When tracking broke mid-shelf, chains are
joined only through spines whose text matches; anything else is kept and the
shelf is flagged, because a possible double-count must be visible to review."""

from statistics import median

from rapidfuzz import fuzz

from .identify import norm

IOU_MIN = 0.3
LEGIBILITY_RANK = {"clear": 2, "partial": 1, "unreadable": 0}


def iou(a, b) -> float:
    ix = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = ix * iy
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / union if union > 0 else 0.0


def _box(cluster) -> tuple:
    boxes = [d["shelf_box"] for d in cluster["dets"]]
    return tuple(median(b[i] for b in boxes) for i in range(4))


def _text(cluster) -> str:
    return norm(cluster["reading"]["title_text"] + " " + cluster["reading"]["author_text"])


def _best_reading(dets: list[dict]) -> dict:
    return max(dets, key=lambda d: (LEGIBILITY_RANK[d["legibility"]], len(d["title_text"]) + len(d["author_text"])))


def cluster(dets: list[dict]) -> tuple[list[dict], list[str]]:
    """dets need: segment, chain, frame, shelf_box, legibility, title_text, author_text, publisher_text.
    Returns (clusters, warnings). Each cluster: {segment, chain, dets, reading, shelf_box}."""
    by_chain: dict[tuple, list[dict]] = {}
    for d in dets:
        by_chain.setdefault((d["segment"], d["chain"]), []).append(d)

    clusters, warnings = [], []
    for (segment, chain), group in by_chain.items():
        local: list[dict] = []
        for d in sorted(group, key=lambda d: (d["frame"], d["shelf_box"][0])):
            best, best_iou = None, IOU_MIN
            for c in local:
                if any(x["frame"] == d["frame"] for x in c["dets"]):
                    continue
                o = iou(_box(c), d["shelf_box"])
                if o >= best_iou:
                    best, best_iou = c, o
            if best is None:
                local.append({"segment": segment, "chain": chain, "dets": [d]})
            else:
                best["dets"].append(d)
        clusters.extend(local)

    for c in clusters:
        c["reading"] = _best_reading(c["dets"])
        c["shelf_box"] = _box(c)

    # Join tracking chains of the same shelf through matching legible text.
    segments = {c["segment"] for c in clusters}
    for seg in segments:
        chains = sorted({c["chain"] for c in clusters if c["segment"] == seg})
        if len(chains) < 2:
            continue
        warnings.append(f"shelf {seg}: tracking broke {len(chains) - 1} time(s); unreadable spines near the break may be double-counted")
        first = [c for c in clusters if c["segment"] == seg and c["chain"] == chains[0]]
        for later in chains[1:]:
            for c in [c for c in clusters if c["segment"] == seg and c["chain"] == later]:
                t = _text(c)
                if len(t) < 6:
                    continue
                match = next((f for f in first if len(_text(f)) >= 6 and fuzz.ratio(t, _text(f)) >= 85), None)
                if match:
                    match["dets"].extend(c["dets"])
                    match["reading"] = _best_reading(match["dets"])
                    clusters.remove(c)
            first = [c for c in clusters if c["segment"] == seg]
    return clusters, warnings
