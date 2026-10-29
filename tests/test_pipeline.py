import types

import cv2
import numpy as np
import pytest

from app import identify, merge, packet, pricing, room, scale


def synthetic_shelf(px_per_cm=12.0, tilt=0.12):
    """Dark scene with an A4 sheet and a 2.5 x 20 cm 'spine' on the same plane,
    seen through a perspective warp. Returns (image, spine box in 0-1000 coords, sheet box)."""
    W, H = 1600, 900
    plane = np.full((H, W, 3), 40, np.uint8)
    ox, oy = 300, 200
    a4 = (int(21.0 * px_per_cm), int(29.7 * px_per_cm))
    cv2.rectangle(plane, (ox, oy), (ox + a4[0], oy + a4[1]), (245, 245, 245), -1)
    sx, sy = ox + a4[0] + 200, oy + 50
    sp = (int(2.5 * px_per_cm), int(20.0 * px_per_cm))
    cv2.rectangle(plane, (sx, sy), (sx + sp[0], sy + sp[1]), (30, 30, 160), -1)
    src = np.float32([[0, 0], [W, 0], [W, H], [0, H]])
    dst = np.float32([[0, 0], [W, H * tilt], [W, H * (1 - tilt)], [0, H]])
    M = cv2.getPerspectiveTransform(src, dst)
    img = cv2.warpPerspective(plane, M, (W, H))

    def norm_box(x0, y0, x1, y1):
        pts = cv2.perspectiveTransform(np.float32([[x0, y0], [x1, y0], [x1, y1], [x0, y1]]).reshape(-1, 1, 2), M).reshape(-1, 2)
        return [int(pts[:, 1].min() / H * 1000), int(pts[:, 0].min() / W * 1000), int(pts[:, 1].max() / H * 1000), int(pts[:, 0].max() / W * 1000)]

    return img, norm_box(sx, sy, sx + sp[0], sy + sp[1]), norm_box(ox, oy, ox + a4[0], oy + a4[1])


def test_a4_homography_measures_spine_under_perspective():
    img, spine_box, sheet_box = synthetic_shelf()
    corners = scale.find_sheet(img, sheet_box)
    assert corners is not None
    Hcm = scale.plane_homography(corners)
    w, h = scale.measure_box(Hcm, spine_box, img.shape[1], img.shape[0])
    assert h == pytest.approx(20.0, rel=0.08)
    assert w == pytest.approx(2.5, rel=0.25)  # thin side: a few px of box error is a large fraction


def test_sheet_rejected_when_not_a4_shaped():
    img = np.full((600, 800, 3), 40, np.uint8)
    cv2.rectangle(img, (100, 280), (700, 320), (250, 250, 250), -1)  # long thin strip
    assert scale.find_sheet(img, [450, 100, 550, 900]) is None


def test_a4_sheet_is_not_glare_but_a_highlight_blob_is():
    from app import quality
    img = np.random.default_rng(1).integers(60, 120, (720, 1280, 3), dtype=np.uint8)
    cv2.rectangle(img, (60, 330), (250, 600), (255, 255, 255), -1)   # A4 sheet, ~5.5% of frame
    assert "glare" not in quality.assess(img)["problems"]
    cv2.circle(img, (800, 300), 120, (255, 255, 255), -1)            # specular hotspot
    assert "glare" in quality.assess(img)["problems"]


def det(frame, x0, title="", seg="A1", chain=0, leg="clear"):
    return {"segment": seg, "chain": chain, "frame": frame, "shelf_box": (x0, 0, x0 + 20, 200), "legibility": leg,
            "title_text": title, "author_text": "", "publisher_text": "", "frame_ref": f"frames/{frame:05d}.jpg",
            "t": frame, "box_2d": [0, 0, 1, 1], "orientation": "upright", "height_cm": None, "thickness_cm": None}


def test_merge_counts_each_book_once_across_overlapping_frames():
    # Three books seen in frame 0, then the camera moves ~1 book right: two repeats + one new.
    dets = [det(0, 0, "Alpha"), det(0, 22, "Beta"), det(0, 44, ""),
            det(1, 23, "Beta"), det(1, 45, "", leg="unreadable"), det(1, 66, "Delta")]
    clusters, warnings = merge.cluster(dets)
    assert len(clusters) == 4 and not warnings


