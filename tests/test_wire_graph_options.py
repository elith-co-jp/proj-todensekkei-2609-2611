"""配線グラフで端子どうしを結ぶ規則の、調整用の選択肢と境目（研究用の調整ツールが切り替える値も含む）。

座標は 150 dpi の図面の画素。線・ノード・端子は手で置く。
"""

from __future__ import annotations

from collections import Counter

import pytest
from PIL import Image, ImageDraw

pytest.importorskip("cv2")
from analysis.wiring.connectivity import (  # noqa: E402
    blank_run_measure,
    build_predicted_graph_pairs,
    compact_word_rule_label,
    conductive_junction_nodes,
    pairs_reached_through_graph,
)
from tests.test_wire_graph_assembly import OPTIONS  # noqa: E402


def _payload(nodes, edges, wires, **extra):
    return {"image_size": {"width": 1755, "height": 1241}, "quality": {}, "nodes": nodes, "edges": edges,
            "wires": wires, **extra}


def _node(node_id, x, y, kind="endpoint"):
    return {"id": node_id, "x": x, "y": y, "type": kind}


def _h_wire(wire_id, x0, x1, y):
    return {"id": wire_id, "orientation": "h", "bbox": {"x0": x0, "y0": y - 1, "x1": x1, "y1": y + 1}}


def _v_wire(wire_id, y0, y1, x):
    return {"id": wire_id, "orientation": "v", "bbox": {"x0": x - 1, "y0": y0, "x1": x + 1, "y1": y1}}


def _record(rid, point, cls="contact_a", symbol=None, **extra):
    return {"id": rid, "member_key": rid, "symbol_id": symbol or rid.upper(), "class_name": cls, "point": list(point),
            **extra}


def _pairs(payload, records, **options):
    stats = Counter()
    pairs = build_predicted_graph_pairs(payload, records, **{**OPTIONS, **options}, bridge_stats=stats)
    return pairs, stats


# ---------------------------------------------------------------------------- 端子から同じ向きの線への橋渡し
def _horizontal_line():
    """y=100 の横線（x=100〜300）。"""
    nodes = [_node("l", 100, 100), _node("r", 300, 100)]
    return _payload(nodes, [{"id": "e", "from_node_id": "l", "to_node_id": "r", "wire_id": "w"}], [_h_wire("w", 100, 300, 100)])


GAP = {"terminal_gap_bridge_classes": {"contact_a"}, "terminal_gap_bridge_max": 240}


@pytest.mark.parametrize("point", [(60, 100), (340, 100), (200, 110)])
def test_terminal_reaches_a_horizontal_line_beside_past_either_end_or_above_it(point):
    payload = _horizontal_line()
    records = [_record("a", point), _record("b", (300, 100), node_id="r")]
    pairs, stats = _pairs(payload, records, **GAP)
    assert pairs == {("a", "b")} and stats["terminal_gap_bridge_count"] == 1


def test_terminal_beside_a_vertical_line_and_too_far_or_wrong_class():
    nodes = [_node("t", 100, 100), _node("b", 100, 300)]
    payload = _payload(nodes, [{"id": "e", "from_node_id": "t", "to_node_id": "b", "wire_id": "w"}], [_v_wire("w", 100, 300, 100)])
    records = [_record("a", (110, 200)), _record("b", (100, 300), node_id="b")]
    assert _pairs(payload, records, **GAP)[0] == {("a", "b")}
    assert _pairs(payload, records, terminal_gap_bridge_classes={"contact_a"}, terminal_gap_bridge_max=5)[0] == set()
    coil = [_record("a", (110, 200), cls="relay_coil"), records[1]]
    assert _pairs(payload, coil, **GAP)[0] == set()


