"""Follows the camera along one shelf with ORB feature matching.

Every frame gets a transform into "shelf coordinates" (the first frame of the
chain, in 640-px-wide work units). Two uses:
  * keyframe selection: only frames that show new shelf territory are sent to
    the vision model, which keeps us inside free-tier rate limits;
  * de-duplication: a spine seen in five frames lands at the same shelf x.
If matching fails the chain breaks and a new one starts; the caller tells the
user to pan back so the overlap can be re-established."""

from dataclasses import dataclass, field

import cv2
import numpy as np

WORK_W = 640
MIN_INLIERS = 25
NEW_TERRITORY = 0.45  # fraction of frame width not yet covered by a keyframe


@dataclass
class Placement:
    chain: int
    H: np.ndarray            # 3x3, work coords of this frame -> shelf coords
    keyframe: bool
    lost: bool               # tracking broke on this frame
    scale: float             # work px per original px


@dataclass
class ShelfTracker:
    orb: cv2.ORB = field(default_factory=lambda: cv2.ORB_create(1500))
    matcher: cv2.BFMatcher = field(default_factory=lambda: cv2.BFMatcher(cv2.NORM_HAMMING, crossCheck=True))
    chain: int = -1
    prev: tuple | None = None          # (keypoints, descriptors, H)
    covered: list = field(default_factory=list)  # [(chain, x0, x1)] in shelf coords

    def _features(self, img):
        grey = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        s = WORK_W / grey.shape[1]
        grey = cv2.resize(grey, (WORK_W, int(grey.shape[0] * s)))
        kp, des = self.orb.detectAndCompute(grey, None)
        return kp, des, s, grey.shape

    def add(self, img, usable: bool = True) -> Placement:
        """usable=False (blurry/glare) still tracks motion but never becomes a keyframe."""
        kp, des, s, (h, w) = self._features(img)
        lost = False
        H = None
        if self.prev is not None and des is not None and self.prev[1] is not None:
            matches = self.matcher.match(des, self.prev[1])
            if len(matches) >= MIN_INLIERS:
                src = np.float32([kp[m.queryIdx].pt for m in matches])
                dst = np.float32([self.prev[0][m.trainIdx].pt for m in matches])
                A, inliers = cv2.estimateAffinePartial2D(src, dst, method=cv2.RANSAC, ransacReprojThreshold=4)
                if A is not None and int(inliers.sum()) >= MIN_INLIERS:
                    H = self.prev[2] @ np.vstack([A, [0, 0, 1]])
        if H is None:
            lost = self.prev is not None
            self.chain += 1
            H = np.eye(3)
        self.prev = (kp, des, H)

        # How much of this frame's horizontal span is not yet covered by a keyframe?
        corners = np.array([[0, 0, 1], [w, 0, 1], [w, h, 1], [0, h, 1]], float).T
        xs = (H @ corners)[0]
        x0, x1 = float(xs.min()), float(xs.max())
        seen = 0.0
        for c, a, b in self.covered:
            if c == self.chain:
                seen += max(0.0, min(b, x1) - max(a, x0))
        keyframe = usable and (x1 - x0) > 0 and (1 - seen / (x1 - x0)) >= NEW_TERRITORY
        if keyframe:
            self.covered.append((self.chain, x0, x1))
        return Placement(self.chain, H, keyframe, lost, s)


def to_shelf(placement: Placement, box_norm, img_w: int, img_h: int) -> tuple[float, float, float, float]:
    """Map a vision box [ymin, xmin, ymax, xmax] (0-1000) to a shelf-coordinate box (x0, y0, x1, y1)."""
    ymin, xmin, ymax, xmax = box_norm
    s = placement.scale
    fx, fy = img_w / 1000 * s, img_h / 1000 * s
    pts = np.array([[xmin * fx, ymin * fy, 1], [xmax * fx, ymin * fy, 1],
                    [xmax * fx, ymax * fy, 1], [xmin * fx, ymax * fy, 1]]).T
    xy = placement.H @ pts
    return float(xy[0].min()), float(xy[1].min()), float(xy[0].max()), float(xy[1].max())


def sheet_to_shelf_scale(placement: Placement, corners_px: np.ndarray, area_cm2: float) -> float:
    """cm per shelf-coordinate unit, from the A4 sheet's corners seen in this frame."""
    pts = np.hstack([corners_px * placement.scale, np.ones((4, 1))]).T
    xy = (placement.H @ pts)[:2].T.astype(np.float32)
    area = cv2.contourArea(xy.reshape(-1, 1, 2))
    return float(np.sqrt(area_cm2 / area)) if area > 0 else 0.0
