"""図面のコード表現への変更（DD-0034）で、正しくない操作を断る場合と、細かい操作。"""

import pytest

from services import circuit_edit as ce
from tests.test_circuit_model import self_holding_page


def _err(ops, pages=None):
    with pytest.raises(ce.EditError) as caught:
        ce.apply_ops(pages or [self_holding_page()], ops)
    return [e["message"] for e in caught.value.errors]


def _end(ref, terminal):
    return {"symbol_ref": ref, "terminal": terminal}


def test_invalid_operations_are_explained():
    messages = _err([
        {"op": "connect", "project_id": 1},
        {"op": "connect", "project_id": 1, "from": _end("PB1", "9"), "to": _end("XC", "1")},
        {"op": "add_component", "project_id": 1, "component": {"class_key": "contact_a", "ref": "PB1"}},
        {"op": "add_component", "project_id": 1, "component": {"class_key": "motor"}},
        {"op": "connect", "project_id": 1, "from": _end("PB1", "1"), "to": _end("PB1", "1")},
        {"op": "set_class", "project_id": 1, "symbol_ref": "PB1", "class_key": "motor"},
        {"op": "connect", "project_id": 1, "from": _end("P", "1"), "to": _end("STOP", "1")},
        {"op": "disconnect", "project_id": 1, "from": _end("PB1", "1"), "to": _end("XC", "2")},
        {"op": "insert_in_series", "project_id": 1, "from": _end("P", "1"), "to": _end("STOP", "1"),
         "component": {"class_key": "junction"}},
    ])
    assert messages == [
        "from, to がありません",
        "記号 PB1 に端子 9 がありません",
        "参照名 PB1 はもう使われています",
        f"種類 motor は足せません（{', '.join(ce.COMPONENT_CLASSES + ce.POINT_CLASSES)}）",
        "同じ端子どうしはつなげません",
        "種類 motor にはできません",
        "P と STOP はもうつながっています",
        "PB1-T1 と XC-T2 の間に配線がありません",
        "直列に入れられるのは 2 端子の部品だけです",
    ]
    assert _err([{"op": "set_label", "project_id": 1, "symbol_ref": "X", "label": ""}] * (ce.MAX_OPS + 1)) == [
        f"操作は {ce.MAX_OPS} 個まで"]


def test_points_get_one_terminal_and_new_refs_skip_used_ones():
    page = self_holding_page()
    page["symbols"].append({**page["symbols"][0], "ref": "EDIT-0001"})
    pages, notes = ce.apply_ops([page], [
        {"op": "add_component", "project_id": 1, "component": {"class_key": "junction", "cx": 0.2, "cy": 0.2}},
        {"op": "set_attribute", "project_id": 1, "symbol_ref": "XC", "key": "coil_resistance_ohm", "value": 100},
        {"op": "set_attribute", "project_id": 1, "symbol_ref": "XC", "key": "coil_resistance_ohm", "value": None},
    ])
    added = next(s for s in pages[0]["symbols"] if s["ref"] == "EDIT-0002")
    assert len(added["terminals"]) == 1 and notes[0].startswith("EDIT-0002")
    assert "coil_resistance_ohm" not in next(s for s in pages[0]["symbols"] if s["ref"] == "XC")["attributes"]


def test_terminal_names_and_fixes():
    page = self_holding_page()
    assert ce._term_name(page, "PB1", "PB1-T1") == "1"
    assert ce._term_name(page, "PB1", "GONE") == "GONE"
    assert ce.fix_to_op({"type": "set_label", "project_id": 1, "symbol_ref": "XA", "value": "X R100"})["op"] == "set_label"
    with pytest.raises(ValueError, match="操作にできません"):
        ce.fix_to_op({"type": "remove", "project_id": 1, "symbol_ref": "XA", "value": None})
