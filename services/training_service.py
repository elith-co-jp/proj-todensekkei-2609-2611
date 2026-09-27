"""学習実行サービス。

蓄積済みアノテーションからデータセットを組み立て、バックグラウンドスレッドで
ultralytics の学習を実行する。完了すると best.pt を `MlModel`（source='trained'）として
登録し、学習に使った蓄積データで新旧モデルを評価して指標を記録する。
学習済みモデルは `decision='pending'`（採用待ち）で止め、利用者が比較結果を見て
採用を選んだときだけ active に切り替える。

ultralytics が入っていない環境では `start_training` が ValueError を投げ、
UI は「エクスポート → 外部で学習 → best.pt をアップロード」の代替導線を案内する。
"""

from __future__ import annotations

import contextlib
import csv
import io
import json
import threading
import traceback
from pathlib import Path

import yaml
from sqlalchemy.orm import Session

from database import TRAINING_DIR, SessionLocal
from models import AnnotationProject, MlModel, TrainingRun, utc_now_naive
from runtime import is_frozen, resource_base
from services.annotation_service import classes_ordered, image_path
from services.export_service import build_label_content, project_image_stem
from services.inference_service import ultralytics_available
from services.model_service import (
    activate,
    model_path,
    next_version,
    serialize_model,
    store_model_bytes,
)

DEFAULT_BASE_MODEL = "yolov8n.pt"
_LOG_TAIL_LIMIT = 4000


def default_base_model() -> str:
    """Use the verified packaged weight without a runtime download in the exe."""
    bundled = resource_base() / "models" / DEFAULT_BASE_MODEL
    if bundled.is_file():
        return str(bundled)
    if is_frozen():
        raise FileNotFoundError("同梱した学習用モデルが見つかりません。exe を再取得してください")
    return DEFAULT_BASE_MODEL


def running_training(db: Session) -> TrainingRun | None:
    return (
        db.query(TrainingRun)
        .filter(TrainingRun.status == "running")
        .order_by(TrainingRun.id.desc())
        .first()
    )


def resolve_training_projects(
    db: Session, project_ids: list[int], only_done: bool
) -> list[AnnotationProject]:
    q = db.query(AnnotationProject)
    if project_ids:
        q = q.filter(AnnotationProject.id.in_(project_ids))
    elif only_done:
        q = q.filter(AnnotationProject.status == "done")
    return q.order_by(AnnotationProject.id.asc()).all()


def start_training(
    db: Session,
    project_ids: list[int],
    only_done: bool,
    epochs: int,
    imgsz: int,
    base_model: str | None,
) -> TrainingRun:
    if not ultralytics_available():
        raise ValueError(
            "ultralytics がインストールされていないため、この環境では学習を実行できません。"
            "ZIP をエクスポートして外部で学習し、作成された best.pt をモデルとして登録してください。"
        )
    if running_training(db):
        raise ValueError("別の学習が実行中です。完了を待ってから再実行してください")
    projects = resolve_training_projects(db, project_ids, only_done)
    if not projects:
        raise ValueError("学習対象の図面がありません（完了状態の図面を用意するか、対象を選択してください）")
    image_total = sum(len(p.images) for p in projects)
    if image_total == 0:
        raise ValueError("学習対象の図面に画像がありません")

    run = TrainingRun(
        status="running",
        project_ids_json=json.dumps([p.id for p in projects]),
        image_count=image_total,
        epochs=epochs,
        imgsz=imgsz,
        base_model=base_model or "",
    )
    db.add(run)
    db.commit()
    db.refresh(run)

    thread = threading.Thread(target=_run_training, args=(run.id,), name=f"training-run-{run.id}", daemon=True)
    thread.start()
    return run