def test_merge_never_joins_two_detections_from_the_same_frame():
    clusters, _ = merge.cluster([det(0, 0, "A"), det(0, 2, "B")])  # heavy overlap, same frame
    assert len(clusters) == 2


def test_merge_joins_broken_chains_only_through_matching_text():
    dets = [det(0, 0, "The Name of the Rose", chain=0), det(5, 500, "The Name of the Rose", chain=1), det(5, 530, "", chain=1, leg="unreadable")]
    clusters, warnings = merge.cluster(dets)
    assert len(clusters) == 2 and warnings


def test_room_rectangle_and_areas():
    walls = [{"index": 1, "width_m": 4.0, "height_m": 2.7, "frame_ref": "a", "shelving_units_m2": 1.6},
             {"index": 2, "width_m": 3.0, "height_m": 2.8, "frame_ref": "b", "shelving_units_m2": 0},
             {"index": 3, "width_m": 4.2, "height_m": 2.7, "frame_ref": "c", "shelving_units_m2": 1.6},
             {"index": 4, "width_m": 3.0, "height_m": 2.7, "frame_ref": "d", "shelving_units_m2": 0}]
    r = room.compute(walls)
    assert r["length_m"] == 4.1 and r["width_m"] == 3.0 and r["height_m"] == 2.7
    assert r["floor_area_m2"] == pytest.approx(12.3)
    assert r["wall_area_m2"] == pytest.approx(2 * 7.1 * 2.7, rel=1e-3)
    assert r["shelved_wall_area_m2"] == 3.2


def test_room_unknown_without_both_directions():
    r = room.compute([{"index": 1, "width_m": 4.0, "height_m": 2.7, "frame_ref": "a"}])
    assert r["floor_area_m2"] is None and r["notes"]


def test_price_without_grounding_citation_is_dropped(monkeypatch):
    monkeypatch.setattr(pricing, "_resolve", lambda u: u)
    text = "BOOK 1 | NEW | 499 | INR | Amazon.in | new\nBOOK 1 | USED | 250 | INR | OLX | good"
    supports = [{"text": "BOOK 1 | NEW | 499 | INR | Amazon.in | new", "urls": ["https://amazon.in/x"], "titles": ["amazon.in"]}]
    rows = pricing._parse(text, supports)
    new = next(r for r in rows if r["field"] == "NEW")
    used = next(r for r in rows if r["field"] == "USED")
    assert new["amount"] == 499 and new["url"] == "https://amazon.in/x"
    assert used["amount"] is None and "fabricated" in used["reason"]


def test_shop_matching_skips_translations_out_of_stock_and_wrong_author():
    from app import shops

    def p(title, price, handle, available=True, text=""):
        return {"title": title, "price": price, "handle": handle, "available": available,
                "url": f"https://shop/{handle}", "text": text}
    products = [p("Atomic Habits - Punjabi", 449, "atomic-habits-punjabi"),
                p("Atomic Habits", 1170, "atomic-habits-james-clear-penguin-audio"),
                p("Atomic Habits", 0, "atomic-habits-book-james-clear-9781847941831", available=False),
                p("Atomic Habits", 950, "atomic-habits-someone-else"),
                p("Atomic Habits", 900, "atomic-habits-paperback-james-clear-1"),
                p("Atomic Habits", 4.45, "atomic-habits-book-james-clear-9780735211292")]
    best = shops.match_book(products, "Atomic Habits", "James Clear")
    assert [b["price"] for b in best] == [4.45, 900]          # translation, audio, out of stock, wrong author all dropped
    assert shops.match_book(products, "Atomic Habits", "James Clear", isbn="9780735211292")[0]["isbn_match"]
    assert shops.match_book([p("War and Peace", 300, "war-and-peace-tolstoy")], "War") == []  # fragment never matches


def test_identify_refuses_weak_matches(monkeypatch):
    cand = [{"title": "War and Peace", "subtitle": "", "authors": ["Leo Tolstoy"], "publisher": "Penguin", "published": "1869",
             "isbn": "9780140447934", "url": "u", "source": "Google Books", "height_cm": 19.8, "thickness_cm": 6.0}]
    monkeypatch.setattr(identify, "_google", lambda s: cand)
    weak = identify.identify({"title_text": "War", "author_text": "", "publisher_text": "", "legibility": "partial"})
    assert weak["status"] == "unidentified" and weak["title"] == ""
    strong = identify.identify({"title_text": "War and Peace", "author_text": "Tolstoy", "publisher_text": "Penguin", "legibility": "clear"})
    assert strong["status"] == "identified" and strong["isbn"] == "9780140447934"
    no_pub = identify.identify({"title_text": "War and Peace", "author_text": "Tolstoy", "publisher_text": "", "legibility": "clear"})
    assert no_pub["status"] == "identified" and no_pub["isbn"] == "" and no_pub["edition"] == ""


