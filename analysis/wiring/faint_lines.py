"""Faint wires: hairlines that a 150 dpi scan leaves as broken light grey strokes (DD-0007 §4.22).

The binary mask used for wiring drops them, so a contact and the connector below it stay unconnected.
After the wiring is built, two terminals are joined when they are the nearest terminals to each other
along a vertical or horizontal line, are not connected yet, and a light grey line runs between them.
The page and the wires found so far are left as they are.
"""

from __future__ import annotations

from typing import Any

import numpy as np
from PIL import Image

from analysis.wiring import classes

# Sizes at 150 dpi (1755 px wide pages); scaled by page width.
GREY_MAX = 245          # ink of any tone (the scan background is 255)
DARK_MAX = 128          # a dark line between unconnected terminals was left out on purpose
ALIGN = 8               # terminals on one wire share x (vertical) or y (horizontal) within this
MIN_GAP = 15
MAX_GAP = 450
END_SKIP = 8            # the terminal circles and connector dots themselves
HALF_WIDTH = 3
MIN_GREY_SHARE = 0.3
MAX_DARK_SHARE = 0.35
MAX_WHITE_RUN = 24      # longest dropout along the line
PASS_CLASSES = classes.PASS_THROUGH
# Devices are passed through only between their two terminals. A connector box holds its labels beside the
# dot, so a horizontal line leaving the dot through its own box runs over text.
DEVICE_CLASSES = classes.TWO_TERMINAL_DEVICES
END_CLASSES = classes.TWO_TERMINAL_DEVICES | classes.PASS_THROUGH


def _class(symbol: dict[str, Any]) -> str:
    return symbol.get("class_name") or symbol.get("type") or ""


def _profile(gray: np.ndarray, a: tuple[float, float], b: tuple[float, float], vertical: bool, scale: float) -> np.ndarray:
    """Darkest pixel across the line at each position between the two terminals."""
    # Terminals placed by a template may sit a few pixels off a faint line; widen the band to cover both.
    offset = abs(a[0] - b[0]) if vertical else abs(a[1] - b[1])
    skip, half = round(END_SKIP * scale), max(1, round(HALF_WIDTH * scale + offset / 2))
    if vertical:
        x = round((a[0] + b[0]) / 2)
        y0, y1 = sorted((round(a[1]), round(b[1])))
        band = gray[y0 + skip:y1 - skip, max(0, x - half):x + half + 1]
        return band.min(axis=1) if band.size else np.array([255])
    y = round((a[1] + b[1]) / 2)
    x0, x1 = sorted((round(a[0]), round(b[0])))
    band = gray[max(0, y - half):y + half + 1, x0 + skip:x1 - skip]
    return band.min(axis=0) if band.size else np.array([255])


def _longest(mask: np.ndarray) -> int:
    best = run = 0
    for value in mask:
        run = run + 1 if value else 0
        best = max(best, run)
    return best


def _faint_line(core: np.ndarray, scale: float) -> bool:
    return (
        core.size > 0
        and float((core <= GREY_MAX).mean()) >= MIN_GREY_SHARE
        and float((core <= DARK_MAX).mean()) <= MAX_DARK_SHARE
        and _longest(core > GREY_MAX) <= MAX_WHITE_RUN * scale
    )


def _leaves_device(symbol: dict[str, Any], term: dict[str, Any], toward: tuple[float, float]) -> bool:
    """A wire leaves a contact or coil away from its other terminal; toward it, the line runs through the device.

    A connector holds its labels beside the dot, so a horizontal line from the dot runs over text.
    """
    cls = _class(symbol)
    if cls == "connector":
        return toward[0] == 0
    if cls not in DEVICE_CLASSES:
        return True
    others = [t["point"] for t in symbol.get("terminals") or [] if t is not term and t.get("point")]
    if not others:
        return True
    ox = sum(p[0] for p in others) / len(others)
    oy = sum(p[1] for p in others) / len(others)
    vx, vy = term["point"][0] - ox, term["point"][1] - oy
    norm = (vx * vx + vy * vy) ** 0.5
    return norm > 0 and (vx * toward[0] + vy * toward[1]) >= 0.7 * norm


