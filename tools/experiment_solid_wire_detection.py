from __future__ import annotations

import argparse
import json
import zipfile
from collections import Counter
from dataclasses import asdict
from pathlib import Path
from typing import Any

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

from analyze_annotation_graph import endpoint_key, load_zip_payload
from analyze_pdf_structure import (
    Box,
    DetectedWire,
    LineSegment,
    build_connection_graph,
    center_in_box,
    detect_regions,
    filter_line_segments,
    is_left_voltage_arrow,
    is_near_outer_frame,
    is_right_reference_block_line,
    line_axis,
    line_from_box,
    line_ink_coverage,
    line_intersection,
    line_span,
    make_binary_mask,
    merge_candidate_lines,
    render_page,
    scan_line_segments,
    split_lines_by_ink_support,
    support_vector_for_span,
    write_json,
)


def load_font(size: int) -> ImageFont.ImageFont:
    for font_name in (
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf",
        "Arial.ttf",
    ):
        try:
            return ImageFont.truetype(font_name, size=size)
        except OSError:
            continue
    return ImageFont.load_default()


FONT_SM = load_font(15)
FONT_MD = load_font(20)


def no_bridge_line_mask(binary: np.ndarray, orientation: str, profile: str = "strict") -> np.ndarray:
    """Extract straight ink without closing gaps first.

    This intentionally avoids turning dashed guide lines into solid wires.
    """

    if profile == "recall":
        if orientation == "h":
            kernels = ((7, 1), (11, 1), (17, 1), (27, 1), (43, 1), (67, 1), (111, 1))
        else:
            kernels = ((1, 7), (1, 11), (1, 17), (1, 27), (1, 43), (1, 67), (1, 111))
    elif orientation == "h":
        kernels = ((23, 1), (39, 1), (67, 1), (111, 1))
    else:
        kernels = ((1, 23), (1, 39), (1, 67), (1, 111))

    combined = np.zeros_like(binary)
    for kernel_size in kernels:
        kernel = cv2.getStructuringElement(cv2.MORPH_RECT, kernel_size)
        opened = cv2.morphologyEx(binary, cv2.MORPH_OPEN, kernel, iterations=1)
        combined = cv2.bitwise_or(combined, opened)
    return combined


def run_lengths(values: np.ndarray, value: bool) -> list[int]:
    lengths: list[int] = []
    cursor = 0
    while cursor < values.size:
        while cursor < values.size and bool(values[cursor]) != value:
            cursor += 1
        start = cursor
        while cursor < values.size and bool(values[cursor]) == value:
            cursor += 1
        if cursor > start:
            lengths.append(cursor - start)
    return lengths


def line_pattern_features(binary: np.ndarray, line: LineSegment, half_width: int) -> dict[str, Any]:
    start, end = line_span(line)
    support = support_vector_for_span(binary, line.orientation, line_axis(line), start, end, half_width=half_width)
    if support.size == 0:
        return {
            "coverage": 0.0,
            "long_gap_count": 0,
            "max_gap": 0,
            "ink_run_count": 0,
            "median_ink_run": 0.0,
            "median_gap": 0.0,
            "dash_like": False,
        }

    ink_runs = run_lengths(support, True)
    gaps = run_lengths(support, False)
    long_gaps = [gap for gap in gaps if gap >= 4]
    coverage = float(np.count_nonzero(support) / support.size)
    median_ink_run = float(np.median(ink_runs)) if ink_runs else 0.0
    median_gap = float(np.median(gaps)) if gaps else 0.0
    dash_like = (
        line.length >= 55
        and len(ink_runs) >= 3
        and len(long_gaps) >= 2
        and 0.18 <= coverage <= 0.82
        and median_ink_run <= 42
    )
    return {
        "coverage": round(coverage, 4),
        "long_gap_count": len(long_gaps),
        "max_gap": max(gaps, default=0),
        "ink_run_count": len(ink_runs),
        "median_ink_run": round(median_ink_run, 2),
        "median_gap": round(median_gap, 2),
        "dash_like": dash_like,
    }


def keep_region_line(line: LineSegment, regions: Any, *, profile: str) -> bool:
    drawing_area = regions.drawing_area
    if profile == "recall" and regions.outer_frame is not None:
        outer = regions.outer_frame
        drawing_area = Box(outer.x0, outer.y0, outer.x1, int(outer.y0 + outer.height * 0.90))

    if not center_in_box(line, drawing_area):
        return False
    if is_near_outer_frame(line, regions):
        return False
    if profile == "recall" and center_in_box(line, regions.title_block_area):
        return False
    if profile == "recall":
        return True
    return not is_left_voltage_arrow(line, regions) and not is_right_reference_block_line(line, regions)


