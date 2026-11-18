"""図面を回路コードにする・「もしも」のシミュレーション・波及範囲（DD-0029）。"""

import pytest

from analysis.synthetic_circuits.dataset import DatasetConfig, _make_page
from services import circuit_code, circuit_model
from services import circuit_whatif as w
from services.circuit_model import build_circuit, simulate
from tests.test_circuit_api import _save
from tests.test_circuit_model import self_holding_page

X = "X R100"


@pytest.fixture(autouse=True)
def builtin(monkeypatch):
    monkeypatch.setattr(circuit_model, "ngspice_path", lambda: None)


@pytest.fixture
def circuit():
    return build_circuit([self_holding_page()])


def _synthetic_pages(count=6):
    pages = []
    for i in range(count):
        project = _make_page(i, DatasetConfig())[0]["project"]
        project["id"] = i + 1
        pages.append(project)
    return pages


# ------------------------------------------------------------------------------------------- 回路コード


def test_code_round_trip_reproduces_circuit_and_simulation():
    pages = [self_holding_page()]
    circuit = build_circuit(pages)
    code = circuit_code.to_python(circuit, pages=pages, title="test")
    assert "c.coil('K1', 'N002', '0', device='X R100', source=(1, 'XC')" in code
    assert "c.contact_b('S2', 'P', 'N001', device='STOP'" in code
    again = circuit_code.from_python(code)
    assert again.elements == circuit.elements and again.coils == circuit.coils
    assert again.inputs == circuit.inputs and again.nodes == circuit.nodes
    assert again.diagnostics == circuit.diagnostics
    for inputs, energized in [({}, {}), ({"PB1": True}, {}), ({}, {X: True})]:
        a, b = simulate(circuit, inputs, energized), simulate(again, inputs, energized)
        assert a["coils"] == b["coils"] and a["nodes"] == b["nodes"]


def test_code_round_trip_on_synthetic_drawings():
    pages = _synthetic_pages()
    circuit = build_circuit(pages)
    again = circuit_code.from_python(circuit_code.to_python(circuit, pages=pages))
    assert again.elements == circuit.elements and again.coils == circuit.coils and again.inputs == circuit.inputs
    assert again.nodes == circuit.nodes


def test_edited_code_changes_the_circuit():
    """コードを書き換えると、図面に手を入れずに回路を変えられる（STOP を a 接点にすると、押さない限り止まる）。"""
    pages = [self_holding_page()]
    code = circuit_code.to_python(build_circuit(pages), pages=pages)
    edited = circuit_code.from_python(code.replace("c.contact_b('S2'", "c.contact_a('S2'"))
    assert simulate(edited, {"PB1": True})["coils"][X]["energized"] is False
    assert simulate(edited, {"PB1": True, "STOP": True})["coils"][X]["energized"] is True


def test_code_without_circuit_is_rejected():
    with pytest.raises(TypeError):
        circuit_code.from_python("x = 1")


# ------------------------------------------------------------------------------------------- もしも


def test_what_if_stop_pressed_while_holding(circuit):
    r = w.what_if(circuit, {}, {X: True}, {"inputs": {"STOP": True}})
    assert r["before"]["coils"][X] is True and r["after"]["coils"][X] is False
    assert [c["device"] for c in r["changes"]["coils"]] == [X]
    lamp = next(x for x in r["changes"]["loads"] if x["symbol_ref"] == "L")
    assert (lamp["before"], lamp["after"]) == (True, False)


def test_what_if_welded_contact_lights_lamp(circuit):
    r = w.what_if(circuit, {}, {}, {"contacts": [{"project_id": 1, "symbol_ref": "XL", "closed": True}]})
    assert r["changes"]["coils"] == []
    assert [(x["symbol_ref"], x["after"]) for x in r["changes"]["loads"]] == [("L", True)]


def test_what_if_coil_open_drops_relay(circuit):
    r = w.what_if(circuit, {}, {X: True}, {"open": [{"project_id": 1, "symbol_ref": "XC"}]})
    assert r["after"]["coils"][X] is False
    assert any(x["symbol_ref"] == "L" and x["after"] is False for x in r["changes"]["loads"])


