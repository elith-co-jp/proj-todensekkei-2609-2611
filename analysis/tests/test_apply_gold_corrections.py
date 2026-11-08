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


REBOX = {"sheet": "ER21276", "op": "rebox", "ref": "SYM-0009", "was_bbox_px": [180, 90, 220, 110],
         "bbox_px": [180, 80, 220, 110], "reason": "横棒まで含める"}


def test_a_box_is_moved_once_and_only_from_the_reviewed_box():
    fixed, results = apply_corrections(bundle(), [REBOX])
    symbol = fixed["projects"][0]["symbols"][1]
    assert (round(symbol["cy"] * 500), round(symbol["h"] * 500)) == (95, 30)
    assert [r["status"] for r in results] == ["applied"]
    _, results = apply_corrections(fixed, [REBOX])
    assert [r["status"] for r in results] == ["already"]
    with pytest.raises(CorrectionError, match="expected"):
        apply_corrections(bundle(), [{**REBOX, "was_bbox_px": [100, 90, 140, 110]}])


def four_terminal_bundle():
    data = bundle()
    project = data["projects"][0]
    contact = project["symbols"][0]
    contact["terminals"] = [{"ref": f"SYM-0008-T{i}", "name": str(i), "tx": 0.49, "ty": y}
                            for i, y in enumerate((0.42, 0.47, 0.53, 0.58), start=1)]
    project["connections"] = [
        {"from_symbol_ref": "SYM-0009", "from_terminal_ref": None, "to_symbol_ref": "SYM-0008", "to_terminal_ref": "SYM-0008-T1"},
        {"from_symbol_ref": "SYM-0008", "from_terminal_ref": "SYM-0008-T1", "to_symbol_ref": "SYM-0008", "to_terminal_ref": "SYM-0008-T2"},
        {"from_symbol_ref": "SYM-0008", "from_terminal_ref": "SYM-0008-T4", "to_symbol_ref": "SYM-0009", "to_terminal_ref": None},
    ]
    return data


TWO = {"sheet": "ER21276", "op": "two_terminal_contact", "ref": "SYM-0008", "bbox_px": [480, 225, 520, 275], "reason": "内側の 2 端子"}


def test_a_four_terminal_contact_keeps_its_inner_terminals_and_their_wires():
    """外側の端子の配線は同じ側の内側の端子へ移り、外側と内側を結んでいた配線は自分どうしになって消える。"""
    fixed, results = apply_corrections(four_terminal_bundle(), [TWO])
    project = fixed["projects"][0]
    assert [t["ref"] for t in project["symbols"][0]["terminals"]] == ["SYM-0008-T2", "SYM-0008-T3"]
    assert [(c["from_terminal_ref"], c["to_terminal_ref"]) for c in project["connections"]] == [
        (None, "SYM-0008-T2"), ("SYM-0008-T3", None)]
    assert [r["status"] for r in results] == ["applied"]
    again, results = apply_corrections(fixed, [TWO])
    assert again == fixed and [r["status"] for r in results] == ["already"]


def test_a_left_out_wire_is_added_once_between_existing_terminals():
    """図面にあるのに付け忘れた配線（ER21226 の 33hb の端子 2 と NC2 の黒丸、DD-0020）を 1 回だけ足す。"""
    original = bundle()
    project = original["projects"][0]
    project["symbols"][0]["terminals"] = [{"ref": "SYM-0008-T1"}, {"ref": "SYM-0008-T2"}]
    project["symbols"][1]["terminals"] = [{"ref": "SYM-0009-T1"}]
    project["connections"] = [{"ref": "W-0003", "from_symbol_ref": "SYM-0008", "from_terminal_ref": "SYM-0008-T1",
                               "to_symbol_ref": "SYM-0008", "to_terminal_ref": "SYM-0008-T2"}]
    wire = {"sheet": "ER21276", "op": "add_wire", "from": "SYM-0008-T2", "to": "SYM-0009-T1", "reason": "図面の線"}
    fixed, results = apply_corrections(original, [wire])
    added = fixed["projects"][0]["connections"][-1]
    assert (added["ref"], added["from_symbol_ref"], added["to_symbol_ref"], added["note"]) == ("W-0004", "SYM-0008", "SYM-0009", ADDED_NOTE)
    assert [r["status"] for r in results] == ["applied"]
    again, results = apply_corrections(fixed, [wire])
    assert again == fixed and [r["status"] for r in results] == ["already"]
    with pytest.raises(CorrectionError, match="terminal not found"):
        apply_corrections(original, [{**wire, "to": "SYM-0009-T2"}])