def _book(pos, title, amount, used, published="2015"):
    ident = {"status": "identified", "title": title, "author": "X", "edition": "", "isbn": "", "id_confidence": 0.9,
             "catalogue_url": "", "catalogue_source": "", "published": published, "review": None}
    return {"key": title, "shelf": "A1", "chain": 0, "position": pos, "frame_ref": "frames/00001.jpg", "frame_box_2d": [0, 0, 1, 1],
            "t": 1.0, "reading": {"title_text": title, "author_text": "", "publisher_text": "", "legibility": "clear"},
            "orientation": "upright", "sightings": 2, "shelf_box": (pos * 20, 0, pos * 20 + 20, 200),
            "spine_height_cm": 20.0, "spine_thickness_cm": 2.0, "scale_method": "A4 sheet in the same frame", "ident": ident,
            "_price": {"replacement_cost": {"amount": amount, "source": "s", "url": "u", "retrieved_at": "d", "converted": False},
                       "used_value": {"amount": used, "source": "s", "url": "u", "retrieved_at": "d", "condition_assumed": "good"}, "review": []}}


def test_packet_totals_are_sums_of_lines_and_rare_books_are_not_priced():
    books = [_book(1, "One", 300, 150), _book(2, "Two", 450, 200), _book(3, "Old Rare", 900, 400, published="1902"),
             _book(4, "Pricey", 9000, 5000)]
    fake = types.SimpleNamespace(
        facts=[{"kind": "signed", "target": "Two", "note": "", "t": 0}], currency="INR", country="IN", items=[],
        prices={f'{b["ident"]["title"]}|X': b["_price"] for b in books}, id="t", captured_at="", device="", ended_at=10.0, t0=0.0,
        frames=[], vision_usage={"calls": 0, "in_tokens": 0, "out_tokens": 0}, timings={}, pricing_stats0=dict(pricing.stats),
        now=lambda: 10.0)
    p = packet.build(fake, books, [], {}, [], {}, room.compute([]))
    status = {b["title"]: b["status"] for b in p["books"]}
    assert status == {"One": "identified", "Two": "needs_appraisal", "Old Rare": "needs_appraisal", "Pricey": "needs_appraisal"}
    assert p["totals"]["books_replacement_cost"] == 300 == sum(b["replacement_cost"]["amount"] or 0 for b in p["books"])
    assert p["totals"]["books_used_value"] == 150
    assert p["totals"]["excluded_from_totals"] == 3
    assert {r["ref_id"] for r in p["review_queue"]} >= {"A1-02", "A1-03", "A1-04", "room"}


def test_no_walls_statement_explains_missing_room_and_front_labels(tmp_path, monkeypatch):
    import asyncio

    from app import sweep as sweep_mod
    monkeypatch.setattr(sweep_mod.config, "SWEEPS_DIR", tmp_path)

    async def run():
        async def quiet(_):
            pass
        s = sweep_mod.Sweep(quiet)
        assert s.start_segment("front", "")["label"] == "F1"
        assert s.start_segment("front", "front")["label"] == "F1"   # repeated at once: same group (FAILURES #14)
        s.frames.append({"segment": "F1"}); s.segment["t"] -= 60
        assert s.start_segment("front", "front")["label"] == "F2"   # a later, real new group
        s.expected_shelves["1"] = 5
        s.start_segment("shelf", "A1")
        assert s.shelf_progress()["shelves_possibly_missed"] == 4
        s.add_fact("no_walls", "room")
        return await s.finish()

    p = asyncio.run(run())
    assert p["room"]["floor_area_m2"] is None
    assert "no walls to capture" in next(r["reason"] for r in p["review_queue"] if r["ref_id"] == "room")
    assert p["sweep"]["capture_modes"] == ["front", "shelf"]


