"""検出結果を構造 JSON（todensekkei.sequence_extraction）の形にまとめる。"""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from analysis.wiring.detect import DEFAULT_WIRE_TEXT_MASK, WiringDetection
from analysis.wiring.geometry import wire_points


def label_text(label: dict[str, Any]) -> str:
    return str(label.get("text") or label.get("normalized_text") or label.get("raw_text") or label["id"])


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
        confidence = min(0.98, confidence + 0.08) if has_endpoint_labels else max(0.25, confidence - 0.18)

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


def assemble_structure(
    *, labels: list[dict[str, Any]], wires: list[dict[str, Any]], nodes: list[dict[str, Any]], edges: list[dict[str, Any]],
    text_regions: list[dict[str, Any]], regions: dict[str, Any], image_size: dict[str, int], coordinate_unit: str,
    ocr_status: str | None, wire_text_mask: str, source: dict[str, Any],
) -> dict[str, Any]:
    """検出した配線・ラベル・グラフ（dataclass を asdict した形）から構造 JSON を組み立てる。"""
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
        for label in labels
    ]
    wires = [
        {
            "id": wire["id"],
            "orientation": wire["orientation"],
            "bbox": wire["box"],
            "polyline": [list(point) for point in wire_points(wire)],
            "axis": wire["axis"],
            "length": wire["length"],
            "confidence": wire["confidence"],
            "visible_coverage": wire.get("visible_coverage"),
            "reason": wire["reason"],
            "nearby_label_ids": wire.get("nearby_label_ids", []),
            "status": "detected",
        }
        for wire in wires
    ]
    connections = build_connection_candidates({"nodes": nodes, "edges": edges, "wires": wires}, labels)
    coverages = [float(wire["visible_coverage"] or 0.0) for wire in wires]
    return {
        "schema_version": "todensekkei.sequence_extraction.demo.v1",
        "source": source,
        "status": "prototype",
        "image_size": image_size,
        "coordinate_unit": coordinate_unit,
        "regions": regions,
        "symbols": [],
        "labels": labels,
        "text_regions": text_regions,
        "wires": wires,
        "nodes": nodes,
        "edges": edges,
        "connections": connections,
        "quality": {
            "wire_count": len(wires),
            "horizontal_wire_count": sum(wire["orientation"] == "h" for wire in wires),
            "vertical_wire_count": sum(wire["orientation"] == "v" for wire in wires),
            "label_count": len(labels),
            "node_count": len(nodes),
            "edge_count": len(edges),
            "connection_count": len(connections),
            "candidate_connection_count": sum(item["type"] == "candidate_connection" for item in connections),
            "wire_visible_coverage_min": round(min(coverages, default=0.0), 3),
            "wire_visible_coverage_avg": round(sum(coverages) / max(1, len(coverages)), 3),
            "ocr_status": ocr_status,
            "wire_text_mask": wire_text_mask,
            "detected_text_region_count": len(text_regions),
        },
        "notes": [
            "symbols is empty for validation PDF because real symbol classes and trained detector are pending.",
            "connections are prototype graph candidates, not domain-approved from/to records.",
            "wire_visible_coverage compares detected wire spans against source image ink support.",
        ],
    }


def structure_from_detection(detection: WiringDetection, source: dict[str, Any]) -> dict[str, Any]:
    """本番の検出結果（メモリ上）から構造 JSON を組み立てる。ファイルには書き出さない。"""
    return assemble_structure(
        labels=[], wires=[asdict(wire) for wire in detection.wires],
        nodes=detection.graph["nodes"], edges=detection.graph["edges"],
        text_regions=[asdict(region) for region in detection.text_regions],
        regions=asdict(detection.regions), image_size=detection.image_size,
        coordinate_unit="rendered_pixel", ocr_status="disabled", wire_text_mask=DEFAULT_WIRE_TEXT_MASK, source=source,
    )
