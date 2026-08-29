from __future__ import annotations

import argparse
import csv
import json
import math
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw

from analyze_annotation_graph import load_zip_payload, parse_member, symbol_bbox
from build_e2e_demo_outputs import load_font


FONT_XS = load_font(12)
FONT_SM = load_font(15)
FONT_MD = load_font(18)

OK_COLOR = (37, 99, 235)
NG_COLOR = (220, 38, 38)
EXTRA_COLOR = (217, 119, 6)
INDIRECT_COLOR = (115, 115, 115)
TERMINAL_MISSING_COLOR = (22, 163, 74)
GRAY_COLOR = (110, 110, 110)


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def parse_mapping(items: list[str]) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for item in items:
        if "=" not in item:
            raise ValueError(f"mapping must be KEY=VALUE: {item}")
        key, value = item.split("=", 1)
        mapping[key.strip()] = value.strip()
    return mapping


def terminal_member_key(symbol_ref: str, terminal_ref: str | None) -> str:
    return f"{symbol_ref}:{terminal_ref}" if terminal_ref else symbol_ref


def sorted_pair(left: str, right: str) -> tuple[str, str]:
    return tuple(sorted((left, right)))  # type: ignore[return-value]


def point_distance(a: tuple[float, float] | list[float], b: tuple[float, float] | list[float]) -> float:
    return math.hypot(float(a[0]) - float(b[0]), float(a[1]) - float(b[1]))


def point_segment_projection(
    point: tuple[float, float] | list[float],
    start: tuple[float, float],
    end: tuple[float, float],
) -> tuple[float, float, tuple[float, float]]:
    px, py = float(point[0]), float(point[1])
    x1, y1 = start
    x2, y2 = end
    dx = x2 - x1
    dy = y2 - y1
    length_sq = dx * dx + dy * dy
    if length_sq == 0:
        return 0.0, point_distance((px, py), start), start
    t = ((px - x1) * dx + (py - y1) * dy) / length_sq
    clamped = max(0.0, min(1.0, t))
    projected = (x1 + clamped * dx, y1 + clamped * dy)
    return clamped, point_distance((px, py), projected), projected


def segment_orientation(start: tuple[float, float], end: tuple[float, float]) -> str:
    return "h" if abs(end[0] - start[0]) >= abs(end[1] - start[1]) else "v"


def segment_bounds(start: tuple[float, float], end: tuple[float, float]) -> tuple[float, float, float, float]:
    return min(start[0], end[0]), min(start[1], end[1]), max(start[0], end[0]), max(start[1], end[1])


def metric(tp: int, fp: int, fn: int) -> dict[str, int | float | None]:
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
    }


def box_area(box: dict[str, int | float]) -> float:
    return max(0.0, float(box["x1"]) - float(box["x0"])) * max(0.0, float(box["y1"]) - float(box["y0"]))


def box_intersection_area(a: dict[str, int | float], b: dict[str, int | float]) -> float:
    x0 = max(float(a["x0"]), float(b["x0"]))
    y0 = max(float(a["y0"]), float(b["y0"]))
    x1 = min(float(a["x1"]), float(b["x1"]))
    y1 = min(float(a["y1"]), float(b["y1"]))
    return max(0.0, x1 - x0) * max(0.0, y1 - y0)


def box_iou(a: dict[str, int | float], b: dict[str, int | float]) -> float:
    intersection = box_intersection_area(a, b)
    union = box_area(a) + box_area(b) - intersection
    return intersection / max(1.0, union)


def min_overlap_ratio(a: dict[str, int | float], b: dict[str, int | float]) -> float:
    return box_intersection_area(a, b) / max(1.0, min(box_area(a), box_area(b)))


def attach_eval_gold_refs(
    payload: dict[str, Any],
    project: dict[str, Any],
    include_classes: set[str],
    exclude_classes: set[str],
    *,
    iou_threshold: float,
    min_overlap_threshold: float,
) -> dict[str, Any]:
    width = int(project["image_width"])
    height = int(project["image_height"])
    gold_by_ref = {
        str(symbol["ref"]): {
            "ref": str(symbol["ref"]),
            "class_name": str(symbol.get("class_key") or ""),
            "label": symbol.get("label") or "",
            "bbox": symbol_bbox(symbol, width, height),
        }
        for symbol in project.get("symbols", [])
        if str(symbol.get("class_key") or "") in include_classes and str(symbol.get("class_key") or "") not in exclude_classes
    }
    used_gold_refs = {str(symbol.get("gold_ref")) for symbol in payload.get("symbols", []) if symbol.get("gold_ref")}
    symbols = []
    mapped = 0
    already_mapped = 0
    unmapped = 0
    for symbol in payload.get("symbols", []):
        class_name = str(symbol.get("class_name") or symbol.get("type") or "")
        if class_name not in include_classes or class_name in exclude_classes:
            symbols.append(symbol)
            continue
        if symbol.get("gold_ref"):
            already_mapped += 1
            symbols.append(symbol)
            continue
        pred_box = symbol.get("bbox")
        if not pred_box:
            symbols.append(symbol)
            unmapped += 1
            continue
        best_ref: str | None = None
        best_iou = 0.0
        best_min_overlap = 0.0
        best_score = -1.0
        for ref, gold in gold_by_ref.items():
            if ref in used_gold_refs:
                continue
            if gold["class_name"] != class_name:
                continue
            iou = box_iou(pred_box, gold["bbox"])
            min_overlap = min_overlap_ratio(pred_box, gold["bbox"])
            if iou < iou_threshold and min_overlap < min_overlap_threshold:
                continue
            score = iou + min_overlap + float(symbol.get("confidence") or 0.0) * 0.01
            if score > best_score:
                best_ref = ref
                best_iou = iou
                best_min_overlap = min_overlap
                best_score = score
        if best_ref is None:
            symbols.append(symbol)
            unmapped += 1
            continue
        used_gold_refs.add(best_ref)
        mapped_symbol = {
            **symbol,
            "gold_ref": best_ref,
            "eval_gold_match": {
                "method": "same_class_bbox_match",
                "iou": round(best_iou, 4),
                "min_overlap": round(best_min_overlap, 4),
                "thresholds": {
                    "iou": iou_threshold,
                    "min_overlap": min_overlap_threshold,
                },
            },
        }
        if not mapped_symbol.get("label"):
            mapped_symbol["label"] = gold_by_ref[best_ref]["label"]
        symbols.append(mapped_symbol)
        mapped += 1
    return {
        **payload,
        "symbols": symbols,
        "eval_symbol_gold_mapping": {
            "method": "evaluation-only same-class bbox matching; predicted bbox/terminals are not replaced",
            "iou_threshold": iou_threshold,
            "min_overlap_threshold": min_overlap_threshold,
            "already_had_gold_ref": already_mapped,
            "newly_mapped_count": mapped,
            "unmapped_count": unmapped,
            "used_gold_ref_count": len(used_gold_refs),
        },
    }


