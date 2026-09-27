"""AI 改善サイクル API（モデル管理・推論・学習）。

推論 → 結果表示（JSON）→ エディタで修正 → データ蓄積 → 学習 → 改善の導線。
エラーは日本語メッセージで返す。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from database import get_db
from models import AnnotationProject, MlModel, TrainingRun
from routers.common import get_project_or_404, safe_upload_name
from schemas import InferenceRunRequest, TrainingRunRequest
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
from services.training_service import running_training, serialize_run, start_training

router = APIRouter(prefix="/api/ml", tags=["ml"])


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
    try:
        results = run_inference(db, model, projects, conf=payload.conf)
    except RuntimeError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {
        "model": serialize_model(model),
        "conf": payload.conf,
        "results": results,
        "detection_count": sum(r["detections"] for r in results),
    }


@router.post("/inference/import")
async def inference_import(archive: UploadFile = File(...), db: Session = Depends(get_db)):
    """外部の `yolo predict --save-txt --save-conf` 出力 ZIP を推論結果として取り込む。"""
    name = safe_upload_name(archive, "predictions.zip")
    if not name.lower().endswith(".zip"):
        raise HTTPException(400, "ZIP ファイルを指定してください")
    blob = await archive.read()
    if not blob:
        raise HTTPException(400, "ファイルが空です")
    try:
        return import_predictions_zip(db, blob, source_label=name)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


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
    return [serialize_run(r) for r in runs]


@router.get("/training/runs/{run_id}")
def get_training_run(run_id: int, db: Session = Depends(get_db)):
    run = db.query(TrainingRun).filter(TrainingRun.id == run_id).one_or_none()
    if run is None:
        raise HTTPException(404, "学習ジョブが見つかりません")
    return serialize_run(run)
