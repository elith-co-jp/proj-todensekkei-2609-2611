from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont


PDF_ENV_VAR = "TODENSEKKEI_REFERENCE_PDF"
DEFAULT_PDF_PATH = Path("private/inputs/reference/reference.pdf")
DEFAULT_ANALYSIS_ROOT = Path("private/results/pdf_structure/pdf_structure")
DEFAULT_OUT_DIR = Path("private/results/e2e_demo")


@dataclass(frozen=True)
class DemoPaths:
    final_json: Path
    source_image: Path
    reconstructed: Path
    reconstructed_no_label_boxes: Path
    overlay: Path
    overlay_no_label_boxes: Path
    wire_support_diff: Path
    comparison_sheet: Path
    comparison_sheet_highres: Path
    comparison_sheet_no_label_boxes: Path
    comparison_sheet_no_label_boxes_highres: Path


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


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


FONT_XS = load_font(12)
FONT_SM = load_font(15)
FONT_MD = load_font(18)
FONT_LG = load_font(26)


def box_tuple(box: dict[str, int]) -> tuple[int, int, int, int]:
    return int(box["x0"]), int(box["y0"]), int(box["x1"]), int(box["y1"])


def label_text(label: dict[str, Any]) -> str:
    return str(label.get("text") or label.get("normalized_text") or label.get("raw_text") or label["id"])


def wire_points(wire: dict[str, Any]) -> tuple[tuple[int, int], tuple[int, int]]:
    if "polyline" in wire:
        start, end = wire["polyline"][0], wire["polyline"][-1]
        return (round(start[0]), round(start[1])), (round(end[0]), round(end[1]))
    if wire["orientation"] == "h":
        return (int(wire["span_start"]), int(wire["axis"])), (int(wire["span_end"]), int(wire["axis"]))
    return (int(wire["axis"]), int(wire["span_start"])), (int(wire["axis"]), int(wire["span_end"]))


def build_connection_candidates(graph: dict[str, Any], labels: list[dict[str, Any]]) -> list[dict[str, Any]]:
    labels_by_id = {label["id"]: label for label in labels}
    nodes_by_id = {node["id"]: node for node in graph.get("nodes", [])}
    wires_by_id = {wire["id"]: wire for wire in graph.get("wires", [])}
    connections: list[dict[str, Any]] = []

    for edge in graph.get("edges", []):
        from_node = nodes_by_id.get(edge["from_node_id"], {})
        to_node = nodes_by_id.get(edge["to_node_id"], {})
        wire = wires_by_id.get(edge.get("wire_id"), {})

        from_label_ids = list(from_node.get("nearby_label_ids", []))
        to_label_ids = list(to_node.get("nearby_label_ids", []))
        from_labels = [label_text(labels_by_id[label_id]) for label_id in from_label_ids if label_id in labels_by_id]
        to_labels = [label_text(labels_by_id[label_id]) for label_id in to_label_ids if label_id in labels_by_id]
        has_endpoint_labels = bool(from_labels and to_labels)
        confidence = float(wire.get("confidence", 0.4))
        if has_endpoint_labels:
            confidence = min(0.98, confidence + 0.08)
        else:
            confidence = max(0.25, confidence - 0.18)

        connections.append(
            {
                "id": f"connection_{len(connections) + 1:04d}",
                "type": "candidate_connection" if has_endpoint_labels else "geometry_edge",
                "from_node_id": edge["from_node_id"],
                "to_node_id": edge["to_node_id"],
                "wire_id": edge.get("wire_id"),
                "from_label_ids": from_label_ids,
                "to_label_ids": to_label_ids,
                "from_labels": from_labels,
                "to_labels": to_labels,
                "length": edge.get("length"),
                "confidence": round(confidence, 3),
                "status": "prototype",
            }
        )
    return connections


