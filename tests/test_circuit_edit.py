"""図面のコード表現への変更（LLM が使う操作）: 適用・試す・履歴・取り消し（DD-0034）。"""

import copy

import pytest

from analysis.synthetic_circuits import logic, logic_eval
from services import circuit_edit as E
from services import circuit_model
from services.circuit_model import build_circuit
from tests.test_circuit_api import _save
from tests.test_circuit_model import self_holding_page


@pytest.fixture(autouse=True)
def builtin(monkeypatch):
    monkeypatch.setattr(circuit_model, "ngspice_path", lambda: None)


@pytest.fixture
def pages():
    return [self_holding_page()]


def test_tool_definitions_are_valid_json_schema():
    tools = E.tool_definitions()
    assert [t["name"] for t in tools] == ["get_circuit_context", "preview_circuit_edit"]
    ops = tools[1]["input_schema"]["properties"]["ops"]["items"]["oneOf"]
    assert {o["properties"]["op"]["const"] for o in ops} == set(E.OPS)
    jsonschema = pytest.importorskip("jsonschema")
    jsonschema.Draft202012Validator.check_schema(tools[1]["input_schema"])
    jsonschema.validate({"project_ids": [1], "title": "t", "ops": [{"op": "set_label", "project_id": 1, "symbol_ref": "X",
                                                                    "label": "Y"}]}, tools[1]["input_schema"])


def test_insert_interlock_changes_behaviour_and_reports_impact(pages):
    ops = [{"op": "insert_in_series", "project_id": 1, "from": {"symbol_ref": "STOP", "terminal": "1"},
            "to": {"symbol_ref": "P"}, "component": {"class_key": "contact_b", "label": "EMG"}}]
    r = E.preview(pages, ops)
    assert r["ok"], r.get("errors")
    assert r["circuit"]["inputs_added"] == ["EMG"]
    assert "+test:EDIT-0001 b接点 EMG" in r["netlist_diff"] or "EDIT-0001 b接点 EMG" in r["netlist_diff"]
    changed = [b for b in r["behaviour"] if "EMG=ON" in b["inputs"]]
    assert changed and any(c["device"] == "X R100" and c["before"] and not c["after"] for b in changed for c in b["coils"])
    assert any(i["device"] == "EMG" and "X R100" in i["devices"] for i in r["impact"])
    # 元の図面は変わらない
    assert all(s["ref"] != "EDIT-0001" for s in pages[0]["symbols"])


def test_invalid_ops_change_nothing_and_say_why(pages):
    r = E.preview(pages, [{"op": "set_label", "project_id": 1, "symbol_ref": "XA", "label": "Y"},
                          {"op": "connect", "project_id": 1, "from": {"symbol_ref": "XA"}, "to": {"symbol_ref": "NOPE"}},
                          {"op": "explode", "project_id": 1},
                          {"op": "set_class", "project_id": 1, "symbol_ref": "P", "class_key": "contact_a"}])
    assert not r["ok"]
    assert [e["index"] for e in r["errors"]] == [1, 2, 3]
    assert "端子" in r["errors"][0]["message"]
    with pytest.raises(E.EditError):
        E.apply_ops(pages, [{"op": "remove_component", "project_id": 2, "symbol_ref": "XA"}])


def test_each_operation(pages):
    ops = [
        {"op": "set_class", "project_id": 1, "symbol_ref": "XL", "class_key": "contact_b"},
        {"op": "set_attribute", "project_id": 1, "symbol_ref": "XC", "key": "timer_mode", "value": "on_delay"},
        {"op": "set_attribute", "project_id": 1, "symbol_ref": "XC", "key": "delay_s", "value": 3},
        {"op": "add_component", "project_id": 1, "component": {"class_key": "lamp", "label": "RL2", "ref": "L2"}},
        {"op": "connect", "project_id": 1, "from": {"symbol_ref": "XL", "terminal": "2"}, "to": {"symbol_ref": "L2", "terminal": "1"},
         "wire_no": "700"},
        {"op": "connect", "project_id": 1, "from": {"symbol_ref": "L2", "terminal": "2"}, "to": {"symbol_ref": "N"}},
        {"op": "set_wire_no", "project_id": 1, "from": {"symbol_ref": "XL", "terminal": "2"},
         "to": {"symbol_ref": "L2", "terminal": "1"}, "wire_no": "701"},
        {"op": "disconnect", "project_id": 1, "from": {"symbol_ref": "XL", "terminal": "2"}, "to": {"symbol_ref": "L", "terminal": "1"}},
        {"op": "remove_component", "project_id": 1, "symbol_ref": "L"},
        {"op": "set_label", "project_id": 1, "symbol_ref": "PB1", "label": "START"},
    ]
    after, notes = E.apply_ops(pages, ops)
    assert len(notes) == len(ops)
    circuit = build_circuit(after)
    assert circuit.timers == {"X R100": {"mode": "on_delay", "delay_s": 3.0}}
    assert "START" in circuit.inputs and "PB1" not in circuit.inputs
    assert any(c.get("wire_no") == "701" for c in after[0]["connections"])
    r = E.preview(pages, ops)
    assert r["circuit"]["timers_changed"] == ["X R100"]
    assert r["circuit"]["inputs_added"] == ["START"]


