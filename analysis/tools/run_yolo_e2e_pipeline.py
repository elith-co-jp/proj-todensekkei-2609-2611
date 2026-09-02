from __future__ import annotations

import argparse
import json
import os
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw

from analyze_pdf_structure import analyze_page
from analyze_annotation_graph import load_zip_payload, symbol_bbox
from build_e2e_demo_outputs import (
    DEFAULT_ANALYSIS_ROOT,
    DEFAULT_PDF_PATH,
    PDF_ENV_VAR,
    build_final_output_from_analysis,
    draw_reconstruction,
    draw_wire_support_diff,
    load_json,
    load_font,
    wire_points,
    write_json,
    write_visual_outputs,
)
from evaluate_yolo_label_ocr import (
    OCR_ALLOWLIST,
    build_label_candidate,
    label_text_for_match,
    read_label,
)
from evaluate_label_word_rules import build_word_rule_label
from experiment_symbol_port_inference import (
    EXPLICIT_NODE_CLASSES,
    default_configs as default_port_inference_configs,
    infer_ports_for_symbol,
)
from prepare_yolo_dataset import suppress_text_like_components


DEFAULT_MODEL_PATH = Path("data/private/yolo/symbol_detection/runs/from_scratch_60e/weights/best.pt")
DEFAULT_OUT_DIR = Path("data/private/e2e_yolo")
FONT_SM = load_font(15)
FONT_MD = load_font(18)
DEFAULT_INFERRED_TERMINAL_CLASSES = {"contact_a", "contact_b", "solenoid", "relay_coil"}


def parse_pages(values: list[str]) -> list[int]:
    pages: set[int] = set()
    for value in values:
        for part in value.split(","):
            part = part.strip()
            if not part:
                continue
            if "-" in part:
                start, end = (int(item) for item in part.split("-", 1))
                if end < start:
                    raise ValueError(f"invalid page range: {part}")
                pages.update(range(start, end + 1))
            else:
                pages.add(int(part))
    return sorted(pages)


def ensure_analysis_outputs(pdf: Path, pages: list[int], analysis_root: Path, dpi: int, run_ocr: bool) -> None:
    for page in pages:
        page_dir = analysis_root / f"page_{page:03d}"
        if (page_dir / "connection_graph.json").exists() and (page_dir / "base.png").exists():
            continue
        analyze_page(pdf, page, dpi, analysis_root, run_ocr=run_ocr)


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


def box_area(box: dict[str, int | float]) -> float:
    return max(0.0, float(box["x1"]) - float(box["x0"])) * max(0.0, float(box["y1"]) - float(box["y0"]))


def box_intersection_area(a: dict[str, int | float], b: dict[str, int | float]) -> float:
    x0 = max(float(a["x0"]), float(b["x0"]))
    y0 = max(float(a["y0"]), float(b["y0"]))
    x1 = min(float(a["x1"]), float(b["x1"]))
    y1 = min(float(a["y1"]), float(b["y1"]))
    return max(0.0, x1 - x0) * max(0.0, y1 - y0)


def filter_symbols_by_label_overlap(
    symbols: list[dict[str, Any]],
    labels: list[dict[str, Any]],
    threshold: float,
) -> tuple[list[dict[str, Any]], int]:
    if threshold <= 0:
        return symbols, 0
    text_sensitive_classes = {"connector", "junction", "terminal"}
    filtered: list[dict[str, Any]] = []
    removed = 0
    label_boxes = [label["bbox"] for label in labels if label.get("bbox")]
    for symbol in symbols:
        class_name = str(symbol.get("class_name") or symbol.get("type"))
        if class_name not in text_sensitive_classes:
            filtered.append(symbol)
            continue
        symbol_box = symbol["bbox"]
        symbol_area = max(1.0, box_area(symbol_box))
        max_overlap = max((box_intersection_area(symbol_box, label_box) / symbol_area for label_box in label_boxes), default=0.0)
        if max_overlap >= threshold:
            removed += 1
            continue
        filtered.append(symbol)
    return filtered, removed


def min_overlap_ratio(a: dict[str, int | float], b: dict[str, int | float]) -> float:
    return box_intersection_area(a, b) / max(1.0, min(box_area(a), box_area(b)))


def box_iou(a: dict[str, int | float], b: dict[str, int | float]) -> float:
    intersection = box_intersection_area(a, b)
    union = box_area(a) + box_area(b) - intersection
    return intersection / max(1.0, union)


def parse_mapping(items: list[str]) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for item in items:
        if "=" not in item:
            raise ValueError(f"mapping must be KEY=VALUE: {item}")
        key, value = item.split("=", 1)
        key = key.strip()
        value = value.strip()
        if not key or not value:
            raise ValueError(f"mapping must be KEY=VALUE: {item}")
        mapping[key] = value
    return mapping


def load_annotation_projects(annotation_zip: Path | None) -> dict[str, dict[str, Any]]:
    if not annotation_zip:
        return {}
    bundle, _csv_rows, _netlist = load_zip_payload(annotation_zip)
    return {str(project["sheet_no"]): project for project in bundle.get("projects", [])}


def terminal_point(terminal: dict[str, Any], width: int, height: int) -> list[float]:
    return [round(float(terminal["tx"]) * width, 1), round(float(terminal["ty"]) * height, 1)]


def external_reference_target(label: str) -> str | None:
    match = re.fullmatch(r"\s*(ER\d+(?:-\d+)?)へ\s*", label)
    return match.group(1) if match else None


def parenthesized_sheet_refs(label: str) -> list[str]:
    return re.findall(r"\((ER\d+)\)", label)


def geometry_band(point: tuple[float, float], width: int, height: int) -> dict[str, str]:
    x, y = point
    horizontal = "left" if x < width * 0.25 else "right" if x > width * 0.75 else "middle"
    vertical = "top" if y < height * 0.4 else "bottom" if y > height * 0.6 else "middle"
    return {"side": horizontal, "vertical_band": vertical}


