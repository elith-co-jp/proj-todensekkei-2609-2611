"""検出した配線から、交点・端点のノードと配線上の辺のグラフを作る。"""

from __future__ import annotations

from analysis.wiring.params import DEFAULT, GraphParams
from analysis.wiring.shapes import DetectedLabel, DetectedWire, LineSegment, line_axis, line_intersection


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


def point_on_line(point: tuple[int, int], line: LineSegment, tolerance: int = 16, across: int = 16) -> bool:
    x, y = point
    box = line.box
    if line.orientation == "h":
        return abs(y - line_axis(line)) <= across and box.x0 - tolerance <= x <= box.x1 + tolerance
    return abs(x - line_axis(line)) <= across and box.y0 - tolerance <= y <= box.y1 + tolerance


def build_connection_graph(
    wires: list[DetectedWire], labels: list[DetectedLabel], params: GraphParams = DEFAULT.graph,
) -> dict:
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
            point = line_intersection(h_line, v_line, tolerance=params.line_reach_px)
            if point is None:
                continue
            intersection_keys.add((round(point[0] / 12), round(point[1] / 12)))
            node_points.append(point)

    nodes: list[dict] = []
    for index, point in enumerate(cluster_points(node_points, tolerance=params.node_merge_px), start=1):
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
        points = [(node["x"], node["y"]) for node in nodes
                  if point_on_line((node["x"], node["y"]), line, tolerance=params.line_reach_px, across=params.on_line_across_px)]
        points = cluster_points(points, tolerance=params.node_merge_px)
        points = sorted(points, key=lambda point: point[0] if wire.orientation == "h" else point[1])
        for start, end in zip(points, points[1:], strict=False):
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
