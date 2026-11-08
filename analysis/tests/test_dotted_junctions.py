"""黒丸は 1 つの交点だけをつなぐ（間隔の狭い平行線で、黒丸の近くの黒丸の無い交差をつながない）。"""

from __future__ import annotations

from analysis.wiring.connectivity import dotted_junction_nodes


def test_dot_joins_only_its_nearest_crossing():
    nodes = [{"id": "a", "type": "junction", "x": 100, "y": 60}, {"id": "b", "type": "junction", "x": 100, "y": 72},
             {"id": "c", "type": "endpoint", "x": 100, "y": 61}]
    assert dotted_junction_nodes(nodes, [(101.0, 71.0)], threshold=24.0) == {"b"}
    assert dotted_junction_nodes(nodes, [(140.0, 71.0)], threshold=24.0) == set()

