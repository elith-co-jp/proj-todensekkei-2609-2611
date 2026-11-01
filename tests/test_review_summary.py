"""AI 検出を人がどう確認したか（採用・種別修正・削除・手動追加）の集計を確認する。"""

from __future__ import annotations

from services.review_service import summarize_review
from tests.conftest import create_project
from tests.test_ml_cycle import _predictions_zip


def _box(class_key, cx, cy=0.5, w=0.1, h=0.1, **extra):
    return {"class_key": class_key, "cx": cx, "cy": cy, "w": w, "h": h, **extra}


def test_counts_each_review_outcome():
    predictions = [
        _box("relay_coil", 0.1),
        _box("contact_a", 0.3),
        _box("lamp", 0.5),
        _box("junction", 0.7),
    ]
    symbols = [
        _box("relay_coil", 0.1, origin="inference"),
        _box("contact_b", 0.31, origin="inference"),
        _box("lamp", 0.54, origin="inference"),
        _box("terminal", 0.9, origin="manual"),
    ]
    assert summarize_review(predictions, symbols) == {
        "imported": True,
        "kept": 1,
        "relabeled": 1,
        "deleted": 2,
        "manual_added": 1,
    }


def test_each_detection_is_matched_once_best_overlap_first():
    predictions = [_box("relay_coil", 0.489), _box("relay_coil", 0.505)]
    symbols = [_box("relay_coil", 0.5, origin="inference"), _box("relay_coil", 0.464, origin="manual")]
    assert summarize_review(predictions, symbols) == {
        "imported": True,
        "kept": 2,
        "relabeled": 0,
        "deleted": 0,
        "manual_added": 0,
    }


def test_without_import_nothing_counts_as_deleted():
    predictions = [_box("relay_coil", 0.1), _box("lamp", 0.5)]
    symbols = [_box("relay_coil", 0.1, origin="manual"), _box("terminal", 0.9, origin="manual")]
    assert summarize_review(predictions, symbols) == {
        "imported": False,
        "kept": 1,
        "relabeled": 0,
        "deleted": 0,
        "manual_added": 1,
    }


def test_review_summary_api_reports_training_effect(client):
    pid = create_project(client)
    lines = [
        "0 0.10 0.50 0.05 0.05 0.9",
        "1 0.30 0.50 0.05 0.05 0.8",
        "0 0.50 0.50 0.95 0.90 0.7",
    ]
    res = client.post(
        "/api/ml/inference/import",
        files={"archive": ("preds.zip", _predictions_zip(pid, lines), "application/zip")},
    )
    assert res.status_code == 200, res.text
    annotations = {
        "symbols": [
            {"class_key": "relay_coil", "cx": 0.10, "cy": 0.50, "w": 0.05, "h": 0.05, "origin": "inference"},
            {"class_key": "terminal", "cx": 0.80, "cy": 0.20, "w": 0.03, "h": 0.03},
        ],
        "connections": [],
    }
    assert client.put(f"/api/projects/{pid}/annotations", json=annotations).status_code == 200

    expected = {
        "project_id": pid,
        "status": "draft",
        "in_next_training": False,
        "ai_detection_count": 2,
        "region_excluded_count": 1,
        "imported": True,
        "kept": 1,
        "relabeled": 0,
        "deleted": 1,
        "manual_added": 1,
    }
    assert client.get(f"/api/ml/projects/{pid}/review-summary").json() == expected

    assert client.put(f"/api/projects/{pid}/meta", json={"status": "done"}).status_code == 200
    summary = client.get(f"/api/ml/projects/{pid}/review-summary").json()
    assert (summary["status"], summary["in_next_training"]) == ("done", True)


def test_review_summary_404(client):
    assert client.get("/api/ml/projects/9999/review-summary").status_code == 404
