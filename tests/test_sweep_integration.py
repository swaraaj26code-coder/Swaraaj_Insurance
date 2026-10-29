"""End-to-end offline run of the sweep pipeline on a synthetic pan.

A wide synthetic shelf with a known number of spines (and an A4 sheet) is
"filmed" by sliding a window across it. The vision call is replaced by an
oracle that returns the true boxes for each window, so this test isolates what
our own code does: tracking, keyframe choice, de-duplication, scale and totals."""

import asyncio
import random

import cv2
import numpy as np
import pytest

from app import identify, pricing, sweep as sweep_mod, vision

PX_PER_CM = 9.0


def make_shelf(n=40, seed=7):
    rng = random.Random(seed)
    W, H = 5200, 900
    img = np.random.default_rng(seed).integers(60, 120, (H, W, 3), dtype=np.uint8)  # textured wall
    img = cv2.GaussianBlur(img, (3, 3), 0)
    a4w, a4h = int(21.0 * PX_PER_CM), int(29.7 * PX_PER_CM)
    sheet = (60, H - 120 - a4h, 60 + a4w, H - 120)
    cv2.rectangle(img, sheet[:2], sheet[2:], (250, 250, 250), -1)
    spines, x = [], sheet[2] + 40
    for k in range(n):
        w_cm, h_cm = rng.uniform(1.5, 5.0), rng.uniform(17.5, 26.0)
        w, h = int(w_cm * PX_PER_CM), int(h_cm * PX_PER_CM)
        y1 = H - 120
        colour = tuple(rng.randint(20, 230) for _ in range(3))
        cv2.rectangle(img, (x, y1 - h), (x + w, y1), colour, -1)
        for _ in range(6):  # texture so ORB has something to hold on to
            yy = rng.randint(y1 - h + 5, y1 - 5)
            cv2.line(img, (x + 2, yy), (x + w - 2, yy), tuple(255 - c for c in colour), 2)
        spines.append({"box": (x, y1 - h, x + w, y1), "title": f"Book number {k}" if k % 3 else "", "h_cm": h * 1.0 / PX_PER_CM, "w_cm": w * 1.0 / PX_PER_CM})
        x += w + rng.randint(0, 3)
    # End the scene shortly after the last book, like a real shelf end.
    return np.ascontiguousarray(img[:, :x + 400]), spines, sheet


@pytest.mark.parametrize("kind", ["shelf", "front"])
def test_synthetic_pan_counts_each_book_once(monkeypatch, tmp_path, kind):
    shelf, spines, sheet = make_shelf(n=110)
    fw, fh, step = 1280, 720, 260
    y0 = shelf.shape[0] - fh
    offsets = list(range(0, shelf.shape[1] - fw, step))
    oracle = {}
    frames = []
    for ox in offsets:
        crop = np.ascontiguousarray(shelf[y0:y0 + fh, ox:ox + fw])
        jpeg = cv2.imencode(".jpg", crop, [cv2.IMWRITE_JPEG_QUALITY, 92])[1].tobytes()
        oracle[jpeg] = ox
        frames.append(jpeg)

    def to_norm(b, ox):
        x0, y_0, x1, y_1 = b
        return [int((y_0 - y0) / fh * 1000), int(max(0, x0 - ox) / fw * 1000), int((y_1 - y0) / fh * 1000), int(min(fw, x1 - ox) / fw * 1000)]

    async def fake_analyse(jpeg, kind):
        ox = oracle[jpeg]
        visible = [s for s in spines if s["box"][2] > ox + 3 and s["box"][0] < ox + fw - 3]
        sheet_in = sheet[0] >= ox and sheet[2] <= ox + fw
        return {"reference_sheet": {"present": sheet_in, "box_2d": to_norm(sheet, ox) if sheet_in else []},
                "spines": [{"box_2d": to_norm(s["box"], ox), "orientation": "upright",
                            "legibility": "clear" if s["title"] else "unreadable", "title_text": s["title"],
                            "author_text": "", "publisher_text": "", "colour": ""} for s in visible],
                "items": [], "_meta": {"latency_s": 0.0, "in_tokens": 0, "out_tokens": 0, "model": "oracle"}}

    async def no_prices(*a, **k):
        return {}

    monkeypatch.setattr(vision, "analyse", fake_analyse)
    blank = identify.identify({"title_text": ""})
    monkeypatch.setattr(identify, "identify", lambda r: {**blank, "review": "offline test"})
    monkeypatch.setattr(pricing, "price_books", no_prices)
    monkeypatch.setattr(pricing, "price_items", no_prices)
    monkeypatch.setattr(sweep_mod.config, "SWEEPS_DIR", tmp_path)

    async def run():
        async def quiet(_):
            pass
        s = sweep_mod.Sweep(quiet)
        s.start_segment(kind, "A1" if kind == "shelf" else "")
        for jpeg in frames:
            await s.add_frame(jpeg)
        return await s.finish(), s

    packet, s = asyncio.run(run())
    count = packet["totals"]["book_count"]
    print(f"\nsynthetic pan: {len(frames)} frames, {s.vision_usage['calls']} keyframes analysed, "
          f"counted {count} of {len(spines)} books")
    assert abs(count - len(spines)) <= max(1, round(0.05 * len(spines))), f"counted {count}, truth {len(spines)}"
    assert s.vision_usage["calls"] < len(frames), "keyframe selection should skip redundant frames"
    measured = [b for b in packet["books"] if b["spine_height_cm"]]
    truth_h = sorted(sp["h_cm"] for sp in spines)
    got_h = sorted(b["spine_height_cm"] for b in measured)
    print(f"median spine height: measured {np.median(got_h):.1f} cm vs true {np.median(truth_h):.1f} cm; "
          f"shelf run {packet['totals']['shelf_run_m']} m vs true {(spines[-1]['box'][2] - spines[0]['box'][0]) / PX_PER_CM / 100:.2f} m")
    assert abs(np.median(got_h) - np.median(truth_h)) / np.median(truth_h) < 0.1
