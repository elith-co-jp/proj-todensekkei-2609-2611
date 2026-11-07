"""配線の検出の実験・デバッグ用（本番の解析は analysis.wiring.detect.detect_wiring）。

OCR（EasyOCR）で文字を見つけて消してから配線を選ぶ実験の分岐（wire_text_mask）、検出結果の確認画像、
検出結果をページのフォルダに JSON で書き出す analyze_image と、それを読み戻す build_final_output_from_analysis。
"""

from __future__ import annotations

import _repo_path  # noqa: F401  (リポジトリの直下を import できるようにする)

import json
import os
import re
import warnings
from collections import Counter
from collections.abc import Iterable
from dataclasses import asdict
from pathlib import Path
from typing import Any

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

from analysis.wiring.assemble import assemble_structure
from analysis.wiring.detect import (
    build_detected_wires,
    build_initial_wire_candidates,
    detect_regions,
    detect_text_regions_from_components,
    make_binary_mask,
    remove_line_pixels_from_binary,
    scan_line_segments,
)
from analysis.wiring.graph import build_connection_graph
from analysis.wiring.shapes import Box, DetectedLabel, DetectedWire, LineSegment, OcrResult, PageRegions, TextRegion


def remove_line_mask_from_binary(binary: np.ndarray, h_lines: list[LineSegment], v_lines: list[LineSegment]) -> Image.Image:
    remaining = remove_line_pixels_from_binary(binary, h_lines, v_lines)
    return Image.fromarray(255 - remaining.astype(np.uint8), mode="L").convert("RGB")


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


def analyze_image(
    image: Image.Image,
    page_number: int,
    dpi: int,
    out_dir: Path,
    run_ocr: bool,
    wire_text_mask: str = "none",
    *,
    source_name: str = "",
    write_debug_images: bool = True,
) -> dict:
    page_dir = out_dir / f"page_{page_number:03d}"
    page_dir.mkdir(parents=True, exist_ok=True)
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
    if write_debug_images:
        image.save(page_dir / "base.png")
        line_removed_input.save(page_dir / "ocr_line_removed_input.png")
        draw_detection_review(image, wires, labels, graph).save(page_dir / "final_extraction_review.png")

    summary = {
        **common,
        "pdf": source_name,
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



def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def build_final_output_from_analysis(page_dir: Path, source_type: str) -> dict[str, Any]:
    """analyze_image が書き出したページのフォルダから構造 JSON を組み立てる（本番と同じ assemble_structure）。"""
    graph_json = load_json(page_dir / "connection_graph.json")
    summary = load_json(page_dir / "summary.json")
    return assemble_structure(
        labels=load_json(page_dir / "detected_labels.json").get("labels", []),
        wires=load_json(page_dir / "detected_wires.json").get("wires", []),
        nodes=graph_json.get("nodes", []), edges=graph_json.get("edges", []),
        text_regions=graph_json.get("text_regions", []),
        regions=summary.get("regions", {}), image_size=summary["image_size"],
        coordinate_unit=summary["coordinate_unit"],
        ocr_status=summary.get("ocr_status"), wire_text_mask=summary.get("wire_text_mask", "none"),
        source={"type": source_type, "page": summary["page"], "dpi": summary["dpi"],
                "base_image": str(page_dir / "base.png"), "analysis_dir": str(page_dir)},
    )
