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


def _fake_detector(
    monkeypatch,
    *,
    empty=False,
    xywhn=None,
    confidences=None,
    class_indexes=None,
    names=None,
):
    import services.inference_service as inference

    class Array:
        def __init__(self, values):
            self.values = values

        def tolist(self):
            return self.values

    rows = [] if empty else (xywhn or [[0.2, 0.5, 0.1, 0.1]])
    boxes = SimpleNamespace(
        xywhn=Array(rows),
        conf=Array(confidences or [0.9] * len(rows)),
        cls=Array(class_indexes or [0] * len(rows)),
    )
    detector = SimpleNamespace(
        names=names or {0: "connector"},
        predict=lambda *a, **k: [SimpleNamespace(boxes=boxes)],
    )
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


def test_export_links_contacts_to_the_coil_and_relay_table_on_the_referenced_drawing(client):
    contact_page, coil_page = create_project(client), create_project(client, "second.png")
    # 電線番号がくっついたラベル（128 33HB）でも器具番号で照合する
    contact = {"id": "symbol_001", "class_name": "contact_a", "label": "128 33HB HV305 (ER21236)"}
    other = {"id": "symbol_002", "class_name": "contact_a", "label": "43U AMESD (ER29999)"}
    refs = [
        {"target_sheet": "ER21236", "kind": "relay", "symbol_id": "symbol_001", "text": "(ER21236)"},
        {"target_sheet": "ER29999", "kind": "relay", "symbol_id": "symbol_002", "text": "(ER29999)"},
    ]
    coils = [{"id": "symbol_007", "class_name": "relay_coil", "label": "33HB HV305"},
             {"id": "symbol_008", "class_name": "relay_coil", "label": "33LA HV305"}]
    rows = [{"device": "33LA HV305", "terminal": "5-9-1"}, {"device": "33HB HV305", "terminal": "5-9-1"}]
    with database.SessionLocal() as db:
        db.add(StructurePrediction(project_id=contact_page, payload_json=json.dumps(
            {**_result(contact_page), "symbols": [contact, other], "sheet_references": refs})))
        db.add(StructurePrediction(project_id=coil_page, payload_json=json.dumps({
            **_result(coil_page), "symbols": coils, "relay_table": {"rows": rows, "links": []},
            "sheet_number_candidates": [{"text": "ER21236", "score": 0.99}],
        })))
        db.commit()

    exported = client.post("/api/ml/analysis/export", json={"project_ids": [contact_page, coil_page]}).json()
    linked, unresolved = exported["pages"][0]["symbols"]
    assert linked["relay_link"] == {"project_id": coil_page, "coil_symbol_ids": ["symbol_007"], "relay_table_rows": [1]}
    assert "relay_link" not in unresolved
    assert (exported["summary"]["cross_sheet_relay_count"], exported["summary"]["linked_cross_sheet_relay_count"]) == (2, 1)


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


def test_connector_floor_filters_only_low_confidence_connector(client, monkeypatch):
    project_id = create_project(client)
    _upload_model(client)
    _fake_detector(monkeypatch, xywhn=[[0.2, 0.5, 0.1, 0.1], [0.8, 0.5, 0.1, 0.1]],
                   confidences=[0.55, 0.7])

    result = client.post("/api/ml/inference/run", json={
        "project_ids": [project_id], "conf": 0.5, "connector_conf": 0.6,
    })
    assert result.status_code == 200, result.text
    assert result.json()["detection_count"] == 1
    predictions = client.get(f"/api/ml/projects/{project_id}/predictions").json()
    assert predictions["count"] == 1


def test_inference_uses_provisional_and_orientation_classes(client, monkeypatch):
    project_id = create_project(client)
    _upload_model(client)
    _fake_detector(
        monkeypatch,
        xywhn=[[0.2, 0.5, 0.1, 0.1], [0.8, 0.5, 0.1, 0.1]],
        class_indexes=[0, 1],
        names={0: "breaker", 1: "horizontal_contact_a"},
    )

    result = client.post("/api/ml/inference/run", json={"project_ids": [project_id]})

    assert result.status_code == 200, result.text
    predictions = client.get(f"/api/ml/projects/{project_id}/predictions").json()["detections"]
    assert {row["class_key"] for row in predictions} == {"other", "contact_a"}


