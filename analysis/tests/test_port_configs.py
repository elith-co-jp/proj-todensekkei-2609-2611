"""記号の端子の位置を決める規則の、調整用の設定（研究用ツールが名前で切り替える）ごとの振る舞い。"""

from __future__ import annotations

import pytest
from PIL import Image, ImageDraw

from analysis.wiring.ports import (
    card_ports_across,
    infer_ports_for_symbol,
    port_inference_config,
    terminal_points_for_symbol,
)

WHITE = Image.new("RGB", (400, 300), "white")
LOOSE = port_inference_config("template_loose")


def _symbol(cls="contact_a", box=(100, 100, 140, 160), **extra):
    return {"id": "S", "class_name": cls, "bbox": dict(zip(("x0", "y0", "x1", "y1"), box, strict=True)), **extra}


def _v(wire_id, x, y0, y1, confidence=0.9, coverage=0.9):
    return {"id": wire_id, "orientation": "v", "polyline": [[x, y0], [x, y1]], "confidence": confidence,
            "visible_coverage": coverage}


def _h(wire_id, y, x0, x1, confidence=0.9, coverage=0.9):
    return {"id": wire_id, "orientation": "h", "polyline": [[x0, y], [x1, y]], "confidence": confidence,
            "visible_coverage": coverage}


def _points(symbol, wires, config, image=WHITE):
    return [tuple(c["point"]) for c in infer_ports_for_symbol(symbol, wires, image, config=config)]


def test_unknown_config_name():
    with pytest.raises(ValueError, match="unknown inferred terminal config"):
        port_inference_config("nope")


def test_faint_or_unreliable_wires_and_wrong_orientations_are_ignored():
    confident = port_inference_config("template_confident")
    assert _points(_symbol(), [_v("w", 120, 50, 210, confidence=0.3)], confident) == []
    assert _points(_symbol(), [_v("w", 120, 50, 210, coverage=0.3)], confident) == []
    assert _points(_symbol("relay_coil"), [_h("w", 130, 50, 200)], LOOSE) == []
    vertical_only = port_inference_config("template_confident_v2_vertical_ink02") | {"min_ink_ratio": 0.0}
    assert _points(_symbol(), [_h("w", 130, 50, 200)], vertical_only) == []
    assert _points(_symbol(), [_h("w", 130, 300, 390)], LOOSE) == []                # 横線が枠に届かない
    assert _points(_symbol(), [_v("w", 120, 250, 290)], LOOSE) == []                # 縦線が枠に届かない


def test_contact_terminals_outside_the_box_and_on_box_edges():
    outer = LOOSE | {"contact_outer_terminal_margin_px": 8, "require_point_on_wire_span": False}
    assert (120, 92) in _points(_symbol(), [_v("w", 120, 50, 210)], outer)
    wide = _symbol(box=(100, 100, 160, 140))
    assert (92, 120) in _points(wide, [_h("w", 120, 50, 210)], outer)
    edge = port_inference_config("bbox_edge_loose") | {"edge_on_span": True}
    assert _points(wide, [_h("w", 120, 130, 210)], edge) == [(160, 120)]


def test_contact_terminal_position_and_wire_count_limits():
    wide = _symbol(box=(100, 100, 160, 140))
    left = LOOSE | {"contact_min_terminal_rel_x": 0.5}
    right = LOOSE | {"contact_max_terminal_rel_x": 0.5}
    # 片側の端子しか残らないと、枠の辺の位置で探し直す（その辺も同じ範囲で絞る）
    assert _points(wide, [_h("w", 120, 50, 210)], left) == [(160, 120)]
    assert _points(wide, [_h("w", 120, 50, 210)], right) == [(100, 120)]
    one_axis = LOOSE | {"contact_max_wire_axes": 1, "four_port_height_px": 9999}
    two_lines = [_v("a", 119, 50, 210), _v("b", 121, 50, 210, coverage=0.95)]
    assert {c["wire_id"] for c in infer_ports_for_symbol(_symbol(), two_lines, WHITE, config=one_axis)} == {"b"}


def test_card_with_one_inked_port_gets_the_mirrored_one():
    image = Image.new("RGB", (400, 300), "white")
    ImageDraw.Draw(image).rectangle((119, 116, 125, 124), fill="black")     # 左の端子だけ線が濃い
    card = _symbol("other", box=(100, 100, 160, 140), other_kind="card")
    inked = LOOSE | {"min_ink_ratio": 0.2}
    points = sorted(_points(card, [_h("w", 120, 50, 210)], inked, image))
    assert points == [(119.2, 120), (140.8, 120)]
    found = [{"wire_id": "w", "point": [119.2, 120]}, {"wire_id": "w", "point": [140.8, 120]}]
    assert card_ports_across(found, [_h("w", 120, 50, 210)], (100, 100, 160, 140), 8) == []


def test_annotated_points_and_destinations():
    symbol = _symbol(terminals=[{"ref": "T1", "point": None}, {"ref": "T2", "name": "13", "point": [120, 100]}])
    common = {"wires": [], "source_image": WHITE, "inferred_terminal_classes": {"contact_a", "other"},
              "inferred_terminal_config": LOOSE}
    assert terminal_points_for_symbol(symbol, terminal_source="annotation", **common) == [
        {"terminal_ref": "T2", "terminal_name": "13", "point": [120.0, 100.0], "source": "annotation"}]
    destination = _symbol("other", other_kind="destination")
    wire = _h("w", 130, 20, 104)
    [port] = terminal_points_for_symbol(destination, terminal_source="inferred", **{**common, "wires": [wire]})
    assert port["source"] == "destination_wire_end" and port["point"] == [104.0, 130.0]