def test_edit_matches_the_truth_of_the_edited_spec():
    """合成回路の正本を変え（段にインターロックを足す・接点の種類を変える）、同じ変更を操作で図面に当てると、
    変更後の図面のシミュレーションが変えた正本の正解と一致する。"""
    for seed in range(6):
        result = logic_eval.edit_roundtrip(seed)
        assert result["agree"] == result["total"] > 0, (seed, result)


def test_consistency_fix_as_operation():
    spec = logic.make_spec(0)
    data = logic.build(spec)
    contact = next(s for p in data["pages"] for s in p["symbols"] if s["class_key"] == "contact_a" and "H" in (s["label"] or ""))
    contact["class_key"] = "contact_b"
    from services import consistency

    issues = consistency.check(copy.deepcopy(data["pages"]), data["structures"], bus_texts=data["bus_texts"], solvers=False)["issues"]
    ops = [E.fix_to_op(i["fix"]) for i in issues if i["fix"]]
    r = E.preview(data["pages"], ops, structures=data["structures"], bus_texts=data["bus_texts"])
    assert r["ok"] and r["validation"]["issues_resolved"] and not r["validation"]["issues_added"]


def test_edit_api_preview_apply_history_undo(client):
    pid = _save(client, self_holding_page())
    ctx = client.post("/api/circuit/edit/context", json={"project_ids": [pid]}).json()
    assert ctx["relays"] == ["X R100"] and "c.coil(" in ctx["code"]
    ops = [{"op": "insert_in_series", "project_id": pid, "from": {"symbol_ref": "STOP", "terminal": "1"},
            "to": {"symbol_ref": "P"}, "component": {"class_key": "contact_b", "label": "EMG"}}]
    body = {"project_ids": [pid], "ops": ops, "title": "非常停止を足す", "author": "llm", "reason": "テスト"}
    pre = client.post("/api/circuit/edit/preview", json=body).json()
    assert pre["ok"] and pre["base_hash"] == ctx["base_hash"]
    applied = client.post("/api/circuit/edit/apply", json={**body, "base_hash": pre["base_hash"]})
    assert applied.status_code == 200, applied.text
    cs = applied.json()["change_set"]
    assert cs["author"] == "llm" and cs["status"] == "applied"
    summary = client.post("/api/circuit/build", json={"project_ids": [pid]}).json()
    assert {i["device"] for i in summary["inputs"]} == {"EMG", "PB1", "STOP"}
    # 古いハッシュで適用すると 409
    assert client.post("/api/circuit/edit/apply", json={**body, "base_hash": pre["base_hash"]}).status_code == 409
    bad = client.post("/api/circuit/edit/apply", json={**body, "ops": [{"op": "set_label", "project_id": pid,
                                                                        "symbol_ref": "NOPE", "label": "x"}]})
    assert bad.status_code == 422
    history = client.get(f"/api/circuit/edit/history?project_ids={pid}").json()["change_sets"]
    assert [h["title"] for h in history] == ["非常停止を足す"]
    undone = client.post(f"/api/circuit/edit/{cs['id']}/undo")
    assert undone.status_code == 200 and undone.json()["status"] == "undone"
    summary = client.post("/api/circuit/build", json={"project_ids": [pid]}).json()
    assert {i["device"] for i in summary["inputs"]} == {"PB1", "STOP"}
    assert client.post(f"/api/circuit/edit/{cs['id']}/undo").status_code == 409
    tools = client.get("/api/circuit/edit/tools").json()
    assert {t["name"] for t in tools["tools"]} == {"get_circuit_context", "preview_circuit_edit"}


def test_undo_refuses_when_drawing_changed_later(client):
    pid = _save(client, self_holding_page())
    first = client.post("/api/circuit/edit/apply", json={"project_ids": [pid], "ops": [
        {"op": "set_label", "project_id": pid, "symbol_ref": "PB1", "label": "START"}]}).json()["change_set"]
    client.post("/api/circuit/edit/apply", json={"project_ids": [pid], "ops": [
        {"op": "set_class", "project_id": pid, "symbol_ref": "XL", "class_key": "contact_b"}]})
    assert client.post(f"/api/circuit/edit/{first['id']}/undo").status_code == 409
    assert client.post(f"/api/circuit/edit/{first['id']}/undo", json={"force": True}).status_code == 200
