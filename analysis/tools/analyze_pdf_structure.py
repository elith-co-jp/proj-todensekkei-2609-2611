from __future__ import annotations

import argparse
import json
import os
import re
import warnings
from collections import Counter
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable

import cv2
import numpy as np
import pymupdf
from PIL import Image, ImageDraw, ImageFont, ImageOps


DEFAULT_PDF_PATH = Path("private/inputs/reference/reference.pdf")
DEFAULT_OUT_DIR = Path("private/results/pdf_structure/pdf_structure")
PDF_ENV_VAR = "TODENSEKKEI_REFERENCE_PDF"


@dataclass(frozen=True)
class Box:
    x0: int
    y0: int
    x1: int
    y1: int

    @property
    def width(self) -> int:
        return self.x1 - self.x0

    @property
    def height(self) -> int:
        return self.y1 - self.y0

    @property
    def area(self) -> int:
        return max(0, self.width) * max(0, self.height)

    def center(self) -> tuple[float, float]:
        return ((self.x0 + self.x1) / 2, (self.y0 + self.y1) / 2)

    def padded(self, px: int, image_width: int, image_height: int) -> "Box":
        return Box(
            max(0, self.x0 - px),
            max(0, self.y0 - px),
            min(image_width, self.x1 + px),
            min(image_height, self.y1 + px),
        )

    def contains_point(self, x: float, y: float) -> bool:
        return self.x0 <= x <= self.x1 and self.y0 <= y <= self.y1

    def intersection_area(self, other: "Box") -> int:
        x0 = max(self.x0, other.x0)
        y0 = max(self.y0, other.y0)
        x1 = min(self.x1, other.x1)
        y1 = min(self.y1, other.y1)
        return max(0, x1 - x0) * max(0, y1 - y0)


@dataclass(frozen=True)
class LineSegment:
    orientation: str
    box: Box

    @property
    def length(self) -> int:
        return self.box.width if self.orientation == "h" else self.box.height


@dataclass(frozen=True)
class PageRegions:
    outer_frame: Box | None
    drawing_area: Box | None
    reference_table_area: Box | None
    title_block_area: Box | None


@dataclass(frozen=True)
class OcrResult:
    box: Box
    text: str
    confidence: float
    source: str


@dataclass(frozen=True)
class DetectedLabel:
    id: str
    raw_text: str
    normalized_text: str
    label_type: str
    confidence: float
    source: str
    box: Box
    accepted: bool
    reason: str


@dataclass(frozen=True)
class DetectedWire:
    id: str
    orientation: str
    box: Box
    axis: int
    span_start: int
    span_end: int
    length: int
    confidence: float
    visible_coverage: float
    reason: str
    nearby_label_ids: list[str]


@dataclass(frozen=True)
class TextRegion:
    id: str
    box: Box
    confidence: float
    reason: str


def default_pdf_path() -> Path:
    if env_path := os.environ.get(PDF_ENV_VAR):
        return Path(env_path)
    return DEFAULT_PDF_PATH


def render_page(pdf_path: Path, page_number: int, dpi: int) -> Image.Image:
    with pymupdf.open(pdf_path) as doc:
        if page_number < 1 or page_number > doc.page_count:
            raise ValueError(f"page {page_number} is outside PDF page range 1-{doc.page_count}")
        page = doc[page_number - 1]
        pix = page.get_pixmap(matrix=pymupdf.Matrix(dpi / 72, dpi / 72), alpha=False)
        mode = "RGB" if pix.n >= 3 else "L"
        return Image.frombytes(mode, (pix.width, pix.height), pix.samples)


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


def cv_mask_to_pil(mask: np.ndarray) -> Image.Image:
    return Image.fromarray(mask.astype(np.uint8), mode="L")


def _overlap(start_a: int, end_a: int, start_b: int, end_b: int) -> int:
    return max(0, min(end_a, end_b) - max(start_a, start_b))


def _merge_parallel_boxes(boxes: Iterable[Box], orientation: str, gap: int = 2) -> list[Box]:
    ordered = sorted(boxes, key=lambda box: (box.y0, box.x0) if orientation == "h" else (box.x0, box.y0))
    active: list[Box] = []
    merged: list[Box] = []

    for box in ordered:
        next_active: list[Box] = []
        for existing in active:
            if orientation == "h" and existing.y1 + gap < box.y0:
                merged.append(existing)
            elif orientation == "v" and existing.x1 + gap < box.x0:
                merged.append(existing)
            else:
                next_active.append(existing)
        active = next_active

        match_index: int | None = None
        best_overlap = 0
        for index, existing in enumerate(active):
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
            active.append(box)
        else:
            existing = active[match_index]
            active[match_index] = Box(
                min(existing.x0, box.x0),
                min(existing.y0, box.y0),
                max(existing.x1, box.x1),
                max(existing.y1, box.y1),
            )

    merged.extend(active)
    return merged


