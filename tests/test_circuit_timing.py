"""タイマー（オンディレー・オフディレー）と時間の流れのシミュレーション（DD-0033）。"""

import copy

import pytest

from analysis.synthetic_circuits import logic, logic_eval
from services import circuit_code, circuit_model
from services.circuit_model import build_circuit, timer_setting
from services.circuit_timing import run_timeline, state_at
from tests.conftest import create_project
from tests.test_circuit_model import _sym, _wire

T = "2T TM1"


@pytest.fixture(autouse=True)
def builtin(monkeypatch):
    monkeypatch.setattr(circuit_model, "ngspice_path", lambda: None)


def timer_page(mode="on_delay", delay=5, instant=False):
    """PB1 → タイマー T のコイル。T の接点で表示灯 L（instant なら瞬時接点）。"""
    coil = _sym("TC", "relay_coil", T)
    coil["attributes"] = {"timer_mode": mode, "delay_s": delay}
    contact = _sym("TA", "contact_a", T)
    contact["attributes"] = {"timer_contact": "instant"} if instant else {}
    symbols = [_sym("P", "power_bus", terms=1, cy=0.1), _sym("N", "power_bus", terms=1, cy=0.9),
               _sym("PB", "contact_a", "PB1"), coil, contact, _sym("L", "lamp", "RL")]
    wires = [_wire(("P", 1), ("PB", 1)), _wire(("PB", 2), ("TC", 1)), _wire(("TC", 2), ("N", 1)),
             _wire(("P", 1), ("TA", 1)), _wire(("TA", 2), ("L", 1)), _wire(("L", 2), ("N", 1))]
    return {"id": 1, "name": "timer", "image_width": 1000, "image_height": 1000, "symbols": symbols, "connections": wires}


def lamp(timeline, t):
    snap = state_at(timeline, t)
    return next(x["energized"] for x in snap["loads"] if x["label"] == "RL")


def test_timer_setting_from_attributes():
    assert timer_setting({"timer_mode": "オンディレー", "delay_s": "3"}) == {"mode": "on_delay", "delay_s": 3.0}
    assert timer_setting({"timer_mode": "限時復帰", "delay_s": 2}) == {"mode": "off_delay", "delay_s": 2.0}
    assert timer_setting({"timer_mode": "on-delay"}) == {"mode": "on_delay", "delay_s": None}
    assert timer_setting({}) is None
    page = timer_page()
    page["symbols"][3]["attributes"] = {"timer_mode": "on_delay"}
    circuit = build_circuit([page])
    assert circuit.timers[T] == {"mode": "on_delay", "delay_s": 0.0}
    assert any(d["kind"] == "timer_without_delay" for d in circuit.diagnostics)


def test_on_delay_closes_after_the_delay_and_resets_when_released():
    circuit = build_circuit([timer_page()])
    tl = run_timeline(circuit, [{"at": 1, "inputs": {"PB1": True}}, {"at": 3, "inputs": {"PB1": False}},
                                {"at": 4, "inputs": {"PB1": True}}], until=12)["timeline"]
    assert not lamp(tl, 5.9)          # 1 秒で押したが 3 秒で離した（5 秒たっていない）
    assert not lamp(tl, 8.9)
    assert lamp(tl, 9.0)              # 4 秒で押し直して 5 秒後
    assert state_at(tl, 9.0)["label"] == f"{T} の時間が経過"
    assert state_at(tl, 2)["timers"][T] == {"output": False, "due": 6.0}


def test_off_delay_holds_after_release():
    circuit = build_circuit([timer_page("off_delay", 2)])
    tl = run_timeline(circuit, [{"at": 1, "inputs": {"PB1": True}}, {"at": 3, "inputs": {"PB1": False}}])["timeline"]
    assert lamp(tl, 1.0) and lamp(tl, 4.9) and not lamp(tl, 5.0)


def test_instant_contact_follows_the_coil():
    circuit = build_circuit([timer_page(instant=True)])
    tl = run_timeline(circuit, [{"at": 1, "inputs": {"PB1": True}}], until=2)["timeline"]
    assert lamp(tl, 1.0)


