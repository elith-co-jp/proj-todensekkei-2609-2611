"""図面のアノテーションから組んだ回路の On/Off シミュレーション（DD-0009）。"""

import pytest

from services import circuit_model
from services.circuit_model import build_circuit, simulate


def _sym(ref, cls, label=None, terms=2, cy=0.5):
    return {"ref": ref, "class_key": cls, "class_label": cls, "label": label, "cx": 0.5, "cy": cy, "w": 0.02, "h": 0.02,
            "terminals": [{"ref": f"{ref}-T{i}", "name": str(i), "tx": 0.5, "ty": cy} for i in range(1, terms + 1)]}


def _wire(a, b):
    (sa, ta), (sb, tb) = a, b
    return {"from_symbol_ref": sa, "from_terminal_ref": f"{sa}-T{ta}", "to_symbol_ref": sb, "to_terminal_ref": f"{sb}-T{tb}",
            "kind": "wire"}


def self_holding_page():
    """PER ─ STOP(b) ─┬─ PB1(a) ─┬─ X コイル ─ NER、X の a 接点が PB1 と並列。X の別の a 接点で表示灯 L。"""
    symbols = [
        _sym("P", "power_bus", terms=1, cy=0.1), _sym("N", "power_bus", terms=1, cy=0.9),
        _sym("STOP", "contact_b", "STOP"), _sym("PB1", "contact_a", "PB1"), _sym("XA", "contact_a", "X R100"),
        _sym("XC", "relay_coil", "X R100"), _sym("XL", "contact_a", "X R100"), _sym("L", "lamp", "RL"),
    ]
    wires = [
        _wire(("P", 1), ("STOP", 1)), _wire(("STOP", 2), ("PB1", 1)), _wire(("STOP", 2), ("XA", 1)),
        _wire(("PB1", 2), ("XC", 1)), _wire(("XA", 2), ("XC", 1)), _wire(("XC", 2), ("N", 1)),
        _wire(("P", 1), ("XL", 1)), _wire(("XL", 2), ("L", 1)), _wire(("L", 2), ("N", 1)),
        # エディタでは接点の両端を結ぶ線で接点を表す。導線として扱うと接点が短絡する。
        _wire(("PB1", 1), ("PB1", 2)), _wire(("XA", 1), ("XA", 2)),
    ]
    return {"id": 1, "name": "test", "image_width": 1000, "image_height": 1000, "symbols": symbols, "connections": wires}


@pytest.fixture(params=["builtin", "ngspice"])
def engine(request, monkeypatch):
    if request.param == "builtin":
        monkeypatch.setattr(circuit_model, "ngspice_path", lambda: None)
    elif circuit_model.ngspice_path() is None:
        pytest.skip("ngspice が無い環境")
    return request.param


def test_self_holding_relay_keeps_and_releases(engine):
    circuit = build_circuit([self_holding_page()])
    assert circuit.inputs == ["PB1", "STOP"]
    assert any(d["kind"] == "bus_guessed" for d in circuit.diagnostics)

    off = simulate(circuit, {})
    assert off["engine"] == engine and off["coils"]["X R100"]["energized"] is False

    pressed = simulate(circuit, {"PB1": True})
    assert pressed["coils"]["X R100"]["energized"] is True
    lamp = next(e for e in circuit.elements if e.symbol_ref == "L")
    assert pressed["nodes"][lamp.a] > 100

    held = simulate(circuit, {"PB1": False}, {"X R100": True})
    assert held["coils"]["X R100"]["energized"] is True

    stopped = simulate(circuit, {"STOP": True}, {"X R100": True})
    assert stopped["coils"]["X R100"]["energized"] is False
    assert stopped["nodes"][lamp.a] < 1


def test_contact_label_without_unit_follows_the_only_coil_with_that_number():
    page = self_holding_page()
    next(s for s in page["symbols"] if s["ref"] == "XA")["label"] = "X"
    circuit = build_circuit([page])
    assert next(e for e in circuit.elements if e.symbol_ref == "XA").device == "X R100"


def test_buses_with_names_are_separate_supplies_across_pages():
    pages = []
    for pid in (1, 2):
        page = self_holding_page()
        page["id"] = pid
        pages.append(page)
    texts = {pid: [{"text": "PER2100100", "box": [480, 80, 560, 95]}, {"text": "NER2100100", "box": [480, 880, 560, 895]}]
             for pid in (1, 2)}
    circuit = build_circuit(pages, texts)
    assert "P_2100100" in circuit.nodes and not any(d["kind"] == "bus_guessed" for d in circuit.diagnostics)
    # 同じ器具番号のコイルが 2 枚にあれば同じリレー。どちらの PB1 でも励磁する。
    assert len(circuit.coils["X R100"]) == 2
    assert simulate(circuit, {"PB1": True})["coils"]["X R100"]["energized"] is True


def test_device_without_two_terminals_is_reported_not_simulated():
    page = self_holding_page()
    page["symbols"].append(_sym("Y", "relay_coil", "Y R1", terms=1))
    circuit = build_circuit([page])
    assert [d["symbol_ref"] for d in circuit.diagnostics if d["kind"] == "terminals_missing"] == ["Y"]
    assert "Y R1" not in circuit.coils


def test_wire_joining_supply_and_return_is_reported():
    page = self_holding_page()
    page["connections"].append(_wire(("P", 1), ("XC", 2)))  # コイルを飛ばして P と N を導線で直結
    assert any(d["kind"] == "bus_conflict" for d in build_circuit([page]).diagnostics)


def test_closed_contact_across_the_supply_is_warned_as_a_short():
    page = self_holding_page()
    page["symbols"].append(_sym("SC", "contact_a", "SC"))
    page["connections"] += [_wire(("P", 1), ("SC", 1)), _wire(("SC", 2), ("N", 1))]
    circuit = build_circuit([page])
    assert simulate(circuit, {})["warnings"] == []
    assert any("短絡" in w for w in simulate(circuit, {"SC": True})["warnings"])