def make_gold_member_info(
    project: dict[str, Any],
    include_classes: set[str],
    exclude_classes: set[str],
) -> dict[str, dict[str, Any]]:
    width = int(project["image_width"])
    height = int(project["image_height"])
    member_info: dict[str, dict[str, Any]] = {}
    for symbol in project.get("symbols", []):
        class_name = str(symbol.get("class_key") or "")
        if class_name in exclude_classes or class_name not in include_classes:
            continue
        bbox = symbol_bbox(symbol, width, height)
        terminals = symbol.get("terminals") or []
        if not terminals:
            key = terminal_member_key(str(symbol["ref"]), None)
            member_info[key] = {
                "symbol_ref": symbol["ref"],
                "terminal_ref": None,
                "terminal_name": "",
                "class_name": class_name,
                "label": symbol.get("label") or "",
                "bbox": bbox,
                "point": [round(float(symbol["cx"]) * width, 1), round(float(symbol["cy"]) * height, 1)],
            }
            continue
        for terminal in terminals:
            key = terminal_member_key(str(symbol["ref"]), terminal.get("ref"))
            member_info[key] = {
                "symbol_ref": symbol["ref"],
                "terminal_ref": terminal.get("ref"),
                "terminal_name": terminal.get("name") or "",
                "class_name": class_name,
                "label": symbol.get("label") or "",
                "bbox": bbox,
                "point": [
                    round(float(terminal["tx"]) * width, 1),
                    round(float(terminal["ty"]) * height, 1),
                ],
            }
    return member_info


def make_gold_direct_pairs(
    project: dict[str, Any],
    member_info: dict[str, dict[str, Any]],
    exclude_classes: set[str],
) -> set[tuple[str, str]]:
    class_by_ref = {str(symbol["ref"]): str(symbol.get("class_key") or "") for symbol in project.get("symbols", [])}
    pairs: set[tuple[str, str]] = set()
    for connection in project.get("connections", []):
        from_ref = str(connection.get("from_symbol_ref") or "")
        to_ref = str(connection.get("to_symbol_ref") or "")
        if class_by_ref.get(from_ref) in exclude_classes or class_by_ref.get(to_ref) in exclude_classes:
            continue
        left = terminal_member_key(from_ref, connection.get("from_terminal_ref"))
        right = terminal_member_key(to_ref, connection.get("to_terminal_ref"))
        if left == right:
            continue
        if left not in member_info or right not in member_info:
            continue
        pairs.add(sorted_pair(left, right))
    return pairs


def nearest_gold_member_key(
    symbol_ref: str,
    point: list[float],
    gold_by_symbol: dict[str, list[tuple[str, list[float]]]],
    threshold: float,
) -> str | None:
    candidates = gold_by_symbol.get(symbol_ref, [])
    if not candidates:
        return symbol_ref
    best_key, best_point = min(candidates, key=lambda item: point_distance(point, item[1]))
    if point_distance(point, best_point) <= threshold:
        return best_key
    return None


def node_point(node: dict[str, Any]) -> tuple[float, float]:
    return float(node["x"]), float(node["y"])


def make_predicted_terminals(
    payload: dict[str, Any],
    member_info: dict[str, dict[str, Any]],
    include_classes: set[str],
    exclude_classes: set[str],
    terminal_match_threshold: float,
) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    gold_by_symbol: dict[str, list[tuple[str, list[float]]]] = defaultdict(list)
    for key, info in member_info.items():
        gold_by_symbol[str(info["symbol_ref"])].append((key, info["point"]))

    records: list[dict[str, Any]] = []
    pred_member_info: dict[str, dict[str, Any]] = {}
    for symbol in payload.get("symbols", []):
        class_name = str(symbol.get("class_name") or symbol.get("type") or "")
        if class_name in exclude_classes or class_name not in include_classes:
            continue
        gold_ref = symbol.get("gold_ref")
        if not gold_ref:
            continue
        links_by_terminal: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for link in symbol.get("terminal_links", []):
            links_by_terminal[str(link.get("terminal_ref") or "")].append(link)
        for index, terminal in enumerate(symbol.get("terminals") or [], start=1):
            terminal_ref = str(terminal.get("ref") or "")
            point = [float(terminal["point"][0]), float(terminal["point"][1])]
            mapped_key = nearest_gold_member_key(str(gold_ref), point, gold_by_symbol, terminal_match_threshold)
            if not mapped_key:
                mapped_key = f"PRED:{symbol['id']}:{terminal_ref or index}"
            pred_member_info.setdefault(
                mapped_key,
                {
                    "symbol_ref": gold_ref,
                    "terminal_ref": terminal_ref,
                    "terminal_name": terminal.get("name") or "",
                    "class_name": class_name,
                    "label": symbol.get("label") or "",
                    "bbox": symbol.get("bbox"),
                    "point": point,
                },
            )
            links = links_by_terminal.get(terminal_ref) or [{}]
            for link_index, link in enumerate(links, start=1):
                records.append(
                    {
                        "id": f"term_{len(records) + 1:05d}",
                        "member_key": mapped_key,
                        "symbol_id": symbol["id"],
                        "class_name": class_name,
                        "point": point,
                        "wire_id": link.get("wire_id"),
                        "node_id": link.get("node_id"),
                        "link_index": link_index,
                    }
                )
    return records, pred_member_info


def conductive_junction_nodes(payload: dict[str, Any]) -> set[str]:
    quality = payload.get("quality", {})
    config = quality.get("terminal_linking", {})
    policy = str(config.get("junction_node_policy") or "all")
    if policy != "explicit":
        return {str(node["id"]) for node in payload.get("nodes", []) if str(node.get("type") or "") == "junction"}

    classes = set(config.get("junction_node_classes") or [])
    threshold = float(config.get("junction_node_threshold") or 24.0)
    junction_symbols = [
        symbol
        for symbol in payload.get("symbols", [])
        if str(symbol.get("class_name") or symbol.get("type") or "") in classes
    ]
    result = set()
    for node in payload.get("nodes", []):
        if str(node.get("type") or "") != "junction":
            continue
        point = node_point(node)
        if any(point_distance(point, symbol.get("center") or [0, 0]) <= threshold for symbol in junction_symbols):
            result.add(str(node["id"]))
    return result


