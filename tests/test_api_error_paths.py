"""API の失敗の返し方と、画面からはまだ通らない経路（クラスの登録・まとめて削除・画像の欠け）。"""

from __future__ import annotations

import pytest

from routers import annotations as annotations_router
from services import vector_pdf
from services.annotation_service import image_path, thumbnail_path
from tests.conftest import create_project, make_pdf

MISSING = {"project_ids": [999999]}


@pytest.mark.parametrize(("path", "body"), [
    ("/api/circuit/simulate", MISSING),
    ("/api/circuit/netlist", MISSING),
    ("/api/circuit/lcapy", MISSING),
    ("/api/circuit/code", MISSING),
    ("/api/circuit/what-if", MISSING),
    ("/api/circuit/sequence", {**MISSING, "steps": [{"inputs": {}}]}),
    ("/api/circuit/trace/targets", MISSING),
    ("/api/circuit/consistency", MISSING),
    ("/api/circuit/consistency/apply", {**MISSING, "issue_ids": ["x"]}),
    ("/api/circuit/edit/context", MISSING),
    ("/api/circuit/edit/preview", MISSING),
    ("/api/circuit/edit/apply", MISSING),
])
def test_circuit_api_answers_400_for_drawings_that_do_not_exist(client, path, body):
    res = client.post(path, json=body)
    assert res.status_code == 400 and res.json()["detail"] == "図面を選んでください"


def test_circuit_api_other_errors(client):
    assert client.post("/api/circuit/edit/999999/undo", json={}).status_code == 404
    assert client.get("/api/circuit/devices.xlsx?project_ids=999999").status_code == 400
    bad = client.post("/api/circuit/split-coil-wires", json={
        "symbols": [{"ref": "C"}], "connections": [], "image_width": 10, "image_height": 10})
    assert bad.status_code == 400 and "形が正しくありません" in bad.json()["detail"]


def test_symbol_classes_can_be_added_and_renamed(client):
    created = client.post("/api/classes", json={"key": "breaker", "label": "ブレーカー", "sort_order": 50})
    assert created.status_code == 200, created.text
    cid = created.json()["id"]
    assert client.post("/api/classes", json={"key": "breaker", "label": "x"}).status_code == 400
    renamed = client.put(f"/api/classes/{cid}", json={"key": "mccb", "label": "配線用遮断器", "is_active": False})
    assert renamed.json()["key"] == "mccb"
    row = next(c for c in client.get("/api/classes").json() if c["id"] == cid)
    assert (row["label"], row["is_active"]) == ("配線用遮断器", False)
    assert client.put(f"/api/classes/{cid}", json={"key": "relay_coil", "label": "x"}).status_code == 400
    assert client.put("/api/classes/999999", json={"key": "k", "label": "x"}).status_code == 404


def test_bulk_delete_removes_drawings(client):
    a, b = create_project(client, "a.png"), create_project(client, "b.png")
    assert client.post("/api/projects/bulk-delete", json={"ids": []}).status_code == 400
    res = client.post("/api/projects/bulk-delete", json={"ids": [a, b, 999999]})
    assert sorted(res.json()["deleted"]) == sorted([a, b])
    assert client.get(f"/api/projects/{a}").status_code == 404


def test_project_list_by_id_and_empty_upload(client):
    a = create_project(client, "a.png")
    create_project(client, "b.png")
    assert [p["id"] for p in client.get(f"/api/projects?q={a}").json()] == [a]
    assert client.post("/api/projects", files=[]).status_code == 422
    assert client.post("/api/export", json={"ids": [999999]}).status_code == 404
    assert client.post("/api/import", files={"archive": ("x.zip", b"", "application/zip")}).status_code == 400


def test_missing_images_and_thumbnails(client, monkeypatch):
    pid = create_project(client)
    sha = client.get(f"/api/projects/{pid}").json()["images"][0]["sha256"]
    monkeypatch.setattr(annotations_router, "ensure_thumbnail", lambda sha256: (_ for _ in ()).throw(OSError("disk")))
    assert client.get(f"/api/projects/{pid}/thumbnail").status_code == 500
    thumbnail_path(sha).unlink(missing_ok=True)
    image_path(sha).unlink()
    assert client.get(f"/api/projects/{pid}/image").status_code == 404
    assert client.get(f"/api/projects/{pid}/thumbnail").status_code == 404
    assert client.get(f"/api/projects/{pid}/vector-pdf").status_code == 404


def test_drawing_without_image(client):
    from database import SessionLocal
    from models import AnnotationImage

    pid = create_project(client)
    db = SessionLocal()
    db.query(AnnotationImage).filter(AnnotationImage.project_id == pid).delete()
    db.commit()
    db.close()
    assert client.get(f"/api/projects/{pid}/image").status_code == 404
    assert client.get(f"/api/projects/{pid}/thumbnail").status_code == 404


def test_sheet_number_reader_failure_is_a_500(client, monkeypatch):
    pid = create_project(client)
    monkeypatch.setattr(annotations_router, "suggest_sheet_numbers", lambda path: 1 / 0)
    res = client.post(f"/api/projects/{pid}/sheet-number/suggest")
    assert res.status_code == 500 and "手入力" in res.json()["detail"]


def test_turned_pages_are_reported_and_broken_vector_data_does_not_stop_registration(client, monkeypatch):
    monkeypatch.setattr(annotations_router, "upright_page", lambda png: (png, 90))
    monkeypatch.setattr(vector_pdf, "extract_page", lambda page, turn=0: 1 / 0)
    res = client.post("/api/projects", files={"files": ("p.pdf", make_pdf(1), "application/pdf")})
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["turned"] == [{"project_id": body["project_ids"][0], "degrees": 90}]


def test_vector_pages_of_an_unreadable_pdf_are_empty():
    assert annotations_router._vector_pages(b"not a pdf") == []


def test_trace_needs_a_start_or_a_goal(client):
    from tests.test_circuit_api import _save
    from tests.test_circuit_model import self_holding_page

    pid = _save(client, self_holding_page())
    forward = client.post("/api/circuit/trace", json={"project_ids": [pid], "mode": "forward"})
    assert forward.status_code == 400 and "順引き" in forward.json()["detail"]
    backward = client.post("/api/circuit/trace", json={"project_ids": [pid]})
    assert backward.status_code == 400 and "逆引き" in backward.json()["detail"]
