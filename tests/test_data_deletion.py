"""登録したデータの削除（DD-0052）。確認に出す内容と、実際に消えるもの・残るもの・消せないとき。"""

from __future__ import annotations

import json

import database
from database import IMAGE_DIR, TRAINING_DIR
from models import (
    AnnotationImage,
    CircuitChangeSet,
    MlModel,
    Prediction,
    StructurePrediction,
    SymbolClass,
    TrainingRun,
)
from services import analysis_jobs
from services.annotation_service import image_path
from tests.conftest import SAMPLE_ANNOTATION, create_project


def _add_analysis(pid: int) -> None:
    db = database.SessionLocal()
    try:
        cls = db.query(SymbolClass).first()
        db.add(Prediction(project_id=pid, model_label="t", class_id=cls.id, cx=0.5, cy=0.5, w=0.1, h=0.1))
        db.add(StructurePrediction(project_id=pid, payload_json=json.dumps({"wires": [{}]})))
        db.commit()
    finally:
        db.close()


def _add(row):
    db = database.SessionLocal()
    try:
        db.add(row)
        db.commit()
        db.refresh(row)
        return row.id
    finally:
        db.close()


def _change_set(project_ids: list[int]) -> CircuitChangeSet:
    return CircuitChangeSet(title="t", project_ids_json=json.dumps(project_ids), before_json="[]", after_hash="x")


def _running_job(project_ids: list[int]) -> analysis_jobs.AnalysisJob:
    job = analysis_jobs.AnalysisJob(id=9999, project_ids=project_ids, conf=0.25, connector_conf=None)
    analysis_jobs._jobs[job.id] = job
    return job


def test_preview_lists_what_goes_and_what_stays(client):
    pid = create_project(client)
    other = create_project(client, "B.png", 500, 300)
    client.put(f"/api/projects/{pid}/annotations", json=SAMPLE_ANNOTATION)
    _add_analysis(pid)
    client.post("/api/drawing-sets", json={"name": "S", "project_ids": [pid, other]})
    _add(_change_set([pid]))
    _add(TrainingRun(status="success", project_ids_json=json.dumps([pid, other])))

    res = client.post("/api/projects/delete-preview", json={"ids": [pid, 12345]})
    assert res.status_code == 200
    body = res.json()
    assert body["project_ids"] == [pid]
    assert body["missing_ids"] == [12345]
    assert body["counts"] == {
        "symbols": 3, "terminals": 3, "connections": 2, "predictions": 1, "structures": 1,
        "drawing_sets": 1, "change_sets": 1, "image_files": 1, "training_runs": 1,
    }
    assert body["blockers"] == [] and body["analysis_blockers"] == []
    # 確認だけでは何も消えない
    assert client.get(f"/api/projects/{pid}").status_code == 200


def test_delete_removes_related_data_and_unused_image(client):
    pid = create_project(client)
    other = create_project(client, "B.png", 500, 300)
    _add_analysis(pid)
    set_id = client.post("/api/drawing-sets", json={"name": "S", "project_ids": [pid, other]}).json()["id"]
    mine = _add(_change_set([pid]))
    others = _add(_change_set([other]))
    run = _add(TrainingRun(status="success", project_ids_json=json.dumps([pid])))
    db = database.SessionLocal()
    sha = db.query(AnnotationImage).filter_by(project_id=pid).one().sha256
    db.close()
    client.get(f"/api/projects/{pid}/thumbnail")
    assert image_path(sha).exists()

    assert client.post("/api/projects/bulk-delete", json={"ids": [pid]}).json() == {"deleted": [pid]}

    db = database.SessionLocal()
    try:
        assert db.query(Prediction).count() == 0
        assert db.query(StructurePrediction).count() == 0
        assert db.get(CircuitChangeSet, mine) is None      # 図面が無いと戻せない取り消しの記録
        assert db.get(CircuitChangeSet, others) is not None
        assert db.get(TrainingRun, run) is not None        # 学習の履歴は残す
    finally:
        db.close()
    assert client.get(f"/api/drawing-sets/{set_id}").json()["project_ids"] == [other]
    assert not image_path(sha).exists()
    assert not list((IMAGE_DIR / "thumbs").glob(f"{sha}_*"))


