"""つながっていない線をつなぐ誤り（DD-0020）: 枠をかすめる線の端子、ずれた黒丸どうしの薄い線
（接点をまたぐ橋渡しは test_coil_bridges.py）。"""

from __future__ import annotations

from PIL import Image, ImageDraw

from analysis.wiring.faint_lines import bridge_faint_wires
from analysis.wiring.ports import grazes_box_edge

BOX = {"x0": 449, "y0": 339, "x1": 518, "y1": 383}


def test_horizontal_line_along_a_connector_box_edge_grazes_it():
    """コネクタの枠の下の辺を、飛び越しの横線が通る（ER21282 の TB14）。辺の上の端子は線が枠に入っていない。"""
    connector = {"class_name": "connector", "bbox": BOX}
    assert grazes_box_edge(connector, (496, 383), "h", 3.0)
    assert not grazes_box_edge(connector, (492, 353), "v", 3.0)      # 列の縦線は枠の中を通る
    assert not grazes_box_edge(connector, (449, 361), "h", 3.0)      # 横から枠に入る線
    assert not grazes_box_edge(connector, (496, 383), "h", 0.0)


def test_vertical_line_along_a_card_edge_grazes_it_but_not_a_junction():
    """カードの枠の右端を、隣のコネクタの縦線が素通りする（ER21301）。黒丸の枠は線にぴったり沿うので対象外。"""
    card = {"class_name": "other", "other_kind": "card", "bbox": {"x0": 833, "y0": 446, "x1": 1249, "y1": 578}}
    assert grazes_box_edge(card, (1246, 488), "v", 3.0)
    assert not grazes_box_edge(card, (1126, 488), "v", 3.0)
    junction = {"class_name": "junction", "bbox": {"x0": 1013, "y0": 839, "x1": 1026, "y1": 852}}
    assert not grazes_box_edge(junction, (1016, 845), "v", 3.0)


def _junction(sid, point):
    x, y = point
    return {"id": sid, "class_name": "junction", "bbox": {"x0": x - 6, "y0": y - 6, "x1": x + 6, "y1": y + 6},
            "terminals": [{"ref": f"{sid}-N1", "name": None, "point": [x, y]}]}


def _faint_row(left, right):
    image = Image.new("RGB", (1755, 1241), "white")
    draw = ImageDraw.Draw(image)
    y = (left[1] + right[1]) / 2
    for x in range(left[0] + 10, right[0] - 10, 6):
        draw.line((x, y, x + 3, y), fill=(225, 225, 225))
    payload = {"symbols": [_junction("a", left), _junction("b", right)], "from_to": {"connections": [], "connection_count": 0}}
    return bridge_faint_wires(payload, image)


def test_faint_line_does_not_join_offset_junctions():
    """下部の交差の束で、高さが 7 px ずれた黒丸どうし（実際の図面は 4〜8 px）を薄い線でつないだ（ER21279）。そろった黒丸はつなぐ。"""
    assert _faint_row((520, 708), (601, 715)) == []
    assert len(_faint_row((520, 708), (601, 708))) == 1


def test_long_solid_bus_between_aligned_junctions_is_recorded():
    left, right = (200, 708), (1500, 708)
    image = Image.new("RGB", (1755, 1241), "white")
    ImageDraw.Draw(image).line((*left, *right), fill="black", width=2)
    payload = {
        "symbols": [_junction("a", left), _junction("b", right)],
        "from_to": {"connections": [], "connection_count": 0},
    }

    added = bridge_faint_wires(payload, image)

    assert [(item["from"]["symbol_id"], item["to"]["symbol_id"]) for item in added] == [("a", "b")]


