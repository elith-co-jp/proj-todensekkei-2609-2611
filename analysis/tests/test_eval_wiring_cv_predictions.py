from analysis.tools.eval_wiring_cv import structure_predictions


def _prediction(pid, class_name, x0):
    return {
        "id": pid,
        "kind": "symbol",
        "class_name": class_name,
        "confidence": 0.9,
        "bbox": {"x0": x0, "y0": 10, "x1": x0 + 10, "y1": 20},
    }


def _symbol(sid, class_name, x0):
    return {
        "id": sid,
        "class_name": class_name,
        "confidence": 0.9,
        "bbox": {"x0": x0, "y0": 10, "x1": x0 + 10, "y1": 20},
        "label": "",
    }


def test_structure_predictions_matches_surviving_symbols_after_candidate_suppression():
    predictions = [
        _prediction("p0", "connector", 10),
        _prediction("p1", "junction", 30),
        _prediction("p2", "junction", 50),
    ]
    structure = {"symbols": [_symbol("symbol_001", "connector", 10), _symbol("symbol_002", "junction", 50)]}

    result = structure_predictions(structure, predictions)

    assert [(item["id"], item["structure_id"]) for item in result] == [
        ("p0", "symbol_001"),
        ("p2", "symbol_002"),
    ]


def test_structure_predictions_uses_geometry_after_contact_reclassification():
    predictions = [_prediction("p0", "contact_a", 10)]
    structure = {"symbols": [_symbol("symbol_001", "contact_b", 10)]}

    result = structure_predictions(structure, predictions)

    assert [(item["id"], item["structure_id"]) for item in result] == [("p0", "symbol_001")]
