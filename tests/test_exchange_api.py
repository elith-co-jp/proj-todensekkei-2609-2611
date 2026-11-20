"""図面を読む手順の API と、正解のやり取り（対応表・代表回路・CVAT）の API（DD-0035）。"""

import pytest

from services import annotation_table, circuit_model, cvat_format
from tests.test_circuit_api import _save
from tests.test_circuit_model import self_holding_page

X = "X R100"


@pytest.fixture(autouse=True)
def builtin(monkeypatch):
    monkeypatch.setattr(circuit_model, "ngspice_path", lambda: None)


def test_trace_backward_forward_and_targets(client):
    pid = _save(client, self_holding_page())
    r = client.post("/api/circuit/trace", json={"project_ids": [pid], "target": X,
                                                "states": {"STOP": False, "PB1": None}})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "判定不能" and "STOP" in body["formula"]
    assert any("PB1" in reason for reason in body["reasons"])
    assert body["explain"]["inputs"]
    ok = client.post("/api/circuit/trace", json={"project_ids": [pid], "target": X,
                                                 "states": {"STOP": False, "PB1": True}}).json()
    assert ok["status"] == "成立" and ok["branches"][0]["contacts"][0]["page"]
    fwd = client.post("/api/circuit/trace", json={"project_ids": [pid], "mode": "forward", "device": "STOP"}).json()
    assert next(a["target"] for a in fwd["affected"]) == X
    targets = client.post("/api/circuit/trace/targets", json={"project_ids": [pid]}).json()
    assert {d["device"] for d in targets["devices"]} == {"PB1", "STOP", X}
    bad = client.post("/api/circuit/trace", json={"project_ids": [pid], "target": "NOPE"})
    assert bad.status_code == 400


def test_scenarios_api_with_yaml(client):
    pid = _save(client, self_holding_page())
    text = ("scenarios:\n  - id: S1\n    target: X\n    condition: '!STOP & (PB1 | X)'\n"
            "    cases:\n      - states: {STOP: false, PB1: true}\n        expected: 成立\n")
    r = client.post("/api/exchange/scenarios/evaluate", json={"project_ids": [pid], "text": text})
    assert r.status_code == 200, r.text
    assert r.json()["passed"] == 1


def test_table_export_and_compare(client):
    pid = _save(client, self_holding_page())
    r = client.get(f"/api/exchange/table?project_ids={pid}&drawing_set=DS01")
    assert r.status_code == 200 and r.content.startswith(b"PK")
    rows = annotation_table.read_table(r.content, "t.xlsx")["対応表"]
    assert rows and rows[0]["図面セットID"] == "DS01"
    wire = next(x for x in rows if x["種別"] == "配線")
    wire["判定"] = "非接続"
    blob = annotation_table.to_csv(rows)
    cmp_ = client.post("/api/exchange/table/compare", data={"project_ids": str(pid)},
                       files={"file": ("gold.csv", blob, "text/csv")})
    assert cmp_.status_code == 200, cmp_.text
    assert cmp_.json()["relations"]["配線"]["wrong_connections"] == 1
    assert client.get(f"/api/exchange/table?project_ids={pid}&format=json").json()["columns"][0] == "図面セットID"


def test_cvat_round_trip(client):
    pid = _save(client, self_holding_page())
    xml = client.get(f"/api/exchange/cvat?project_ids={pid}")
    assert xml.status_code == 200 and b"<annotations>" in xml.content
    before = client.get(f"/api/projects/{pid}").json()
    preview = client.post("/api/exchange/cvat/import", files={"file": ("a.xml", xml.content, "application/xml")}).json()
    assert preview["images"][0]["project_id"] == pid and not preview["applied"]
    assert preview["images"][0]["symbols"] == len(before["symbols"])
    applied = client.post("/api/exchange/cvat/import", data={"apply": "true"},
                          files={"file": ("a.xml", xml.content, "application/xml")}).json()
    assert applied["images"][0]["applied"]
    after = client.get(f"/api/projects/{pid}").json()
    assert len(after["connections"]) == len(before["connections"])
    assert {s["ref"]: s["label"] for s in after["symbols"]} == {s["ref"]: s["label"] for s in before["symbols"]}
    sim = client.post("/api/circuit/simulate", json={"project_ids": [pid], "inputs": {"PB1": True}}).json()
    assert sim["coil_states"][X]["energized"] is True


def test_cvat_polyline_snaps_to_terminals_and_skips_non_connections():
    xml = """<annotations><version>1.1</version>
    <image id="0" name="drawing.png" width="100" height="100">
      <box label="contact_a" xtl="10" ytl="10" xbr="20" ybr="30" group_id="1"><attribute name="text">PB1</attribute></box>
      <points label="terminal" points="15,10" group_id="1"/>
      <points label="terminal" points="15,30" group_id="1"/>
      <box label="relay_coil" xtl="10" ytl="50" xbr="20" ybr="70"><attribute name="text">X</attribute></box>
      <points label="terminal" points="15,50"/>
      <polyline label="wire" points="15,31;15,40;16,49"/>
      <polyline label="wire" points="15,10;15,50"><attribute name="judge">非接続</attribute></polyline>
      <polyline label="wire" points="80,80;90,90"/>
    </image></annotations>"""
    page = cvat_format.parse_xml(xml)[0]
    assert len(page["symbols"]) == 2 and len(page["symbols"][1]["terminals"]) == 1
    assert len(page["connections"]) == 1
    c = page["connections"][0]
    assert (c["from_symbol_ref"], c["to_symbol_ref"]) == ("CV-0001", "CV-0002")
    assert any("端子がありません" in w for w in page["warnings"])