def build_predicted_graph_pairs(
    payload: dict[str, Any],
    terminal_records: list[dict[str, Any]],
    *,
    internal_bridge_classes: set[str],
    node_anchor_classes: set[str],
    node_anchor_threshold: float,
    terminal_gap_bridge_classes: set[str],
    terminal_gap_bridge_max: float,
    wire_gap_bridge_max: float,
    gap_bridge_align_threshold: float,
    wire_gap_bridge_terminal_anchor_threshold: float,
    terminal_terminal_bridge_classes: set[str],
    terminal_terminal_bridge_max: float,
    bridge_stats: Counter[str] | None = None,
) -> set[tuple[str, str]]:
    nodes_by_id = {str(node["id"]): node for node in payload.get("nodes", [])}
    edges = list(payload.get("edges", []))
    wires_by_id = {str(wire["id"]): wire for wire in payload.get("wires", [])}
    conductive_nodes = conductive_junction_nodes(payload)
    graph: dict[str, set[str]] = defaultdict(set)
    coords: dict[str, tuple[float, float]] = {}
    terminal_by_stop: dict[str, str] = {}
    stop_by_member: dict[str, str] = {}
    terminal_members_by_graph_node: dict[str, set[str]] = defaultdict(set)
    stats = bridge_stats if bridge_stats is not None else Counter()

    def graph_node_id(node_id: str, wire_id: str | None) -> str:
        node = nodes_by_id.get(str(node_id), {})
        if str(node.get("type") or "") == "junction" and str(node_id) not in conductive_nodes:
            return f"node:{node_id}@{wire_id or 'unknown'}"
        return f"node:{node_id}"

    def add_graph_edge(left: str, right: str) -> None:
        if left == right:
            return
        graph[left].add(right)
        graph[right].add(left)

    edge_segments: dict[str, dict[str, Any]] = {}
    edges_by_wire: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for edge in edges:
        from_node = nodes_by_id.get(str(edge.get("from_node_id")))
        to_node = nodes_by_id.get(str(edge.get("to_node_id")))
        if not from_node or not to_node:
            continue
        wire_id = str(edge.get("wire_id") or "")
        start = node_point(from_node)
        end = node_point(to_node)
        wire = wires_by_id.get(wire_id, {})
        segment = {
            **edge,
            "start": start,
            "end": end,
            "orientation": str(wire.get("orientation") or segment_orientation(start, end)),
        }
        edge_segments[str(edge["id"])] = segment
        edges_by_wire[wire_id].append(segment)
        left = graph_node_id(str(edge["from_node_id"]), wire_id)
        right = graph_node_id(str(edge["to_node_id"]), wire_id)
        coords[left] = start
        coords[right] = end

    stops_by_edge: dict[str, list[tuple[float, str]]] = defaultdict(list)
    incident_wire_by_node: dict[str, set[str]] = defaultdict(set)
    for edge in edges:
        wire_id = str(edge.get("wire_id") or "")
        incident_wire_by_node[str(edge.get("from_node_id"))].add(wire_id)
        incident_wire_by_node[str(edge.get("to_node_id"))].add(wire_id)

    def bridge_terminal_to_collinear_wire(record: dict[str, Any], stop_id: str) -> bool:
        if terminal_gap_bridge_max <= 0:
            return False
        if str(record.get("class_name") or "") not in terminal_gap_bridge_classes:
            return False
        px, py = float(record["point"][0]), float(record["point"][1])
        best: tuple[float, float, str, float, str | None] | None = None
        for edge_id, segment in edge_segments.items():
            start = segment["start"]
            end = segment["end"]
            orientation = str(segment["orientation"])
            x0, y0, x1, y1 = segment_bounds(start, end)
            endpoint_node: str | None = None
            if orientation == "v":
                align = min(abs(px - start[0]), abs(px - end[0]))
                if align > gap_bridge_align_threshold:
                    continue
                if py < y0:
                    gap = y0 - py
                    t = 0.0 if start[1] <= end[1] else 1.0
                    endpoint_node = str(segment["from_node_id"] if t == 0.0 else segment["to_node_id"])
                elif py > y1:
                    gap = py - y1
                    t = 1.0 if start[1] <= end[1] else 0.0
                    endpoint_node = str(segment["from_node_id"] if t == 0.0 else segment["to_node_id"])
                else:
                    gap = align
                    t, _distance, _projection = point_segment_projection((px, py), start, end)
            else:
                align = min(abs(py - start[1]), abs(py - end[1]))
                if align > gap_bridge_align_threshold:
                    continue
                if px < x0:
                    gap = x0 - px
                    t = 0.0 if start[0] <= end[0] else 1.0
                    endpoint_node = str(segment["from_node_id"] if t == 0.0 else segment["to_node_id"])
                elif px > x1:
                    gap = px - x1
                    t = 1.0 if start[0] <= end[0] else 0.0
                    endpoint_node = str(segment["from_node_id"] if t == 0.0 else segment["to_node_id"])
                else:
                    gap = align
                    t, _distance, _projection = point_segment_projection((px, py), start, end)
            if gap > terminal_gap_bridge_max:
                continue
            score = gap + align * 2.0
            if best is None or score < best[0]:
                best = (score, gap, edge_id, t, endpoint_node)
        if best is None:
            return False

        _score, gap, edge_id, t, endpoint_node = best
        segment = edge_segments[edge_id]
        wire_id = str(segment.get("wire_id") or "")
        if endpoint_node:
            add_graph_edge(stop_id, graph_node_id(endpoint_node, wire_id))
        else:
            stops_by_edge[edge_id].append((t, stop_id))
        stats["terminal_gap_bridge_count"] += 1
        stats[f"terminal_gap_bridge_{str(record.get('class_name') or 'unknown')}"] += 1
        if gap > 1:
            stats["terminal_gap_bridge_nonzero_gap_count"] += 1
        return True

    def add_wire_gap_bridges() -> None:
        if wire_gap_bridge_max <= 0:
            return

        def has_terminal_anchor(target_edge_id: str, projection: tuple[float, float]) -> bool:
            if wire_gap_bridge_terminal_anchor_threshold <= 0:
                return True
            for _t, stop_id in stops_by_edge.get(target_edge_id, []):
                if stop_id in terminal_by_stop and point_distance(coords[stop_id], projection) <= wire_gap_bridge_terminal_anchor_threshold:
                    return True
            return False

        best_by_endpoint: dict[str, tuple[float, str, float, tuple[float, float], str]] = {}
        for source_edge_id, source in edge_segments.items():
            source_wire_id = str(source.get("wire_id") or "")
            source_orientation = str(source["orientation"])
            if source_orientation not in {"h", "v"}:
                continue
            for endpoint_key, point in (
                (graph_node_id(str(source["from_node_id"]), source_wire_id), source["start"]),
                (graph_node_id(str(source["to_node_id"]), source_wire_id), source["end"]),
            ):
                px, py = point
                for target_edge_id, target in edge_segments.items():
                    if target_edge_id == source_edge_id:
                        continue
                    target_orientation = str(target["orientation"])
                    if target_orientation == source_orientation:
                        continue
                    start = target["start"]
                    end = target["end"]
                    x0, y0, x1, y1 = segment_bounds(start, end)
                    if target_orientation == "h":
                        if not (x0 - gap_bridge_align_threshold <= px <= x1 + gap_bridge_align_threshold):
                            continue
                        gap = abs(py - start[1])
                        t = (px - start[0]) / (end[0] - start[0]) if end[0] != start[0] else 0.0
                        projection = (px, start[1])
                    else:
                        if not (y0 - gap_bridge_align_threshold <= py <= y1 + gap_bridge_align_threshold):
                            continue
                        gap = abs(px - start[0])
                        t = (py - start[1]) / (end[1] - start[1]) if end[1] != start[1] else 0.0
                        projection = (start[0], py)
                    if gap <= 1 or gap > wire_gap_bridge_max:
                        continue
                    if t < -0.02 or t > 1.02:
                        continue
                    t = max(0.0, min(1.0, t))
                    if not has_terminal_anchor(target_edge_id, projection):
                        continue
                    candidate = (gap, target_edge_id, t, projection, str(target.get("wire_id") or ""))
                    if endpoint_key not in best_by_endpoint or gap < best_by_endpoint[endpoint_key][0]:
                        best_by_endpoint[endpoint_key] = candidate
        for index, (endpoint_key, (gap, target_edge_id, t, projection, _wire_id)) in enumerate(best_by_endpoint.items(), start=1):
            bridge_id = f"bridge:{index:05d}:{target_edge_id}"
            coords[bridge_id] = projection
            stops_by_edge[target_edge_id].append((t, bridge_id))
            add_graph_edge(endpoint_key, bridge_id)
            stats["wire_gap_bridge_count"] += 1
            if gap > 1:
                stats["wire_gap_bridge_nonzero_gap_count"] += 1

    def add_terminal_terminal_bridges() -> None:
        if terminal_terminal_bridge_max <= 0:
            return
        record_by_member: dict[str, dict[str, Any]] = {}
        for record in terminal_records:
            record_by_member.setdefault(str(record["member_key"]), record)

        terminals = []
        for member_key, stop_id in stop_by_member.items():
            record = record_by_member.get(member_key)
            if not record:
                continue
            class_name = str(record.get("class_name") or "")
            if class_name not in terminal_terminal_bridge_classes:
                continue
            terminals.append(
                {
                    "stop_id": stop_id,
                    "member_key": member_key,
                    "symbol_id": str(record.get("symbol_id") or ""),
                    "class_name": class_name,
                    "point": coords[stop_id],
                }
            )

        bridge_pairs: set[tuple[str, str]] = set()
        for current in terminals:
            cx, cy = current["point"]
            nearest: dict[str, tuple[float, dict[str, Any]]] = {}
            for other in terminals:
                if other["stop_id"] == current["stop_id"]:
                    continue
                if other["symbol_id"] and other["symbol_id"] == current["symbol_id"]:
                    continue
                ox, oy = other["point"]
                dx = ox - cx
                dy = oy - cy
                if abs(dx) <= gap_bridge_align_threshold and abs(dy) > 1:
                    distance = abs(dy)
                    if distance <= terminal_terminal_bridge_max:
                        direction = "down" if dy > 0 else "up"
                        if direction not in nearest or distance < nearest[direction][0]:
                            nearest[direction] = (distance, other)
                if abs(dy) <= gap_bridge_align_threshold and abs(dx) > 1:
                    distance = abs(dx)
                    if distance <= terminal_terminal_bridge_max:
                        direction = "right" if dx > 0 else "left"
                        if direction not in nearest or distance < nearest[direction][0]:
                            nearest[direction] = (distance, other)
            for _distance, other in nearest.values():
                bridge_pairs.add(sorted_pair(current["stop_id"], other["stop_id"]))

        for left, right in sorted(bridge_pairs):
            add_graph_edge(left, right)
            stats["terminal_terminal_bridge_count"] += 1

    def nearest_node_id(point: list[float]) -> tuple[str | None, float]:
        if not nodes_by_id:
            return None, float("inf")
        node_id, node = min(
            nodes_by_id.items(),
            key=lambda item: point_distance(point, node_point(item[1])),
        )
        distance = point_distance(point, node_point(node))
        return node_id, distance

    for record in terminal_records:
        stop_id = f"stop:{record['id']}"
        member_key = str(record["member_key"])
        terminal_by_stop[stop_id] = member_key
        stop_by_member.setdefault(member_key, stop_id)
        coords[stop_id] = (float(record["point"][0]), float(record["point"][1]))
        wire_id = record.get("wire_id")
        node_id = record.get("node_id")
        anchor_node_id = node_id
        if not anchor_node_id and str(record.get("class_name") or "") in node_anchor_classes:
            candidate_node_id, node_distance = nearest_node_id(record["point"])
            if candidate_node_id and node_distance <= node_anchor_threshold:
                anchor_node_id = candidate_node_id
        if anchor_node_id:
            wire_ids = incident_wire_by_node.get(str(anchor_node_id)) or {wire_id or None}
            for incident_wire in wire_ids:
                graph_id = graph_node_id(str(anchor_node_id), incident_wire)
                terminal_members_by_graph_node[graph_id].add(str(record["member_key"]))
                add_graph_edge(stop_id, graph_id)
            continue
        if wire_id:
            candidates = edges_by_wire.get(str(wire_id), [])
            if not candidates:
                continue
            best = min(candidates, key=lambda edge: point_segment_projection(record["point"], edge["start"], edge["end"])[1])
            t, _distance, _projection = point_segment_projection(record["point"], best["start"], best["end"])
            stops_by_edge[str(best["id"])].append((t, stop_id))
            continue
        if node_id:
            wire_ids = incident_wire_by_node.get(str(node_id)) or {None}
            for incident_wire in wire_ids:
                add_graph_edge(stop_id, graph_node_id(str(node_id), incident_wire))
            continue
        bridge_terminal_to_collinear_wire(record, stop_id)

    add_wire_gap_bridges()

    for edge in edges:
        edge_id = str(edge["id"])
        wire_id = str(edge.get("wire_id") or "")
        from_node = str(edge["from_node_id"])
        to_node = str(edge["to_node_id"])
        items = [
            (0.0, graph_node_id(from_node, wire_id)),
            (1.0, graph_node_id(to_node, wire_id)),
            *stops_by_edge.get(edge_id, []),
        ]
        items.sort(key=lambda item: (item[0], item[1]))
        for (_left_t, left), (_right_t, right) in zip(items, items[1:]):
            add_graph_edge(left, right)

    add_terminal_terminal_bridges()

    pairs: set[tuple[str, str]] = set()
    terminal_stops = set(terminal_by_stop)
    for start_stop, start_member in terminal_by_stop.items():
        for neighbor in graph.get(start_stop, set()):
            stack = [(neighbor, start_stop)]
            visited_edges = {sorted_pair(start_stop, neighbor)}
            while stack:
                current, previous = stack.pop()
                if current in terminal_stops:
                    other_member = terminal_by_stop[current]
                    if other_member != start_member:
                        pairs.add(sorted_pair(start_member, other_member))
                    continue
                members_here = terminal_members_by_graph_node.get(current, set())
                for other_member in sorted(members_here - {start_member}):
                    pairs.add(sorted_pair(start_member, other_member))
                if members_here and start_member not in members_here:
                    continue
                for next_node in graph.get(current, set()):
                    if next_node == previous:
                        continue
                    edge_key = sorted_pair(current, next_node)
                    if edge_key in visited_edges:
                        continue
                    visited_edges.add(edge_key)
                    stack.append((next_node, current))

    terminals_by_symbol: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in terminal_records:
        if str(record.get("class_name") or "") in internal_bridge_classes:
            terminals_by_symbol[str(record["symbol_id"])].append(record)
    for records in terminals_by_symbol.values():
        unique_by_member: dict[str, dict[str, Any]] = {}
        for record in records:
            unique_by_member.setdefault(str(record["member_key"]), record)
        unique = list(unique_by_member.values())
        if len(unique) < 2:
            continue
        xs = [float(item["point"][0]) for item in unique]
        ys = [float(item["point"][1]) for item in unique]
        if max(ys) - min(ys) >= max(xs) - min(xs):
            unique.sort(key=lambda item: (float(item["point"][1]), float(item["point"][0]), str(item["member_key"])))
        else:
            unique.sort(key=lambda item: (float(item["point"][0]), float(item["point"][1]), str(item["member_key"])))
        for left, right in zip(unique, unique[1:]):
            if left["member_key"] != right["member_key"]:
                pairs.add(sorted_pair(str(left["member_key"]), str(right["member_key"])))

    return pairs


