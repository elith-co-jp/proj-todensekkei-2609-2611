"""正解つきの合成回路での検証（DD-0032）: 動作・波及範囲が正解の論理と一致し、入れた誤りを矛盾チェックが見つけて直す。"""

import copy
import random

import pytest

from analysis.synthetic_circuits import logic, logic_eval
from services import circuit_code, circuit_model, consistency

SEEDS = [0, 3, 7]


@pytest.fixture(autouse=True)
def builtin(monkeypatch):
    monkeypatch.setattr(circuit_model, "ngspice_path", lambda: None)


def _circuit(data):
    return circuit_model.build_circuit(copy.deepcopy(data["pages"]), data["bus_texts"])


@pytest.mark.parametrize("seed", SEEDS)
def test_clean_drawings_reproduce_the_true_logic(seed):
    spec = logic.make_spec(seed)
    data = logic.build(spec)
    circuit = _circuit(data)
    assert sorted(circuit.coils) == sorted(spec.relays)
    assert sorted(circuit.inputs) == sorted(spec.inputs)
    assert len(circuit.sheet_links) == data["tag_pairs"]
    sim = logic_eval.behaviour(spec, circuit, seed=seed)
    assert sim["agree"] == sim["total"] > 0
    again = logic_eval.behaviour(spec, circuit_code.from_python(circuit_code.to_python(circuit, pages=data["pages"])), seed=seed)
    assert again["agree"] == again["total"]
    impact = logic_eval.impact_agreement(spec, circuit)
    assert impact["agree"] == impact["total"] > 0


@pytest.mark.parametrize("seed", SEEDS)
def test_no_false_alarms_on_clean_drawings(seed):
    data = logic.build(logic.make_spec(seed))
    result = consistency.check(copy.deepcopy(data["pages"]), data["structures"], bus_texts=data["bus_texts"], solvers=False)
    assert result["issues"] == []
    compared = {c["check"]: c["compared"] for c in result["checks"]}
    assert compared["relay_table"] > 0 and compared["wire_numbers"] > 0 and compared["open_terminals"] > 0


def test_oracle_is_independent_of_the_circuit_solver():
    # 自己保持: 起動で入り、起動を離しても保持し、停止で切れる。
    spec = logic.Spec(0, ["ER1"], ["30HA FV1"], ["STOP01", "PB01"],
                      [logic.Rung(0, ("coil", "30HA FV1"), [[("STOP01", "b")], [("PB01", "a"), ("30HA FV1", "a")]])],
                      {"30HA FV1": 0})
    assert logic.oracle(spec, {"PB01": True})["coils"]["30HA FV1"]
    assert logic.oracle(spec, {}, {"30HA FV1": True})["coils"]["30HA FV1"]
    assert not logic.oracle(spec, {"STOP01": True}, {"30HA FV1": True})["coils"]["30HA FV1"]


@pytest.mark.parametrize("kind", sorted(logic_eval.FIXABLE))
@pytest.mark.parametrize("seed", [0, 7])
def test_misreads_are_found_and_fixed_back_to_the_truth(kind, seed):
    spec = logic.make_spec(seed)
    data = logic.build(spec)
    row = logic_eval.evaluate_fault(spec, data, kind, seed * 1000 + 5)
    if row is None:
        pytest.skip("この回路には入れられない誤り")
    assert row["detected"] and row["located"]
    assert row["right_fix"] and row["wrong_fixes"] == 0
    assert row["restored"]


@pytest.mark.parametrize("kind", ["drop_contact", "cut_wire", "extra_wire"])
@pytest.mark.parametrize("seed", [0, 7])
def test_wiring_faults_are_found_without_wrong_fixes(kind, seed):
    spec = logic.make_spec(seed)
    row = logic_eval.evaluate_fault(spec, logic.build(spec), kind, seed * 1000 + 5)
    assert row["detected"] and row["located"]
    assert row["wrong_fixes"] == 0


def test_coil_misread_fixes_the_coil_not_the_contacts():
    spec = logic.make_spec(0)
    data = logic.build(spec)
    fault = logic_eval.inject(data, spec, "coil_label", random.Random(1))
    result = consistency.check(copy.deepcopy(data["pages"]), data["structures"], bus_texts=data["bus_texts"], solvers=False)
    fixes = [i["fix"] for i in result["issues"] if i["fix"]]
    assert fixes and {f["symbol_ref"] for f in fixes} == {fault["refs"][0]}


def test_cut_wire_is_reported_as_open_terminal():
    spec = logic.make_spec(3)
    data = logic.build(spec)
    page = data["pages"][0]
    coil = next(s for s in page["symbols"] if s["class_key"] == "relay_coil")
    page["connections"] = [c for c in page["connections"]
                           if not (c["from_symbol_ref"] == coil["ref"] and c["from_terminal_ref"].endswith("T2"))]
    result = consistency.check(copy.deepcopy(data["pages"]), data["structures"], bus_texts=data["bus_texts"], solvers=False)
    issues = [i for i in result["issues"] if i["check"] == "open_terminals"]
    assert [loc["symbol_ref"] for i in issues for loc in i["locations"]] == [coil["ref"]]
