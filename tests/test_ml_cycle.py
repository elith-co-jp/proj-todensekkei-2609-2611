"""AI 改善サイクル（モデル管理・推論・推論結果取込・学習）の API テスト。

ultralytics 非搭載の環境でも外部実行ルート（ZIP 取込 / best.pt 登録）が
機能すること、および推論由来シンボルの蓄積を確認する。
"""

from __future__ import annotations

import io
import zipfile

import pytest

from tests.conftest import SAMPLE_ANNOTATION, create_project


def _upload_model(client, name: str = "best.pt", raw: bytes = b"PK\x03\x04-fake-model"):
    return client.post("/api/ml/models", files={"file": (name, raw, "application/octet-stream")})


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


def test_project_predictions_404(client):
    assert client.get("/api/ml/projects/9999/predictions").status_code == 404
