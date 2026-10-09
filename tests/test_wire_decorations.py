"""Regression cases contain only generated strokes and dummy symbol boxes."""

from copy import deepcopy

import pytest
from PIL import Image, ImageDraw


@pytest.fixture
def detector():
    pytest.importorskip("cv2")
    from analysis import runtime  # Register the shared CLI module path.

    assert runtime is not None
    from filter_wire_decorations import filter_wire_decorations

    return filter_wire_decorations


def wire(key, orientation, start, end, axis, thickness=3):
    points = [[start, axis], [end, axis]] if orientation == "h" else [[axis, start], [axis, end]]
    box = (
        {"x0": start, "y0": axis - thickness // 2, "x1": end, "y1": axis + thickness // 2 + 1}
        if orientation == "h"
        else {"x0": axis - thickness // 2, "y0": start, "x1": axis + thickness // 2 + 1, "y1": end}
    )
    return {
        "id": key,
        "orientation": orientation,
        "bbox": box,
        "polyline": points,
        "axis": axis,
        "length": end - start,
        "confidence": 0.8,
        "visible_coverage": 0.8,
        "reason": "synthetic",
        "nearby_label_ids": [],
    }


def payload(*wires):
    return {"wires": list(wires), "nodes": [], "edges": [], "connections": [], "labels": [], "quality": {}}


def card():
    return {"class_name": "other", "confidence": 0.9, "bbox": {"x0": 80, "y0": 70, "x1": 350, "y1": 350}}


@pytest.mark.parametrize("orientation", ["h", "v"])
def test_regular_dashes_removed_but_solid_and_single_gap_retained(detector, orientation):
    image = Image.new("RGB", (1000, 700), "white")
    draw = ImageDraw.Draw(image)
    for start in range(100, 300, 10):
        points = ((start, 100), (start + 5, 100)) if orientation == "h" else ((100, start), (100, start + 5))
        draw.line(points, fill="black", width=2)
    wires = [wire("dash", orientation, 100, 296, 100)]
    for key, axis, spans in [("solid", 150, [(100, 300)]), ("faded", 200, [(100, 190), (197, 300)])]:
        for start, end in spans:
            points = ((start, axis), (end, axis)) if orientation == "h" else ((axis, start), (axis, end))
            draw.line(points, fill="black", width=2)
        wires.append(wire(key, orientation, 100, 300, axis))
    original = payload(*wires)
    snapshot = deepcopy(original)
    result = detector(original, image, [])
    assert [w["id"] for w in result["wires"]] == ["solid", "faded"]
    assert original == snapshot
    assert all(edge["wire_id"] != "dash" for edge in result["edges"])
    assert result["quality"]["wire_count"] == 2
    assert all(item["wire_id"] != "dash" for item in result["connections"])


def test_solid_wire_just_inside_dashed_border_is_not_removed(detector):
    image = Image.new("RGB", (1000, 700), "white")
    draw = ImageDraw.Draw(image)
    for y in range(100, 300, 10):
        draw.line((100, y, 100, y + 5), fill="black", width=2)
    draw.line((104, 100, 104, 300), fill="black", width=2)
    original = payload(wire("inner", "v", 100, 300, 103, thickness=10))
    result = detector(original, image, [card()])
    assert result["wires"] == original["wires"]


def ruled_label():
    image = Image.new("RGB", (1000, 700), "white")
    draw = ImageDraw.Draw(image)
    for y in (100, 118):
        draw.line((100, y, 220, y), fill="black", width=2)
    draw.text((140, 103), "CARD", fill="black")
    draw.line((240, 90, 240, 300), fill="black", width=2)
    return image, payload(
        wire("top", "h", 100, 220, 100), wire("bottom", "h", 100, 220, 118), wire("real", "v", 90, 300, 240)
    )


def test_confirmed_card_label_rules_removed_without_erasing_card_wires(detector):
    image, original = ruled_label()
    result = detector(original, image, [card()], confirm_text=lambda _: True)
    assert [w["id"] for w in result["wires"]] == ["real"]
    assert result["quality"]["wire_decoration_filter"]["removed_by_reason"] == {"label_rule": 2}


@pytest.mark.parametrize("text", [None, lambda _: False])
def test_unconfirmed_text_does_not_remove_solid_rules(detector, text):
    image, original = ruled_label()
    assert detector(original, image, [card()], confirm_text=text)["wires"] == original["wires"]


def test_symbol_and_terminal_detection_protects_pair_of_real_wires(detector):
    image, original = ruled_label()
    symbol = {"class_name": "connector", "bbox": {"x0": 95, "y0": 98, "x1": 115, "y1": 120}}
    result = detector(original, image, [card(), symbol], confirm_text=lambda _: True)
    assert result["wires"] == original["wires"]


def test_label_filter_works_page_wide_without_card_or_other_detection(detector):
    image, original = ruled_label()
    whole_sheet = {**card(), "bbox": {"x0": 0, "y0": 0, "x1": 1000, "y1": 700}}
    for symbols in ([], [whole_sheet]):
        assert [
            w["id"] for w in detector(original, image, symbols, confirm_text=lambda _: True)["wires"]
        ] == ["real"]


def test_large_label_inclusive_symbol_box_does_not_exclude_label_rules(detector):
    image, original = ruled_label()
    symbol = {**card(), "class_name": "connector"}
    result = detector(original, image, [symbol], confirm_text=lambda _: True)
    assert [w["id"] for w in result["wires"]] == ["real"]


def test_coil_body_and_connected_buses_are_not_label_rules(detector):
    image, original = ruled_label()
    for kind in ("relay_coil", "solenoid"):
        symbol = {**card(), "class_name": kind}
        assert detector(original, image, [symbol], confirm_text=lambda _: True)["wires"] == original["wires"]
    ImageDraw.Draw(image).line((100, 60, 100, 160), fill="black", width=2)
    assert detector(original, image, [], confirm_text=lambda _: True)["wires"] == original["wires"]


def test_label_rule_removes_only_confirmed_section_of_longer_candidate(detector):
    image, original = ruled_label()
    original["wires"][0] = wire("long", "h", 50, 450, 100)
    result = detector(original, image, [card()], confirm_text=lambda _: True)
    parts = [w for w in result["wires"] if w["id"].startswith("long_part_")]
    assert len(parts) == 2
    assert parts[0]["polyline"][0] == [50, 100]
    assert parts[-1]["polyline"][-1] == [450, 100]
    assert 99 <= parts[0]["polyline"][-1][0] <= 103
    assert 219 <= parts[1]["polyline"][0][0] <= 224
    assert result["quality"]["wire_decoration_filter"]["trimmed_wire_count"] == 1
    assert all(edge["wire_id"] != "long" for edge in result["edges"])


def test_ocr_failure_does_not_erase_label_rules_or_block_analysis(detector):
    image, original = ruled_label()

    def fail(_):
        raise RuntimeError("OCR unavailable")

    result = detector(original, image, [card()], confirm_text=fail)
    assert result["wires"] == original["wires"]
    assert result["quality"]["wire_decoration_filter"]["label_rule_ocr_failed"]


def test_real_continuous_wire_beside_label_rule_is_retained(detector):
    image, original = ruled_label()
    ImageDraw.Draw(image).line((100, 107, 220, 107), fill="black", width=2)
    original["wires"][0] = wire("inner", "h", 100, 220, 104, thickness=12)
    assert any(
        w["id"] == "inner" for w in detector(original, image, [card()], confirm_text=lambda _: True)["wires"]
    )


def test_no_wires_is_valid(detector):
    result = detector(payload(), Image.new("RGB", (100, 100), "white"), [])
    assert not result["wires"] and not result["edges"]


@pytest.mark.parametrize(
    "points,blocked",
    [
        ([(50, 100), (110, 100)], True),
        ([(80, 60), (80, 120)], False),
        ([(50, 105), (110, 105)], False),
    ],
)
def test_gap_repair_cannot_reintroduce_rejected_stroke(detector, points, blocked):
    from build_from_to_review_outputs import build_predicted_graph_pairs

    records = [
        {"id": str(i), "member_key": str(i), "symbol_id": str(i), "class_name": "junction", "point": point}
        for i, point in enumerate(points)
    ]
    options = {
        "internal_bridge_classes": set(),
        "node_anchor_classes": set(),
        "node_anchor_threshold": 0,
        "terminal_gap_bridge_classes": set(),
        "terminal_gap_bridge_max": 0,
        "wire_gap_bridge_max": 0,
        "gap_bridge_align_threshold": 3,
        "wire_gap_bridge_terminal_anchor_threshold": 0,
        "terminal_terminal_bridge_classes": {"junction"},
        "terminal_terminal_bridge_max": 90,
        "gap_bridge_text_overlap_max": 1.0,
        "gap_bridge_symbol_overlap_max": 1.0,
        "nonconductive_anchor_policy": "all",
    }
    original = payload()
    assert build_predicted_graph_pairs(original, records, **options) == {("0", "1")}
    original["quality"]["wire_decoration_filter"] = {
        "excluded_segments": [{"orientation": "h", "axis": 100, "start": 70, "end": 90}]
    }
    predicted = build_predicted_graph_pairs(original, records, **options)
    assert predicted == (set() if blocked else {("0", "1")})
