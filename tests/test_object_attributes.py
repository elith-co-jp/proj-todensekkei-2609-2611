"""記号・配線の属性と、オブジェクト ID と 1 対 1 の JSON（DD-0010）。"""

from __future__ import annotations

import io
import json

from sqlalchemy import text

import database
from services.circuit_model import build_circuit, netlist
from tests.conftest import create_project

ANNOTATION = {
    "symbols": [
        {"ref": "SYM-0001", "class_key": "relay_coil", "label": "33HB FV2306", "cx": 0.3, "cy": 0.5, "w": 0.05, "h": 0.05,
         "attributes": {"maker": "オムロン", "coil_resistance_ohm": 4400, "location": ""},
         "terminals": [{"ref": "SYM-0001-T1", "name": "13", "tx": 0.3, "ty": 0.47},
                       {"ref": "SYM-0001-T2", "name": "14", "tx": 0.3, "ty": 0.53}]},
        {"ref": "SYM-0002", "class_key": "connector", "label": "CN1 7", "cx": 0.3, "cy": 0.2, "w": 0.03, "h": 0.03,
         "terminals": [{"ref": "SYM-0002-T1", "name": "CN1-7", "tx": 0.3, "ty": 0.2}]},
    ],
    "connections": [
        {"from_symbol_ref": "SYM-0002", "from_terminal_ref": "SYM-0002-T1", "to_symbol_ref": "SYM-0001",
         "to_terminal_ref": "SYM-0001-T1", "attributes": {"length_m": 12.5, "wire_type": "CVV", "color": None}},
        {"from_symbol_ref": "SYM-0001", "from_terminal_ref": "SYM-0001-T2", "to_symbol_ref": "SYM-0002",
         "to_terminal_ref": "SYM-0002-T1"},
    ],
}


def _project(client, sheet="ER21216"):
    pid = create_project(client, f"{sheet}.png")
    assert client.put(f"/api/projects/{pid}/meta", json={"sheet_no": sheet}).status_code == 200
    assert client.put(f"/api/projects/{pid}/annotations", json=ANNOTATION).status_code == 200
    return pid


def test_attributes_and_wire_refs_survive_saving_again(client):
    pid = _project(client)
    detail = client.get(f"/api/projects/{pid}").json()
    coil = detail["symbols"][0]
    assert coil["attributes"] == {"maker": "オムロン", "coil_resistance_ohm": 4400}  # 空の値は保存しない
    assert [c["ref"] for c in detail["connections"]] == ["W-0001", "W-0002"]
    assert detail["connections"][0]["attributes"] == {"length_m": 12.5, "wire_type": "CVV"}

    # エディタは読み込んだ内容を送り返す。配線の順が変わっても参照名と属性は配線について回る。
    again = {"symbols": detail["symbols"], "connections": detail["connections"][::-1]}
    assert client.put(f"/api/projects/{pid}/annotations", json=again).status_code == 200
    wires = {c["ref"]: c for c in client.get(f"/api/projects/{pid}").json()["connections"]}
    assert wires["W-0001"]["attributes"]["length_m"] == 12.5 and wires["W-0002"]["attributes"] == {}


def test_attribute_values_are_validated(client):
    pid = create_project(client)
    bad = json.loads(json.dumps(ANNOTATION))
    bad["symbols"][0]["attributes"] = {"x" * 65: 1}
    assert client.put(f"/api/projects/{pid}/annotations", json=bad).status_code == 422
    bad["symbols"][0]["attributes"] = {"maker": ["a", "b"]}
    assert client.put(f"/api/projects/{pid}/annotations", json=bad).status_code == 422


def _export(client, pids, template=False):
    res = client.post("/api/attributes/export", json={"project_ids": pids, "template": template})
    assert res.status_code == 200 and "attachment" in res.headers["content-disposition"]
    return res.json()


def test_export_lists_every_object_once_with_template_fields(client):
    pid = _project(client)
    doc = _export(client, [pid])
    assert doc["schema"] == "todenyolo.object_attributes.v1"
    ids = [o["object_id"] for o in doc["objects"]]
    assert ids == ["ER21216/SYM-0001", "ER21216/SYM-0002", "ER21216/W-0001", "ER21216/W-0002"]
    assert doc["objects"][2]["from"] == "SYM-0002/SYM-0002-T1" and doc["objects"][2]["attributes"]["length_m"] == 12.5

    template = _export(client, [pid], template=True)
    coil, connector, wire = template["objects"][0], template["objects"][1], template["objects"][3]
    assert coil["attributes"]["maker"] == "オムロン" and coil["attributes"]["pickup_voltage_v"] is None
    assert "pin_count" in connector["attributes"] and "coil_resistance_ohm" not in connector["attributes"]
    assert set(wire["attributes"]) >= {"length_m", "wire_type", "cross_section_mm2", "color", "cable_no"}


