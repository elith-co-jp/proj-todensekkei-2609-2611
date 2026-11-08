"""端子から配線グラフをたどり、端子どうしの直接の接続（from-to）を求める。線の途切れの橋渡しを含む。"""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Callable
from typing import Any

from PIL import Image

from analysis.wiring import classes
from analysis.wiring.detect import make_binary_mask, support_vector_for_span
from analysis.wiring.geometry import (
    node_point,
    payload_box,
    point_distance,
    point_segment_projection,
    segment_bounds,
    segment_box_overlap_ratio,
    segment_orientation,
    segment_text_overlap_ratio,
)
from analysis.wiring.params import DEFAULT, BridgeParams

# 端子どうしを記号の中で結ぶ記号（2 端子の機器）。
INTERNAL_BRIDGE_CLASSES = classes.TWO_TERMINAL_DEVICES
# 端子が配線に結ばれていないとき、近くのノードに結ぶ記号。
NODE_ANCHOR_CLASSES = classes.PASS_THROUGH
# 端子が配線に結ばれていないとき、同じ向きの配線の端まで橋渡しする記号。
TERMINAL_GAP_BRIDGE_CLASSES = classes.TWO_TERMINAL_DEVICES | (classes.PASS_THROUGH - {"terminal"})
# 端子どうしを直接結ぶ記号（距離は BridgeParams.terminal_terminal_bridge_max_px）。
TERMINAL_TERMINAL_BRIDGE_CLASSES = frozenset({classes.JUNCTION})


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


def direct_terminal_key(symbol_id: str, terminal_ref: str, index: int) -> str:
    return f"{symbol_id}:{terminal_ref or f'P{index:02d}'}"


def direct_terminal_view(
    symbol: dict[str, Any],
    terminal: dict[str, Any],
    member_key: str,
    links: list[dict[str, Any]],
) -> dict[str, Any]:
    raw_ocr = symbol.get("label_ocr", {}).get("raw_text") if symbol.get("label_ocr") else None
    label_source = (
        "ocr_word_rule"
        if symbol.get("label_word_rule")
        else "ocr_candidate"
        if symbol.get("label_candidate")
        else "detection"
    )
    return {
        "id": member_key,
        "symbol_id": symbol["id"],
        "class_name": symbol.get("class_name") or symbol.get("type"),
        "symbol_confidence": symbol.get("confidence"),
        "symbol_bbox": symbol.get("bbox"),
        "label": symbol.get("label") or "",
        "label_source": label_source,
        "raw_ocr": raw_ocr,
        "label_candidate": compact_label_candidate(symbol.get("label_candidate")),
        "label_word_rule": compact_word_rule_label(symbol.get("label_word_rule")),
        "terminal_ref": terminal.get("ref"),
        "terminal_name": terminal.get("name") or "",
        "point": [round(float(value), 1) for value in terminal["point"]],
        "source": terminal.get("source"),
        "wire_ids": sorted({str(link["wire_id"]) for link in links if link.get("wire_id")}),
        "node_ids": sorted({str(link["node_id"]) for link in links if link.get("node_id")}),
        "link_reasons": sorted({str(link["reason"]) for link in links if link.get("reason")}),
    }


