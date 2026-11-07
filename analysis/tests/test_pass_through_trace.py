"""線をたどってコネクタに着いたコイルの端子が、そのコネクタの端子と直接つながること（ER21315）。"""

from __future__ import annotations

from analysis.wiring.connectivity import build_direct_from_to


def test_coil_port_traced_to_a_connector_connects_to_that_connector():
    payload = {
        "source": {}, "wires": [], "nodes": [], "edges": [],
        "symbols": [
            {"id": "cn", "class_name": "connector", "bbox": {"x0": 80, "y0": 62, "x1": 120, "y1": 78}, "center": [100, 70],
             "terminals": [{"ref": "cn-N01", "name": "N1", "point": [100, 70]}], "terminal_links": []},
            {"id": "c1", "class_name": "relay_coil", "bbox": {"x0": 80, "y0": 80, "x1": 120, "y1": 120}, "center": [100, 100],
             "terminals": [{"ref": "c1-L01", "name": "P1", "point": [100, 80]}],
             "terminal_links": [{"symbol_id": "c1", "terminal_ref": "c1-L01", "peer_symbol_id": "cn"}]},
        ],
    }
    pairs = {tuple(sorted((c["from"]["terminal_ref"], c["to"]["terminal_ref"]))) for c in build_direct_from_to(payload)["connections"]}
    assert ("c1-L01", "cn-N01") in pairs