def build_final_output_from_analysis(page_dir: Path, source_type: str) -> dict[str, Any]:
    labels_json = load_json(page_dir / "detected_labels.json")
    wires_json = load_json(page_dir / "detected_wires.json")
    graph_json = load_json(page_dir / "connection_graph.json")
    summary = load_json(page_dir / "summary.json")

    labels = [
        {
            "id": label["id"],
            "text": label["normalized_text"],
            "raw_text": label["raw_text"],
            "type": label["label_type"],
            "bbox": label["box"],
            "confidence": label["confidence"],
            "source": label["source"],
            "status": "detected",
        }
        for label in labels_json.get("labels", [])
    ]
    wires = [
        {
            "id": wire["id"],
            "orientation": wire["orientation"],
            "bbox": wire["box"],
            "polyline": [list(wire_points(wire)[0]), list(wire_points(wire)[1])],
            "axis": wire["axis"],
            "length": wire["length"],
            "confidence": wire["confidence"],
            "visible_coverage": wire.get("visible_coverage"),
            "reason": wire["reason"],
            "nearby_label_ids": wire.get("nearby_label_ids", []),
            "status": "detected",
        }
        for wire in wires_json.get("wires", [])
    ]
    graph_for_connections = {**graph_json, "wires": wires}
    connections = build_connection_candidates(graph_for_connections, labels)

    return {
        "schema_version": "todensekkei.sequence_extraction.demo.v1",
        "source": {
            "type": source_type,
            "page": summary["page"],
            "dpi": summary["dpi"],
            "base_image": str(page_dir / "base.png"),
            "analysis_dir": str(page_dir),
        },
        "status": "prototype",
        "image_size": summary["image_size"],
        "coordinate_unit": summary["coordinate_unit"],
        "regions": summary.get("regions", {}),
        "symbols": [],
        "labels": labels,
        "wires": wires,
        "nodes": graph_json.get("nodes", []),
        "edges": graph_json.get("edges", []),
        "connections": connections,
        "quality": {
            "wire_count": len(wires),
            "horizontal_wire_count": sum(wire["orientation"] == "h" for wire in wires),
            "vertical_wire_count": sum(wire["orientation"] == "v" for wire in wires),
            "label_count": len(labels),
            "node_count": len(graph_json.get("nodes", [])),
            "edge_count": len(graph_json.get("edges", [])),
            "connection_count": len(connections),
            "candidate_connection_count": sum(item["type"] == "candidate_connection" for item in connections),
            "wire_visible_coverage_min": summary.get("wire_visible_coverage_min"),
            "wire_visible_coverage_avg": summary.get("wire_visible_coverage_avg"),
            "ocr_status": summary.get("ocr_status"),
        },
        "notes": [
            "symbols is empty for validation PDF because real symbol classes and trained detector are pending.",
            "connections are prototype graph candidates, not domain-approved from/to records.",
            "wire_visible_coverage compares detected wire spans against source image ink support.",
        ],
    }


def draw_reconstruction(
    payload: dict[str, Any],
    *,
    semantic_colors: bool = True,
    label_style: str = "boxed",
) -> Image.Image:
    width = int(payload["image_size"]["width"])
    height = int(payload["image_size"]["height"])
    image = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(image)
    if label_style not in {"boxed", "text", "none"}:
        raise ValueError("label_style must be one of: boxed, text, none")

    regions = payload.get("regions", {})
    drawing_area = regions.get("drawing_area")
    if drawing_area:
        draw.rectangle(box_tuple(drawing_area), outline=(230, 230, 230), width=2)

    for wire in payload.get("wires", []):
        start, end = wire_points(wire)
        if semantic_colors:
            color = (210, 35, 30) if float(wire.get("confidence", 0)) >= 0.72 else (230, 130, 30)
            line_width = 8 if float(wire.get("confidence", 0)) >= 0.72 else 5
        else:
            color = (20, 20, 20)
            line_width = 4
        draw.line((start, end), fill=color, width=line_width)

    for symbol in payload.get("symbols", []):
        box = symbol.get("bbox")
        symbol_type = symbol.get("type", "symbol")
        if box:
            draw.rectangle(box_tuple(box), outline=(110, 60, 180), width=3)
            draw.text((box["x0"], max(0, box["y0"] - 16)), symbol_type, fill=(110, 60, 180), font=FONT_XS)
        center = symbol.get("center")
        if center and symbol_type in {"black_terminal", "junction_dot"}:
            x, y = round(center[0]), round(center[1])
            draw.ellipse((x - 8, y - 8, x + 8, y + 8), fill=(30, 30, 30))
        elif center and symbol_type == "open_terminal":
            x, y = round(center[0]), round(center[1])
            draw.ellipse((x - 9, y - 9, x + 9, y + 9), outline=(30, 30, 30), width=3)

    for node in payload.get("nodes", []):
        x = int(node["x"])
        y = int(node["y"])
        r = 6 if node.get("type") == "junction" else 4
        draw.ellipse((x - r, y - r, x + r, y + r), fill=(0, 95, 220), outline="white", width=1)

    for label in payload.get("labels", []):
        if label_style == "none":
            continue
        box = label.get("bbox")
        if not box:
            continue
        x0, y0, x1, y1 = box_tuple(box)
        text = label_text(label)[:22]
        if label_style == "boxed":
            draw.rectangle((x0, y0, x1, y1), outline=(0, 135, 70), width=2)
            text_y = max(0, y0 - 15)
            draw.rectangle((x0, text_y, x0 + max(24, len(text) * 8), text_y + 14), fill=(0, 135, 70))
            draw.text((x0 + 2, text_y), text, fill="white", font=FONT_XS)
        else:
            text_size = max(11, min(20, round((y1 - y0) * 0.85)))
            draw.text((x0, y0), text, fill=(0, 95, 45), font=load_font(text_size))

    return image


