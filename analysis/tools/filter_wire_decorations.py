"""Conservative, image-supported rejection of dashed guides and label rules.

Bounding boxes only constrain the search. They are never erased, and a nearby
continuous parallel line vetoes dashed rejection so inner card wiring survives.
"""

from __future__ import annotations

import logging
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from itertools import pairwise
from typing import Any

import cv2
import numpy as np
from analyze_pdf_structure import (
    Box,
    DetectedLabel,
    DetectedWire,
    LineSegment,
    build_connection_graph,
    line_ink_coverage,
    line_span,
    make_binary_mask,
    support_vector_for_span,
)
from build_e2e_demo_outputs import build_connection_candidates
from PIL import Image, ImageOps


@dataclass(frozen=True)
class Rule:
    axis: int
    start: int
    end: int


def _runs(support: np.ndarray) -> list[tuple[int, int]]:
    transitions = np.diff(np.pad(support.astype(np.int8), (1, 1)))
    return list(zip(np.flatnonzero(transitions == 1), np.flatnonzero(transitions == -1), strict=True))


def _regular_dashes(support: np.ndarray, scale: float) -> bool:
    runs = _runs(support)
    if len(runs) < 4:
        return False
    lengths = np.array([end - start for start, end in runs])
    gaps = np.array([right[0] - left[1] for left, right in pairwise(runs)])
    median_gap = float(np.median(gaps))
    # Several regular gaps, not just two missing patches in an otherwise solid wire.
    regular = (gaps >= max(2, 2 * scale)) & (np.abs(gaps - median_gap) <= max(2, median_gap * 0.5))
    return bool(
        0.22 <= float(support.mean()) <= 0.86
        and max(2, 2 * scale) <= median_gap <= 12 * scale
        and np.count_nonzero(regular) >= 3
        and float(regular.mean()) >= 0.8
        and float(np.median(lengths)) <= 28 * scale
        and int(lengths.max()) <= max(32 * scale, 3 * float(np.median(lengths)))
        and runs[0][0] <= 12 * scale
        and support.size - runs[-1][1] <= 12 * scale
    )


def _is_dashed(binary: np.ndarray, line: LineSegment, scale: float) -> bool:
    start, end = line_span(line)
    box = line.box
    lo, hi = (box.y0, box.y1) if line.orientation == "h" else (box.x0, box.x1)
    axis_limit = binary.shape[0 if line.orientation == "h" else 1]
    vectors = [
        support_vector_for_span(binary, line.orientation, axis, start, end, half_width=1)
        for axis in range(max(0, lo - 1), min(axis_limit, hi + 1))
    ]
    vectors = [vector for vector in vectors if vector.size]
    # Merging may have put a dashed frame and an adjacent solid wire in one box.
    if any(float(vector.mean()) >= 0.9 for vector in vectors):
        return False
    return any(_regular_dashes(vector, scale) for vector in vectors)


def _horizontal_rules(binary: np.ndarray, scale: float) -> list[Rule]:
    minimum = max(24, round(32 * scale))
    opened = cv2.morphologyEx(binary, cv2.MORPH_OPEN, np.ones((1, minimum), np.uint8))
    count, _, stats, _ = cv2.connectedComponentsWithStats(opened, connectivity=8)
    rules = []
    for x, y, width, height, area in stats[1:count]:
        if width >= minimum and height <= max(4, round(5 * scale)) and area / (width * height) >= 0.55:
            rules.append(Rule(round(y + (height - 1) / 2), int(x), int(x + width)))
    return sorted(rules, key=lambda rule: rule.axis)


def _external_end_connection(binary: np.ndarray, upper: Rule, lower: Rule, scale: float) -> bool:
    length = max(10, round(12 * scale))
    for rule, direction in ((upper, -1), (lower, 1)):
        start, end = sorted((rule.axis + direction * 3, rule.axis + direction * length))
        for axis in (rule.start, rule.end - 1):
            support = support_vector_for_span(binary, "v", axis, start, end, half_width=1)
            if support.size and float(support.mean()) >= 0.9:
                return True
    return False


