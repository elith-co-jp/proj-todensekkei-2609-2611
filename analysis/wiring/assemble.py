"""検出結果を構造 JSON（todensekkei.sequence_extraction）の形にまとめる。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from analysis.wiring.geometry import wire_points


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


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
    text_regions = graph_json.get("text_regions", [])

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
        "text_regions": text_regions,
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
            "wire_text_mask": summary.get("wire_text_mask", "none"),
            "detected_text_region_count": summary.get("detected_text_region_count", 0),
        },
        "notes": [
            "symbols is empty for validation PDF because real symbol classes and trained detector are pending.",
            "connections are prototype graph candidates, not domain-approved from/to records.",
            "wire_visible_coverage compares detected wire spans against source image ink support.",
        ],
    }