def detect_solid_wire_candidates(
    binary: np.ndarray,
    *,
    width: int,
    height: int,
    profile: str = "strict",
    frame_filter: str = "reject",
) -> tuple[list[LineSegment], list[dict[str, Any]], Any]:
    base_h = scan_line_segments(binary, "h", max(36, width // 100))
    base_v = scan_line_segments(binary, "v", max(32, height // 90))
    regions = detect_regions(width, height, base_h, base_v)

    accepted: list[LineSegment] = []
    rejected: list[dict[str, Any]] = []
    for orientation in ("h", "v"):
        if profile == "recall":
            min_length = 18 if orientation == "h" else 18
            min_accept_length = 24 if orientation == "h" else 22
            max_empty_run = 12
            min_visible_coverage = 0.18
            merge_max_gap = 70 if orientation == "h" else 82
            merge_min_gap_coverage = 0.14
            low_coverage_threshold = 0.18 if orientation == "h" else 0.16
            max_allowed_gap = 76
            max_long_gap_count = 14
        elif profile == "relaxed":
            min_length = 30 if orientation == "h" else 28
            min_accept_length = 42 if orientation == "h" else 34
            max_empty_run = 5
            min_visible_coverage = 0.50
            merge_max_gap = 26 if orientation == "h" else 34
            merge_min_gap_coverage = 0.55
            low_coverage_threshold = 0.56 if orientation == "h" else 0.54
            max_allowed_gap = 20
            max_long_gap_count = 3
        elif profile == "strict":
            min_length = 42 if orientation == "h" else 38
            min_accept_length = 58 if orientation == "h" else 48
            max_empty_run = 3
            min_visible_coverage = 0.62
            merge_max_gap = 18 if orientation == "h" else 22
            merge_min_gap_coverage = 0.72
            low_coverage_threshold = 0.70 if orientation == "h" else 0.66
            max_allowed_gap = 12
            max_long_gap_count = 1
        else:
            raise ValueError("profile must be 'strict', 'relaxed', or 'recall'")

        mask = no_bridge_line_mask(binary, orientation, profile=profile)
        raw = filter_line_segments(scan_line_segments(mask, orientation, 18))
        raw = [line for line in raw if keep_region_line(line, regions, profile=profile)]
        split = split_lines_by_ink_support(
            binary,
            raw,
            min_length=min_length,
            max_empty_run=max_empty_run,
            half_width=4 if orientation == "h" else 5,
            min_visible_coverage=min_visible_coverage,
        )
        merged = merge_candidate_lines(
            split,
            orientation,
            axis_tolerance=8 if orientation == "h" else 10,
            max_gap=merge_max_gap,
            binary=binary,
            max_empty_gap=max_empty_run,
            min_gap_coverage=merge_min_gap_coverage,
        )
        merged = split_lines_by_ink_support(
            binary,
            merged,
            min_length=min_length,
            max_empty_run=max_empty_run,
            half_width=4 if orientation == "h" else 5,
            min_visible_coverage=min_visible_coverage,
        )

        for line in merged:
            features = line_pattern_features(binary, line, half_width=4 if orientation == "h" else 5)
            too_short = line.length < min_accept_length
            dash_like = bool(features["dash_like"]) and profile != "recall"
            low_coverage = float(features["coverage"]) < low_coverage_threshold
            too_gappy = int(features["long_gap_count"]) > max_long_gap_count or int(features["max_gap"]) > max_allowed_gap
            if too_short or dash_like or low_coverage or too_gappy:
                rejected.append(
                    {
                        "orientation": orientation,
                        "box": asdict(line.box),
                        "axis": line_axis(line),
                        "span": list(line_span(line)),
                        "length": line.length,
                        "reason": "dash_like" if dash_like else "too_gappy" if too_gappy else "low_coverage" if low_coverage else "too_short",
                        "features": features,
                    }
                )
            else:
                accepted.append(line)

    frame_keys = detect_rectangular_frame_lines(accepted) if frame_filter == "reject" else set()
    if frame_filter not in {"reject", "keep"}:
        raise ValueError("frame_filter must be 'reject' or 'keep'")
    if frame_keys:
        next_accepted = []
        for index, line in enumerate(accepted):
            if index in frame_keys:
                features = line_pattern_features(binary, line, half_width=4 if line.orientation == "h" else 5)
                rejected.append(
                    {
                        "orientation": line.orientation,
                        "box": asdict(line.box),
                        "axis": line_axis(line),
                        "span": list(line_span(line)),
                        "length": line.length,
                        "reason": "frame_like",
                        "features": features,
                    }
                )
            else:
                next_accepted.append(line)
        accepted = next_accepted

    return accepted, rejected, regions


def span_overlap_ratio(a: tuple[int, int], b: tuple[int, int]) -> float:
    overlap = max(0, min(a[1], b[1]) - max(a[0], b[0]))
    return overlap / max(1, min(a[1] - a[0], b[1] - b[0]))


def detect_rectangular_frame_lines(lines: list[LineSegment]) -> set[int]:
    """Find line candidates that look like rectangular device/card frames.

    A rectangle-like enclosure has two parallel horizontal lines with nearly
    the same span, plus vertical side lines near both ends. These candidates
    are useful as structure, but should not be unioned into wire nets.
    """

    h_items = [(index, line) for index, line in enumerate(lines) if line.orientation == "h" and line.length >= 65]
    v_items = [(index, line) for index, line in enumerate(lines) if line.orientation == "v" and line.length >= 28]
    frame_keys: set[int] = set()
    for h1_index, h1 in h_items:
        h1_start, h1_end = line_span(h1)
        h1_axis = line_axis(h1)
        for h2_index, h2 in h_items:
            if h2_index <= h1_index:
                continue
            h2_start, h2_end = line_span(h2)
            h2_axis = line_axis(h2)
            vertical_gap = abs(h2_axis - h1_axis)
            if vertical_gap < 28 or vertical_gap > 145:
                continue
            if abs(h1_start - h2_start) > 18 or abs(h1_end - h2_end) > 18:
                continue
            if span_overlap_ratio((h1_start, h1_end), (h2_start, h2_end)) < 0.86:
                continue

            y_span = (min(h1_axis, h2_axis), max(h1_axis, h2_axis))
            left_supported = False
            right_supported = False
            left_indices: list[int] = []
            right_indices: list[int] = []
            for v_index, v_line in v_items:
                x_axis = line_axis(v_line)
                v_span = line_span(v_line)
                if span_overlap_ratio(y_span, v_span) < 0.58:
                    continue
                near_left = abs(x_axis - round((h1_start + h2_start) / 2)) <= 18
                near_right = abs(x_axis - round((h1_end + h2_end) / 2)) <= 18
                if near_left:
                    left_supported = True
                    left_indices.append(v_index)
                if near_right:
                    right_supported = True
                    right_indices.append(v_index)

            if left_supported and right_supported:
                frame_keys.update((h1_index, h2_index))
                frame_keys.update(left_indices)
                frame_keys.update(right_indices)
    return frame_keys


def line_connection_count(line: LineSegment, h_lines: list[LineSegment], v_lines: list[LineSegment]) -> int:
    if line.orientation == "h":
        return sum(1 for other in v_lines if line_intersection(line, other, tolerance=10) is not None)
    return sum(1 for other in h_lines if line_intersection(other, line, tolerance=10) is not None)


def build_wires(binary: np.ndarray, lines: list[LineSegment]) -> list[DetectedWire]:
    h_lines = [line for line in lines if line.orientation == "h"]
    v_lines = [line for line in lines if line.orientation == "v"]
    wires: list[DetectedWire] = []
    for line in sorted(lines, key=lambda item: (item.orientation, line_axis(item), line_span(item)[0])):
        coverage = line_ink_coverage(binary, line, half_width=5 if line.orientation == "v" else 4)
        connection_count = line_connection_count(line, h_lines, v_lines)
        if line.length >= 250:
            confidence = 0.92
            reason = "solid_long_wire"
        elif connection_count:
            confidence = 0.78
            reason = "solid_connected_wire"
        else:
            confidence = 0.64
            reason = "solid_isolated_wire"
        start, end = line_span(line)
        wires.append(
            DetectedWire(
                id=f"wire_{len(wires) + 1:03d}",
                orientation=line.orientation,
                box=line.box,
                axis=line_axis(line),
                span_start=start,
                span_end=end,
                length=line.length,
                confidence=round(confidence, 3),
                visible_coverage=round(coverage, 3),
                reason=reason,
                nearby_label_ids=[],
            )
        )
    return wires


def distance_point_to_line(point: tuple[float, float], line: LineSegment) -> float:
    x, y = point
    box = line.box
    axis = line_axis(line)
    start, end = line_span(line)
    if line.orientation == "h":
        dx = max(start - x, x - end, 0.0)
        dy = abs(y - axis)
    else:
        dx = abs(x - axis)
        dy = max(start - y, y - end, 0.0)
    return float((dx * dx + dy * dy) ** 0.5)


def endpoint_points(project: dict[str, Any]) -> dict[str, tuple[float, float]]:
    width = int(project["image_width"])
    height = int(project["image_height"])
    points: dict[str, tuple[float, float]] = {}
    for symbol in project.get("symbols", []):
        symbol_ref = symbol["ref"]
        points[symbol_ref] = (float(symbol["cx"]) * width, float(symbol["cy"]) * height)
        for terminal in symbol.get("terminals") or []:
            points[endpoint_key(symbol_ref, terminal["ref"])] = (
                float(terminal["tx"]) * width,
                float(terminal["ty"]) * height,
            )
    return points


def used_endpoint_points(project: dict[str, Any]) -> dict[str, tuple[float, float]]:
    points = endpoint_points(project)
    used_ids = set()
    for connection in project.get("connections", []):
        used_ids.add(endpoint_key(connection.get("from_symbol_ref"), connection.get("from_terminal_ref")))
        used_ids.add(endpoint_key(connection.get("to_symbol_ref"), connection.get("to_terminal_ref")))
    return {endpoint_id: points[endpoint_id] for endpoint_id in sorted(used_ids) if endpoint_id in points}


def line_overlaps_existing(candidate: LineSegment, existing: list[LineSegment], axis_tolerance: int = 10) -> bool:
    cand_start, cand_end = line_span(candidate)
    for line in existing:
        if line.orientation != candidate.orientation:
            continue
        if abs(line_axis(line) - line_axis(candidate)) > axis_tolerance:
            continue
        start, end = line_span(line)
        overlap = max(0, min(cand_end, end) - max(cand_start, start))
        if overlap / max(1, cand_end - cand_start) >= 0.65:
            return True
    return False


def recover_endpoint_aligned_lines(
    binary: np.ndarray,
    project: dict[str, Any],
    detected_lines: list[LineSegment],
    *,
    orientations: tuple[str, ...] = ("h", "v"),
    axis_tolerance: int = 9,
    max_segment_length: int = 520,
) -> list[dict[str, Any]]:
    points = used_endpoint_points(project)
    point_items = [(endpoint_id, round(point[0]), round(point[1])) for endpoint_id, point in points.items()]
    recovered: list[dict[str, Any]] = []

    for orientation in orientations:
        axis_index = 2 if orientation == "h" else 1
        span_index = 1 if orientation == "h" else 2
        ordered = sorted(point_items, key=lambda item: (item[axis_index], item[span_index]))
        clusters: list[list[tuple[str, int, int]]] = []
        for item in ordered:
            matched = False
            for cluster in clusters:
                cluster_axis = round(sum(existing[axis_index] for existing in cluster) / len(cluster))
                if abs(item[axis_index] - cluster_axis) <= axis_tolerance:
                    cluster.append(item)
                    matched = True
                    break
            if not matched:
                clusters.append([item])

        for cluster in clusters:
            if len(cluster) < 2:
                continue
            cluster_axis = round(sum(item[axis_index] for item in cluster) / len(cluster))
            sorted_cluster = sorted(cluster, key=lambda item: item[span_index])
            for left, right in zip(sorted_cluster, sorted_cluster[1:]):
                span_start = min(left[span_index], right[span_index])
                span_end = max(left[span_index], right[span_index])
                length = span_end - span_start
                if length < 32 or length > max_segment_length:
                    continue
                if orientation == "h":
                    candidate = LineSegment("h", Box(span_start, cluster_axis - 2, span_end, cluster_axis + 2))
                else:
                    candidate = LineSegment("v", Box(cluster_axis - 2, span_start, cluster_axis + 2, span_end))
                if line_overlaps_existing(candidate, detected_lines):
                    continue

                features = line_pattern_features(binary, candidate, half_width=5 if orientation == "v" else 4)
                if features["dash_like"] or int(features["long_gap_count"]) >= 5:
                    continue

                nearby_existing = min((distance_line_to_line(candidate, line) for line in detected_lines), default=10_000.0)
                coverage = float(features["coverage"])
                if coverage < 0.045 and nearby_existing > 36:
                    continue

                recovered.append(
                    {
                        "orientation": orientation,
                        "box": asdict(candidate.box),
                        "axis": line_axis(candidate),
                        "span": list(line_span(candidate)),
                        "length": candidate.length,
                        "from_endpoint": left[0],
                        "to_endpoint": right[0],
                        "reason": "endpoint_aligned_partial_ink" if coverage >= 0.08 else "endpoint_aligned_faint",
                        "features": features,
                        "nearest_detected_line_distance": round(nearby_existing, 2),
                    }
                )
    return dedupe_recovered_lines(recovered)


def recovered_pair_evaluation(project: dict[str, Any], recovered: list[dict[str, Any]]) -> dict[str, Any]:
    gold_pairs = set()
    for connection in project.get("connections", []):
        left = endpoint_key(connection.get("from_symbol_ref"), connection.get("from_terminal_ref"))
        right = endpoint_key(connection.get("to_symbol_ref"), connection.get("to_terminal_ref"))
        gold_pairs.add(tuple(sorted((left, right))))

    recovered_pairs = {
        tuple(sorted((item["from_endpoint"], item["to_endpoint"]))) for item in recovered
    }
    tp = len(gold_pairs & recovered_pairs)
    fp = len(recovered_pairs - gold_pairs)
    fn = len(gold_pairs - recovered_pairs)
    precision = tp / (tp + fp) if tp + fp else None
    recall = tp / (tp + fn) if tp + fn else None
    f1 = 2 * precision * recall / (precision + recall) if precision and recall else None
    return {
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "precision": round(precision, 4) if precision is not None else None,
        "recall": round(recall, 4) if recall is not None else None,
        "f1": round(f1, 4) if f1 is not None else None,
        "false_positive_sample": [list(item) for item in sorted(recovered_pairs - gold_pairs)[:20]],
    }


def distance_line_to_line(left: LineSegment, right: LineSegment) -> float:
    if left.orientation == right.orientation:
        axis_distance = abs(line_axis(left) - line_axis(right))
        left_start, left_end = line_span(left)
        right_start, right_end = line_span(right)
        span_distance = max(left_start - right_end, right_start - left_end, 0)
        return float((axis_distance * axis_distance + span_distance * span_distance) ** 0.5)

    point = line_intersection(left, right) if left.orientation == "h" else line_intersection(right, left)
    if point is not None:
        return 0.0
    return 10_000.0


def dedupe_recovered_lines(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for item in sorted(items, key=lambda value: (value["orientation"], value["axis"], value["span"][0], value["span"][1])):
        candidate = LineSegment(item["orientation"], Box(**item["box"]))
        if any(line_overlaps_existing(candidate, [LineSegment(existing["orientation"], Box(**existing["box"]))], axis_tolerance=6) for existing in output):
            continue
        output.append(item)
    return output


def endpoint_support_metrics(project: dict[str, Any], lines: list[LineSegment], tolerance: float = 20.0) -> dict[str, Any]:
    points = endpoint_points(project)
    endpoint_ids: list[str] = []
    pair_count = 0
    pair_both_supported = 0
    for connection in project.get("connections", []):
        left = endpoint_key(connection.get("from_symbol_ref"), connection.get("from_terminal_ref"))
        right = endpoint_key(connection.get("to_symbol_ref"), connection.get("to_terminal_ref"))
        endpoint_ids.extend([left, right])
        pair_count += 1
        left_supported = min((distance_point_to_line(points[left], line) for line in lines), default=10_000.0) <= tolerance
        right_supported = min((distance_point_to_line(points[right], line) for line in lines), default=10_000.0) <= tolerance
        if left_supported and right_supported:
            pair_both_supported += 1

    unique_endpoint_ids = sorted(set(endpoint_ids))
    unsupported = []
    for endpoint_id in unique_endpoint_ids:
        point = points.get(endpoint_id)
        if point is None:
            unsupported.append({"endpoint": endpoint_id, "reason": "missing_point"})
            continue
        min_distance = min((distance_point_to_line(point, line) for line in lines), default=10_000.0)
        if min_distance > tolerance:
            unsupported.append({"endpoint": endpoint_id, "distance": round(min_distance, 2), "point": [round(point[0]), round(point[1])]})

    supported_count = len(unique_endpoint_ids) - len(unsupported)
    return {
        "gold_connection_count": len(project.get("connections", [])),
        "gold_unique_endpoint_count": len(unique_endpoint_ids),
        "endpoint_near_solid_wire_count": supported_count,
        "endpoint_near_solid_wire_recall": round(supported_count / max(1, len(unique_endpoint_ids)), 4),
        "connection_pairs_with_both_endpoints_near_wire": pair_both_supported,
        "connection_pair_endpoint_support_recall": round(pair_both_supported / max(1, pair_count), 4),
        "unsupported_endpoint_sample": unsupported[:30],
    }


def draw_panel_title(image: Image.Image, title: str) -> Image.Image:
    header = 40
    canvas = Image.new("RGB", (image.width, image.height + header), "white")
    canvas.paste(image.convert("RGB"), (0, header))
    draw = ImageDraw.Draw(canvas)
    draw.text((12, 9), title, font=FONT_MD, fill=(30, 30, 30))
    return canvas


def draw_overlay(
    image: Image.Image,
    wires: list[DetectedWire],
    recovered: list[dict[str, Any]],
    rejected: list[dict[str, Any]],
    project: dict[str, Any],
    *,
    mode: str,
) -> Image.Image:
    canvas = image.convert("RGBA")
    draw = ImageDraw.Draw(canvas)
    if mode in {"solid", "combined"}:
        for wire in wires:
            if wire.orientation == "h":
                start = (wire.span_start, wire.axis)
                end = (wire.span_end, wire.axis)
            else:
                start = (wire.axis, wire.span_start)
                end = (wire.axis, wire.span_end)
            draw.line((start, end), fill=(220, 36, 32, 230), width=5)
    if mode in {"solid", "combined"}:
        for item in recovered:
            if item["orientation"] == "h":
                start = (item["span"][0], item["axis"])
                end = (item["span"][1], item["axis"])
            else:
                start = (item["axis"], item["span"][0])
                end = (item["axis"], item["span"][1])
            draw.line((start, end), fill=(37, 99, 235, 220), width=4)
    if mode in {"rejected", "combined"}:
        for item in rejected:
            if item["reason"] not in {"dash_like", "frame_like"}:
                continue
            box = item["box"]
            if item["orientation"] == "h":
                start = (item["span"][0], item["axis"])
                end = (item["span"][1], item["axis"])
            else:
                start = (item["axis"], item["span"][0])
                end = (item["axis"], item["span"][1])
            draw.line((start, end), fill=(126, 34, 206, 210), width=4)
            draw.rectangle((box["x0"], box["y0"], box["x1"], box["y1"]), outline=(126, 34, 206, 180), width=1)
    if mode == "endpoints":
        points = endpoint_points(project)
        used = set()
        support_lines = [LineSegment(wire.orientation, wire.box) for wire in wires] + [
            LineSegment(item["orientation"], Box(**item["box"])) for item in recovered
        ]
        for connection in project.get("connections", []):
            used.add(endpoint_key(connection.get("from_symbol_ref"), connection.get("from_terminal_ref")))
            used.add(endpoint_key(connection.get("to_symbol_ref"), connection.get("to_terminal_ref")))
        for endpoint_id in used:
            point = points.get(endpoint_id)
            if point is None:
                continue
            supported = min((distance_point_to_line(point, line) for line in support_lines), default=10_000) <= 20
            color = (5, 150, 105, 235) if supported else (220, 38, 38, 235)
            x, y = round(point[0]), round(point[1])
            draw.ellipse((x - 5, y - 5, x + 5, y + 5), fill=color, outline=(255, 255, 255, 255), width=1)
    return canvas.convert("RGB")


def make_review_sheet(
    image: Image.Image,
    wires: list[DetectedWire],
    recovered: list[dict[str, Any]],
    rejected: list[dict[str, Any]],
    project: dict[str, Any],
    *,
    target_width: int = 1050,
) -> Image.Image:
    panels = [
        draw_panel_title(image, "original"),
        draw_panel_title(draw_overlay(image, wires, recovered, rejected, project, mode="solid"), "solid wires: red / recovered by endpoint alignment: blue"),
        draw_panel_title(draw_overlay(image, wires, recovered, rejected, project, mode="rejected"), "rejected dashed/frame candidates: purple"),
        draw_panel_title(draw_overlay(image, wires, recovered, rejected, project, mode="endpoints"), "gold from-to endpoints: green=supported, red=not supported"),
    ]
    resized = []
    for panel in panels:
        scale = target_width / panel.width
        resized.append(panel.resize((target_width, round(panel.height * scale)), Image.Resampling.LANCZOS))
    cell_h = max(panel.height for panel in resized)
    sheet = Image.new("RGB", (target_width * 2, cell_h * 2), (245, 245, 245))
    for index, panel in enumerate(resized):
        x = (index % 2) * target_width
        y = (index // 2) * cell_h
        sheet.paste(panel, (x, y))
    return sheet


def page_sheet_map(values: list[str] | None) -> dict[int, str]:
    if not values:
        raise ValueError("--page-sheet-map is required, for example: 3=SHEET001")
    mapping: dict[int, str] = {}
    for value in values:
        page, sheet = value.split("=", 1)
        mapping[int(page)] = sheet
    return mapping


def analyze_pages(
    *,
    pdf_path: Path,
    annotation_zip: Path,
    pages: list[int],
    page_to_sheet: dict[int, str],
    dpi: int,
    out_dir: Path,
    profile: str,
    recovery: str,
    frame_filter: str,
) -> dict[str, Any]:
    bundle, _csv_rows, _netlist = load_zip_payload(annotation_zip)
    projects = {str(project["sheet_no"]): project for project in bundle.get("projects", [])}
    out_dir.mkdir(parents=True, exist_ok=True)

    summaries = []
    for page in pages:
        sheet_no = page_to_sheet[page]
        project = projects[sheet_no]
        page_dir = out_dir / f"page_{page:03d}"
        page_dir.mkdir(parents=True, exist_ok=True)
        image = render_page(pdf_path, page, dpi)
        binary = make_binary_mask(image)
        width, height = image.size
        lines, rejected, regions = detect_solid_wire_candidates(
            binary,
            width=width,
            height=height,
            profile=profile,
            frame_filter=frame_filter,
        )
        if recovery == "endpoint_alignment":
            recovered = recover_endpoint_aligned_lines(binary, project, lines)
        elif recovery == "vertical_endpoint_alignment":
            recovered = recover_endpoint_aligned_lines(binary, project, lines, orientations=("v",), max_segment_length=180)
        else:
            recovered = []
        recovered_lines = [LineSegment(item["orientation"], Box(**item["box"])) for item in recovered]
        combined_lines = [*lines, *recovered_lines]
        wires = build_wires(binary, lines)
        combined_wires = build_wires(binary, combined_lines)
        graph = build_connection_graph(combined_wires, [])
        metrics = endpoint_support_metrics(project, combined_lines)
        detector_only_metrics = endpoint_support_metrics(project, lines)
        recovered_pair_metrics = recovered_pair_evaluation(project, recovered)
        rejected_reason_counts = Counter(item["reason"] for item in rejected)
        accepted_dash_like = [
            {
                "wire_id": wire.id,
                "orientation": wire.orientation,
                "box": asdict(wire.box),
                "length": wire.length,
                "features": line_pattern_features(binary, LineSegment(wire.orientation, wire.box), half_width=5 if wire.orientation == "v" else 4),
            }
            for wire in wires
            if line_pattern_features(binary, LineSegment(wire.orientation, wire.box), half_width=5 if wire.orientation == "v" else 4)["dash_like"]
        ]

        payload = {
            "page": page,
            "sheet_no": sheet_no,
            "dpi": dpi,
            "image_size": {"width": width, "height": height},
            "regions": asdict(regions),
            "solid_wire_count": len(wires),
            "horizontal_solid_wire_count": sum(wire.orientation == "h" for wire in wires),
            "vertical_solid_wire_count": sum(wire.orientation == "v" for wire in wires),
            "recovered_wire_count": len(recovered),
            "recovered_reason_counts": dict(sorted(Counter(item["reason"] for item in recovered).items())),
            "rejected_candidate_count": len(rejected),
            "rejected_reason_counts": dict(sorted(rejected_reason_counts.items())),
            "accepted_dash_like_count": len(accepted_dash_like),
            "accepted_dash_like_sample": accepted_dash_like[:20],
            "endpoint_support": metrics,
            "detector_only_endpoint_support": detector_only_metrics,
            "recovered_pair_evaluation": recovered_pair_metrics,
            "graph": {
                "node_count": len(graph["nodes"]),
                "edge_count": len(graph["edges"]),
            },
            "wires": [asdict(wire) for wire in wires],
            "recovered_wires": recovered,
            "rejected_candidates": rejected[:250],
        }
        write_json(page_dir / "solid_wire_experiment.json", payload)
        write_json(
            page_dir / "connection_graph.json",
            {
                "page": page,
                "sheet_no": sheet_no,
                "wires": [asdict(wire) for wire in combined_wires],
                "nodes": graph["nodes"],
                "edges": graph["edges"],
            },
        )
        image.save(page_dir / "original.png")
        make_review_sheet(image, wires, recovered, rejected, project).save(page_dir / "solid_wire_review.png")
        summaries.append(
            {
                "page": page,
                "sheet_no": sheet_no,
                "solid_wire_count": payload["solid_wire_count"],
                "horizontal_solid_wire_count": payload["horizontal_solid_wire_count"],
                "vertical_solid_wire_count": payload["vertical_solid_wire_count"],
                "recovered_wire_count": payload["recovered_wire_count"],
                "recovered_reason_counts": payload["recovered_reason_counts"],
                "rejected_reason_counts": payload["rejected_reason_counts"],
                "accepted_dash_like_count": payload["accepted_dash_like_count"],
                "detector_only_endpoint_near_solid_wire_recall": detector_only_metrics["endpoint_near_solid_wire_recall"],
                "detector_only_connection_pair_endpoint_support_recall": detector_only_metrics["connection_pair_endpoint_support_recall"],
                "endpoint_near_solid_wire_recall": metrics["endpoint_near_solid_wire_recall"],
                "connection_pair_endpoint_support_recall": metrics["connection_pair_endpoint_support_recall"],
                "recovered_pair_precision": recovered_pair_metrics["precision"],
                "recovered_pair_recall": recovered_pair_metrics["recall"],
                "recovered_pair_tp": recovered_pair_metrics["tp"],
                "recovered_pair_fp": recovered_pair_metrics["fp"],
                "node_count": payload["graph"]["node_count"],
                "edge_count": payload["graph"]["edge_count"],
                "review": str(page_dir / "solid_wire_review.png"),
                "json": str(page_dir / "solid_wire_experiment.json"),
            }
        )

    aggregate = {
        "page_count": len(summaries),
        "solid_wire_count": sum(item["solid_wire_count"] for item in summaries),
        "horizontal_solid_wire_count": sum(item["horizontal_solid_wire_count"] for item in summaries),
        "vertical_solid_wire_count": sum(item["vertical_solid_wire_count"] for item in summaries),
        "recovered_wire_count": sum(item["recovered_wire_count"] for item in summaries),
        "accepted_dash_like_count": sum(item["accepted_dash_like_count"] for item in summaries),
        "detector_only_endpoint_near_solid_wire_recall_avg": round(
            sum(item["detector_only_endpoint_near_solid_wire_recall"] for item in summaries) / max(1, len(summaries)),
            4,
        ),
        "detector_only_connection_pair_endpoint_support_recall_avg": round(
            sum(item["detector_only_connection_pair_endpoint_support_recall"] for item in summaries) / max(1, len(summaries)),
            4,
        ),
        "endpoint_near_solid_wire_recall_avg": round(
            sum(item["endpoint_near_solid_wire_recall"] for item in summaries) / max(1, len(summaries)),
            4,
        ),
        "connection_pair_endpoint_support_recall_avg": round(
            sum(item["connection_pair_endpoint_support_recall"] for item in summaries) / max(1, len(summaries)),
            4,
        ),
    }
    recovered_tp = sum(item["recovered_pair_tp"] for item in summaries)
    recovered_fp = sum(item["recovered_pair_fp"] for item in summaries)
    recovered_gold = sum(len(projects[item["sheet_no"]].get("connections", [])) for item in summaries)
    aggregate["recovered_pair_precision"] = round(recovered_tp / max(1, recovered_tp + recovered_fp), 4) if recovered_tp + recovered_fp else None
    aggregate["recovered_pair_recall"] = round(recovered_tp / max(1, recovered_gold), 4) if recovered_gold else None
    result = {
        "schema_version": "todensekkei.solid_wire_experiment.v1",
        "pdf": str(pdf_path),
        "annotation_zip": str(annotation_zip),
        "dpi": dpi,
        "profile": profile,
        "recovery": recovery,
        "frame_filter": frame_filter,
        "method": "no-closing morphology + support gap classification; recall profile relaxes region/dash suppression and keeps bottom drawing content before graphing",
        "aggregate": aggregate,
        "pages": summaries,
        "notes": [
            "Red lines in review images are accepted as solid wires.",
            "Purple lines are dash-like or frame-like candidates rejected from the connection graph.",
            "Endpoint support uses gold from-to endpoint points only as a proxy for whether real wires are present near annotated endpoints.",
        ],
    }
    write_json(out_dir / "summary.json", result)
    return result


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Experiment with solid-only wire detection that rejects dashed candidates.")
    parser.add_argument("--pdf", type=Path, required=True)
    parser.add_argument("--annotation-zip", type=Path, required=True)
    parser.add_argument("--pages", nargs="+", type=int, required=True)
    parser.add_argument("--page-sheet-map", nargs="+", required=True, help="Example: 3=SHEET001")
    parser.add_argument("--dpi", type=int, default=150)
    parser.add_argument("--out-dir", type=Path, default=Path("data/private/wire_experiments/solid_no_dash_v1"))
    parser.add_argument("--profile", choices=("strict", "relaxed", "recall"), default="strict")
    parser.add_argument("--recovery", choices=("none", "endpoint_alignment", "vertical_endpoint_alignment"), default="none")
    parser.add_argument("--frame-filter", choices=("reject", "keep"), default="reject")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    result = analyze_pages(
        pdf_path=args.pdf,
        annotation_zip=args.annotation_zip,
        pages=args.pages,
        page_to_sheet=page_sheet_map(args.page_sheet_map),
        dpi=args.dpi,
        out_dir=args.out_dir,
        profile=args.profile,
        recovery=args.recovery,
        frame_filter=args.frame_filter,
    )
    print(json.dumps({"aggregate": result["aggregate"], "pages": result["pages"]}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