def test_terminal_bridge_rejections():
    records = [_record("a", (60, 100)), _record("b", (300, 100), node_id="r")]
    decorated = _horizontal_line()
    decorated["quality"] = {"wire_decoration_filter": {"excluded_segments": [
        {"orientation": "h", "axis": 100, "start": 50, "end": 110}]}}
    assert _pairs(decorated, records, **GAP)[1]["terminal_gap_bridge_rejected_decoration_count"] == 1

    texty = {**_horizontal_line(), "text_regions": [{"bbox": {"x0": 55, "y0": 90, "x1": 105, "y1": 110}}]}
    assert _pairs(texty, records, **GAP, gap_bridge_text_overlap_max=0.0)[1][
        "terminal_gap_bridge_rejected_text_overlap_count"] == 1

    blank = _pairs(_horizontal_line(), records, **GAP, gap_blank_run=lambda _a, _b: 99, terminal_gap_bridge_max_blank=10)
    assert blank[1]["terminal_gap_bridge_rejected_blank_count"] == 1

    blocker = _record("x", (80, 95), cls="other", symbol="X", bbox={"x0": 70, "y0": 90, "x1": 90, "y1": 110})
    blocked = _pairs(_horizontal_line(), [*records, blocker], **GAP, gap_bridge_symbol_overlap_max=0.1)
    assert blocked[1]["terminal_gap_bridge_rejected_symbol_overlap_count"] == 1
    own = _record("y", (65, 95), cls="contact_a", symbol="A", bbox={"x0": 55, "y0": 90, "x1": 70, "y1": 110})
    dot = _record("z", (95, 98), cls="junction", symbol="Z", bbox={"x0": 92, "y0": 95, "x1": 98, "y1": 101})
    allowed = _pairs(_horizontal_line(), [own, *records, dot], **GAP, gap_bridge_symbol_overlap_max=0.1)
    assert allowed[1]["terminal_gap_bridge_rejected_symbol_overlap_count"] == 0


# ---------------------------------------------------------------------------- 線の端から直交する線への橋渡し
def _tee(v_start, v_end, x=100, end_kind="endpoint", h_extra=None):
    """y=100 の横線（x=0〜200）と、その下の x の縦線（v_start〜v_end）。"""
    nodes = [_node("hl", 0, 100), _node("hr", 200, 100), _node("vt", x, v_start, end_kind), _node("vb", x, v_end)]
    edges = [{"id": "e_h", "from_node_id": "hl", "to_node_id": "hr", "wire_id": "h"},
             {"id": "e_v", "from_node_id": "vt", "to_node_id": "vb", "wire_id": "v"}]
    return _payload(nodes, edges, [_h_wire("h", 0, 200, 100), _v_wire("v", v_start, v_end, x)], **(h_extra or {}))


def _tee_records(x=100, v_end=200):
    return [_record("top", (x, v_end), wire_id="v"), _record("left", (0, 100), wire_id="h")]


def test_wire_end_bridges_outward_to_the_line_above():
    pairs, stats = _pairs(_tee(110, 200), _tee_records(), wire_gap_bridge_outward_only=True)
    assert pairs == {("left", "top")} and stats["wire_gap_bridge_count"] == 1


def test_wire_gap_bridge_rejections():
    assert _pairs(_tee(110, 200, x=220), _tee_records(x=220))[0] == set()            # 横線の端より先
    far = _pairs(_tee(170, 300), _tee_records(v_end=300))                            # 端子も無く、線の端でも近くない
    assert far[0] == set()
    blank = _pairs(_tee(110, 200), _tee_records(), gap_blank_run=lambda _a, _b: 99, wire_gap_bridge_max_blank=10)
    assert blank[1]["wire_gap_bridge_rejected_blank_count"] == 1
    excluded = {"quality": {"wire_decoration_filter": {"excluded_segments": [
        {"orientation": "v", "axis": 100, "start": 95, "end": 115}]}}}
    assert _pairs(_tee(110, 200, h_extra=excluded), _tee_records())[1]["wire_gap_bridge_rejected_decoration_count"] == 1
    texty = {"text_regions": [{"bbox": {"x0": 90, "y0": 98, "x1": 110, "y1": 112}}]}
    assert _pairs(_tee(110, 200, h_extra=texty), _tee_records(), gap_bridge_text_overlap_max=0.0)[1][
        "wire_gap_bridge_rejected_text_overlap_count"] == 1