def build_external_reference_payload(
    project: dict[str, Any] | None,
    *,
    annotated_sheets: set[str],
    source: str,
) -> dict[str, Any]:
    if project is None or source == "none":
        return {
            "external_references": [],
            "external_reference_connections": [],
            "symbol_sheet_references": [],
            "external_reference_summary": {
                "source": source,
                "enabled": False,
                "external_reference_count": 0,
                "external_reference_connection_count": 0,
                "symbol_sheet_reference_count": 0,
            },
        }

    width = int(project["image_width"])
    height = int(project["image_height"])
    sheet_no = str(project["sheet_no"])
    symbols_by_ref = {str(symbol["ref"]): symbol for symbol in project.get("symbols", [])}
    external_refs_by_symbol: dict[str, dict[str, Any]] = {}
    symbol_sheet_refs: list[dict[str, Any]] = []

    for symbol in project.get("symbols", []):
        symbol_ref = str(symbol["ref"])
        class_name = str(symbol.get("class_key") or "")
        label = str(symbol.get("label") or "").strip()
        bbox = symbol_bbox(symbol, width, height)
        center_tuple = box_center(bbox)
        center = [round(center_tuple[0], 1), round(center_tuple[1], 1)]

        target_sheet = external_reference_target(label)
        if class_name == "other" and target_sheet:
            terminal_items = [
                {
                    "ref": terminal.get("ref"),
                    "name": terminal.get("name") or "",
                    "point": terminal_point(terminal, width, height),
                }
                for terminal in symbol.get("terminals") or []
            ]
            external_refs_by_symbol[symbol_ref] = {
                "id": f"external_ref_{sheet_no}_{symbol_ref}",
                "type": "external_reference",
                "source": "annotation_other_label",
                "current_sheet": sheet_no,
                "target_sheet": target_sheet,
                "target_sheet_available": target_sheet in annotated_sheets,
                "resolution_status": "target_sheet_available" if target_sheet in annotated_sheets else "target_sheet_missing",
                "symbol_ref": symbol_ref,
                "class_name": class_name,
                "label": label,
                "bbox": bbox,
                "center": center,
                **geometry_band(center_tuple, width, height),
                "terminals": terminal_items,
            }

        for referenced_sheet in parenthesized_sheet_refs(label):
            symbol_sheet_refs.append(
                {
                    "source": "annotation_symbol_label",
                    "current_sheet": sheet_no,
                    "symbol_ref": symbol_ref,
                    "class_name": class_name,
                    "label": label,
                    "referenced_sheet": referenced_sheet,
                    "referenced_sheet_available": referenced_sheet in annotated_sheets,
                    "interpretation": "symbol metadata, not a direct page-continuation edge",
                    "bbox": bbox,
                    "center": center,
                }
            )

    external_connections: list[dict[str, Any]] = []
    for index, connection in enumerate(project.get("connections", []), start=1):
        endpoint_specs = (
            ("from", str(connection.get("from_symbol_ref") or ""), connection.get("from_terminal_ref")),
            ("to", str(connection.get("to_symbol_ref") or ""), connection.get("to_terminal_ref")),
        )
        external_side = None
        local_side = None
        for spec in endpoint_specs:
            if spec[1] in external_refs_by_symbol:
                external_side = spec
            else:
                local_side = spec
        if external_side is None or local_side is None:
            continue

        _external_role, external_symbol_ref, external_terminal_ref = external_side
        local_role, local_symbol_ref, local_terminal_ref = local_side
        external_ref = external_refs_by_symbol[external_symbol_ref]
        local_symbol = symbols_by_ref.get(local_symbol_ref, {})
        external_connections.append(
            {
                "id": f"external_connection_{len(external_connections) + 1:04d}",
                "source": "annotation_connection_to_external_reference_symbol",
                "current_sheet": sheet_no,
                "annotation_connection_index": index,
                "external_reference_id": external_ref["id"],
                "target_sheet": external_ref["target_sheet"],
                "resolution_status": external_ref["resolution_status"],
                "external_endpoint": {
                    "symbol_ref": external_symbol_ref,
                    "terminal_ref": external_terminal_ref,
                    "label": external_ref["label"],
                },
                "local_endpoint": {
                    "symbol_ref": local_symbol_ref,
                    "terminal_ref": local_terminal_ref,
                    "class_name": local_symbol.get("class_key"),
                    "label": local_symbol.get("label") or "",
                    "role_in_annotation": local_role,
                },
                "kind": connection.get("kind") or "wire",
                "wire_no": connection.get("wire_no"),
                "note": connection.get("note"),
            }
        )

    return {
        "external_references": sorted(external_refs_by_symbol.values(), key=lambda item: str(item["id"])),
        "external_reference_connections": external_connections,
        "symbol_sheet_references": symbol_sheet_refs,
        "external_reference_summary": {
            "source": source,
            "enabled": True,
            "current_sheet": sheet_no,
            "external_reference_count": len(external_refs_by_symbol),
            "external_reference_connection_count": len(external_connections),
            "symbol_sheet_reference_count": len(symbol_sheet_refs),
            "target_sheet_counts": dict(
                sorted(Counter(item["target_sheet"] for item in external_refs_by_symbol.values()).items())
            ),
            "symbol_sheet_reference_counts": dict(
                sorted(Counter(item["referenced_sheet"] for item in symbol_sheet_refs).items())
            ),
        },
    }


def gold_symbol_to_pipeline_symbol(
    gold_symbol: dict[str, Any],
    *,
    project: dict[str, Any],
    status: str,
    source_prediction: dict[str, Any] | None = None,
) -> dict[str, Any]:
    width = int(project["image_width"])
    height = int(project["image_height"])
    bbox = symbol_bbox(gold_symbol, width, height)
    center = box_center(bbox)
    terminals = []
    for terminal in gold_symbol.get("terminals") or []:
        point = [round(float(terminal["tx"]) * width, 1), round(float(terminal["ty"]) * height, 1)]
        terminals.append(
            {
                "ref": terminal["ref"],
                "name": terminal.get("name") or "",
                "point": point,
            }
        )
    output = {
        "id": f"gold_{gold_symbol['ref']}",
        "type": gold_symbol["class_key"],
        "class_name": gold_symbol["class_key"],
        "class_id": gold_symbol.get("yolo_index"),
        "bbox": bbox,
        "center": [round(center[0], 1), round(center[1], 1)],
        "confidence": 1.0 if source_prediction is None else float(source_prediction.get("confidence", 0.0)),
        "status": status,
        "gold_ref": gold_symbol["ref"],
        "class_label": gold_symbol.get("class_label"),
        "label": gold_symbol.get("label") or "",
        "terminals": terminals,
    }
    if source_prediction is not None:
        output["source_prediction"] = {
            "id": source_prediction.get("id"),
            "class_name": source_prediction.get("class_name"),
            "bbox": source_prediction.get("bbox"),
            "confidence": source_prediction.get("confidence"),
            "match_iou": round(box_iou(source_prediction["bbox"], bbox), 4),
            "match_min_overlap": round(min_overlap_ratio(source_prediction["bbox"], bbox), 4),
        }
    return output