def test_front_only_sweep_says_why_room_is_missing(tmp_path, monkeypatch):
    import asyncio

    from app import live, sweep as sweep_mod
    monkeypatch.setattr(sweep_mod.config, "SWEEPS_DIR", tmp_path)

    async def run():
        async def quiet(_):
            pass
        s = sweep_mod.Sweep(quiet)
        s.start_segment("front", "")
        return await s.finish()

    p = asyncio.run(run())
    assert "front-view sweep" in next(r["reason"] for r in p["review_queue"] if r["ref_id"] == "room")
    assert "No walls were captured" in live.packet_summary(p)


def _verified_packet():
    books = [_book(1, "One", 300, 150), _book(2, "Two", 450, 200)]
    fake = types.SimpleNamespace(
        facts=[], currency="INR", country="IN", items=[], prices={f'{b["ident"]["title"]}|X': b["_price"] for b in books},
        id="t", captured_at="", device="", ended_at=10.0, t0=0.0, frames=[], segments=[],
        vision_usage={"calls": 0, "in_tokens": 0, "out_tokens": 0}, timings={}, pricing_stats0=dict(pricing.stats),
        now=lambda: 10.0)
    return packet.build(fake, books, [], {}, [], {}, room.compute([]))


def test_customer_packet_is_separate_labelled_and_recomputed():
    import copy

    from app import report
    original = _verified_packet()
    before = copy.deepcopy(original)
    corrections = [
        {"target": "A1-01", "title": "", "author": "", "replacement_cost": 500.0, "used_value": None, "comment": "I paid 500 for it"},
        {"target": "Qwzx unknown", "title": "", "author": "", "replacement_cost": 99.0, "used_value": None, "comment": "?"},
        {"target": "new", "title": "The Great Gatsby", "author": "F. Scott Fitzgerald", "replacement_cost": 300.0,
         "used_value": 120.0, "comment": "this one was missed"},
    ]
    p = packet.build_customer(original, corrections)
    assert original == before, "the verified packet must never change"
    one = next(b for b in p["books"] if b["id"] == "A1-01")
    assert one["replacement_cost"]["amount"] == 500 and one["replacement_cost"]["source"] == packet.CUSTOMER_SOURCE
    assert one["system_values"]["replacement_cost"]["amount"] == 300
    assert one["customer_advised"] and one["user_comment"] == "I paid 500 for it"
    new = next(b for b in p["books"] if b["id"].startswith("C-"))
    assert new["title"] == "The Great Gatsby" and new["frame_ref"] == ""
    assert p["totals"]["books_replacement_cost"] == 500 + 450 + 300 == sum(b["replacement_cost"]["amount"] or 0 for b in p["books"])
    assert p["based_on"]["verified_totals"]["books_replacement_cost"] == 750
    assert [c["applied"] for c in p["customer_corrections"]] == [True, False, True]
    reasons = " ".join(r["reason"] for r in p["review_queue"])
    assert "customer-advised" in reasons and "not seen in the sweep" in reasons
    html = report.render(p)
    assert "Customer-advised claim packet" in html and "I paid 500 for it" in html


def test_sweep_correction_validates_target_and_parses_amounts(tmp_path, monkeypatch):
    import asyncio

    from app import sweep as sweep_mod
    monkeypatch.setattr(sweep_mod.config, "SWEEPS_DIR", tmp_path)

    async def run():
        async def quiet(_):
            pass
        s = sweep_mod.Sweep(quiet)
        s.result = _verified_packet()
        bad = s.add_correction("Some Book Nobody Has", replacement_cost="200", comment="x")
        good = s.add_correction("A1-02", replacement_cost="₹1,250", comment="it was a hardcover")
        built = s.build_customer_packet()
        s.finished = True
        for w in s.workers:
            w.cancel()
        s.pricing_task.cancel()
        return s, bad, good, built

    s, bad, good, built = asyncio.run(run())
    assert not bad["ok"] and "lines" in bad
    assert good["ok"] and good["correction"]["replacement_cost"] == 1250.0
    assert built["customer_version"] == 1
    assert (s.dir / "customer_advised_claim_packet.json").exists() and (s.dir / "customer_advised_report.html").exists()


def test_vision_client_is_recreated_for_a_new_event_loop():
    """Regression (FAILURES #12): a client made inside the doctor's asyncio.run() must not be
    reused by the server's later event loop."""
    import asyncio

    from app import vision

    async def get():
        return vision.client()

    first = asyncio.run(get())
    second = asyncio.run(get())
    assert first is not second
    assert vision.client() is second  # no running loop (worker thread case): reuse what exists