def draw_overlay(source: Image.Image, reconstructed: Image.Image) -> Image.Image:
    base = source.convert("RGB")
    faded = Image.blend(base, Image.new("RGB", base.size, "white"), 0.35)
    colored = reconstructed.convert("RGBA")
    overlay = Image.new("RGBA", colored.size, (255, 255, 255, 0))
    pixels = np.array(colored)
    non_white = np.any(pixels[:, :, :3] < 245, axis=2)
    alpha = np.zeros(non_white.shape, dtype=np.uint8)
    alpha[non_white] = 210
    pixels[:, :, 3] = alpha
    overlay = Image.fromarray(pixels, mode="RGBA")
    return Image.alpha_composite(faded.convert("RGBA"), overlay).convert("RGB")


def wire_mask(payload: dict[str, Any], size: tuple[int, int], width: int = 7) -> np.ndarray:
    mask = np.zeros((size[1], size[0]), dtype=np.uint8)
    for wire in payload.get("wires", []):
        start, end = wire_points(wire)
        cv2.line(mask, start, end, 255, thickness=width)
    return mask


def source_ink_mask(source: Image.Image) -> np.ndarray:
    gray = np.array(source.convert("L"))
    blur = cv2.GaussianBlur(gray, (3, 3), 0)
    _threshold, mask = cv2.threshold(blur, 0, 255, cv2.THRESH_BINARY_INV | cv2.THRESH_OTSU)
    return mask


def draw_wire_support_diff(source: Image.Image, payload: dict[str, Any]) -> tuple[Image.Image, dict[str, float]]:
    source_rgb = source.convert("RGB")
    recon = wire_mask(payload, source_rgb.size, width=7)
    ink = source_ink_mask(source_rgb)
    ink_dilated = cv2.dilate(ink, cv2.getStructuringElement(cv2.MORPH_RECT, (7, 7)), iterations=1)
    recon_dilated = cv2.dilate(recon, cv2.getStructuringElement(cv2.MORPH_RECT, (9, 9)), iterations=1)

    matched = (recon > 0) & (ink_dilated > 0)
    unsupported_recon = (recon > 0) & ~matched
    source_near_recon = (ink > 0) & (recon_dilated > 0) & ~(recon > 0)

    canvas = Image.blend(source_rgb, Image.new("RGB", source_rgb.size, "white"), 0.55)
    pixels = np.array(canvas)
    pixels[source_near_recon] = (70, 130, 230)
    pixels[unsupported_recon] = (230, 50, 45)
    pixels[matched] = (35, 170, 80)
    image = Image.fromarray(pixels)

    recon_pixels = int(np.count_nonzero(recon))
    matched_pixels = int(np.count_nonzero(matched))
    unsupported_pixels = int(np.count_nonzero(unsupported_recon))
    metrics = {
        "reconstructed_wire_pixels": recon_pixels,
        "matched_wire_pixels": matched_pixels,
        "unsupported_reconstructed_wire_pixels": unsupported_pixels,
        "wire_pixel_support_ratio": round(matched_pixels / max(1, recon_pixels), 4),
        "source_ink_near_reconstruction_pixels": int(np.count_nonzero(source_near_recon)),
    }
    return image, metrics