def _label_rules(
    image: Image.Image,
    binary: np.ndarray,
    symbols: list[dict[str, Any]],
    scale: float,
    confirm_text: Callable[[Image.Image], bool] | None,
) -> tuple[list[Rule], int]:
    if confirm_text is None:
        return [], 0
    rules = _horizontal_rules(binary, scale)
    protected = [
        (
            (symbol.get("class_name") or symbol.get("type")),
            Box(**{key: round(value) for key, value in symbol["bbox"].items()}),
        )
        for symbol in symbols
        if (symbol.get("class_name") or symbol.get("type")) != "other"
    ]
    confirmed: set[Rule] = set()
    checked = 0
    tolerance = max(3, round(4 * scale))
    for index, upper in enumerate(rules):
        for lower in rules[index + 1 :]:
            gap = lower.axis - upper.axis
            if gap > 38 * scale:
                break
            if gap < 10 * scale:
                continue
            if abs(upper.start - lower.start) > tolerance or abs(upper.end - lower.end) > tolerance:
                continue
            band = Box(min(upper.start, lower.start), upper.axis, max(upper.end, lower.end), lower.axis)
            if band.width < gap * 1.8 or band.width > 420 * scale:
                continue
            # A third close parallel stroke may be actual wiring, not a label box.
            margin = max(12, round(8 * scale))
            if any(
                rule not in (upper, lower)
                and upper.axis - margin <= rule.axis <= lower.axis + margin
                and min(rule.end, band.x1) - max(rule.start, band.x0) >= 0.8 * band.width
                for rule in rules
            ):
                continue
            if _external_end_connection(binary, upper, lower, scale):
                continue
            # Search the whole page, even without YOLO. Large label-inclusive boxes
            # must not exclude their own labels, but compact terminals and coils need protection.
            expanded = band.padded(max(3, round(3 * scale)), *image.size)
            if any(
                expanded.intersection_area(box) > 0
                and (kind in {"relay_coil", "solenoid"} or box.area <= band.area * 0.5)
                for kind, box in protected
            ):
                continue
            inset = max(2, round(3 * scale))
            crop = image.crop((band.x0 + inset, band.y0 + inset, band.x1 - inset, band.y1 - inset))
            checked += 1
            if confirm_text(crop):
                confirmed.update((upper, lower))
    return sorted(confirmed, key=lambda rule: (rule.axis, rule.start)), checked


def rapidocr_text_confirmation(reader: Any) -> Callable[[Image.Image], bool]:
    def confirm(crop: Image.Image) -> bool:
        # Tight, short strips otherwise miss the OCR detector's minimum text size.
        pixels = np.asarray(ImageOps.expand(crop.convert("RGB"), border=8, fill="white"))
        pixels = cv2.resize(pixels, None, fx=4, fy=4, interpolation=cv2.INTER_CUBIC)
        results, _ = reader(pixels, use_cls=False)
        return any(
            float(score) >= 0.75 and sum(character.isalnum() for character in text) >= 2
            for _points, text, score in results or []
        )

    return confirm


def _rule_cuts(
    binary: np.ndarray, line: LineSegment, rules: list[Rule], scale: float
) -> list[tuple[int, int]]:
    if line.orientation != "h":
        return []
    start, end = line_span(line)
    tolerance = max(2, round(2 * scale))
    nearby = [rule for rule in rules if line.box.y0 - tolerance <= rule.axis <= line.box.y1 + tolerance]
    cuts = []
    for rule in nearby:
        lo, hi = max(start, rule.start), min(end, rule.end)
        if hi - lo < 24 * scale:
            continue
        parallel_solid = False
        for axis in range(max(0, line.box.y0), min(binary.shape[0], line.box.y1)):
            if any(
                abs(axis - candidate.axis) <= tolerance + 1 and candidate.start <= lo and candidate.end >= hi
                for candidate in nearby
            ):
                continue
            support = support_vector_for_span(binary, "h", axis, lo, hi, half_width=0)
            if support.size and float(support.mean()) >= 0.9:
                parallel_solid = True
                break
        if not parallel_solid:
            cuts.append((lo, hi))
    merged: list[tuple[int, int]] = []
    for lo, hi in sorted(cuts):
        if merged and lo <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(hi, merged[-1][1]))
        else:
            merged.append((lo, hi))
    return merged


def _cut_wire(wire: dict[str, Any], cuts: list[tuple[int, int]], binary: np.ndarray) -> list[dict[str, Any]]:
    line = LineSegment(wire["orientation"], Box(**wire["bbox"]))
    start, end = line_span(line)
    spans = []
    for lo, hi in cuts:
        if lo > start:
            spans.append((start, lo))
        start = max(start, hi)
    if start < end:
        spans.append((start, end))
    parts = []
    for index, (lo, hi) in enumerate(spans, 1):
        box = {**wire["bbox"], "x0": lo, "x1": hi}
        parts.append(
            {
                **wire,
                "id": f"{wire['id']}_part_{index:02d}",
                "bbox": box,
                "polyline": [[lo, wire["axis"]], [hi, wire["axis"]]],
                "length": hi - lo,
                "visible_coverage": round(line_ink_coverage(binary, LineSegment("h", Box(**box))), 3),
            }
        )
    return parts


