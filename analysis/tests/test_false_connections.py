"""つながっていない線をつなぐ誤り（DD-0014）: 文字の並びを配線にしない、角からは隣の平行線へ橋渡ししない。"""

from __future__ import annotations

import numpy as np

from analysis.wiring.connectivity import _wire_ends_at_node, build_direct_from_to
from analysis.wiring.detect import looks_like_text_row
from analysis.wiring.ports import card_ports_across
from analysis.wiring.shapes import Box, LineSegment


def test_row_of_characters_is_not_a_wire():
    binary = np.zeros((40, 220), dtype=np.uint8)
    line = LineSegment("h", Box(10, 19, 210, 21))
    for x in range(10, 210, 8):          # 5 px の字画と 3 px の隙間が並ぶ（「(ER21271)」の行）
        binary[14:26, x:x + 5] = 255
    assert looks_like_text_row(binary, line)
    binary[:] = 0
    binary[19:22, 10:211] = 255          # 途切れの無い線
    binary[19:22, 100:130] = 0           # 記号で 1 か所途切れる
    assert not looks_like_text_row(binary, line)


def _symbol(sid, cls, point, link):
    x, y = point
    return {"id": sid, "type": cls, "class_name": cls, "bbox": {"x0": x - 4, "y0": y - 4, "x1": x + 4, "y1": y + 4},
            "center": [x, y], "terminals": [{"ref": f"{sid}-N1", "name": "N1", "point": [x, y]}],
            "terminal_links": [{"symbol_id": sid, "terminal_ref": f"{sid}-N1", **link}]}


def _wire(wid, orientation, start, end):
    (x0, y0), (x1, y1) = start, end
    return {"id": wid, "orientation": orientation, "polyline": [list(start), list(end)],
            "bbox": {"x0": min(x0, x1) - 1, "y0": min(y0, y1) - 1, "x1": max(x0, x1) + 1, "y1": max(y0, y1) + 1}}


def test_corner_does_not_bridge_to_the_next_parallel_line():
    """縦線が y=100 で左へ曲がって横線になり、11 px 下（y=111）を別の横線が通る（ER21277）。角の点から下の横線へ
    橋渡しすると 2 本の横線が 1 つになる。"""
    payload = {
        "source": {}, "text_regions": [], "quality": {},
        "wires": [_wire("v", "v", (200, 0), (200, 100)), _wire("h1", "h", (0, 100), (200, 100)),
                  _wire("h2", "h", (50, 111), (300, 111))],
        "nodes": [{"id": "top", "type": "endpoint", "x": 200, "y": 0}, {"id": "corner", "type": "endpoint", "x": 200, "y": 100},
                  {"id": "h1l", "type": "endpoint", "x": 0, "y": 100},
                  {"id": "h2l", "type": "endpoint", "x": 50, "y": 111}, {"id": "h2r", "type": "endpoint", "x": 300, "y": 111}],
        "edges": [{"id": "ev", "wire_id": "v", "from_node_id": "top", "to_node_id": "corner"},
                  {"id": "eh1", "wire_id": "h1", "from_node_id": "h1l", "to_node_id": "corner"},
                  {"id": "eh2", "wire_id": "h2", "from_node_id": "h2l", "to_node_id": "h2r"}],
        "symbols": [_symbol("a", "connector", (200, 0), {"wire_id": "v"}), _symbol("b", "connector", (0, 100), {"wire_id": "h1"}),
                    _symbol("c", "connector", (300, 111), {"wire_id": "h2"})],
    }
    pairs = {frozenset((c["from"]["symbol_id"], c["to"]["symbol_id"])) for c in build_direct_from_to(payload)["connections"]}
    assert frozenset(("a", "b")) in pairs
    assert not any("c" in pair and len(pair) == 2 for pair in pairs)


def test_parallel_line_end_beside_a_bare_crossing_is_not_a_tee():
    """黒丸の無い交差 (100, 50) の 11 px 横で別の縦線が終わる（ER21276 の平行線）。その端をこの交点で終わる線とみなすと、
    T 字としてつないでしまう。"""
    crossing = [_wire("h", "h", (0, 50), (200, 50)), _wire("v", "v", (100, 0), (100, 120))]
    # 検出した線の枠は線の太さより広い（幅 8 px ほど）。
    neighbour_end = {**_wire("v2", "v", (111, 50), (111, 150)), "bbox": {"x0": 107, "y0": 50, "x1": 115, "y1": 150}}
    assert not _wire_ends_at_node(crossing + [neighbour_end], 100, 50)
    assert _wire_ends_at_node(crossing + [_wire("v3", "v", (100, 50), (100, 150))], 100, 50)


def test_terminal_bridge_does_not_jump_over_a_contact_without_terminals():
    """母線の黒丸 (100, 0) と、90 px 下から始まる縦線の間に接点がある（ER21302）。接点は線が薄く端子を決められなかった。
    黒丸から縦線へ橋渡しすると、接点を飛ばして黒丸とコネクタをつなぐ。"""
    def payload(with_contact: bool) -> dict:
        contact = {"id": "k", "type": "contact_a", "class_name": "contact_a",
                   "bbox": {"x0": 85, "y0": 30, "x1": 115, "y1": 70}, "center": [100, 50], "terminals": []}
        return {
            "source": {}, "text_regions": [], "quality": {},
            "wires": [_wire("v", "v", (100, 90), (100, 200))],
            "nodes": [{"id": "top", "type": "endpoint", "x": 100, "y": 90}, {"id": "bot", "type": "endpoint", "x": 100, "y": 200}],
            "edges": [{"id": "ev", "wire_id": "v", "from_node_id": "top", "to_node_id": "bot"}],
            "symbols": [{**_symbol("dot", "junction", (100, 0), {}), "terminal_links": []},
                        _symbol("cn", "connector", (100, 200), {"wire_id": "v"}), *([contact] if with_contact else [])],
        }

    def pairs(with_contact: bool) -> set[frozenset]:
        return {frozenset((c["from"]["symbol_id"], c["to"]["symbol_id"]))
                for c in build_direct_from_to(payload(with_contact))["connections"]}

    assert frozenset(("dot", "cn")) in pairs(with_contact=False)
    assert frozenset(("dot", "cn")) not in pairs(with_contact=True)


def test_wire_through_a_card_gets_both_terminals():
    """縦線がカード (y 445〜575) を上から下まで貫き、端子は上の + しか見つからない（ER21303）。下の - を足さないと、
    線がカードを素通りして + 側が下のコネクタにつながる。カードの中で終わる線には足さない。"""
    box = (574.0, 445.0, 760.0, 575.0)
    plus = {"wire_id": "w", "point": [604.0, 486.6], "reason": "template:vertical_axis", "ink_ratio": 0.3}
    through = [_wire("w", "v", (604, 213), (604, 853))]
    added = card_ports_across([plus], through, box, margin_px=4.0)
    assert [p["point"] for p in added] == [[604.0, 533.4]]
    ends_inside = [_wire("w", "v", (604, 213), (604, 490))]
    assert card_ports_across([plus], ends_inside, box, margin_px=4.0) == []


