"""AI 解析・学習の API が、届かない対象・二重実行・壊れた入力を日本語のエラーで断ることを確かめる。"""

from __future__ import annotations

import io

import pytest

import database
from models import AnnotationProject, MlModel
from routers import ml
from services import analysis_jobs, structure_service
from services.model_service import model_path
from tests.conftest import create_project
from tests.test_ml_cycle import _insert_training_run, _upload_model
from tests.test_structure_api import _fake_detector


@pytest.fixture()
def held_lock():
    assert ml._inference_lock.acquire(blocking=False)
    yield
    ml._inference_lock.release()


def test_models_that_do_not_exist_or_lost_their_file(client):
    assert client.delete("/api/ml/models/9999").status_code == 404
    model_id = _upload_model(client).json()["id"]
    with database.SessionLocal() as db:
        sha = db.get(MlModel, model_id).sha256
    model_path(sha).unlink()
    res = client.get(f"/api/ml/models/{model_id}/download")
    assert res.status_code == 404
    assert res.json()["detail"] == "モデルファイルが見つかりません"


@pytest.mark.parametrize("url", ["/api/ml/inference/run", "/api/ml/analysis/run"])
def test_inference_targets_must_all_exist(client, url):
    project_id = create_project(client)
    _upload_model(client)
    assert client.post(url, json={"project_ids": [9999]}).json()["detail"] == "対象のプロジェクトがありません"
    res = client.post(url, json={"project_ids": [project_id, 9999]})
    assert res.status_code == 404
    assert res.json()["detail"] == "対象に存在しない図面が含まれています"


def test_inference_refuses_while_another_runs(client, held_lock):
    create_project(client)
    _upload_model(client)
    res = client.post("/api/ml/inference/run", json={})
    assert res.status_code == 409


def test_unexpected_inference_error_is_500_and_releases_the_lock(client, monkeypatch):
    create_project(client)
    _upload_model(client)

    def broken(*_args, **_kwargs):
        raise KeyError("boom")

    monkeypatch.setattr(ml, "run_inference", broken)
    res = client.post("/api/ml/inference/run", json={})
    assert res.status_code == 500
    assert "保存済みの結果は変更されていません" in res.json()["detail"]
    assert ml._inference_lock.acquire(blocking=False)
    ml._inference_lock.release()


def test_analysis_job_preconditions(client, monkeypatch):
    assert client.post("/api/ml/analysis/jobs", json={}).json()["detail"].startswith("推論に使うモデルがありません")
    _upload_model(client)
    monkeypatch.setattr(ml.inference_service, "ultralytics_available", lambda: False)
    res = client.post("/api/ml/analysis/jobs", json={})
    assert res.status_code == 400
    assert "ultralytics" in res.json()["detail"]
    _fake_detector(monkeypatch)
    assert client.post("/api/ml/analysis/jobs", json={}).status_code == 404  # 図面が無い
    project_id = create_project(client)
    assert client.post("/api/ml/analysis/jobs", json={"project_ids": [project_id, 9999]}).status_code == 404
    assert client.post("/api/ml/analysis/jobs/9999/cancel").status_code == 404


def test_analysis_job_releases_the_lock_when_it_cannot_start(client, monkeypatch):
    create_project(client)
    _upload_model(client)
    _fake_detector(monkeypatch)

    def no_thread(*_args, **_kwargs):
        raise RuntimeError("thread could not start")

    monkeypatch.setattr(analysis_jobs, "start", no_thread)
    with pytest.raises(RuntimeError):
        client.post("/api/ml/analysis/jobs", json={})
    assert ml._inference_lock.acquire(blocking=False)
    ml._inference_lock.release()


def test_prediction_import_rejects_wrong_files_and_busy_server(client):
    def post(name, blob):
        return client.post("/api/ml/inference/import", files={"archive": (name, io.BytesIO(blob), "application/zip")})

    assert post("predictions.txt", b"x").json()["detail"] == "ZIP ファイルを指定してください"
    assert post("predictions.zip", b"").json()["detail"] == "ファイルが空です"
    assert ml._inference_lock.acquire(blocking=False)
    try:
        assert post("predictions.zip", b"PK").status_code == 409
    finally:
        ml._inference_lock.release()


def test_ai_import_needs_refs(client):
    project_id = create_project(client)
    symbol = {"ref": "", "class_key": "relay_coil", "cx": 0.5, "cy": 0.5, "w": 0.1, "h": 0.1, "terminals": []}
    res = client.post(f"/api/ml/projects/{project_id}/ai-import", json={"symbols": [symbol], "connections": []})
    assert res.status_code == 400


def test_training_runs_that_do_not_exist_or_lost_their_model(client):
    assert client.get("/api/ml/training/runs/9999").status_code == 404
    assert client.post("/api/ml/training/runs/9999/decision", json={"decision": "adopt"}).status_code == 404
    model_id = _upload_model(client).json()["id"]
    run_id = _insert_training_run(result_model_id=model_id)
    assert client.delete(f"/api/ml/models/{model_id}").status_code == 200
    res = client.post(f"/api/ml/training/runs/{run_id}/decision", json={"decision": "adopt"})
    assert res.status_code == 400
    assert "削除されている" in res.json()["detail"]


def test_structure_analysis_reads_the_single_registered_image(client):
    project_id = create_project(client)
    model_id = _upload_model(client).json()["id"]
    with database.SessionLocal() as db:
        project, model = db.get(AnnotationProject, project_id), db.get(MlModel, model_id)
        result = structure_service.analyze_project(project, model, [])
    assert result["source"]["project_id"] == project_id
    assert result["symbols"] == []
