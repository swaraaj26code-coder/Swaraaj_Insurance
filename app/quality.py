"""Cheap per-frame capture checks that run on every uploaded frame.

These decide what the live agent says ("slow down", "glare") and which frames
are allowed into the expensive vision stage."""

import cv2
import numpy as np

from . import config


def decode(jpeg: bytes) -> np.ndarray:
    return cv2.imdecode(np.frombuffer(jpeg, np.uint8), cv2.IMREAD_COLOR)


def _glare(grey: np.ndarray) -> float:
    """Share of blown-out pixels, not counting quadrilateral bright patches:
    the white A4 reference sheet is meant to be bright and must not count as glare."""
    mask = (grey >= 250).astype(np.uint8)
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    for c in contours:
        if cv2.contourArea(c) > 0.003 * grey.size:
            approx = cv2.approxPolyDP(c, 0.03 * cv2.arcLength(c, True), True)
            if len(approx) == 4:
                cv2.drawContours(mask, [c], -1, 0, -1)
    return float(mask.mean())


def assess(img: np.ndarray) -> dict:
    grey = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    # Downscale so the blur score does not depend on upload resolution.
    small = cv2.resize(grey, (640, int(640 * grey.shape[0] / grey.shape[1])))
    blur = float(cv2.Laplacian(small, cv2.CV_64F).var())
    glare = _glare(small)
    brightness = float(small.mean())
    problems = []
    if blur < config.BLUR_MIN:
        problems.append("blurry")
    if glare > config.GLARE_MAX:
        problems.append("glare")
    if brightness < config.DARK_MAX:
        problems.append("dark")
    return {"blur": round(blur, 1), "glare": round(glare, 4), "brightness": round(brightness, 1),
            "problems": problems, "ok": not problems}
