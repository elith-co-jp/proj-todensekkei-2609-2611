from __future__ import annotations

# ruff: noqa: I001

import sys
import unittest
from pathlib import Path


TOOLS_DIR = Path(__file__).resolve().parents[1] / "tools"
sys.path.insert(0, str(TOOLS_DIR))

from run_yolo_e2e_pipeline import build_direct_from_to


def connector(symbol_id: str, x: float) -> dict:
    terminal_ref = f"{symbol_id}-N01"
    return {
        "id": symbol_id,
        "type": "connector",
        "class_name": "connector",
        "confidence": 0.9,
        "bbox": {"x0": x - 4, "y0": 46, "x1": x + 4, "y1": 54},
        "center": [x, 50],
        "terminals": [
            {
                "ref": terminal_ref,
                "name": "N1",
                "point": [x, 50],
                "source": "explicit_node_center",
            }
        ],
        "terminal_links": [
            {
                "symbol_id": symbol_id,
                "terminal_ref": terminal_ref,
                "terminal_name": "N1",
                "wire_id": "wire_001",
                "distance": 0,
                "reason": "nearest_terminal_wire",
            }
        ],
    }


class DirectFromToTests(unittest.TestCase):
    def test_same_wire_stops_at_intermediate_terminal(self) -> None:
        payload = {
            "source": {"page": 1},
            "symbols": [connector("symbol_a", 10), connector("symbol_b", 50), connector("symbol_c", 90)],
            "wires": [
                {
                    "id": "wire_001",
                    "orientation": "h",
                    "polyline": [[0, 50], [100, 50]],
                }
            ],
            "nodes": [
                {"id": "node_left", "type": "endpoint", "x": 0, "y": 50},
                {"id": "node_right", "type": "endpoint", "x": 100, "y": 50},
            ],
            "edges": [
                {
                    "id": "edge_001",
                    "wire_id": "wire_001",
                    "from_node_id": "node_left",
                    "to_node_id": "node_right",
                }
            ],
            "quality": {"terminal_linking": {"junction_node_policy": "all"}},
            "text_regions": [],
        }

        result = build_direct_from_to(payload)
        pairs = {
            frozenset((connection["from"]["symbol_id"], connection["to"]["symbol_id"]))
            for connection in result["connections"]
        }

        self.assertEqual(result["schema_version"], "todensekkei.from_to.v1")
        self.assertEqual(result["terminal_count"], 3)
        self.assertEqual(result["connection_count"], 2)
        self.assertEqual(pairs, {frozenset(("symbol_a", "symbol_b")), frozenset(("symbol_b", "symbol_c"))})
        self.assertNotIn(frozenset(("symbol_a", "symbol_c")), pairs)


if __name__ == "__main__":
    unittest.main()
