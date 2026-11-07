"""回路シミュレーション API（DD-0009）。"""

from services import circuit_model
from tests.conftest import create_project
from tests.test_circuit_model import self_holding_page


def _save(client, page):
    pid = create_project(client, "sheet.png", 1000, 1000)
    symbols = [{k: s[k] for k in ("ref", "class_key", "label", "cx", "cy", "w", "h")}
               | {"terminals": [{k: t[k] for k in ("ref", "name", "tx", "ty")} for t in s["terminals"]]}
               for s in page["symbols"]]
    for s in symbols:
        if s["class_key"] == "lamp":
            s["class_key"] = "solenoid"  # 正解のクラスマスタに表示灯が無い環境でも負荷として扱う
    res = client.put(f"/api/projects/{pid}/annotations", json={"symbols": symbols, "connections": page["connections"]})
    assert res.status_code == 200, res.text
    return pid


def test_build_and_simulate_saved_annotations(client, monkeypatch):
    monkeypatch.setattr(circuit_model, "ngspice_path", lambda: None)
    pid = _save(client, self_holding_page())

    built = client.post("/api/circuit/build", json={"project_ids": [pid]}).json()
    assert [i["device"] for i in built["inputs"]] == ["PB1", "STOP"]
    assert [c["device"] for c in built["coils"]] == ["X R100"]
    assert built["coils"][0]["contacts"] and built["engine"] == "builtin"

    on = client.post("/api/circuit/simulate", json={"project_ids": [pid], "inputs": {"PB1": True}}).json()
    assert on["coil_states"]["X R100"]["energized"] is True
    page = on["overlay"][0]
    assert page["project_id"] == pid and any(w["live"] for w in page["wires"]) and page["coils"][0]["energized"]
    held = client.post("/api/circuit/simulate",
                       json={"project_ids": [pid], "inputs": {}, "energized": {"X R100": True}}).json()
    assert held["coil_states"]["X R100"]["energized"] is True


def test_netlist_download_and_validation(client):
    pid = _save(client, self_holding_page())
    res = client.post("/api/circuit/netlist", json={"project_ids": [pid], "inputs": {"PB1": True}})
    assert res.status_code == 200 and "attachment" in res.headers["content-disposition"]
    assert ".param S_PB1=1" in res.text and "SW_A" in res.text
    assert client.post("/api/circuit/build", json={"project_ids": [9999]}).status_code == 400
    assert client.post("/api/circuit/build", json={"project_ids": []}).status_code == 422
