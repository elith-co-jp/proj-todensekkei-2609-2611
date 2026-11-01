"""図面全体・DI 範囲などの大枠を領域枠として分け、シンボル件数に含めないことを確認する。"""

from __future__ import annotations

from sqlalchemy import text

import database
from tests.conftest import create_project
from tests.test_ml_cycle import _predictions_zip, _upload_model
from tests.test_structure_api import _fake_detector, _result

SYMBOL = (0.30, 0.40, 0.05, 0.05)
NEAR_LIMIT_SYMBOL = (0.60, 0.40, 0.19, 0.20)
DRAWING_FRAME = (0.50, 0.50, 0.95, 0.90)
DI_RANGE = (0.50, 0.80, 0.45, 0.03)


def _import(client, project_id, boxes):
    lines = [f"0 {cx} {cy} {w} {h} 0.9" for cx, cy, w, h in boxes]
    return client.post(
        "/api/ml/inference/import",
        files={"archive": ("preds.zip", _predictions_zip(project_id, lines), "application/zip")},
    )


def test_import_separates_region_frames_from_symbol_counts(client):
    pid = create_project(client)
    res = _import(client, pid, [SYMBOL, NEAR_LIMIT_SYMBOL, DRAWING_FRAME, DI_RANGE])
    assert res.status_code == 200, res.text
    entry = res.json()["results"][0]
    assert (entry["detections"], entry["region_count"]) == (2, 2)

    data = client.get(f"/api/ml/projects/{pid}/predictions").json()
    assert data["count"] == 2
    assert sorted(d["w"] for d in data["detections"]) == [0.05, 0.19]
    assert data["region_count"] == 2
    assert sorted(r["w"] for r in data["regions"]) == [0.45, 0.95]

    row = next(r for r in client.get("/api/projects").json() if r["id"] == pid)
    assert row["prediction_count"] == 2
    assert client.get("/api/ml/predictions").json()[0]["count"] == 2
    assert client.get("/api/stats").json()["prediction_count"] == 2


def test_structure_analysis_receives_symbols_only(client, monkeypatch):
    from services import structure_service

    pid = create_project(client)
    _upload_model(client)
    _fake_detector(monkeypatch, xywhn=[list(SYMBOL), list(DRAWING_FRAME)])
    seen = []

    def analyze(project, model, detections):
        seen.extend(detections)
        return _result(project.id)

    monkeypatch.setattr(structure_service, "analyze_project", analyze)
    res = client.post("/api/ml/analysis/run", json={"project_ids": [pid]})
    assert res.status_code == 200, res.text
    assert [d["w"] for d in seen] == [0.05]
    entry = res.json()["results"][0]
    assert (entry["detections"], entry["region_count"]) == (1, 1)
    assert res.json()["detection_count"] == 1
    assert client.get(f"/api/ml/projects/{pid}/predictions").json()["region_count"] == 1


def test_existing_db_upgrade_moves_large_frames_to_regions(client):
    pid = create_project(client)
    assert _import(client, pid, [SYMBOL, DRAWING_FRAME]).status_code == 200
    with database.engine.begin() as conn:
        conn.execute(text("ALTER TABLE predictions DROP COLUMN kind"))

    database._migrate_columns()

    data = client.get(f"/api/ml/projects/{pid}/predictions").json()
    assert (data["count"], data["region_count"]) == (1, 1)
    assert data["regions"][0]["w"] == 0.95
