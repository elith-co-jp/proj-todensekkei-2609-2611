"""コイルの中で終わる配線の端からの橋渡しが、同じコイルのもう一方の端子を越えないこと（DD-0009 §9）。"""

from __future__ import annotations

from analysis.wiring.connectivity import build_direct_from_to


def _symbol(sid, cls, box, terminals):
    """terminals: (ref, point, link) の並び。link は {"wire_id": ...} か {"node_id": ...}。"""
    return {
        "id": sid, "type": cls, "class_name": cls, "bbox": dict(zip(("x0", "y0", "x1", "y1"), box, strict=True)),
        "center": [(box[0] + box[2]) / 2, (box[1] + box[3]) / 2],
        "terminals": [{"ref": ref, "name": ref, "point": list(point)} for ref, point, _ in terminals],
        "terminal_links": [{"symbol_id": sid, "terminal_ref": ref, "terminal_name": ref, **link} for ref, _, link in terminals],
    }


def _payload(coil_bottom_link):
    """上の端子台から下りる縦線がコイル（y 60〜112）の中の y=90 で終わり、40 px 下に母線（y=130）がある。
    母線のコネクタは左（x=20）、コイルの下の端子は線より少し右（x=103）で、母線の上では
    コネクタ → 橋渡しの先（x=100）→ 下の端子 の順に並ぶ（ER21298 と同じ並び）。"""
    return {
        "source": {}, "text_regions": [], "quality": {},
        "wires": [{"id": "w_top", "orientation": "v", "polyline": [[100, 0], [100, 90]]},
                  {"id": "w_bus", "orientation": "h", "polyline": [[0, 130], [200, 130]]}],
        "nodes": [{"id": "n_top", "type": "endpoint", "x": 100, "y": 0}, {"id": "n_end", "type": "endpoint", "x": 100, "y": 90},
                  {"id": "n_l", "type": "endpoint", "x": 0, "y": 130}, {"id": "n_r", "type": "endpoint", "x": 200, "y": 130}],
        "edges": [{"id": "e_top", "wire_id": "w_top", "from_node_id": "n_top", "to_node_id": "n_end"},
                  {"id": "e_bus", "wire_id": "w_bus", "from_node_id": "n_l", "to_node_id": "n_r"}],
        "symbols": [
            _symbol("top", "connector", (96, 0, 104, 8), [("top-N1", (100, 4), {"wire_id": "w_top"})]),
            _symbol("coil", "relay_coil", (80, 60, 120, 112),
                    [("coil-P1", (100, 62), {"wire_id": "w_top"}), ("coil-P2", (103, 110), coil_bottom_link)]),
            _symbol("bus", "connector", (16, 126, 24, 134), [("bus-N1", (20, 130), {"wire_id": "w_bus"})]),
        ],
    }


def _reaches_without(payload, start_ref, goal_ref, avoid_ref):
    """配線グラフの上で、avoid の端子を通らずに start の端子から goal の端子へ行けるか（コイルを飛ばす経路）。"""
    trace: dict = {}
    build_direct_from_to(payload, trace=trace)
    stops = {ref: {s for s, m in trace["terminal_by_stop"].items() if m.endswith(ref)} for ref in (start_ref, goal_ref, avoid_ref)}
    seen, todo = set(stops[start_ref]), list(stops[start_ref])
    while todo:
        node = todo.pop()
        for nxt in trace["graph"].get(node, ()):
            if nxt in seen or nxt in stops[avoid_ref]:
                continue
            if nxt in stops[goal_ref]:
                return True
            seen.add(nxt)
            todo.append(nxt)
    return False


def test_bridge_from_a_wire_end_inside_the_coil_does_not_jump_the_coils_other_terminal():
    payload = _payload({"wire_id": "w_bus"})
    # 以前は y=90 の線の端から母線へ橋渡しし、コイルの上の端子が下の端子を飛ばして母線につながっていた（ER21298）。
    assert not _reaches_without(payload, "coil-P1", "bus-N1", avoid_ref="coil-P2")
    assert _reaches_without(payload, "coil-P2", "bus-N1", avoid_ref="coil-P1")


def test_terminal_anchored_at_the_wire_end_still_bridges_to_the_bus():
    """下の端子が線の端の点に結ばれているとき、橋渡しはその端子から出る（ER21279）。"""
    payload = _payload({"node_id": "n_end"})
    assert _reaches_without(payload, "coil-P2", "bus-N1", avoid_ref="coil-P1")

