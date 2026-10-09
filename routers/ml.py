"""AI 改善サイクル API（モデル管理・推論・学習）。

推論 → 結果表示（JSON）→ エディタで修正 → データ蓄積 → 学習 → 改善の導線。
エラーは日本語メッセージで返す。
"""

from __future__ import annotations

import json
import logging
import threading

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, Response
from sqlalchemy.orm import Session

from database import get_db
from models import AnnotationProject, MlModel, TrainingRun
from routers.common import get_project_or_404, safe_upload_name
from schemas import InferenceRunRequest, TrainingDecisionPayload, TrainingRunRequest
from services.inference_service import (
    import_predictions_zip,
    predictions_payload,
    predictions_summary,
    run_inference,
    ultralytics_available,
)
from services.model_service import (
    activate,
    active_model,
    delete_model,
    model_path,
    next_version,
    serialize_model,
    store_model_bytes,
)
from services.structure_service import document_structure, get_structure
from services.training_service import running_training, serialize_run, start_training

router = APIRouter(prefix="/api/ml", tags=["ml"])
_inference_lock = threading.Lock()
_logger = logging.getLogger(__name__)


def _get_model_or_404(db: Session, model_id: int) -> MlModel:
    m = db.query(MlModel).filter(MlModel.id == model_id).one_or_none()
    if m is None:
        raise HTTPException(404, "モデルが見つかりません")
    return m


# ---------------------------------------------------------------- 状態
@router.get("/status")
def ml_status(db: Session = Depends(get_db)):
    active = active_model(db)
    running = running_training(db)
    return {
        "ultralytics": ultralytics_available(),
        "active_model": serialize_model(active) if active else None,
        "model_count": db.query(MlModel).count(),
        "training_running": serialize_run(running) if running else None,
    }


# ---------------------------------------------------------------- モデル
@router.get("/models")
def list_models(db: Session = Depends(get_db)):
    models = db.query(MlModel).order_by(MlModel.version.desc()).all()
    return [serialize_model(m) for m in models]


@router.post("/models")
async def upload_model(file: UploadFile = File(...), db: Session = Depends(get_db)):
    raw = await file.read()
    name = safe_upload_name(file, "model.pt")
    try:
        digest, size = store_model_bytes(raw, name)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    model = MlModel(
        name=name.rsplit(".", 1)[0] or name,
        version=next_version(db),
        file_name=name,
        sha256=digest,
        size_bytes=size,
        source="upload",
        is_active=False,
    )
    db.add(model)
    db.flush()
    # 初めてのモデルはそのまま active にして導線を短くする
    if db.query(MlModel).filter(MlModel.is_active.is_(True)).count() == 0:
        model.is_active = True
    db.commit()
    return serialize_model(model)


@router.post("/models/{model_id}/activate")
def activate_model(model_id: int, db: Session = Depends(get_db)):
    model = _get_model_or_404(db, model_id)
    activate(db, model)
    db.commit()
    return serialize_model(model)


@router.delete("/models/{model_id}")
def remove_model(model_id: int, db: Session = Depends(get_db)):
    model = _get_model_or_404(db, model_id)
    delete_model(db, model)
    db.commit()
    return {"deleted": model_id}


@router.get("/models/{model_id}/download")
def download_model(model_id: int, db: Session = Depends(get_db)):
    model = _get_model_or_404(db, model_id)
    path = model_path(model.sha256)
    if not path.exists():
        raise HTTPException(404, "モデルファイルが見つかりません")
    return FileResponse(path, media_type="application/octet-stream", filename=model.file_name)


# ---------------------------------------------------------------- 推論
@router.post("/inference/run")
def inference_run(payload: InferenceRunRequest, db: Session = Depends(get_db)):
    return _run_inference(payload, db, include_structure=False)


@router.post("/analysis/run")
def analysis_run(payload: InferenceRunRequest, db: Session = Depends(get_db)):
    return _run_inference(payload, db, include_structure=True)


def _run_inference(payload: InferenceRunRequest, db: Session, *, include_structure: bool):
    model = active_model(db)
    if model is None:
        raise HTTPException(
            400, "推論に使うモデルがありません。先にモデルを登録してください"
        )
    q = db.query(AnnotationProject)
    if payload.project_ids:
        q = q.filter(AnnotationProject.id.in_(payload.project_ids))
    projects = q.order_by(AnnotationProject.id.asc()).all()
    if not projects:
        raise HTTPException(404, "対象のプロジェクトがありません")
    if payload.project_ids and set(payload.project_ids) != {p.id for p in projects}:
        raise HTTPException(404, "対象に存在しない図面が含まれています")
    if not _inference_lock.acquire(blocking=False):
        raise HTTPException(409, "別の解析を実行中です。完了してから再実行してください。")
    try:
        if include_structure:
            results = run_inference(db, model, projects, conf=payload.conf, include_structure=True)
        else:
            results = run_inference(db, model, projects, conf=payload.conf)
    except RuntimeError as exc:
        db.rollback()
        raise HTTPException(400, str(exc)) from exc
    except Exception as exc:
        db.rollback()
        _logger.exception("Inference failed")
        raise HTTPException(500, "解析に失敗しました。保存済みの結果は変更されていません。") from exc
    finally:
        _inference_lock.release()
    return {
        "model": serialize_model(model),
        "conf": payload.conf,
        "results": results,
        "detection_count": sum(r["detections"] for r in results),
    }