def scan_line_segments(mask: np.ndarray, orientation: str, min_length: int) -> list[LineSegment]:
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

    merged = _merge_parallel_boxes(candidates, orientation)
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
    for kernel_size, bridge_size in zip(kernels, bridge_kernels):
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


def line_axis(line: LineSegment) -> int:
    x, y = line.box.center()
    return round(y if line.orientation == "h" else x)


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
            merged[match_index] = Box(
                min(existing.x0, box.x0),
                min(existing.y0, box.y0),
                max(existing.x1, box.x1),
                max(existing.y1, box.y1),
            )

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
    h_lines = filter_line_segments(scan_line_segments(h_mask, "h", 18))
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


def line_intersection(h_line: LineSegment, v_line: LineSegment, tolerance: int = 18) -> tuple[int, int] | None:
    h = h_line.box
    v = v_line.box
    vx = round((v.x0 + v.x1) / 2)
    hy = round((h.y0 + h.y1) / 2)
    if h.x0 - tolerance <= vx <= h.x1 + tolerance and v.y0 - tolerance <= hy <= v.y1 + tolerance:
        return vx, hy
    return None


def count_line_connections(line: LineSegment, h_lines: list[LineSegment], v_lines: list[LineSegment]) -> int:
    if line.orientation == "h":
        return sum(1 for other in v_lines if line_intersection(line, other) is not None)
    return sum(1 for other in h_lines if line_intersection(other, line) is not None)


def remove_line_mask_from_binary(binary: np.ndarray, h_lines: list[LineSegment], v_lines: list[LineSegment]) -> Image.Image:
    remaining = remove_line_pixels_from_binary(binary, h_lines, v_lines)
    return Image.fromarray(255 - remaining.astype(np.uint8), mode="L").convert("RGB")


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


def detect_text_regions_from_ocr_results(ocr_results: list[OcrResult], width: int, height: int) -> list[TextRegion]:
    text_regions: list[TextRegion] = []
    for item in ocr_results:
        if item.confidence < 0.12:
            continue
        box = item.box.padded(3, width, height)
        if box.width < 4 or box.height < 4:
            continue
        text_regions.append(
            TextRegion(
                id=f"text_region_{len(text_regions) + 1:03d}",
                box=box,
                confidence=item.confidence,
                reason=f"ocr_{item.source}",
            )
        )
    return text_regions


def erase_text_regions_from_binary(binary: np.ndarray, text_regions: list[TextRegion], padding: int = 2) -> np.ndarray:
    if not text_regions:
        return binary
    height, width = binary.shape
    masked = binary.copy()
    for region in text_regions:
        box = region.box.padded(padding, width, height)
        masked[box.y0 : box.y1 + 1, box.x0 : box.x1 + 1] = 0
    return masked


def erase_text_pixels_from_binary(
    binary: np.ndarray,
    text_pixel_candidates: np.ndarray,
    text_regions: list[TextRegion],
    padding: int = 2,
) -> np.ndarray:
    if not text_regions:
        return binary
    height, width = binary.shape
    region_mask = np.zeros_like(binary)
    for region in text_regions:
        box = region.box.padded(padding, width, height)
        region_mask[box.y0 : box.y1 + 1, box.x0 : box.x1 + 1] = 255
    masked = binary.copy()
    masked[(region_mask > 0) & (text_pixel_candidates > 0)] = 0
    return masked


def detect_ocr_text_components(
    binary_without_lines: np.ndarray,
    ocr_results: list[OcrResult],
    width: int,
    height: int,
) -> tuple[np.ndarray, list[TextRegion]]:
    ocr_boxes = [
        item.box.padded(3, width, height)
        for item in ocr_results
        if item.confidence >= 0.12 and item.box.width >= 4 and item.box.height >= 4
    ]
    text_pixels = np.zeros_like(binary_without_lines)
    if not ocr_boxes:
        return text_pixels, []

    component_count, component_labels, stats, centroids = cv2.connectedComponentsWithStats(
        (binary_without_lines > 0).astype(np.uint8),
        connectivity=8,
    )
    text_regions: list[TextRegion] = []
    for component_id in range(1, component_count):
        x, y, w, h, area = (int(value) for value in stats[component_id])
        if area < 3 or area > 900:
            continue
        if w < 1 or h < 2 or w > 85 or h > 55:
            continue
        if w >= 45 and h <= 4:
            continue
        if h >= 45 and w <= 4:
            continue
        if max(w / max(1, h), h / max(1, w)) > 14:
            continue
        cx, cy = centroids[component_id]
        if not any(box.contains_point(float(cx), float(cy)) for box in ocr_boxes):
            continue
        text_pixels[component_labels == component_id] = 255
        box = Box(x, y, x + w, y + h).padded(1, width, height)
        text_regions.append(
            TextRegion(
                id=f"text_region_{len(text_regions) + 1:03d}",
                box=box,
                confidence=0.75,
                reason="ocr_text_component",
            )
        )

    if np.any(text_pixels):
        text_pixels = cv2.dilate(text_pixels, cv2.getStructuringElement(cv2.MORPH_RECT, (2, 2)), iterations=1)
    return text_pixels, text_regions


