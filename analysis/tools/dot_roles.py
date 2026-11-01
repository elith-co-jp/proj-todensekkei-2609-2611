"""Roles of dot-shaped detections: wire junction, terminal, or needs review.

A junction dot joins three or more wire arms. A terminal dot sits on a wire that passes
straight through it, with terminal text on both sides. Anything else needs a person to look.
The detector box only points at the dot; the role is read from the ink, and two boxes on
the same dot collapse to one.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

import cv2
import numpy as np
from PIL import Image

from analyze_pdf_structure import make_binary_mask

DOT_CLASSES = ("junction", "connector")
DIRECTIONS = {"up": (0, -1), "down": (0, 1), "left": (-1, 0), "right": (1, 0)}
# False leaves symbols untouched and the structure payload without dot roles.
ENABLED = True


@dataclass(frozen=True)
class DotRules:
    open_px: int = 5
    min_size_px: int = 4
    max_size_px: int = 30
    arm_px: int = 14
    arm_min_ink: float = 0.7
    junction_search_px: float = 8.0
    same_dot_px: float = 6.0
    side_text_px: int = 60
    side_text_half_height_px: int = 14
    side_text_min_ink: float = 0.02


@dataclass
class Dot:
    x: float
    y: float
    size: int
    arms: dict[str, bool] = field(default_factory=dict)

    @property
    def degree(self) -> int:
        return sum(self.arms.values())

    def as_dict(self) -> dict[str, Any]:
        return {"center": [round(self.x, 1), round(self.y, 1)], "size": self.size,
                "arms": sorted(k for k, v in self.arms.items() if v), "degree": self.degree}


def find_dots(binary: np.ndarray, rules: DotRules) -> list[Dot]:
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (rules.open_px, rules.open_px))
    opened = cv2.morphologyEx(binary, cv2.MORPH_OPEN, kernel)
    count, _labels, stats, centroids = cv2.connectedComponentsWithStats(opened, connectivity=8)
    dots = []
    for index in range(1, count):
        _x, _y, width, height, area = stats[index]
        if not (rules.min_size_px <= width <= rules.max_size_px and rules.min_size_px <= height <= rules.max_size_px):
            continue
        if not 0.6 <= width / height <= 1.6 or area < 0.5 * width * height:
            continue
        dot = Dot(float(centroids[index][0]), float(centroids[index][1]), int(max(width, height)))
        dot.arms = wire_arms(binary, dot, rules)
        dots.append(dot)
    return dots


def wire_arms(binary: np.ndarray, dot: Dot, rules: DotRules) -> dict[str, bool]:
    """An arm is a line leaving the dot: mostly inked for arm_px pixels past the dot edge.

    Drawings often leave a small break beside a terminal dot, so short gaps are allowed.
    """
    height, width = binary.shape
    start = dot.size // 2 + 2
    arms = {}
    for name, (dx, dy) in DIRECTIONS.items():
        covered = 0
        for step in range(start, start + rules.arm_px):
            x, y = round(dot.x + dx * step), round(dot.y + dy * step)
            if not (0 <= x < width and 0 <= y < height):
                break
            x0, x1 = (x - 1, x + 2) if dx == 0 else (x, x + 1)
            y0, y1 = (y - 1, y + 2) if dy == 0 else (y, y + 1)
            covered += bool(binary[max(0, y0):y1, max(0, x0):x1].any())
        arms[name] = covered >= rules.arm_min_ink * rules.arm_px
    return arms


def side_text(binary: np.ndarray, dot: Dot, vertical_wire: bool, rules: DotRules) -> bool:
    """Ink on both sides of the wire, away from it: terminal numbers and names.

    A wire crossing through the dot is masked out so a crossing does not read as text.
    """
    mask = (binary > 0).copy()
    height, width = mask.shape
    x, y = round(dot.x), round(dot.y)
    band = 3
    if vertical_wire:
        mask[max(0, y - band):y + band + 1, :] = False
    else:
        mask[:, max(0, x - band):x + band + 1] = False
    gap = dot.size // 2 + 4
    reach, half = rules.side_text_px, rules.side_text_half_height_px
    if vertical_wire:
        windows = [(y - half, y + half, x - gap - reach, x - gap), (y - half, y + half, x + gap, x + gap + reach)]
    else:
        windows = [(y - gap - reach, y - gap, x - half, x + half), (y + gap, y + gap + reach, x - half, x + half)]
    shares = []
    for y0, y1, x0, x1 in windows:
        patch = mask[max(0, y0):min(height, y1), max(0, x0):min(width, x1)]
        shares.append(float(patch.mean()) if patch.size else 0.0)
    return min(shares) >= rules.side_text_min_ink


def role_of(binary: np.ndarray, dot: Dot, rules: DotRules) -> str:
    vertical = dot.arms.get("up") or dot.arms.get("down")
    horizontal = dot.arms.get("left") or dot.arms.get("right")
    if vertical and side_text(binary, dot, True, rules):
        return "terminal"
    if horizontal and not vertical and side_text(binary, dot, False, rules):
        return "terminal"
    if dot.degree >= 3:
        return "junction"
    return "review"


def _dot_for(symbol: dict[str, Any], dots: list[Dot], rules: DotRules) -> Dot | None:
    box = symbol["bbox"]
    cx, cy = (box["x0"] + box["x1"]) / 2, (box["y0"] + box["y1"]) / 2
    if symbol["class_name"] == "junction":
        near = [d for d in dots if abs(d.x - cx) <= rules.junction_search_px and abs(d.y - cy) <= rules.junction_search_px]
    else:
        near = [d for d in dots if box["x0"] - 2 <= d.x <= box["x1"] + 2 and box["y0"] - 2 <= d.y <= box["y1"] + 2 and d.degree >= 1]
    return min(near, key=lambda d: (d.x - cx) ** 2 + (d.y - cy) ** 2) if near else None


def assign_dot_roles(binary: np.ndarray, symbols: list[dict[str, Any]], rules: DotRules = DotRules()) -> dict[str, Any]:
    """Set symbol["dot_role"] on junction and connector detections, in place.

    Symbols keep their list positions, so callers pairing them with detections by index stay
    aligned; a second box on an already claimed dot is marked suppressed instead of removed.
    """
    dots = find_dots(binary, rules)
    roles: dict[str, int] = {}
    duplicates = 0
    owners: list[tuple[Dot, dict[str, Any]]] = []
    for symbol in symbols:
        if symbol.get("class_name") not in DOT_CLASSES:
            continue
        dot = _dot_for(symbol, dots, rules)
        role = role_of(binary, dot, rules) if dot else "review"
        symbol["dot_role"] = {"role": role, "dot": dot.as_dict() if dot else None, "suppressed": False}
        roles[role] = roles.get(role, 0) + 1
        if dot is None:
            continue
        index = next((i for i, (d, _s) in enumerate(owners) if abs(d.x - dot.x) <= rules.same_dot_px and abs(d.y - dot.y) <= rules.same_dot_px), None)
        if index is None:
            owners.append((dot, symbol))
            continue
        kept = owners[index][1]
        if (symbol.get("confidence") or 0) > (kept.get("confidence") or 0):
            kept, symbol = symbol, kept
            owners[index] = (dot, kept)
        symbol["dot_role"].update(suppressed=True, duplicate_of=kept["id"])
        duplicates += 1
    return {"rules": asdict(rules), "roles": roles, "duplicates": duplicates, "dots_found": len(dots)}


def annotate_dots(image: Image.Image, symbols: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not ENABLED:
        return None
    return assign_dot_roles(make_binary_mask(image), symbols)