def test_repeated_step_reuses_the_segment_instead_of_double_counting(tmp_path, monkeypatch):
    """Regression (FAILURES #14): agent + button (or the agent twice) starting the same step must
    not split one group of books across two segments."""
    import asyncio

    from app import sweep as sweep_mod
    monkeypatch.setattr(sweep_mod.config, "SWEEPS_DIR", tmp_path)

    async def run():
        async def quiet(_):
            pass
        s = sweep_mod.Sweep(quiet)
        f1 = s.start_segment("front", "")
        assert s.start_segment("front", "")["label"] == f1["label"] == "F1"      # repeated at once
        a1 = s.start_segment("shelf", "A1"); tracker = s.trackers["A1"]
        assert s.start_segment("shelf", "A1") is a1                              # same shelf again
        s.frames.append({"segment": "A1"}); a1["t"] -= 60                        # A1 was filmed, long ago
        s.start_segment("shelf", "A2"); s.start_segment("shelf", "A1")          # come back to A1 later
        assert s.trackers["A1"] is tracker                                       # tracker kept, not reset
        s.frames.append({"segment": "A1"}); s.segment["t"] -= 60
        assert s.start_segment("front", "")["label"] == "F2"                     # a real new group
        s.finished = True
        for w in s.workers:
            w.cancel()
        s.pricing_task.cancel()

    asyncio.run(run())


def test_same_books_in_two_segments_are_flagged_as_possible_double_count():
    from app.sweep import Sweep

    def book(seg, title):
        return {"shelf": seg, "reading": {"title_text": title}}
    titles = ["ATOMIC HABITS", "SAPIENS", "IKIGAI", "WINGS OF FIRE"]
    warn = Sweep._duplicate_segments([book("F1", t) for t in titles] + [book("F2", t) for t in titles])
    assert warn and "double count" in warn[0]
    assert not Sweep._duplicate_segments([book("A1", t) for t in titles] + [book("A2", "OTHER BOOK")])


def test_bookshop_fallback_ignores_translated_editions(monkeypatch):
    from app import shops
    products = [{"title": "Ikigai - Tamil", "handle": "ikigai-tamil", "price": 299, "available": True, "url": "u1", "text": "hector garcia"},
                {"title": "Ikigai", "handle": "ikigai-hector-garcia", "price": 399, "available": True, "url": "u2", "text": "hector garcia"}]
    monkeypatch.setattr(shops, "shopify_search", lambda shop, q: {"retrieved_at": "d", "products": products})
    titles = [c["title"] for c in shops.catalogue_candidates("IKIGAI", "HECTOR GARCIA", "IN")]
    assert titles == ["Ikigai"]


def test_plain_wall_is_not_rejected_as_blurry(tmp_path, monkeypatch):
    """Regression (FAILURES #15): a sharp photo of a plain painted wall has little fine detail;
    it must still become a keyframe (the blur check is for reading spines only)."""
    import asyncio
    import time as _time

    from app import sweep as sweep_mod
    monkeypatch.setattr(sweep_mod.config, "SWEEPS_DIR", tmp_path)

    async def no_vision(jpeg, kind):  # keep the test offline
        return {"reference_sheet": {"present": False}, "spines": [], "items": [], "_meta": {"latency_s": 0, "in_tokens": 0, "out_tokens": 0}}
    monkeypatch.setattr(sweep_mod.vision, "analyse", no_vision)
    plain = cv2.imencode(".jpg", np.full((720, 1280, 3), 200, np.uint8))[1].tobytes()

    async def run():
        async def quiet(_):
            pass
        s = sweep_mod.Sweep(quiet)
        s.start_segment("wall", "1")
        s.segment["t"] -= 5                       # the wall shot starts after the 1 s settle time
        await s.add_frame(plain)
        kf = s.frames[-1]["keyframe"]
        s.start_segment("front", "")
        q = await s.add_frame(plain)              # the same picture on a book shelf view stays "blurry"
        s.finished = True
        for w in s.workers:
            w.cancel()
        s.pricing_task.cancel()
        return kf, q

    keyframe, front_q = asyncio.run(run())
    assert keyframe is True
    assert "blurry" in front_q["problems"]