def test_wire_gap_bridge_anchors_and_coil_checks():
    # 線の端ではない点（junction）からでも、横線の上に端子があれば橋渡しする
    anchored = _tee(110, 200, end_kind="junction")
    records = [*_tee_records(), _record("on_h", (105, 100), wire_id="h")]
    assert ("on_h", "top") in _pairs(anchored, records)[0]
    assert ("left", "top") in _pairs(anchored, _tee_records(), wire_gap_bridge_terminal_anchor_threshold=0)[0]
    # 接点だけが乗る線では、コイルの中を越える確認は要らない
    assert _pairs(_tee(110, 200), _tee_records(), device_jump_across=4.0, own_device_jump_classes={"relay_coil"})[0]


def test_bridge_leaves_from_the_farthest_terminal_past_the_wire_end():
    payload = _tee(130, 200)
    records = [_record("near", (100, 126), wire_id="v"), _record("far", (100, 121), wire_id="v"),
               _record("left", (0, 100), wire_id="h")]
    pairs, stats = _pairs(payload, records)
    assert ("far", "left") in pairs and stats["wire_gap_bridge_count"] == 1


def test_diagonal_wires_are_not_bridged_from():
    payload = _tee(110, 200)
    payload["wires"][1]["orientation"] = "d"
    assert _pairs(payload, _tee_records())[1]["wire_gap_bridge_count"] == 0


# ---------------------------------------------------------------------------- 端子どうし・ノード・グラフの形
def test_terminal_to_terminal_bridges():
    payload = _payload([], [], [])
    records = [
        _record("a1", (100, 100), symbol="A"), _record("b1", (100, 150), symbol="B"), _record("b2", (100, 180), symbol="B"),
        _record("k", (160, 100), cls="relay_coil", symbol="K"), _record("c1", (130, 100), symbol="C"),
    ]
    options = {"terminal_terminal_bridge_classes": {"contact_a"}, "terminal_terminal_bridge_max": 60}
    pairs, stats = _pairs(payload, records, **options)
    assert {("a1", "b1"), ("a1", "c1")} <= pairs and ("b1", "b2") not in pairs
    blocker = _record("x", (100, 125), cls="other", symbol="X", bbox={"x0": 90, "y0": 115, "x1": 110, "y1": 135})
    _pairs_blocked, stats = _pairs(payload, [*records, blocker], **options, gap_bridge_symbol_overlap_max=0.1)
    assert stats["terminal_terminal_bridge_rejected_symbol_overlap_count"] >= 1


def test_records_anchor_to_nearby_nodes_and_skip_wires_without_edges():
    payload = _horizontal_line()
    payload["edges"].append({"id": "loop", "from_node_id": "l", "to_node_id": "l", "wire_id": "w"})
    payload["edges"].append({"id": "broken", "from_node_id": "l", "to_node_id": "missing", "wire_id": "w"})
    records = [_record("dot", (104, 103), cls="junction"), _record("ghost", (200, 50), wire_id="nowhere"),
               _record("b", (300, 100), node_id="r")]
    pairs, _stats = _pairs(payload, records, node_anchor_classes={"junction"}, node_anchor_threshold=24)
    assert pairs == {("b", "dot")}