def connected_by_member(pairs: set[tuple[str, str]]) -> dict[str, set[str]]:
    connected: dict[str, set[str]] = defaultdict(set)
    for left, right in pairs:
        connected[left].add(right)
        connected[right].add(left)
    return connected


def component_by_member(pairs: set[tuple[str, str]]) -> dict[str, str]:
    parent: dict[str, str] = {}

    def find(item: str) -> str:
        parent.setdefault(item, item)
        if parent[item] != item:
            parent[item] = find(parent[item])
        return parent[item]

    def union(left: str, right: str) -> None:
        left_root = find(left)
        right_root = find(right)
        if left_root != right_root:
            parent[right_root] = left_root

    for left, right in pairs:
        union(left, right)

    groups: dict[str, list[str]] = defaultdict(list)
    for member in sorted(parent):
        groups[find(member)].append(member)

    output: dict[str, str] = {}
    for index, members in enumerate(sorted(groups.values(), key=lambda items: items[0]), start=1):
        component_id = f"component_{index:04d}"
        for member in members:
            output[member] = component_id
    return output


def split_extra_pairs(
    raw_extra: set[tuple[str, str]],
    gold_pairs: set[tuple[str, str]],
    member_info: dict[str, dict[str, Any]],
) -> tuple[set[tuple[str, str]], set[tuple[str, str]]]:
    gold_component_by_member = component_by_member(gold_pairs)
    indirect: set[tuple[str, str]] = set()
    hard_extra: set[tuple[str, str]] = set()
    for left, right in raw_extra:
        if (
            left in member_info
            and right in member_info
            and gold_component_by_member.get(left)
            and gold_component_by_member.get(left) == gold_component_by_member.get(right)
        ):
            indirect.add((left, right))
        else:
            hard_extra.add((left, right))
    return hard_extra, indirect


