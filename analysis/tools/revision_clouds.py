"""Revision clouds (雲マーク): closed outlines of small arcs drawn around changed parts.

They are not circuit elements. Left in the page, their edges become wires that join every wire they cross.
"""

from __future__ import annotations

from typing import Any, NamedTuple

import cv2
import numpy as np
from PIL import Image

# Sizes at 150 dpi (1755 px wide pages); scaled by page width.
STRAIGHT_MIN = 41
THIN_MAX = 5
MIN_SIDE = 40
MIN_HOLE = 0.35
DIP_PROMINENCE = 1.5
MIN_DIPS = 8
BAND_RADIUS = 2
# Longer than the widest cloud stroke, so a wire crossing the cloud keeps reaching what it led to.
EXTEND = 20
# Longer than a cloud side. Only continuous ink is kept on it: a wire covered by a cloud edge.
UNDER_EDGE = 601
# Symbols up to this size keep their ink; larger boxes (cards, panels) may contain a whole cloud.
PROTECT_MAX_SIDE = 120


class Clouds(NamedTuple):
    regions: list[dict[str, Any]]
    erase: np.ndarray


def _run_through(ink: np.ndarray, orientation: str) -> np.ndarray:
    """Length of the ink run through each pixel, across a line of the given orientation."""
    a = (ink if orientation == "h" else ink.T).astype(np.int32)
    up, down = np.zeros_like(a), np.zeros_like(a)
    up[0], down[-1] = a[0], a[-1]
    for y in range(1, a.shape[0]):
        up[y] = (up[y - 1] + 1) * a[y]
    for y in range(a.shape[0] - 2, -1, -1):
        down[y] = (down[y + 1] + 1) * a[y]
    runs = np.where(a > 0, up + down - 1, 0)
    return runs if orientation == "h" else runs.T


def _kernel(orientation: str, length: int) -> np.ndarray:
    return cv2.getStructuringElement(cv2.MORPH_RECT, (length, 1) if orientation == "h" else (1, length))


def _thin_straight_lines(ink: np.ndarray, orientation: str, scale: float) -> np.ndarray:
    """Wires, frames and rules. A cloud side can contain long straight runs too, but its stroke is thicker."""
    thin_max = THIN_MAX * scale
    straight = cv2.morphologyEx(ink.astype(np.uint8), cv2.MORPH_OPEN, _kernel(orientation, max(3, round(STRAIGHT_MIN * scale)))) > 0
    count, labels = cv2.connectedComponents(straight.astype(np.uint8), connectivity=8)
    if count <= 1:
        return straight
    across = _run_through(ink, orientation)
    order = np.argsort(labels[straight], kind="stable")
    groups = np.split(across[straight][order], np.cumsum(np.bincount(labels[straight], minlength=count))[:-1])
    thin_line = np.array([len(g) > 0 and np.median(g) <= thin_max for g in groups])
    # Keep thick pixels so a wire crossing a cloud side does not cut the cloud outline.
    return thin_line[labels] & straight & (across <= thin_max)


def _hull_dips(contour: np.ndarray, prominence: float) -> int:
    """Dips of the outline below its convex hull; a cloud has one per cusp between two arcs."""
    hull = cv2.convexHull(contour)
    depth = np.array([cv2.pointPolygonTest(hull, (float(x), float(y)), True) for x, y in contour.reshape(-1, 2)])
    depth = np.roll(depth, -int(np.argmin(depth)))
    dips, low, peak = 0, depth[0], None
    for value in depth[1:]:
        if peak is None:
            if value >= low + prominence:
                peak = value
            low = min(low, value)
        elif value > peak:
            peak = value
        elif value <= peak - prominence:
            dips += 1
            peak, low = None, value
    return dips


def _wires_through(ink: np.ndarray, line: np.ndarray, band: np.ndarray, orientation: str, scale: float) -> np.ndarray:
    """Where lines that mostly run outside the clouds continue through a cloud stroke.

    A line crossing the stroke is extended past its end. A line covered by a cloud edge continues
    on the same row as one unbroken ink run up to the line piece on the other side.
    """
    count, labels = cv2.connectedComponents(line.astype(np.uint8), connectivity=8)
    inside = np.bincount(labels[line & band], minlength=count)
    total = np.maximum(np.bincount(labels[line], minlength=count), 1)
    outside = (inside / total < 0.5) & (np.arange(count) > 0)
    real = (outside[labels] & line).astype(np.uint8)
    crossing = cv2.dilate(real, _kernel(orientation, 2 * max(1, round(EXTEND * scale)) + 1)) > 0
    covered = cv2.morphologyEx(real, cv2.MORPH_CLOSE, _kernel(orientation, max(3, round(UNDER_EDGE * scale)))) > 0
    along = _run_through(ink, "v" if orientation == "h" else "h")
    return crossing | (covered & (along >= STRAIGHT_MIN * scale))


def detect_clouds(binary: np.ndarray) -> Clouds:
    """Clouds in a binary page (ink > 0) and the ink to erase for wiring: the cloud strokes, minus crossing wires."""
    ink = binary > 0
    scale = max(0.5, ink.shape[1] / 1755)
    lines = {o: _thin_straight_lines(ink, o, scale) for o in ("h", "v")}
    rest = (ink & ~lines["h"] & ~lines["v"]).astype(np.uint8)
    count, labels, stats, _ = cv2.connectedComponentsWithStats(rest, connectivity=8)
    min_side = MIN_SIDE * scale
    stroke = np.zeros(ink.shape, np.uint8)
    regions = []
    for index in range(1, count):
        x, y, w, h, _area = (int(v) for v in stats[index])
        if w < min_side or h < min_side:
            continue
        component = (labels[y:y + h, x:x + w] == index).astype(np.uint8)
        contours, _ = cv2.findContours(component, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
        outline = max(contours, key=len)
        filled = np.zeros_like(component)
        cv2.drawContours(filled, [outline], -1, 1, -1)
        if (filled.sum() - component.sum()) / (w * h) < MIN_HOLE:
            continue
        dips = _hull_dips(outline, DIP_PROMINENCE * scale)
        if dips < MIN_DIPS:
            continue
        stroke[y:y + h, x:x + w] |= component
        regions.append({
            "bbox": {"x0": x, "y0": y, "x1": x + w, "y1": y + h},
            "region_type": "cloud", "source": "image", "arc_count": dips,
        })
    if not regions:
        return Clouds([], np.zeros(ink.shape, bool))
    radius = max(1, round(BAND_RADIUS * scale))
    band = cv2.dilate(stroke, np.ones((2 * radius + 1, 2 * radius + 1), np.uint8)) > 0
    keep = _wires_through(ink, lines["h"], band, "h", scale) | _wires_through(ink, lines["v"], band, "v", scale)
    return Clouds(regions, band & ink & ~keep)


def erase_clouds(image: Image.Image, clouds: Clouds, symbol_boxes: list[dict[str, float]]) -> Image.Image:
    """The page with cloud strokes painted white, for wiring. Ink inside symbol boxes stays."""
    if not clouds.regions:
        return image
    erase = clouds.erase.copy()
    scale = max(0.5, image.width / 1755)
    for box in symbol_boxes:
        x0, y0, x1, y1 = (int(round(box[k])) for k in ("x0", "y0", "x1", "y1"))
        if max(x1 - x0, y1 - y0) <= PROTECT_MAX_SIDE * scale:
            erase[max(0, y0):max(0, y1), max(0, x0):max(0, x1)] = False
    pixels = np.array(image)
    pixels[erase] = 255
    return Image.fromarray(pixels)