def test_preferred_anchor_policy_uses_the_hinted_or_nearest_wire():
    nodes = [_node("x", 100, 100, "junction"), _node("hl", 0, 100), _node("hr", 200, 100), _node("vt", 100, 0),
             _node("vb", 100, 200)]
    edges = [{"id": "h1", "from_node_id": "hl", "to_node_id": "x", "wire_id": "h"},
             {"id": "h2", "from_node_id": "x", "to_node_id": "hr", "wire_id": "h"},
             {"id": "v1", "from_node_id": "vt", "to_node_id": "x", "wire_id": "v"},
             {"id": "v2", "from_node_id": "x", "to_node_id": "vb", "wire_id": "v"},
             {"id": "h3", "from_node_id": "hr", "to_node_id": "far", "wire_id": "h"},
             {"id": "loose", "from_node_id": "x", "to_node_id": "hl", "wire_id": ""}]
    nodes.append(_node("far", 260, 100))
    payload = _payload(nodes, edges, [_h_wire("h", 0, 260, 100), _v_wire("v", 0, 200, 100)])
    records = [_record("hinted", (100, 100), node_id="x", wire_id_hint="v"), _record("bare", (101, 120), node_id="x"),
               _record("left", (0, 100), node_id="hl"), _record("top", (100, 0), node_id="vt")]
    pairs, _stats = _pairs(payload, records, nonconductive_anchor_policy="preferred")
    assert ("bare", "top") in pairs and ("hinted", "top") in pairs
    lonely = _payload([_node("j", 50, 50, "junction")], [], [])
    assert _pairs(lonely, [_record("j1", (50, 50), node_id="j")], nonconductive_anchor_policy="preferred")[0] == set()


def test_graph_walk_with_a_loop_visits_each_wire_once():
    graph = {"stop:a": {"n1"}, "n1": {"stop:a", "n2", "n3"}, "n2": {"n1", "n3"}, "n3": {"n1", "n2", "stop:b"},
             "stop:b": {"n3"}}
    assert pairs_reached_through_graph(graph, {"stop:a": "a", "stop:b": "b"}, {}) == {("a", "b")}


# ---------------------------------------------------------------------------- 小さな部品
def test_dots_from_symbol_centres_make_crossings_conductive():
    nodes = [_node("j", 100, 100, "junction"), _node("k", 300, 100, "junction")]
    payload = {"nodes": nodes, "wires": [], "quality": {"terminal_linking": {
        "junction_node_policy": "explicit", "junction_node_classes": ["junction"], "junction_node_threshold": 10}},
        "symbols": [{"class_name": "junction", "center": [101, 99]},
                    {"class_name": "junction", "bbox": {"x0": 296, "y0": 96, "x1": 304, "y1": 104}}]}
    assert conductive_junction_nodes(payload) == {"j", "k"}


def test_unported_device_box_of_the_bridging_terminal_itself_does_not_block():
    payload = _horizontal_line()
    payload["symbols"] = [{"id": "A", "class_name": "contact_a", "bbox": {"x0": 40, "y0": 80, "x1": 80, "y1": 120}}]
    records = [_record("a", (60, 100), symbol="A"), _record("b", (300, 100), node_id="r")]
    assert _pairs(payload, records, **GAP, device_jump_across=4.0)[0] == {("a", "b")}


def test_tee_detection_ignores_wires_away_from_the_node():
    payload = {"nodes": [_node("j", 100, 100, "junction")], "wires": [
        _h_wire("far_y", 0, 200, 150), _h_wire("far_x", 300, 400, 100), _v_wire("far_v", 300, 400, 100),
        _v_wire("pass", 0, 200, 100)]}
    assert conductive_junction_nodes(payload) == set()


def test_blank_runs_and_compact_labels():
    image = Image.new("RGB", (1755, 400), "white")
    draw = ImageDraw.Draw(image)
    draw.line((100, 100, 300, 100), fill="black", width=3)
    draw.line((500, 50, 500, 150), fill="black", width=3)
    longest = blank_run_measure(image)
    assert longest((100, 100), (300, 100)) == 0
    assert longest((100, 100), (400, 100)) >= 90
    assert longest((500, 50), (500, 300)) >= 140
    label = compact_word_rule_label({"final_label": "33HB", "final_tokens": ["33HB"], "selected_tokens": [],
                                     "rejected_tokens": []})
    assert label["final_label"] == "33HB"