def filter_wire_decorations(
    payload: dict[str, Any],
    image: Image.Image,
    symbols: list[dict[str, Any]],
    *,
    confirm_text: Callable[[Image.Image], bool] | None = None,
) -> dict[str, Any]:
    """Filter before terminal inference, then rebuild ALL wire-derived geometry."""
    binary = make_binary_mask(image)
    scale = max(0.5, image.width / 1755)
    ocr_failed = False
    try:
        rules, checked = _label_rules(image, binary, symbols, scale, confirm_text)
    except Exception:
        logging.getLogger(__name__).exception("Label rule OCR failed; keeping solid rules")
        rules, checked, ocr_failed = [], 0, True
    kept = []
    removed = []
    trimmed = []
    excluded_segments = []
    for wire in payload.get("wires", []):
        line = LineSegment(wire["orientation"], Box(**wire["bbox"]))
        if _is_dashed(binary, line, scale):
            removed.append({"wire_id": wire["id"], "reason": "regular_dashed_line"})
            start, end = line_span(line)
            excluded_segments.append(
                {"orientation": line.orientation, "axis": wire["axis"], "start": start, "end": end}
            )
        elif cuts := _rule_cuts(binary, line, rules, scale):
            parts = _cut_wire(wire, cuts, binary)
            kept.extend(parts)
            target = trimmed if parts else removed
            target.append({"wire_id": wire["id"], "reason": "label_rule", "spans": cuts})
            excluded_segments.extend(
                {"orientation": "h", "axis": wire["axis"], "start": lo, "end": hi} for lo, hi in cuts
            )
        else:
            kept.append(wire)

    info = {
        "method": "regular_dashes_and_ocr_confirmed_label_rules_v2",
        "input_wire_count": len(payload.get("wires", [])),
        "removed_wire_count": len(removed),
        "removed_by_reason": dict(Counter(item["reason"] for item in [*removed, *trimmed])),
        "removed": removed,
        "trimmed_wire_count": len(trimmed),
        "trimmed": trimmed,
        "excluded_segments": excluded_segments,
        "label_rule_count": len(rules),
        "label_ocr_crop_count": checked,
        "label_rule_ocr_available": confirm_text is not None,
        "label_rule_ocr_failed": ocr_failed,
    }
    result = {**payload, "quality": {**payload.get("quality", {}), "wire_decoration_filter": info}}
    if not removed and not trimmed:
        return result
    detected = [
        DetectedWire(
            id=wire["id"],
            orientation=wire["orientation"],
            box=Box(**wire["bbox"]),
            axis=wire["axis"],
            span_start=int(wire["polyline"][0][0 if wire["orientation"] == "h" else 1]),
            span_end=int(wire["polyline"][-1][0 if wire["orientation"] == "h" else 1]),
            length=wire["length"],
            confidence=wire["confidence"],
            visible_coverage=wire.get("visible_coverage") or 0.0,
            reason=wire["reason"],
            nearby_label_ids=wire.get("nearby_label_ids", []),
        )
        for wire in kept
    ]
    labels = [
        DetectedLabel(
            id=label["id"],
            raw_text=label.get("raw_text", label["text"]),
            normalized_text=label["text"],
            label_type=label["type"],
            confidence=label["confidence"],
            source=label["source"],
            box=Box(**label["bbox"]),
            accepted=True,
            reason="retained",
        )
        for label in payload.get("labels", [])
    ]
    graph = build_connection_graph(detected, labels)
    connections = build_connection_candidates({**graph, "wires": kept}, payload.get("labels", []))
    result.update(wires=kept, nodes=graph["nodes"], edges=graph["edges"], connections=connections)
    result["quality"].update(
        {
            "wire_count": len(kept),
            "horizontal_wire_count": sum(wire["orientation"] == "h" for wire in kept),
            "vertical_wire_count": sum(wire["orientation"] == "v" for wire in kept),
            "node_count": len(graph["nodes"]),
            "edge_count": len(graph["edges"]),
            "connection_count": len(connections),
            "candidate_connection_count": sum(item["type"] == "candidate_connection" for item in connections),
            "wire_visible_coverage_min": min((wire.visible_coverage for wire in detected), default=0.0),
            "wire_visible_coverage_avg": sum(wire.visible_coverage for wire in detected)
            / max(1, len(detected)),
        }
    )
    return result
