"""Wire graph assembly on generated geometry: which wire ends may be joined across a gap."""

import pytest

pytest.importorskip("cv2")
from analysis import runtime  # noqa: E402,F401  registers the analysis tool imports

from build_from_to_review_outputs import build_predicted_graph_pairs  # noqa: E402

OPTIONS = {
    "internal_bridge_classes": set(),
    "node_anchor_classes": set(),
    "node_anchor_threshold": 0,
    "terminal_gap_bridge_classes": set(),
    "terminal_gap_bridge_max": 0,
    "wire_gap_bridge_max": 80,
    "gap_bridge_align_threshold": 28,
    "wire_gap_bridge_terminal_anchor_threshold": 18,
    "terminal_terminal_bridge_classes": set(),
    "terminal_terminal_bridge_max": 0,
    "gap_bridge_text_overlap_max": 1.0,
    "gap_bridge_symbol_overlap_max": 1.0,
    "nonconductive_anchor_policy": "all",
    "endpoint_bridge_max": 60,
}


def vertical_and_horizontal(v_start: int, v_end: int) -> tuple[dict, list[dict]]:
    """A vertical wire x=100 from v_start to v_end and a horizontal wire y=100 from x=0 to 200."""
    nodes = [
        {"id": "vt", "x": 100, "y": v_start, "type": "endpoint"},
        {"id": "vb", "x": 100, "y": v_end, "type": "endpoint"},
        {"id": "hl", "x": 0, "y": 100, "type": "endpoint"},
        {"id": "hr", "x": 200, "y": 100, "type": "endpoint"},
    ]
    edges = [{"id": "e_h", "from_node_id": "hl", "to_node_id": "hr", "wire_id": "h"}]
    if v_start < 100 < v_end:
        nodes.append({"id": "x", "x": 100, "y": 100, "type": "junction"})
        edges = [
            {"id": "e_h1", "from_node_id": "hl", "to_node_id": "x", "wire_id": "h"},
            {"id": "e_h2", "from_node_id": "x", "to_node_id": "hr", "wire_id": "h"},
            {"id": "e_v1", "from_node_id": "vt", "to_node_id": "x", "wire_id": "v"},
            {"id": "e_v2", "from_node_id": "x", "to_node_id": "vb", "wire_id": "v"},
        ]
    else:
        edges.append({"id": "e_v", "from_node_id": "vt", "to_node_id": "vb", "wire_id": "v"})
    payload = {
        "image_size": {"width": 1755, "height": 1241},
        "quality": {},
        "nodes": nodes,
        "edges": edges,
        "wires": [
            {"id": "v", "orientation": "v", "bbox": {"x0": 99, "y0": v_start, "x1": 101, "y1": v_end}},
            {"id": "h", "orientation": "h", "bbox": {"x0": 0, "y0": 99, "x1": 200, "y1": 101}},
        ],
    }
    records = [
        {"id": "a", "member_key": "A", "symbol_id": "A", "class_name": "contact_a", "point": [100, v_start], "wire_id": "v"},
        {"id": "b", "member_key": "B", "symbol_id": "B", "class_name": "contact_a", "point": [0, 100], "wire_id": "h"},
    ]
    return payload, records


@pytest.mark.parametrize("outward_only", [False, True])
def test_wire_end_short_of_a_perpendicular_wire_is_joined(outward_only):
    payload, records = vertical_and_horizontal(40, 90)
    pairs = build_predicted_graph_pairs(payload, records, **OPTIONS, wire_gap_bridge_outward_only=outward_only)
    assert pairs == {("A", "B")}


def test_wire_crossing_a_line_without_a_dot_is_not_joined_to_it_from_its_far_end():
    payload, records = vertical_and_horizontal(40, 140)
    assert build_predicted_graph_pairs(payload, records, **OPTIONS, wire_gap_bridge_outward_only=False) == {("A", "B")}
    assert build_predicted_graph_pairs(payload, records, **OPTIONS, wire_gap_bridge_outward_only=True) == set()