def test_shared_image_is_kept(client):
    a = create_project(client, "A.png")
    b = create_project(client, "B.png")  # 同じ内容の画像は同じファイルを使う
    db = database.SessionLocal()
    sha = db.query(AnnotationImage).filter_by(project_id=a).one().sha256
    db.close()
    assert client.post("/api/projects/delete-preview", json={"ids": [a]}).json()["counts"]["image_files"] == 0
    assert client.delete(f"/api/projects/{a}").status_code == 200
    assert image_path(sha).exists()
    assert client.get(f"/api/projects/{b}/image").status_code == 200


def test_running_analysis_blocks_delete(client):
    pid = create_project(client)
    job = _running_job([pid])
    try:
        preview = client.post("/api/projects/delete-preview", json={"ids": [pid]}).json()
        assert "AI 解析を実行中" in preview["blockers"][0]
        assert client.delete(f"/api/projects/{pid}").status_code == 409
        assert client.post("/api/projects/bulk-delete", json={"ids": [pid]}).status_code == 409
        assert client.post("/api/ml/analysis/clear", json={"ids": [pid]}).status_code == 409
        assert client.get(f"/api/projects/{pid}").status_code == 200
    finally:
        analysis_jobs._jobs.pop(job.id, None)


def test_running_training_blocks_project_delete_but_not_clear(client):
    pid = create_project(client)
    _add_analysis(pid)
    _add(TrainingRun(status="running", project_ids_json=json.dumps([pid])))
    preview = client.post("/api/projects/delete-preview", json={"ids": [pid]}).json()
    assert preview["blockers"] and preview["analysis_blockers"] == []
    res = client.delete(f"/api/projects/{pid}")
    assert res.status_code == 409
    assert "学習" in res.json()["detail"]
    assert client.post("/api/ml/analysis/clear", json={"ids": [pid]}).json() == {"predictions": 1, "structures": 1}


def test_clear_analysis_keeps_annotations(client):
    pid = create_project(client)
    client.put(f"/api/projects/{pid}/annotations", json=SAMPLE_ANNOTATION)
    _add_analysis(pid)
    assert client.post("/api/ml/analysis/clear", json={"ids": []}).status_code == 400
    assert client.post("/api/ml/analysis/clear", json={"ids": [pid]}).json() == {"predictions": 1, "structures": 1}
    assert len(client.get(f"/api/projects/{pid}").json()["symbols"]) == 3
    assert client.get(f"/api/ml/projects/{pid}/structure").json()["result"] is None


def test_delete_training_run_keeps_model(client):
    model = _add(MlModel(name="m", version=1, file_name="m.pt", sha256="a" * 64))
    run = _add(TrainingRun(status="success", result_model_id=model))
    folder = TRAINING_DIR / f"run_{run}"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "x.txt").write_text("x")
    running = _add(TrainingRun(status="running"))

    assert client.delete(f"/api/ml/training/runs/{running}").status_code == 409
    assert client.delete("/api/ml/training/runs/9999").status_code == 404
    assert client.delete(f"/api/ml/training/runs/{run}").json() == {"deleted": run}
    assert not folder.exists()
    assert model in {m["id"] for m in client.get("/api/ml/models").json()}


def test_model_in_use_cannot_be_deleted(client):
    active = _add(MlModel(name="m", version=1, file_name="m.pt", sha256="b" * 64, is_active=True))
    base = _add(MlModel(name="n", version=2, file_name="n.pt", sha256="c" * 64))
    _add(TrainingRun(status="running", base_model=f"/data/models/{'c' * 64}.pt"))
    job = _running_job([1])
    try:
        assert "AI 解析を実行中" in client.delete(f"/api/ml/models/{active}").json()["detail"]
        assert "学習" in client.delete(f"/api/ml/models/{base}").json()["detail"]
    finally:
        analysis_jobs._jobs.pop(job.id, None)
    assert client.delete(f"/api/ml/models/{active}").status_code == 200


def test_delete_change_set_keeps_drawing(client):
    pid = create_project(client)
    client.put(f"/api/projects/{pid}/annotations", json=SAMPLE_ANNOTATION)
    cs = _add(_change_set([pid]))
    assert client.delete(f"/api/circuit/edit/{cs}").json() == {"deleted": cs}
    assert client.delete(f"/api/circuit/edit/{cs}").status_code == 404
    assert client.get("/api/circuit/edit/history").json()["change_sets"] == []
    assert len(client.get(f"/api/projects/{pid}").json()["symbols"]) == 3
