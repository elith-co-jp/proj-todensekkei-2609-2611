"""コイルの配線の取り決め（DD-0009 §6）: 判定と、以前の描き方からの移し替え。"""

from __future__ import annotations

from services.circuit_model import build_circuit
from services.coil_wiring import coil_issues, split_through_wires

W, H = 1000, 1000


def _sym(ref, cls, cx, cy, w=0.04, h=0.04, terminals=()):
    return {"ref": ref, "class_key": cls, "label": "33X ABC" if cls == "relay_coil" else "", "cx": cx, "cy": cy, "w": w, "h": h,
            "terminals": [{"ref": f"{ref}-T{i}", "name": str(i), "tx": tx, "ty": ty} for i, (tx, ty) in enumerate(terminals, 1)]}


def _wire(a, ta, b, tb):
    return {"from_symbol_ref": a, "from_terminal_ref": ta, "to_symbol_ref": b, "to_terminal_ref": tb, "kind": "wire"}


def _old_style():
    """上の分岐点から下の分岐点へ、コイルの中を通り抜けて 1 本で引いた配線（以前の取り決め）。"""
    symbols = [_sym("J1", "junction", 0.5, 0.3, 0.01, 0.01, [(0.5, 0.3)]),
               _sym("C1", "relay_coil", 0.5, 0.5),
               _sym("J2", "junction", 0.5, 0.7, 0.01, 0.01, [(0.5, 0.7)])]
    return symbols, [_wire("J1", "J1-T1", "J2", "J2-T1")]


def test_wire_through_coil_is_reported():
    symbols, connections = _old_style()
    assert [i["kind"] for i in coil_issues(symbols, connections, W, H)] == ["through"]


def test_split_gives_the_coil_two_terminals_on_separate_nodes():
    symbols, connections = _old_style()
    # 以前の描き方では、コイルの上下が同じノードになり、コイルは回路に入らない。
    assert build_circuit([{"id": 1, "symbols": symbols, "connections": connections}]).elements == []

    symbols, connections, counts = split_through_wires(symbols, connections, W, H)

    assert counts["split"] == 1
    coil = next(s for s in symbols if s["ref"] == "C1")
    assert [(round(t["tx"], 3), round(t["ty"], 3)) for t in coil["terminals"]] == [(0.5, 0.481), (0.5, 0.519)]
    assert coil_issues(symbols, connections, W, H) == []
    element = build_circuit([{"id": 1, "symbols": symbols, "connections": connections}]).elements[0]
    assert element.kind == "load" and element.a != element.b


def test_wire_passing_by_the_edge_of_a_coil_is_not_through():
    symbols = [_sym("J1", "junction", 0.518, 0.3, 0.01, 0.01, [(0.518, 0.3)]),
               _sym("C1", "relay_coil", 0.5, 0.5, terminals=[(0.5, 0.48), (0.5, 0.52)]),
               _sym("J2", "junction", 0.518, 0.7, 0.01, 0.01, [(0.518, 0.7)])]
    connections = [_wire("J1", "J1-T1", "J2", "J2-T1"), _wire("J1", "J1-T1", "C1", "C1-T1"), _wire("C1", "C1-T2", "J2", "J2-T1")]
    assert coil_issues(symbols, connections, W, H) == []


def test_missing_terminal_and_unwired_terminal_are_reported():
    symbols = [_sym("C1", "relay_coil", 0.2, 0.5, terminals=[(0.2, 0.48)]),
               _sym("C2", "solenoid", 0.6, 0.5, terminals=[(0.6, 0.48), (0.6, 0.52)]),
               _sym("J1", "junction", 0.6, 0.3, 0.01, 0.01, [(0.6, 0.3)])]
    connections = [_wire("J1", "J1-T1", "C2", "C2-T1")]
    issues = coil_issues(symbols, connections, W, H)
    assert [(i["symbol_ref"], i["kind"]) for i in issues] == [("C1", "terminals"), ("C2", "unwired")]
    assert issues[1]["terminal_refs"] == ["C2-T2"]