def detect_ocr_box_ink_text_pixels(
    binary: np.ndarray,
    ocr_results: list[OcrResult],
    width: int,
    height: int,
) -> tuple[np.ndarray, list[TextRegion]]:
    text_pixels = np.zeros_like(binary)
    text_regions: list[TextRegion] = []
    for item in ocr_results:
        if item.confidence < 0.12 or item.box.width < 4 or item.box.height < 4:
            continue
        ocr_box = item.box.padded(2, width, height)
        crop = (binary[ocr_box.y0 : ocr_box.y1 + 1, ocr_box.x0 : ocr_box.x1 + 1] > 0).astype(np.uint8)
        if crop.size == 0:
            continue
        component_count, component_labels, stats, _centroids = cv2.connectedComponentsWithStats(crop, connectivity=8)
        for component_id in range(1, component_count):
            x, y, w, h, area = (int(value) for value in stats[component_id])
            if area < 2 or area > 1600:
                continue
            aspect = max(w / max(1, h), h / max(1, w))
            long_horizontal_line = w >= 70 and h <= 6 and aspect >= 12
            long_vertical_line = h >= 70 and w <= 6 and aspect >= 12
            if long_horizontal_line or long_vertical_line:
                continue
            if w > 120 or h > 80:
                continue
            gx0 = ocr_box.x0 + x
            gy0 = ocr_box.y0 + y
            gx1 = gx0 + w
            gy1 = gy0 + h
            component_mask = component_labels[y : y + h, x : x + w] == component_id
            text_pixels[gy0:gy1, gx0:gx1][component_mask] = 255
            text_regions.append(
                TextRegion(
                    id=f"text_region_{len(text_regions) + 1:03d}",
                    box=Box(gx0, gy0, gx1, gy1).padded(1, width, height),
                    confidence=min(0.95, max(0.2, item.confidence)),
                    reason="ocr_box_ink_text_component",
                )
            )

    if np.any(text_pixels):
        text_pixels = cv2.dilate(text_pixels, cv2.getStructuringElement(cv2.MORPH_RECT, (2, 2)), iterations=1)
    return text_pixels, text_regions


def box_from_ocr_points(points: Iterable[Iterable[float]]) -> Box:
    xs: list[int] = []
    ys: list[int] = []
    for point in points:
        x, y = point
        xs.append(round(float(x)))
        ys.append(round(float(y)))
    return Box(min(xs), min(ys), max(xs), max(ys))


def box_iou(a: Box, b: Box) -> float:
    intersection = a.intersection_area(b)
    if intersection == 0:
        return 0.0
    return intersection / max(1, a.area + b.area - intersection)


def merge_ocr_results(results: list[OcrResult]) -> list[OcrResult]:
    ordered = sorted(results, key=lambda item: (-item.confidence, item.box.y0, item.box.x0))
    merged: list[OcrResult] = []
    for item in ordered:
        if any(box_iou(item.box, existing.box) >= 0.55 for existing in merged):
            continue
        merged.append(item)
    return sorted(merged, key=lambda item: (item.box.y0, item.box.x0, -item.confidence))


def run_easyocr(
    image: Image.Image,
    line_removed_input: Image.Image,
    regions: PageRegions,
    enabled: bool,
) -> tuple[list[OcrResult], str]:
    if not enabled:
        return [], "disabled"
    try:
        os.environ.setdefault("CUDA_VISIBLE_DEVICES", "")
        import easyocr  # type: ignore[import-not-found]
    except ModuleNotFoundError:
        return [], "easyocr_not_installed"

    warning_filters = (
        "torch.quantize_per_tensor.*",
        "CUDA initialization.*",
        "'pin_memory' argument is set as true.*",
    )
    try:
        with warnings.catch_warnings():
            for warning_filter in warning_filters:
                warnings.filterwarnings("ignore", message=warning_filter)
            reader = easyocr.Reader(["ja", "en"], gpu=False, verbose=False)
    except Exception as exc:
        return [], f"easyocr_reader_failed: {exc}"

    results: list[OcrResult] = []
    for source_name, source_image in (("original", image.convert("RGB")), ("line_removed", line_removed_input)):
        try:
            with warnings.catch_warnings():
                for warning_filter in warning_filters:
                    warnings.filterwarnings("ignore", message=warning_filter)
                ocr_items = reader.readtext(
                    np.array(source_image),
                    detail=1,
                    paragraph=False,
                    text_threshold=0.45,
                    low_text=0.25,
                    link_threshold=0.35,
                    mag_ratio=1.0,
                )
        except Exception as exc:
            return results, f"easyocr_read_failed_{source_name}: {exc}"

        for points, text, confidence in ocr_items:
            normalized = str(text).strip()
            if not normalized:
                continue
            box = box_from_ocr_points(points)
            cx, cy = box.center()
            if regions.drawing_area is not None and not regions.drawing_area.contains_point(cx, cy):
                continue
            if box.area < 24 or float(confidence) < 0.12:
                continue
            results.append(OcrResult(box, normalized, round(float(confidence), 4), source_name))
    return merge_ocr_results(results), "ok"