def test_every_step_has_change_lists():
    # 画面（タイマーの表）は初期状態・終了でも changes.coils などを配列として読む
    circuit = build_circuit([timer_page(instant=True)])
    tl = run_timeline(circuit, [{"at": 1, "inputs": {"PB1": True}}], until=2)["timeline"]
    assert tl[0]["label"] == "初期状態" and tl[-1]["label"] == "終了"
    for step in tl:
        assert all(isinstance(step["changes"][k], list) for k in ("coils", "loads", "contacts")), step["label"]


def test_what_if_longer_delay_and_faults():
    circuit = build_circuit([timer_page()])
    events = [{"at": 0, "inputs": {"PB1": True}}]
    assert lamp(run_timeline(circuit, events, until=6)["timeline"], 5)
    assert not lamp(run_timeline(circuit, events, until=6, delays={T: 8})["timeline"], 6)
    burnt = run_timeline(circuit, events, until=6, change={"open": [{"project_id": 1, "symbol_ref": "TC"}]})
    assert not lamp(burnt["timeline"], 6)
    with pytest.raises(ValueError):
        run_timeline(circuit, events, delays={"NOPE": 1})


def test_code_round_trip_keeps_timers():
    circuit = build_circuit([timer_page("off_delay", 2.5, instant=True)])
    code = circuit_code.to_python(circuit)
    assert f"c.timer({T!r}, 'off_delay', 2.5)" in code and "instant=True" in code
    again = circuit_code.from_python(code)
    assert again.timers == circuit.timers
    assert [e.instant for e in again.elements] == [e.instant for e in circuit.elements]


@pytest.mark.parametrize("seed", [0, 3, 6])
def test_timeline_matches_fixed_step_truth_on_synthetic_drawings(seed):
    spec = logic.make_spec(seed, timers=True)
    data = logic.build(spec)
    circuit = build_circuit(copy.deepcopy(data["pages"]), data["bus_texts"])
    assert set(circuit.timers) == set(spec.timers)
    result = logic_eval.timed_behaviour(spec, circuit, seed=seed)
    assert result["agree"] == result["total"] > 0
    # 時間を変えると正解とずれる（比べ方が時間に敏感であること）
    for cfg in circuit.timers.values():
        cfg["delay_s"] += 1.0
    changed = logic_eval.timed_behaviour(spec, circuit, seed=seed)
    assert changed["agree"] < changed["total"]


def test_timeline_api(client):
    pid = create_project(client, "sheet.png", 1000, 1000)
    page = timer_page()
    symbols = [{k: s[k] for k in ("ref", "class_key", "label", "cx", "cy", "w", "h")} | {"attributes": s.get("attributes") or {}}
               | {"terminals": [{k: t[k] for k in ("ref", "name", "tx", "ty")} for t in s["terminals"]]}
               for s in page["symbols"]]
    for s in symbols:
        if s["class_key"] == "lamp":
            s["class_key"] = "solenoid"
    assert client.put(f"/api/projects/{pid}/annotations", json={"symbols": symbols, "connections": page["connections"]}).status_code == 200
    summary = client.post("/api/circuit/build", json={"project_ids": [pid]}).json()
    assert summary["timers"] == [{"device": T, "mode": "on_delay", "delay_s": 5.0}]
    res = client.post("/api/circuit/timeline", json={"project_ids": [pid], "events": [{"at": 1, "inputs": {"PB1": True}}],
                                                     "until": 10})
    assert res.status_code == 200, res.text
    body = res.json()
    assert [s["t"] for s in body["timeline"]] == [0, 1, 6, 10]
    assert body["timeline"][2]["changes"]["contacts"][0]["after"] is True
    bad = client.post("/api/circuit/timeline", json={"project_ids": [pid], "delays": {"NOPE": 1}})
    assert bad.status_code == 400


def test_input_at_the_same_moment_as_expiry_wins_and_is_reported():
    circuit = build_circuit([timer_page()])
    tl = run_timeline(circuit, [{"at": 1, "inputs": {"PB1": True}}, {"at": 6, "inputs": {"PB1": False}}], until=8)["timeline"]
    assert not lamp(tl, 6) and not lamp(tl, 8)
    assert any("競合" in w for w in state_at(tl, 6)["warnings"])
