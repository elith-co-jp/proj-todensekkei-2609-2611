"""AI 改善サイクル（モデル管理・推論・推論結果取込・学習）の API テスト。

ultralytics 非搭載の環境でも外部実行ルート（ZIP 取込 / best.pt 登録）が
機能すること、および推論由来シンボルの蓄積を確認する。
"""

from __future__ import annotations

import io
import json
import zipfile

import pytest

import database
from models import TrainingRun
from tests.conftest import SAMPLE_ANNOTATION, create_project


def _upload_model(client, name: str = "best.pt", raw: bytes = b"PK\x03\x04-fake-model"):
    return client.post("/api/ml/models", files={"file": (name, raw, "application/octet-stream")})


def _insert_training_run(
    *,
    status: str = "success",
    decision: str = "pending",
    result_model_id: int | None = None,
    baseline_metrics: dict | None = None,
) -> int:
    """完了済み学習ジョブを直接作る（実学習は重いので再現しない）。"""
    db = database.SessionLocal()
    try:
        run = TrainingRun(
            status=status,
            project_ids_json="[]",
            image_count=2,
            epochs=5,
            imgsz=640,
            base_model="yolov8n.pt",
            result_model_id=result_model_id,
            metrics_json=json.dumps({"metrics/mAP50-95(B)": "0.8123"}),
            baseline_metrics_json=json.dumps(baseline_metrics) if baseline_metrics else None,
            baseline_label="best v1",
            decision=decision,
        )
        db.add(run)
        db.commit()
        db.refresh(run)
        return run.id
    finally:
        db.close()


