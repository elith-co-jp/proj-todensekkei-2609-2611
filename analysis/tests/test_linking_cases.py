"""記号の端子と配線・点の結び付け、網の候補づくりの細かい場合（linking）。"""

from __future__ import annotations

import dataclasses
import math

from PIL import Image, ImageDraw

from analysis.wiring import linking
from analysis.wiring.params import DEFAULT

NO_LINE_PORTS = dataclasses.replace(DEFAULT.ports, line_ports=False)


def _box(x0, y0, x1, y1):
    return {"x0": x0, "y0": y0, "x1": x1, "y1": y1}


def _symbol(sid, cls, box, **extra):
    return {"id": sid, "class_name": cls, "bbox": _box(*box),
            "center": [(box[0] + box[2]) / 2, (box[1] + box[3]) / 2], **extra}


def _hwire(wid, y, x0, x1):
    return {"id": wid, "orientation": "h", "polyline": [[x0, y], [x1, y]], "bbox": _box(x0, y - 1, x1, y + 1),
            "box": _box(x0, y - 1, x1, y + 1)}


def _vwire(wid, x, y0, y1):
    return {"id": wid, "orientation": "v", "polyline": [[x, y0], [x, y1]], "bbox": _box(x - 1, y0, x + 1, y1),
            "box": _box(x - 1, y0, x + 1, y1)}


def _nets(edges, nodes, symbols, wires, policy, image=None, classes=frozenset({"junction"})):
    return linking.build_net_candidates(
        edges, nodes, symbols, wires, symbol_terminal_bridge_classes=set(),
        junction_node_policy=policy, junction_node_classes=set(classes), junction_node_threshold=10,
        source_image=image,
    )


def test_nearest_wire_without_wires():
    assert linking.nearest_wire_for_point((1, 1), []) == (None, math.inf)


def test_hinted_device_port_cases():
    coil = _symbol("C", "relay_coil", (100, 100, 140, 160))
    vwire, hwire = _vwire("v", 120, 40, 100), _hwire("h", 130, 40, 100)
    assert linking._hinted_device_port(coil, (120, 110), None) is False
    assert linking._hinted_device_port({**coil, "class_name": "connector"}, (120, 110), vwire) is False
    assert linking._hinted_device_port(coil, (10, 10), vwire) is False                  # 枠の外
    assert linking._hinted_device_port(coil, (120, 110), {"orientation": "v"}) is False  # 線の枠が無い
    assert linking._hinted_device_port(coil, (120, 110), vwire) is True                 # 縦線の軸の上
    assert linking._hinted_device_port(coil, (130, 110), vwire) is False
    assert linking._hinted_device_port(coil, (110, 130), hwire) is True                 # 横線の軸の上
    assert linking._hinted_device_port(coil, (110, 150), hwire) is False


def test_enrich_links_traced_ports_nodes_and_labels(monkeypatch):
    coil = _symbol("C", "relay_coil", (100, 100, 140, 160))
    hidden = _symbol("D", "junction", (300, 300, 306, 306), dot_role={"suppressed": True})
    plug = _symbol("P", "connector", (200, 100, 220, 120))
    ports = {
        "C": [
            # 線をたどってコネクタに着いた端子はそのコネクタの端子につなぐ
            {"terminal_ref": "C-1", "terminal_name": "1", "point": [120, 100], "traced": True,
             "symbol_id_hint": "P", "source": "line_port"},
            # 線をたどっても配線に当たらなかった端子はつながない
            {"terminal_ref": "C-2", "terminal_name": "2", "point": [120, 160], "traced": True, "source": "line_port"},
            # 配線は遠いが点は近い端子は点につなぐ
            {"terminal_ref": "C-3", "terminal_name": "3", "point": [100, 130], "source": "annotation"},
        ],
        "P": [{"terminal_ref": None, "terminal_name": None, "point": [210, 110], "source": "bbox_center_fallback"}],
    }
    monkeypatch.setattr(linking, "terminal_points_for_symbol", lambda symbol, **_: ports[symbol["id"]])
    payload = {
        "image_size": {"width": 400, "height": 400},
        "wires": [_hwire("w1", 20, 0, 50)],
        "nodes": [{"id": "n1", "x": 101, "y": 131, "type": "end"}],
        "edges": [],
        "labels": [{"id": "L1", "bbox": _box(150, 120, 170, 130)}, {"id": "L0"}, {"id": "far", "bbox": _box(390, 390, 399, 399)}],
    }
    out = linking.enrich_symbols_and_graph(
        payload, [coil, hidden, plug], terminal_wire_threshold=5, terminal_node_threshold=5,
        terminal_source="annotation", inferred_terminal_classes=set(), inferred_terminal_config={},
        symbol_terminal_bridge_classes=set(), junction_node_policy="none", junction_node_classes=set(),
        junction_node_threshold=10, source_image=Image.new("RGB", (400, 400), "white"), port_params=NO_LINE_PORTS,
    )
    reasons = {link["terminal_ref"]: link["reason"] for link in coil["terminal_links"]}
    assert reasons == {"C-1": "traced_to_pass_through", "C-3": "nearest_terminal_node_no_wire"}
    assert coil["terminal_links"][0]["peer_symbol_id"] == "P"
    assert coil["nearby_label_ids"] == ["L1"] and coil["nearby_node_ids"] == ["n1"]
    assert hidden["terminals"] == [] and hidden["terminal_links"] == []
    assert out["nodes"][0]["symbol_ids"] == ["C"]


