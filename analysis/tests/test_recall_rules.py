"""見落としを減らす変更（DD-0022）: 交差の間の短い辺を残す、角の短い線を文字と重なっても配線にする、
盤の枠の中の配線で正解の端子をまとめる。"""

from __future__ import annotations

import sys
from pathlib import Path

from analysis.wiring.graph import build_connection_graph
from analysis.wiring.shapes import Box, DetectedWire

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

from eval_editor_import import gold_edges, inside_aliases, match_connection_symbols  # noqa: E402


def _wire(wid, orientation, box):
    span = (box.x0, box.x1) if orientation == "h" else (box.y0, box.y1)
    axis = round((box.y0 + box.y1) / 2) if orientation == "h" else round((box.x0 + box.x1) / 2)
    return DetectedWire(wid, orientation, box, axis, span[0], span[1], span[1] - span[0], 0.9, 1.0, "test", [])


def test_vertical_wire_stays_whole_across_rows_11_px_apart():
    """縦線が 11 px おきの横線 2 本と交わる（ER21276 の下部の束）。交差と交差の間の 11 px の辺を捨てると、上のコネクタと
    下の分岐の黒丸がつながらない。"""
    wires = [_wire("v", "v", Box(437, 500, 441, 800)), _wire("h1", "h", Box(270, 671, 863, 673)),
             _wire("h2", "h", Box(354, 682, 945, 684))]
    graph = build_connection_graph(wires, [])
    nodes = {n["id"]: (n["x"], n["y"]) for n in graph["nodes"]}
    ys = sorted({nodes[e[k]][1] for e in graph["edges"] if e["wire_id"] == "v" for k in ("from_node_id", "to_node_id")})
    assert [y for y in ys if 660 <= y <= 690] == [672, 683]
    assert any(e["wire_id"] == "v" and {nodes[e["from_node_id"]][1], nodes[e["to_node_id"]][1]} == {672, 683} for e in graph["edges"])


def _project(connections):
    def sym(ref, cls, points):
        return {"ref": ref, "class_key": cls, "label": "", "cx": 0.5, "cy": 0.5, "w": 0.1, "h": 0.1,
                "terminals": [{"ref": f"{ref}-T{i}", "tx": x / 1000, "ty": y / 1000} for i, (x, y) in enumerate(points, 1)]}
    symbols = [sym("F", "other", [(673, 479), (676, 493), (676, 553)]), sym("K", "contact_a", [(677, 505), (677, 520)]),
               sym("C", "connector", [(676, 600)])]
    return {"image_width": 1000, "image_height": 1000, "symbols": symbols, "connections": [
        {"from_symbol_ref": a.split("-")[0], "from_terminal_ref": a, "to_symbol_ref": b.split("-")[0], "to_terminal_ref": b}
        for a, b in connections]}


def test_terminals_joined_inside_a_panel_frame_are_one_point():
    """ER21315 の 2RAXP: 黒丸 41（T1）から母線（T2）へ枠の中の線、母線から接点へ。T1 と T2 は 1 つの点として採点する。
    枠の中の縦に長い線（カードの + と - の書き方、T2 から T3）はまとめない。"""
    project = _project([("F-T1", "F-T2"), ("F-T2", "K-T1"), ("F-T2", "F-T3"), ("F-T3", "C-T1")])
    alias = inside_aliases(project)
    assert alias == {"F:F-T1": "F:F-T2"} or alias == {"F:F-T2": "F:F-T1"}
    edges = gold_edges(project)
    one = alias.get("F:F-T1", "F:F-T1")
    assert frozenset((one, "K:K-T1")) in edges
    assert frozenset(("F:F-T3", "C:C-T1")) in edges

def _destination_page():
    from PIL import Image, ImageDraw

    image = Image.new("RGB", (1755, 1241), "white")
    draw = ImageDraw.Draw(image)
    for x in range(140, 300, 6):                 # 薄い母線（ER21261 の「ER21141へ」に届く線）
        draw.line((x, 225, x + 4, 225), fill=(200, 200, 200))
    draw.text((150, 235), "(ER21141)", fill=(0, 0, 0))
    return image


def test_destination_takes_its_port_where_a_faint_bus_ends_inside_it():
    """札に届く母線が薄く配線として検出されないとき、札の辺を横切る灰色の線の終わり（矢印の先）を端子にし、
    母線の黒丸と薄い線でつなぐ（ER21261・ER21302・ER21294、DD-0022）。"""
    from analysis.wiring.faint_lines import bridge_faint_wires
    from analysis.wiring.ports import destination_port

    image = _destination_page()
    tag = {"id": "d", "class_name": "other", "other_kind": "destination", "bbox": {"x0": 119, "y0": 216, "x1": 228, "y1": 261}}
    ports = destination_port(tag, [], image)
    assert [(round(p["point"][0]), round(p["point"][1])) for p in ports] == [(140, 225)]
    assert destination_port(tag, []) == []

    tag["terminals"] = [{"ref": "d-P01", "name": "P1", "point": ports[0]["point"]}]
    dot = {"id": "j", "class_name": "junction", "bbox": {"x0": 268, "y0": 219, "x1": 280, "y1": 231},
           "terminals": [{"ref": "j-N1", "name": None, "point": [274, 225]}]}
    payload = {"symbols": [tag, dot], "from_to": {"connections": [], "connection_count": 0}}
    added = bridge_faint_wires(payload, image)
    assert {(c["from"]["symbol_id"], c["to"]["symbol_id"]) for c in added} == {("d", "j")}


