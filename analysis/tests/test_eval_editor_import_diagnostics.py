from analysis.tools.eval_editor_import import wire_miss_detail


EDGE = frozenset({"S1:T1", "S2:T1"})
POSITIONS = {"S1:T1": (10.0, 20.0), "S2:T1": (10.0, 80.0)}
SYMBOLS = {
    "S1": {"ref": "S1", "class_key": "contact_a", "label": "33A"},
    "S2": {"ref": "S2", "class_key": "connector", "label": "TB1"},
}


def test_wire_miss_detail_identifies_first_failed_stage():
    unmatched = wire_miss_detail(EDGE, POSITIONS, SYMBOLS, {"S1"}, {})
    assert unmatched["reason"] == "symbol_unmatched"

    unmapped = wire_miss_detail(EDGE, POSITIONS, SYMBOLS, {"S1", "S2"}, {"S1:T1": {"N1"}})
    assert unmapped["reason"] == "terminal_unmapped"

    disconnected = wire_miss_detail(
        EDGE, POSITIONS, SYMBOLS, {"S1", "S2"}, {"S1:T1": {"N1"}, "S2:T1": {"N2"}},
    )
    assert disconnected["reason"] == "different_ai_nodes"
    assert disconnected["geometry"] == {
        "dx": 0.0, "dy": 60.0, "distance": 60.0, "orientation": "vertical", "alignment_error": 0.0,
    }


def test_wire_miss_detail_reports_the_imported_terminal_mapped_to_each_gold_end():
    imported = {
        "S1:T1": [{"symbol_ref": "P1", "terminal_ref": "P1-T2", "point": [10.0, 21.0]}],
        "S2:T1": [{"symbol_ref": "P2", "terminal_ref": "P2-T1", "point": [10.0, 79.0]}],
    }

    detail = wire_miss_detail(
        EDGE,
        POSITIONS,
        SYMBOLS,
        {"S1", "S2"},
        {"S1:T1": {"N1"}, "S2:T1": {"N2"}},
        imported,
    )

    assert detail["endpoints"][0]["mapped_imported"] == imported["S1:T1"]
    assert detail["endpoints"][1]["mapped_imported"] == imported["S2:T1"]
