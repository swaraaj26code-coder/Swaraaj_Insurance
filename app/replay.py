"""Re-run the whole pipeline on a recorded sweep, offline.

    python -m app.replay data/sweeps/<id>

Frames, segment changes, locale and policyholder statements are replayed in
their original order. Vision and price lookups hit the disk cache, so a replay
after a code change costs nothing unless a prompt changed. Writes a new sweep
folder with its own packet, so before/after numbers can be compared."""

import asyncio
import json
import sys
from pathlib import Path

from .sweep import Sweep


async def replay(src: Path) -> Path:
    events = [json.loads(line) for line in (src / "events.jsonl").read_text(encoding="utf-8").splitlines()]
    frames = [json.loads(line) for line in (src / "frames.jsonl").read_text(encoding="utf-8").splitlines()]
    start = next(e for e in events if e["event"] == "start")

    async def quiet(_event):
        pass

    sweep = Sweep(quiet, country=start["country"], currency=start["currency"], device=f"replay of {src.name}")
    controls = [e for e in events if e["event"] in ("segment", "fact", "locale")]
    for fr in frames:
        while controls and controls[0]["t"] <= fr["t"]:
            e = controls.pop(0)
            if e["event"] == "segment":
                sweep.start_segment(e["kind"], e["label"])
            elif e["event"] == "fact":
                sweep.facts.append({k: e[k] for k in ("kind", "target", "note", "t")})
            else:
                sweep.set_locale(e["country"], e["currency"])
        await sweep.add_frame((src / fr["ref"]).read_bytes())
    for e in controls:
        if e["event"] == "fact":
            sweep.facts.append({k: e[k] for k in ("kind", "target", "note", "t")})
    result = await sweep.finish()
    print(json.dumps(result["totals"], indent=1))
    print(f"room: {result['room']['floor_area_m2']} m2 floor, {result['room']['wall_area_m2']} m2 walls")
    print(f"packet: {sweep.dir / 'claim_packet.json'}")
    return sweep.dir


if __name__ == "__main__":
    asyncio.run(replay(Path(sys.argv[1])))
