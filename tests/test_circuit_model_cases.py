"""回路の組み立て（build_circuit）とシミュレーションの、図面の書き方のばらつき・壊れ方への備え。"""

from __future__ import annotations

import dataclasses

from services import circuit_model
from services.circuit_model import Element, _MnaPlan, build_circuit, export_netlist, joins_inside_symbol, simulate
from tests.test_circuit_model import _sym, _wire, self_holding_page


def _page(symbols, connections, pid=1, **extra):
    return {"id": pid, "name": f"p{pid}", "image_width": 1000, "image_height": 1000, "symbols": symbols,
            "connections": connections, **extra}


def test_bus_names_and_inside_links():
    assert circuit_model._bus_side("RST100") is None
    assert joins_inside_symbol(_sym("J", "junction"), "J-T1", "J-T2", 1000, 1000) is True
    frame = _sym("F", "other", "盤")
    assert joins_inside_symbol(frame, "F-T1", "F-T9", 1000, 1000) is False


def test_wires_with_unknown_symbols_and_missing_terminal_refs():
    symbols = [
        _sym("P", "power_bus", "PER1", terms=1, cy=0.1), _sym("N", "power_bus", "NER1", terms=1, cy=0.9),
        _sym("J", "junction", terms=3), _sym("L", "lamp", "RL"), _sym("K", "contact_a", "K1"),
    ]
    loose = {"from_symbol_ref": "P", "from_terminal_ref": None, "to_symbol_ref": "J", "to_terminal_ref": None, "kind": "wire"}
    no_term = {"from_symbol_ref": "J", "from_terminal_ref": "J-T2", "to_symbol_ref": "K", "to_terminal_ref": None,
               "kind": "wire"}
    ghost = {"from_symbol_ref": "GHOST", "from_terminal_ref": None, "to_symbol_ref": "L", "to_terminal_ref": "L-T1",
             "kind": "wire"}
    circuit = build_circuit([_page(symbols, [loose, no_term, ghost, _wire(("J", 3), ("L", 1)), _wire(("L", 2), ("N", 1))])])
    assert circuit.node_of[(1, "P", "P-T1")] == circuit.node_of[(1, "J", "J-T1")] == circuit.node_of[(1, "J", "J-T3")]
    assert [d["symbol_ref"] for d in circuit.diagnostics if d["kind"] == "wire_without_terminal"] == ["K"]


def test_sheet_ref_wire_to_a_tag_symbol_adds_its_external_ref():
    tag = _sym("TAG", "other", "ER21300へ", terms=1)
    lamp = _sym("L", "lamp", "RL")
    wire = {**_wire(("L", 1), ("TAG", 1)), "kind": "sheet_ref", "external_ref": "ER21300", "wire_no": "601"}
    circuit = build_circuit([_page([lamp, tag], [wire])])
    assert circuit.wires and circuit.wires[0]["to"] == (1, "TAG", "TAG-T1")


def test_bus_from_texts_when_there_are_no_bus_symbols():
    left = {**_sym("JL", "junction", terms=1), "cx": 0.05, "cy": 0.12}
    right = {**_sym("JR", "junction", terms=1), "cx": 0.55, "cy": 0.12}
    lamp = _sym("L", "lamp", "RL")
    texts = {1: [{"text": "PER2100100", "box": [40, 100, 120, 110]}, {"text": "NER2100100", "box": [540, 100, 620, 110]},
                 {"text": "読めない", "box": [0, 0, 1, 1]}]}
    circuit = build_circuit([_page([left, right, lamp], [_wire(("JL", 1), ("L", 1)), _wire(("L", 2), ("JR", 1))])],
                            bus_texts=texts)
    assert circuit.node_of[(1, "JL", "JL-T1")] == "P_2100100"
    assert circuit.node_of[(1, "JR", "JR-T1")] == circuit_model.GROUND
    assert sum(d["kind"] == "bus_guessed" for d in circuit.diagnostics) == 2


def test_a_lone_bus_end_without_texts_is_unknown():
    circuit = build_circuit([_page([_sym("P", "power_bus", terms=1)], [])])
    kinds = [d["kind"] for d in circuit.diagnostics]
    assert "bus_unknown" in kinds and "no_supply" in kinds


def test_fast_solver_falls_back_for_names_it_cannot_reproduce():
    circuit = build_circuit([self_holding_page()])
    assert _MnaPlan.build(circuit) is not None
    first = circuit.elements[0]

    def plan_with(**change):
        return _MnaPlan.build(dataclasses.replace(circuit, elements=[dataclasses.replace(first, **change),
                                                                      *circuit.elements[1:]]))

    assert plan_with(a="") is None                       # ノード名が空
    assert plan_with(kind="load", name="V1") is None     # 電源と同じ頭文字の素子名
    assert plan_with(kind="load", name="R 1") is None    # 空白入りの素子名
    assert _MnaPlan.build(dataclasses.replace(circuit, nodes=[*circuit.nodes, "!"])) is not None
    assert _MnaPlan.build(dataclasses.replace(circuit, nodes=[*circuit.nodes, ""])) is None


def test_diodes_that_never_settle_are_reported(monkeypatch):
    symbols = [_sym("P", "power_bus", "PER1", terms=1, cy=0.1), _sym("N", "power_bus", "NER1", terms=1, cy=0.9),
               _sym("D", "diode", "D1"), _sym("L", "lamp", "RL")]
    page = _page(symbols, [_wire(("P", 1), ("D", 1)), _wire(("D", 2), ("L", 1)), _wire(("L", 2), ("N", 1))])
    circuit = build_circuit([page])
    monkeypatch.setattr(circuit_model, "ngspice_path", lambda: None)
    monkeypatch.setattr(circuit_model, "_diode_conducts", lambda _e, on, _v: not on)
    result = simulate(circuit, {})
    assert any("ダイオードの向きが落ち着きません" in w for w in result["warnings"])
    # 向きの分からないダイオードは、書き出すネットリストでは導線（R_ON の抵抗）にする
    assert "\nRD_1_D " in export_netlist(circuit)


def test_element_defaults():
    e = Element("R_1_X", "load", "N001", "0", 1, "X", "X")
    assert e.resistance == circuit_model.R_LOAD and not e.directed
