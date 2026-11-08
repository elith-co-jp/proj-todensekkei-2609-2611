"""取りこぼした配線を減らす規則（DD-0016）。"""

from __future__ import annotations

import numpy as np
from PIL import Image, ImageDraw

from analysis.wiring.connectivity import build_direct_from_to
from analysis.wiring.faint_lines import bridge_parallel_contacts
from analysis.wiring.params import DEFAULT
from analysis.wiring.ports import _faint_column, _lead_column, destination_port, terminal_points_for_symbol


def _wire(wid, orientation, start, end):
    (x0, y0), (x1, y1) = start, end
    return {"id": wid, "orientation": orientation, "polyline": [list(start), list(end)], "axis": x0 if orientation == "v" else y0,
            "bbox": {"x0": min(x0, x1) - 1, "y0": min(y0, y1) - 1, "x1": max(x0, x1) + 1, "y1": max(y0, y1) + 1}}


def test_destination_tag_takes_the_end_of_the_bus_inside_it():
    """母線が「ER21276へ」の札の中で終わる（ER21275）。その端を札の端子にする。札に着かない線は使わない。"""
    tag = {"id": "t", "bbox": {"x0": 1302, "y0": 203, "x1": 1394, "y1": 246}}
    bus = _wire("bus", "h", (129, 222), (1368, 222))
    elsewhere = _wire("v", "v", (700, 300), (700, 600))
    [port] = destination_port(tag, [bus, elsewhere])
    assert port["point"] == [1368.0, 222.0] and port["wire_id_hint"] == "bus"
    assert destination_port(tag, [elsewhere]) == []


def test_bridge_from_a_contact_terminal_does_not_run_through_the_contact():
    """接点の上の端子には線が無く、下の端子の 7 px 下から配線がコネクタまで続く（ER21302）。上の端子から下の配線へ
    橋渡しすると、接点の中を突き抜けて上の端子とコネクタがつながる。"""
    def symbol(sid, cls, box, terminals):
        return {"id": sid, "type": cls, "class_name": cls, "bbox": dict(zip(("x0", "y0", "x1", "y1"), box, strict=True)),
                "center": [(box[0] + box[2]) / 2, (box[1] + box[3]) / 2],
                "terminals": [{"ref": f"{sid}-{i}", "name": str(i), "point": list(p)} for i, p in enumerate(terminals, 1)],
                "terminal_links": []}
    contact = symbol("k", "contact_a", (926, 250, 1012, 306), [(944, 250), (944, 306)])
    connector = symbol("cn", "connector", (905, 341, 978, 391), [(944, 357)])
    connector["terminal_links"] = [{"symbol_id": "cn", "terminal_ref": "cn-1", "wire_id": "w"}]
    payload = {"source": {}, "text_regions": [], "quality": {}, "wires": [_wire("w", "v", (944, 313), (944, 400))],
               "nodes": [{"id": "a", "type": "endpoint", "x": 944, "y": 313}, {"id": "b", "type": "endpoint", "x": 944, "y": 400}],
               "edges": [{"id": "e", "wire_id": "w", "from_node_id": "a", "to_node_id": "b"}], "symbols": [contact, connector]}
    pairs = {frozenset((c["from"]["terminal_ref"], c["to"]["terminal_ref"])) for c in build_direct_from_to(payload)["connections"]}
    assert frozenset(("k-2", "cn-1")) in pairs
    assert frozenset(("k-1", "cn-1")) not in pairs


def test_contact_ports_come_from_its_leads_when_no_line_shows_outside():
    """枠の外の線は見えないが、接点の左寄りに上下の引き出し線がある（ER21293）。右の名前の欄の文字は使わない。"""
    mask = np.zeros((80, 120), dtype=bool)
    x0, y0, x1, y1 = 10, 20, 70, 58
    mask[20:33, 18] = True               # 上の引き出し線（枠の上から）
    mask[45:59, 18] = True               # 下の引き出し線（枠の下まで）
    mask[24:31, 50] = mask[47:54, 50] = True   # 右の名前の文字（1 行ぶんの短い画）
    assert _lead_column(mask, x0, y0, x1, y1, DEFAULT.ports) == (18, 20, 58)
    mask[:, 18] = False
    assert _lead_column(mask, x0, y0, x1, y1, DEFAULT.ports) is None


