"""One sweep: frames in, live inventory and capture coaching out, packet at the end.

Pipeline per uploaded frame (cheap, every frame):
    quality check -> save as evidence -> pan tracking (shelf segments) -> keyframe?
Per keyframe (rate-limited, background workers):
    vision -> scale from A4 sheet -> spine/item/wall detections
On demand (live, and once more at the end):
    merge spines -> identify -> price -> room -> packet
Each stage keeps its own timings so cost and latency per stage can be reported."""

import asyncio
import datetime as dt
import json
import logging
import re
import time
import uuid
from collections import defaultdict
from statistics import median

from rapidfuzz import fuzz

from . import config, identify, merge, packet, pricing, quality, report, room, scale, vision
from .explain import explain
from .tracker import ShelfTracker, sheet_to_shelf_scale, to_shelf

log = logging.getLogger(__name__)
A4_AREA = config.REF_LONG_CM * config.REF_SHORT_CM
STANDARD_HEIGHT_CM = 19.8  # B-format paperback, last-resort scale prior
VISION_WORKERS = 3


class Sweep:
    def __init__(self, notify, country=config.COUNTRY, currency=config.CURRENCY, device=""):
        self.id = dt.datetime.now().strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:4]
        self.dir = config.SWEEPS_DIR / self.id
        (self.dir / "frames").mkdir(parents=True)
        self.notify = notify                 # async fn(event: dict)
        self.country, self.currency, self.device = country, currency, device
        self.captured_at = dt.datetime.now().astimezone().isoformat(timespec="seconds")
        self.t0 = time.monotonic()
        self.ended_at: float | None = None
        self.segment = {"kind": "room", "label": "room", "index": 0}
        self.segments: list[dict] = []
        self.trackers: dict[str, ShelfTracker] = {}
        self.frames: list[dict] = []
        self.spine_dets: list[dict] = []
        self.items: list[dict] = []          # incrementally de-duplicated
        self.walls: dict[str, dict] = {}     # label -> best measurement
        self.wall_attempts: dict[str, int] = defaultdict(int)
        self.chain_scale: dict[tuple, list[float]] = defaultdict(list)
        self.expected_shelves: dict[str, int] = {}  # wall label -> shelves counted on its bookcases
        self.barge_ins = {"local": 0, "confirmed": 0}  # phone-side pauses / model-confirmed interruptions
        self.corrections: list[dict] = []           # policyholder's own corrections (customer-advised packet)
        self.customer_result: dict | None = None
        self.facts: list[dict] = []
        self.ids: dict[str, dict] = {}       # reading key -> identification
        self.prices: dict[str, dict] = {}    # title|author -> price entry
        self.timings: dict[str, list[float]] = defaultdict(list)
        self.vision_usage = {"calls": 0, "in_tokens": 0, "out_tokens": 0}
        self.pricing_stats0 = dict(pricing.stats)
        self.recent_quality: list[dict] = []
        self.said: dict[str, float] = {}     # coaching message key -> last time
        self.last_book_key = ""
        self.last_room_kf = -99.0
        self.queue: asyncio.Queue = asyncio.Queue()
        self.workers = [asyncio.create_task(self._vision_worker()) for _ in range(VISION_WORKERS)]
        self.pricing_task = asyncio.create_task(self._pricing_loop())
        self.finished = False
        self.result: dict | None = None
        self._log_event({"event": "start", "country": country, "currency": currency})

    # ---- segment control (called by the live agent's tools) -------------------
    def start_segment(self, kind: str, label: str) -> dict:
        """kind: 'wall' | 'shelf' | 'front' (books in front of the camera, no shelf) | 'room'.

        The same step asked for again (the agent repeating a tool call, or the agent and a
        button press together) keeps filming into the current segment: a duplicate segment
        would count the same books twice (FAILURES #14)."""
        label = label.strip()
        if kind == "front" and label.lower() == "front":
            label = ""
        cur = self.segment
        same_step = cur["kind"] == kind and (not label or label == cur["label"])
        cur_frames = sum(1 for f in self.frames if f["segment"] == cur["label"])
        if same_step and cur.get("t") is not None and (cur_frames == 0 or self.now() - cur["t"] < 8):
            return cur
        index = sum(1 for s in self.segments if s["kind"] == kind) + 1
        if kind == "front" and not label:
            label = f"F{index}"
        label = label or kind
        self.segment = {"kind": kind, "label": label, "index": index, "t": self.now()}
        self.segments.append(self.segment)
        if kind in vision.SPINE_KINDS and label not in self.trackers:
            # A shelf filmed again later keeps its tracker: a reset would restart chain ids and
            # mix two coordinate systems in the merge. Re-capture then starts a new chain safely.
            self.trackers[label] = ShelfTracker()
        self._log_event({"event": "segment", **self.segment})
        return self.segment

    def set_locale(self, country: str, currency: str):
        self.country, self.currency = country.upper(), currency.upper()
        self._log_event({"event": "locale", "country": self.country, "currency": self.currency})

    def add_fact(self, kind: str, target: str, note: str = "") -> dict:
        target = (target or "").strip()
        if kind in ("first_edition", "signed", "not_mine_book") and target.lower() in ("", "last", "that", "this"):
            target = self.last_book_key
        if kind == "exclude_shelf" and target.lower() in ("", "current", "this"):
            target = self.segment["label"]
        fact = {"kind": kind, "target": target, "note": note, "t": self.now()}
        self.facts.append(fact)
        self._log_event({"event": "fact", **fact})
        return fact

    # ---- frame ingest ----------------------------------------------------------
    def now(self) -> float:
        return round(time.monotonic() - self.t0, 2)

    async def add_frame(self, jpeg: bytes) -> dict:
        if self.finished:
            return {"ok": False}
        t = self.now()
        idx = len(self.frames)
        seg = dict(self.segment)
        img = await asyncio.to_thread(quality.decode, jpeg)
        if img is None:
            return {"ok": False}
        q = await asyncio.to_thread(quality.assess, img)
        ref = f"frames/{idx:05d}.jpg"
        (self.dir / ref).write_bytes(jpeg)
        frame = {"idx": idx, "t": t, "ref": ref, "segment": seg["label"], "kind": seg["kind"], "quality": q,
                 "w": img.shape[1], "h": img.shape[0], "keyframe": False}
        self.frames.append(frame)
        with open(self.dir / "frames.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps({k: frame[k] for k in ("idx", "t", "ref", "segment", "kind")} | {"quality": q}) + "\n")

        placement = None
        if seg["kind"] not in vision.SPINE_KINDS and q["problems"] == ["blurry"]:
            # Sharpness only matters for reading spines. A plain painted wall has almost no fine
            # detail, so its "blur" score is low even when the photo is sharp (FAILURES #15).
            q = {**q, "problems": [], "ok": True}
        if seg["kind"] in vision.SPINE_KINDS:
            placement = await asyncio.to_thread(self.trackers[seg["label"]].add, img, q["ok"])
            # While tracking keeps failing (featureless view), every frame would look "new";
            # cap those to one keyframe per 2 s so the vision quota is not burnt.
            frame["keyframe"] = placement.keyframe and not (
                placement.lost and t - self.said.get(f'kf-{seg["label"]}', -9) < 2.0)
            if frame["keyframe"]:
                self.said[f'kf-{seg["label"]}'] = t
            if placement.lost:
                await self.coach("lost", f'Tracking lost on {self.where(seg)}. Ask them to pan back a little so the last spines are in view again, then continue slowly.', 10)
        elif seg["kind"] == "wall":
            frame["keyframe"] = q["ok"] and seg["label"] not in self.walls and self.wall_attempts[seg["label"]] < 4 \
                and t - seg.get("t", 0) > 1.0 and t - self.said.get(f'wall-kf-{seg["label"]}', -9) > 2.0
            if frame["keyframe"]:
                self.wall_attempts[seg["label"]] += 1
                self.said[f'wall-kf-{seg["label"]}'] = t
        else:
            frame["keyframe"] = q["ok"] and t - self.last_room_kf > 6
            if frame["keyframe"]:
                self.last_room_kf = t

        self.recent_quality = (self.recent_quality + [q])[-4:]
        if len(self.recent_quality) >= 3 and all("blurry" in x["problems"] for x in self.recent_quality[-3:]):
            await self.coach("blur", "The last frames are blurry. Ask them to slow down and hold the phone steady.", 8)
        if sum("glare" in x["problems"] for x in self.recent_quality) >= 3:
            await self.coach("glare", "There is glare in the picture. Ask them to tilt the phone slightly to get rid of the reflection.", 10)
        if sum("dark" in x["problems"] for x in self.recent_quality) >= 3:
            await self.coach("dark", "The picture is too dark to read spines. Ask them to switch on a light.", 15)

        if frame["keyframe"]:
            await self.queue.put((frame, seg, placement, jpeg, img))
        await self.notify({"type": "frame", "idx": idx, "t": t, "quality": q, "keyframe": frame["keyframe"],
                           "queue": self.queue.qsize()})
        return q

    def shelf_progress(self) -> dict:
        """Shelves swept so far vs shelves counted on the bookcases in the wall shots."""
        swept = sorted({s["label"] for s in self.segments if s["kind"] == "shelf"})
        expected = sum(self.expected_shelves.values())
        return {"shelves_swept": swept, "shelves_seen_in_wall_shots": expected,
                "shelves_possibly_missed": max(0, expected - len(swept))}

    @staticmethod
    def where(seg: dict) -> str:
        return f'shelf {seg["label"]}' if seg["kind"] == "shelf" else "the books in front of the camera"

    async def coach(self, key: str, text: str, every_s: float):
        """Tell the live agent something it should say, at most once per every_s."""
        now = self.now()
        if now - self.said.get(key, -999) < every_s or self.finished:
            return
        self.said[key] = now
        self._log_event({"event": "coach", "key": key, "text": text})
        await self.notify({"type": "coach", "text": text})

    # ---- vision ---------------------------------------------------------------
    async def _vision_worker(self):
        while True:
            frame, seg, placement, jpeg, img = await self.queue.get()
            try:
                result = await vision.analyse(jpeg, seg["kind"])
                self.timings["vision"].append(result["_meta"]["latency_s"])
                self.vision_usage["calls"] += 1
                self.vision_usage["in_tokens"] += result["_meta"]["in_tokens"]
                self.vision_usage["out_tokens"] += result["_meta"]["out_tokens"]
                (self.dir / "frames" / f'{frame["idx"]:05d}.vision.json').write_text(json.dumps(result), encoding="utf-8")
                await self._ingest(frame, seg, placement, img, result)
            except Exception as exc:
                log.exception("Vision failed on frame %s", frame["idx"])
                self._log_event({"event": "vision_error", "frame": frame["idx"], "error": str(exc)[:200]})
                if self.now() - self.said.get("vision-error", -99) > 20:
                    self.said["vision-error"] = self.now()
                    await self.notify({"type": "warning", "text": "Frames are not being analysed: " + explain(exc)})
            finally:
                self.queue.task_done()

    async def _ingest(self, frame, seg, placement, img, result):
        h, w = img.shape[:2]
        corners = None
        sheet = result.get("reference_sheet") or {}
        if sheet.get("present") and len(sheet.get("box_2d") or []) == 4:
            corners = await asyncio.to_thread(scale.find_sheet, img, sheet["box_2d"])
        Hcm = scale.plane_homography(corners) if corners is not None else None
        frame["sheet_found"] = corners is not None

        blocked = result.get("view_blocked") or {}
        if blocked.get("blocked"):
            await self.coach("blocked", f'Something is blocking the view ({blocked.get("by") or "unknown"}). Ask them to move it or step around it and capture that part again.', 10)

        if seg["kind"] in vision.SPINE_KINDS:
            if Hcm is not None:
                self.chain_scale[(seg["label"], placement.chain)].append(sheet_to_shelf_scale(placement, corners, A4_AREA))
            spines = [s for s in result.get("spines", []) if len(s.get("box_2d") or []) == 4]
            for s in spines:
                d = {"segment": seg["label"], "chain": placement.chain, "frame": frame["idx"], "frame_ref": frame["ref"],
                     "t": frame["t"], "box_2d": s["box_2d"], "shelf_box": to_shelf(placement, s["box_2d"], w, h),
                     "orientation": s["orientation"], "legibility": s["legibility"], "colour": s.get("colour", ""),
                     "title_text": s["title_text"].strip(), "author_text": s["author_text"].strip(),
                     "publisher_text": s["publisher_text"].strip(), "height_cm": None, "thickness_cm": None}
                if Hcm is not None:
                    bw, bh = scale.measure_box(Hcm, s["box_2d"], w, h)
                    d["height_cm"], d["thickness_cm"] = (bh, bw) if s["orientation"] == "upright" else (bw, bh)
                self.spine_dets.append(d)
                if d["title_text"]:
                    self.last_book_key = d["title_text"]
            unreadable = sum(s["legibility"] == "unreadable" for s in spines)
            if len(spines) >= 4 and unreadable / len(spines) > 0.5:
                await self.coach(f'close-{seg["label"]}', f'Most spines on {self.where(seg)} are unreadable. Ask them to move the camera closer and pan more slowly.', 12)
            seg_frames = [f for f in self.frames if f["segment"] == seg["label"] and "sheet_found" in f]
            if len(seg_frames) >= 2 and not any(f["sheet_found"] for f in seg_frames):
                place = "upright against the books on this shelf" if seg["kind"] == "shelf" else "next to the books, facing the camera"
                await self.coach(f'sheet-{seg["label"]}', f'No A4 reference sheet seen on {self.where(seg)}. Ask them to place the A4 sheet {place}.', 30)

        for it in result.get("items", []):
            await self._add_item(frame, it, img, corners, Hcm)

        if seg["kind"] == "wall":
            wall = result.get("wall") or {}
            unit_boxes = [u for u in wall.get("shelving_units", []) if len(u.get("box_2d") or []) == 4]
            # Shelf counts seen in the wall shots let the agent notice shelves that were never swept.
            self.expected_shelves[seg["label"]] = sum(max(0, int(u.get("shelf_count") or 0)) for u in unit_boxes)
            size = scale.wall_size(Hcm, wall, w, h) if Hcm is not None and wall.get("fully_visible") else None
            if size and 1.0 < size[0] < 15 and 1.8 < size[1] < 6:
                units = [scale.measure_box(Hcm, u["box_2d"], w, h) for u in unit_boxes]
                self.walls[seg["label"]] = {"index": seg["index"], "label": seg["label"], "width_m": size[0], "height_m": size[1],
                                            "frame_ref": frame["ref"], "shelving_units_m2": sum(a * b for a, b in units) / 1e4,
                                            "shelving_units_cm": [[round(a), round(b)] for a, b in units],
                                            "note": wall.get("non_rectangular_note", "")}
                await self.coach(f'wall-ok-{seg["label"]}', f'Wall {seg["label"]} measured: {size[0]:.2f} m wide, {size[1]:.2f} m high. Tell them briefly and move to the next wall.', 999)
            else:
                why = "the A4 sheet" if Hcm is None else "all four corners of the wall"
                await self.coach(f'wall-retry-{seg["label"]}', f'I cannot see {why} in the shot of wall {seg["label"]}. Ask them to step back so the whole wall from floor to ceiling and corner to corner is in view, with the A4 sheet visible.', 8)

        await self.publish_inventory()

    async def _add_item(self, frame, it, img, corners, Hcm):
        if len(it.get("box_2d") or []) != 4:
            return
        h, w = img.shape[:2]
        dims = None
        if corners is not None:
            # Approximate: assumes the item is at roughly the sheet's distance from the camera.
            k = scale.cm_per_px(Hcm, corners)
            bx = scale.box_px(it["box_2d"], w, h)
            dims = {"w": round(float(bx[1][0] - bx[0][0]) * k), "h": round(float(bx[2][1] - bx[1][1]) * k), "d": None}
        desc = f'{it["brand_model"]} {it["description"]}'.strip()
        for existing in self.items:
            if existing["category"] == it["category"] and fuzz.token_set_ratio(existing["description_key"], desc.lower()) >= 75:
                existing["sightings"].append(frame["ref"])
                if dims and not existing["dimensions_cm"]:
                    existing["dimensions_cm"] = dims
                if it["brand_model"] and not existing["brand_model"]:
                    existing["brand_model"] = it["brand_model"]
                return
        item = {"id": f"item-{len(self.items) + 1:02d}", "category": it["category"], "description": it["description"],
                "material": it.get("material", ""), "brand_model": it.get("brand_model", ""),
                "search_term": it.get("search_term", ""), "frame_ref": frame["ref"],
                "frame_box_2d": it["box_2d"], "t": frame["t"], "dimensions_cm": dims, "sightings": [frame["ref"]],
                "description_key": desc.lower()}
        self.items.append(item)
        if item["category"] == "art":
            await self.coach(f'art-{item["id"]}', f'You can see {item["description"]} ({item["id"]}). Ask whether it is an original or a print, then record the answer with record_fact.', 999)

    # ---- merge / identify / measure ------------------------------------------
    def books(self) -> tuple[list[dict], list[str]]:
        clusters, warnings = merge.cluster(self.spine_dets)
        for seg in {c["segment"] for c in clusters}:
            seg_clusters = sorted([c for c in clusters if c["segment"] == seg], key=lambda c: (c["chain"], c["shelf_box"][0]))
            for pos, c in enumerate(seg_clusters, 1):
                c["position"] = pos
        books = []
        for c in clusters:
            r = c["reading"]
            key = f'{r["title_text"]}|{r["author_text"]}|{r["publisher_text"]}'
            books.append({"key": key, "shelf": c["segment"], "chain": c["chain"], "position": c["position"],
                          "frame_ref": r["frame_ref"], "frame_box_2d": r["box_2d"], "t": r["t"],
                          "reading": {k: r[k] for k in ("title_text", "author_text", "publisher_text", "legibility")},
                          "orientation": r["orientation"], "sightings": len(c["dets"]), "shelf_box": c["shelf_box"],
                          "direct_h": [d["height_cm"] for d in c["dets"] if d["height_cm"]],
                          "direct_t": [d["thickness_cm"] for d in c["dets"] if d["thickness_cm"]],
                          "ident": self.ids.get(key)})
        self._measure(books)
        return sorted(books, key=lambda b: (b["shelf"], b["position"])), warnings + self._duplicate_segments(books)

    @staticmethod
    def _duplicate_segments(books: list[dict]) -> list[str]:
        """Safety net: two segments that share 3+ spine readings were probably the same books
        filmed twice. Say so (inventory + review queue) instead of silently double counting."""
        texts: dict[str, set] = defaultdict(set)
        for b in books:
            t = identify.norm(b["reading"]["title_text"])
            if len(t) >= 4:
                texts[b["shelf"]].add(t)
        out, segs = [], sorted(texts)
        for i, a in enumerate(segs):
            for b in segs[i + 1:]:
                common = texts[a] & texts[b]
                if len(common) >= 3 and len(common) >= 0.5 * min(len(texts[a]), len(texts[b])):
                    out.append(f"{a} and {b} show {len(common)} of the same books: possibly filmed twice (double count); check before paying")
        return out

    def _measure(self, books: list[dict]):
        """Spine cm: direct homography if the sheet was in frame, else chain scale
        from the sheet, else known-height calibration, else standard-format prior."""
        def box_hw(b):
            x0, y0, x1, y1 = b["shelf_box"]
            return ((y1 - y0), (x1 - x0)) if b["orientation"] == "upright" else ((x1 - x0), (y1 - y0))

        scales: dict[tuple, tuple[float, str]] = {}
        for key, vals in self.chain_scale.items():
            if vals:
                scales[key] = (median(vals), "A4 sheet seen on this shelf")
        groups = defaultdict(list)
        for b in books:
            groups[(b["shelf"], b["chain"])].append(b)
        for key, group in groups.items():
            if key in scales:
                continue
            known = [b["ident"]["known_height_cm"] / box_hw(b)[0] for b in group
                     if b["ident"] and b["ident"].get("known_height_cm") and box_hw(b)[0] > 0]
            if len(known) >= 2:
                scales[key] = (median(known), f"calibrated from {len(known)} identified books with catalogue heights")
            else:
                heights = [box_hw(b)[0] for b in group if b["orientation"] == "upright" and box_hw(b)[0] > 0]
                if heights:
                    scales[key] = (STANDARD_HEIGHT_CM / median(heights), "standard-format prior (median spine = 19.8 cm B-format); low confidence")
        for b in books:
            if b["direct_h"]:
                b["spine_height_cm"] = round(median(b["direct_h"]), 1)
                b["spine_thickness_cm"] = round(median(b["direct_t"]), 1)
                b["scale_method"] = "A4 sheet in the same frame (plane homography)"
            elif (b["shelf"], b["chain"]) in scales:
                k, how = scales[(b["shelf"], b["chain"])]
                hh, ww = box_hw(b)
                b["spine_height_cm"], b["spine_thickness_cm"], b["scale_method"] = round(hh * k, 1), round(ww * k, 1), how
            else:
                b["spine_height_cm"] = b["spine_thickness_cm"] = None
                b["scale_method"] = "no scale available"

    async def identify_new(self, books: list[dict]):
        todo = [b for b in books if b["key"] not in self.ids]
        if not todo:
            return
        started = time.monotonic()
        for b in todo:
            try:
                self.ids[b["key"]] = await asyncio.to_thread(identify.identify, b["reading"])
            except Exception as exc:
                log.warning("Identify failed: %s", exc)
                self.ids[b["key"]] = {**identify.identify({"title_text": ""}), "review": f"catalogue lookup failed: {exc}"}
            b["ident"] = self.ids[b["key"]]
        self.timings["identify"].append(round(time.monotonic() - started, 2))

    async def publish_inventory(self):
        books, warnings = self.books()
        await self.identify_new(books)
        books, warnings = self.books()
        await self.notify({"type": "inventory", "books": [packet.book_preview(b, self.prices) for b in books],
                           "items": [packet.item_preview(i) for i in self.items],
                           "walls": list(self.walls.values()), "warnings": warnings,
                           "counts": {"books": len(books), "identified": sum(1 for b in books if b["ident"] and b["ident"]["status"] == "identified"),
                                      "items": len(self.items), "walls": len(self.walls)}})

    async def _pricing_loop(self):
        """Price identified books in the background during the sweep."""
        while not self.finished:
            await asyncio.sleep(15)
            try:
                await self.price_pending(minimum=config.PRICING_BATCH)
            except Exception:
                log.exception("Background pricing failed")

    async def price_pending(self, minimum: int = 1):
        books, _ = self.books()
        pending = []
        for b in books:
            ident = b["ident"]
            if ident and ident["status"] == "identified":
                k = f'{ident["title"]}|{ident["author"]}'
                if k not in self.prices and k not in [f'{p["title"]}|{p["author"]}' for p in pending]:
                    pending.append({"title": ident["title"], "author": ident["author"], "isbn": ident["isbn"]})
        if len(pending) < minimum:
            return
        started = time.monotonic()
        res = await pricing.price_books(pending, self.country, self.currency)
        for i, p in enumerate(pending):
            self.prices[f'{p["title"]}|{p["author"]}'] = res[i]
        self.timings["pricing_books"].append(round(time.monotonic() - started, 2))

    # ---- finish -----------------------------------------------------------------
    async def finish(self) -> dict:
        if self.result:
            return self.result
        self.ended_at = time.monotonic()
        self.finished = True
        self._log_event({"event": "end"})
        await self.notify({"type": "status", "text": f"Sweep ended. Finishing {self.queue.qsize()} queued frames…"})
        await self.queue.join()
        for w in self.workers:
            w.cancel()
        self.pricing_task.cancel()
        stage = time.monotonic()
        books, warnings = self.books()
        await self.identify_new(books)
        await self.notify({"type": "status", "text": "Pricing books and items…"})
        await self.price_pending()
        books, warnings = self.books()
        started = time.monotonic()
        item_prices = await pricing.price_items(
            [{"category": i["category"], "description": i["description"], "brand_model": i["brand_model"],
              "search_term": i.get("search_term", "")} for i in self.items],
            self.country, self.currency)
        self.timings["pricing_items"].append(round(time.monotonic() - started, 2))
        # Second locale: re-price the first 10 identified books to show locale is a setting.
        sample = []
        for b in books:
            ident = b["ident"]
            if ident and ident["status"] == "identified" and len(sample) < 10:
                sample.append({"id": "", "title": ident["title"], "author": ident["author"], "isbn": ident["isbn"]})
        started = time.monotonic()
        second = await pricing.price_books(sample, config.SECOND_COUNTRY, config.SECOND_CURRENCY) if sample else {}
        self.timings["pricing_second_locale"].append(round(time.monotonic() - started, 2))
        started = time.monotonic()
        room_result = room.compute(sorted(self.walls.values(), key=lambda w: w["index"]))
        self.timings["room"].append(round(time.monotonic() - started, 3))
        self.timings["post_sweep_to_packet"].append(round(time.monotonic() - stage, 2))
        result = packet.build(self, books, warnings, item_prices, sample, second, room_result)
        result["metrics"]["time_to_packet_s"] = round(time.monotonic() - self.ended_at, 1)
        (self.dir / "claim_packet.json").write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
        (self.dir / "report.html").write_text(report.render(result), encoding="utf-8")
        self.result = result
        return result

    # ---- customer-advised packet ---------------------------------------------
    def add_correction(self, target: str, title: str = "", author: str = "", replacement_cost=None,
                       used_value=None, comment: str = "") -> dict:
        """Record a correction exactly as the policyholder gave it. Nothing is changed in the
        verified packet; corrections only feed the separate customer-advised packet."""
        def amount(v):
            if v in (None, ""):
                return None
            digits = re.sub(r"[^\d.]", "", str(v))
            return float(digits) if digits else None

        target = (target or "").strip()
        if not target:
            return {"ok": False, "error": "Say which book or item: its id (like A1-02), its title, or 'new' for a book that was missed."}
        if self.result and target.lower() != "new" and not packet.find_line(self.result, target):
            known = [f'{b["id"]} {b["title"] or b["spine_text_read"]["title_text"] or "(unidentified)"}' for b in self.result["books"]][:40]
            return {"ok": False, "error": f'No book or item matches "{target}". Ask which one they mean.', "lines": known}
        c = {"target": target, "title": title.strip(), "author": author.strip(),
             "replacement_cost": amount(replacement_cost), "used_value": amount(used_value),
             "comment": comment.strip(), "t": self.now()}
        if not (c["title"] or c["author"] or c["replacement_cost"] is not None or c["used_value"] is not None or c["comment"]):
            return {"ok": False, "error": "Nothing to change: give a title, author, price or comment."}
        self.corrections.append(c)
        self._log_event({"event": "customer_correction", **c})
        return {"ok": True, "correction": c, "corrections_so_far": len(self.corrections)}

    def build_customer_packet(self) -> dict:
        if not self.result:
            raise ValueError("The verified claim packet is not ready yet; end the sweep first.")
        if not self.corrections:
            raise ValueError("No corrections from the policyholder yet.")
        p = packet.build_customer(self.result, self.corrections)
        p["customer_version"] = (self.customer_result or {}).get("customer_version", 0) + 1
        (self.dir / "customer_advised_claim_packet.json").write_text(json.dumps(p, indent=2, ensure_ascii=False), encoding="utf-8")
        (self.dir / "customer_advised_report.html").write_text(report.render(p), encoding="utf-8")
        self.customer_result = p
        self._log_event({"event": "customer_packet", "version": p["customer_version"], "corrections": len(self.corrections)})
        return p

    def _log_event(self, e: dict):
        with open(self.dir / "events.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps({"t": self.now(), **e}) + "\n")