def test_what_if_burnt_lamp_and_shorted_lamp(circuit):
    burnt = w.what_if(circuit, {}, {X: True}, {"open": [{"project_id": 1, "symbol_ref": "L"}]})
    assert [(x["symbol_ref"], x["after"]) for x in burnt["changes"]["loads"]] == [("L", False)]
    assert burnt["after"]["coils"][X] is True
    shorted = w.what_if(circuit, {}, {X: True}, {"short": [{"project_id": 1, "symbol_ref": "L"}]})
    assert any("短絡" in m for m in shorted["warnings"])


def test_what_if_forced_relay_and_unknown_symbol(circuit):
    r = w.what_if(circuit, {}, {}, {"devices": {X: True}, "open": [{"project_id": 9, "symbol_ref": "NONE"}]})
    assert r["after"]["coils"][X] is True
    assert r["unknown_symbols"] == [{"project_id": 9, "symbol_ref": "NONE"}]


def test_sequence_self_holding(circuit):
    steps = w.run_sequence(circuit, [{"inputs": {"PB1": True}, "label": "押す"}, {"inputs": {"PB1": False}},
                                     {"inputs": {"STOP": True}}])
    assert [s["coils"][X] for s in steps] == [False, True, True, False]
    assert [s["label"] for s in steps] == ["初期状態", "押す", "手順 2", "手順 3"]
    assert steps[2]["changes"]["coils"] == [] and steps[3]["inputs"] == {"PB1": False, "STOP": True}


def test_sequence_with_fault_never_holds(circuit):
    """X の自己保持の接点が接触不良なら、押している間だけ動く。"""
    fault = {"contacts": [{"project_id": 1, "symbol_ref": "XA", "closed": False}]}
    steps = w.run_sequence(circuit, [{"inputs": {"PB1": True}}, {"inputs": {"PB1": False}}], change=fault)
    assert [s["coils"][X] for s in steps] == [False, True, False]


# ------------------------------------------------------------------------------------------- 波及範囲


def test_impact_downstream_and_upstream(circuit):
    pb = w.impact(circuit, "PB1")
    assert [(d["device"], d["depth"]) for d in pb["devices"]] == [(X, 1)]
    assert [(x["symbol_ref"], x["depth"]) for x in pb["loads"]] == [("XC", 1), ("L", 2)]
    assert w.impact(circuit, X)["upstream"] == ["PB1", "STOP"]
    with pytest.raises(ValueError):
        w.impact(circuit, "NOPE")


def test_static_impact_covers_every_actual_change_on_synthetic_drawings():
    """静的な波及範囲は取りこぼさない: 入力を 1 つ切り替えて実際に変わったリレー・負荷は、必ず波及範囲に入る。"""
    circuit = build_circuit(_synthetic_pages(10))
    checked = 0
    for device in circuit.inputs:
        reach = w.impact(circuit, device)
        devices = {d["device"] for d in reach["devices"]}
        loads = {(x["project_id"], x["symbol_ref"]) for x in reach["loads"]}
        r = w.what_if(circuit, {}, {}, {"inputs": {device: True}})
        for c in r["changes"]["coils"]:
            assert c["device"] in devices
        for x in r["changes"]["loads"]:
            assert (x["project_id"], x["symbol_ref"]) in loads
            checked += 1
    assert checked > 0


# ------------------------------------------------------------------------------------------- API


def test_what_if_sequence_impact_and_code_api(client):
    pid = _save(client, self_holding_page())
    body = {"project_ids": [pid], "energized": {X: True}, "change": {"inputs": {"STOP": True}}}
    r = client.post("/api/circuit/what-if", json=body).json()
    assert r["changes"]["coils"] == [{"device": X, "before": True, "after": False}]
    assert all(x["page"] for x in r["changes"]["loads"])

    seq = client.post("/api/circuit/sequence", json={"project_ids": [pid], "steps": [
        {"label": "押す", "inputs": {"PB1": True}}, {"label": "離す", "inputs": {"PB1": False}}]}).json()
    assert [s["coils"][X] for s in seq["steps"]] == [False, True, True]

    imp = client.post("/api/circuit/impact", json={"project_ids": [pid], "device": "PB1"}).json()
    assert imp["devices"][0]["device"] == X and imp["loads"][0]["page"]
    assert client.post("/api/circuit/impact", json={"project_ids": [pid], "device": "NOPE"}).status_code == 400

    code = client.post("/api/circuit/code", json={"project_ids": [pid]})
    assert code.status_code == 200 and "attachment" in code.headers["content-disposition"]
    assert circuit_code.from_python(code.text).inputs == ["PB1", "STOP"]