def _import(client, doc):
    raw = json.dumps(doc, ensure_ascii=False).encode()
    return client.post("/api/attributes/import", files={"file": ("a.json", io.BytesIO(raw), "application/json")})


def test_import_replaces_listed_objects_and_reports_problems(client):
    pid = _project(client)
    doc = _export(client, [pid], template=True)
    objects = {o["object_id"]: o for o in doc["objects"]}
    objects["ER21216/W-0002"]["attributes"].update({"length_m": "3,000", "color": "赤"})
    objects["ER21216/SYM-0001"]["attributes"].update({"maker": None, "pickup_voltage_v": "abc"})
    doc["objects"].append({**objects["ER21216/SYM-0002"], "object_id": "ER21216/SYM-0002"})  # 重複
    doc["objects"].append({"object_id": "ER21216/SYM-0099", "project_id": pid, "sheet_no": "ER21216", "type": "symbol",
                           "ref": "SYM-0099", "attributes": {}})
    doc["objects"].append({"object_id": "ER21216/W-0001x", "project_id": pid, "sheet_no": "ER21216", "type": "wire",
                           "ref": "SYM-0001", "attributes": {}})
    report = _import(client, doc).json()
    assert report["duplicates"] == ["ER21216/SYM-0002"]
    assert report["not_found"] == ["ER21216/SYM-0099"] and report["type_mismatch"] == ["ER21216/W-0001x"]
    assert report["invalid"] == ["ER21216/SYM-0001 pickup_voltage_v: 数値ではありません（abc）"]
    assert report["updated"] == 2 and report["unchanged"] == 1

    detail = client.get(f"/api/projects/{pid}").json()
    assert detail["symbols"][0]["attributes"] == {"coil_resistance_ohm": 4400}  # null は未入力として消える
    wires = {c["ref"]: c["attributes"] for c in detail["connections"]}
    assert wires["W-0002"] == {"length_m": 3000.0, "color": "赤"}
    assert _import(client, doc).json()["updated"] == 0  # 同じファイルをもう一度取り込んでも変わらない


def test_import_matches_by_sheet_number_in_another_environment(client):
    pid = _project(client, "ER21302")
    doc = _export(client, [pid])
    for o in doc["objects"]:
        o["project_id"] = 999  # 別の環境の図面 ID
    doc["objects"][0]["attributes"] = {"maker": "富士電機"}
    assert _import(client, doc).json()["updated"] == 1
    assert client.get(f"/api/projects/{pid}").json()["symbols"][0]["attributes"] == {"maker": "富士電機"}
    assert _import(client, {"schema": "other", "objects": []}).status_code == 400


def test_zip_round_trip_keeps_attributes_and_wire_refs(client):
    pid = _project(client)
    blob = client.post("/api/export", json={"ids": [pid]}).content
    res = client.post("/api/import", files={"archive": ("x.zip", io.BytesIO(blob), "application/zip")})
    assert res.status_code == 200, res.text
    restored = max(p["id"] for p in client.get("/api/projects").json())
    detail = client.get(f"/api/projects/{restored}").json()
    assert detail["symbols"][0]["attributes"]["coil_resistance_ohm"] == 4400
    assert [c["ref"] for c in detail["connections"]] == ["W-0001", "W-0002"]


def test_circuit_uses_coil_resistance_attribute(client):
    pid = _project(client)
    page = client.get(f"/api/projects/{pid}").json()
    circuit = build_circuit([page])
    assert circuit.elements[0].resistance == 4400
    assert " 4400\n" in netlist(circuit, {})


def test_existing_db_upgrade_adds_attribute_columns(client):
    with database.engine.begin() as conn:
        conn.execute(text("ALTER TABLE connections DROP COLUMN attributes_json"))
        conn.execute(text("ALTER TABLE connections DROP COLUMN ref"))
        conn.execute(text("ALTER TABLE annotation_symbols DROP COLUMN attributes_json"))
    database._migrate_columns()
    pid = _project(client)
    assert client.get(f"/api/projects/{pid}").json()["connections"][0]["ref"] == "W-0001"
