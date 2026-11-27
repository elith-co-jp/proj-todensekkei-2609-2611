"""かすれ・途切れ・太った線のスキャンでの読み取り（DD-0054）。"""

from __future__ import annotations

import numpy as np
from PIL import Image

from analysis.synthetic_circuits.scan import scan_hard_variant
from analysis.wiring.connectivity import build_predicted_graph_pairs
from analysis.wiring.contact_kind import bar_side
from analysis.wiring.ports import line_ports, point_ink_ratio

BOX = {"x0": 10, "y0": 15, "x1": 50, "y1": 45}


def test_thick_lead_and_circle_rims_are_not_a_bar():
    """太った線（6 px）と白丸の縁が、棒の無い左側に棒があるように見えていた。"""
    binary = np.zeros((60, 60), dtype=bool)
    binary[0:60, 18:24] = True         # 太った引き出し線（上下とも枠の外へ続く）
    binary[22:38, 15:18] = True        # 白丸の左の縁（線のすぐ外の肩）
    assert bar_side(binary, BOX) is None
    binary[22:38, 30] = True           # 右に離れた棒
    assert bar_side(binary, BOX) == "a"


def test_faded_line_still_counts_as_ink():
    gray = np.full((40, 40), 240, dtype=np.uint8)
    gray[:, 19:22] = 195               # かすれた線（地より 45 暗い）
    image = Image.fromarray(gray)
    assert point_ink_ratio(image, (20, 20), 4) > 0.2
    assert point_ink_ratio(Image.fromarray(np.full((40, 40), 240, dtype=np.uint8)), (20, 20), 4) == 0.0
    dark = gray.copy()
    dark[:, 19:22] = 0                 # 濃い線の所は今までと同じ（180 未満）
    assert point_ink_ratio(Image.fromarray(dark), (20, 20), 4) == 27 / 81


def test_line_ports_bridge_dropouts_in_the_leads():
    """枠の上下の線が 4〜6 px ずつ途切れていても、長く続いていれば端子にする。"""
    mask = np.zeros((200, 200), dtype=bool)
    mask[39:42, 20:181] = True
    mask[159:162, 20:181] = True
    mask[40:81, 99:102] = True
    mask[120:161, 99:102] = True
    for y in (50, 62, 74, 128, 140, 152):
        mask[y:y + 5, 99:102] = False
    wires = [{"id": "top", "orientation": "h", "bbox": {"x0": 20, "y0": 39, "x1": 180, "y1": 41}},
             {"id": "bottom", "orientation": "h", "bbox": {"x0": 20, "y0": 159, "x1": 180, "y1": 161}}]
    lamp = {"id": "l1", "class_name": "lamp", "bbox": {"x0": 80, "y0": 80, "x1": 120, "y1": 120}}
    ports = line_ports(lamp, mask, wires, [lamp])
    assert [p["wire_id_hint"] for p in ports] == ["top", "bottom"]


def _payload(symbols):
    nodes = [{"id": "n1", "x": 50, "y": 10, "type": "endpoint"}, {"id": "n2", "x": 50, "y": 100, "type": "endpoint"},
             {"id": "n3", "x": 51, "y": 118, "type": "endpoint"}, {"id": "n4", "x": 51, "y": 200, "type": "endpoint"}]
    wires = [{"id": "w1", "orientation": "v", "bbox": {"x0": 48, "y0": 10, "x1": 52, "y1": 100}},
             {"id": "w2", "orientation": "v", "bbox": {"x0": 49, "y0": 118, "x1": 53, "y1": 200}}]
    edges = [{"id": "e1", "from_node_id": "n1", "to_node_id": "n2", "wire_id": "w1"},
             {"id": "e2", "from_node_id": "n3", "to_node_id": "n4", "wire_id": "w2"}]
    return {"nodes": nodes, "wires": wires, "edges": edges, "symbols": symbols}


def _pairs(symbols, gap):
    from analysis.wiring.connectivity import build_direct_terminal_records

    def ends(symbol_id, y, wire):
        return {"id": symbol_id, "class_name": "connector", "bbox": {"x0": 45, "y0": y - 5, "x1": 55, "y1": y + 5},
                "terminals": [{"ref": "T", "point": [50, y]}], "terminal_links": [{"terminal_ref": "T", "wire_id": wire}]}

    payload = _payload([ends("top", 30, "w1"), ends("bottom", 180, "w2"), *symbols])
    records, _ = build_direct_terminal_records(payload)
    return build_predicted_graph_pairs(
        payload, records, internal_bridge_classes=set(), node_anchor_classes=set(), node_anchor_threshold=0,
        terminal_gap_bridge_classes=set(), terminal_gap_bridge_max=0, wire_gap_bridge_max=0, gap_bridge_align_threshold=0,
        wire_gap_bridge_terminal_anchor_threshold=0, terminal_terminal_bridge_classes=set(), terminal_terminal_bridge_max=0,
        gap_bridge_text_overlap_max=1.0, gap_bridge_symbol_overlap_max=1.0, nonconductive_anchor_policy="all",
        collinear_bridge_max=gap,
    )


def test_collinear_break_without_a_symbol_is_joined():
    """かすれで 1 本の縦線が 18 px 途切れて 2 本になっても、間に記号が無ければつながる。"""
    assert not _pairs([], 0)
    assert _pairs([], 30)
    contact = {"id": "c", "class_name": "contact_a", "bbox": {"x0": 40, "y0": 95, "x1": 60, "y1": 125}}
    assert not _pairs([contact], 30)


def test_hard_scan_keeps_size_and_differs_from_the_original():
    page = np.full((300, 400), 255, dtype=np.uint8)
    page[100:103, 20:380] = 0
    out = scan_hard_variant(page, 3)
    assert out.shape == page.shape and out.dtype == np.uint8
    assert np.array_equal(out, scan_hard_variant(page, 3))
    assert out[100:103, 20:380].mean() < 200 < out[200:, :].mean()
