"""図面の画像から配線（線分）と文字領域を検出し、配線のグラフを作る（本番の解析で使う部分）。

OCR で文字を消す実験の分岐、デバッグ画像、ファイルへの書き出しは analysis/tools/wire_detection_lab.py にある。
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

import cv2
import numpy as np
from PIL import Image

from analysis.wiring.geometry import merged_span_length
from analysis.wiring.graph import build_connection_graph
from analysis.wiring.shapes import (
    Box,
    DetectedLabel,
    DetectedWire,
    LineSegment,
    PageRegions,
    TextRegion,
    line_axis,
    line_intersection,
)

# When two vertical pieces merge side by side (the union is this many px wider than the wider piece), the
# longer piece keeps the x position: a text or dot fragment beside a thin wire must not pull the wire's axis
# off its ink, or the ink check rejects the wire (ER21282). Overlapping pieces still merge as a union.
MERGE_SPINE_MIN_SPREAD = 6


def make_binary_mask(image: Image.Image) -> np.ndarray:
    gray = np.array(image.convert("L"))
    blurred = cv2.GaussianBlur(gray, (3, 3), 0)
    _threshold, otsu = cv2.threshold(blurred, 0, 255, cv2.THRESH_BINARY_INV | cv2.THRESH_OTSU)
    adaptive = cv2.adaptiveThreshold(
        blurred,
        255,
        cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
        cv2.THRESH_BINARY_INV,
        41,
        13,
    )
    return cv2.bitwise_or(otsu, adaptive)


def _overlap(start_a: int, end_a: int, start_b: int, end_b: int) -> int:
    return max(0, min(end_a, end_b) - max(start_a, start_b))


def _merge_parallel_boxes(
    boxes: Iterable[Box], orientation: str, gap: int = 2, *, component_labels: np.ndarray | None = None
) -> list[Box]:
    ordered = sorted(boxes, key=lambda box: (box.y0, box.x0) if orientation == "h" else (box.x0, box.y0))
    active: list[tuple[Box, dict[int, Box]]] = []
    merged: list[tuple[Box, dict[int, Box]]] = []

    def union(a: Box, b: Box) -> Box:
        return Box(min(a.x0, b.x0), min(a.y0, b.y0), max(a.x1, b.x1), max(a.y1, b.y1))

    for box in ordered:
        component = int(component_labels[box.y0, box.x0]) if component_labels is not None else 0
        next_active: list[tuple[Box, dict[int, Box]]] = []
        for existing, components in active:
            if orientation == "h" and existing.y1 + gap < box.y0 or orientation == "v" and existing.x1 + gap < box.x0:
                merged.append((existing, components))
            else:
                next_active.append((existing, components))
        active = next_active

        match_index: int | None = None
        best_overlap = 0
        for index, (existing, _components) in enumerate(active):
            if orientation == "h":
                overlap = _overlap(existing.x0, existing.x1, box.x0, box.x1)
                required = max(8, int(min(existing.width, box.width) * 0.35))
            else:
                overlap = _overlap(existing.y0, existing.y1, box.y0, box.y1)
                required = max(8, int(min(existing.height, box.height) * 0.35))
            if overlap >= required and overlap > best_overlap:
                match_index = index
                best_overlap = overlap

        if match_index is None:
            active.append((box, {component: box}))
        else:
            existing, components = active[match_index]
            components[component] = union(components.get(component, box), box)
            active[match_index] = (union(existing, box), components)

    merged.extend(active)
    if component_labels is None:
        return [box for box, _ in merged]

    result: list[Box] = []

    def span(item: Box) -> int:
        return item.width if orientation == "h" else item.height

    for box, components in merged:
        # Locate each candidate on its longest connected stroke, not detached
        # text inside its bbox. Do not promote the discarded text to new wires.
        longest = max(span(item) for item in components.values())
        # Restrict correction to a stroke spanning the original candidate.
        # Partial/gapped candidates remain the existing recovery path's job.
        if longest < span(box):
            result.append(box)
            continue
        result.extend(item for item in components.values() if span(item) == longest)
    return result


def scan_line_segments(
    mask: np.ndarray, orientation: str, min_length: int, *, isolate_components: bool = False
) -> list[LineSegment]:
    height, width = mask.shape
    candidates: list[Box] = []
    if orientation == "h":
        for y in range(height):
            row = mask[y]
            x = 0
            while x < width:
                while x < width and row[x] == 0:
                    x += 1
                start = x
                while x < width and row[x] != 0:
                    x += 1
                if x - start >= min_length:
                    candidates.append(Box(start, y, x, y + 1))
    elif orientation == "v":
        for x in range(width):
            y = 0
            while y < height:
                while y < height and mask[y, x] == 0:
                    y += 1
                start = y
                while y < height and mask[y, x] != 0:
                    y += 1
                if y - start >= min_length:
                    candidates.append(Box(x, start, x + 1, y))
    else:
        raise ValueError("orientation must be 'h' or 'v'")

    component_labels = None
    if isolate_components:
        _, component_labels = cv2.connectedComponents(mask, connectivity=8)
    merged = _merge_parallel_boxes(candidates, orientation, component_labels=component_labels)
    if orientation == "h":
        filtered = [box for box in merged if box.width >= min_length and box.width >= box.height * 6]
    else:
        filtered = [box for box in merged if box.height >= min_length and box.height >= box.width * 6]
    return [LineSegment(orientation, box) for box in filtered]


def line_mask(binary: np.ndarray, orientation: str) -> np.ndarray:
    if orientation == "h":
        kernels = ((31, 1), (55, 1), (95, 1), (165, 1))
        bridge_kernels = ((13, 1), (21, 1), (35, 1), (55, 1))
    else:
        kernels = ((1, 37), (1, 65), (1, 115), (1, 195))
        bridge_kernels = ((1, 13), (1, 21), (1, 35), (1, 55))

    combined = np.zeros_like(binary)
    for kernel_size, bridge_size in zip(kernels, bridge_kernels, strict=False):
        bridge = cv2.getStructuringElement(cv2.MORPH_RECT, bridge_size)
        line_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, kernel_size)
        bridged = cv2.morphologyEx(binary, cv2.MORPH_CLOSE, bridge, iterations=1)
        opened = cv2.morphologyEx(bridged, cv2.MORPH_OPEN, line_kernel, iterations=1)
        combined = cv2.bitwise_or(combined, opened)
    return combined


def filter_line_segments(lines: Iterable[LineSegment]) -> list[LineSegment]:
    filtered: list[LineSegment] = []
    for line in lines:
        box = line.box
        if line.orientation == "h":
            thickness = max(1, box.height)
            accept = box.width >= 45 and thickness <= 32 and box.width / thickness >= 8
        else:
            thickness = max(1, box.width)
            accept = box.height >= 45 and thickness <= 32 and box.height / thickness >= 8
        if accept:
            filtered.append(line)
    return filtered


def line_span(line: LineSegment) -> tuple[int, int]:
    if line.orientation == "h":
        return line.box.x0, line.box.x1
    return line.box.y0, line.box.y1


def line_from_box(orientation: str, box: Box) -> LineSegment:
    return LineSegment(orientation=orientation, box=box)


def support_vector_for_span(
    binary: np.ndarray,
    orientation: str,
    axis: int,
    start: int,
    end: int,
    half_width: int = 4,
) -> np.ndarray:
    if end <= start:
        return np.array([], dtype=bool)

    height, width = binary.shape
    if orientation == "h":
        x0 = max(0, start)
        x1 = min(width, end)
        y0 = max(0, axis - half_width)
        y1 = min(height, axis + half_width + 1)
        strip = binary[y0:y1, x0:x1]
        return np.any(strip > 0, axis=0) if strip.size else np.array([], dtype=bool)

    x0 = max(0, axis - half_width)
    x1 = min(width, axis + half_width + 1)
    y0 = max(0, start)
    y1 = min(height, end)
    strip = binary[y0:y1, x0:x1]
    return np.any(strip > 0, axis=1) if strip.size else np.array([], dtype=bool)


def close_short_1d_gaps(support: np.ndarray, max_gap: int) -> np.ndarray:
    if support.size == 0:
        return support
    closed = support.copy()
    supported_indices = np.flatnonzero(support)
    if supported_indices.size < 2:
        return closed
    previous = int(supported_indices[0])
    for current in supported_indices[1:]:
        current = int(current)
        if current - previous - 1 <= max_gap:
            closed[previous : current + 1] = True
        previous = current
    return closed


def line_ink_coverage(binary: np.ndarray, line: LineSegment, half_width: int = 4) -> float:
    support = support_vector_for_span(binary, line.orientation, line_axis(line), *line_span(line), half_width=half_width)
    if support.size == 0:
        return 0.0
    return float(np.count_nonzero(support) / support.size)


def gap_has_ink_support(
    binary: np.ndarray,
    orientation: str,
    axis: int,
    gap_start: int,
    gap_end: int,
    *,
    max_empty_gap: int = 8,
    min_gap_coverage: float = 0.18,
    half_width: int = 4,
) -> bool:
    gap = gap_end - gap_start
    if gap <= 0:
        return True
    if gap <= max_empty_gap:
        return True
    support = support_vector_for_span(binary, orientation, axis, gap_start, gap_end, half_width=half_width)
    if support.size == 0:
        return False
    return float(np.count_nonzero(support) / support.size) >= min_gap_coverage


def split_line_by_ink_support(
    binary: np.ndarray,
    line: LineSegment,
    *,
    min_length: int,
    max_empty_run: int,
    half_width: int = 4,
    min_visible_coverage: float = 0.35,
) -> list[LineSegment]:
    start, end = line_span(line)
    support = support_vector_for_span(binary, line.orientation, line_axis(line), start, end, half_width=half_width)
    if support.size == 0:
        return []

    closed = close_short_1d_gaps(support, max_empty_run)
    split: list[LineSegment] = []
    cursor = 0
    while cursor < closed.size:
        while cursor < closed.size and not closed[cursor]:
            cursor += 1
        run_start = cursor
        while cursor < closed.size and closed[cursor]:
            cursor += 1
        run_end = cursor
        run_length = run_end - run_start
        if run_length < min_length:
            continue
        visible_coverage = float(np.count_nonzero(support[run_start:run_end]) / max(1, run_length))
        if visible_coverage < min_visible_coverage:
            continue

        if line.orientation == "h":
            box = Box(start + run_start, line.box.y0, start + run_end, line.box.y1)
        else:
            box = Box(line.box.x0, start + run_start, line.box.x1, start + run_end)
        split.append(LineSegment(line.orientation, box))
    return split


def split_lines_by_ink_support(
    binary: np.ndarray,
    lines: Iterable[LineSegment],
    *,
    min_length: int,
    max_empty_run: int,
    half_width: int = 4,
    min_visible_coverage: float = 0.35,
) -> list[LineSegment]:
    split: list[LineSegment] = []
    for line in lines:
        split.extend(
            split_line_by_ink_support(
                binary,
                line,
                min_length=min_length,
                max_empty_run=max_empty_run,
                half_width=half_width,
                min_visible_coverage=min_visible_coverage,
            )
        )
    return split


def line_gap_span(existing: LineSegment, incoming: LineSegment) -> tuple[int, int]:
    existing_start, existing_end = line_span(existing)
    incoming_start, incoming_end = line_span(incoming)
    if incoming_start >= existing_end:
        return existing_end, incoming_start
    if existing_start >= incoming_end:
        return incoming_end, existing_start
    return 0, 0


def merge_candidate_lines(
    lines: list[LineSegment],
    orientation: str,
    axis_tolerance: int = 14,
    max_gap: int = 38,
    binary: np.ndarray | None = None,
    max_empty_gap: int = 8,
    min_gap_coverage: float = 0.18,
) -> list[LineSegment]:
    ordered = sorted(lines, key=lambda line: (line_axis(line), line_span(line)[0]))
    merged: list[Box] = []

    for line in ordered:
        match_index: int | None = None
        start, end = line_span(line)
        for index, existing in enumerate(merged):
            existing_line = line_from_box(orientation, existing)
            existing_start, existing_end = line_span(existing_line)
            same_axis = abs(line_axis(existing_line) - line_axis(line)) <= axis_tolerance
            gap = max(start, existing_start) - min(end, existing_end)
            gap_supported = True
            if same_axis and binary is not None:
                gap_start, gap_end = line_gap_span(existing_line, line)
                axis = round((line_axis(existing_line) + line_axis(line)) / 2)
                gap_supported = gap_has_ink_support(
                    binary,
                    orientation,
                    axis,
                    gap_start,
                    gap_end,
                    max_empty_gap=max_empty_gap,
                    min_gap_coverage=min_gap_coverage,
                    half_width=5 if orientation == "v" else 4,
                )
            if same_axis and gap <= max_gap and gap_supported:
                match_index = index
                break

        if match_index is None:
            merged.append(line.box)
        else:
            existing = merged[match_index]
            box = line.box
            union = Box(min(existing.x0, box.x0), min(existing.y0, box.y0), max(existing.x1, box.x1), max(existing.y1, box.y1))
            if orientation == "v" and union.width - max(existing.width, box.width) > MERGE_SPINE_MIN_SPREAD:
                spine = existing if line_from_box(orientation, existing).length >= line.length else box
                union = Box(spine.x0, union.y0, spine.x1, union.y1)
            merged[match_index] = union

    return [line_from_box(orientation, box) for box in merged]


def detect_regions(width: int, height: int, h_lines: list[LineSegment], v_lines: list[LineSegment]) -> PageRegions:
    long_h = [line.box for line in h_lines if line.box.width >= width * 0.55]
    if not long_h:
        outer = Box(int(width * 0.03), int(height * 0.05), int(width * 0.95), int(height * 0.93))
    else:
        top = min([box for box in long_h if box.y0 <= height * 0.25] or long_h, key=lambda box: box.y0)
        bottom = max([box for box in long_h if box.y1 >= height * 0.70] or long_h, key=lambda box: box.y1)
        x0 = min(top.x0, bottom.x0)
        x1 = max(top.x1, bottom.x1)
        long_v = [line.box for line in v_lines if line.box.height >= height * 0.35]
        left_support = [box for box in long_v if abs(box.x0 - x0) <= width * 0.03]
        right_support = [box for box in long_v if abs(box.x1 - x1) <= width * 0.03]
        if left_support:
            x0 = min(x0, min(box.x0 for box in left_support))
        if right_support:
            x1 = max(x1, max(box.x1 for box in right_support))
        outer = Box(x0, top.y0, x1, bottom.y1)

    bottom_band_y = int(outer.y0 + outer.height * 0.78)
    return PageRegions(
        outer_frame=outer,
        drawing_area=Box(outer.x0, outer.y0, outer.x1, bottom_band_y),
        reference_table_area=Box(outer.x0, bottom_band_y, int(outer.x0 + outer.width * 0.30), outer.y1),
        title_block_area=Box(int(outer.x0 + outer.width * 0.74), bottom_band_y, outer.x1, outer.y1),
    )


def center_in_box(line: LineSegment, region: Box | None) -> bool:
    if region is None:
        return True
    x, y = line.box.center()
    return region.contains_point(x, y)


def is_near_outer_frame(line: LineSegment, regions: PageRegions, tolerance: int = 18) -> bool:
    outer = regions.outer_frame
    if outer is None:
        return False
    x, y = line.box.center()
    if line.orientation == "h":
        return abs(y - outer.y0) <= tolerance or abs(y - outer.y1) <= tolerance
    return abs(x - outer.x0) <= tolerance or abs(x - outer.x1) <= tolerance


def is_left_voltage_arrow(line: LineSegment, regions: PageRegions) -> bool:
    outer = regions.outer_frame
    drawing = regions.drawing_area
    if outer is None or drawing is None:
        return False
    x, _ = line.box.center()
    if line.orientation == "v":
        return x <= outer.x0 + outer.width * 0.10 and line.box.y0 >= drawing.y0 + drawing.height * 0.08
    return line.box.x1 <= outer.x0 + outer.width * 0.10 + 30 and line.length <= 180


def is_right_reference_block_line(line: LineSegment, regions: PageRegions) -> bool:
    outer = regions.outer_frame
    drawing = regions.drawing_area
    if outer is None or drawing is None:
        return False
    x, y = line.box.center()
    return x >= outer.x0 + outer.width * 0.77 and y >= drawing.y0 + drawing.height * 0.45 and line.length <= 380


def build_initial_wire_candidates(
    binary: np.ndarray,
    regions: PageRegions,
    width: int,
    height: int,
) -> tuple[list[LineSegment], list[LineSegment], list[LineSegment]]:
    h_mask = line_mask(binary, "h")
    v_mask = line_mask(binary, "v")
    # Keep raw scans and vertical recovery unchanged; correct horizontal-mask geometry only.
    h_lines = filter_line_segments(scan_line_segments(h_mask, "h", 18, isolate_components=True))
    v_lines = filter_line_segments(scan_line_segments(v_mask, "v", 18))

    def keep(line: LineSegment) -> bool:
        return (
            center_in_box(line, regions.drawing_area)
            and not is_near_outer_frame(line, regions)
            and not is_left_voltage_arrow(line, regions)
            and not is_right_reference_block_line(line, regions)
        )

    h_lines = [line for line in h_lines if keep(line)]
    v_lines = [line for line in v_lines if keep(line)]
    h_lines = split_lines_by_ink_support(
        binary,
        h_lines,
        min_length=45,
        max_empty_run=8,
        half_width=4,
        min_visible_coverage=0.42,
    )
    v_lines = split_lines_by_ink_support(
        binary,
        v_lines,
        min_length=45,
        max_empty_run=10,
        half_width=5,
        min_visible_coverage=0.38,
    )
    raw_v_lines = scan_line_segments(binary, "v", 18)
    restored_v_lines = restore_vertical_columns([*raw_v_lines, *v_lines], binary, regions, width, height)
    final_v_lines = merge_candidate_lines(
        [*v_lines, *restored_v_lines],
        "v",
        axis_tolerance=12,
        max_gap=82,
        binary=binary,
        max_empty_gap=8,
        min_gap_coverage=0.16,
    )
    final_h_lines = merge_candidate_lines(
        h_lines,
        "h",
        axis_tolerance=10,
        max_gap=38,
        binary=binary,
        max_empty_gap=6,
        min_gap_coverage=0.20,
    )
    final_v_lines = split_lines_by_ink_support(
        binary,
        final_v_lines,
        min_length=45,
        max_empty_run=10,
        half_width=5,
        min_visible_coverage=0.38,
    )
    final_h_lines = split_lines_by_ink_support(
        binary,
        final_h_lines,
        min_length=45,
        max_empty_run=8,
        half_width=4,
        min_visible_coverage=0.42,
    )
    return final_h_lines, final_v_lines, restored_v_lines


def restore_vertical_columns(
    v_lines: list[LineSegment],
    binary: np.ndarray,
    regions: PageRegions,
    image_width: int,
    image_height: int,
    axis_tolerance: int = 10,
    max_gap: int = 82,
) -> list[LineSegment]:
    candidates: list[LineSegment] = []
    for line in v_lines:
        box = line.box
        cx, cy = box.center()
        if regions.drawing_area is not None and not regions.drawing_area.contains_point(cx, cy):
            continue
        if is_near_outer_frame(line, regions) or is_left_voltage_arrow(line, regions) or is_right_reference_block_line(line, regions):
            continue
        if box.width <= 18 and box.height >= 18:
            candidates.append(line)

    clusters: list[list[LineSegment]] = []
    for line in sorted(candidates, key=lambda item: (item.box.center()[0], item.box.y0)):
        axis = line_axis(line)
        matched_index: int | None = None
        for index, cluster in enumerate(clusters):
            cluster_axes = [line_axis(item) for item in cluster]
            cluster_axis = round(sum(cluster_axes) / len(cluster_axes))
            cluster_x0 = min(item.box.x0 for item in cluster)
            cluster_x1 = max(item.box.x1 for item in cluster)
            if abs(axis - cluster_axis) <= axis_tolerance and max(cluster_x1, line.box.x1) - min(cluster_x0, line.box.x0) <= 28:
                matched_index = index
                break
        if matched_index is None:
            clusters.append([line])
        else:
            clusters[matched_index].append(line)

    restored: list[LineSegment] = []
    for cluster in clusters:
        spans = sorted((line.box.y0, line.box.y1) for line in cluster)
        merged_spans: list[tuple[int, int, int]] = []
        axis = round(sum(line_axis(line) for line in cluster) / len(cluster))
        start, end = spans[0]
        count = 1
        for next_start, next_end in spans[1:]:
            gap_supported = gap_has_ink_support(
                binary,
                "v",
                axis,
                end,
                next_start,
                max_empty_gap=8,
                min_gap_coverage=0.16,
                half_width=5,
            )
            if next_start - end <= max_gap and gap_supported:
                end = max(end, next_end)
                count += 1
            else:
                merged_spans.append((start, end, count))
                start, end, count = next_start, next_end, 1
        merged_spans.append((start, end, count))

        x0 = max(0, min(min(line.box.x0 for line in cluster), axis - 3))
        x1 = min(image_width, max(max(line.box.x1 for line in cluster), axis + 3))
        for start, end, count in merged_spans:
            length = end - start
            if length < 65:
                continue
            if count < 2 and length < 120:
                continue
            restored.append(LineSegment("v", Box(x0, max(0, start), x1, min(image_height, end))))

    return merge_candidate_lines(
        restored,
        "v",
        axis_tolerance=axis_tolerance,
        max_gap=max_gap,
        binary=binary,
        max_empty_gap=8,
        min_gap_coverage=0.16,
    )


def count_line_connections(line: LineSegment, h_lines: list[LineSegment], v_lines: list[LineSegment]) -> int:
    if line.orientation == "h":
        return sum(1 for other in v_lines if line_intersection(line, other) is not None)
    return sum(1 for other in h_lines if line_intersection(other, line) is not None)


def corner_ends(h_line: LineSegment, v_lines: list[LineSegment], tolerance: int = 8) -> int:
    """How many ends of a horizontal line meet the end of a vertical line (a corner of a drawn box)."""
    y = line_axis(h_line)
    ends = 0
    for x in (h_line.box.x0, h_line.box.x1):
        if any(abs(line_axis(v) - x) <= tolerance and min(abs(v.box.y0 - y), abs(v.box.y1 - y)) <= tolerance for v in v_lines):
            ends += 1
    return ends


def remove_line_pixels_from_binary(binary: np.ndarray, h_lines: list[LineSegment], v_lines: list[LineSegment]) -> np.ndarray:
    line_pixels = np.zeros_like(binary)
    for line in [*h_lines, *v_lines]:
        box = line.box.padded(4, binary.shape[1], binary.shape[0])
        cv2.rectangle(line_pixels, (box.x0, box.y0), (box.x1, box.y1), 255, thickness=-1)
    remaining = binary.copy()
    remaining[line_pixels > 0] = 0
    return remaining


def detect_text_regions_from_components(binary_without_lines: np.ndarray, regions: PageRegions) -> list[TextRegion]:
    height, width = binary_without_lines.shape
    component_count, component_labels, stats, centroids = cv2.connectedComponentsWithStats(
        (binary_without_lines > 0).astype(np.uint8),
        connectivity=8,
    )
    text_pixels = np.zeros_like(binary_without_lines)

    for component_id in range(1, component_count):
        x, y, w, h, area = (int(value) for value in stats[component_id])
        if area < 5 or area > 1800:
            continue
        if w < 2 or h < 3 or w > 95 or h > 58:
            continue
        if w >= 45 and h <= 3:
            continue
        if h >= 45 and w <= 3:
            continue
        if max(w / max(1, h), h / max(1, w)) > 18:
            continue
        cx, cy = centroids[component_id]
        if regions.drawing_area is not None and not regions.drawing_area.contains_point(float(cx), float(cy)):
            continue
        text_pixels[component_labels == component_id] = 255

    if not np.any(text_pixels):
        return []

    horizontal_join = cv2.getStructuringElement(cv2.MORPH_RECT, (23, 5))
    grouped = cv2.dilate(text_pixels, horizontal_join, iterations=1)
    grouped = cv2.morphologyEx(grouped, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_RECT, (5, 3)), iterations=1)
    group_count, group_labels, group_stats, _group_centroids = cv2.connectedComponentsWithStats(
        (grouped > 0).astype(np.uint8),
        connectivity=8,
    )

    text_regions: list[TextRegion] = []
    for group_id in range(1, group_count):
        x, y, w, h, _area = (int(value) for value in group_stats[group_id])
        box = Box(x, y, x + w, y + h).padded(2, width, height)
        cx, cy = box.center()
        if regions.drawing_area is not None and not regions.drawing_area.contains_point(cx, cy):
            continue
        if box.width < 8 or box.height < 5 or box.width > 260 or box.height > 70:
            continue
        if box.width >= 180 and box.height <= 9:
            continue
        ink_count = int(np.count_nonzero(text_pixels[group_labels == group_id]))
        if ink_count < 6:
            continue
        ink_density = ink_count / max(1, box.area)
        if ink_density > 0.72:
            continue
        confidence = min(0.95, 0.25 + min(0.5, ink_count / 160.0) + min(0.2, box.width / 220.0))
        text_regions.append(
            TextRegion(
                id=f"text_region_{len(text_regions) + 1:03d}",
                box=box,
                confidence=round(confidence, 3),
                reason="component_text_candidate",
            )
        )
    return text_regions


def line_label_overlap_ratio(line: LineSegment, labels: list[DetectedLabel], padding: int = 3) -> float:
    spans: list[tuple[int, int]] = []
    axis = line_axis(line)
    start, end = line_span(line)
    for label in labels:
        if not label.accepted:
            continue
        box = label.box.padded(padding, 10_000, 10_000)
        if line.orientation == "h":
            if box.y0 <= axis <= box.y1:
                overlap = _overlap(start, end, box.x0, box.x1)
                if overlap:
                    spans.append((max(start, box.x0), min(end, box.x1)))
        else:
            if box.x0 <= axis <= box.x1:
                overlap = _overlap(start, end, box.y0, box.y1)
                if overlap:
                    spans.append((max(start, box.y0), min(end, box.y1)))
    return merged_span_length(spans) / max(1, line.length)


def line_text_region_overlap_ratio(line: LineSegment, text_regions: list[TextRegion], padding: int = 3) -> float:
    spans: list[tuple[int, int]] = []
    axis = line_axis(line)
    start, end = line_span(line)
    for region in text_regions:
        box = region.box.padded(padding, 10_000, 10_000)
        if line.orientation == "h":
            if box.y0 <= axis <= box.y1:
                overlap = _overlap(start, end, box.x0, box.x1)
                if overlap:
                    spans.append((max(start, box.x0), min(end, box.x1)))
        else:
            if box.x0 <= axis <= box.x1:
                overlap = _overlap(start, end, box.y0, box.y1)
                if overlap:
                    spans.append((max(start, box.y0), min(end, box.y1)))
    return merged_span_length(spans) / max(1, line.length)


def line_box_distance(line: LineSegment, box: Box) -> float:
    line_box = line.box
    dx = max(line_box.x0 - box.x1, box.x0 - line_box.x1, 0)
    dy = max(line_box.y0 - box.y1, box.y0 - line_box.y1, 0)
    return (dx * dx + dy * dy) ** 0.5


def nearby_label_ids_for_line(line: LineSegment, labels: list[DetectedLabel], max_distance: int = 45) -> list[str]:
    nearby = [label for label in labels if label.accepted and line_box_distance(line, label.box) <= max_distance]
    nearby.sort(key=lambda label: (line_box_distance(line, label.box), -label.confidence))
    return [label.id for label in nearby[:8]]


def build_detected_wires(
    binary: np.ndarray,
    h_lines: list[LineSegment],
    v_lines: list[LineSegment],
    labels: list[DetectedLabel],
    regions: PageRegions,
    text_regions: list[TextRegion] | None = None,
) -> list[DetectedWire]:
    merged_h = merge_candidate_lines(
        h_lines,
        "h",
        axis_tolerance=10,
        max_gap=38,
        binary=binary,
        max_empty_gap=6,
        min_gap_coverage=0.20,
    )
    merged_v = merge_candidate_lines(
        v_lines,
        "v",
        axis_tolerance=16,
        max_gap=82,
        binary=binary,
        max_empty_gap=8,
        min_gap_coverage=0.16,
    )
    detected: list[DetectedWire] = []

    for line in [*merged_h, *merged_v]:
        cx, cy = line.box.center()
        if regions.drawing_area is not None and not regions.drawing_area.contains_point(cx, cy):
            continue

        label_overlap = line_label_overlap_ratio(line, labels)
        text_overlap = line_text_region_overlap_ratio(line, text_regions or [])
        connection_count = count_line_connections(line, merged_h, merged_v)
        visible_coverage = line_ink_coverage(binary, line, half_width=5 if line.orientation == "v" else 4)
        accept = False
        confidence = 0.0
        reason = "rejected"

        if line.orientation == "h":
            medium_text_threshold = 0.22 if line.length < 260 else 0.48
            if line.length >= 500 and label_overlap <= 0.25 and text_overlap <= 0.75 and visible_coverage >= 0.35:
                accept, confidence, reason = True, 0.9, "long_horizontal_bus"
            elif line.length >= 130 and connection_count >= 2 and label_overlap <= 0.28 and text_overlap <= medium_text_threshold and visible_coverage >= 0.38:
                accept, confidence, reason = True, 0.72, "connected_horizontal_wire"
            elif line.length >= 80 and connection_count >= 1 and label_overlap <= 0.12 and text_overlap <= 0.10 and visible_coverage >= 0.45:
                accept, confidence, reason = True, 0.55, "short_horizontal_branch"
            elif (line.length >= 40 and label_overlap <= 0.12 and text_overlap <= 0.10 and visible_coverage >= 0.9
                  and corner_ends(line, merged_v) == 2):
                # The top of a box drawn around a contact, joining the contact line to the return line (ER21282).
                accept, confidence, reason = True, 0.5, "short_corner_link"
        else:
            if line.length >= 130 and label_overlap <= 0.78 and text_overlap <= 0.90 and visible_coverage >= 0.35:
                accept, confidence, reason = True, 0.78 if connection_count else 0.62, "long_vertical_wire"
            elif line.length >= 70 and connection_count >= 1 and label_overlap <= 0.25 and text_overlap <= 0.45 and visible_coverage >= 0.42:
                accept, confidence, reason = True, 0.56, "connected_vertical_drop"

        if not accept:
            continue

        span_start, span_end = line_span(line)
        detected.append(
            DetectedWire(
                id=f"wire_{len(detected) + 1:03d}",
                orientation=line.orientation,
                box=line.box,
                axis=line_axis(line),
                span_start=span_start,
                span_end=span_end,
                length=line.length,
                confidence=round(confidence, 3),
                visible_coverage=round(visible_coverage, 3),
                reason=reason,
                nearby_label_ids=nearby_label_ids_for_line(line, labels),
            )
        )
    return detected


DEFAULT_WIRE_TEXT_MASK = "components"


@dataclass(frozen=True)
class WiringDetection:
    """1 ページの配線の検出結果。ラベル（OCR）は本番の検出では使わないので無い。"""

    regions: PageRegions
    image_size: dict[str, int]
    wires: list[DetectedWire]
    text_regions: list[TextRegion]
    graph: dict
    candidate_counts: dict[str, int]


def detect_wiring(image: Image.Image) -> WiringDetection:
    """線の候補を拾い、文字の塊（連結成分）と重なる線を除いて配線を選び、交点・端点のグラフを作る。"""
    binary = make_binary_mask(image)
    width, height = image.size
    base_h = scan_line_segments(binary, "h", max(36, width // 100))
    base_v = scan_line_segments(binary, "v", max(32, height // 90))
    regions = detect_regions(width, height, base_h, base_v)
    h_lines, v_lines, restored_v_lines = build_initial_wire_candidates(binary, regions, width, height)
    text_regions = detect_text_regions_from_components(remove_line_pixels_from_binary(binary, h_lines, v_lines), regions)
    wires = build_detected_wires(binary, h_lines, v_lines, [], regions, text_regions=text_regions)
    return WiringDetection(
        regions=regions, image_size={"width": width, "height": height}, wires=wires, text_regions=text_regions,
        graph=build_connection_graph(wires, []),
        candidate_counts={"horizontal": len(h_lines), "vertical": len(v_lines), "restored_vertical": len(restored_v_lines)},
    )