def _tee():
    # 横線 h の右端に縦線 v が着く T 字（n_tee）と、横線 h2 と縦線 v が交わる十字（n_cross）
    wires = [_hwire("h", 50, 10, 50), _vwire("v", 50, 20, 120), _hwire("h2", 100, 20, 80)]
    nodes = [
        {"id": "a", "x": 10, "y": 50, "type": "end"}, {"id": "n_tee", "x": 50, "y": 50, "type": "junction"},
        {"id": "top", "x": 50, "y": 20, "type": "end"}, {"id": "n_cross", "x": 50, "y": 100, "type": "junction"},
        {"id": "bottom", "x": 50, "y": 120, "type": "end"}, {"id": "l", "x": 20, "y": 100, "type": "end"},
        {"id": "r", "x": 80, "y": 100, "type": "end"},
    ]
    edges = [
        {"id": "e1", "from_node_id": "a", "to_node_id": "n_tee", "wire_id": "h"},
        {"id": "e2", "from_node_id": "top", "to_node_id": "n_tee", "wire_id": "v"},
        {"id": "e3", "from_node_id": "n_tee", "to_node_id": "n_cross", "wire_id": "v"},
        {"id": "e4", "from_node_id": "n_cross", "to_node_id": "bottom", "wire_id": "v"},
        {"id": "e5", "from_node_id": "l", "to_node_id": "n_cross", "wire_id": "h2"},
        {"id": "e6", "from_node_id": "n_cross", "to_node_id": "r", "wire_id": "h2"},
        {"id": "e7", "from_node_id": "far1", "to_node_id": "far2"},
    ]
    return wires, nodes, edges


def _net_of(nets, node):
    return next(net["id"] for net in nets if node in net["node_ids"])


def test_all_policy_without_image_joins_tees_and_dotted_crossings():
    wires, nodes, edges = _tee()
    nets = _nets(edges, nodes, [], wires, "all")
    assert _net_of(nets, "a") == _net_of(nets, "top")             # T 字はつながる
    assert _net_of(nets, "l") != _net_of(nets, "bottom")          # 黒丸の無い十字は交差
    dot = _symbol("J", "junction", (47, 97, 53, 103))
    nets = _nets(edges, nodes, [dot], wires, "all")
    assert _net_of(nets, "l") == _net_of(nets, "bottom")          # 黒丸のある十字は接続


def test_all_policy_with_image_needs_ink_on_three_arms():
    wires, nodes, edges = _tee()
    image = Image.new("L", (140, 140), 255)
    draw = ImageDraw.Draw(image)
    draw.line((10, 50, 50, 50), fill=0, width=2)
    draw.line((50, 20, 50, 70), fill=0, width=2)                  # T 字は描くが十字の縦線は描かない
    draw.line((20, 100, 80, 100), fill=0, width=2)
    dot = _symbol("J", "junction", (47, 97, 53, 103))
    nets = _nets(edges, nodes, [dot], wires, "all", image=image.convert("RGB"))
    assert _net_of(nets, "a") == _net_of(nets, "top")
    assert _net_of(nets, "l") != _net_of(nets, "bottom")          # 腕が 2 本しか見えない
    blank = Image.new("RGB", (140, 140), "white")
    nets = _nets(edges, nodes, [dot], wires, "all", image=blank)
    assert _net_of(nets, "a") != _net_of(nets, "top")             # 点に墨が無い


