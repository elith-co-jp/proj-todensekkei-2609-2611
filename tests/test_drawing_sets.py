"""図面セット（DD-0041）: 名前を付けて残す・行先札の過不足・組の候補。"""

from services.sheet_links import page_references
from tests.test_sheet_links import receiver, sender


def _save_pages(client, *pages):
    from tests.test_circuit_api import _save

    ids = []
    for page in pages:
        pid = _save(client, page)
        assert client.put(f"/api/projects/{pid}/meta", json={"sheet_no": page["sheet_no"]}).status_code == 200
        ids.append(pid)
    return ids


def test_page_references_lists_tags_and_sheet_ref_wires():
    page = sender(tags=(("T1", "ER21212へ", None), ("T2", "(ER21300-2)", None)))
    page["connections"].append({"from_symbol_ref": "P", "to_symbol_ref": "T1", "kind": "sheet_ref",
                                "external_ref": "ER21400"})
    assert [(r["target"], r["source"]) for r in page_references(page)] == [
        ("ER21212", "symbol"), ("ER21300-2", "symbol"), ("ER21400", "connection")]


def test_crud_and_validation(client):
    a, b = _save_pages(client, sender(), receiver())
    res = client.post("/api/drawing-sets", json={"name": "DS01", "project_ids": [b, a]})
    assert res.status_code == 201, res.text
    created = res.json()
    assert created["project_ids"] == [a, b]
    assert client.post("/api/drawing-sets", json={"name": "DS01"}).status_code == 400  # 同じ名前
    assert client.post("/api/drawing-sets", json={"name": "X", "project_ids": [999]}).status_code == 404

    listed = client.get("/api/drawing-sets").json()
    assert [(s["name"], s["sheets"]) for s in listed] == [("DS01", ["ER21211", "ER21212"])]

    updated = client.put(f"/api/drawing-sets/{created['id']}", json={"name": "DS02", "project_ids": [a]}).json()
    assert (updated["name"], updated["project_ids"]) == ("DS02", [a])

    # 図面を消すとセットからも外れる
    assert client.delete(f"/api/projects/{a}").status_code in (200, 204)
    assert client.get(f"/api/drawing-sets/{created['id']}").json()["project_ids"] == []

    assert client.delete(f"/api/drawing-sets/{created['id']}").status_code == 204
    assert client.get(f"/api/drawing-sets/{created['id']}").status_code == 404


def test_coverage_reports_missing_sheets_with_candidates(client):
    a, b = _save_pages(client, sender(), receiver())
    only_sender = client.post("/api/drawing-sets", json={"name": "S", "project_ids": [a]}).json()
    cov = client.get(f"/api/drawing-sets/{only_sender['id']}/coverage").json()
    assert cov["complete"] is False
    [missing] = cov["missing_sheets"]
    assert missing["sheet"] == "ER21212"
    assert [c["project_id"] for c in missing["candidates"]] == [b]
    assert missing["referenced_by"][0]["symbol_ref"] == "T1"

    both = client.post("/api/drawing-sets", json={"name": "S2", "project_ids": [a, b]}).json()
    cov = client.get(f"/api/drawing-sets/{both['id']}/coverage").json()
    assert cov["complete"] is True and cov["missing_sheets"] == []

    # セットの図面で回路を組めば、行先札で図面をまたいでつながる
    on = client.post("/api/circuit/simulate", json={"project_ids": both["project_ids"], "inputs": {"PB1": True}}).json()
    assert on["coil_states"]["X1 R100"]["energized"] is True


def test_suggest_groups_sheets_linked_by_tags(client):
    a, b = _save_pages(client, sender(), receiver())
    lone, = _save_pages(client, sender(sheet="ER30000", tags=(("T1", "ER39999へ", None),)))
    groups = client.get("/api/drawing-sets/suggest").json()
    assert [(g["project_ids"], g["missing_sheets"]) for g in groups] == [([a, b], []), ([lone], ["ER39999"])]
    assert groups[0]["name"] == "ER21211"
