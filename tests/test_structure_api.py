"""Structure predictions must never overwrite manual annotation data."""

from __future__ import annotations

import json
import sys
from types import SimpleNamespace

import pytest

import database
from models import StructurePrediction
from tests.conftest import SAMPLE_ANNOTATION, create_project
from tests.test_ml_cycle import _predictions_zip, _upload_model


def _fake_detector(monkeypatch, *, empty=False, xywhn=None):
    import services.inference_service as inference

    class Array:
        def __init__(self, values):
            self.values = values

        def tolist(self):
            return self.values

    rows = [] if empty else (xywhn or [[0.2, 0.5, 0.1, 0.1]])
    boxes = SimpleNamespace(
        xywhn=Array(rows), conf=Array([0.9] * len(rows)), cls=Array([0] * len(rows)),
    )
    detector = SimpleNamespace(names={0: "connector"}, predict=lambda *a, **k: [SimpleNamespace(boxes=boxes)])
    monkeypatch.setattr(inference, "ultralytics_available", lambda: True)
    monkeypatch.setitem(sys.modules, "ultralytics", SimpleNamespace(YOLO=lambda path: detector))


def _result(project_id):
    return {
        "status": "completed", "source": {"project_id": project_id},
        "image_size": {"width": 400, "height": 300},
        "symbols": [{"id": "symbol_001"}],
        "wires": [{"id": "wire_001", "polyline": [[80, 150], [200, 150]]}],
        "from_to": {"connection_count": 0, "connections": []}, "warnings": [],
    }


def test_structure_missing_and_export_rejects_missing_pages(client):
    project_id = create_project(client)
    assert client.get(f"/api/ml/projects/{project_id}/structure").json()["result"] is None
    assert client.get("/api/ml/projects/999/structure").status_code == 404
    assert client.post("/api/ml/analysis/export", json={"project_ids": [project_id]}).status_code == 409
    with database.SessionLocal() as db:
        db.add(StructurePrediction(project_id=project_id, payload_json=json.dumps(_result(project_id))))
        db.commit()
    missing = create_project(client, "second.png")
    assert client.post("/api/ml/analysis/export", json={"project_ids": [project_id, missing]}).status_code == 409
    assert client.post("/api/ml/analysis/export", json={"project_ids": [project_id, 999]}).status_code == 404
    result = client.post("/api/ml/analysis/export", json={"project_ids": [project_id]})
    assert result.status_code == 200
    assert result.headers["content-disposition"].endswith('"structure.json"')
    assert result.json()["pages"][0]["from_to"]["connections"] == []
    assert result.json()["summary"]["wire_count"] == 1


def test_export_links_sheet_references_to_the_exported_drawings(client):
    source, target = create_project(client), create_project(client, "second.png")
    assert client.put(f"/api/projects/{source}/meta", json={"sheet_no": "ER21216"}).status_code == 200
    refs = [
        {"target_sheet": "ER21212", "kind": "wire", "text": "(ER21212)"},
        {"target_sheet": "ER21212-1", "kind": "wire", "text": "(ER21212-1)"},
        {"target_sheet": "ER29999", "kind": "relay", "text": "(ER29999)"},
    ]
    with database.SessionLocal() as db:
        db.add(StructurePrediction(project_id=source, payload_json=json.dumps({**_result(source), "sheet_references": refs})))
        # シート番号が未入力の図面は、自動読取の 1 位で照合する
        db.add(StructurePrediction(project_id=target, payload_json=json.dumps(
            {**_result(target), "sheet_number_candidates": [{"text": "ER21212", "score": 0.99}]})))
        db.commit()

    exported = client.post("/api/ml/analysis/export", json={"project_ids": [source, target]}).json()
    linked = [r["target_project_id"] for r in exported["pages"][0]["sheet_references"]]
    assert linked == [target, target, None]
    assert exported["pages"][0]["sheet_no"] == "ER21216"
    assert (exported["summary"]["sheet_reference_count"], exported["summary"]["resolved_sheet_reference_count"]) == (3, 2)