def augment_symbols_with_gold(
    symbols: list[dict[str, Any]],
    *,
    project: dict[str, Any] | None,
    classes: set[str],
    iou_threshold: float,
    min_overlap_threshold: float,
    unmatched_yolo: str,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if project is None:
        return symbols, {"enabled": False}

    target_gold = [
        symbol
        for symbol in project.get("symbols", [])
        if not classes or str(symbol.get("class_key")) in classes
    ]
    used_prediction_indexes: set[int] = set()
    output: list[dict[str, Any]] = []
    matched_gold_refs: set[str] = set()
    for gold in target_gold:
        gold_box = symbol_bbox(gold, int(project["image_width"]), int(project["image_height"]))
        best_index: int | None = None
        best_score = -1.0
        best_iou = 0.0
        best_min_overlap = 0.0
        for index, prediction in enumerate(symbols):
            if index in used_prediction_indexes:
                continue
            pred_box = prediction.get("bbox")
            if not pred_box:
                continue
            iou = box_iou(pred_box, gold_box)
            min_overlap = min_overlap_ratio(pred_box, gold_box)
            if iou < iou_threshold and min_overlap < min_overlap_threshold:
                continue
            class_bonus = 1.0 if str(prediction.get("class_name")) == str(gold.get("class_key")) else 0.0
            score = class_bonus + iou + min_overlap + float(prediction.get("confidence", 0.0)) * 0.01
            if score > best_score:
                best_score = score
                best_index = index
                best_iou = iou
                best_min_overlap = min_overlap
        if best_index is None:
            continue
        used_prediction_indexes.add(best_index)
        matched_gold_refs.add(str(gold["ref"]))
        replacement = gold_symbol_to_pipeline_symbol(
            gold,
            project=project,
            status="gold_replaced_yolo",
            source_prediction=symbols[best_index],
        )
        replacement["source_prediction"]["match_iou"] = round(best_iou, 4)
        replacement["source_prediction"]["match_min_overlap"] = round(best_min_overlap, 4)
        output.append(replacement)

    supplemented = 0
    for gold in target_gold:
        if str(gold["ref"]) in matched_gold_refs:
            continue
        output.append(gold_symbol_to_pipeline_symbol(gold, project=project, status="gold_supplemented"))
        supplemented += 1

    kept_unmatched = 0
    if unmatched_yolo == "keep":
        for index, prediction in enumerate(symbols):
            if index in used_prediction_indexes:
                continue
            output.append({**prediction, "status": "unmatched_yolo"})
            kept_unmatched += 1

    return output, {
        "enabled": True,
        "gold_sheet_no": project.get("sheet_no"),
        "gold_symbol_count": len(target_gold),
        "replaced_yolo_count": len(matched_gold_refs),
        "supplemented_gold_count": supplemented,
        "kept_unmatched_yolo_count": kept_unmatched,
        "unmatched_yolo_policy": unmatched_yolo,
        "iou_threshold": iou_threshold,
        "min_overlap_threshold": min_overlap_threshold,
        "classes": sorted(classes) if classes else ["*"],
    }


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


def resize_panel(image: Image.Image, target_w: int) -> Image.Image:
    scale = target_w / image.width
    resample = Image.Resampling.LANCZOS if hasattr(Image, "Resampling") else Image.LANCZOS
    return image.resize((target_w, round(image.height * scale)), resample)


def draw_yolo_overlay(source: Image.Image, symbols: list[dict[str, Any]]) -> Image.Image:
    canvas = source.convert("RGB").copy()
    draw = ImageDraw.Draw(canvas)
    palette = [
        (220, 38, 38),
        (37, 99, 235),
        (5, 150, 105),
        (217, 119, 6),
        (124, 58, 237),
        (8, 145, 178),
        (190, 24, 93),
        (77, 124, 15),
    ]
    for symbol in symbols:
        box = symbol["bbox"]
        class_id = int(symbol.get("class_id", 0))
        color = palette[class_id % len(palette)]
        x0, y0, x1, y1 = int(box["x0"]), int(box["y0"]), int(box["x1"]), int(box["y1"])
        draw.rectangle((x0, y0, x1, y1), outline=color, width=4)
        label = f"{symbol.get('class_name', 'symbol')} {float(symbol.get('confidence', 0)):.2f}"
        text_box = draw.textbbox((x0, max(0, y0 - 19)), label, font=FONT_SM)
        draw.rectangle((text_box[0] - 2, text_box[1] - 2, text_box[2] + 2, text_box[3] + 2), fill=color)
        draw.text((x0, max(0, y0 - 19)), label, fill="white", font=FONT_SM)
    return canvas


def draw_extraction_panel(payload: dict[str, Any]) -> Image.Image:
    width = int(payload["image_size"]["width"])
    height = int(payload["image_size"]["height"])
    canvas = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(canvas)

    for wire in payload.get("wires", []):
        start, end = wire_points(wire)
        color = (210, 35, 30) if float(wire.get("confidence", 0)) >= 0.72 else (230, 130, 30)
        draw.line((start, end), fill=color, width=6)

    for node in payload.get("nodes", []):
        x = int(node["x"])
        y = int(node["y"])
        r = 5 if node.get("type") == "junction" else 3
        draw.ellipse((x - r, y - r, x + r, y + r), fill=(37, 99, 235))

    for label in payload.get("labels", []):
        box = label.get("bbox")
        if not box:
            continue
        text = str(label.get("text") or label.get("raw_text") or label["id"])[:24]
        draw.text((int(box["x0"]), int(box["y0"])), text, fill=(0, 115, 70), font=FONT_SM)

    return canvas


def make_review_sheet(
    source: Image.Image,
    yolo_overlay: Image.Image,
    extraction: Image.Image,
    reconstructed: Image.Image,
    *,
    target_w: int = 1100,
) -> Image.Image:
    panels = [
        ("original", source.convert("RGB")),
        ("YOLO detections on original", yolo_overlay.convert("RGB")),
        ("detected wires and text", extraction.convert("RGB")),
        ("reconstructed result", reconstructed.convert("RGB")),
    ]
    header_h = 42
    rendered: list[Image.Image] = []
    for title, image in panels:
        resized = resize_panel(image, target_w)
        panel = Image.new("RGB", (target_w, resized.height + header_h), "white")
        panel.paste(resized, (0, header_h))
        draw = ImageDraw.Draw(panel)
        draw.text((12, 10), title, fill=(25, 25, 25), font=FONT_MD)
        rendered.append(panel)

    cell_h = max(panel.height for panel in rendered)
    sheet = Image.new("RGB", (target_w * 2, cell_h * 2), (245, 245, 245))
    for index, panel in enumerate(rendered):
        x = (index % 2) * target_w
        y = (index // 2) * cell_h
        sheet.paste(panel, (x, y))
    return sheet


def write_compact_outputs(
    payload: dict[str, Any],
    source_image: Image.Image,
    symbols: list[dict[str, Any]],
    out_dir: Path,
) -> dict[str, str]:
    out_dir.mkdir(parents=True, exist_ok=True)
    final_json = out_dir / "final_output.json"
    labeled_json = out_dir / "labeled_connections.json"
    external_refs_json = out_dir / "external_references.json"
    review_png = out_dir / "review.png"
    labeled_connections = payload.get("labeled_connections") or build_labeled_connections(payload)
    reconstructed = draw_reconstruction(payload, semantic_colors=False, label_style="none")
    _diff_image, diff_metrics = draw_wire_support_diff(source_image, payload)
    review = make_review_sheet(
        source_image,
        draw_yolo_overlay(source_image, symbols),
        draw_extraction_panel(payload),
        reconstructed,
    )
    payload = {
        **payload,
        "labeled_connections": labeled_connections,
        "quality": {
            **payload.get("quality", {}),
            "wire_support_diff": diff_metrics,
        },
        "outputs": {
            "final_json": str(final_json),
            "labeled_connections": str(labeled_json),
            "external_references": str(external_refs_json),
            "review": str(review_png),
        },
    }
    write_json(final_json, payload)
    write_json(labeled_json, labeled_connections)
    write_json(
        external_refs_json,
        {
            "schema_version": "todensekkei.external_references.v1",
            "source": payload.get("source"),
            "summary": payload.get("external_reference_summary", {}),
            "external_references": payload.get("external_references", []),
            "external_reference_connections": payload.get("external_reference_connections", []),
            "symbol_sheet_references": payload.get("symbol_sheet_references", []),
        },
    )
    review.save(review_png)
    return payload["outputs"]


def run_yolo_prediction(
    model_path: Path,
    image_path: Path,
    *,
    imgsz: int,
    conf: float,
    iou: float,
    max_det: int,
    device: str,
) -> tuple[list[dict[str, Any]], dict[int, str]]:
    from ultralytics import YOLO

    model = YOLO(str(model_path))
    result = model.predict(
        source=str(image_path),
        imgsz=imgsz,
        conf=conf,
        iou=iou,
        max_det=max_det,
        device=device,
        verbose=False,
    )[0]
    names = {int(key): str(value) for key, value in result.names.items()}
    symbols: list[dict[str, Any]] = []
    if result.boxes is None:
        return symbols, names

    for index, box in enumerate(result.boxes, start=1):
        xyxy = box.xyxy[0].tolist()
        class_id = int(box.cls[0].item())
        confidence = float(box.conf[0].item())
        bbox = {
            "x0": round(float(xyxy[0])),
            "y0": round(float(xyxy[1])),
            "x1": round(float(xyxy[2])),
            "y1": round(float(xyxy[3])),
        }
        center = box_center(bbox)
        class_name = names.get(class_id, str(class_id))
        symbols.append(
            {
                "id": f"symbol_{index:03d}",
                "type": class_name,
                "class_name": class_name,
                "class_id": class_id,
                "bbox": bbox,
                "center": [round(center[0], 1), round(center[1], 1)],
                "confidence": round(confidence, 4),
                "status": "detected_by_yolo",
            }
        )
    return symbols, names


def preprocessed_yolo_input(
    image: Image.Image,
    out_dir: Path,
    *,
    text_suppression: str,
    text_suppression_scope: str,
) -> tuple[Path, dict[str, Any]]:
    out_dir.mkdir(parents=True, exist_ok=True)
    if text_suppression == "none":
        input_path = out_dir / "yolo_input.png"
        image.save(input_path)
        return input_path, {
            "text_suppression": text_suppression,
            "text_suppression_scope": text_suppression_scope,
            "input_path": str(input_path),
        }

    processed, stats = suppress_text_like_components(
        image,
        [],
        mode=text_suppression,
        scope=text_suppression_scope,
    )
    input_path = out_dir / "yolo_input_preprocessed.png"
    processed.save(input_path)
    return input_path, {
        "text_suppression": text_suppression,
        "text_suppression_scope": text_suppression_scope,
        "input_path": str(input_path),
        **stats,
    }


def enrich_symbols_with_label_ocr(
    image: Image.Image,
    symbols: list[dict[str, Any]],
    *,
    reader: Any | None,
    engine: str,
    classes: set[str],
    padding: int,
    preprocess: str,
    scale: int,
    allowlist: str,
    label_source: str,
) -> int:
    if reader is None or not classes:
        return 0

    enriched_count = 0
    for symbol in symbols:
        class_name = str(symbol.get("class_name") or symbol.get("type") or "")
        if class_name not in classes:
            continue
        ocr = read_label(
            reader,
            image,
            symbol["bbox"],
            padding,
            engine,
            preprocess,
            scale,
            allowlist,
        )
        candidate = build_label_candidate(class_name, ocr)
        word_rule_label = build_word_rule_label(
            {
                "class_key": class_name,
                "label_candidate": candidate,
            }
        )
        final_label = str(word_rule_label.get("final_label") or candidate.get("text") or "")
        symbol["label_ocr"] = {
            "engine": engine,
            "raw_text": ocr.get("text", ""),
            "fragments": ocr.get("fragments", []),
            "crop_box": ocr.get("crop_box"),
            "processed_size": ocr.get("processed_size"),
        }
        symbol["label_candidate"] = candidate
        symbol["label_word_rule"] = word_rule_label
        symbol["label"] = final_label
        symbol["label_match_text"] = final_label or label_text_for_match(ocr, candidate, label_source)
        enriched_count += 1
    return enriched_count


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


def port_inference_config(name: str) -> dict[str, Any]:
    for config in default_port_inference_configs():
        if str(config.get("name")) == name:
            return config
    raise ValueError(f"unknown inferred terminal config: {name}")


def terminal_points_for_symbol(
    symbol: dict[str, Any],
    *,
    wires: list[dict[str, Any]],
    source_image: Image.Image,
    terminal_source: str,
    inferred_terminal_classes: set[str],
    inferred_terminal_config: dict[str, Any],
) -> list[dict[str, Any]]:
    class_name = str(symbol.get("class_name") or symbol.get("type") or "")
    terminals = symbol.get("terminals") or []

    if terminal_source in {"annotation", "annotation-or-inferred"}:
        points = []
        for index, terminal in enumerate(terminals, start=1):
            point = terminal.get("point")
            if not point:
                continue
            points.append(
                {
                    "terminal_ref": terminal.get("ref") or f"{symbol['id']}-T{index}",
                    "terminal_name": terminal.get("name") or "",
                    "point": [float(point[0]), float(point[1])],
                    "source": "annotation",
                }
            )
        if points:
            return points

    if terminal_source in {"inferred", "annotation-or-inferred"} and class_name in inferred_terminal_classes:
        candidates = infer_ports_for_symbol(symbol, wires, source_image, config=inferred_terminal_config)
        if candidates:
            return [
                {
                    "terminal_ref": f"{symbol['id']}-P{index:02d}",
                    "terminal_name": f"P{index}",
                    "point": [float(candidate["point"][0]), float(candidate["point"][1])],
                    "source": "inferred_symbol_port",
                    "wire_id_hint": candidate.get("wire_id"),
                    "wire_orientation": candidate.get("wire_orientation"),
                    "port_reason": candidate.get("reason"),
                    "port_ink_ratio": candidate.get("ink_ratio"),
                }
                for index, candidate in enumerate(candidates, start=1)
            ]

    if class_name in EXPLICIT_NODE_CLASSES:
        return [
            {
                "terminal_ref": f"{symbol['id']}-N01",
                "terminal_name": "N1",
                "point": [float(symbol["center"][0]), float(symbol["center"][1])],
                "source": "explicit_node_center",
            }
        ]

    return [
        {
            "terminal_ref": None,
            "terminal_name": None,
            "point": [float(symbol["center"][0]), float(symbol["center"][1])],
            "source": "bbox_center_fallback",
        }
    ]


def nearest_wire_for_point(
    point: tuple[float, float],
    wires: list[dict[str, Any]],
) -> tuple[dict[str, Any], float] | tuple[None, float]:
    if not wires:
        return None, float("inf")
    best_wire = min(wires, key=lambda wire: point_to_wire_distance(point, wire))
    return best_wire, point_to_wire_distance(point, best_wire)


def enrich_symbols_and_graph(
    payload: dict[str, Any],
    symbols: list[dict[str, Any]],
    *,
    terminal_wire_threshold: float,
    terminal_node_threshold: float,
    terminal_source: str,
    inferred_terminal_classes: set[str],
    inferred_terminal_config: dict[str, Any],
    symbol_terminal_bridge_classes: set[str],
    junction_node_policy: str,
    junction_node_classes: set[str],
    junction_node_threshold: float,
    source_image: Image.Image,
) -> dict[str, Any]:
    width = int(payload["image_size"]["width"])
    height = int(payload["image_size"]["height"])
    labels = payload.get("labels", [])
    wires = payload.get("wires", [])
    wires_by_id = {str(wire.get("id")): wire for wire in wires}
    nodes = [{**node} for node in payload.get("nodes", [])]
    nodes_by_id = {node["id"]: node for node in nodes}

    symbol_node_links: list[dict[str, Any]] = []
    symbol_wire_links: list[dict[str, Any]] = []
    symbol_terminal_links: list[dict[str, Any]] = []
    for symbol in symbols:
        bbox = symbol["bbox"]
        center = tuple(symbol["center"])
        terminal_points = terminal_points_for_symbol(
            symbol,
            wires=wires,
            source_image=source_image,
            terminal_source=terminal_source,
            inferred_terminal_classes=inferred_terminal_classes,
            inferred_terminal_config=inferred_terminal_config,
        )
        symbol["terminals"] = [
            {
                "ref": terminal.get("terminal_ref"),
                "name": terminal.get("terminal_name") or "",
                "point": terminal["point"],
                "source": terminal.get("source"),
                "wire_id_hint": terminal.get("wire_id_hint"),
                "wire_orientation": terminal.get("wire_orientation"),
                "port_reason": terminal.get("port_reason"),
                "port_ink_ratio": terminal.get("port_ink_ratio"),
            }
            for terminal in terminal_points
            if terminal.get("terminal_ref")
        ]
        linked_node_ids = []
        terminal_wire_ids = []
        terminal_links_for_symbol = []
        for terminal in terminal_points:
            point = (float(terminal["point"][0]), float(terminal["point"][1]))
            nearest_node: tuple[float, dict[str, Any]] | None = None
            if nodes:
                node = min(nodes, key=lambda item: point_distance(point, (float(item["x"]), float(item["y"]))))
                distance = point_distance(point, (float(node["x"]), float(node["y"])))
                nearest_node = (distance, node)
                if distance <= terminal_node_threshold and node["id"] not in linked_node_ids:
                    linked_node_ids.append(node["id"])
                    symbol_node_links.append(
                        {
                            "symbol_id": symbol["id"],
                            "terminal_ref": terminal["terminal_ref"],
                            "terminal_name": terminal["terminal_name"],
                            "node_id": node["id"],
                            "distance": round(distance, 2),
                            "reason": "nearest_terminal_node",
                        }
                    )
                    nodes_by_id[node["id"]].setdefault("symbol_ids", []).append(symbol["id"])

            hinted_wire_id = terminal.get("wire_id_hint")
            if hinted_wire_id and str(hinted_wire_id) in wires_by_id:
                nearest_wire = wires_by_id[str(hinted_wire_id)]
                wire_distance = point_to_wire_distance(point, nearest_wire)
            else:
                nearest_wire, wire_distance = nearest_wire_for_point(point, wires)
            if nearest_wire is not None and wire_distance <= terminal_wire_threshold:
                if nearest_wire["id"] not in terminal_wire_ids:
                    terminal_wire_ids.append(nearest_wire["id"])
                link = {
                    "symbol_id": symbol["id"],
                    "terminal_ref": terminal["terminal_ref"],
                    "terminal_name": terminal["terminal_name"],
                    "wire_id": nearest_wire["id"],
                    "distance": round(wire_distance, 2),
                    "reason": "inferred_terminal_wire" if hinted_wire_id else "nearest_terminal_wire",
                    "terminal_source": terminal.get("source"),
                }
                terminal_links_for_symbol.append(link)
                symbol_terminal_links.append(link)
            elif nearest_node is not None and nearest_node[0] <= terminal_node_threshold:
                link = {
                    "symbol_id": symbol["id"],
                    "terminal_ref": terminal["terminal_ref"],
                    "terminal_name": terminal["terminal_name"],
                    "node_id": nearest_node[1]["id"],
                    "distance": round(nearest_node[0], 2),
                    "reason": "nearest_terminal_node_no_wire",
                }
                terminal_links_for_symbol.append(link)
                symbol_terminal_links.append(link)

        expanded = padded_box(bbox, 10, width, height)
        touching_wire_ids = []
        for wire in wires:
            if box_intersects(expanded, wire_bbox(wire, padding=10)):
                touching_wire_ids.append(wire["id"])
                symbol_wire_links.append(
                    {
                        "symbol_id": symbol["id"],
                        "wire_id": wire["id"],
                        "reason": "symbol_bbox_touches_wire",
                    }
                )

        nearby_labels = []
        for label in labels:
            label_bbox = label.get("bbox")
            if not label_bbox:
                continue
            distance = point_distance(center, box_center(label_bbox))
            if distance <= 90:
                nearby_labels.append((distance, label["id"]))
        nearby_labels.sort(key=lambda item: item[0])
        symbol["nearby_node_ids"] = linked_node_ids
        symbol["terminal_wire_ids"] = terminal_wire_ids
        symbol["terminal_links"] = terminal_links_for_symbol
        symbol["touching_wire_ids"] = touching_wire_ids[:10]
        symbol["nearby_label_ids"] = [label_id for _distance, label_id in nearby_labels[:8]]

    return {
        **payload,
        "symbols": symbols,
        "nodes": nodes,
        "symbol_node_links": symbol_node_links,
        "symbol_wire_links": symbol_wire_links,
        "symbol_terminal_links": symbol_terminal_links,
        "nets": build_net_candidates(
            payload.get("edges", []),
            nodes,
            symbols,
            wires,
            symbol_terminal_bridge_classes=symbol_terminal_bridge_classes,
            junction_node_policy=junction_node_policy,
            junction_node_classes=junction_node_classes,
            junction_node_threshold=junction_node_threshold,
        ),
    }


def build_net_candidates(
    edges: list[dict[str, Any]],
    nodes: list[dict[str, Any]],
    symbols: list[dict[str, Any]],
    wires: list[dict[str, Any]],
    *,
    symbol_terminal_bridge_classes: set[str],
    junction_node_policy: str,
    junction_node_classes: set[str],
    junction_node_threshold: float,
) -> list[dict[str, Any]]:
    parent: dict[str, str] = {}
    nodes_by_id = {str(node["id"]): node for node in nodes}

    def find(node_id: str) -> str:
        parent.setdefault(node_id, node_id)
        if parent[node_id] != node_id:
            parent[node_id] = find(parent[node_id])
        return parent[node_id]

    def union(a: str, b: str) -> None:
        ra = find(a)
        rb = find(b)
        if ra != rb:
            parent[rb] = ra

    conductive_junction_node_ids: set[str] = set()
    if junction_node_policy == "explicit":
        junction_symbols = [
            symbol
            for symbol in symbols
            if str(symbol.get("class_name") or symbol.get("type") or "") in junction_node_classes
        ]
        for node in nodes:
            if str(node.get("type") or "") != "junction":
                continue
            point = (float(node["x"]), float(node["y"]))
            for symbol in junction_symbols:
                center = tuple(symbol.get("center") or box_center(symbol["bbox"]))
                if point_distance(point, center) <= junction_node_threshold:
                    conductive_junction_node_ids.add(str(node["id"]))
                    break

    def graph_node_id(node_id: str, wire_id: str | None) -> str:
        node = nodes_by_id.get(str(node_id), {})
        if (
            junction_node_policy == "explicit"
            and str(node.get("type") or "") == "junction"
            and str(node_id) not in conductive_junction_node_ids
        ):
            return f"{node_id}@{wire_id or 'unknown_wire'}"
        return str(node_id)

    for edge in edges:
        union(
            graph_node_id(str(edge["from_node_id"]), edge.get("wire_id")),
            graph_node_id(str(edge["to_node_id"]), edge.get("wire_id")),
        )

    edge_by_root: dict[str, list[str]]
    wire_by_root: dict[str, set[str]]
    root_by_wire: dict[str, set[str]]
    root_by_node: dict[str, set[str]]

    def rebuild_indexes() -> tuple[dict[str, set[str]], dict[str, list[str]], dict[str, set[str]], dict[str, set[str]], dict[str, set[str]]]:
        indexed_nodes_by_root: dict[str, set[str]] = defaultdict(set)
        indexed_edge_by_root: dict[str, list[str]] = defaultdict(list)
        indexed_wire_by_root: dict[str, set[str]] = defaultdict(set)
        indexed_root_by_wire: dict[str, set[str]] = defaultdict(set)
        indexed_root_by_node: dict[str, set[str]] = defaultdict(set)
        for edge in edges:
            wire_id = edge.get("wire_id")
            from_key = graph_node_id(str(edge["from_node_id"]), wire_id)
            to_key = graph_node_id(str(edge["to_node_id"]), wire_id)
            root = find(from_key)
            indexed_edge_by_root[root].append(edge["id"])
            indexed_nodes_by_root[root].update([str(edge["from_node_id"]), str(edge["to_node_id"])])
            indexed_root_by_node[str(edge["from_node_id"])].add(root)
            indexed_root_by_node[str(edge["to_node_id"])].add(root)
            if wire_id:
                indexed_wire_by_root[root].add(wire_id)
                indexed_root_by_wire[wire_id].add(root)
            find(to_key)
        return (
            indexed_nodes_by_root,
            indexed_edge_by_root,
            indexed_wire_by_root,
            indexed_root_by_wire,
            indexed_root_by_node,
        )

    nodes_by_root, edge_by_root, wire_by_root, root_by_wire, root_by_node = rebuild_indexes()

    for edge in edges:
        find(graph_node_id(str(edge["from_node_id"]), edge.get("wire_id")))
        find(graph_node_id(str(edge["to_node_id"]), edge.get("wire_id")))

    for symbol in symbols:
        class_name = str(symbol.get("class_name") or symbol.get("type") or "")
        if class_name not in symbol_terminal_bridge_classes:
            continue
        linked_roots: set[str] = set()
        for link in symbol.get("terminal_links", []):
            wire_id = link.get("wire_id")
            if wire_id:
                linked_roots.update(root_by_wire.get(wire_id, set()))
            node_id = link.get("node_id")
            if node_id:
                linked_roots.add(find(node_id))
        linked_roots = {find(root) for root in linked_roots}
        if len(linked_roots) < 2:
            continue
        first, *rest = sorted(linked_roots)
        for root in rest:
            union(first, root)

    nodes_by_root, edge_by_root, wire_by_root, root_by_wire, root_by_node = rebuild_indexes()

    symbol_by_root: dict[str, set[str]] = defaultdict(set)
    terminal_by_root: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for symbol in symbols:
        linked = False
        for link in symbol.get("terminal_links", []):
            wire_id = link.get("wire_id")
            if not wire_id:
                continue
            for root in root_by_wire.get(wire_id, set()):
                symbol_by_root[root].add(symbol["id"])
                terminal_by_root[root].append(
                    {
                        "symbol_id": symbol["id"],
                        "terminal_ref": link.get("terminal_ref"),
                        "terminal_name": link.get("terminal_name"),
                        "wire_id": wire_id,
                        "distance": link.get("distance"),
                        "reason": link.get("reason"),
                    }
                )
                linked = True
        if linked:
            continue
        for node_id in symbol.get("nearby_node_ids", []):
            for root in root_by_node.get(str(node_id), {find(str(node_id))}):
                symbol_by_root[root].add(symbol["id"])
                terminal_by_root[root].append(
                    {
                        "symbol_id": symbol["id"],
                        "terminal_ref": None,
                        "terminal_name": None,
                        "node_id": node_id,
                        "reason": "nearby_node_fallback",
                    }
                )

    nets = []
    for root, node_ids in sorted(nodes_by_root.items(), key=lambda item: item[0]):
        if len(node_ids) < 2 and not symbol_by_root.get(root):
            continue
        nets.append(
            {
                "id": f"net_{len(nets) + 1:04d}",
                "node_ids": sorted(node_ids),
                "edge_ids": sorted(edge_by_root.get(root, [])),
                "wire_ids": sorted(wire_by_root.get(root, [])),
                "symbol_ids": sorted(symbol_by_root.get(root, [])),
                "terminal_members": sorted(
                    terminal_by_root.get(root, []),
                    key=lambda item: (
                        str(item.get("symbol_id")),
                        str(item.get("terminal_ref")),
                        str(item.get("wire_id") or item.get("node_id")),
                    ),
                ),
                "status": "geometry_candidate",
            }
        )
    return nets


def terminal_point_by_ref(symbol: dict[str, Any], terminal_ref: str | None) -> list[float] | None:
    if terminal_ref:
        for terminal in symbol.get("terminals") or []:
            if terminal.get("ref") == terminal_ref:
                point = terminal.get("point")
                return [round(float(point[0]), 1), round(float(point[1]), 1)] if point else None
    if symbol.get("center"):
        return [round(float(symbol["center"][0]), 1), round(float(symbol["center"][1]), 1)]
    return None


def compact_label_candidate(candidate: dict[str, Any] | None) -> dict[str, Any] | None:
    if not candidate:
        return None
    return {
        "text": candidate.get("text") or "",
        "match_text": candidate.get("match_text") or "",
        "tokens": [
            {
                "text": token.get("text"),
                "kind": token.get("kind"),
                "confidence": token.get("confidence"),
                "source_text": token.get("source_text"),
            }
            for token in candidate.get("tokens", [])
        ],
        "rejected_tokens": [
            {
                "text": token.get("text"),
                "reason": token.get("reason"),
                "source_text": token.get("source_text"),
            }
            for token in candidate.get("rejected_tokens", [])
        ],
    }


def compact_word_rule_label(label: dict[str, Any] | None) -> dict[str, Any] | None:
    if not label:
        return None
    return {
        "final_label": label.get("final_label") or "",
        "final_tokens": label.get("final_tokens") or [],
        "selected_tokens": [
            {
                "text": token.get("text"),
                "kind": token.get("kind"),
                "confidence": token.get("confidence"),
                "source_text": token.get("source_text"),
            }
            for token in label.get("selected_tokens", [])
        ],
        "discarded_tokens": [
            {
                "text": token.get("text"),
                "reason": token.get("reason"),
                "kind": token.get("kind"),
                "source_text": token.get("source_text"),
            }
            for token in label.get("discarded_tokens", [])
        ],
    }


def connection_member_view(member: dict[str, Any], symbol_by_id: dict[str, dict[str, Any]]) -> dict[str, Any]:
    symbol = symbol_by_id.get(str(member.get("symbol_id")), {})
    label_source = "ocr_word_rule" if symbol.get("label_word_rule") else "ocr_candidate" if symbol.get("label_candidate") else "annotation_or_detection"
    raw_ocr = None
    if symbol.get("label_ocr"):
        raw_ocr = symbol["label_ocr"].get("raw_text")
    return {
        "symbol_id": member.get("symbol_id"),
        "gold_ref": symbol.get("gold_ref"),
        "class_name": symbol.get("class_name") or symbol.get("type"),
        "label": symbol.get("label") or "",
        "label_source": label_source,
        "raw_ocr": raw_ocr,
        "label_candidate": compact_label_candidate(symbol.get("label_candidate")),
        "label_word_rule": compact_word_rule_label(symbol.get("label_word_rule")),
        "terminal_ref": member.get("terminal_ref"),
        "terminal_name": member.get("terminal_name") or "",
        "terminal_point": terminal_point_by_ref(symbol, member.get("terminal_ref")),
        "wire_id": member.get("wire_id"),
        "node_id": member.get("node_id"),
        "link_distance": member.get("distance"),
        "link_reason": member.get("reason"),
    }


def terminal_member_key(member: dict[str, Any]) -> tuple[str, str, str, str]:
    return (
        str(member.get("symbol_id") or ""),
        str(member.get("terminal_ref") or ""),
        str(member.get("wire_id") or ""),
        str(member.get("node_id") or ""),
    )


def build_labeled_connections(payload: dict[str, Any]) -> dict[str, Any]:
    symbol_by_id = {str(symbol["id"]): symbol for symbol in payload.get("symbols", [])}
    nets = []
    all_pairs = []
    symbol_connections: dict[str, dict[str, Any]] = {}
    for net in payload.get("nets", []):
        raw_members = sorted(net.get("terminal_members", []), key=terminal_member_key)
        members = [connection_member_view(member, symbol_by_id) for member in raw_members]
        pairs = []
        for left_index, left in enumerate(members):
            for right in members[left_index + 1 :]:
                if left.get("symbol_id") == right.get("symbol_id"):
                    continue
                pair = {
                    "net_id": net["id"],
                    "from": {
                        "symbol_id": left.get("symbol_id"),
                        "gold_ref": left.get("gold_ref"),
                        "class_name": left.get("class_name"),
                        "label": left.get("label"),
                        "terminal_ref": left.get("terminal_ref"),
                        "terminal_name": left.get("terminal_name"),
                    },
                    "to": {
                        "symbol_id": right.get("symbol_id"),
                        "gold_ref": right.get("gold_ref"),
                        "class_name": right.get("class_name"),
                        "label": right.get("label"),
                        "terminal_ref": right.get("terminal_ref"),
                        "terminal_name": right.get("terminal_name"),
                    },
                    "basis": "same_predicted_net",
                }
                pairs.append(pair)
                all_pairs.append(pair)
        for member in members:
            symbol_id = str(member.get("symbol_id") or "")
            if not symbol_id:
                continue
            symbol_connections.setdefault(
                symbol_id,
                {
                    "symbol_id": symbol_id,
                    "gold_ref": member.get("gold_ref"),
                    "class_name": member.get("class_name"),
                    "label": member.get("label") or "",
                    "terminals": [],
                },
            )
            connected_members = []
            seen_connected: set[tuple[str, str]] = set()
            for other in members:
                if other.get("symbol_id") == member.get("symbol_id"):
                    continue
                key = (str(other.get("symbol_id") or ""), str(other.get("terminal_ref") or ""))
                if key in seen_connected:
                    continue
                seen_connected.add(key)
                connected_members.append(
                    {
                        "symbol_id": other.get("symbol_id"),
                        "gold_ref": other.get("gold_ref"),
                        "class_name": other.get("class_name"),
                        "label": other.get("label") or "",
                        "terminal_ref": other.get("terminal_ref"),
                        "terminal_name": other.get("terminal_name") or "",
                    }
                )
            symbol_connections[symbol_id]["terminals"].append(
                {
                    "terminal_ref": member.get("terminal_ref"),
                    "terminal_name": member.get("terminal_name") or "",
                    "terminal_point": member.get("terminal_point"),
                    "net_id": net["id"],
                    "wire_id": member.get("wire_id"),
                    "node_id": member.get("node_id"),
                    "link_distance": member.get("link_distance"),
                    "connected_member_count": len(connected_members),
                    "connected_members": connected_members,
                }
            )
        nets.append(
            {
                "net_id": net["id"],
                "status": net.get("status"),
                "wire_ids": net.get("wire_ids", []),
                "node_ids": net.get("node_ids", []),
                "edge_ids": net.get("edge_ids", []),
                "member_count": len(members),
                "pair_count": len(pairs),
                "members": members,
                "connection_pairs": pairs,
            }
        )
    return {
        "schema_version": "todensekkei.labeled_connections.v1",
        "source": payload.get("source"),
        "basis": "terminal members grouped by predicted wire net",
        "net_count": len(nets),
        "connection_pair_count": len(all_pairs),
        "nets": nets,
        "symbol_connections": sorted(symbol_connections.values(), key=lambda item: str(item.get("symbol_id"))),
        "connection_pairs": all_pairs,
    }


def process_page(
    page: int,
    *,
    model_path: Path,
    analysis_root: Path,
    out_dir: Path,
    imgsz: int,
    conf: float,
    iou: float,
    max_det: int,
    device: str,
    debug_artifacts: bool,
    label_overlap_threshold: float,
    text_suppression: str,
    text_suppression_scope: str,
    symbol_ocr_reader: Any | None,
    symbol_ocr_engine: str,
    symbol_ocr_classes: set[str],
    symbol_ocr_padding: int,
    symbol_ocr_preprocess: str,
    symbol_ocr_scale: int,
    symbol_ocr_allowlist: str,
    symbol_label_source: str,
    gold_project: dict[str, Any] | None,
    annotated_sheets: set[str],
    external_reference_source: str,
    gold_symbol_classes: set[str],
    gold_match_iou: float,
    gold_match_min_overlap: float,
    gold_unmatched_yolo: str,
    terminal_wire_threshold: float,
    terminal_node_threshold: float,
    terminal_source: str,
    inferred_terminal_classes: set[str],
    inferred_terminal_config_name: str,
    symbol_terminal_bridge_classes: set[str],
    junction_node_policy: str,
    junction_node_classes: set[str],
    junction_node_threshold: float,
) -> dict[str, Any]:
    page_dir = analysis_root / f"page_{page:03d}"
    payload = build_final_output_from_analysis(page_dir, "validation_pdf_with_yolo")
    source = Image.open(page_dir / "base.png").convert("RGB")
    page_out_dir = out_dir / f"page_{page:03d}"
    yolo_input_path, preprocess_info = preprocessed_yolo_input(
        source,
        page_out_dir,
        text_suppression=text_suppression,
        text_suppression_scope=text_suppression_scope,
    )
    symbols, model_names = run_yolo_prediction(
        model_path,
        yolo_input_path,
        imgsz=imgsz,
        conf=conf,
        iou=iou,
        max_det=max_det,
        device=device,
    )
    if not debug_artifacts:
        yolo_input_path.unlink(missing_ok=True)
        preprocess_info = {key: value for key, value in preprocess_info.items() if key != "input_path"}
    symbols, removed_by_label_overlap = filter_symbols_by_label_overlap(
        symbols,
        payload.get("labels", []),
        label_overlap_threshold,
    )
    symbols, gold_assist_info = augment_symbols_with_gold(
        symbols,
        project=gold_project,
        classes=gold_symbol_classes,
        iou_threshold=gold_match_iou,
        min_overlap_threshold=gold_match_min_overlap,
        unmatched_yolo=gold_unmatched_yolo,
    )
    symbol_label_ocr_count = enrich_symbols_with_label_ocr(
        source,
        symbols,
        reader=symbol_ocr_reader,
        engine=symbol_ocr_engine,
        classes=symbol_ocr_classes,
        padding=symbol_ocr_padding,
        preprocess=symbol_ocr_preprocess,
        scale=symbol_ocr_scale,
        allowlist=symbol_ocr_allowlist,
        label_source=symbol_label_source,
    )
    inferred_terminal_config = port_inference_config(inferred_terminal_config_name)
    payload = enrich_symbols_and_graph(
        payload,
        symbols,
        terminal_wire_threshold=terminal_wire_threshold,
        terminal_node_threshold=terminal_node_threshold,
        terminal_source=terminal_source,
        inferred_terminal_classes=inferred_terminal_classes,
        inferred_terminal_config=inferred_terminal_config,
        symbol_terminal_bridge_classes=symbol_terminal_bridge_classes,
        junction_node_policy=junction_node_policy,
        junction_node_classes=junction_node_classes,
        junction_node_threshold=junction_node_threshold,
        source_image=source,
    )
    external_reference_payload = build_external_reference_payload(
        gold_project,
        annotated_sheets=annotated_sheets,
        source=external_reference_source,
    )
    class_counts = Counter(symbol["class_name"] for symbol in symbols)
    payload = {
        **payload,
        **external_reference_payload,
        "quality": {
            **payload.get("quality", {}),
            "symbol_count": len(symbols),
            "symbol_class_counts": dict(sorted(class_counts.items())),
            "symbol_node_link_count": len(payload.get("symbol_node_links", [])),
            "symbol_wire_link_count": len(payload.get("symbol_wire_links", [])),
            "symbol_terminal_link_count": len(payload.get("symbol_terminal_links", [])),
            "symbol_removed_by_label_overlap": removed_by_label_overlap,
            "symbol_label_ocr_count": symbol_label_ocr_count,
            "external_reference_count": external_reference_payload["external_reference_summary"][
                "external_reference_count"
            ],
            "external_reference_connection_count": external_reference_payload["external_reference_summary"][
                "external_reference_connection_count"
            ],
            "symbol_sheet_reference_count": external_reference_payload["external_reference_summary"][
                "symbol_sheet_reference_count"
            ],
            "net_count": len(payload.get("nets", [])),
            "gold_assist": gold_assist_info,
            "terminal_linking": {
                "wire_threshold": terminal_wire_threshold,
                "node_threshold": terminal_node_threshold,
                "basis": "symbol terminals are selected by terminal_source, then linked to nearest or hinted wire",
                "terminal_source": terminal_source,
                "inferred_terminal_classes": sorted(inferred_terminal_classes),
                "inferred_terminal_config": inferred_terminal_config,
                "symbol_terminal_bridge_classes": sorted(symbol_terminal_bridge_classes),
                "junction_node_policy": junction_node_policy,
                "junction_node_classes": sorted(junction_node_classes),
                "junction_node_threshold": junction_node_threshold,
            },
            "yolo": {
                "model": str(model_path),
                "imgsz": imgsz,
                "conf": conf,
                "iou": iou,
                "max_det": max_det,
                "device": device,
                "label_overlap_filter_threshold": label_overlap_threshold,
                "input_preprocessing": preprocess_info,
                "class_names": model_names,
            },
            "symbol_label_ocr": {
                "engine": symbol_ocr_engine,
                "classes": sorted(symbol_ocr_classes),
                "padding": symbol_ocr_padding,
                "preprocess": symbol_ocr_preprocess,
                "scale": symbol_ocr_scale,
                "label_source": symbol_label_source,
                "enabled": symbol_ocr_reader is not None,
            },
            "external_references": external_reference_payload["external_reference_summary"],
        },
        "notes": [
            *payload.get("notes", []),
            "symbols are YOLO detections from a from-scratch prototype model.",
            "nets are geometry candidates from detected wires and nearby YOLO symbols.",
            "external_references are annotation-derived page-continuation symbols until a detector/OCR path is added.",
        ],
    }
    payload["labeled_connections"] = build_labeled_connections(payload)
    if debug_artifacts:
        paths = write_visual_outputs(payload, source, page_out_dir)
        final_payload = load_json(paths.final_json)
        write_json(page_out_dir / "detected_symbols_yolo.json", symbols)
        write_json(page_out_dir / "labeled_connections.json", payload["labeled_connections"])
    else:
        outputs = write_compact_outputs(payload, source, symbols, page_out_dir)
        final_payload = load_json(page_out_dir / "final_output.json")
    return {
        "page": page,
        "quality": final_payload["quality"],
        "outputs": final_payload.get("outputs", outputs if not debug_artifacts else {}),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run local YOLO + wire graph + reconstruction pipeline.")
    parser.add_argument("--pdf", type=Path, default=Path(os.environ.get(PDF_ENV_VAR, DEFAULT_PDF_PATH)))
    parser.add_argument("--pages", nargs="+", default=["1-5"])
    parser.add_argument("--analysis-root", type=Path, default=DEFAULT_ANALYSIS_ROOT)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
    parser.add_argument("--model", type=Path, default=DEFAULT_MODEL_PATH)
    parser.add_argument("--annotation-zip", type=Path, default=None)
    parser.add_argument("--page-sheet-map", nargs="*", default=[])
    parser.add_argument(
        "--external-reference-source",
        choices=("none", "annotation-other-label"),
        default="annotation-other-label",
        help="build page-continuation nodes from annotated other symbols whose label is ERxxxxxへ",
    )
    parser.add_argument("--gold-symbol-classes", nargs="*", default=[])
    parser.add_argument("--gold-match-iou", type=float, default=0.25)
    parser.add_argument("--gold-match-min-overlap", type=float, default=0.65)
    parser.add_argument("--gold-unmatched-yolo", choices=("keep", "drop"), default="keep")
    parser.add_argument("--terminal-wire-threshold", type=float, default=10.0)
    parser.add_argument("--terminal-node-threshold", type=float, default=28.0)
    parser.add_argument(
        "--terminal-source",
        choices=("annotation", "inferred", "annotation-or-inferred"),
        default="annotation",
        help="annotation uses saved terminals; inferred estimates parent-symbol ports from detected wires",
    )
    parser.add_argument("--inferred-terminal-classes", nargs="*", default=sorted(DEFAULT_INFERRED_TERMINAL_CLASSES))
    parser.add_argument("--inferred-terminal-config", default="template_confident_relaxed_span_ink02")
    parser.add_argument(
        "--symbol-terminal-bridge-classes",
        nargs="*",
        default=[],
        help="merge predicted wire nets through multi-terminal symbols of these classes",
    )
    parser.add_argument("--junction-node-policy", choices=("all", "explicit"), default="all")
    parser.add_argument("--junction-node-classes", nargs="*", default=["junction"])
    parser.add_argument("--junction-node-threshold", type=float, default=24.0)
    parser.add_argument("--dpi", type=int, default=200)
    parser.add_argument("--imgsz", type=int, default=640)
    parser.add_argument("--conf", type=float, default=0.05)
    parser.add_argument("--iou", type=float, default=0.45)
    parser.add_argument("--max-det", type=int, default=500)
    parser.add_argument("--device", default="cpu")
    parser.add_argument("--label-overlap-threshold", type=float, default=0.35)
    parser.add_argument(
        "--text-suppression",
        choices=("none", "light", "medium", "heavy", "erase"),
        default="none",
        help="apply the same text-like component suppression to YOLO input",
    )
    parser.add_argument(
        "--text-suppression-scope",
        choices=("whole-image", "symbol-boxes"),
        default="whole-image",
    )
    parser.add_argument("--symbol-label-ocr-engine", choices=("none", "easyocr", "rapidocr"), default="rapidocr")
    parser.add_argument("--symbol-label-ocr-lang", default="en")
    parser.add_argument("--symbol-label-classes", nargs="*", default=["connector", "contact_a"])
    parser.add_argument("--symbol-label-source", choices=("raw", "structured", "structured_match"), default="structured_match")
    parser.add_argument("--symbol-ocr-padding", type=int, default=8)
    parser.add_argument("--symbol-ocr-preprocess", choices=("adaptive", "gray", "otsu", "line_remove", "line_remove_wide"), default="gray")
    parser.add_argument("--symbol-ocr-scale", type=int, default=4)
    parser.add_argument("--symbol-ocr-allowlist", default=OCR_ALLOWLIST)
    parser.add_argument("--skip-ocr", action="store_true")
    parser.add_argument("--debug-artifacts", action="store_true", help="write all intermediate PNGs")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    pages = parse_pages(args.pages)
    if not args.pdf.exists():
        raise SystemExit(f"PDF not found: {args.pdf}")
    if not args.model.exists():
        raise SystemExit(f"YOLO model not found: {args.model}")

    args.out_dir.mkdir(parents=True, exist_ok=True)
    if args.annotation_zip and not args.annotation_zip.exists():
        raise SystemExit(f"annotation ZIP not found: {args.annotation_zip}")
    project_by_sheet = load_annotation_projects(args.annotation_zip)
    annotated_sheets = set(project_by_sheet)
    page_sheet_map = parse_mapping(args.page_sheet_map)
    symbol_ocr_reader = None
    if args.symbol_label_ocr_engine == "easyocr":
        import easyocr

        symbol_ocr_reader = easyocr.Reader([args.symbol_label_ocr_lang], gpu=False, verbose=False)
    elif args.symbol_label_ocr_engine == "rapidocr":
        from rapidocr_onnxruntime import RapidOCR

        symbol_ocr_reader = RapidOCR()

    ensure_analysis_outputs(args.pdf, pages, args.analysis_root, args.dpi, run_ocr=not args.skip_ocr)
    page_summaries = [
        process_page(
            page,
            model_path=args.model,
            analysis_root=args.analysis_root,
            out_dir=args.out_dir,
            imgsz=args.imgsz,
            conf=args.conf,
            iou=args.iou,
            max_det=args.max_det,
            device=args.device,
            debug_artifacts=args.debug_artifacts,
            label_overlap_threshold=args.label_overlap_threshold,
            text_suppression=args.text_suppression,
            text_suppression_scope=args.text_suppression_scope,
            symbol_ocr_reader=symbol_ocr_reader,
            symbol_ocr_engine=args.symbol_label_ocr_engine,
            symbol_ocr_classes=set(args.symbol_label_classes),
            symbol_ocr_padding=args.symbol_ocr_padding,
            symbol_ocr_preprocess=args.symbol_ocr_preprocess,
            symbol_ocr_scale=args.symbol_ocr_scale,
            symbol_ocr_allowlist=args.symbol_ocr_allowlist,
            symbol_label_source=args.symbol_label_source,
            gold_project=project_by_sheet.get(page_sheet_map.get(str(page), "")),
            annotated_sheets=annotated_sheets,
            external_reference_source=args.external_reference_source,
            gold_symbol_classes=set(args.gold_symbol_classes),
            gold_match_iou=args.gold_match_iou,
            gold_match_min_overlap=args.gold_match_min_overlap,
            gold_unmatched_yolo=args.gold_unmatched_yolo,
            terminal_wire_threshold=args.terminal_wire_threshold,
            terminal_node_threshold=args.terminal_node_threshold,
            terminal_source=args.terminal_source,
            inferred_terminal_classes=set(args.inferred_terminal_classes),
            inferred_terminal_config_name=args.inferred_terminal_config,
            symbol_terminal_bridge_classes=set(args.symbol_terminal_bridge_classes),
            junction_node_policy=args.junction_node_policy,
            junction_node_classes=set(args.junction_node_classes),
            junction_node_threshold=args.junction_node_threshold,
        )
        for page in pages
    ]
    summary = {
        "schema_version": "todensekkei.yolo_e2e.summary.v1",
        "status": "prototype",
        "pdf": str(args.pdf),
        "pages": pages,
        "model": str(args.model),
        "annotation_zip": str(args.annotation_zip) if args.annotation_zip else None,
        "page_sheet_map": page_sheet_map,
        "page_summaries": page_summaries,
        "notes": [
            "All outputs are local-only artifacts under data/private/.",
            "This is a prototype pipeline: YOLO symbols, image-processing wires, graph candidates, and visual reconstruction.",
        ],
    }
    write_json(args.out_dir / "e2e_summary.json", summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