def test_detector_uses_a_junction_probe_floor_without_lowering_other_classes(tmp_path):
    from PIL import Image

    from services.inference_service import _detect

    class Array:
        def __init__(self, values):
            self.values = values

        def tolist(self):
            return self.values

    boxes = SimpleNamespace(
        xywhn=Array([[0.25, 0.5, 0.05, 0.05], [0.75, 0.5, 0.05, 0.05]]),
        conf=Array([0.36, 0.2]),
        cls=Array([0, 1]),
    )
    calls = []

    class Detector:
        names = {0: "junction", 1: "contact_a"}

        def predict(self, page, **kwargs):
            calls.append(kwargs)
            return [SimpleNamespace(boxes=boxes)]

    classes = {
        0: SimpleNamespace(id=10, key="junction", label="分岐"),
        1: SimpleNamespace(id=11, key="contact_a", label="接点"),
    }

    detections = _detect(Detector(), Image.new("RGB", (200, 100), "white"), 0.5, None, classes)

    assert calls == [{"conf": 0.05, "verbose": False}]
    assert [(item["class_key"], item["confidence"]) for item in detections] == [("junction", 0.36)]

    image_path = tmp_path / "page.png"
    Image.new("RGB", (200, 100), "white").save(image_path)
    calls.clear()

    detections = _detect(Detector(), str(image_path), 0.5, None, classes)

    assert calls == [{"conf": 0.05, "verbose": False}]
    assert [(item["class_key"], item["confidence"]) for item in detections] == [("junction", 0.36)]

    calls.clear()

    class HorizontalJunctionDetector(Detector):
        names = {0: "horizontal_junction", 1: "contact_a"}

    detections = _detect(HorizontalJunctionDetector(), Image.new("RGB", (200, 100), "white"), 0.5, None, classes)

    assert calls == [{"conf": 0.05, "verbose": False}]
    assert [(item["class_key"], item["confidence"]) for item in detections] == [("junction", 0.36)]

    calls.clear()

    class ContactDetector(Detector):
        names = {0: "contact_a", 1: "contact_a"}

    detections = _detect(ContactDetector(), Image.new("RGB", (200, 100), "white"), 0.5, None, classes)

    assert calls == [{"conf": 0.5, "verbose": False}]
    assert detections == []


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


# ------------------------------------------------------------------------- バックグラウンドの解析（DD-0039）


def _wait_job(client, job_id, timeout=10.0):
    import time

    deadline = time.time() + timeout
    while time.time() < deadline:
        job = client.get(f"/api/ml/analysis/jobs/{job_id}").json()
        if job["status"] != "running":
            return job
        time.sleep(0.02)
    raise AssertionError("job did not finish")


def test_analysis_job_saves_each_drawing_and_skips_failures(client, monkeypatch):
    from services import structure_service

    first, second, third = create_project(client), create_project(client, "b.png"), create_project(client, "c.png")
    _upload_model(client)
    _fake_detector(monkeypatch)

    def fail_second(project, *_args):
        if project.id == second:
            raise RuntimeError("test failure")
        return _result(project.id)

    monkeypatch.setattr(structure_service, "analyze_project", fail_second)
    res = client.post("/api/ml/analysis/jobs", json={"project_ids": []})
    assert res.status_code == 202, res.text
    assert res.json()["total"] == 3
    job = _wait_job(client, res.json()["id"])
    assert job["status"] == "done"
    assert job["done"] == 3
    assert [r["project_id"] for r in job["results"]] == [first, third]
    assert [(f["project_id"], f["message"]) for f in job["failures"]] == [(second, "test failure")]
    assert client.get(f"/api/ml/projects/{first}/structure").json()["result"] is not None
    assert client.get(f"/api/ml/projects/{second}/structure").json()["result"] is None
    assert client.get(f"/api/ml/projects/{third}/structure").json()["result"] is not None
    assert client.get("/api/ml/analysis/jobs").json()[0]["id"] == job["id"]
    # ロックは解放され、次のジョブを始められる
    assert client.post("/api/ml/analysis/jobs", json={"project_ids": [first]}).status_code == 202