def _predictions_zip(project_id: int, lines: list[str]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(f"labels/p{project_id}_page1.txt", "\n".join(lines))
    return buf.getvalue()


def test_ml_status(client):
    res = client.get("/api/ml/status")
    assert res.status_code == 200
    body = res.json()
    assert set(body) >= {"ultralytics", "active_model", "model_count", "training_running"}
    assert body["model_count"] == 0
    assert body["active_model"] is None


def test_model_lifecycle(client):
    res = _upload_model(client)
    assert res.status_code == 200, res.text
    model = res.json()
    assert model["is_active"] is True  # 最初のモデルは自動で使用中に
    assert model["version"] == 1
    assert model["source"] == "upload"

    res = _upload_model(client, name="best2.pt", raw=b"PK\x03\x04-second")
    assert res.json()["version"] == 2
    assert res.json()["is_active"] is False

    models = client.get("/api/ml/models").json()
    assert len(models) == 2
    assert [m["is_active"] for m in models].count(True) == 1

    res = client.post("/api/ml/models/2/activate")
    assert res.status_code == 200
    models = {m["id"]: m for m in client.get("/api/ml/models").json()}
    assert models[2]["is_active"] is True
    assert models[1]["is_active"] is False

    res = client.get("/api/ml/models/1/download")
    assert res.status_code == 200
    assert res.content == b"PK\x03\x04-fake-model"

    res = client.delete("/api/ml/models/2")
    assert res.status_code == 200
    assert len(client.get("/api/ml/models").json()) == 1


def test_model_upload_rejects_bad_file(client):
    res = client.post(
        "/api/ml/models",
        files={"file": ("model.txt", b"not a torch model", "application/octet-stream")},
    )
    assert res.status_code == 400


def test_inference_requires_model(client):
    pid = create_project(client)
    res = client.post("/api/ml/inference/run", json={"project_ids": [pid]})
    assert res.status_code == 400
    assert "モデル" in res.json()["detail"]


def test_inference_without_ultralytics(client, monkeypatch):
    import services.inference_service as inf

    monkeypatch.setattr(inf, "ultralytics_available", lambda: False)
    pid = create_project(client)
    _upload_model(client)
    res = client.post("/api/ml/inference/run", json={"project_ids": [pid]})
    assert res.status_code == 400
    assert "yolo predict" in res.json()["detail"]  # 外部実行への誘導


def test_import_predictions_zip_and_json(client):
    pid = create_project(client)
    classes = client.get("/api/classes").json()
    target = classes[0]

    lines = [f"{target['yolo_index']} 0.5 0.5 0.1 0.1 0.92"]
    res = client.post(
        "/api/ml/inference/import",
        files={"archive": ("preds.zip", _predictions_zip(pid, lines), "application/zip")},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["count"] == 1
    assert body["results"][0]["detections"] == 1

    # 結果表示（JSON）
    res = client.get(f"/api/ml/projects/{pid}/predictions")
    assert res.status_code == 200
    data = res.json()
    assert data["count"] == 1
    det = data["detections"][0]
    assert det["class_key"] == target["key"]
    assert abs(det["confidence"] - 0.92) < 1e-6

    # 一覧・サマリに検出件数が載る
    row = next(r for r in client.get("/api/projects").json() if r["id"] == pid)
    assert row["prediction_count"] == 1
    summary = client.get("/api/ml/predictions").json()
    assert summary[0]["project_id"] == pid

    # 図面を削除すると推論結果も連鎖削除される
    res = client.delete(f"/api/projects/{pid}")
    assert res.status_code == 200
    assert client.get("/api/ml/predictions").json() == []


def test_import_predictions_zip_unmatched_and_missing(client):
    pid = create_project(client)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(f"p{pid}_a.txt", "0 0.5 0.5 0.1 0.1 0.9")
        zf.writestr("p999_a.txt", "0 0.5 0.5 0.1 0.1 0.9")
        zf.writestr("readme.txt", "hello")
    res = client.post(
        "/api/ml/inference/import",
        files={"archive": ("preds.zip", buf.getvalue(), "application/zip")},
    )
    assert res.status_code == 200
    body = res.json()
    assert body["missing_project_ids"] == [999]
    assert "readme.txt" in body["unmatched_files"]


def test_import_predictions_zip_invalid(client):
    res = client.post(
        "/api/ml/inference/import",
        files={"archive": ("bad.zip", b"not a zip", "application/zip")},
    )
    assert res.status_code == 400


def test_import_predictions_replaces_previous(client):
    pid = create_project(client)
    classes = client.get("/api/classes").json()
    yi = classes[0]["yolo_index"]
    for lines in ([f"{yi} 0.1 0.1 0.05 0.05 0.9"], [f"{yi} 0.2 0.2 0.05 0.05 0.8", f"{yi} 0.3 0.3 0.05 0.05 0.7"]):
        res = client.post(
            "/api/ml/inference/import",
            files={"archive": ("p.zip", _predictions_zip(pid, lines), "application/zip")},
        )
        assert res.status_code == 200
    data = client.get(f"/api/ml/projects/{pid}/predictions").json()
    assert data["count"] == 2  # 最新だけを保持（蓄積しない）


def test_inference_origin_symbols_accumulate(client):
    """推論由来（origin=inference, confidence 付き）で保存したシンボルが往復し、
    stats の by_origin に集計される。"""
    pid = create_project(client)
    payload = {
        "symbols": [
            {
                "ref": "SYM-0001",
                "class_key": "relay_coil",
                "label": None,
                "cx": 0.5,
                "cy": 0.5,
                "w": 0.1,
                "h": 0.1,
                "origin": "inference",
                "confidence": 0.87,
                "terminals": [],
            }
        ],
        "connections": [],
    }
    res = client.put(f"/api/projects/{pid}/annotations", json=payload)
    assert res.status_code == 200, res.text

    detail = client.get(f"/api/projects/{pid}").json()
    sym = detail["symbols"][0]
    assert sym["origin"] == "inference"
    assert abs(sym["confidence"] - 0.87) < 1e-6

    stats = client.get("/api/stats").json()
    assert stats["by_origin"]["inference"] == 1


def test_annotation_without_origin_defaults_manual(client):
    pid = create_project(client)
    res = client.put(f"/api/projects/{pid}/annotations", json=SAMPLE_ANNOTATION)
    assert res.status_code == 200
    detail = client.get(f"/api/projects/{pid}").json()
    assert all(s["origin"] == "manual" for s in detail["symbols"])
    stats = client.get("/api/stats").json()
    assert stats["by_origin"]["manual"] == len(SAMPLE_ANNOTATION["symbols"])


def test_training_without_ultralytics(client, monkeypatch):
    import services.training_service as tr

    monkeypatch.setattr(tr, "ultralytics_available", lambda: False)
    pid = create_project(client)
    res = client.post("/api/ml/training/run", json={"project_ids": [pid], "epochs": 1})
    assert res.status_code == 400
    assert "best.pt" in res.json()["detail"]  # 外部学習への誘導


def test_training_runs_list(client):
    res = client.get("/api/ml/training/runs")
    assert res.status_code == 200
    assert res.json() == []


def test_frozen_training_uses_bundled_base_model(tmp_path, monkeypatch):
    from services import training_service

    bundled = tmp_path / "models" / "yolov8n.pt"
    bundled.parent.mkdir()
    bundled.write_bytes(b"test weight")
    monkeypatch.setattr(training_service, "resource_base", lambda: tmp_path)
    monkeypatch.setattr(training_service, "is_frozen", lambda: True)

    assert training_service.default_base_model() == str(bundled)
    bundled.unlink()
    with pytest.raises(FileNotFoundError, match="同梱した学習用モデル"):
        training_service.default_base_model()


def test_frozen_bundled_model_auto_registered(client, tmp_path, monkeypatch):
    """凍結 exe の初回起動時、モデル未登録なら同梱モデルが使用中として登録される。"""
    from services import model_service

    bundled = tmp_path / "models" / model_service.BUNDLED_MODEL_NAME
    bundled.parent.mkdir()
    bundled.write_bytes(b"PK\x03\x04-bundled-model")
    monkeypatch.setattr(model_service, "resource_base", lambda: tmp_path)
    monkeypatch.setattr(model_service, "is_frozen", lambda: True)

    db = database.SessionLocal()
    try:
        model = model_service.ensure_bundled_model(db)
        assert model is not None
        assert model.is_active and model.source == "bundled"
        assert model_service.ensure_bundled_model(db) is None  # 二重登録しない
    finally:
        db.close()

    models = client.get("/api/ml/models").json()
    assert len(models) == 1
    assert models[0]["file_name"] == "yolo11n_all_symbols_best.pt"
    status = client.get("/api/ml/status").json()
    assert status["active_model"]["name"] == "yolo11n_all_symbols_best"


def test_project_predictions_404(client):
    assert client.get("/api/ml/projects/9999/predictions").status_code == 404


def test_training_decision_adopt_switches_active_model(client):
    """採用を選ぶと学習済みモデルが使用中に切り替わる。"""
    _upload_model(client)  # v1 が使用中
    new_model = _upload_model(client, name="run1_best.pt", raw=b"PK\x03\x04-trained").json()
    run_id = _insert_training_run(
        result_model_id=new_model["id"],
        baseline_metrics={"metrics/mAP50-95(B)": "0.7000"},
    )

    body = client.get(f"/api/ml/training/runs/{run_id}").json()
    assert body["decision"] == "pending"
    assert body["baseline_metrics"]["metrics/mAP50-95(B)"] == "0.7000"
    assert body["baseline_label"] == "best v1"
    assert body["result_model"]["id"] == new_model["id"]

    res = client.post(f"/api/ml/training/runs/{run_id}/decision", json={"decision": "adopt"})
    assert res.status_code == 200, res.text
    assert res.json()["decision"] == "adopted"

    models = {m["id"]: m for m in client.get("/api/ml/models").json()}
    assert models[new_model["id"]]["is_active"] is True
    assert models[1]["is_active"] is False


def test_training_decision_reject_keeps_current_model(client):
    """見送りを選ぶと現行モデルがそのまま使われ続ける。"""
    _upload_model(client)
    new_model = _upload_model(client, name="run1_best.pt", raw=b"PK\x03\x04-trained").json()
    run_id = _insert_training_run(result_model_id=new_model["id"])

    res = client.post(f"/api/ml/training/runs/{run_id}/decision", json={"decision": "reject"})
    assert res.status_code == 200, res.text
    assert res.json()["decision"] == "rejected"

    models = {m["id"]: m for m in client.get("/api/ml/models").json()}
    assert models[1]["is_active"] is True
    assert models[new_model["id"]]["is_active"] is False


def test_training_decision_only_for_pending_successful_runs(client):
    new_model = _upload_model(client, name="x.pt", raw=b"PK\x03\x04-t2").json()
    done_run = _insert_training_run(result_model_id=new_model["id"], decision="adopted")
    res = client.post(f"/api/ml/training/runs/{done_run}/decision", json={"decision": "adopt"})
    assert res.status_code == 400  # 判定済み

    running_run = _insert_training_run(status="running", decision="", result_model_id=None)
    res = client.post(f"/api/ml/training/runs/{running_run}/decision", json={"decision": "adopt"})
    assert res.status_code == 400  # 未完了

    assert client.post("/api/ml/training/runs/9999/decision", json={"decision": "adopt"}).status_code == 404


def test_pick_metrics_extracts_main_keys():
    from services import training_service

    results_dict = {
        "metrics/precision(B)": 0.91,
        "metrics/recall(B)": 0.88,
        "metrics/mAP50(B)": 0.95,
        "metrics/mAP50-95(B)": 0.81234,
        "fitness": 0.5,
    }
    assert training_service._pick_metrics(results_dict) == {
        "metrics/precision(B)": "0.9100",
        "metrics/recall(B)": "0.8800",
        "metrics/mAP50(B)": "0.9500",
        "metrics/mAP50-95(B)": "0.8123",
    }
    assert training_service._pick_metrics({}) is None
    assert training_service._pick_metrics({"fitness": 1.0}) is None
