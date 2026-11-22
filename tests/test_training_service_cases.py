"""学習ジョブの前提の確認・評価・失敗の記録。ultralytics は重いので、学習と評価だけ偽物に置き換える。"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

import database
from models import AnnotationImage, AnnotationProject, TrainingRun
from services import training_service as ts
from services.annotation_service import image_path
from tests.conftest import create_project
from tests.test_ml_cycle import _insert_training_run, _upload_model
from tests.test_training_holdout import _drawings, _wait_for_run


def _fake_yolo(monkeypatch, *, write_best=True, val=None, seen=None):
    class FakeYOLO:
        def __init__(self, weights):
            self.weights = str(weights)

        def train(self, *, project, name, **kwargs):
            if seen is not None:
                seen.append(kwargs)
            if write_best:
                weights = Path(project) / name / "weights"
                weights.mkdir(parents=True, exist_ok=True)
                (weights / "best.pt").write_bytes(b"PK\x03\x04-trained")

        def val(self, **kwargs):
            if seen is not None:
                seen.append(kwargs)
            return val(self.weights) if val else SimpleNamespace(results_dict={ts.MAP_KEY: 0.5})

    monkeypatch.setattr(ts, "ultralytics_available", lambda: True)
    monkeypatch.setitem(sys.modules, "ultralytics", SimpleNamespace(YOLO=FakeYOLO))


def test_default_base_model_outside_the_exe(monkeypatch, tmp_path):
    monkeypatch.setattr(ts, "resource_base", lambda: tmp_path)
    assert ts.default_base_model() == ts.DEFAULT_BASE_MODEL
    (tmp_path / "models").mkdir()
    (tmp_path / "models" / ts.DEFAULT_BASE_MODEL).write_bytes(b"pt")
    assert ts.default_base_model() == str(tmp_path / "models" / ts.DEFAULT_BASE_MODEL)


def test_start_training_preconditions(client, monkeypatch):
    _fake_yolo(monkeypatch)
    done = create_project(client)
    create_project(client, "draft.png")
    assert client.put(f"/api/projects/{done}/meta", json={"status": "done"}).status_code == 200
    with database.SessionLocal() as db:
        assert [p.id for p in ts.resolve_training_projects(db, [], only_done=True)] == [done]
        with pytest.raises(ValueError, match="学習対象の図面がありません"):
            ts.start_training(db, [9999], False, 1, 640, None, True)
        db.query(AnnotationImage).filter(AnnotationImage.project_id == done).delete()
        db.commit()
        with pytest.raises(ValueError, match="画像がありません"):
            ts.start_training(db, [done], False, 1, 640, None, True)
    _insert_training_run(status="running", decision="")
    with database.SessionLocal() as db, pytest.raises(ValueError, match="別の学習が実行中"):
        ts.start_training(db, [], True, 1, 640, None, True)


def test_dataset_skips_lost_image_files(client, tmp_path):
    first, second = create_project(client), create_project(client, "lost.png", w=200)
    with database.SessionLocal() as db:
        lost = db.get(AnnotationProject, second)
        image_path(lost.images[0].sha256).unlink()
        with pytest.raises(ValueError, match="学習用画像を出力できませんでした"):
            ts._write_dataset(db, tmp_path / "a", [lost], holdout=False)
        # 1 枚目の図面に、ファイルの消えた画像をもう 1 枚つなぐ
        db.add(AnnotationImage(project_id=first, filename="x.png", sha256=lost.images[0].sha256,
                               width=10, height=10))
        db.commit()
        project = db.get(AnnotationProject, first)
        data_yaml, _scope = ts._write_dataset(db, tmp_path / "b", [project], holdout=False)
    assert len(list((tmp_path / "b" / "dataset" / "images" / "train").glob("*.png"))) == 3  # 元 + 回転 2 枚


def test_metrics_reading_tolerates_odd_values(tmp_path, monkeypatch):
    assert ts._pick_metrics({ts.MAP_KEY: "n/a", "metrics/recall(B)": 0.25}) == {"metrics/recall(B)": "0.2500"}
    run_dir = tmp_path / "run"
    assert ts._read_metrics(run_dir) is None
    csv_path = run_dir / "train" / "results.csv"
    csv_path.parent.mkdir(parents=True)
    csv_path.write_text("epoch, metrics/mAP50-95(B), lr\n", encoding="utf-8")
    assert ts._read_metrics(run_dir) is None
    csv_path.write_text("epoch, metrics/mAP50-95(B), lr\n1, 0.4, 0.01\n", encoding="utf-8")
    assert ts._read_metrics(run_dir) == {"epoch": "1", "metrics/mAP50-95(B)": "0.4"}
    csv_path.write_text("lr, momentum\n0.01, 0.9\n", encoding="utf-8")
    assert ts._read_metrics(run_dir) == {"lr": "0.01", "momentum": "0.9"}

    def unreadable(*_a, **_k):
        raise OSError("locked")

    monkeypatch.setattr(Path, "open", unreadable)
    assert ts._read_metrics(run_dir) is None
    assert ts._loads_or_none("{broken") is None


def test_evaluation_failures_keep_what_they_can(tmp_path, monkeypatch):
    weights = tmp_path / "best.pt"
    assert ts._eval_weights(weights, "data.yaml", 640) is None
    weights.write_bytes(b"pt")
    seen: list[dict] = []

    def broken(_weights):
        raise RuntimeError("cuda")

    _fake_yolo(monkeypatch, val=broken)
    assert ts._eval_weights(weights, "data.yaml", 640) is None
    _fake_yolo(monkeypatch, seen=seen)
    monkeypatch.setattr(ts, "is_frozen", lambda: True)
    # data.yaml が無いのでクラス別の値は取れないが、全体の値は残す
    assert ts._eval_weights(weights, str(tmp_path / "missing.yaml"), 640) == {ts.MAP_KEY: "0.5000"}
    assert seen[-1]["workers"] == 0


def test_first_training_in_the_exe_is_adopted_automatically(client, monkeypatch):
    ids = _drawings(client, 2)
    seen: list[dict] = []
    _fake_yolo(monkeypatch, seen=seen)
    monkeypatch.setattr(ts, "is_frozen", lambda: True)
    monkeypatch.setattr(ts, "default_base_model", lambda: "missing-base.pt")
    run_id = client.post("/api/ml/training/run", json={"project_ids": ids, "epochs": 1}).json()["id"]
    _wait_for_run(run_id)
    run = client.get(f"/api/ml/training/runs/{run_id}").json()
    assert run["status"] == "success", run["log_tail"]
    assert run["decision"] == "adopted" and run["baseline_label"] == "missing-base.pt"
    assert seen[0]["workers"] == 0 and seen[0]["plots"] is False


def test_training_without_best_pt_is_recorded_as_failed(client, monkeypatch):
    # 前の DB で同じ番号の学習が残した best.pt を、今回の成果物と取り違えない
    stale = ts.TRAINING_DIR / "run_1" / "train" / "weights"
    stale.mkdir(parents=True, exist_ok=True)
    (stale / "best.pt").write_bytes(b"PK\x03\x04-old")
    ids = _drawings(client, 2)
    _upload_model(client)
    _fake_yolo(monkeypatch, write_best=False)
    run_id = client.post("/api/ml/training/run", json={"project_ids": ids, "epochs": 1}).json()["id"]
    _wait_for_run(run_id)
    run = client.get(f"/api/ml/training/runs/{run_id}").json()
    assert run["status"] == "failed"
    assert "best.pt が見つかりません" in run["log_tail"]


def test_a_run_deleted_while_failing_is_left_alone(monkeypatch):
    run_id = _insert_training_run(status="running", decision="")

    def vanish(*_args, **_kwargs):
        with database.SessionLocal() as db:
            db.query(TrainingRun).filter(TrainingRun.id == run_id).delete()
            db.commit()
        raise RuntimeError("disk full")

    monkeypatch.setattr(ts, "resolve_training_projects", vanish)
    ts._train(run_id, holdout=True)
    with database.SessionLocal() as db:
        assert db.get(TrainingRun, run_id) is None