def draw_text_box(draw: ImageDraw.ImageDraw, xy: tuple[int, int], lines: list[str]) -> None:
    x, y = xy
    line_h = 22
    width = max(draw.textbbox((0, 0), line, font=FONT_SM)[2] for line in lines)
    draw.rectangle((x - 8, y - 8, x + width + 16, y + line_h * len(lines) + 8), fill="white", outline=(80, 80, 80), width=2)
    for index, line in enumerate(lines):
        draw.text((x, y + index * line_h), line, fill=(20, 20, 20), font=FONT_SM)


def draw_title(panel: Image.Image, title: str, subtitle: str | None = None) -> Image.Image:
    header_h = 54
    output = Image.new("RGB", (panel.width, panel.height + header_h), "white")
    output.paste(panel, (0, header_h))
    draw = ImageDraw.Draw(output)
    draw.text((14, 8), title, fill=(20, 20, 20), font=FONT_MD)
    if subtitle:
        draw.text((14, 31), subtitle, fill=(75, 75, 75), font=FONT_SM)
    return output


def make_side_by_side(left: Image.Image, right: Image.Image) -> Image.Image:
    cell_w = max(left.width, right.width)
    cell_h = max(left.height, right.height)
    sheet = Image.new("RGB", (cell_w * 2, cell_h), (235, 235, 235))
    sheet.paste(left, (0, 0))
    sheet.paste(right, (cell_w, 0))
    return sheet


