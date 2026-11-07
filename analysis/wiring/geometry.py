"""点・枠・線分の基本計算（配線解析の各段で共通に使う）。"""

from __future__ import annotations

from typing import Any


def merged_span_length(spans: list[tuple[int, int]]) -> int:
    if not spans:
        return 0
    ordered = sorted(spans)
    merged = [ordered[0]]
    for start, end in ordered[1:]:
        if start > merged[-1][1]:
            merged.append((start, end))
        else:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
    return sum(end - start for start, end in merged)


def wire_points(wire: dict[str, Any]) -> tuple[tuple[int, int], tuple[int, int]]:
    if "polyline" in wire:
        start, end = wire["polyline"][0], wire["polyline"][-1]
        return (round(start[0]), round(start[1])), (round(end[0]), round(end[1]))
    if wire["orientation"] == "h":
        return (int(wire["span_start"]), int(wire["axis"])), (int(wire["span_end"]), int(wire["axis"]))
    return (int(wire["axis"]), int(wire["span_start"])), (int(wire["axis"]), int(wire["span_end"]))


def clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


def wire_span(wire: dict[str, Any]) -> tuple[float, float, float, float]:
    start, end = wire_points(wire)
    return float(start[0]), float(start[1]), float(end[0]), float(end[1])


def wire_axis_and_range(wire: dict[str, Any]) -> tuple[str, float, float, float]:
    x0, y0, x1, y1 = wire_span(wire)
    orientation = str(wire.get("orientation") or "")
    if orientation == "h" or abs(y0 - y1) <= abs(x0 - x1):
        return "h", (y0 + y1) / 2, min(x0, x1), max(x0, x1)
    return "v", (x0 + x1) / 2, min(y0, y1), max(y0, y1)


def box_center(box: dict[str, int | float]) -> tuple[float, float]:
    return ((float(box["x0"]) + float(box["x1"])) / 2, (float(box["y0"]) + float(box["y1"])) / 2)


def point_distance(a: tuple[float, float], b: tuple[float, float]) -> float:
    return ((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2) ** 0.5


def padded_box(box: dict[str, int | float], padding: float, width: int, height: int) -> dict[str, float]:
    return {
        "x0": max(0.0, float(box["x0"]) - padding),
        "y0": max(0.0, float(box["y0"]) - padding),
        "x1": min(float(width), float(box["x1"]) + padding),
        "y1": min(float(height), float(box["y1"]) + padding),
    }


def box_intersects(a: dict[str, int | float], b: dict[str, int | float]) -> bool:
    return not (
        float(a["x1"]) < float(b["x0"])
        or float(b["x1"]) < float(a["x0"])
        or float(a["y1"]) < float(b["y0"])
        or float(b["y1"]) < float(a["y0"])
    )


def wire_bbox(wire: dict[str, Any], padding: int = 8) -> dict[str, int]:
    if "polyline" in wire and wire["polyline"]:
        xs = [int(round(point[0])) for point in wire["polyline"]]
        ys = [int(round(point[1])) for point in wire["polyline"]]
        return {
            "x0": min(xs) - padding,
            "y0": min(ys) - padding,
            "x1": max(xs) + padding,
            "y1": max(ys) + padding,
        }
    if wire["orientation"] == "h":
        return {
            "x0": int(wire["span_start"]) - padding,
            "y0": int(wire["axis"]) - padding,
            "x1": int(wire["span_end"]) + padding,
            "y1": int(wire["axis"]) + padding,
        }
    return {
        "x0": int(wire["axis"]) - padding,
        "y0": int(wire["span_start"]) - padding,
        "x1": int(wire["axis"]) + padding,
        "y1": int(wire["span_end"]) + padding,
    }


def point_to_wire_distance(point: tuple[float, float], wire: dict[str, Any]) -> float:
    if wire.get("polyline") and len(wire["polyline"]) >= 2:
        start = wire["polyline"][0]
        end = wire["polyline"][-1]
        if wire.get("orientation") == "h":
            x0, x1 = sorted((float(start[0]), float(end[0])))
            x = min(max(point[0], x0), x1)
            return point_distance(point, (x, float(start[1])))
        y0, y1 = sorted((float(start[1]), float(end[1])))
        y = min(max(point[1], y0), y1)
        return point_distance(point, (float(start[0]), y))
    if wire["orientation"] == "h":
        if "span_start" in wire and "span_end" in wire:
            x0, x1 = float(wire["span_start"]), float(wire["span_end"])
        else:
            x0, x1 = float(wire["bbox"]["x0"]), float(wire["bbox"]["x1"])
        x = min(max(point[0], x0), x1)
        return point_distance(point, (x, float(wire["axis"])))
    if "span_start" in wire and "span_end" in wire:
        y0, y1 = float(wire["span_start"]), float(wire["span_end"])
    else:
        y0, y1 = float(wire["bbox"]["y0"]), float(wire["bbox"]["y1"])
    y = min(max(point[1], y0), y1)
    return point_distance(point, (float(wire["axis"]), y))
