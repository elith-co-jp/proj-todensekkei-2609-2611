"""端子を配線・ノードに結び、ノードのまとまり（ネット）を作る。"""

from __future__ import annotations

from collections import defaultdict
from typing import Any

import numpy as np
from PIL import Image

from analysis.wiring import classes
from analysis.wiring.detect import make_binary_mask
from analysis.wiring.geometry import (
    box_center,
    box_intersects,
    padded_box,
    point_distance,
    point_to_wire_distance,
    wire_bbox,
)
from analysis.wiring.params import DEFAULT, PortParams
from analysis.wiring.ports import (
    LINE_PORT_CLASSES,
    is_four_port_symbol,
    line_ports,
    terminal_points_for_symbol,
)

BOX_PORTS_SKIP_COIL_INTERIOR = True


# Classes whose electrical port is wherever a wire meets the box; when such a
# symbol has no terminal link, attach it to every wire touching its bbox.
TOUCHING_WIRE_FALLBACK_CLASSES = classes.PASS_THROUGH


def nearest_wire_for_point(
    point: tuple[float, float],
    wires: list[dict[str, Any]],
) -> tuple[dict[str, Any], float] | tuple[None, float]:
    if not wires:
        return None, float("inf")
    best_wire = min(wires, key=lambda wire: point_to_wire_distance(point, wire))
    return best_wire, point_to_wire_distance(point, best_wire)


