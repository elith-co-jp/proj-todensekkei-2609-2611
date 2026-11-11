"""Wire graph assembly on generated geometry: which wire ends may be joined across a gap."""

import pytest

pytest.importorskip("cv2")
from analysis.wiring.connectivity import build_predicted_graph_pairs  # noqa: E402

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

    from analysis.wiring.ports import infer_ports_for_symbol
    from analysis.wiring.ports import port_inference_config

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

    from analysis.wiring.dot_roles import assign_dot_roles

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


def test_low_confidence_junctions_require_three_wire_arms_and_do_not_duplicate_a_dot():
    from PIL import Image, ImageDraw

    from analysis.wiring.dot_roles import select_junction_detections

    image = Image.new("RGB", (240, 120), "white")
    draw = ImageDraw.Draw(image)
    for x in (50, 120):
        draw.line((x - 30, 60, x + 30, 60), fill="black", width=2)
        draw.line((x, 60, x, 105), fill="black", width=2)
        draw.ellipse((x - 4, 56, x + 4, 64), fill="black")
    draw.line((180, 20, 180, 100), fill="black", width=2)
    draw.ellipse((176, 56, 184, 64), fill="black")

    def detection(identifier, x, confidence, y=60):
        return {
            "id": identifier,
            "class_key": "junction",
            "cx": x / image.width,
            "cy": y / image.height,
            "w": 10 / image.width,
            "h": 10 / image.height,
            "confidence": confidence,
        }

    candidates = [
        detection("normal", 44, 0.9),
        detection("moderate-duplicate", 56, 0.39),
        detection("duplicate", 51, 0.08),
        detection("supported", 120, 0.08),
        detection("two-arms", 180, 0.08),
        detection("moderate", 220, 0.36),
        detection("below-probe-floor", 120, 0.04),
    ]

    selected = select_junction_detections(image, candidates, requested_confidence=0.5)

    assert [item["id"] for item in selected] == ["normal", "supported", "moderate"]

    selected = select_junction_detections(
        image, [detection("unsupported-user-low", 180, 0.2)], requested_confidence=0.1
    )

    assert [item["id"] for item in selected] == ["unsupported-user-low"]

    selected = select_junction_detections(
        image,
        [detection("corner-high", 42, 0.9, 52.5), detection("corner-duplicate", 58, 0.39, 67.5)],
        requested_confidence=0.5,
    )

    assert [item["id"] for item in selected] == ["corner-high"]

def test_terminal_numbers_pair_device_names_with_pins_by_row():
    from analysis.wiring.terminal_numbers import connector_numbers

    def fragment(text, x, y):
        return {"text": text, "bbox": [[x - 8, y - 6], [x + 8, y - 6], [x + 8, y + 6], [x - 8, y + 6]]}

    symbol = {
        "class_name": "connector",
        "bbox": {"x0": 0, "y0": 0, "x1": 100, "y1": 60},
        "dot_role": {"dot": {"center": [50, 30]}},
        "label_ocr": {
            "crop_box": [0, 0, 100, 60],
            "processed_size": [100, 60],
            "fragments": [
                fragment("609", 25, 5),
                fragment("T86", 25, 20),
                fragment("CN29", 25, 40),
                fragment("104", 75, 20),
                fragment("17CN2", 75, 40),
                fragment("8", 75, 57),
            ],
        },
    }
    assert connector_numbers(symbol) == [{"device": "TB6", "number": "104"}, {"device": "CN29", "number": "17"}]

def _contact_ports(draw_contact, monkeypatch, **switches):
    from PIL import Image, ImageDraw

    from analysis.wiring import ports
    from analysis.wiring.ports import port_inference_config

    for name, value in switches.items():
        monkeypatch.setattr(ports, name, value)
    image = Image.new("RGB", (300, 200), "white")
    wires = draw_contact(ImageDraw.Draw(image))
    contact = {"id": "c", "class_name": "contact_a", "bbox": {"x0": 90, "y0": 58, "x1": 180, "y1": 102}}
    config = port_inference_config("template_confident_relaxed_span_ink02")
    return ports.infer_ports_for_symbol(contact, wires, image, config=config)


def _wire(wire_id, x, y0, y1):
    return {"id": wire_id, "orientation": "v", "polyline": [[x, y0], [x, y1]], "confidence": 0.9, "visible_coverage": 0.9}


def test_contact_box_covering_its_label_takes_ports_on_its_own_wire_only(monkeypatch):
    def draw_contact(draw):
        draw.line((100, 20, 100, 150), fill="black", width=2)
        draw.line((190, 20, 190, 150), fill="black", width=2)
        for y in (72, 88):
            draw.rectangle((176, y - 3, 182, y + 3), fill="black")
        return [_wire("own", 100, 20, 150), _wire("next", 190, 20, 150)]

    found = _contact_ports(draw_contact, monkeypatch)
    assert len(found) >= 2 and {p["wire_id"] for p in found} == {"own"}
    found = _contact_ports(draw_contact, monkeypatch, CONTACT_PREFERS_WIRES_INSIDE_BOX=False, CONTACT_KEEPS_INNERMOST_LINE=False)
    assert "next" in {p["wire_id"] for p in found}


def test_contact_drawn_as_two_open_circles_takes_a_port_on_each_wire(monkeypatch):
    def draw_contact(draw):
        draw.line((100, 20, 100, 58), fill="black", width=2)
        draw.ellipse((97, 59, 103, 65), outline="black")
        draw.ellipse((97, 95, 103, 101), outline="black")
        draw.line((100, 102, 100, 150), fill="black", width=2)
        return [_wire("above", 100, 20, 58), _wire("below", 100, 102, 150)]

    found = _contact_ports(draw_contact, monkeypatch)
    assert sorted((p["wire_id"], round(p["point"][1])) for p in found) == [("above", 58), ("below", 102)]
    assert len(_contact_ports(draw_contact, monkeypatch, CONTACT_EDGE_FALLBACK=False)) < 2


def test_wire_end_is_not_bridged_to_the_bus_through_a_coil():
    payload, records = vertical_and_horizontal(40, 60)
    coil = {"id": "c", "member_key": "C", "symbol_id": "C", "class_name": "relay_coil", "point": [160, 75],
            "bbox": {"x0": 80, "y0": 62, "x1": 120, "y1": 96}}
    options = {**OPTIONS, "wire_gap_bridge_outward_only": True}
    assert build_predicted_graph_pairs(payload, [*records, coil], **options) == {("A", "B")}
    assert build_predicted_graph_pairs(payload, [*records, coil], **options, gap_bridge_device_classes={"relay_coil"}) == set()