def test_analysis_job_can_be_cancelled_between_drawings(client, monkeypatch):
    import threading

    from services import structure_service

    ids = [create_project(client, f"{n}.png") for n in "abc"]
    _upload_model(client)
    _fake_detector(monkeypatch)
    gate, started = threading.Event(), threading.Event()

    def slow(project, *_args):
        started.set()
        gate.wait(5)
        return _result(project.id)

    monkeypatch.setattr(structure_service, "analyze_project", slow)
    job_id = client.post("/api/ml/analysis/jobs", json={"project_ids": ids}).json()["id"]
    assert client.post("/api/ml/analysis/jobs", json={"project_ids": ids}).status_code == 409
    # 1 枚目の解析が始まってから中止する（始まる前の中止は 0 枚で止まる）
    assert started.wait(5)
    assert client.post(f"/api/ml/analysis/jobs/{job_id}/cancel").json()["cancel_requested"]
    gate.set()
    job = _wait_job(client, job_id)
    assert job["status"] == "cancelled"
    assert job["done"] == 1
    assert client.get("/api/ml/analysis/jobs/999").status_code == 404


def test_analysis_job_rejects_unknown_drawings(client, monkeypatch):
    pid = create_project(client)
    _upload_model(client)
    _fake_detector(monkeypatch)
    assert client.post("/api/ml/analysis/jobs", json={"project_ids": [pid, 999]}).status_code == 404


def test_detector_without_names_uses_class_order_and_skips_empty_results():
    from PIL import Image

    from services.inference_service import _detect

    class Array:
        def __init__(self, values):
            self.values = values

        def tolist(self):
            return self.values

    boxes = SimpleNamespace(xywhn=Array([[0.5, 0.5, 0.1, 0.1], [0.2, 0.2, 0.1, 0.1]]), conf=None, cls=Array([1, 7]))

    class Detector:
        names: dict = {}

        def predict(self, page, **kwargs):
            return [SimpleNamespace(boxes=None), SimpleNamespace(boxes=boxes)]

    classes = {1: SimpleNamespace(id=11, key="contact_a", label="接点")}
    detections = _detect(Detector(), Image.new("RGB", (20, 20), "white"), 0.5, None, classes)
    assert [(d["class_key"], d["confidence"]) for d in detections] == [("contact_a", None)]


def test_inference_reports_missing_model_files_images_and_extra_images(client, monkeypatch):
    from models import AnnotationImage
    from services.annotation_service import image_path
    from services.model_service import model_path

    _fake_detector(monkeypatch)
    first, second = create_project(client), create_project(client, "second.png")
    model = _upload_model(client).json()
    # 画像ファイルが無い図面: 検出は飛ばすが、構造解析は止める
    sha = client.get(f"/api/projects/{second}").json()["images"][0]["sha256"]
    image_path(sha).unlink()
    ok = client.post("/api/ml/inference/run", json={"project_ids": [second]})
    assert ok.status_code == 200 and ok.json()["detection_count"] == 0
    missing = client.post("/api/ml/analysis/run", json={"project_ids": [second]})
    assert missing.status_code == 400 and "図面画像が見つかりません" in missing.json()["detail"]
    # 画像が 2 枚の図面は構造解析しない
    with database.SessionLocal() as db:
        db.add(AnnotationImage(project_id=first, filename="extra.png", sha256="0" * 64, width=1, height=1))
        db.commit()
    two = client.post("/api/ml/analysis/run", json={"project_ids": [first]})
    assert two.status_code == 400 and "1画像" in two.json()["detail"]
    # モデルのファイルが消えている
    model_path(model["sha256"]).unlink()
    gone = client.post("/api/ml/inference/run", json={"project_ids": [first]})
    assert gone.status_code == 400 and "モデルファイルが見つかりません" in gone.json()["detail"]


def test_prediction_zip_skips_broken_lines_and_needs_project_files(client):
    pid = create_project(client)
    lines = ["0 0.5", "x 0.5 0.5 0.1 0.1", "99 0.5 0.5 0.1 0.1", "0 0.5 0.5 0.1 0.1 0.8"]
    res = client.post("/api/ml/inference/import",
                      files={"archive": ("p.zip", _predictions_zip(pid, lines), "application/zip")})
    assert res.status_code == 200, res.text
    assert res.json()["results"][0]["detections"] == 1
    import io
    import zipfile

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("labels/page1.txt", "0 0.5 0.5 0.1 0.1")
    res = client.post("/api/ml/inference/import", files={"archive": ("p.zip", buf.getvalue(), "application/zip")})
    assert res.status_code == 400 and "取り込める推論結果がありません" in res.json()["detail"]