def _hinted_device_port(symbol: dict[str, Any], point: tuple[float, float], wire: dict[str, Any] | None) -> bool:
    """接点・コイルの枠の中の端子が、推定に使った線の軸の上にあるか（線の先の延長を含む）。"""
    if wire is None or symbol.get("class_name") not in LINE_PORT_CLASSES:
        return False
    box, wbox = symbol["bbox"], wire.get("bbox") or {}
    if not (box["x0"] - 1 <= point[0] <= box["x1"] + 1 and box["y0"] - 1 <= point[1] <= box["y1"] + 1) or not wbox:
        return False
    if wire.get("orientation") == "v":
        return wbox["x0"] - 2 <= point[0] <= wbox["x1"] + 2
    return wbox["y0"] - 2 <= point[1] <= wbox["y1"] + 2


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
    port_params: PortParams = DEFAULT.ports,
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
    # A wire end inside a coil or solenoid belongs to that device, never to a box drawn around it.
    coil_boxes = [
        s["bbox"] for s in symbols
        if str(s.get("class_name") or "") in classes.COILS and not (s.get("dot_role") or {}).get("suppressed")
    ] if BOX_PORTS_SKIP_COIL_INTERIOR else []
    line_mask = None
    faint_mask = None
    for symbol in symbols:
        if (symbol.get("dot_role") or {}).get("suppressed"):
            symbol.update(terminals=[], nearby_node_ids=[], terminal_wire_ids=[], terminal_links=[],
                          touching_wire_ids=[], nearby_label_ids=[])
            continue
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
        if coil_boxes and symbol.get("class_name") == "other":
            terminal_points = [
                t for t in terminal_points
                if not any(b["x0"] <= t["point"][0] <= b["x1"] and b["y0"] <= t["point"][1] <= b["y1"] for b in coil_boxes)
            ]
        if (
            port_params.line_ports and symbol.get("class_name") in LINE_PORT_CLASSES
            and not is_four_port_symbol(str(symbol.get("class_name")), bbox, float(inferred_terminal_config["four_port_height_px"]))
            and len([t for t in terminal_points if t.get("terminal_ref")]) < 2
        ):
            if line_mask is None:
                line_mask = make_binary_mask(source_image)
                faint_mask = np.asarray(source_image.convert("L")) < port_params.faint_gray
            terminal_points = line_ports(symbol, line_mask, wires, symbols, faint_mask, port_params) or terminal_points
        symbol["terminals"] = [
            {
                "ref": terminal.get("terminal_ref"),
                "name": terminal.get("terminal_name") or "",
                "point": terminal["point"],
                "source": terminal.get("source"),
                "wire_id_hint": terminal.get("wire_id_hint"),
                "symbol_id_hint": terminal.get("symbol_id_hint"),
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
            if terminal.get("traced") and terminal.get("symbol_id_hint") and not hinted_wire_id:
                # 線をたどってコネクタ・端子台の中心に着いた端子は、その記号の端子につなぐ（ER21315）。
                link = {
                    "symbol_id": symbol["id"],
                    "terminal_ref": terminal["terminal_ref"],
                    "terminal_name": terminal["terminal_name"],
                    "peer_symbol_id": terminal["symbol_id_hint"],
                    "reason": "traced_to_pass_through",
                    "terminal_source": terminal.get("source"),
                }
                terminal_links_for_symbol.append(link)
                symbol_terminal_links.append(link)
                continue
            if terminal.get("traced") and not hinted_wire_id:
                # 線をたどっても配線に当たらなかった端子は、近くの別の配線（記号を貫く線など）に結ばない。
                continue
            if hinted_wire_id and str(hinted_wire_id) in wires_by_id:
                nearest_wire = wires_by_id[str(hinted_wire_id)]
                wire_distance = point_to_wire_distance(point, nearest_wire)
            else:
                nearest_wire, wire_distance = nearest_wire_for_point(point, wires)
            # 画像の線をたどった端子（line_ports）は、離れた先の配線にもつなぐ。接点・コイルの枠の中の端子で、
            # 近くに結べる点も無いものは、推定に使った線が枠の中で終わっていても（線の先にある下側の端子、
            # ER21293）その線につなぐ。線の上の位置で端の先に並ぶので、端からの橋渡しはこの端子から出る。
            node_reachable = nearest_node is not None and nearest_node[0] <= terminal_node_threshold
            on_own_wire = bool(hinted_wire_id) and not node_reachable and _hinted_device_port(symbol, point, nearest_wire)
            if nearest_wire is not None and (
                wire_distance <= terminal_wire_threshold or (terminal.get("traced") and hinted_wire_id) or on_own_wire
            ):
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
            source_image=source_image,
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
    source_image: Image.Image | None = None,
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
    elif junction_node_policy == "all":
        junction_symbol_points = [
            tuple(symbol.get("center") or box_center(symbol["bbox"]))
            for symbol in symbols
            if str(symbol.get("class_name") or symbol.get("type") or "") in junction_node_classes
        ]

        def _wire_ends_at(node_x: float, node_y: float, tol: float = 8.0) -> bool:
            """A wire span terminates at this node (tee join, not a through-crossing)."""
            for wire in wires:
                box = wire.get("box") or {}
                if wire.get("orientation") == "h":
                    if not (float(box.get("y0", -1)) - tol <= node_y <= float(box.get("y1", -1)) + tol):
                        continue
                    if not (float(box.get("x0", -1)) - tol <= node_x <= float(box.get("x1", -1)) + tol):
                        continue
                    if abs(node_x - float(box.get("x0", -999))) <= tol or abs(node_x - float(box.get("x1", -999))) <= tol:
                        return True
                elif wire.get("orientation") == "v":
                    if not (float(box.get("x0", -1)) - tol <= node_x <= float(box.get("x1", -1)) + tol):
                        continue
                    if not (float(box.get("y0", -1)) - tol <= node_y <= float(box.get("y1", -1)) + tol):
                        continue
                    if abs(node_y - float(box.get("y0", -999))) <= tol or abs(node_y - float(box.get("y1", -999))) <= tol:
                        return True
            return False

        tee_node_ids = {
            str(node["id"])
            for node in nodes
            if str(node.get("type") or "") == "junction"
            and _wire_ends_at(float(node["x"]), float(node["y"]))
        }
        # Through-crossings (no wire ends here) only conduct when a junction
        # symbol sits nearby — bare line crossings are not connections.
        cross_conductive_ids = {
            str(node["id"])
            for node in nodes
            if str(node.get("type") or "") == "junction"
            and str(node["id"]) not in tee_node_ids
            and any(
                point_distance((float(node["x"]), float(node["y"])), point) <= junction_node_threshold
                for point in junction_symbol_points
            )
        }
        if source_image is not None:
            junction_mask = make_binary_mask(source_image)
            mask_h, mask_w = junction_mask.shape[:2]

            def _ink_at(px: int, py: int, r: int = 2) -> bool:
                window = junction_mask[
                    max(0, py - r):min(mask_h, py + r + 1),
                    max(0, px - r):min(mask_w, px + r + 1),
                ]
                return bool((window > 0).any())

            for node in nodes:
                if str(node.get("type") or "") != "junction":
                    continue
                node_id = str(node["id"])
                if node_id not in tee_node_ids and node_id not in cross_conductive_ids:
                    continue
                x = int(round(float(node["x"])))
                y = int(round(float(node["y"])))
                if not _ink_at(x, y):
                    continue
                arm_distance = 5
                arms = sum(
                    1
                    for dx, dy in ((arm_distance, 0), (-arm_distance, 0), (0, arm_distance), (0, -arm_distance))
                    if _ink_at(x + dx, y + dy)
                )
                if arms >= 3:
                    conductive_junction_node_ids.add(node_id)
        else:
            conductive_junction_node_ids = {
                str(node["id"])
                for node in nodes
                if str(node.get("type") or "") == "junction"
                and (str(node["id"]) in tee_node_ids or str(node["id"]) in cross_conductive_ids)
            }

    def graph_node_id(node_id: str, wire_id: str | None) -> str:
        node = nodes_by_id.get(str(node_id), {})
        if (
            junction_node_policy in ("explicit", "all")
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
        if class_name != "junction" and len(symbol.get("terminals") or []) > 2:
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
                linked = True
        if linked:
            continue
        if str(symbol.get("class_name") or symbol.get("type") or "") not in TOUCHING_WIRE_FALLBACK_CLASSES:
            continue
        for wire_id in symbol.get("touching_wire_ids", []):
            for root in root_by_wire.get(str(wire_id), set()):
                symbol_by_root[root].add(symbol["id"])
                terminal_by_root[root].append(
                    {
                        "symbol_id": symbol["id"],
                        "terminal_ref": None,
                        "terminal_name": None,
                        "wire_id": wire_id,
                        "reason": "touching_wire_fallback",
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
