"""黒丸は 1 つの交点だけをつなぐ（間隔の狭い平行線で、黒丸の近くの黒丸の無い交差をつながない）。"""

from __future__ import annotations

from analysis.wiring.connectivity import dotted_junction_nodes


def test_dot_joins_only_its_nearest_crossing():
    nodes = [{"id": "a", "type": "junction", "x": 100, "y": 60}, {"id": "b", "type": "junction", "x": 100, "y": 72},
             {"id": "c", "type": "endpoint", "x": 100, "y": 61}]
    assert dotted_junction_nodes(nodes, [(101.0, 71.0)], threshold=24.0) == {"b"}
    assert dotted_junction_nodes(nodes, [(140.0, 71.0)], threshold=24.0) == set()


def test_connector_takes_its_large_dot_over_a_small_speck_near_the_centre():
    """TB13 10 の枠の中心近くに大きさ 5 の点（文字のかけら）があり、本物の黒丸（大きさ 14）より中心に近い（ER21281）。"""
    from analysis.wiring.dot_roles import Dot, DotRules, _dot_for

    symbol = {"class_name": "connector", "bbox": {"x0": 301, "y0": 854, "x1": 368, "y1": 885}}
    speck = Dot(328.0, 863.0, 5, {"right": True})
    dot = Dot(342.0, 863.0, 14, {"up": True, "down": True})
    assert _dot_for(symbol, [speck, dot], DotRules()) is dot
    assert _dot_for(symbol, [speck, dot], DotRules(connector_min_size_share=0.0)) is speck
