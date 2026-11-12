from analysis.tools.eval_editor_import import match_connection_symbols, wire_miss_detail


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


def test_connection_matching_uses_unique_connector_label_for_shorter_box():
    gold = [{
        "ref": "G1", "class_name": "connector", "label": "CN57 24", "kind": "symbol",
        "bbox": {"x0": 658.0, "y0": 673.0, "x1": 749.0, "y1": 741.0},
    }]
    predicted = [{
        "ref": "P1", "class_name": "connector", "label": "CN57 24", "kind": "symbol",
        "bbox": {"x0": 659.0, "y0": 683.0, "x1": 734.0, "y1": 723.0},
    }]

    assert match_connection_symbols(gold, predicted) == [(0, 0, 0.0)]


def test_connection_matching_recovers_one_unambiguous_same_class_box_with_mutual_centres():
    gold = [{
        "ref": "G1", "class_name": "contact_a", "label": "", "kind": "symbol",
        "bbox": {"x0": 1139.5, "y0": 499.4, "x1": 1165.1, "y1": 533.9},
    }]
    predicted = [{
        "ref": "P1", "class_name": "contact_a", "label": "", "kind": "symbol",
        "bbox": {"x0": 1119.0, "y0": 499.0, "x1": 1175.0, "y1": 536.0},
    }]

    assert match_connection_symbols(gold, predicted) == [(0, 0, 0.0)]


def test_connection_matching_does_not_guess_between_ambiguous_overlapping_contacts():
    gold = [{
        "ref": "G1", "class_name": "contact_a", "label": "", "kind": "symbol",
        "bbox": {"x0": 100.0, "y0": 100.0, "x1": 140.0, "y1": 140.0},
    }]
    predicted = [
        {"ref": "P1", "class_name": "contact_a", "label": "", "kind": "symbol",
         "bbox": {"x0": 80.0, "y0": 80.0, "x1": 160.0, "y1": 160.0}},
        {"ref": "P2", "class_name": "contact_a", "label": "", "kind": "symbol",
         "bbox": {"x0": 81.0, "y0": 81.0, "x1": 159.0, "y1": 159.0}},
    ]

    assert match_connection_symbols(gold, predicted) == []
