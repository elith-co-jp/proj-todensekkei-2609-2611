"""上部の母線の行のまばらな点線（DD-0023）: 点線を端子どうしの線とみなし、白丸しかない接点に点線の列で端子を付ける。"""

from __future__ import annotations

import numpy as np
from PIL import Image, ImageDraw

from analysis.wiring.faint_lines import bridge_faint_wires
from analysis.wiring.params import PortParams
from analysis.wiring.ports import _dotted_column


def _junction(sid, point):
    x, y = point
    return {"id": sid, "class_name": "junction", "bbox": {"x0": x - 6, "y0": y - 6, "x1": x + 6, "y1": y + 6},
            "terminals": [{"ref": f"{sid}-N1", "name": None, "point": [x, y]}]}


def _connector(sid, point):
    x, y = point
    return {"id": sid, "class_name": "connector", "bbox": {"x0": x - 30, "y0": y - 15, "x1": x + 30, "y1": y + 15},
            "terminals": [{"ref": f"{sid}-N1", "name": None, "point": [x, y]}]}


def _page(step, tone):
    image = Image.new("RGB", (1755, 1241), "white")
    draw = ImageDraw.Draw(image)
    for y in range(240, 340, step):              # まばらな点（ER21261 の CN56 28 の下の線は約 10〜30 px おき）
        draw.line((558, y, 558, y + 1), fill=(tone, tone, tone))
    return image


def _bridged(image):
    payload = {"symbols": [_junction("j", (558, 224)), _connector("c", (558, 357))],
               "from_to": {"connections": [], "connection_count": 0}}
    return [(c["from"]["symbol_id"], c["to"]["symbol_id"]) for c in bridge_faint_wires(payload, image)]


def test_sparse_light_dots_join_the_bus_dot_and_the_connector():
    assert _bridged(_page(step=14, tone=210)) == [("j", "c")]


def test_blank_paper_and_dark_crossings_are_not_a_dotted_line():
    assert _bridged(Image.new("RGB", (1755, 1241), "white")) == []
    # 11 px おきに交わる濃い横線（交差の束）は、点が並んでいても点線ではない。
    assert _bridged(_page(step=11, tone=40)) == []


def test_contact_with_only_circles_takes_the_dotted_column():
    """63LL の接点: 枠の中は白丸だけで、枠の上と下の外に点が同じ列（x 556）に並ぶ。"""
    faint = np.zeros((600, 800), dtype=bool)
    for y in (215, 228, 240, 305, 318, 330):
        faint[y:y + 2, 556] = True
    assert _dotted_column(faint, 539, 248, 608, 299, PortParams()) == 556
    assert _dotted_column(np.zeros((600, 800), dtype=bool), 539, 248, 608, 299, PortParams()) is None