def test_destination_accepts_an_18px_faint_entry_but_not_17px_or_detached_text():
    """ER21261: 18/30 pixels survive with short gaps; a shorter or edge-detached stroke is not a wire."""
    from PIL import Image, ImageDraw

    from analysis.wiring.ports import destination_port

    tag = {"id": "d", "class_name": "other", "other_kind": "destination",
           "bbox": {"x0": 119, "y0": 216, "x1": 228, "y1": 261}}

    def page(outside_pixels):
        image = Image.new("RGB", (1755, 1241), "white")
        ImageDraw.Draw(image).line((200, 225, 228 + outside_pixels, 225), fill=(230, 230, 230), width=1)
        return image

    assert destination_port(tag, [], page(18))[0]["point"] == [200.0, 225.0]
    assert destination_port(tag, [], page(17)) == []

    detached = Image.new("RGB", (1755, 1241), "white")
    ImageDraw.Draw(detached).line((236, 225, 253, 225), fill=(230, 230, 230), width=1)
    assert destination_port(tag, [], detached) == []

    stops_at_edge = Image.new("RGB", (1755, 1241), "white")
    ImageDraw.Draw(stops_at_edge).line((228, 225, 246, 225), fill=(230, 230, 230), width=1)
    assert destination_port(tag, [], stops_at_edge) == []


def _item(ref, label, box, class_name="other"):
    return {"ref": ref, "class_name": class_name, "label": label, "kind": "symbol", "bbox": box}


def test_connection_matching_accepts_same_unique_destination_when_box_expands():
    """ER21280: OCR の周辺文字まで札枠に入り IoU が 0.5 未満でも、同じ行先札の中心位置は変わらない。"""
    gold = [_item("gold", "ER21291へ", {"x0": 1302, "y0": 200, "x1": 1386, "y1": 240})]
    pred = [_item("pred", "ER21291へ", {"x0": 1294, "y0": 184, "x1": 1404, "y1": 265})]
    assert match_connection_symbols(gold, pred) == [(0, 0, 0.0)]


def test_connection_matching_uses_unique_connector_label_for_shorter_box():
    gold = [_item("G1", "CN57 24", {"x0": 658.0, "y0": 673.0, "x1": 749.0, "y1": 741.0}, "connector")]
    predicted = [_item("P1", "CN57 24", {"x0": 659.0, "y0": 683.0, "x1": 734.0, "y1": 723.0}, "connector")]
    assert match_connection_symbols(gold, predicted) == [(0, 0, 0.0)]


def test_connection_matching_recovers_one_unambiguous_same_class_contact():
    gold = [_item("G1", "", {"x0": 1139.5, "y0": 499.4, "x1": 1165.1, "y1": 533.9}, "contact_a")]
    predicted = [_item("P1", "", {"x0": 1119.0, "y0": 499.0, "x1": 1175.0, "y1": 536.0}, "contact_a")]
    assert match_connection_symbols(gold, predicted) == [(0, 0, 0.0)]


def test_connection_matching_does_not_guess_between_overlapping_contacts():
    gold = [_item("G1", "", {"x0": 100.0, "y0": 100.0, "x1": 140.0, "y1": 140.0}, "contact_a")]
    predicted = [
        _item("P1", "", {"x0": 80.0, "y0": 80.0, "x1": 160.0, "y1": 160.0}, "contact_a"),
        _item("P2", "", {"x0": 81.0, "y0": 81.0, "x1": 159.0, "y1": 159.0}, "contact_a"),
    ]
    assert match_connection_symbols(gold, predicted) == []


def test_connection_matching_does_not_relax_non_destination_or_ambiguous_labels():
    expanded = {"x0": 90, "y0": 80, "x1": 210, "y1": 220}
    small = {"x0": 120, "y0": 120, "x1": 180, "y1": 180}
    assert match_connection_symbols([_item("g", "制御盤", small)], [_item("p", "制御盤", expanded)]) == []
    assert match_connection_symbols([_item("g", "ER21291へ", small)], [_item("p", "ER21292へ", expanded)]) == []
    assert match_connection_symbols(
        [_item("g", "TB1", small, class_name="connector")],
        [_item("p", "TB1", expanded, class_name="connector")],
    ) == []
    assert match_connection_symbols(
        [_item("g", "CONTACTB12", small, class_name="connector")],
        [_item("p", "CONTACTB12", expanded, class_name="connector")],
    ) == []
    assert match_connection_symbols(
        [_item("g", "ER21291へ", small, class_name="breaker")],
        [_item("p", "ER21291へ", expanded)],
    ) == [(0, 0, 0.0)]
    assert match_connection_symbols(
        [_item("g1", "ER21291へ", small), _item("g2", "ER21291へ", small)],
        [_item("p", "ER21291へ", expanded)],
    ) == []