def test_explicit_policy_needs_a_junction_symbol():
    wires, nodes, edges = _tee()
    nets = _nets(edges, nodes, [], wires, "explicit")
    assert _net_of(nets, "a") != _net_of(nets, "top")
    dot = {"id": "J", "type": "junction", "bbox": _box(47, 47, 53, 53)}
    far = _symbol("K", "junction", (200, 200, 206, 206))
    nets = _nets(edges, nodes, [far, dot], wires, "explicit")
    assert _net_of(nets, "a") == _net_of(nets, "top")


def test_vertical_tee_and_wires_beside_the_node():
    # 縦線 v の下端が横線 h に着く T 字。横線 off は点の高さだが左右に外れ、縦線 low は点の列だが上下に外れる。
    wires = [_hwire("off", 50, 200, 260), _vwire("low", 50, 200, 260), _hwire("h", 50, 10, 90), _vwire("v", 50, 10, 50)]
    nodes = [{"id": "a", "x": 10, "y": 50, "type": "end"}, {"id": "t", "x": 50, "y": 50, "type": "junction"},
             {"id": "b", "x": 90, "y": 50, "type": "end"}, {"id": "top", "x": 50, "y": 10, "type": "end"}]
    edges = [{"id": "e1", "from_node_id": "a", "to_node_id": "t", "wire_id": "h"},
             {"id": "e2", "from_node_id": "t", "to_node_id": "b", "wire_id": "h"},
             {"id": "e3", "from_node_id": "top", "to_node_id": "t", "wire_id": "v"}]
    nets = _nets(edges, nodes, [], wires, "all")
    assert _net_of(nets, "a") == _net_of(nets, "top")


def test_bridges_touching_fallback_and_lonely_loops():
    nodes = [{"id": k, "x": i * 10, "y": 0, "type": "end"} for i, k in enumerate("abcd")]
    edges = [{"id": "e1", "from_node_id": "a", "to_node_id": "b", "wire_id": "w1"},
             {"id": "e2", "from_node_id": "c", "to_node_id": "d", "wire_id": "w2"},
             {"id": "loop", "from_node_id": "z", "to_node_id": "z"}]          # 1 点だけの輪は網にしない
    fuse = {"id": "F", "class_name": "fuse", "terminals": [{}, {}],
            "terminal_links": [{"wire_id": "w1"}, {"node_id": "c"}]}
    plug = {"id": "P", "class_name": "connector", "terminal_links": [], "touching_wire_ids": ["w2"]}
    lamp = {"id": "L", "class_name": "lamp", "terminal_links": [], "touching_wire_ids": ["w1"]}
    nets = linking.build_net_candidates(
        edges, nodes, [fuse, plug, lamp], [], symbol_terminal_bridge_classes={"fuse"},
        junction_node_policy="none", junction_node_classes=set(), junction_node_threshold=10,
    )
    assert len(nets) == 1 and nets[0]["node_ids"] == ["a", "b", "c", "d"]   # ヒューズの両端をつなぐ
    members = {m["symbol_id"]: m["reason"] for m in nets[0]["terminal_members"]}
    assert members["P"] == "touching_wire_fallback" and "L" not in members  # 接点・ランプは枠の接触で結ばない


def test_terminals_inside_a_coil_do_not_belong_to_a_box_around_it(monkeypatch):
    coil = _symbol("C", "relay_coil", (100, 100, 140, 160))
    box = _symbol("O", "other", (80, 80, 160, 180))
    ports = {
        "C": [{"terminal_ref": None, "terminal_name": None, "point": [120, 130], "source": "bbox_center_fallback"}],
        "O": [{"terminal_ref": "O-1", "terminal_name": "1", "point": [120, 100], "source": "annotation"},
              {"terminal_ref": "O-2", "terminal_name": "2", "point": [80, 130], "source": "annotation"}],
    }
    monkeypatch.setattr(linking, "terminal_points_for_symbol", lambda symbol, **_: ports[symbol["id"]])
    linking.enrich_symbols_and_graph(
        {"image_size": {"width": 200, "height": 200}}, [coil, box], terminal_wire_threshold=5,
        terminal_node_threshold=5, terminal_source="annotation", inferred_terminal_classes=set(),
        inferred_terminal_config={}, symbol_terminal_bridge_classes=set(), junction_node_policy="none",
        junction_node_classes=set(), junction_node_threshold=10, source_image=None, port_params=NO_LINE_PORTS,
    )
    assert [t["ref"] for t in box["terminals"]] == ["O-2"]
