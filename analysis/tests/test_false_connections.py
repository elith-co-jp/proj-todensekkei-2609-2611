"""つながっていない線をつなぐ誤り（DD-0014）: 文字の並びを配線にしない、角からは隣の平行線へ橋渡ししない。"""

from __future__ import annotations

import numpy as np

from analysis.wiring.connectivity import build_direct_from_to
from analysis.wiring.detect import looks_like_text_row
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