def normalize_ocr_text(text: str) -> str:
    normalized = text.strip().upper()
    normalized = normalized.translate(
        str.maketrans(
            {
                "（": "(",
                "）": ")",
                "〔": "(",
                "〕": ")",
                "[": "(",
                "]": ")",
                "{": "(",
                "}": ")",
                "ー": "-",
                "―": "-",
                "−": "-",
                " ": "",
                "　": "",
            }
        )
    )
    normalized = re.sub(r"^[`'\"|:;,.、。]+|[`'\"|:;,.、。]+$", "", normalized)
    replacements = {
        "CN3G": "CN36",
        "CW3G": "CN36",
        "CNSJ": "CN53",
        "CM53": "CN53",
        "TBG": "TB6",
        "1B16": "TB16",
        "6U9": "609",
        "699": "609",
        "LDC": "LOC",
        "2B-ZRCVDDC": "2B-2RCVDDC",
        "?-ZRSW": "2B-2RSAM",
    }
    return replacements.get(normalized, normalized)


def classify_label_text(text: str) -> str:
    if re.search(r"[\u3040-\u30ff\u3400-\u9fff]", text):
        return "non_diagram_text"
    if re.fullmatch(r"\(?ER\d+\)?", text):
        return "sheet_reference"
    if re.fullmatch(r"(CN|TB)\d+[A-Z]?", text):
        return "connector_or_terminal_block"
    if re.fullmatch(r"\d{1,3}", text):
        return "terminal_number"
    if re.fullmatch(r"\d{2,3}[A-Z]{1,4}", text):
        return "device_code"
    if "FV" in text or re.fullmatch(r"\d{1,3}[A-Z]{1,4}\d{2,5}", text):
        return "equipment_label"
    if text == "LOC":
        return "location_label"
    if re.fullmatch(r"[A-Z0-9]+[-/()][A-Z0-9-/()]+", text):
        return "diagram_label"
    if re.fullmatch(r"[A-Z0-9]{2,12}", text):
        return "diagram_label"
    return "non_diagram_text"


def build_detected_labels(ocr_results: list[OcrResult]) -> list[DetectedLabel]:
    labels: list[DetectedLabel] = []
    for index, item in enumerate(ocr_results, start=1):
        normalized = normalize_ocr_text(item.text)
        label_type = classify_label_text(normalized)
        accepted = label_type != "non_diagram_text" and (item.confidence >= 0.18 or len(normalized) >= 3)
        labels.append(
            DetectedLabel(
                id=f"label_{index:03d}",
                raw_text=item.text,
                normalized_text=normalized,
                label_type=label_type,
                confidence=item.confidence,
                source=item.source,
                box=item.box,
                accepted=accepted,
                reason="diagram_label_pattern" if accepted else "not_diagram_label",
            )
        )
    return labels


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


def cluster_points(points: list[tuple[int, int]], tolerance: int = 12) -> list[tuple[int, int]]:
    clusters: list[list[tuple[int, int]]] = []
    for point in points:
        for cluster in clusters:
            cx = sum(item[0] for item in cluster) / len(cluster)
            cy = sum(item[1] for item in cluster) / len(cluster)
            if abs(point[0] - cx) <= tolerance and abs(point[1] - cy) <= tolerance:
                cluster.append(point)
                break
        else:
            clusters.append([point])
    return [
        (round(sum(item[0] for item in cluster) / len(cluster)), round(sum(item[1] for item in cluster) / len(cluster)))
        for cluster in clusters
    ]


def point_on_line(point: tuple[int, int], line: LineSegment, tolerance: int = 16) -> bool:
    x, y = point
    box = line.box
    if line.orientation == "h":
        return abs(y - line_axis(line)) <= tolerance and box.x0 - tolerance <= x <= box.x1 + tolerance
    return abs(x - line_axis(line)) <= tolerance and box.y0 - tolerance <= y <= box.y1 + tolerance