def build_direct_terminal_records(
    payload: dict[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    records: list[dict[str, Any]] = []
    terminal_by_key: dict[str, dict[str, Any]] = {}
    for symbol in payload.get("symbols", []):
        links_by_terminal: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for link in symbol.get("terminal_links", []):
            links_by_terminal[str(link.get("terminal_ref") or "")].append(link)
        for index, terminal in enumerate(symbol.get("terminals") or [], start=1):
            terminal_ref = str(terminal.get("ref") or "")
            member_key = direct_terminal_key(str(symbol["id"]), terminal_ref, index)
            links = links_by_terminal.get(terminal_ref) or []
            terminal_by_key[member_key] = direct_terminal_view(symbol, terminal, member_key, links)
            for link_index, link in enumerate(links or [{}], start=1):
                records.append(
                    {
                        "id": f"terminal_{len(records) + 1:05d}",
                        "member_key": member_key,
                        "symbol_id": symbol["id"],
                        "class_name": symbol.get("class_name") or symbol.get("type") or "",
                        "bbox": symbol.get("bbox"),
                        "point": [float(terminal["point"][0]), float(terminal["point"][1])],
                        "wire_id": link.get("wire_id"),
                        "wire_id_hint": terminal.get("wire_id_hint"),
                        "node_id": link.get("node_id"),
                        "peer_symbol_id": link.get("peer_symbol_id"),
                        "link_index": link_index,
                    }
                )
    return records, terminal_by_key


def blank_run_measure(source_image: Image.Image) -> Callable[[tuple[float, float], tuple[float, float]], int]:
    binary = make_binary_mask(source_image)
    scale = source_image.width / 1755

    def longest_blank(start: tuple[float, float], end: tuple[float, float]) -> int:
        (x0, y0), (x1, y1) = start, end
        if abs(x1 - x0) >= abs(y1 - y0):
            lo, hi = sorted((round(x0), round(x1)))
            support = support_vector_for_span(binary, "h", round((y0 + y1) / 2), lo, hi + 1, half_width=2)
        else:
            lo, hi = sorted((round(y0), round(y1)))
            support = support_vector_for_span(binary, "v", round((x0 + x1) / 2), lo, hi + 1, half_width=2)
        longest = run = 0
        for inked in support:
            run = 0 if inked else run + 1
            longest = max(longest, run)
        return round(longest / scale)

    return longest_blank


def build_direct_from_to(
    payload: dict[str, Any], source_image: Image.Image | None = None, trace: dict[str, Any] | None = None,
    params: BridgeParams = DEFAULT.bridge,
) -> dict[str, Any]:
    terminal_records, terminal_by_key = build_direct_terminal_records(payload)
    bridge_stats: Counter[str] = Counter()
    blank_run = blank_run_measure(source_image) if source_image is not None and params.wire_gap_bridge_max_blank_px > 0 else None
    pairs = build_predicted_graph_pairs(
        payload,
        terminal_records,
        internal_bridge_classes=INTERNAL_BRIDGE_CLASSES,
        node_anchor_classes=NODE_ANCHOR_CLASSES,
        node_anchor_threshold=params.node_anchor_px,
        terminal_gap_bridge_classes=TERMINAL_GAP_BRIDGE_CLASSES,
        terminal_gap_bridge_max=params.terminal_gap_bridge_max_px,
        wire_gap_bridge_max=params.wire_gap_bridge_max_px,
        gap_bridge_align_threshold=params.gap_bridge_align_px,
        wire_gap_bridge_terminal_anchor_threshold=params.wire_gap_terminal_anchor_px,
        terminal_terminal_bridge_classes=TERMINAL_TERMINAL_BRIDGE_CLASSES,
        terminal_terminal_bridge_max=params.terminal_terminal_bridge_max_px,
        gap_bridge_text_overlap_max=1.0,
        gap_bridge_symbol_overlap_max=1.0,
        nonconductive_anchor_policy="all",
        bridge_stats=bridge_stats,
        endpoint_bridge_max=params.endpoint_bridge_max_px,
        wire_gap_bridge_outward_only=params.wire_gap_bridge_outward_only,
        gap_blank_run=blank_run,
        wire_gap_bridge_max_blank=params.wire_gap_bridge_max_blank_px,
        terminal_gap_bridge_max_blank=params.terminal_gap_bridge_max_blank_px,
        gap_bridge_device_classes=set(classes.COILS),
        own_device_jump_classes=set(classes.COILS | classes.CONTACTS),
        device_jump_across=params.device_jump_across_px,
        device_jump_end=params.device_jump_end_px,
        trace=trace,
    )
    valid_pairs = sorted(
        pair for pair in pairs if pair[0] in terminal_by_key and pair[1] in terminal_by_key
    )
    connected_terminal_ids = {terminal_id for pair in valid_pairs for terminal_id in pair}
    connections = []
    for index, (left_key, right_key) in enumerate(valid_pairs, start=1):
        left = terminal_by_key[left_key]
        right = terminal_by_key[right_key]
        left_endpoint = {
            key: left.get(key)
            for key in ("id", "symbol_id", "class_name", "label", "terminal_ref", "terminal_name", "point")
        }
        right_endpoint = {
            key: right.get(key)
            for key in ("id", "symbol_id", "class_name", "label", "terminal_ref", "terminal_name", "point")
        }
        connections.append(
            {
                "id": f"from_to_{index:05d}",
                "type": "internal" if left["symbol_id"] == right["symbol_id"] else "wire",
                "directed": False,
                "from": left_endpoint,
                "to": right_endpoint,
                "basis": "first_terminal_reached_by_wire_graph_traversal",
                "status": "predicted",
            }
        )
    terminals = [terminal_by_key[key] for key in sorted(terminal_by_key)]
    return {
        "schema_version": "todensekkei.from_to.v1",
        "source": payload.get("source"),
        "definition": "Direct terminal-to-terminal connections; connected components are not expanded into all pairs.",
        "directed": False,
        "terminal_count": len(terminals),
        "connected_terminal_count": len(connected_terminal_ids),
        "unconnected_terminal_count": len(terminals) - len(connected_terminal_ids),
        "connection_count": len(connections),
        "terminals": terminals,
        "unconnected_terminal_ids": [
            terminal["id"] for terminal in terminals if terminal["id"] not in connected_terminal_ids
        ],
        "connections": connections,
        "resolver": {
            "algorithm": "direct_wire_graph_traversal",
            "internal_bridge_classes": sorted(INTERNAL_BRIDGE_CLASSES),
            "node_anchor_classes": sorted(NODE_ANCHOR_CLASSES),
            "node_anchor_threshold": params.node_anchor_px,
            "terminal_gap_bridge_classes": sorted(TERMINAL_GAP_BRIDGE_CLASSES),
            "terminal_gap_bridge_max": params.terminal_gap_bridge_max_px,
            "wire_gap_bridge_max": params.wire_gap_bridge_max_px,
            "gap_bridge_align_threshold": params.gap_bridge_align_px,
            "wire_gap_terminal_anchor_threshold": params.wire_gap_terminal_anchor_px,
            "terminal_terminal_bridge_classes": sorted(TERMINAL_TERMINAL_BRIDGE_CLASSES),
            "terminal_terminal_bridge_max": params.terminal_terminal_bridge_max_px,
            "wire_gap_bridge_outward_only": params.wire_gap_bridge_outward_only,
            "wire_gap_bridge_max_blank": params.wire_gap_bridge_max_blank_px if blank_run is not None else 0.0,
            "terminal_gap_bridge_max_blank": params.terminal_gap_bridge_max_blank_px if blank_run is not None else 0.0,
            "device_jump_across": params.device_jump_across_px,
            "bridge_stats": dict(sorted(bridge_stats.items())),
        },
    }


def sorted_pair(left: str, right: str) -> tuple[str, str]:
    return tuple(sorted((left, right)))  # type: ignore[return-value]


# T 字の判定: 配線の端が交点から線の向きにこれ以内なら、そこで終わるとみなす。
TEE_ALONG_PX = 8.0
# 線に直角な向きは、配線の枠からこれ以内。8 px にしていたとき、11 px おきの平行線（ER21276）で隣の線の端を
# この交点で終わる線とみなし、黒丸の無い交差をつないでいた。
TEE_ACROSS_PX = 3.0


def _wire_ends_at_node(
    wires: list[dict[str, Any]], node_x: float, node_y: float, tol: float = TEE_ALONG_PX, across: float = TEE_ACROSS_PX,
) -> bool:
    """True when some wire's span terminates at the node (tee join), rather
    than all incident wires passing straight through it (bare crossing)."""
    for wire in wires:
        box = wire.get("bbox") or wire.get("box") or {}
        if wire.get("orientation") == "h":
            if not (float(box.get("y0", -1)) - across <= node_y <= float(box.get("y1", -1)) + across):
                continue
            if not (float(box.get("x0", -1)) - tol <= node_x <= float(box.get("x1", -1)) + tol):
                continue
            if abs(node_x - float(box.get("x0", -999))) <= tol or abs(node_x - float(box.get("x1", -999))) <= tol:
                return True
        elif wire.get("orientation") == "v":
            if not (float(box.get("x0", -1)) - across <= node_x <= float(box.get("x1", -1)) + across):
                continue
            if not (float(box.get("y0", -1)) - tol <= node_y <= float(box.get("y1", -1)) + tol):
                continue
            if abs(node_y - float(box.get("y0", -999))) <= tol or abs(node_y - float(box.get("y1", -999))) <= tol:
                return True
    return False


def _symbol_center(symbol: dict[str, Any]) -> tuple[float, float]:
    center = symbol.get("center")
    if center:
        return float(center[0]), float(center[1])
    box = symbol.get("bbox") or {}
    return (float(box.get("x0", 0)) + float(box.get("x1", 0))) / 2, (float(box.get("y0", 0)) + float(box.get("y1", 0))) / 2


def dotted_junction_nodes(nodes: list[dict[str, Any]], dots: list[tuple[float, float]], threshold: float) -> set[str]:
    """黒丸（分岐点）が付く交点。黒丸は 1 つの点に描くので、1 つの黒丸がつなぐのは threshold 以内で最も近い交点だけ。

    間隔の狭い平行線（ER21277 の 11〜12 px おきの横線）では、黒丸から threshold 以内に黒丸の無い交差も入る。
    範囲内をすべてつなぐと、その交差で別々の配線が 1 つになった。
    """
    crossings = [(str(node["id"]), node_point(node)) for node in nodes if str(node.get("type") or "") == "junction"]
    result = set()
    for dot in dots:
        near = [(point_distance(point, dot), node_id) for node_id, point in crossings if point_distance(point, dot) <= threshold]
        if near:
            result.add(min(near)[1])
    return result


def conductive_junction_nodes(payload: dict[str, Any]) -> set[str]:
    """電気的につながる交点: 黒丸の付く交点と、（policy が explicit でなければ）配線が終わる T 字の交点。"""
    config = payload.get("quality", {}).get("terminal_linking", {})
    policy = str(config.get("junction_node_policy") or "all")
    classes = set(config.get("junction_node_classes") or [])
    threshold = float(config.get("junction_node_threshold") or 24.0)
    nodes = list(payload.get("nodes", []))
    dots = [
        _symbol_center(symbol) for symbol in payload.get("symbols", [])
        if str(symbol.get("class_name") or symbol.get("type") or "") in classes
    ]
    result = dotted_junction_nodes(nodes, dots, threshold)
    if policy != "explicit":
        wires = list(payload.get("wires", []))
        result |= {
            str(node["id"]) for node in nodes
            if str(node.get("type") or "") == "junction" and _wire_ends_at_node(wires, *node_point(node))
        }
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
    gap_bridge_text_overlap_max: float,
    gap_bridge_symbol_overlap_max: float,
    nonconductive_anchor_policy: str,
    bridge_stats: Counter[str] | None = None,
    endpoint_bridge_max: float = 0.0,
    wire_gap_bridge_outward_only: bool = False,
    gap_blank_run: Callable[[tuple[float, float], tuple[float, float]], int] | None = None,
    wire_gap_bridge_max_blank: float = 0.0,
    terminal_gap_bridge_max_blank: float = 0.0,
    gap_bridge_device_classes: set[str] | None = None,
    own_device_jump_classes: set[str] | None = None,
    device_jump_across: float = 0.0,
    device_jump_end: float = 1.0,
    trace: dict[str, Any] | None = None,
) -> set[tuple[str, str]]:
    nodes_by_id = {str(node["id"]): node for node in payload.get("nodes", [])}
    edges = list(payload.get("edges", []))
    wires_by_id = {str(wire["id"]): wire for wire in payload.get("wires", [])}
    conductive_nodes = conductive_junction_nodes(payload)
    text_regions = list(payload.get("text_regions") or [])
    graph: dict[str, set[str]] = defaultdict(set)
    coords: dict[str, tuple[float, float]] = {}
    terminal_by_stop: dict[str, str] = {}
    stop_by_member: dict[str, str] = {}
    terminal_members_by_graph_node: dict[str, set[str]] = defaultdict(set)
    stats = bridge_stats if bridge_stats is not None else Counter()
    edge_kind: dict[tuple[str, str], str] = {}
    gap_bridged_stops: set[str] = set()
    excluded_segments = payload.get("quality", {}).get("wire_decoration_filter", {}).get("excluded_segments", [])

    def follows_excluded_decoration(start: tuple[float, float], end: tuple[float, float]) -> bool:
        # Crossing a card border is allowed; following a rejected border is not.
        tolerance = max(2.0, 2.0 * payload.get("image_size", {}).get("width", 1755) / 1755)
        for segment in excluded_segments:
            along, across = (0, 1) if segment["orientation"] == "h" else (1, 0)
            if max(abs(start[across] - segment["axis"]), abs(end[across] - segment["axis"])) > tolerance:
                continue
            lo, hi = sorted((start[along], end[along]))
            overlap = min(hi, segment["end"]) - max(lo, segment["start"])
            if overlap > max(2.0, min(6.0, (hi - lo) * 0.25)):
                return True
        return False

    symbol_boxes: dict[str, tuple[str, dict[str, float]]] = {}
    for record in terminal_records:
        symbol_id = str(record.get("symbol_id") or "")
        if not symbol_id or symbol_id in symbol_boxes:
            continue
        box = payload_box(record)
        if not box:
            continue
        symbol_boxes[symbol_id] = (str(record.get("class_name") or ""), box)

    device_terminals = [
        (str(record.get("symbol_id") or ""), (float(record["point"][0]), float(record["point"][1])))
        for record in terminal_records if str(record.get("class_name") or "") in classes.TWO_TERMINAL_DEVICES
    ]

    def passes_device_terminal(start: tuple[float, float], end: tuple[float, float]) -> bool:
        """橋渡しの線の途中に機器の端子が乗っている。線はそこで機器に入るので、橋渡しで越えるとその機器を飛ばす。
        自分の機器のもう一方の端子も数える。越えると機器の中を突き抜けて両端子をつなぐ（ER21302 の接点の上の端子から
        下の配線への橋渡し）。橋渡しを始める端子は範囲の端にあるので数えない。"""
        if device_jump_across <= 0:
            return False
        vertical = abs(end[1] - start[1]) > abs(end[0] - start[0])
        lo, hi = sorted((start[1], end[1]) if vertical else (start[0], end[0]))
        for _symbol_id, (sx, sy) in device_terminals:
            along, across = (sy, sx - start[0]) if vertical else (sx, sy - start[1])
            if abs(across) <= device_jump_across and lo + device_jump_end < along < hi - device_jump_end:
                return True
        return False

    ported = Counter(symbol_id for symbol_id, _point in device_terminals)
    # 端子を決められなかった機器は端子の記録に現れないので、枠は検出した記号から取る。
    unported_device_boxes = [
        (str(symbol.get("id") or ""), box) for symbol in payload.get("symbols", [])
        if str(symbol.get("class_name") or "") in classes.TWO_TERMINAL_DEVICES
        and ported[str(symbol.get("id") or "")] < 2 and (box := payload_box(symbol))
    ]

    def passes_unported_device(start: tuple[float, float], end: tuple[float, float], own_symbol_id: str) -> bool:
        """端子が 2 個に満たない接点・コイル（線が薄く端子を決められなかった）の枠を、橋渡しの線がまるごと越える。
        端子で見る passes_device_terminal では見えない（ER21302 の母線の黒丸からコネクタへの橋渡しが接点をまたいだ）。"""
        if device_jump_across <= 0:
            return False
        vertical = abs(end[1] - start[1]) > abs(end[0] - start[0])
        lo, hi = sorted((start[1], end[1]) if vertical else (start[0], end[0]))
        axis = (start[0] + end[0]) / 2 if vertical else (start[1] + end[1]) / 2
        for symbol_id, box in unported_device_boxes:
            if symbol_id == own_symbol_id:
                continue
            a0, a1, c0, c1 = (box["y0"], box["y1"], box["x0"], box["x1"]) if vertical else (box["x0"], box["x1"], box["y0"], box["y1"])
            if c0 <= axis <= c1 and lo < a0 and a1 < hi:
                return True
        return False

    def graph_node_id(node_id: str, wire_id: str | None) -> str:
        node = nodes_by_id.get(str(node_id), {})
        if str(node.get("type") or "") == "junction" and str(node_id) not in conductive_nodes:
            return f"node:{node_id}@{wire_id or 'unknown'}"
        return f"node:{node_id}"

    def add_graph_edge(left: str, right: str, kind: str) -> None:
        if left == right:
            return
        graph[left].add(right)
        graph[right].add(left)
        edge_kind.setdefault(sorted_pair(left, right), kind)

    def crosses_blocking_symbol(
        start: tuple[float, float],
        end: tuple[float, float],
        *,
        allowed_symbol_ids: set[str],
    ) -> bool:
        # A missing wire never runs through the middle of a coil: a bridge there would short it.
        if gap_bridge_device_classes:
            horizontal = abs(end[0] - start[0]) >= abs(end[1] - start[1])
            lo, hi = sorted((start[0], end[0]) if horizontal else (start[1], end[1]))
            across = (start[1] + end[1]) / 2 if horizontal else (start[0] + end[0]) / 2
            for class_name, box in symbol_boxes.values():
                if class_name not in gap_bridge_device_classes:
                    continue
                a0, a1, c0, c1 = (box["x0"], box["x1"], box["y0"], box["y1"]) if horizontal else (box["y0"], box["y1"], box["x0"], box["x1"])
                quarter = (c1 - c0) / 4
                if lo <= (a0 + a1) / 2 <= hi and c0 + quarter <= across <= c1 - quarter:
                    return True
        if gap_bridge_symbol_overlap_max >= 1.0:
            return False
        for symbol_id, (class_name, box) in symbol_boxes.items():
            if symbol_id in allowed_symbol_ids or class_name == "junction":
                continue
            if segment_box_overlap_ratio(start, end, box) > gap_bridge_symbol_overlap_max:
                return True
        return False

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

    def is_nonconductive_junction_node(node_id: str) -> bool:
        node = nodes_by_id.get(str(node_id), {})
        return str(node.get("type") or "") == "junction" and str(node_id) not in conductive_nodes

    def nearest_incident_wire_id(node_id: str, point: tuple[float, float]) -> str | None:
        candidates: list[tuple[float, str]] = []
        for incident_wire_id in incident_wire_by_node.get(str(node_id), set()):
            if not incident_wire_id:
                continue
            for segment in edges_by_wire.get(incident_wire_id, []):
                if str(segment.get("from_node_id")) != str(node_id) and str(segment.get("to_node_id")) != str(node_id):
                    continue
                _t, distance, _projection = point_segment_projection(point, segment["start"], segment["end"])
                candidates.append((distance, incident_wire_id))
        if not candidates:
            return None
        return min(candidates, key=lambda item: (item[0], item[1]))[1]

    def graph_ids_for_anchor_node(record: dict[str, Any], node_id: str, preferred_wire_id: str | None) -> list[str]:
        incident_wires = incident_wire_by_node.get(str(node_id), set())
        if nonconductive_anchor_policy == "preferred" and preferred_wire_id:
            return [graph_node_id(node_id, preferred_wire_id)]
        if nonconductive_anchor_policy == "preferred" and is_nonconductive_junction_node(str(node_id)):
            point = (float(record["point"][0]), float(record["point"][1]))
            nearest_wire_id = nearest_incident_wire_id(str(node_id), point)
            return [graph_node_id(node_id, nearest_wire_id)] if nearest_wire_id else [graph_node_id(node_id, None)]
        return [graph_node_id(node_id, incident_wire_id) for incident_wire_id in sorted(incident_wires)] or [graph_node_id(node_id, None)]

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
        terminal_point = (float(record["point"][0]), float(record["point"][1]))
        if endpoint_node:
            bridge_end = node_point(nodes_by_id[endpoint_node])
        else:
            _projection_t, _distance, bridge_end = point_segment_projection(terminal_point, segment["start"], segment["end"])
        if follows_excluded_decoration(terminal_point, bridge_end):
            stats["terminal_gap_bridge_rejected_decoration_count"] += 1
            return False
        text_overlap = segment_text_overlap_ratio(terminal_point, bridge_end, text_regions)
        if text_overlap > gap_bridge_text_overlap_max:
            stats["terminal_gap_bridge_rejected_text_overlap_count"] += 1
            return False
        if gap_blank_run is not None and terminal_gap_bridge_max_blank > 0 \
                and gap_blank_run(terminal_point, bridge_end) > terminal_gap_bridge_max_blank:
            stats["terminal_gap_bridge_rejected_blank_count"] += 1
            return False
        if crosses_blocking_symbol(terminal_point, bridge_end, allowed_symbol_ids={str(record.get("symbol_id") or "")}):
            stats["terminal_gap_bridge_rejected_symbol_overlap_count"] += 1
            return False
        if passes_device_terminal(terminal_point, bridge_end):
            stats["terminal_gap_bridge_rejected_device_jump_count"] += 1
            return False
        if passes_unported_device(terminal_point, bridge_end, str(record.get("symbol_id") or "")):
            stats["terminal_gap_bridge_rejected_unported_device_count"] += 1
            return False
        if endpoint_node:
            add_graph_edge(stop_id, graph_node_id(endpoint_node, wire_id), "terminal_gap_bridge")
        else:
            stops_by_edge[edge_id].append((t, stop_id))
            gap_bridged_stops.add(stop_id)
        stats["terminal_gap_bridge_count"] += 1
        stats[f"terminal_gap_bridge_{str(record.get('class_name') or 'unknown')}"] += 1
        if gap > 1:
            stats["terminal_gap_bridge_nonzero_gap_count"] += 1
        return True

    def add_wire_gap_bridges() -> None:
        if wire_gap_bridge_max <= 0:
            return

        wire_span: dict[str, tuple[float, float]] = {}
        for span_wire_id, segments in edges_by_wire.items():
            along = 0 if str(segments[0]["orientation"]) == "h" else 1
            values = [segment[key][along] for segment in segments for key in ("start", "end")]
            wire_span[span_wire_id] = (min(values), max(values))

        def leaves_wire_end(wire_id: str, orientation: str, point: tuple[float, float], target: tuple[float, float]) -> bool:
            # A bridge running back over its own wire reaches a line that wire crosses,
            # which would turn a crossing the junction rule rejected into a connection.
            lo, hi = wire_span.get(wire_id, (float("-inf"), float("inf")))
            along = 0 if orientation == "h" else 1
            tolerance = 2.0
            if target[along] < lo - tolerance:
                return point[along] <= lo + tolerance
            if target[along] > hi + tolerance:
                return point[along] >= hi - tolerance
            return False

        def has_terminal_anchor(target_edge_id: str, projection: tuple[float, float]) -> bool:
            if wire_gap_bridge_terminal_anchor_threshold <= 0:
                return True
            for _t, stop_id in stops_by_edge.get(target_edge_id, []):
                if stop_id in terminal_by_stop and point_distance(coords[stop_id], projection) <= wire_gap_bridge_terminal_anchor_threshold:
                    return True
            return False

        def is_corner(node_id: str, wire_id: str, orientation: str) -> bool:
            """この点で直交する配線も終わっている（線がここで向きを変える角）。角から先へは線が続かないので、
            角からの橋渡しは間隔の狭い隣の平行線へ渡る（ER21277・ER21276 の 11 px おきの横線）。"""
            node = nodes_by_id.get(node_id)
            if node is None:
                return False
            point = node_point(node)
            for other in incident_wire_by_node.get(node_id, set()):
                segments = edges_by_wire.get(other, [])
                if not other or other == wire_id or not segments or str(segments[0]["orientation"]) == orientation:
                    continue
                along = 0 if str(segments[0]["orientation"]) == "h" else 1
                lo, hi = wire_span[other]
                if min(abs(point[along] - lo), abs(point[along] - hi)) <= 2.0:
                    return True
            return False

        # 端子（member）ごとの記号と、配線ごとに乗っている端子。1 つの端子が配線とノードの両方に結ばれると停留点が
        # 2 つできるので、停留点ではなく端子で見る。
        symbol_of_member = {str(record["member_key"]): str(record.get("symbol_id") or "") for record in terminal_records}
        # 自分の機器のもう一方の端子を越える橋渡しを止める機器。既定（None）はコイルの中を通さない機器と同じ。
        jump_classes = gap_bridge_device_classes if own_device_jump_classes is None else own_device_jump_classes
        coil_symbols = {sid for sid, (class_name, _box) in symbol_boxes.items() if class_name in (jump_classes or set())}
        members_on_wire: dict[str, set[str]] = defaultdict(set)
        for wire_edge_id, segment in edge_segments.items():
            for _t, stop_id in stops_by_edge.get(wire_edge_id, []):
                if stop_id in terminal_by_stop:
                    members_on_wire[str(segment.get("wire_id") or "")].add(terminal_by_stop[stop_id])

        def jumps_own_coil(wire_id: str, source_key: str, start: tuple[float, float], end: tuple[float, float]) -> bool:
            """コイルの一方の端子が乗る配線がコイルの中まで伸びて終わっているとき、その端からの橋渡しが同じコイルの
            もう一方の端子（別の配線に結ばれている）を越えると、コイルの両側がつながる（ER21298）。
            接点も同じで、母線の黒丸から下りる線が接点の中で終わり、そこからの橋渡しが接点の下の端子を越えた
            （ER21261・ER21276、DD-0020）。

            橋渡しの出発点（線の端の点）にアンカーされた端子は、そこから橋渡しが出るので越えたことにしない（ER21279）。"""
            on_wire = members_on_wire.get(wire_id, set()) | terminal_members_by_graph_node.get(source_key, set())
            if device_jump_across <= 0:
                return False
            coils = {symbol_of_member.get(m) for m in on_wire} & coil_symbols
            if not coils:
                return False
            vertical = abs(end[1] - start[1]) > abs(end[0] - start[0])
            lo, hi = sorted((start[1], end[1]) if vertical else (start[0], end[0]))
            for stop_id, member in terminal_by_stop.items():
                if member in on_wire or symbol_of_member.get(member) not in coils or stop_id not in coords:
                    continue
                sx, sy = coords[stop_id]
                along, across = (sy, sx - start[0]) if vertical else (sx, sy - start[1])
                if abs(across) <= device_jump_across and lo + device_jump_end < along < hi + device_jump_end:
                    return True
            return False

        def past_end_stop(edge_id: str, end_point: tuple[float, float], end_t: float) -> str | None:
            """The terminal stop lying on this edge beyond its end point, farthest out (None when there is none)."""
            segment = edge_segments[edge_id]
            other = segment["end"] if end_t == 0.0 else segment["start"]
            direction = (end_point[0] - other[0], end_point[1] - other[1])
            norm = (direction[0] ** 2 + direction[1] ** 2) ** 0.5 or 1.0
            best: tuple[float, str] | None = None
            for t, stop_id in stops_by_edge.get(edge_id, []):
                if stop_id not in terminal_by_stop or abs(t - end_t) > 1e-9:
                    continue
                sx, sy = coords[stop_id]
                out = ((sx - end_point[0]) * direction[0] + (sy - end_point[1]) * direction[1]) / norm
                if out > 2.0 and (best is None or out > best[0]):
                    best = (out, stop_id)
            return best[1] if best else None

        best_by_endpoint: dict[str, tuple[float, str, float, tuple[float, float], str]] = {}
        for source_edge_id, source in edge_segments.items():
            source_wire_id = str(source.get("wire_id") or "")
            source_orientation = str(source["orientation"])
            if source_orientation not in {"h", "v"}:
                continue
            for endpoint_key, point, end_node, end_t in (
                (graph_node_id(str(source["from_node_id"]), source_wire_id), source["start"], source["from_node_id"], 0.0),
                (graph_node_id(str(source["to_node_id"]), source_wire_id), source["end"], source["to_node_id"], 1.0),
            ):
                # A wire detected into a coil can end inside it, short of the coil's far terminal. That terminal
                # sits on the wire past its end, so a bridge leaving the end starts from the terminal; leaving the
                # end node itself would skip the coil and join both its sides (ER21280).
                beyond = past_end_stop(source_edge_id, point, end_t)
                if beyond is not None:
                    endpoint_key, point = beyond, coords[beyond]
                elif is_corner(str(end_node), source_wire_id, source_orientation):
                    stats["wire_gap_bridge_rejected_corner_count"] += 1
                    continue
                is_wire_end = str(nodes_by_id.get(str(end_node), {}).get("type") or "") == "endpoint"
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
                    if wire_gap_bridge_outward_only and not leaves_wire_end(source_wire_id, source_orientation, point, projection):
                        stats["wire_gap_bridge_rejected_inward_count"] += 1
                        continue
                    # A wire's own span END almost touching a perpendicular wire is a drawn T-join:
                    # bridge it even without a symbol terminal sitting on the junction point.
                    if not has_terminal_anchor(target_edge_id, projection) and not (is_wire_end and gap <= endpoint_bridge_max):
                        continue
                    # Markers on a wire (numbered circles, wire-number boxes) leave short blanks;
                    # a long blank stretch is open paper, e.g. between the + and - dots of a card.
                    if gap_blank_run is not None and wire_gap_bridge_max_blank > 0 and gap_blank_run(point, projection) > wire_gap_bridge_max_blank:
                        stats["wire_gap_bridge_rejected_blank_count"] += 1
                        continue
                    if jumps_own_coil(source_wire_id, endpoint_key, point, projection):
                        stats["wire_gap_bridge_rejected_coil_jump_count"] += 1
                        continue
                    candidate = (gap, target_edge_id, t, projection, str(target.get("wire_id") or ""))
                    if endpoint_key not in best_by_endpoint or gap < best_by_endpoint[endpoint_key][0]:
                        best_by_endpoint[endpoint_key] = candidate
        for index, (endpoint_key, (gap, target_edge_id, t, projection, _wire_id)) in enumerate(best_by_endpoint.items(), start=1):
            if follows_excluded_decoration(coords[endpoint_key], projection):
                stats["wire_gap_bridge_rejected_decoration_count"] += 1
                continue
            if segment_text_overlap_ratio(coords[endpoint_key], projection, text_regions) > gap_bridge_text_overlap_max:
                stats["wire_gap_bridge_rejected_text_overlap_count"] += 1
                continue
            if crosses_blocking_symbol(coords[endpoint_key], projection, allowed_symbol_ids=set()):
                stats["wire_gap_bridge_rejected_symbol_overlap_count"] += 1
                continue
            bridge_id = f"bridge:{index:05d}:{target_edge_id}"
            coords[bridge_id] = projection
            stops_by_edge[target_edge_id].append((t, bridge_id))
            add_graph_edge(endpoint_key, bridge_id, "wire_gap_bridge")
            stats["wire_gap_bridge_count"] += 1
            if gap <= endpoint_bridge_max:
                stats["endpoint_bridge_count"] += 1
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
            if follows_excluded_decoration(coords[left], coords[right]):
                stats["terminal_terminal_bridge_rejected_decoration_count"] += 1
                continue
            left_symbol_id = ""
            right_symbol_id = ""
            for record in terminals:
                if record["stop_id"] == left:
                    left_symbol_id = str(record.get("symbol_id") or "")
                elif record["stop_id"] == right:
                    right_symbol_id = str(record.get("symbol_id") or "")
            if crosses_blocking_symbol(coords[left], coords[right], allowed_symbol_ids={left_symbol_id, right_symbol_id}):
                stats["terminal_terminal_bridge_rejected_symbol_overlap_count"] += 1
                continue
            add_graph_edge(left, right, "terminal_terminal_bridge")
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

    peer_links: list[tuple[str, str]] = []
    for record in terminal_records:
        stop_id = f"stop:{record['id']}"
        member_key = str(record["member_key"])
        terminal_by_stop[stop_id] = member_key
        stop_by_member.setdefault(member_key, stop_id)
        coords[stop_id] = (float(record["point"][0]), float(record["point"][1]))
        wire_id = record.get("wire_id")
        node_id = record.get("node_id")
        anchor_node_id = node_id
        if not anchor_node_id and not wire_id and str(record.get("class_name") or "") in node_anchor_classes:
            candidate_node_id, node_distance = nearest_node_id(record["point"])
            if candidate_node_id and node_distance <= node_anchor_threshold:
                anchor_node_id = candidate_node_id
        if anchor_node_id:
            preferred_wire_id = str(wire_id or record.get("wire_id_hint") or "")
            for graph_id in graph_ids_for_anchor_node(record, str(anchor_node_id), preferred_wire_id or None):
                terminal_members_by_graph_node[graph_id].add(str(record["member_key"]))
                add_graph_edge(stop_id, graph_id, "node_anchor")
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
            preferred_wire_id = str(wire_id or record.get("wire_id_hint") or "")
            for graph_id in graph_ids_for_anchor_node(record, str(node_id), preferred_wire_id or None):
                add_graph_edge(stop_id, graph_id, "node_anchor")
            continue
        if record.get("peer_symbol_id"):
            peer_links.append((stop_id, str(record["peer_symbol_id"])))
            continue
        bridge_terminal_to_collinear_wire(record, stop_id)

    # 線をたどってコネクタ・端子台に着いた端子は、その記号の端子と直接つながる。
    for stop_id, peer_symbol_id in peer_links:
        peer_stop = next((s for m, s in stop_by_member.items() if m.startswith(f"{peer_symbol_id}:")), None)
        if peer_stop is not None:
            add_graph_edge(stop_id, peer_stop, "pass_through_trace")

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
        for (_left_t, left), (_right_t, right) in zip(items, items[1:], strict=False):
            add_graph_edge(left, right, "wire")

    add_terminal_terminal_bridges()
    if trace is not None:
        trace.update(graph=graph, edge_kind=edge_kind, coords=coords, terminal_by_stop=terminal_by_stop,
                     members_by_graph_node=terminal_members_by_graph_node, gap_bridged_stops=gap_bridged_stops)

    return pairs_reached_through_graph(graph, terminal_by_stop, terminal_members_by_graph_node) | pairs_inside_symbols(
        terminal_records, internal_bridge_classes,
    )


def pairs_reached_through_graph(
    graph: dict[str, set[str]], terminal_by_stop: dict[str, str], terminal_members_by_graph_node: dict[str, set[str]],
) -> set[tuple[str, str]]:
    """各端子からグラフをたどり、最初に当たる端子（停留点か、端子の結ばれたノード）との組。その先へは進まない。"""
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
    return pairs


def pairs_inside_symbols(terminal_records: list[dict[str, Any]], internal_bridge_classes: set[str]) -> set[tuple[str, str]]:
    """記号の中を通り抜ける種類（端子台など）の端子を、並んだ順に隣どうしで組にする。"""
    pairs: set[tuple[str, str]] = set()
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
        for left, right in zip(unique, unique[1:], strict=False):
            if left["member_key"] != right["member_key"]:
                pairs.add(sorted_pair(str(left["member_key"]), str(right["member_key"])))

    return pairs