@router.get("/projects/{project_id}/structure")
def project_structure(project_id: int, db: Session = Depends(get_db)):
    get_project_or_404(db, project_id)
    return {"project_id": project_id, "result": get_structure(db, project_id)}


@router.post("/analysis/export")
def export_structure(payload: InferenceRunRequest, db: Session = Depends(get_db)):
    query = db.query(AnnotationProject).order_by(AnnotationProject.id)
    if payload.project_ids:
        query = query.filter(AnnotationProject.id.in_(payload.project_ids))
    projects = query.all()
    if not projects or (payload.project_ids and set(payload.project_ids) != {p.id for p in projects}):
        raise HTTPException(404, "対象の図面が見つかりません")
    pages = []
    for project in projects:
        result = get_structure(db, project.id)
        if result is None:
            raise HTTPException(409, f"構造解析が未実行の図面があります: {project.name}")
        pages.append(result)
    return Response(
        json.dumps(document_structure(pages), ensure_ascii=False, indent=2, allow_nan=False),
        media_type="application/json",
        headers={"Content-Disposition": 'attachment; filename="structure.json"'},
    )


@router.post("/inference/import")
async def inference_import(archive: UploadFile = File(...), db: Session = Depends(get_db)):
    """外部の `yolo predict --save-txt --save-conf` 出力 ZIP を推論結果として取り込む。"""
    name = safe_upload_name(archive, "predictions.zip")
    if not name.lower().endswith(".zip"):
        raise HTTPException(400, "ZIP ファイルを指定してください")
    blob = await archive.read()
    if not blob:
        raise HTTPException(400, "ファイルが空です")
    if not _inference_lock.acquire(blocking=False):
        raise HTTPException(409, "解析を実行中です。完了してから取り込んでください。")
    try:
        return import_predictions_zip(db, blob, source_label=name)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    finally:
        _inference_lock.release()


@router.get("/predictions")
def list_predictions(db: Session = Depends(get_db)):
    return predictions_summary(db)


@router.get("/projects/{project_id}/predictions")
def project_predictions(project_id: int, db: Session = Depends(get_db)):
    """結果表示（JSON 出力）：この図面の最新推論結果。"""
    project = get_project_or_404(db, project_id)
    return predictions_payload(db, project)


# ---------------------------------------------------------------- 学習
@router.post("/training/run")
def training_run(payload: TrainingRunRequest, db: Session = Depends(get_db)):
    try:
        run = start_training(
            db,
            project_ids=payload.project_ids,
            only_done=payload.only_done,
            epochs=payload.epochs,
            imgsz=payload.imgsz,
            base_model=payload.base_model,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return serialize_run(run)


@router.get("/training/runs")
def list_training_runs(db: Session = Depends(get_db)):
    runs = db.query(TrainingRun).order_by(TrainingRun.id.desc()).limit(50).all()
    seen = {r.id for r in runs}
    # 履歴上限を超えても採用待ちジョブは必ず返す（判定不能にしないため）
    pending = (
        db.query(TrainingRun)
        .filter(TrainingRun.decision == "pending", ~TrainingRun.id.in_(seen))
        .order_by(TrainingRun.id.desc())
        .all()
    )
    return [serialize_run(r) for r in [*runs, *pending]]


@router.get("/training/runs/{run_id}")
def get_training_run(run_id: int, db: Session = Depends(get_db)):
    run = db.query(TrainingRun).filter(TrainingRun.id == run_id).one_or_none()
    if run is None:
        raise HTTPException(404, "学習ジョブが見つかりません")
    return serialize_run(run)


@router.post("/training/runs/{run_id}/decision")
def decide_training_run(
    run_id: int, payload: TrainingDecisionPayload, db: Session = Depends(get_db)
):
    """学習済みモデルの採用判定。蓄積データでの新旧比較を見て採用/見送りを選ぶ。"""
    run = db.query(TrainingRun).filter(TrainingRun.id == run_id).one_or_none()
    if run is None:
        raise HTTPException(404, "学習ジョブが見つかりません")
    if run.status != "success" or run.result_model_id is None:
        raise HTTPException(400, "採用判定できるのは完了した学習ジョブのみです")
    if run.decision != "pending":
        raise HTTPException(400, "この学習ジョブはすでに判定済みです")
    model = (
        db.query(MlModel).filter(MlModel.id == run.result_model_id).one_or_none()
    )
    if model is None:
        raise HTTPException(400, "成果物のモデルが削除されているため判定できません")
    if payload.decision == "adopt":
        activate(db, model)
        run.decision = "adopted"
    else:
        run.decision = "rejected"
    db.commit()
    db.refresh(run)
    return serialize_run(run)