def test_faint_line_beside_the_contact_label_is_found_for_contacts_only_with_full_width():
    """薄い点線が接点の枠の左寄り（幅の 2 割の位置）で上下に続く（ER21302）。コイルの探し方（中央の半分）では見えない。"""
    faint = np.zeros((200, 200), dtype=bool)
    x0, y0, x1, y1 = 100, 80, 185, 130
    faint[40:78:2, 117] = faint[132:170:2, 117] = True
    assert _faint_column(faint, x0, y0, x1, y1, DEFAULT.ports, middle_only=False) == 117
    assert _faint_column(faint, x0, y0, x1, y1, DEFAULT.ports, middle_only=True) is None


def test_connector_terminal_sits_on_its_dot():
    """「2AT3 14」は黒丸が枠の左寄り（x=343）で、枠の中心（x=355）に端子を置くと細い線の上の端子とそろわない（ER21281）。"""
    symbol = {"id": "c", "class_name": "connector", "bbox": {"x0": 333, "y0": 1020, "x1": 378, "y1": 1042},
              "center": [355.5, 1031.0], "dot_role": {"dot": {"center": [343.2, 1032.2]}}}
    [terminal] = terminal_points_for_symbol(symbol, wires=[], source_image=Image.new("L", (10, 10)), terminal_source="inferred",
                                            inferred_terminal_classes=set(), inferred_terminal_config={})
    assert terminal["point"] == [343.2, 1032.2]


def _contact(sid, x, y0, y1):
    return {"id": sid, "class_name": "contact_a", "bbox": {"x0": x - 8, "y0": y0, "x1": x + 50, "y1": y1},
            "terminals": [{"ref": f"{sid}-1", "point": [x, y0 + 6]}, {"ref": f"{sid}-2", "point": [x, y1 - 6]}]}


def _bypass_page(bus: bool) -> Image.Image:
    """左の接点 A（x=818）から右の接点 B（x=888）へ、上下に灰色の迂回の線（ER21296 の 10ESD）。"""
    image = Image.new("L", (1755, 1240), 255)
    draw = ImageDraw.Draw(image)
    for y, end in ((478, 489), (526, 513)):
        draw.line((818, y, 888, y), fill=200)
        draw.line((888, min(y, end), 888, max(y, end)), fill=200)
    if bus:
        draw.line((700, 478, 1000, 478), fill=200)   # A・B の外まで続く横線は母線
    return image


def test_parallel_contact_is_joined_by_its_bypass_line():
    a, b = _contact("a", 818, 483, 519), _contact("b", 888, 483, 519)
    payload = {"symbols": [a, b], "from_to": {"connections": [
        {"type": "wire", "from": {"id": "a:a-1", "symbol_id": "a"}, "to": {"id": "w:w-1", "symbol_id": "w"}},
        {"type": "wire", "from": {"id": "a:a-2", "symbol_id": "a"}, "to": {"id": "v:v-1", "symbol_id": "v"}},
    ], "connection_count": 2}}
    added = bridge_parallel_contacts(payload, _bypass_page(bus=False))
    assert sorted((c["from"]["terminal_ref"], c["to"]["terminal_ref"]) for c in added) == [("b-1", "a-1"), ("b-2", "a-2")]


def test_line_running_past_both_contacts_is_not_a_bypass():
    a, b = _contact("a", 818, 483, 519), _contact("b", 888, 483, 519)
    payload = {"symbols": [a, b], "from_to": {"connections": [], "connection_count": 0}}
    added = bridge_parallel_contacts(payload, _bypass_page(bus=True))
    assert ("b-1", "a-1") not in {(c["from"]["terminal_ref"], c["to"]["terminal_ref"]) for c in added}

