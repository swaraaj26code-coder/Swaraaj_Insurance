"""Score a claim packet against hand-collected ground truth, bar by bar.

    python -m eval.score data/sweeps/<id>/claim_packet.json eval/ground_truth

Ground truth folder (templates in eval/ground_truth_template):
  books.csv  shelf,position,title,author,legible,height_cm,thickness_cm,price_new,price_used,price_source_url
             (one row per physical book; legible = yes/no as judged by a human from normal standing distance)
  items.csv  category,description
  room.json  {"length_m":..,"width_m":..,"height_m":..}  (tape-measured)

Interpretations of the brief (stated, not hidden):
  * Title bar: correct = legible true titles that the system identified on the right shelf (fuzzy >= 85).
    Confidently wrong = system "identified" lines (not in review) that match no true title on that shelf,
    as a share of all system-identified lines.
  * Dimension and price bars use mean absolute percentage error over the matched sample.
  * Every line of ground truth is used: no shelf or frame is left out."""

import csv
import json
import sys
from pathlib import Path

from rapidfuzz import fuzz

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.identify import norm  # noqa: E402


def _rows(path: Path) -> list[dict]:
    with open(path, newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _mape(pairs):
    errs = [abs(p - t) / t for p, t in pairs if p is not None and t]
    return (sum(errs) / len(errs), len(errs)) if errs else (None, 0)


def score(packet: dict, gt: Path) -> list[dict]:
    books_gt = _rows(gt / "books.csv")
    items_gt = _rows(gt / "items.csv")
    room_gt = json.loads((gt / "room.json").read_text())
    books = packet["books"]
    review_ids = {r["ref_id"] for r in packet["review_queue"]}
    out = []

    true_count, got = len(books_gt), packet["totals"]["book_count"]
    err = abs(got - true_count) / true_count
    out.append({"measure": "Book count", "bar": "within 5%", "system": got, "truth": true_count,
                "result": f"{err:.1%} off", "pass": err <= 0.05})

    # Title identification: one-to-one matching per shelf.
    identified = [b for b in books if b["status"] in ("identified", "needs_appraisal") and b["title"]]
    used, correct = set(), 0
    legible = [g for g in books_gt if g["legible"].strip().lower() in ("yes", "y", "true", "1")]
    for g in legible:
        for b in identified:
            if b["id"] in used or b["shelf"] != g["shelf"]:
                continue
            if fuzz.token_set_ratio(norm(b["title"]), norm(g["title"])) >= 85:
                used.add(b["id"]); correct += 1
                break
    confident = [b for b in identified if b["id"] not in review_ids]
    wrong = [b for b in confident if not any(g["shelf"] == b["shelf"] and fuzz.token_set_ratio(norm(b["title"]), norm(g["title"])) >= 85 for g in books_gt)]
    rate = correct / len(legible) if legible else 0
    wrong_rate = len(wrong) / len(identified) if identified else 0
    out.append({"measure": "Title identification", "bar": ">=70% of legible correct, <=3% confidently wrong",
                "system": f"{correct}/{len(legible)} correct; {len(wrong)} confidently wrong",
                "truth": f"{len(legible)} legible", "result": f"{rate:.0%} correct, {wrong_rate:.1%} wrong",
                "pass": rate >= 0.7 and wrong_rate <= 0.03, "wrong_titles": [b["title"] for b in wrong]})

    def match(g):
        return next((b for b in identified if b["shelf"] == g["shelf"] and fuzz.token_set_ratio(norm(b["title"]), norm(g["title"])) >= 85), None)

    measured = [g for g in books_gt if _num(g["height_cm"])]
    pairs_h, pairs_t = [], []
    for g in measured:
        b = match(g) or next((b for b in books if b["shelf"] == g["shelf"] and b["position"] == int(g["position"] or 0)), None)
        if b:
            pairs_h.append((b["spine_height_cm"], _num(g["height_cm"])))
            pairs_t.append((b["spine_thickness_cm"], _num(g["thickness_cm"])))
    mh, nh = _mape(pairs_h)
    mt, nt = _mape(pairs_t)
    out.append({"measure": "Spine dimensions", "bar": "within 15% (MAPE)", "system": f"{nh} of {len(measured)} matched",
                "truth": f"{len(measured)} hand-measured", "result": f"height {mh:.1%}, thickness {mt:.1%}" if mh is not None and mt is not None else "no matches",
                "pass": mh is not None and mt is not None and mh <= 0.15 and mt <= 0.15})

    priced = [g for g in books_gt if _num(g["price_new"])]
    pairs_p = [(m["replacement_cost"]["amount"], _num(g["price_new"])) for g in priced if (m := match(g))]
    mp, np_ = _mape(pairs_p)
    out.append({"measure": "Book prices", "bar": "within 25% (MAPE), same source type", "system": f"{np_} of {len(priced)} priced",
                "truth": f"{len(priced)} hand-checked", "result": f"{mp:.1%}" if mp is not None else "no matches",
                "pass": mp is not None and mp <= 0.25 and np_ >= min(15, len(priced))})

    found, used_items = 0, set()
    for g in items_gt:
        for it in packet["items"]:
            if it["id"] in used_items or it["category"] != g["category"].strip().lower():
                continue
            if fuzz.token_set_ratio(norm(it["description"]), norm(g["description"])) >= 50:
                used_items.add(it["id"]); found += 1
                break
    recall = found / len(items_gt) if items_gt else 0
    out.append({"measure": "Non-book items", "bar": ">=80% found with correct category", "system": f"{len(packet['items'])} listed",
                "truth": f"{len(items_gt)} items", "result": f"{found}/{len(items_gt)} = {recall:.0%}", "pass": recall >= 0.8})

    r = packet["room"]
    floor_t = room_gt["length_m"] * room_gt["width_m"]
    walls_t = 2 * (room_gt["length_m"] + room_gt["width_m"]) * room_gt["height_m"]
    for name, got, truth, bar in (("Floor area", r["floor_area_m2"], floor_t, 0.10), ("Wall area", r["wall_area_m2"], walls_t, 0.15)):
        e = abs(got - truth) / truth if got else None
        out.append({"measure": name, "bar": f"within {bar:.0%}", "system": got, "truth": round(truth, 2),
                    "result": f"{e:.1%} off" if e is not None else "not measured", "pass": e is not None and e <= bar})

    ttp = packet["metrics"].get("time_to_packet_s")
    out.append({"measure": "Time to packet", "bar": "< 300 s", "system": ttp, "truth": "-", "result": f"{ttp} s", "pass": bool(ttp and ttp < 300)})
    return out


def to_markdown(rows: list[dict]) -> str:
    lines = ["| Measure | Pass bar | System | Ground truth | Result | Pass |", "|---|---|---|---|---|---|"]
    for r in rows:
        lines.append(f'| {r["measure"]} | {r["bar"]} | {r["system"]} | {r["truth"]} | {r["result"]} | {"PASS" if r["pass"] else "FAIL"} |')
    return "\n".join(lines)


if __name__ == "__main__":
    packet_path, gt_dir = Path(sys.argv[1]), Path(sys.argv[2])
    rows = score(json.loads(packet_path.read_text(encoding="utf-8")), gt_dir)
    md = to_markdown(rows)
    print(md)
    (packet_path.parent / "results.md").write_text(md + "\n", encoding="utf-8")
    (packet_path.parent / "results.json").write_text(json.dumps(rows, indent=1), encoding="utf-8")
