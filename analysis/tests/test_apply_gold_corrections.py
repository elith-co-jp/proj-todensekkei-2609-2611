"""正解データの修正（apply_gold_corrections）が、確かめた修正だけを、何度当てても同じ結果になるように当てることを確かめる。"""

from __future__ import annotations

# ruff: noqa: I001

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

from apply_gold_corrections import ADDED_NOTE, CorrectionError, apply_corrections


def bundle():
    return {
        "classes": [{"key": "contact_a", "label": "a接点", "yolo_index": 1}, {"key": "connector", "label": "コネクタ", "yolo_index": 7}],
        "projects": [{
            "name": "ER21276-001", "image_width": 1000, "image_height": 500,
            "symbols": [
                {"ref": "SYM-0008", "class_key": "contact_a", "label": "83D-2 ESD(ER21271)", "cx": 0.5, "cy": 0.5, "w": 0.04, "h": 0.06, "terminals": []},
                {"ref": "SYM-0009", "class_key": "connector", "label": "TB3 1", "cx": 0.2, "cy": 0.2, "w": 0.04, "h": 0.04, "terminals": []},
            ],
            "connections": [],
        }],
    }


RELABEL = {"sheet": "ER21276", "op": "relabel", "ref": "SYM-0008", "was": "83D-2 ESD(ER21271)", "label": "83D-6 ESD(ER21271)", "reason": "図面は 83D-6"}
ADD = {"sheet": "ER21276", "op": "add_symbol", "class_key": "connector", "label": "TB3 2", "bbox_px": [580, 180, 620, 200], "reason": "枠の付け忘れ"}


def test_corrections_are_applied_once_and_the_input_is_left_alone():
    original = bundle()
    fixed, results = apply_corrections(original, [RELABEL, ADD])
    symbols = fixed["projects"][0]["symbols"]
    assert symbols[0]["label"] == "83D-6 ESD(ER21271)"
    assert symbols[2] | {"cx": round(symbols[2]["cx"], 3), "cy": round(symbols[2]["cy"], 3)} == {
        "ref": "SYM-0010", "class_key": "connector", "class_label": "コネクタ", "yolo_index": 7, "label": "TB3 2",
        "cx": 0.6, "cy": 0.38, "w": 0.04, "h": 0.04, "note": ADDED_NOTE, "terminals": []}
    assert [r["status"] for r in results] == ["applied", "applied"]
    assert original == bundle()

    again, results = apply_corrections(fixed, [RELABEL, ADD])
    assert again == fixed
    assert [r["status"] for r in results] == ["already", "already"]


def test_a_label_that_differs_from_the_reviewed_one_stops_the_run():
    changed = bundle()
    changed["projects"][0]["symbols"][0]["label"] = "83D-5 ESD(ER21271)"
    with pytest.raises(CorrectionError, match="expected"):
        apply_corrections(changed, [RELABEL])


def test_an_added_box_on_top_of_a_different_symbol_stops_the_run():
    clash = {**ADD, "label": "TB3 9", "bbox_px": [180, 90, 220, 110]}
    with pytest.raises(CorrectionError, match="already covers"):
        apply_corrections(bundle(), [clash])


def test_corrections_for_other_drawings_are_skipped():
    _, results = apply_corrections(bundle(), [{**RELABEL, "sheet": "ER21301"}])
    assert results == []

