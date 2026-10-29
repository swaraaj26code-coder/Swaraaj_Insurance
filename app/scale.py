"""Metric scale from a plain A4 sheet (29.7 x 21.0 cm).

The vision model only says roughly where the sheet is. OpenCV then finds the
sheet's four corners precisely, and those corners give a homography from image
pixels to centimetres on the sheet's plane. Anything lying in that plane
(spines on the same shelf, the wall the sheet is taped to) can then be measured
in cm, even when the camera is not square-on."""

import cv2
import numpy as np

from . import config


def _order(pts: np.ndarray) -> np.ndarray:
    """tl, tr, br, bl."""
    s, d = pts.sum(1), np.diff(pts, axis=1).ravel()
    return np.float32([pts[s.argmin()], pts[d.argmin()], pts[s.argmax()], pts[d.argmax()]])


def find_sheet(img: np.ndarray, box_norm) -> np.ndarray | None:
    """Precise corners (image px) of the A4 sheet inside a rough box, or None."""
    h, w = img.shape[:2]
    ymin, xmin, ymax, xmax = box_norm
    pad = 0.15
    bw, bh = (xmax - xmin) * w / 1000, (ymax - ymin) * h / 1000
    x0 = int(max(0, xmin * w / 1000 - pad * bw)); x1 = int(min(w, xmax * w / 1000 + pad * bw))
    y0 = int(max(0, ymin * h / 1000 - pad * bh)); y1 = int(min(h, ymax * h / 1000 + pad * bh))
    crop = cv2.cvtColor(img[y0:y1, x0:x1], cv2.COLOR_BGR2GRAY)
    if crop.size == 0:
        return None
    _, mask = cv2.threshold(cv2.GaussianBlur(crop, (5, 5), 0), 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    best = None
    for c in sorted(contours, key=cv2.contourArea, reverse=True)[:3]:
        if cv2.contourArea(c) < 0.25 * crop.size:
            break
        approx = cv2.approxPolyDP(c, 0.02 * cv2.arcLength(c, True), True)
        if len(approx) == 4 and cv2.isContourConvex(approx):
            best = approx.reshape(4, 2).astype(np.float32)
            break
    if best is None:
        return None
    best = cv2.cornerSubPix(crop, best.copy(), (5, 5), (-1, -1),
                            (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 30, 0.01))
    corners = _order(best + np.float32([x0, y0]))
    # Reject shapes that are nowhere near A4 proportions (1.414) after perspective.
    sides = [np.linalg.norm(corners[i] - corners[(i + 1) % 4]) for i in range(4)]
    long_, short_ = max(sides[0] + sides[2], sides[1] + sides[3]), min(sides[0] + sides[2], sides[1] + sides[3])
    if not 1.0 < long_ / max(short_, 1) < 2.2:
        return None
    return corners


def plane_homography(corners: np.ndarray) -> np.ndarray:
    """Homography: image px -> cm on the sheet's plane."""
    tl, tr, br, bl = corners
    horiz = np.linalg.norm(tr - tl) + np.linalg.norm(br - bl)
    vert = np.linalg.norm(bl - tl) + np.linalg.norm(br - tr)
    W, H = (config.REF_LONG_CM, config.REF_SHORT_CM) if horiz >= vert else (config.REF_SHORT_CM, config.REF_LONG_CM)
    dst = np.float32([[0, 0], [W, 0], [W, H], [0, H]])
    return cv2.getPerspectiveTransform(corners, dst)


def to_plane(Hcm: np.ndarray, pts_px: np.ndarray) -> np.ndarray:
    return cv2.perspectiveTransform(np.float32(pts_px).reshape(-1, 1, 2), Hcm).reshape(-1, 2)


def box_px(box_norm, w: int, h: int) -> np.ndarray:
    ymin, xmin, ymax, xmax = box_norm
    return np.float32([[xmin * w / 1000, ymin * h / 1000], [xmax * w / 1000, ymin * h / 1000],
                       [xmax * w / 1000, ymax * h / 1000], [xmin * w / 1000, ymax * h / 1000]])


def measure_box(Hcm: np.ndarray, box_norm, w: int, h: int) -> tuple[float, float]:
    """(width_cm, height_cm) of an image box lying in the sheet's plane."""
    tl, tr, br, bl = to_plane(Hcm, box_px(box_norm, w, h))
    width = (np.linalg.norm(tr - tl) + np.linalg.norm(br - bl)) / 2
    height = (np.linalg.norm(bl - tl) + np.linalg.norm(br - tr)) / 2
    return float(width), float(height)


def cm_per_px(Hcm: np.ndarray, corners: np.ndarray) -> float:
    """Average scale at the sheet, for things roughly at the sheet's depth."""
    area_px = cv2.contourArea(corners.reshape(-1, 1, 2))
    return float(np.sqrt(config.REF_LONG_CM * config.REF_SHORT_CM / max(area_px, 1)))


def wall_size(Hcm: np.ndarray, points_norm: dict, w: int, h: int) -> tuple[float, float] | None:
    """Wall width/height (m) from its four corners (floor-left, floor-right,
    ceiling-left, ceiling-right) mapped onto the wall plane the sheet is taped to."""
    keys = ["ceiling_left", "ceiling_right", "floor_right", "floor_left"]
    if not all(k in points_norm and points_norm[k] for k in keys):
        return None
    pts = np.float32([[points_norm[k][1] * w / 1000, points_norm[k][0] * h / 1000] for k in keys])
    tl, tr, br, bl = to_plane(Hcm, pts)
    width = (np.linalg.norm(tr - tl) + np.linalg.norm(br - bl)) / 2 / 100
    height = (np.linalg.norm(bl - tl) + np.linalg.norm(br - tr)) / 2 / 100
    return float(width), float(height)