def _write_dataset(db: Session, run_dir, projects: list[AnnotationProject]) -> str:
    """data/training/run_<id>/dataset に ultralytics 標準構成を書き出し、data.yaml のパスを返す。"""
    dataset_dir = run_dir / "dataset"
    images_dir = dataset_dir / "images" / "train"
    labels_dir = dataset_dir / "labels" / "train"
    images_dir.mkdir(parents=True, exist_ok=True)
    labels_dir.mkdir(parents=True, exist_ok=True)

    written = 0
    for project in projects:
        label_content = build_label_content(db, project)
        for image in project.images:
            src = image_path(image.sha256)
            if not src.exists():
                continue
            base = project_image_stem(project, image)
            (images_dir / f"{base}.png").write_bytes(src.read_bytes())
            (labels_dir / f"{base}.txt").write_text(label_content, encoding="utf-8")
            written += 1
    if written == 0:
        raise ValueError("学習用画像を出力できませんでした")

    names = {c.yolo_index: c.key for c in classes_ordered(db)}
    data_yaml = {
        "path": str(dataset_dir),
        "train": "images/train",
        "val": "images/train",
        "nc": len(names),
        "names": {int(k): v for k, v in sorted(names.items())},
    }
    data_yaml_path = run_dir / "data.yaml"
    data_yaml_path.write_text(yaml.safe_dump(data_yaml, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return str(data_yaml_path)


_METRIC_KEYS = (
    "metrics/precision(B)",
    "metrics/recall(B)",
    "metrics/mAP50(B)",
    "metrics/mAP50-95(B)",
)


def _pick_metrics(results_dict: dict) -> dict | None:
    """ultralytics の val 結果から主要メトリクスだけを文字列で取り出す。"""
    picked = {}
    for key in _METRIC_KEYS:
        if key in results_dict:
            try:
                picked[key] = f"{float(results_dict[key]):.4f}"
            except (TypeError, ValueError):
                continue
    return picked or None


def _eval_weights(weights: Path, data_yaml: str, imgsz: int) -> dict | None:
    """蓄積データの data.yaml で val を実行し主要メトリクスを返す。失敗時は None。"""
    if not weights.exists():
        return None
    try:
        from ultralytics import YOLO  # 重量級のため遅延 import

        kwargs: dict = {"data": data_yaml, "imgsz": imgsz, "verbose": False, "plots": False}
        if is_frozen():
            kwargs["workers"] = 0
        results = YOLO(str(weights)).val(**kwargs)
        return _pick_metrics(getattr(results, "results_dict", {}) or {})
    except Exception as exc:  # noqa: BLE001  評価だけ落ちても学習自体は成功として残す
        print(f"[eval] {weights.name} の評価に失敗: {exc}")
        return None


def _baseline_label(db: Session, base: str) -> str:
    """比較対象モデルの表示名。登録済みモデルなら名称+バージョン、それ以外はファイル名。"""
    if not base:
        return "初期モデル"
    known = db.query(MlModel).filter(MlModel.sha256 == Path(base).stem).one_or_none()
    if known is not None:
        return f"{known.name} v{known.version}"
    return Path(base).name


def _read_metrics(run_dir) -> dict | None:
    results_csv = run_dir / "train" / "results.csv"
    if not results_csv.exists():
        return None
    try:
        with results_csv.open(newline="", encoding="utf-8") as fh:
            rows = list(csv.DictReader(fh))
        if not rows:
            return None
        last = {k.strip(): v.strip() for k, v in rows[-1].items()}
        picked = {
            key: last[key]
            for key in last
            if key.startswith("metrics/") or key in ("epoch", "train/box_loss", "train/cls_loss")
        }
        return picked or last
    except OSError:
        return None


def _run_training(run_id: int) -> None:
    db = SessionLocal()
    log_buf = io.StringIO()
    try:
        run = db.query(TrainingRun).filter(TrainingRun.id == run_id).one()
        projects = resolve_training_projects(db, json.loads(run.project_ids_json), only_done=False)
        run_dir = TRAINING_DIR / f"run_{run.id}"
        run_dir.mkdir(parents=True, exist_ok=True)

        data_yaml = _write_dataset(db, run_dir, projects)

        base = run.base_model
        if not base:
            active = db.query(MlModel).filter(MlModel.is_active.is_(True)).one_or_none()
            base = str(model_path(active.sha256)) if active else default_base_model()
        run.base_model = base
        db.commit()

        from ultralytics import YOLO  # 重量級のため遅延 import

        print(f"[training-run-{run.id}] dataset={data_yaml} base={base}", file=log_buf)
        with contextlib.redirect_stdout(log_buf), contextlib.redirect_stderr(log_buf):
            yolo = YOLO(base)
            train_args = {
                "data": data_yaml,
                "epochs": run.epochs,
                "imgsz": run.imgsz,
                "project": str(run_dir),
                "name": "train",
                "exist_ok": True,
            }
            if is_frozen():
                # Windows の凍結 exe から DataLoader の子プロセスを起動しない。
                train_args["workers"] = 0
                train_args["plots"] = False
            yolo.train(**train_args)

            best = run_dir / "train" / "weights" / "best.pt"
            if not best.exists():
                raise RuntimeError("学習は完了しましたが best.pt が見つかりません")

            # 蓄積データ（この run のデータセット）で新旧モデルを評価し、採用判定の材料にする
            print(f"[training-run-{run.id}] evaluate candidate {best}", file=log_buf)
            eval_metrics = _eval_weights(best, data_yaml, run.imgsz)
            print(f"[training-run-{run.id}] evaluate baseline {base}", file=log_buf)
            baseline_metrics = (
                _eval_weights(Path(base), data_yaml, run.imgsz) if base else None
            )

        digest, size = store_model_bytes(best.read_bytes(), f"run_{run.id}_best.pt")
        metrics = eval_metrics or _read_metrics(run_dir)
        model = MlModel(
            name=f"trained_run_{run.id}",
            version=next_version(db),
            file_name=f"run_{run.id}_best.pt",
            sha256=digest,
            size_bytes=size,
            source="trained",
            is_active=False,
            metrics_json=json.dumps(metrics, ensure_ascii=False) if metrics else None,
            note=f"epochs={run.epochs} imgsz={run.imgsz} base={base}",
        )
        db.add(model)
        db.flush()

        run.baseline_metrics_json = (
            json.dumps(baseline_metrics, ensure_ascii=False) if baseline_metrics else None
        )
        run.baseline_label = _baseline_label(db, base)
        has_active = db.query(MlModel).filter(MlModel.is_active.is_(True)).count() > 0
        if has_active or baseline_metrics is not None:
            # 新旧の比較結果を見てから採用を決められるよう、採用待ちで止める
            run.decision = "pending"
        else:
            # 比較対象を用意できない初回のみ従来どおり自動適用
            activate(db, model)
            run.decision = "adopted"

        run.status = "success"
        run.result_model_id = model.id
        run.metrics_json = model.metrics_json
        run.finished_at = utc_now_naive()
        run.log_tail = (log_buf.getvalue() or "")[-_LOG_TAIL_LIMIT:]
        db.commit()
    except Exception:  # noqa: BLE001  失敗理由を履歴へ残す
        db.rollback()
        run = db.query(TrainingRun).filter(TrainingRun.id == run_id).one_or_none()
        if run is not None:
            run.status = "failed"
            run.finished_at = utc_now_naive()
            tail = (log_buf.getvalue() or "") + "\n" + traceback.format_exc()
            run.log_tail = tail[-_LOG_TAIL_LIMIT:]
            db.commit()
    finally:
        db.close()


def _loads_or_none(raw: str | None) -> dict | None:
    if not raw:
        return None
    try:
        return json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return None


def serialize_run(run: TrainingRun) -> dict:
    return {
        "id": run.id,
        "status": run.status,
        "project_ids": json.loads(run.project_ids_json or "[]"),
        "image_count": run.image_count,
        "epochs": run.epochs,
        "imgsz": run.imgsz,
        "base_model": run.base_model,
        "result_model_id": run.result_model_id,
        "result_model": serialize_model(run.result_model) if run.result_model else None,
        "metrics": _loads_or_none(run.metrics_json),
        "baseline_metrics": _loads_or_none(run.baseline_metrics_json),
        "baseline_label": run.baseline_label,
        "decision": run.decision or None,
        "log_tail": run.log_tail,
        "started_at": run.started_at.isoformat() if run.started_at else None,
        "finished_at": run.finished_at.isoformat() if run.finished_at else None,
    }