def draw_from_to_review(
    source: Image.Image,
    out_path: Path,
    *,
    gold_pairs: set[tuple[str, str]],
    pred_pairs: set[tuple[str, str]],
    hard_extra_pairs: set[tuple[str, str]],
    indirect_pairs: set[tuple[str, str]],
    detected_member_keys: set[str],
    member_info: dict[str, dict[str, Any]],
    pred_member_info: dict[str, dict[str, Any]],
    summary: dict[str, Any],
) -> None:
    canvas = Image.blend(source.convert("RGB"), Image.new("RGB", source.size, "white"), 0.18)
    draw = ImageDraw.Draw(canvas)
    all_info = {**pred_member_info, **member_info}
    missing = sorted(gold_pairs - pred_pairs)

    def point_for(key: str) -> tuple[int, int] | None:
        info = all_info.get(key)
        if not info or not info.get("point"):
            return None
        return round(float(info["point"][0])), round(float(info["point"][1]))

    for left, right in sorted(indirect_pairs):
        p1 = point_for(left)
        p2 = point_for(right)
        if p1 and p2:
            draw.line((p1, p2), fill=INDIRECT_COLOR, width=2)
    for left, right in missing:
        p1 = point_for(left)
        p2 = point_for(right)
        if p1 and p2:
            draw.line((p1, p2), fill=NG_COLOR, width=3)
    for left, right in sorted(hard_extra_pairs):
        p1 = point_for(left)
        p2 = point_for(right)
        if p1 and p2:
            draw.line((p1, p2), fill=EXTRA_COLOR, width=3)

    gold_connected = connected_by_member(gold_pairs)
    pred_connected = connected_by_member(pred_pairs)
    status_counts: Counter[str] = Counter()
    for key, info in member_info.items():
        if key not in detected_member_keys:
            status = "terminal_missing"
        elif gold_connected.get(key, set()) == pred_connected.get(key, set()):
            status = "ok"
        else:
            status = "ng"
        status_counts[status] += 1
        x, y = point_for(key) or (0, 0)
        if status == "ok":
            color = OK_COLOR
        elif status == "terminal_missing":
            color = TERMINAL_MISSING_COLOR
        else:
            color = NG_COLOR
        draw.ellipse((x - 7, y - 7, x + 7, y + 7), fill=color, outline="white", width=2)

    pred_only_keys = (set(pred_connected) - set(member_info)) & {key for pair in hard_extra_pairs for key in pair}
    for key in pred_only_keys:
        point = point_for(key)
        if not point:
            continue
        x, y = point
        draw.ellipse((x - 7, y - 7, x + 7, y + 7), fill=EXTRA_COLOR, outline="white", width=2)

    lines = [
        "blue point: OK / red point: connected_to NG / green point: terminal not detected",
        "red line: missing / orange: hard extra / gray: indirect",
        f"precision {summary['precision']}  recall {summary['recall']}  f1 {summary['f1']}",
        f"tp/hard_fp/indirect/fn {summary['tp']} / {summary['hard_fp']} / {summary['indirect']} / {summary['fn']}",
        f"terminals ok/ng/missing {status_counts.get('ok', 0)} / {status_counts.get('ng', 0)} / {status_counts.get('terminal_missing', 0)}",
    ]
    draw_text_box(draw, (18, 18), lines)

    original_panel = draw_title(source.convert("RGB"), "Original", "source drawing")
    review_panel = draw_title(canvas, "From-to Review", "lines show mismatched or indirect predicted connections")
    output = make_side_by_side(original_panel, review_panel)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    output.save(out_path)


def write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def member_label(key: str, info: dict[str, dict[str, Any]]) -> str:
    item = info.get(key) or {}
    label = " ".join(str(item.get("label") or "").split())
    return f"{key} {item.get('class_name', '')} {label}".strip()


