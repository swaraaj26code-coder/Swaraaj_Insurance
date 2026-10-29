"""Stage 5b: room dimensions from the wall shots taken during the sweep.

Each wall shot has the A4 sheet taped to that wall, so the wall's corners can be
mapped onto the wall plane in metres (scale.wall_size). Walls are captured in
order around the room, so walls 1/3 give the length and 2/4 the width.
Assumption (stated in the packet): rectangular room, gross wall area with door
and window openings NOT subtracted."""

from statistics import mean, median

M2_TO_FT2 = 10.7639


def compute(walls: list[dict]) -> dict:
    """walls: [{index, width_m, height_m, frame_ref, shelving_units_m2, note}] (measured ones only)."""
    empty = {"length_m": None, "width_m": None, "height_m": None, "floor_area_m2": None, "floor_area_ft2": None,
             "wall_area_m2": None, "wall_area_ft2": None, "shelved_wall_area_m2": None, "shelved_wall_area_ft2": None,
             "scale_method": "A4 reference sheet taped to each wall; wall-plane homography from its four corners",
             "shape_handling": "", "confidence": 0.0, "walls_measured": len(walls), "frame_refs": [], "notes": []}
    if not walls:
        empty["notes"].append("no wall was captured with the reference sheet and all four corners visible")
        return empty
    odd = [w["width_m"] for w in walls if w["index"] % 2 == 1]
    even = [w["width_m"] for w in walls if w["index"] % 2 == 0]
    notes = [w["note"] for w in walls if w.get("note")]
    if not odd or not even:
        empty.update(height_m=round(median(w["height_m"] for w in walls), 2), frame_refs=[w["frame_ref"] for w in walls],
                     notes=["only one wall direction measured; length and width unknown"] + notes, confidence=0.2)
        return empty
    length, width = mean(odd), mean(even)
    height = median(w["height_m"] for w in walls)
    floor = length * width
    wall_area = 2 * (length + width) * height
    shelved = sum(w.get("shelving_units_m2", 0) for w in walls)
    # Confidence: more walls and agreement between opposite walls raise it.
    spread = max((max(g) - min(g)) / mean(g) for g in (odd, even) if len(g) > 1) if len(walls) > 2 else None
    confidence = 0.55 + 0.1 * min(len(walls) - 2, 2) - (min(spread, 0.3) if spread else 0.1)
    return {**empty,
            "length_m": round(length, 2), "width_m": round(width, 2), "height_m": round(height, 2),
            "floor_area_m2": round(floor, 2), "floor_area_ft2": round(floor * M2_TO_FT2, 1),
            "wall_area_m2": round(wall_area, 2), "wall_area_ft2": round(wall_area * M2_TO_FT2, 1),
            "shelved_wall_area_m2": round(shelved, 2), "shelved_wall_area_ft2": round(shelved * M2_TO_FT2, 1),
            "shape_handling": "treated as a rectangle: length = mean of walls 1/3, width = mean of walls 2/4"
                              + ("; non-rectangular features reported: " + "; ".join(notes) if notes else ""),
            "confidence": round(max(0.1, min(confidence, 0.9)), 2), "frame_refs": [w["frame_ref"] for w in walls],
            "notes": ["gross wall area: door and window openings not subtracted"]
                     + ([f"opposite walls disagree by {spread:.0%}"] if spread and spread > 0.08 else [])}
