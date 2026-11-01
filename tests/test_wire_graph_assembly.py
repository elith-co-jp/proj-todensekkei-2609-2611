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


def test_card_terminal_where_a_wire_stops_inside_the_box_becomes_a_port():
    from PIL import Image, ImageDraw

    from experiment_symbol_port_inference import infer_ports_for_symbol
    from run_yolo_e2e_pipeline import port_inference_config

    image = Image.new("RGB", (300, 300), "white")
    draw = ImageDraw.Draw(image)
    draw.rectangle((50, 50, 250, 200), outline="black")
    draw.line((100, 20, 100, 70), fill="black", width=2)
    draw.ellipse((96, 66, 104, 74), fill="black")
    draw.line((100, 185, 100, 290), fill="black", width=2)
    draw.ellipse((96, 181, 104, 189), fill="black")
    card = {"id": "card", "class_name": "other", "bbox": {"x0": 50, "y0": 50, "x1": 250, "y1": 200}}
    wires = [
        {"id": "plus", "orientation": "v", "polyline": [[100, 20], [100, 70]], "confidence": 0.9, "visible_coverage": 0.9},
        {"id": "minus", "orientation": "v", "polyline": [[100, 185], [100, 290]], "confidence": 0.9, "visible_coverage": 0.9},
    ]
    config = port_inference_config("template_confident_relaxed_span_ink02")

    ports = infer_ports_for_symbol(card, wires, image, config=config)
    assert any(p["wire_id"] == "minus" and abs(p["point"][1] - 185) <= 3 for p in ports)
    without = infer_ports_for_symbol(card, wires, image, config={**config, "wire_end_port_classes": set()})
    assert not any(p["wire_id"] == "minus" for p in without)

def test_dot_roles_follow_the_ink():
    import numpy as np
    from PIL import Image, ImageDraw

    from dot_roles import assign_dot_roles

    image = Image.new("L", (400, 200), 255)
    draw = ImageDraw.Draw(image)
    draw.line((20, 100, 180, 100), fill=0, width=2)
    draw.line((100, 100, 100, 190), fill=0, width=2)
    draw.ellipse((96, 96, 104, 104), fill=0)
    draw.line((300, 10, 300, 190), fill=0, width=2)
    draw.ellipse((295, 95, 305, 105), fill=0)
    for x in (250, 262, 274):
        draw.rectangle((x, 92, x + 6, 108), outline=0, width=2)
    for x in (318, 330):
        draw.rectangle((x, 92, x + 6, 108), outline=0, width=2)
    binary = (np.asarray(image) < 128).astype(np.uint8) * 255

    def box(cx, cy, half):
        return {"x0": cx - half, "y0": cy - half, "x1": cx + half, "y1": cy + half}

    symbols = [
        {"id": "t_join", "class_name": "junction", "bbox": box(100, 100, 7), "confidence": 0.9},
        {"id": "t_join_again", "class_name": "junction", "bbox": box(102, 101, 7), "confidence": 0.5},
        {"id": "terminal", "class_name": "junction", "bbox": box(300, 100, 7), "confidence": 0.9},
    ]
    assign_dot_roles(binary, symbols)
    roles = {s["id"]: s["dot_role"] for s in symbols}
    assert roles["t_join"]["role"] == "junction" and not roles["t_join"]["suppressed"]
    assert roles["t_join_again"]["suppressed"] and roles["t_join_again"]["duplicate_of"] == "t_join"
    assert roles["terminal"]["role"] == "terminal"
