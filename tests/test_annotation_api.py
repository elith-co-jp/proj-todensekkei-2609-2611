from __future__ import annotations

from tests.conftest import SAMPLE_ANNOTATION, create_project, make_pdf


def test_health(client):
    assert client.get("/api/health").json() == {"status": "ok"}


def test_default_classes_have_unique_yolo_index(client):
    classes = client.get("/api/classes").json()
    assert len(classes) == 12
    idx = [c["yolo_index"] for c in classes]
    assert idx == sorted(idx)
    assert len(set(idx)) == len(idx)
    assert classes[0]["key"] == "relay_coil"


def test_create_project_and_fetch_image(client):
    pid = create_project(client, "ER21216.png", 640, 480)
    detail = client.get(f"/api/projects/{pid}").json()
    assert detail["image_width"] == 640
    assert detail["image_height"] == 480
    assert detail["symbols"] == []
    img = client.get(f"/api/projects/{pid}/image")
    assert img.status_code == 200
    assert img.headers["content-type"] == "image/png"


def test_upload_pdf_creates_one_annotation_project_per_page(client):
    res = client.post(
        "/api/projects",
        files={"files": ("検証図面.pdf", make_pdf(page_count=2), "application/pdf")},
    )

    assert res.status_code == 200, res.text
    body = res.json()
    assert body["count"] == 2
    assert len(body["project_ids"]) == 2

    rows = sorted(client.get("/api/projects").json(), key=lambda row: row["page_no"])
    assert [row["page_no"] for row in rows] == ["1", "2"]
    assert [row["name"] for row in rows] == ["検証図面 - 001", "検証図面 - 002"]
    assert all(row["image_width"] > 0 and row["image_height"] > 0 for row in rows)

    for project_id in body["project_ids"]:
        image = client.get(f"/api/projects/{project_id}/image")
        assert image.status_code == 200
        assert image.headers["content-type"] == "image/png"


def test_upload_rejects_broken_pdf_without_creating_projects(client):
    res = client.post(
        "/api/projects",
        files={"files": ("broken.pdf", b"%PDF-1.7\nnot-a-pdf", "application/pdf")},
    )

    assert res.status_code == 400
    assert "PDF" in res.json()["detail"]
    assert client.get("/api/projects").json() == []


def test_put_annotations_roundtrip(client):
    pid = create_project(client)
    res = client.put(f"/api/projects/{pid}/annotations", json=SAMPLE_ANNOTATION)
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["symbol_count"] == 3
    assert body["connection_count"] == 2
    assert body["skipped"] == []

    detail = client.get(f"/api/projects/{pid}").json()
    refs = [s["ref"] for s in detail["symbols"]]
    assert refs == ["SYM-0001", "SYM-0002", "SYM-0003"]
    coil = detail["symbols"][0]
    assert coil["class_key"] == "relay_coil"
    assert coil["label"] == "33HB"
    assert [t["name"] for t in coil["terminals"]] == ["13", "14"]

    c0 = detail["connections"][0]
    assert c0["from_symbol_ref"] == "SYM-0001"
    assert c0["from_terminal_ref"] == "SYM-0001-T2"
    assert c0["to_symbol_ref"] == "SYM-0002"
    assert c0["to_terminal_ref"] == "SYM-0002-T1"
    assert c0["wire_no"] == "610"
    c1 = detail["connections"][1]
    assert c1["kind"] == "sheet_ref"
    assert c1["external_ref"] == "(610-2F-9)"
    assert c1["from_terminal_ref"] is None


def test_put_annotations_is_full_replace(client):
    pid = create_project(client)
    client.put(f"/api/projects/{pid}/annotations", json=SAMPLE_ANNOTATION)
    res = client.put(
        f"/api/projects/{pid}/annotations",
        json={"symbols": [{"class_key": "diode", "cx": 0.1, "cy": 0.1, "w": 0.05, "h": 0.05}], "connections": []},
    )
    assert res.json()["symbol_count"] == 1
    assert res.json()["connection_count"] == 0
    detail = client.get(f"/api/projects/{pid}").json()
    assert detail["symbols"][0]["ref"] == "SYM-0001"  # ref 未指定なら自動採番
    assert detail["symbols"][0]["class_key"] == "diode"


def test_connection_to_unknown_symbol_is_skipped(client):
    pid = create_project(client)
    res = client.put(
        f"/api/projects/{pid}/annotations",
        json={
            "symbols": [{"ref": "A", "class_key": "terminal", "cx": 0.1, "cy": 0.1, "w": 0.02, "h": 0.02}],
            "connections": [{"from_symbol_ref": "A", "to_symbol_ref": "MISSING"}],
        },
    )
    assert res.json()["connection_count"] == 0
    assert res.json()["skipped"] == ["A -> MISSING"]


def test_unknown_class_key_is_auto_registered(client):
    pid = create_project(client)
    client.put(
        f"/api/projects/{pid}/annotations",
        json={"symbols": [{"class_key": "timer_relay", "cx": 0.5, "cy": 0.5, "w": 0.1, "h": 0.1}], "connections": []},
    )
    keys = [c["key"] for c in client.get("/api/classes").json()]
    assert "timer_relay" in keys
    detail = client.get(f"/api/projects/{pid}").json()
    assert detail["symbols"][0]["class_key"] == "timer_relay"


def test_out_of_range_box_is_clamped(client):
    pid = create_project(client)
    client.put(
        f"/api/projects/{pid}/annotations",
        json={"symbols": [{"class_key": "other", "cx": 1.8, "cy": -0.4, "w": 3.0, "h": 0.0}], "connections": []},
    )
    s = client.get(f"/api/projects/{pid}").json()["symbols"][0]
    assert s["cx"] == 1.0 and s["cy"] == 0.0
    assert s["w"] == 1.0 and 0 < s["h"] <= 1.0


def test_meta_update_and_list(client):
    pid = create_project(client)
    client.put(
        f"/api/projects/{pid}/meta",
        json={"sheet_no": "ER21216", "page_no": "133", "revision": "0", "status": "review", "assignee": "東電設計"},
    )
    rows = client.get("/api/projects?q=ER21216").json()
    assert len(rows) == 1
    assert rows[0]["sheet_no"] == "ER21216"
    assert rows[0]["status"] == "review"


def test_stats_and_delete(client):
    pid = create_project(client)
    client.put(f"/api/projects/{pid}/annotations", json=SAMPLE_ANNOTATION)
    st = client.get("/api/stats").json()
    assert st["project_count"] == 1
    assert st["symbol_count"] == 3
    assert st["connection_count"] == 2
    assert st["by_class"]["relay_coil"] == 1

    assert client.delete(f"/api/projects/{pid}").status_code == 200
    assert client.get(f"/api/projects/{pid}").status_code == 404
    assert client.get("/api/stats").json()["symbol_count"] == 0


def test_missing_project_returns_japanese_error(client):
    res = client.get("/api/projects/9999")
    assert res.status_code == 404
    assert res.json()["detail"] == "アノテーションプロジェクトが見つかりません"
