"""学習データの量と質、クラス別の評価と採用判定の基準（#24）。"""

from __future__ import annotations

import sys
from types import SimpleNamespace

import pytest

from services import training_service
from services.training_service import adoption_review
from tests.conftest import create_project
from tests.test_ml_cycle import _predictions_zip, _upload_model
from tests.test_training_holdout import _drawings, _wait_for_run

MAP = "metrics/mAP50-95(B)"


def _metrics(overall: float, **per_class: tuple[float, int]) -> dict:
    return {
        MAP: f"{overall:.4f}",
        "per_class": {
            key: {"label": key, "instances": n, "precision": 0.9, "recall": 0.9, "map50": 0.9, "map50_95": value}
            for key, (value, n) in per_class.items()
        },
    }


HOLDOUT = {"mode": "holdout", "train_project_ids": [1], "val_project_ids": [2]}


@pytest.mark.parametrize(
    ("candidate", "baseline", "scope", "verdict"),
    [
        (_metrics(0.80, contact_a=(0.80, 40)), _metrics(0.78, contact_a=(0.79, 40)), HOLDOUT, "adopt"),
        # 全体の値が許容幅を超えて下がる
        (_metrics(0.77, contact_a=(0.80, 40)), _metrics(0.78, contact_a=(0.79, 40)), HOLDOUT, "hold"),
        # 全体は上がっても、評価用に 5 件以上あるクラスが 0.05 を超えて下がる
        (_metrics(0.82, contact_b=(0.30, 8)), _metrics(0.78, contact_b=(0.40, 8)), HOLDOUT, "hold"),
        # 4 件しかないクラスの下がり方は判定に使わない
        (_metrics(0.82, contact_b=(0.10, 4)), _metrics(0.78, contact_b=(0.40, 4)), HOLDOUT, "adopt"),
        # 学習に使った図面での評価は良くても判断できない
        (_metrics(0.90), _metrics(0.78), {"mode": "train"}, "caution"),
        (None, _metrics(0.78), HOLDOUT, "unknown"),
    ],
)
def test_adoption_review_criteria(candidate, baseline, scope, verdict):
    review = adoption_review(candidate, baseline, scope)
    assert review["verdict"] == verdict
    assert review["reasons"]


def _fake_ultralytics(monkeypatch, scores: dict[str, tuple[float, float]]):
    """best.pt と比較対象の val に、全体とリレーコイルの mAP50-95 を返させる。"""

    class FakeYOLO:
        def __init__(self, weights):
            self.name = str(weights).rsplit("/", 1)[-1]

        def train(self, *, project, name, **_):
            from pathlib import Path

            weights = Path(project) / name / "weights"
            weights.mkdir(parents=True, exist_ok=True)
            (weights / "best.pt").write_bytes(b"PK\x03\x04-trained")

        def val(self, **_):
            overall, coil = scores["best.pt" if self.name == "best.pt" else "baseline"]
            box = SimpleNamespace(ap_class_index=[0], p=[0.9], r=[0.8], ap50=[0.95], ap=[coil])
            return SimpleNamespace(results_dict={MAP: overall}, box=box, names={0: "relay_coil"})

    monkeypatch.setattr(training_service, "ultralytics_available", lambda: True)
    monkeypatch.setitem(sys.modules, "ultralytics", SimpleNamespace(YOLO=FakeYOLO))


def test_training_run_records_per_class_metrics_and_recommendation(client, monkeypatch):
    ids = _drawings(client, 6)
    _upload_model(client)
    _fake_ultralytics(monkeypatch, {"best.pt": (0.50, 0.40), "baseline": (0.60, 0.70)})

    run_id = client.post("/api/ml/training/run", json={"project_ids": ids, "epochs": 1}).json()["id"]
    _wait_for_run(run_id)
    run = client.get(f"/api/ml/training/runs/{run_id}").json()

    assert run["status"] == "success", run["log_tail"]
    coil = run["per_class"]["relay_coil"]
    # 評価用の図面は 1 枚で、リレーコイルが 1 件ある。表示名はクラスマスタの名前。
    assert coil["instances"] == 1 and coil["map50_95"] == 0.4 and coil["label"] != "relay_coil"
    assert run["baseline_per_class"]["relay_coil"]["map50_95"] == 0.7
    assert "per_class" not in run["metrics"] and run["metrics"][MAP] == "0.5000"
    assert run["review"]["verdict"] == "hold"
    assert all("per_class" not in (m["metrics"] or {}) for m in client.get("/api/ml/models").json())


def _annotate(client, pid: int, boxes: list[tuple[str, float, float]], status: str = "done") -> None:
    symbols = [{"class_key": key, "cx": cx, "cy": cy, "w": 0.05, "h": 0.05} for key, cx, cy in boxes]
    assert client.put(f"/api/projects/{pid}/annotations", json={"symbols": symbols, "connections": []}).status_code == 200
    assert client.put(f"/api/projects/{pid}/meta", json={"status": status}).status_code == 200


def test_class_stats_counts_done_drawings_and_marks_thin_classes(client):
    done, draft = create_project(client, "a.png"), create_project(client, "b.png")
    _annotate(client, done, [("relay_coil", 0.2, 0.2), ("relay_coil", 0.6, 0.2), ("contact_a", 0.4, 0.6)])
    _annotate(client, draft, [("contact_a", 0.4, 0.6)], status="draft")

    stats = client.get("/api/ml/training/class-stats").json()
    rows = {row["key"]: row for row in stats["classes"]}
    assert stats["done_drawings"] == 1
    assert rows["relay_coil"]["done_count"] == 2 and rows["relay_coil"]["done_drawings"] == 1
    assert rows["contact_a"]["done_count"] == 1 and rows["contact_a"]["total_count"] == 2
    assert rows["contact_a"]["total_drawings"] == 2
    assert all(row["thin"] for row in stats["classes"])  # どれも 30 件未満


def test_hard_drawings_rank_done_drawings_by_ai_agreement(client):
    classes = {c["key"]: c["yolo_index"] for c in client.get("/api/classes").json()}
    good, hard = create_project(client, "good.png"), create_project(client, "hard.png")
    for pid in (good, hard):
        _annotate(client, pid, [("relay_coil", 0.2, 0.2), ("contact_a", 0.6, 0.6)])
    lines = {
        good: [f"{classes['relay_coil']} 0.2 0.2 0.05 0.05 0.9", f"{classes['contact_a']} 0.6 0.6 0.05 0.05 0.9"],
        # 1 件は種別違い、1 件は何も無い位置。正解の接点は見落とし。
        hard: [f"{classes['contact_a']} 0.2 0.2 0.05 0.05 0.9", f"{classes['connector']} 0.9 0.9 0.05 0.05 0.9"],
    }
    for pid, rows in lines.items():
        res = client.post(
            "/api/ml/inference/import",
            files={"archive": ("p.zip", _predictions_zip(pid, rows), "application/zip")},
        )
        assert res.status_code == 200, res.text

    body = client.get("/api/ml/training/hard-drawings").json()
    first, second = body["drawings"]
    assert first["project_id"] == hard and first["hard"] is True and first["f1"] == 0.0
    assert (first["missed"], first["false_detections"], first["relabeled"]) == (1, 1, 1)
    assert second["project_id"] == good and second["f1"] == 1.0 and second["hard"] is False