def build_connection_graph(wires: list[DetectedWire], labels: list[DetectedLabel]) -> dict:
    h_wires = [wire for wire in wires if wire.orientation == "h"]
    v_wires = [wire for wire in wires if wire.orientation == "v"]
    node_points: list[tuple[int, int]] = []
    intersection_keys: set[tuple[int, int]] = set()

    for wire in wires:
        if wire.orientation == "h":
            points = [(wire.span_start, wire.axis), (wire.span_end, wire.axis)]
        else:
            points = [(wire.axis, wire.span_start), (wire.axis, wire.span_end)]
        node_points.extend(points)

    for h_wire in h_wires:
        h_line = LineSegment(h_wire.orientation, h_wire.box)
        for v_wire in v_wires:
            v_line = LineSegment(v_wire.orientation, v_wire.box)
            point = line_intersection(h_line, v_line)
            if point is None:
                continue
            intersection_keys.add((round(point[0] / 12), round(point[1] / 12)))
            node_points.append(point)

    nodes: list[dict] = []
    for index, point in enumerate(cluster_points(node_points), start=1):
        key = (round(point[0] / 12), round(point[1] / 12))
        nearby_labels = [
            label.id
            for label in labels
            if label.accepted and ((label.box.center()[0] - point[0]) ** 2 + (label.box.center()[1] - point[1]) ** 2) ** 0.5 <= 48
        ][:6]
        nodes.append(
            {
                "id": f"node_{index:03d}",
                "type": "junction" if key in intersection_keys else "endpoint",
                "x": point[0],
                "y": point[1],
                "nearby_label_ids": nearby_labels,
            }
        )

    def nearest_node_id(point: tuple[int, int]) -> str:
        nearest = min(nodes, key=lambda node: (node["x"] - point[0]) ** 2 + (node["y"] - point[1]) ** 2)
        return str(nearest["id"])

    edges: list[dict] = []
    for wire in wires:
        line = LineSegment(wire.orientation, wire.box)
        points = [(node["x"], node["y"]) for node in nodes if point_on_line((node["x"], node["y"]), line)]
        points = cluster_points(points, tolerance=8)
        points = sorted(points, key=lambda point: point[0] if wire.orientation == "h" else point[1])
        for start, end in zip(points, points[1:]):
            length = abs(end[0] - start[0]) + abs(end[1] - start[1])
            if length < 12:
                continue
            edges.append(
                {
                    "id": f"edge_{len(edges) + 1:03d}",
                    "wire_id": wire.id,
                    "from_node_id": nearest_node_id(start),
                    "to_node_id": nearest_node_id(end),
                    "length": length,
                }
            )

    return {"status": "prototype", "nodes": nodes, "edges": edges}


def load_font(size: int) -> ImageFont.ImageFont:
    for font_name in ("DejaVuSans.ttf", "Arial.ttf"):
        try:
            return ImageFont.truetype(font_name, size=size)
        except OSError:
            continue
    return ImageFont.load_default()


def draw_text_badge(
    draw: ImageDraw.ImageDraw,
    xy: tuple[int, int],
    text: str,
    font: ImageFont.ImageFont,
    fill: tuple[int, int, int],
) -> None:
    x, y = xy
    bbox = draw.textbbox((x, y), text, font=font)
    draw.rectangle((bbox[0] - 3, bbox[1] - 2, bbox[2] + 3, bbox[3] + 2), fill=fill)
    draw.text((x, y), text, font=font, fill=(255, 255, 255))


def draw_detection_review(
    image: Image.Image,
    wires: list[DetectedWire],
    labels: list[DetectedLabel],
    graph: dict,
) -> Image.Image:
    base = image.convert("RGBA")
    overlay = Image.alpha_composite(base, Image.new("RGBA", base.size, (255, 255, 255, 70)))
    draw = ImageDraw.Draw(overlay)
    small_font = load_font(13)
    wire_font = load_font(15)
    header_font = load_font(26)

    accepted_labels = [label for label in labels if label.accepted]
    for label in accepted_labels:
        box = label.box
        draw.rectangle((box.x0, box.y0, box.x1, box.y1), outline=(0, 120, 70, 220), width=2)
        draw_text_badge(draw, (box.x0, max(0, box.y0 - 16)), label.normalized_text[:18], small_font, (0, 120, 70, 210))

    for wire in wires:
        if wire.orientation == "h":
            start = (wire.span_start, wire.axis)
            end = (wire.span_end, wire.axis)
        else:
            start = (wire.axis, wire.span_start)
            end = (wire.axis, wire.span_end)
        width = 8 if wire.confidence >= 0.75 else 6 if wire.confidence >= 0.6 else 4
        color = (220, 40, 35, 235) if wire.confidence >= 0.75 else (235, 120, 30, 225)
        draw.line((start, end), fill=color, width=width)
        draw_text_badge(
            draw,
            (round((start[0] + end[0]) / 2) + 5, round((start[1] + end[1]) / 2) - 18),
            wire.id.replace("wire_", "W"),
            wire_font,
            (30, 30, 30, 225),
        )

    for node in graph["nodes"]:
        x = int(node["x"])
        y = int(node["y"])
        radius = 7 if node["type"] == "junction" else 5
        draw.ellipse((x - radius, y - radius, x + radius, y + radius), fill=(0, 95, 220, 240), outline=(255, 255, 255, 255), width=2)

    header_height = 78
    canvas = Image.new("RGB", (overlay.width, overlay.height + header_height), (248, 248, 248))
    header_draw = ImageDraw.Draw(canvas)
    header_draw.text(
        (24, 16),
        f"Final extraction review  Wires:{len(wires)}  Labels:{len(accepted_labels)}  Nodes:{len(graph['nodes'])}  Edges:{len(graph['edges'])}",
        font=header_font,
        fill=(25, 25, 25),
    )
    header_draw.text(
        (24, 50),
        "Only accepted wires/labels/nodes are shown. Confirm missing real wires and included non-wires.",
        font=small_font,
        fill=(80, 80, 80),
    )
    canvas.paste(overlay.convert("RGB"), (0, header_height))
    return canvas