def test_integrated_run_preserves_annotations_and_uses_detector_names(client, monkeypatch):
    from services import structure_service

    project_id = create_project(client)
    _upload_model(client)
    saved = client.put(f"/api/projects/{project_id}/annotations", json=SAMPLE_ANNOTATION)
    assert saved.status_code == 200
    before = client.get(f"/api/projects/{project_id}").json()
    _fake_detector(monkeypatch)
    seen = []

    def analyze(project, model, detections, regions=None):
        seen.extend(detections)
        return _result(project.id)

    monkeypatch.setattr(structure_service, "analyze_project", analyze)
    result = client.post("/api/ml/analysis/run", json={"project_ids": [project_id]})
    assert result.status_code == 200, result.text
    assert seen[0]["class_key"] == "connector"
    assert "terminals" not in seen[0] and "label" not in seen[0]
    assert result.json()["results"][0]["wire_count"] == 1
    after = client.get(f"/api/projects/{project_id}").json()
    assert before["symbols"] == after["symbols"]
    assert before["connections"] == after["connections"]
    assert client.get(f"/api/ml/projects/{project_id}/structure").json()["result"]["wires"]
    assert client.delete(f"/api/projects/{project_id}").status_code == 200
    with database.SessionLocal() as db:
        assert db.get(StructurePrediction, project_id) is None


@pytest.mark.parametrize("mode", ["inference", "import"])
def test_replacing_symbols_invalidates_old_structure(client, monkeypatch, mode):
    project_id = create_project(client)
    _upload_model(client)
    with database.SessionLocal() as db:
        db.add(StructurePrediction(project_id=project_id, payload_json=json.dumps(_result(project_id))))
        db.commit()
    _fake_detector(monkeypatch)
    if mode == "inference":
        res = client.post("/api/ml/inference/run", json={"project_ids": [project_id]})
    else:
        res = client.post("/api/ml/inference/import", files={
            "archive": ("new.zip", _predictions_zip(project_id, ["0 .5 .5 .1 .1 .9"]), "application/zip")
        })
    assert res.status_code == 200
    assert client.get(f"/api/ml/projects/{project_id}/structure").json()["result"] is None


def test_failed_batch_keeps_previous_predictions_and_structure(client, monkeypatch):
    from services import structure_service

    first, second = create_project(client), create_project(client, "second.png")
    _upload_model(client)
    _fake_detector(monkeypatch)
    with database.SessionLocal() as db:
        db.add(StructurePrediction(project_id=first, payload_json=json.dumps(_result(first))))
        db.commit()
    old = client.get(f"/api/ml/projects/{first}/structure").json()

    def fail_second(project, *_args):
        if project.id == second:
            raise RuntimeError("test failure")
        return {**_result(first), "wires": []}

    monkeypatch.setattr(structure_service, "analyze_project", fail_second)
    res = client.post("/api/ml/analysis/run", json={"project_ids": [first, second]})
    assert res.status_code == 400
    assert client.get(f"/api/ml/projects/{first}/structure").json() == old
    assert client.get(f"/api/ml/projects/{first}/predictions").json()["count"] == 0


def test_zero_symbols_still_saves_wire_result(client, monkeypatch):
    from services import structure_service

    project_id = create_project(client)
    _upload_model(client)
    _fake_detector(monkeypatch, empty=True)
    monkeypatch.setattr(structure_service, "analyze_project", lambda project, *_: {**_result(project.id), "symbols": []})
    res = client.post("/api/ml/analysis/run", json={"project_ids": [project_id]})
    assert res.status_code == 200
    assert res.json()["detection_count"] == 0
    assert res.json()["results"][0]["structure_available"]
    assert client.post("/api/ml/analysis/export", json={"project_ids": [project_id]}).json()["summary"]["wire_count"] == 1


def test_project_list_reports_analysis_counts_only_when_analyzed(client):
    analyzed, untouched = create_project(client), create_project(client, "second.png")
    result = {
        **_result(analyzed),
        "wires": [{"id": f"wire_{i:03d}"} for i in range(4)],
        "from_to": {"connection_count": 3, "connections": []},
    }
    with database.SessionLocal() as db:
        db.add(StructurePrediction(project_id=analyzed, payload_json=json.dumps(result)))
        db.commit()

    rows = {r["id"]: r for r in client.get("/api/projects").json()}
    assert (rows[analyzed]["analysis_wire_count"], rows[analyzed]["analysis_connection_count"]) == (4, 3)
    assert (rows[untouched]["analysis_wire_count"], rows[untouched]["analysis_connection_count"]) == (None, None)
