"""学習時に図面単位で評価用を分け、未学習の図面で新旧モデルを比べることを確認する。"""

from __future__ import annotations

import sys
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml
from sqlalchemy import text

import database
from models import TrainingRun
from services import training_service
from tests.conftest import create_project
from tests.test_ml_cycle import _upload_model

ONE_SYMBOL = {
    "symbols": [{"class_key": "relay_coil", "cx": 0.5, "cy": 0.5, "w": 0.1, "h": 0.1}],
    "connections": [],
}


def _drawings(client, n: int) -> list[int]:
    ids = []
    for i in range(n):
        pid = create_project(client, f"sheet{i}.png")
        assert client.put(f"/api/projects/{pid}/annotations", json=ONE_SYMBOL).status_code == 200
        ids.append(pid)
    return ids


def _write(ids: list[int], run_dir: Path, holdout: bool):
    with database.SessionLocal() as db:
        projects = training_service.resolve_training_projects(db, ids, only_done=False)
        data_yaml, scope = training_service._write_dataset(db, run_dir, projects, holdout)
    return yaml.safe_load(Path(data_yaml).read_text(encoding="utf-8")), scope


def _project_ids_in(folder: Path, suffix: str) -> list[int]:
    return sorted(int(f.name.split("_", 1)[0][1:]) for f in folder.glob(f"*{suffix}"))


def test_wire_bend_marks_are_left_out_of_training_labels(client, tmp_path):
    pid = create_project(client, "sheet0.png")
    annotation = {"symbols": [
        {"class_key": "relay_coil", "label": "33HB", "cx": 0.5, "cy": 0.5, "w": 0.1, "h": 0.1},
        {"class_key": "other", "label": "分岐点", "cx": 0.2, "cy": 0.2, "w": 0.02, "h": 0.02},
        {"class_key": "other", "label": "カード", "cx": 0.8, "cy": 0.8, "w": 0.1, "h": 0.1},
    ], "connections": []}
    assert client.put(f"/api/projects/{pid}/annotations", json=annotation).status_code == 200
    _write([pid], tmp_path, holdout=False)

    lines = next((tmp_path / "dataset" / "labels" / "train").glob("*.txt")).read_text().splitlines()
    assert sorted(line.split()[1] for line in lines) == ["0.500000", "0.800000"]


def test_six_drawings_hold_one_out_for_evaluation(client, tmp_path):
    ids = _drawings(client, 6)
    data, scope = _write(ids, tmp_path, holdout=True)

    dataset = tmp_path / "dataset"
    train_ids = _project_ids_in(dataset / "images" / "train", ".png")
    val_ids = _project_ids_in(dataset / "images" / "val", ".png")
    assert len(val_ids) == 1 and len(train_ids) == 5
    assert sorted(train_ids + val_ids) == ids
    assert _project_ids_in(dataset / "labels" / "val", ".txt") == val_ids
    assert _project_ids_in(dataset / "labels" / "train", ".txt") == train_ids
    assert (data["train"], data["val"]) == ("images/train", "images/val")
    assert scope == {"mode": "holdout", "train_project_ids": train_ids, "val_project_ids": val_ids}

    _, again = _write(ids, tmp_path / "again", holdout=True)
    assert again == scope


@pytest.mark.parametrize(("n", "holdout"), [(3, True), (6, False)])
def test_few_drawings_or_holdout_off_evaluate_on_training_drawings(client, tmp_path, n, holdout):
    ids = _drawings(client, n)
    data, scope = _write(ids, tmp_path, holdout=holdout)

    dataset = tmp_path / "dataset"
    assert _project_ids_in(dataset / "images" / "train", ".png") == ids
    assert not (dataset / "images" / "val").exists()
    assert (data["train"], data["val"]) == ("images/train", "images/train")
    assert scope == {"mode": "train", "train_project_ids": ids, "val_project_ids": ids}


def _fake_ultralytics(monkeypatch, evaluated: list[tuple[str, str]]):
    class FakeYOLO:
        def __init__(self, weights):
            self.weights = str(weights)

        def train(self, *, project, name, **_):
            weights = Path(project) / name / "weights"
            weights.mkdir(parents=True, exist_ok=True)
            (weights / "best.pt").write_bytes(b"PK\x03\x04-trained")

        def val(self, *, data, **_):
            evaluated.append((Path(self.weights).name, yaml.safe_load(Path(data).read_text())["val"]))
            return SimpleNamespace(results_dict={"metrics/mAP50-95(B)": 0.5})

    monkeypatch.setattr(training_service, "ultralytics_available", lambda: True)
    monkeypatch.setitem(sys.modules, "ultralytics", SimpleNamespace(YOLO=FakeYOLO))


def _wait_for_run(run_id: int) -> None:
    for thread in threading.enumerate():
        if thread.name == f"training-run-{run_id}":
            thread.join(timeout=30)


@pytest.mark.parametrize(
    ("payload", "mode", "val_path"),
    [({}, "holdout", "images/val"), ({"holdout": False}, "train", "images/train")],
)
def test_training_run_compares_models_on_eval_scope(client, monkeypatch, payload, mode, val_path):
    ids = _drawings(client, 6)
    _upload_model(client)
    evaluated: list[tuple[str, str]] = []
    _fake_ultralytics(monkeypatch, evaluated)

    res = client.post("/api/ml/training/run", json={"project_ids": ids, "epochs": 1, **payload})
    assert res.status_code == 200, res.text
    run_id = res.json()["id"]
    _wait_for_run(run_id)

    run = client.get(f"/api/ml/training/runs/{run_id}").json()
    assert run["status"] == "success", run["log_tail"]
    scope = run["eval_scope"]
    assert scope["mode"] == mode
    assert sorted(set(scope["train_project_ids"]) | set(scope["val_project_ids"])) == ids
    assert len(scope["val_project_ids"]) == (1 if mode == "holdout" else 6)
    assert run["baseline_metrics"] is not None
    assert len(evaluated) == 2 and "best.pt" in [name for name, _ in evaluated]
    assert {path for _, path in evaluated} == {val_path}


def test_existing_db_upgrade_adds_eval_scope(client):
    with database.engine.begin() as conn:
        conn.execute(text("ALTER TABLE training_runs DROP COLUMN eval_scope_json"))

    database._migrate_columns()

    with database.SessionLocal() as db:
        db.add(TrainingRun(status="failed", project_ids_json="[]"))
        db.commit()
    runs = client.get("/api/ml/training/runs").json()
    assert runs[0]["eval_scope"] is None