def process_page(
    page_dir: Path,
    *,
    page: int,
    sheet_no: str,
    project: dict[str, Any],
    include_classes: set[str],
    exclude_classes: set[str],
    terminal_match_threshold: float,
    internal_bridge_classes: set[str],
    node_anchor_classes: set[str],
    node_anchor_threshold: float,
    terminal_gap_bridge_classes: set[str],
    terminal_gap_bridge_max: float,
    wire_gap_bridge_max: float,
    gap_bridge_align_threshold: float,
    wire_gap_bridge_terminal_anchor_threshold: float,
    terminal_terminal_bridge_classes: set[str],
    terminal_terminal_bridge_max: float,
    eval_symbol_match_iou: float,
    eval_symbol_match_min_overlap: float,
    out_dir: Path,
) -> dict[str, Any]:
    payload = read_json(page_dir / "final_output.json")
    payload = attach_eval_gold_refs(
        payload,
        project,
        include_classes,
        exclude_classes,
        iou_threshold=eval_symbol_match_iou,
        min_overlap_threshold=eval_symbol_match_min_overlap,
    )
    source = Image.open(payload["source"]["base_image"]).convert("RGB")
    member_info = make_gold_member_info(project, include_classes, exclude_classes)
    gold_pairs = make_gold_direct_pairs(project, member_info, exclude_classes)
    terminal_records, pred_member_info = make_predicted_terminals(
        payload,
        member_info,
        include_classes,
        exclude_classes,
        terminal_match_threshold,
    )
    detected_member_keys = set(pred_member_info) & set(member_info)
    bridge_stats: Counter[str] = Counter()
    pred_pairs = build_predicted_graph_pairs(
        payload,
        terminal_records,
        internal_bridge_classes=internal_bridge_classes,
        node_anchor_classes=node_anchor_classes,
        node_anchor_threshold=node_anchor_threshold,
        terminal_gap_bridge_classes=terminal_gap_bridge_classes,
        terminal_gap_bridge_max=terminal_gap_bridge_max,
        wire_gap_bridge_max=wire_gap_bridge_max,
        gap_bridge_align_threshold=gap_bridge_align_threshold,
        wire_gap_bridge_terminal_anchor_threshold=wire_gap_bridge_terminal_anchor_threshold,
        terminal_terminal_bridge_classes=terminal_terminal_bridge_classes,
        terminal_terminal_bridge_max=terminal_terminal_bridge_max,
        bridge_stats=bridge_stats,
    )
    pred_pairs = {
        pair
        for pair in pred_pairs
        if pair[0] in member_info or pair[0] in pred_member_info
        if pair[1] in member_info or pair[1] in pred_member_info
    }

    tp = gold_pairs & pred_pairs
    raw_extra_pairs = pred_pairs - gold_pairs
    hard_extra_pairs, indirect_pairs = split_extra_pairs(raw_extra_pairs, gold_pairs, member_info)
    fn = gold_pairs - pred_pairs
    pair_metric = metric(len(tp), len(hard_extra_pairs), len(fn))
    strict_metric = metric(len(tp), len(raw_extra_pairs), len(fn))
    gold_connected = connected_by_member(gold_pairs)
    pred_connected = connected_by_member(pred_pairs)
    endpoint_status: Counter[str] = Counter()
    endpoint_status_by_key: dict[str, str] = {}
    for key in member_info:
        if key not in detected_member_keys:
            status = "terminal_missing"
        elif gold_connected.get(key, set()) == pred_connected.get(key, set()):
            status = "ok"
        else:
            status = "ng"
        endpoint_status[status] += 1
        endpoint_status_by_key[key] = status

    page_out = out_dir / f"page_{page:03d}"
    review_png = page_out / "from_to_error_review.png"
    draw_summary = {
        **pair_metric,
        "hard_fp": len(hard_extra_pairs),
        "indirect": len(indirect_pairs),
    }
    draw_from_to_review(
        source,
        review_png,
        gold_pairs=gold_pairs,
        pred_pairs=pred_pairs,
        hard_extra_pairs=hard_extra_pairs,
        indirect_pairs=indirect_pairs,
        detected_member_keys=detected_member_keys,
        member_info=member_info,
        pred_member_info=pred_member_info,
        summary=draw_summary,
    )

    edge_rows = []
    all_info = {**pred_member_info, **member_info}
    for status, pairs in (
        ("ok", sorted(tp)),
        ("missing", sorted(fn)),
        ("extra", sorted(hard_extra_pairs)),
        ("indirect", sorted(indirect_pairs)),
    ):
        for left, right in pairs:
            edge_rows.append(
                {
                    "page": page,
                    "sheet_no": sheet_no,
                    "status": status,
                    "from": left,
                    "to": right,
                    "from_label": member_label(left, all_info),
                    "to_label": member_label(right, all_info),
                }
            )
    write_csv(
        page_out / "from_to_edges.csv",
        edge_rows,
        ["page", "sheet_no", "status", "from", "to", "from_label", "to_label"],
    )
    write_json(
        page_out / "from_to_edges.json",
        {
            "schema_version": "todensekkei.from_to_edges.v1",
            "page": page,
            "sheet_no": sheet_no,
            "definition": "direct from-to pairs; ok/missing/extra/indirect are compared against annotation connections",
            "status_legend": {
                "ok": "predicted direct from-to pair matches annotation",
                "missing": "annotation direct from-to pair was not predicted",
                "extra": "predicted direct from-to pair is not in annotation and not explainable as same gold component",
                "indirect": "predicted direct pair is not direct in annotation but belongs to the same gold connected component",
            },
            "edges": edge_rows,
        },
    )

    endpoint_rows = []
    for key in sorted(member_info):
        status = endpoint_status_by_key[key]
        endpoint_rows.append(
            {
                "page": page,
                "sheet_no": sheet_no,
                "member_key": key,
                "class_name": member_info[key].get("class_name"),
                "label": member_info[key].get("label"),
                "status": status,
                "gold_connected_to": " ; ".join(member_label(item, member_info) for item in sorted(gold_connected.get(key, set()))),
                "pred_connected_to": " ; ".join(member_label(item, all_info) for item in sorted(pred_connected.get(key, set()))),
            }
        )
    write_csv(
        page_out / "from_to_endpoint_status.csv",
        endpoint_rows,
        ["page", "sheet_no", "member_key", "class_name", "label", "status", "gold_connected_to", "pred_connected_to"],
    )
    write_json(
        page_out / "from_to_endpoint_status.json",
        {
            "schema_version": "todensekkei.from_to_endpoint_status.v1",
            "page": page,
            "sheet_no": sheet_no,
            "definition": "endpoint-level comparison of expected and predicted direct connection partners",
            "status_legend": {
                "ok": "all direct connection partners match annotation",
                "ng": "endpoint was detected but direct connection partners differ from annotation",
                "terminal_missing": "endpoint could not be matched to an annotation terminal",
            },
            "endpoints": endpoint_rows,
        },
    )

    summary = {
        "page": page,
        "sheet_no": sheet_no,
        "review_png": str(review_png),
        "edge_csv": str(page_out / "from_to_edges.csv"),
        "edge_json": str(page_out / "from_to_edges.json"),
        "endpoint_csv": str(page_out / "from_to_endpoint_status.csv"),
        "endpoint_json": str(page_out / "from_to_endpoint_status.json"),
        "gold_edge_count": len(gold_pairs),
        "pred_edge_count": len(pred_pairs),
        "endpoint_count": len(member_info),
        "detected_endpoint_count": len(detected_member_keys),
        "endpoint_status_counts": dict(sorted(endpoint_status.items())),
        "raw_extra": len(raw_extra_pairs),
        "hard_fp": len(hard_extra_pairs),
        "indirect": len(indirect_pairs),
        "bridge_stats": dict(sorted(bridge_stats.items())),
        "eval_symbol_gold_mapping": payload.get("eval_symbol_gold_mapping", {}),
        "strict_metrics": strict_metric,
        **pair_metric,
    }
    write_json(page_out / "from_to_review_summary.json", summary)
    return summary


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build one-panel from-to review outputs from E2E JSON.")
    parser.add_argument("--e2e-dir", type=Path, required=True)
    parser.add_argument("--annotation-zip", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--page-sheet-map", nargs="+", required=True)
    parser.add_argument("--pages", nargs="*", type=int, default=None)
    parser.add_argument("--include-classes", nargs="+", default=["connector", "contact_a", "contact_b", "solenoid", "junction"])
    parser.add_argument("--exclude-classes", nargs="*", default=["other"])
    parser.add_argument("--terminal-match-threshold", type=float, default=18.0)
    parser.add_argument("--internal-bridge-classes", nargs="*", default=["contact_a", "contact_b", "solenoid"])
    parser.add_argument("--node-anchor-classes", nargs="*", default=["connector", "junction", "terminal", "power_bus"])
    parser.add_argument("--node-anchor-threshold", type=float, default=8.0)
    parser.add_argument("--terminal-gap-bridge-classes", nargs="*", default=["connector", "contact_a", "contact_b", "solenoid", "junction"])
    parser.add_argument("--terminal-gap-bridge-max", type=float, default=0.0)
    parser.add_argument("--wire-gap-bridge-max", type=float, default=0.0)
    parser.add_argument("--gap-bridge-align-threshold", type=float, default=8.0)
    parser.add_argument("--wire-gap-bridge-terminal-anchor-threshold", type=float, default=0.0)
    parser.add_argument("--terminal-terminal-bridge-classes", nargs="*", default=[])
    parser.add_argument("--terminal-terminal-bridge-max", type=float, default=0.0)
    parser.add_argument("--eval-symbol-match-iou", type=float, default=0.25)
    parser.add_argument("--eval-symbol-match-min-overlap", type=float, default=0.65)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    page_sheet_map = parse_mapping(args.page_sheet_map)
    include_classes = set(args.include_classes)
    exclude_classes = set(args.exclude_classes)
    internal_bridge_classes = set(args.internal_bridge_classes)
    node_anchor_classes = set(args.node_anchor_classes)
    terminal_gap_bridge_classes = set(args.terminal_gap_bridge_classes)
    terminal_terminal_bridge_classes = set(args.terminal_terminal_bridge_classes)
    bundle, _csv_rows, _netlist = load_zip_payload(args.annotation_zip)
    project_by_sheet = {str(project["sheet_no"]): project for project in bundle.get("projects", [])}
    pages = args.pages or sorted(int(page) for page in page_sheet_map)

    page_summaries = []
    totals = Counter()
    endpoint_totals: Counter[str] = Counter()
    for page in pages:
        sheet_no = page_sheet_map[str(page)]
        summary = process_page(
            args.e2e_dir / f"page_{page:03d}",
            page=page,
            sheet_no=sheet_no,
            project=project_by_sheet[sheet_no],
            include_classes=include_classes,
            exclude_classes=exclude_classes,
            terminal_match_threshold=args.terminal_match_threshold,
            internal_bridge_classes=internal_bridge_classes,
            node_anchor_classes=node_anchor_classes,
            node_anchor_threshold=args.node_anchor_threshold,
            terminal_gap_bridge_classes=terminal_gap_bridge_classes,
            terminal_gap_bridge_max=args.terminal_gap_bridge_max,
            wire_gap_bridge_max=args.wire_gap_bridge_max,
            gap_bridge_align_threshold=args.gap_bridge_align_threshold,
            wire_gap_bridge_terminal_anchor_threshold=args.wire_gap_bridge_terminal_anchor_threshold,
            terminal_terminal_bridge_classes=terminal_terminal_bridge_classes,
            terminal_terminal_bridge_max=args.terminal_terminal_bridge_max,
            eval_symbol_match_iou=args.eval_symbol_match_iou,
            eval_symbol_match_min_overlap=args.eval_symbol_match_min_overlap,
            out_dir=args.out_dir,
        )
        page_summaries.append(summary)
        for key in ("tp", "fp", "fn"):
            totals[key] += int(summary[key])
        for key in ("raw_extra", "hard_fp", "indirect"):
            totals[key] += int(summary[key])
        endpoint_totals.update(summary["endpoint_status_counts"])

    aggregate = {
        "schema_version": "todensekkei.from_to_review.v1",
        "e2e_dir": str(args.e2e_dir),
        "annotation_zip": str(args.annotation_zip),
        "include_classes": sorted(include_classes),
        "exclude_classes": sorted(exclude_classes),
        "terminal_match_threshold": args.terminal_match_threshold,
        "internal_bridge_classes": sorted(internal_bridge_classes),
        "node_anchor_classes": sorted(node_anchor_classes),
        "node_anchor_threshold": args.node_anchor_threshold,
        "terminal_gap_bridge_classes": sorted(terminal_gap_bridge_classes),
        "terminal_gap_bridge_max": args.terminal_gap_bridge_max,
        "wire_gap_bridge_max": args.wire_gap_bridge_max,
        "gap_bridge_align_threshold": args.gap_bridge_align_threshold,
        "wire_gap_bridge_terminal_anchor_threshold": args.wire_gap_bridge_terminal_anchor_threshold,
        "terminal_terminal_bridge_classes": sorted(terminal_terminal_bridge_classes),
        "terminal_terminal_bridge_max": args.terminal_terminal_bridge_max,
        "eval_symbol_gold_mapping": {
            "method": "evaluation-only same-class bbox matching; predicted bbox/terminals are not replaced",
            "iou_threshold": args.eval_symbol_match_iou,
            "min_overlap_threshold": args.eval_symbol_match_min_overlap,
        },
        "aggregate_metrics": metric(totals["tp"], totals["fp"], totals["fn"]),
        "aggregate_strict_metrics": metric(totals["tp"], totals["raw_extra"], totals["fn"]),
        "aggregate_extra_counts": {
            "raw_extra": totals["raw_extra"],
            "hard_fp": totals["hard_fp"],
            "indirect": totals["indirect"],
        },
        "aggregate_endpoint_status_counts": dict(sorted(endpoint_totals.items())),
        "pages": page_summaries,
    }
    args.out_dir.mkdir(parents=True, exist_ok=True)
    write_json(args.out_dir / "from_to_review_summary.json", aggregate)
    write_csv(
        args.out_dir / "from_to_review_summary.csv",
        [
            {
                "page": item["page"],
                "sheet_no": item["sheet_no"],
                "gold_edge_count": item["gold_edge_count"],
                "pred_edge_count": item["pred_edge_count"],
                "tp": item["tp"],
                "fp": item["fp"],
                "raw_extra": item["raw_extra"],
                "hard_fp": item["hard_fp"],
                "indirect": item["indirect"],
                "fn": item["fn"],
                "precision": item["precision"],
                "recall": item["recall"],
                "f1": item["f1"],
                "endpoint_count": item["endpoint_count"],
                "detected_endpoint_count": item["detected_endpoint_count"],
                "endpoint_status_counts": json.dumps(item["endpoint_status_counts"], ensure_ascii=False),
                "bridge_stats": json.dumps(item["bridge_stats"], ensure_ascii=False),
                "eval_symbol_gold_mapping": json.dumps(item["eval_symbol_gold_mapping"], ensure_ascii=False),
                "review_png": item["review_png"],
                "edge_csv": item["edge_csv"],
                "edge_json": item["edge_json"],
                "endpoint_csv": item["endpoint_csv"],
                "endpoint_json": item["endpoint_json"],
            }
            for item in page_summaries
        ],
        [
            "page",
            "sheet_no",
            "gold_edge_count",
            "pred_edge_count",
            "tp",
            "fp",
            "raw_extra",
            "hard_fp",
            "indirect",
            "fn",
            "precision",
            "recall",
            "f1",
            "endpoint_count",
            "detected_endpoint_count",
            "endpoint_status_counts",
            "bridge_stats",
            "eval_symbol_gold_mapping",
            "review_png",
            "edge_csv",
            "edge_json",
            "endpoint_csv",
            "endpoint_json",
        ],
    )
    print(json.dumps(aggregate["aggregate_metrics"], ensure_ascii=False, indent=2))
    print(f"wrote {args.out_dir}")


if __name__ == "__main__":
    main()