def resize_panel(image: Image.Image, target_w: int) -> Image.Image:
    scale = target_w / image.width
    resample = Image.Resampling.LANCZOS if hasattr(Image, "Resampling") else Image.LANCZOS
    return image.resize((target_w, round(image.height * scale)), resample)


def make_comparison_sheet(
    source: Image.Image,
    reconstructed: Image.Image,
    overlay: Image.Image,
    diff: Image.Image,
    *,
    target_w: int,
    reconstructed_title: str = "reconstructed_from_json",
) -> Image.Image:
    panels = [
        ("source", source.convert("RGB")),
        (reconstructed_title, reconstructed.convert("RGB")),
        ("overlay_on_source", overlay.convert("RGB")),
        ("wire_support_diff", diff.convert("RGB")),
    ]
    header_h = 38
    rendered: list[Image.Image] = []
    for title, image in panels:
        resized = resize_panel(image, target_w)
        panel = Image.new("RGB", (target_w, resized.height + header_h), "white")
        panel.paste(resized, (0, header_h))
        draw = ImageDraw.Draw(panel)
        draw.text((10, 8), title, fill=(25, 25, 25), font=FONT_MD)
        rendered.append(panel)

    cell_h = max(panel.height for panel in rendered)
    sheet = Image.new("RGB", (target_w * 2, cell_h * 2), (245, 245, 245))
    for index, panel in enumerate(rendered):
        x = (index % 2) * target_w
        y = (index // 2) * cell_h
        sheet.paste(panel, (x, y))
    return sheet


def write_visual_outputs(payload: dict[str, Any], source_image: Image.Image, out_dir: Path) -> DemoPaths:
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = DemoPaths(
        final_json=out_dir / "final_output.json",
        source_image=out_dir / "source.png",
        reconstructed=out_dir / "reconstructed_from_json.png",
        reconstructed_no_label_boxes=out_dir / "reconstructed_no_label_boxes.png",
        overlay=out_dir / "overlay_on_original.png",
        overlay_no_label_boxes=out_dir / "overlay_no_label_boxes.png",
        wire_support_diff=out_dir / "wire_support_diff.png",
        comparison_sheet=out_dir / "comparison_sheet.png",
        comparison_sheet_highres=out_dir / "comparison_sheet_highres.png",
        comparison_sheet_no_label_boxes=out_dir / "comparison_sheet_no_label_boxes.png",
        comparison_sheet_no_label_boxes_highres=out_dir / "comparison_sheet_no_label_boxes_highres.png",
    )
    reconstructed = draw_reconstruction(payload, label_style="boxed")
    reconstructed_no_boxes = draw_reconstruction(payload, label_style="text")
    overlay = draw_overlay(source_image, reconstructed)
    overlay_no_boxes = draw_overlay(source_image, reconstructed_no_boxes)
    diff, diff_metrics = draw_wire_support_diff(source_image, payload)
    sheet = make_comparison_sheet(
        source_image,
        reconstructed,
        overlay,
        diff,
        target_w=900,
        reconstructed_title="reconstructed_from_json",
    )
    sheet_highres = make_comparison_sheet(
        source_image,
        reconstructed,
        overlay,
        diff,
        target_w=source_image.width,
        reconstructed_title="reconstructed_from_json",
    )
    sheet_no_boxes = make_comparison_sheet(
        source_image,
        reconstructed_no_boxes,
        overlay_no_boxes,
        diff,
        target_w=900,
        reconstructed_title="reconstructed_no_label_boxes",
    )
    sheet_no_boxes_highres = make_comparison_sheet(
        source_image,
        reconstructed_no_boxes,
        overlay_no_boxes,
        diff,
        target_w=source_image.width,
        reconstructed_title="reconstructed_no_label_boxes",
    )

    payload = {
        **payload,
        "quality": {
            **payload.get("quality", {}),
            "wire_support_diff": diff_metrics,
        },
        "outputs": {
            "final_json": str(paths.final_json),
            "source_image": str(paths.source_image),
            "reconstructed_from_json": str(paths.reconstructed),
            "reconstructed_no_label_boxes": str(paths.reconstructed_no_label_boxes),
            "overlay_on_original": str(paths.overlay),
            "overlay_no_label_boxes": str(paths.overlay_no_label_boxes),
            "wire_support_diff": str(paths.wire_support_diff),
            "comparison_sheet": str(paths.comparison_sheet),
            "comparison_sheet_highres": str(paths.comparison_sheet_highres),
            "comparison_sheet_no_label_boxes": str(paths.comparison_sheet_no_label_boxes),
            "comparison_sheet_no_label_boxes_highres": str(paths.comparison_sheet_no_label_boxes_highres),
        },
    }
    write_json(paths.final_json, payload)
    source_image.save(paths.source_image)
    reconstructed.save(paths.reconstructed)
    reconstructed_no_boxes.save(paths.reconstructed_no_label_boxes)
    overlay.save(paths.overlay)
    overlay_no_boxes.save(paths.overlay_no_label_boxes)
    diff.save(paths.wire_support_diff)
    sheet.save(paths.comparison_sheet)
    sheet_highres.save(paths.comparison_sheet_highres)
    sheet_no_boxes.save(paths.comparison_sheet_no_label_boxes)
    sheet_no_boxes_highres.save(paths.comparison_sheet_no_label_boxes_highres)
    return paths


def make_synthetic_payload() -> dict[str, Any]:
    width, height = 1800, 1200
    wires = [
        ("wire_001", "h", (180, 180), (1580, 180), 0.99, "upper_bus"),
        ("wire_002", "h", (180, 940), (1580, 940), 0.99, "lower_bus"),
        ("wire_003", "h", (300, 420), (1360, 420), 0.94, "connected_horizontal_wire"),
        ("wire_004", "h", (300, 620), (1360, 620), 0.94, "connected_horizontal_wire"),
        ("wire_005", "h", (430, 760), (730, 760), 0.88, "short_horizontal_branch"),
        ("wire_006", "h", (980, 760), (1280, 760), 0.88, "short_horizontal_branch"),
        ("wire_007", "v", (360, 180), (360, 940), 0.96, "vertical_drop"),
        ("wire_008", "v", (620, 180), (620, 940), 0.96, "vertical_drop"),
        ("wire_009", "v", (880, 180), (880, 940), 0.96, "vertical_drop"),
        ("wire_010", "v", (1140, 180), (1140, 940), 0.96, "vertical_drop"),
        ("wire_011", "v", (1400, 180), (1400, 940), 0.96, "vertical_drop"),
    ]
    labels: list[dict[str, Any]] = []

    def add_label(text: str, x: int, y: int, label_type: str) -> None:
        labels.append(
            {
                "id": f"label_{len(labels) + 1:03d}",
                "text": text,
                "raw_text": text,
                "type": label_type,
                "bbox": {"x0": x, "y0": y, "x1": x + max(32, len(text) * 14), "y1": y + 26},
                "confidence": 1.0,
                "source": "synthetic_ground_truth",
                "status": "ground_truth",
            }
        )

    for x, tb, cn, n1, n2 in (
        (360, "TB-A", "CN-A", "101", "102"),
        (620, "TB-A", "CN-A", "103", "104"),
        (880, "TB-B", "CN-B", "105", "106"),
        (1140, "TB-B", "CN-B", "107", "108"),
        (1400, "TB-C", "CN-C", "109", "110"),
    ):
        add_label(tb, x - 45, 300, "connector_or_terminal_block")
        add_label(cn, x - 45, 330, "connector_or_terminal_block")
        add_label(n1, x + 18, 385, "terminal_number")
        add_label(n2, x + 18, 585, "terminal_number")
        add_label("13", x - 20, 850, "terminal_number")
    add_label("BUS001", 210, 145, "diagram_label")
    add_label("(SHEET-REF-001)", 210, 205, "sheet_reference")
    add_label("DEVICE-GROUP-001", 1220, 390, "diagram_label")
    add_label("PANEL-A", 1220, 585, "diagram_label")
    add_label("DEV-A", 842, 805, "device_code")

    nodes = []
    for x in (360, 620, 880, 1140, 1400):
        for y in (180, 420, 620, 940):
            nodes.append({"id": f"node_{len(nodes) + 1:03d}", "type": "junction", "x": x, "y": y, "nearby_label_ids": []})
    for x, y in ((180, 180), (1580, 180), (180, 940), (1580, 940), (430, 760), (730, 760), (980, 760), (1280, 760)):
        nodes.append({"id": f"node_{len(nodes) + 1:03d}", "type": "endpoint", "x": x, "y": y, "nearby_label_ids": []})

    symbols = []
    for node in nodes:
        if node["type"] == "junction":
            symbols.append(
                {
                    "id": f"symbol_{len(symbols) + 1:03d}",
                    "type": "junction_dot",
                    "class_name": "junction_dot",
                    "center": [node["x"], node["y"]],
                    "bbox": {"x0": node["x"] - 9, "y0": node["y"] - 9, "x1": node["x"] + 9, "y1": node["y"] + 9},
                    "confidence": 1.0,
                    "status": "ground_truth",
                }
            )

    wire_dicts = [
        {
            "id": wire_id,
            "orientation": orientation,
            "bbox": {
                "x0": min(start[0], end[0]) - 3,
                "y0": min(start[1], end[1]) - 3,
                "x1": max(start[0], end[0]) + 3,
                "y1": max(start[1], end[1]) + 3,
            },
            "polyline": [list(start), list(end)],
            "axis": start[1] if orientation == "h" else start[0],
            "length": abs(end[0] - start[0]) + abs(end[1] - start[1]),
            "confidence": confidence,
            "visible_coverage": 1.0,
            "reason": reason,
            "nearby_label_ids": [],
            "status": "ground_truth",
        }
        for wire_id, orientation, start, end, confidence, reason in wires
    ]

    edges = []
    for wire in wire_dicts:
        start, end = wire_points(wire)
        start_node = min(nodes, key=lambda node: (node["x"] - start[0]) ** 2 + (node["y"] - start[1]) ** 2)
        end_node = min(nodes, key=lambda node: (node["x"] - end[0]) ** 2 + (node["y"] - end[1]) ** 2)
        edges.append(
            {
                "id": f"edge_{len(edges) + 1:03d}",
                "wire_id": wire["id"],
                "from_node_id": start_node["id"],
                "to_node_id": end_node["id"],
                "length": wire["length"],
            }
        )

    graph_for_connections = {"wires": wire_dicts, "nodes": nodes, "edges": edges}
    return {
        "schema_version": "todensekkei.sequence_extraction.demo.v1",
        "source": {"type": "synthetic", "page": 1, "dpi": 200, "base_image": "source.png"},
        "status": "ground_truth_demo",
        "image_size": {"width": width, "height": height},
        "coordinate_unit": "rendered_pixel",
        "regions": {"drawing_area": {"x0": 90, "y0": 90, "x1": 1710, "y1": 1010}},
        "symbols": symbols,
        "labels": labels,
        "wires": wire_dicts,
        "nodes": nodes,
        "edges": edges,
        "connections": build_connection_candidates(graph_for_connections, labels),
        "quality": {
            "wire_count": len(wire_dicts),
            "horizontal_wire_count": sum(wire["orientation"] == "h" for wire in wire_dicts),
            "vertical_wire_count": sum(wire["orientation"] == "v" for wire in wire_dicts),
            "label_count": len(labels),
            "symbol_count": len(symbols),
            "node_count": len(nodes),
            "edge_count": len(edges),
            "connection_count": len(edges),
            "wire_visible_coverage_min": 1.0,
            "wire_visible_coverage_avg": 1.0,
        },
        "notes": [
            "This synthetic sample demonstrates the final JSON shape including symbols.",
            "It is not used for validation PDF OCR or wire extraction accuracy.",
        ],
    }


def draw_synthetic_source(payload: dict[str, Any]) -> Image.Image:
    image = draw_reconstruction(payload, semantic_colors=False, label_style="text")
    draw = ImageDraw.Draw(image)
    width = payload["image_size"]["width"]
    height = payload["image_size"]["height"]
    draw.rectangle((70, 70, width - 70, height - 70), outline=(30, 30, 30), width=3)
    draw.rectangle((1120, 1000, width - 90, height - 90), outline=(30, 30, 30), width=2)
    draw.text((1140, 1020), "synthetic reference block", fill=(30, 30, 30), font=FONT_MD)
    draw.text((90, height - 58), "ER-SYN-001", fill=(30, 30, 30), font=FONT_LG)
    return image


def ensure_analysis_outputs(pdf: Path, pages: list[int], root: Path, dpi: int) -> None:
    missing = [page for page in pages if not (root / f"page_{page:03d}" / "connection_graph.json").exists()]
    if not missing:
        return
    if not pdf.exists():
        raise SystemExit(
            f"Missing analysis outputs for pages {missing}, and PDF was not found: {pdf}. "
            f"Pass --pdf or set {PDF_ENV_VAR}."
        )
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from analyze_pdf_structure import analyze_page  # type: ignore[import-not-found]

    for page in missing:
        analyze_page(pdf, page, dpi, root, run_ocr=True)


def build_validation_demo(root: Path, out_dir: Path, pages: list[int]) -> list[dict[str, Any]]:
    summaries: list[dict[str, Any]] = []
    for page in pages:
        page_dir = root / f"page_{page:03d}"
        payload = build_final_output_from_analysis(page_dir, "validation_pdf")
        source = Image.open(page_dir / "base.png").convert("RGB")
        paths = write_visual_outputs(payload, source, out_dir / "validation_pdf" / f"page_{page:03d}")
        final_payload = load_json(paths.final_json)
        summaries.append(
            {
                "source_type": "validation_pdf",
                "page": page,
                "quality": final_payload["quality"],
                "outputs": final_payload["outputs"],
            }
        )
    return summaries


def build_synthetic_demo(out_dir: Path) -> dict[str, Any]:
    payload = make_synthetic_payload()
    source = draw_synthetic_source(payload)
    paths = write_visual_outputs(payload, source, out_dir / "synthetic" / "page_001")
    final_payload = load_json(paths.final_json)
    return {"source_type": "synthetic", "page": 1, "quality": final_payload["quality"], "outputs": final_payload["outputs"]}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build local E2E demo outputs from extraction JSON.")
    parser.add_argument("--pdf", type=Path, default=Path(os.environ.get(PDF_ENV_VAR, DEFAULT_PDF_PATH)))
    parser.add_argument("--analysis-root", type=Path, default=DEFAULT_ANALYSIS_ROOT)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
    parser.add_argument("--validation-pages", type=int, nargs="+", default=[1, 10, 29])
    parser.add_argument("--dpi", type=int, default=200)
    parser.add_argument("--regenerate-missing", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.regenerate_missing:
        ensure_analysis_outputs(args.pdf, args.validation_pages, args.analysis_root, args.dpi)
    for page in args.validation_pages:
        page_dir = args.analysis_root / f"page_{page:03d}"
        if not (page_dir / "connection_graph.json").exists():
            raise SystemExit(f"Missing analysis output: {page_dir}. Run analyze_pdf_structure.py first.")

    args.out_dir.mkdir(parents=True, exist_ok=True)
    summary = {
        "schema_version": "todensekkei.e2e_demo.summary.v1",
        "status": "prototype",
        "validation_pdf": build_validation_demo(args.analysis_root, args.out_dir, args.validation_pages),
        "synthetic": build_synthetic_demo(args.out_dir),
        "notes": [
            "Generated files are local demo artifacts and are ignored by git under data/.",
            "validation_pdf uses detected OCR/wire/graph outputs from the reference PDF.",
            "synthetic uses a small ground-truth JSON sample to show the symbols field and reconstruction behavior.",
        ],
    }
    write_json(args.out_dir / "demo_summary.json", summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