def bridge_faint_wires(payload: dict[str, Any], image: Image.Image) -> list[dict[str, Any]]:
    """Add faint-line connections to payload["from_to"] and return them."""
    gray = np.asarray(image.convert("L"))
    scale = max(0.5, image.width / 1755)
    symbols = {s["id"]: s for s in payload.get("symbols", []) if _class(s) in END_CLASSES}
    ends = [
        (sid, t, float(t["point"][0]), float(t["point"][1]))
        for sid, s in symbols.items() for t in s.get("terminals") or [] if t.get("point")
    ]
    parent: dict[tuple[str, str], tuple[str, str]] = {}

    def find(key: tuple[str, str]) -> tuple[str, str]:
        parent.setdefault(key, key)
        while parent[key] != key:
            parent[key] = parent[parent[key]]
            key = parent[key]
        return key

    from_to = payload.get("from_to") or {}
    for conn in from_to.get("connections", []):
        a = (conn["from"]["symbol_id"], conn["from"]["terminal_ref"])
        b = (conn["to"]["symbol_id"], conn["to"]["terminal_ref"])
        if conn.get("type") == "wire" or (a[0] == b[0] and _class(symbols.get(a[0], {})) in PASS_CLASSES):
            parent[find(a)] = find(b)

    boxes = {sid: s["bbox"] for sid, s in symbols.items()}
    align, min_gap, max_gap = ALIGN * scale, MIN_GAP * scale, MAX_GAP * scale
    added: list[dict[str, Any]] = []
    for vertical in (True, False):
        axis, along = (0, 1) if vertical else (1, 0)
        near, far = ("x0", "x1") if vertical else ("y0", "y1")
        start, end = ("y0", "y1") if vertical else ("x0", "x1")
        for sid, term, x, y in ends:
            here = (x, y)
            ahead = [
                e for e in ends
                if abs((e[2], e[3])[axis] - here[axis]) <= align and (e[2], e[3])[along] - here[along] >= min_gap
            ]
            if not ahead:
                continue
            other_sid, other, ox, oy = min(ahead, key=lambda e: (e[2], e[3])[along])
            # The nearest terminal ahead belongs to the same symbol: the line runs through the symbol.
            if other_sid == sid or (ox, oy)[along] - here[along] > max_gap:
                continue
            a, b = (sid, term["ref"]), (other_sid, other["ref"])
            if find(a) == find(b):
                continue
            lo, hi = here[along], (ox, oy)[along]
            crossed = any(
                k not in (sid, other_sid)
                and box[near] <= here[axis] <= box[far] and box[start] < hi and box[end] > lo
                for k, box in boxes.items()
            )
            step = (0.0, 1.0) if vertical else (1.0, 0.0)
            if crossed or not _leaves_device(symbols[sid], term, step)                     or not _leaves_device(symbols[other_sid], other, (-step[0], -step[1])):
                continue
            if not _faint_line(_profile(gray, here, (ox, oy), vertical, scale), scale):
                continue
            parent[find(a)] = find(b)
            ends_of = [(sid, symbols[sid], term), (other_sid, symbols[other_sid], other)]
            added.append({
                "id": f"from_to_faint_{len(added) + 1:04d}", "type": "wire", "directed": False,
                **{
                    side: {
                        "id": f"{s_id}:{t['ref']}", "symbol_id": s_id, "class_name": _class(s), "label": s.get("label"),
                        "terminal_ref": t["ref"], "terminal_name": t.get("name"), "point": t["point"],
                    }
                    for side, (s_id, s, t) in zip(("from", "to"), ends_of, strict=True)
                },
                "basis": "faint_line_between_aligned_terminals", "status": "predicted",
            })
    if added:
        from_to.setdefault("connections", []).extend(added)
        from_to["connection_count"] = len(from_to["connections"])
    return added