def write_json(path: Path, payload: dict | list) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def analyze_page(
    pdf_path: Path,
    page_number: int,
    dpi: int,
    out_dir: Path,
    run_ocr: bool,
    wire_text_mask: str = "none",
) -> dict:
    page_dir = out_dir / f"page_{page_number:03d}"
    page_dir.mkdir(parents=True, exist_ok=True)
    image = render_page(pdf_path, page_number, dpi)
    binary = make_binary_mask(image)
    width, height = image.size
    base_h = scan_line_segments(binary, "h", max(36, width // 100))
    base_v = scan_line_segments(binary, "v", max(32, height // 90))
    regions = detect_regions(width, height, base_h, base_v)
    h_lines, v_lines, restored_v_lines = build_initial_wire_candidates(binary, regions, width, height)

    line_removed_binary = remove_line_pixels_from_binary(binary, h_lines, v_lines)
    line_removed_input = remove_line_mask_from_binary(binary, h_lines, v_lines)
    ocr_results, ocr_status = run_easyocr(
        image,
        line_removed_input,
        regions,
        enabled=run_ocr
        or wire_text_mask
        in {
            "ocr",
            "components_ocr",
            "ocr_erase",
            "components_ocr_erase",
            "ocr_pixel_erase",
            "components_ocr_pixel_erase",
            "ocr_component_erase",
            "components_ocr_component_erase",
            "ocr_box_ink_erase",
            "components_ocr_box_ink_erase",
        },
    )
    if wire_text_mask == "components":
        text_regions = detect_text_regions_from_components(line_removed_binary, regions)
    elif wire_text_mask == "ocr":
        text_regions = detect_text_regions_from_ocr_results(ocr_results, width, height)
    elif wire_text_mask == "components_ocr":
        component_regions = detect_text_regions_from_components(line_removed_binary, regions)
        ocr_regions = detect_text_regions_from_ocr_results(ocr_results, width, height)
        text_regions = [
            TextRegion(
                id=f"text_region_{index:03d}",
                box=region.box,
                confidence=region.confidence,
                reason=region.reason,
            )
            for index, region in enumerate([*component_regions, *ocr_regions], start=1)
        ]
    elif wire_text_mask == "ocr_erase":
        text_regions = detect_text_regions_from_ocr_results(ocr_results, width, height)
        binary = erase_text_regions_from_binary(binary, text_regions)
        h_lines, v_lines, restored_v_lines = build_initial_wire_candidates(binary, regions, width, height)
    elif wire_text_mask == "components_ocr_erase":
        component_regions = detect_text_regions_from_components(line_removed_binary, regions)
        ocr_regions = detect_text_regions_from_ocr_results(ocr_results, width, height)
        text_regions = [
            TextRegion(
                id=f"text_region_{index:03d}",
                box=region.box,
                confidence=region.confidence,
                reason=region.reason,
            )
            for index, region in enumerate([*component_regions, *ocr_regions], start=1)
        ]
        binary = erase_text_regions_from_binary(binary, text_regions)
        h_lines, v_lines, restored_v_lines = build_initial_wire_candidates(binary, regions, width, height)
    elif wire_text_mask == "ocr_pixel_erase":
        text_regions = detect_text_regions_from_ocr_results(ocr_results, width, height)
        binary = erase_text_pixels_from_binary(binary, line_removed_binary, text_regions)
        h_lines, v_lines, restored_v_lines = build_initial_wire_candidates(binary, regions, width, height)
    elif wire_text_mask == "components_ocr_pixel_erase":
        component_regions = detect_text_regions_from_components(line_removed_binary, regions)
        ocr_regions = detect_text_regions_from_ocr_results(ocr_results, width, height)
        text_regions = [
            TextRegion(
                id=f"text_region_{index:03d}",
                box=region.box,
                confidence=region.confidence,
                reason=region.reason,
            )
            for index, region in enumerate([*component_regions, *ocr_regions], start=1)
        ]
        binary = erase_text_pixels_from_binary(binary, line_removed_binary, text_regions)
        h_lines, v_lines, restored_v_lines = build_initial_wire_candidates(binary, regions, width, height)
    elif wire_text_mask == "ocr_component_erase":
        text_pixel_mask, text_regions = detect_ocr_text_components(line_removed_binary, ocr_results, width, height)
        binary = erase_text_pixels_from_binary(binary, text_pixel_mask, text_regions)
        h_lines, v_lines, restored_v_lines = build_initial_wire_candidates(binary, regions, width, height)
    elif wire_text_mask == "components_ocr_component_erase":
        component_regions = detect_text_regions_from_components(line_removed_binary, regions)
        text_pixel_mask, ocr_component_regions = detect_ocr_text_components(line_removed_binary, ocr_results, width, height)
        text_regions = [
            TextRegion(
                id=f"text_region_{index:03d}",
                box=region.box,
                confidence=region.confidence,
                reason=region.reason,
            )
            for index, region in enumerate([*component_regions, *ocr_component_regions], start=1)
        ]
        binary = erase_text_pixels_from_binary(binary, text_pixel_mask, ocr_component_regions)
        h_lines, v_lines, restored_v_lines = build_initial_wire_candidates(binary, regions, width, height)
    elif wire_text_mask == "ocr_box_ink_erase":
        text_pixel_mask, text_regions = detect_ocr_box_ink_text_pixels(binary, ocr_results, width, height)
        binary = erase_text_pixels_from_binary(binary, text_pixel_mask, text_regions)
        h_lines, v_lines, restored_v_lines = build_initial_wire_candidates(binary, regions, width, height)
    elif wire_text_mask == "components_ocr_box_ink_erase":
        component_regions = detect_text_regions_from_components(line_removed_binary, regions)
        text_pixel_mask, ocr_box_regions = detect_ocr_box_ink_text_pixels(binary, ocr_results, width, height)
        text_regions = [
            TextRegion(
                id=f"text_region_{index:03d}",
                box=region.box,
                confidence=region.confidence,
                reason=region.reason,
            )
            for index, region in enumerate([*component_regions, *ocr_box_regions], start=1)
        ]
        binary = erase_text_pixels_from_binary(binary, text_pixel_mask, ocr_box_regions)
        h_lines, v_lines, restored_v_lines = build_initial_wire_candidates(binary, regions, width, height)
    else:
        text_regions = []
    labels = build_detected_labels(ocr_results)
    wires = build_detected_wires(binary, h_lines, v_lines, labels, regions, text_regions=text_regions)
    graph = build_connection_graph(wires, labels)

    accepted_labels = [label for label in labels if label.accepted]
    common = {
        "page": page_number,
        "dpi": dpi,
        "image_size": {"width": width, "height": height},
        "coordinate_unit": "rendered_pixel",
        "status": "prototype",
        "regions": asdict(regions),
    }
    write_json(
        page_dir / "detected_wires.json",
        {
            **common,
            "wire_count": len(wires),
            "horizontal_wire_count": sum(wire.orientation == "h" for wire in wires),
            "vertical_wire_count": sum(wire.orientation == "v" for wire in wires),
            "wires": [asdict(wire) for wire in wires],
        },
    )
    write_json(
        page_dir / "detected_labels.json",
        {
            **common,
            "label_count": len(accepted_labels),
            "raw_ocr_result_count": len(labels),
            "labels": [asdict(label) for label in accepted_labels],
            "rejected_labels": [asdict(label) for label in labels if not label.accepted],
        },
    )
    write_json(
        page_dir / "detected_text_regions.json",
        {
            **common,
            "wire_text_mask": wire_text_mask,
            "text_region_count": len(text_regions),
            "text_regions": [asdict(region) for region in text_regions],
        },
    )
    write_json(
        page_dir / "connection_graph.json",
        {
            **common,
            "wire_count": len(wires),
            "label_count": len(accepted_labels),
            "node_count": len(graph["nodes"]),
            "edge_count": len(graph["edges"]),
            "wires": [asdict(wire) for wire in wires],
            "labels": [asdict(label) for label in accepted_labels],
            "text_regions": [asdict(region) for region in text_regions],
            "nodes": graph["nodes"],
            "edges": graph["edges"],
        },
    )
    write_json(page_dir / "ocr_results.json", [asdict(item) for item in ocr_results])
    (page_dir / "ocr_results.tsv").write_text(
        "\n".join(
            ["index\tsource\tconfidence\tx0\ty0\tx1\ty1\ttext"]
            + [
                f"{index}\t{item.source}\t{item.confidence:.4f}\t{item.box.x0}\t{item.box.y0}\t{item.box.x1}\t{item.box.y1}\t{item.text.replace(chr(9), ' ').replace(chr(10), ' ')}"
                for index, item in enumerate(ocr_results, start=1)
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    image.save(page_dir / "base.png")
    line_removed_input.save(page_dir / "ocr_line_removed_input.png")
    draw_detection_review(image, wires, labels, graph).save(page_dir / "final_extraction_review.png")

    summary = {
        **common,
        "pdf": str(pdf_path),
        "pdf_size_bytes": pdf_path.stat().st_size,
        "pdf_mtime_ns": pdf_path.stat().st_mtime_ns,
        "ocr_requested": run_ocr,
        "horizontal_candidate_count": len(h_lines),
        "vertical_candidate_count": len(v_lines),
        "restored_vertical_segment_count": len(restored_v_lines),
        "ocr_status": ocr_status,
        "wire_text_mask": wire_text_mask,
        "detected_text_region_count": len(text_regions),
        "ocr_result_count": len(ocr_results),
        "detected_wire_count": len(wires),
        "detected_horizontal_wire_count": sum(wire.orientation == "h" for wire in wires),
        "detected_vertical_wire_count": sum(wire.orientation == "v" for wire in wires),
        "detected_label_count": len(accepted_labels),
        "connection_graph_node_count": len(graph["nodes"]),
        "connection_graph_edge_count": len(graph["edges"]),
        "wire_visible_coverage_min": round(min((wire.visible_coverage for wire in wires), default=0.0), 3),
        "wire_visible_coverage_avg": round(
            sum(wire.visible_coverage for wire in wires) / max(1, len(wires)),
            3,
        ),
        "wire_reason_counts": dict(Counter(wire.reason for wire in wires)),
        "label_type_counts": dict(Counter(label.label_type for label in accepted_labels)),
        "outputs": {
            "final_extraction_review": str(page_dir / "final_extraction_review.png"),
            "detected_wires": str(page_dir / "detected_wires.json"),
            "detected_labels": str(page_dir / "detected_labels.json"),
            "detected_text_regions": str(page_dir / "detected_text_regions.json"),
            "connection_graph": str(page_dir / "connection_graph.json"),
            "summary": str(page_dir / "summary.json"),
        },
    }
    write_json(page_dir / "summary.json", summary)
    return summary


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Analyze scanned sequence drawing PDF and create review artifacts.")
    parser.add_argument("--pdf", type=Path, default=default_pdf_path())
    parser.add_argument("--page", type=int, default=1)
    parser.add_argument("--dpi", type=int, default=200)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
    parser.add_argument("--ocr", choices=("none", "easyocr"), default="none")
    parser.add_argument(
        "--wire-text-mask",
        choices=(
            "none",
            "components",
            "ocr",
            "components_ocr",
            "ocr_erase",
            "components_ocr_erase",
            "ocr_pixel_erase",
            "components_ocr_pixel_erase",
            "ocr_component_erase",
            "components_ocr_component_erase",
            "ocr_box_ink_erase",
            "components_ocr_box_ink_erase",
        ),
        default="none",
    )
    parser.add_argument("--print", choices=("summary", "full", "none"), default="summary")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if not args.pdf.exists():
        raise SystemExit(
            f"PDF not found: {args.pdf}. Pass --pdf or set {PDF_ENV_VAR}; "
            "do not commit private PDFs or generated artifacts."
        )
    summary = analyze_page(
        args.pdf,
        args.page,
        args.dpi,
        args.out_dir,
        run_ocr=args.ocr == "easyocr",
        wire_text_mask=args.wire_text_mask,
    )
    if args.print == "none":
        return
    if args.print == "full":
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return
    concise = {
        "pdf": summary["pdf"],
        "page": summary["page"],
        "dpi": summary["dpi"],
        "ocr_status": summary["ocr_status"],
        "wire_text_mask": summary["wire_text_mask"],
        "detected_text_region_count": summary["detected_text_region_count"],
        "detected_wire_count": summary["detected_wire_count"],
        "detected_horizontal_wire_count": summary["detected_horizontal_wire_count"],
        "detected_vertical_wire_count": summary["detected_vertical_wire_count"],
        "detected_label_count": summary["detected_label_count"],
        "connection_graph_node_count": summary["connection_graph_node_count"],
        "connection_graph_edge_count": summary["connection_graph_edge_count"],
        "outputs": summary["outputs"],
    }
    print(json.dumps(concise, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
