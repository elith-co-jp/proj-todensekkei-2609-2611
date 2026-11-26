"""登録したデータを消す（DD-0052）。

消す前に「一緒に消えるもの」「残るもの」「今は消せない理由」を数えて画面の確認に出し、消すときも同じ規則で消す。

- 図面を消すと、アノテーション（記号・端子・配線）・AI の候補・構造解析の結果・図面セットへの所属・ベクター PDF の線・
  その図面を含む回路の変更履歴（取り消しの記録。図面が無いと戻せない）が一緒に消える。画像ファイルは、ほかの図面が同じ画像を
  使っていなければ消す。学習の履歴とモデルは残す（どの図面で学習したかの記録として残し、図面の ID だけが無効になる）。
- 実行中のジョブ（裏の解析・学習）が使っている図面・モデルは消さない（途中で消すとジョブが失敗し、結果が半端に残るため）。
  状態の移り方は services/lifecycles.py にあり、ここは「実行中か」を読むだけで状態は変えない。
"""

from __future__ import annotations

import json
import shutil
from typing import Any

from sqlalchemy import func
from sqlalchemy.orm import Session

from database import IMAGE_DIR, TRAINING_DIR
from models import (
    AnnotationImage,
    AnnotationProject,
    AnnotationSymbol,
    CircuitChangeSet,
    Connection,
    DrawingSetMember,
    MlModel,
    Prediction,
    StructurePrediction,
    SymbolTerminal,
    TrainingRun,
)
from services import analysis_jobs, vector_pdf
from services.annotation_service import image_path


class DeletionBlocked(Exception):
    """実行中のジョブが使っているので、今は消せない（API では 409 にする）。"""


# ------------------------------------------------------------------------------------- 使用中か


def _running_job_projects() -> set[int]:
    ids: set[int] = set()
    for job in analysis_jobs.recent():
        if job.status == "running":
            ids.update(job.project_ids)
    return ids


def _running_training(db: Session) -> TrainingRun | None:
    return db.query(TrainingRun).filter(TrainingRun.status == "running").order_by(TrainingRun.id.desc()).first()


def project_blockers(db: Session, project_ids: list[int], *, training: bool = True) -> list[str]:
    """図面を消せない理由（実行中の解析・学習が使っている）。空なら消せる。training=False なら解析だけ見る。"""
    wanted = set(project_ids)
    reasons: list[str] = []
    if wanted & _running_job_projects():
        reasons.append("AI 解析を実行中の図面が含まれています。解析が終わるか中止してから削除してください。")
    run = _running_training(db) if training else None
    if run is not None and wanted & set(json.loads(run.project_ids_json or "[]")):
        reasons.append(f"学習（ジョブ #{run.id}）に使っている図面が含まれています。学習が終わってから削除してください。")
    return reasons


def model_blockers(db: Session, model: MlModel) -> list[str]:
    reasons: list[str] = []
    if model.is_active and analysis_jobs.running():
        reasons.append("このモデルで AI 解析を実行中です。解析が終わるか中止してから削除してください。")
    run = _running_training(db)
    if run is not None and run.base_model and model.sha256 in run.base_model:
        reasons.append(f"学習（ジョブ #{run.id}）の起点に使っているモデルです。学習が終わってから削除してください。")
    return reasons


# ------------------------------------------------------------------------------------- 図面


def _change_sets_touching(db: Session, project_ids: set[int]) -> list[CircuitChangeSet]:
    return [row for row in db.query(CircuitChangeSet).all()
            if project_ids & set(json.loads(row.project_ids_json or "[]"))]


def _orphan_images(db: Session, project_ids: set[int]) -> set[str]:
    """消す図面だけが使っている画像（sha256）。"""
    mine = {sha for (sha,) in db.query(AnnotationImage.sha256).filter(AnnotationImage.project_id.in_(project_ids))}
    if not mine:
        return set()
    shared = {sha for (sha,) in db.query(AnnotationImage.sha256).filter(
        AnnotationImage.sha256.in_(mine), AnnotationImage.project_id.notin_(project_ids))}
    return mine - shared


def _count(db: Session, model: Any, project_ids: set[int], **filters: Any) -> int:
    q = db.query(func.count()).select_from(model).filter(model.project_id.in_(project_ids))
    for key, value in filters.items():
        q = q.filter(getattr(model, key) == value)
    return int(q.scalar() or 0)


def projects_impact(db: Session, project_ids: list[int]) -> dict[str, Any]:
    """図面を消したとき・解析結果だけを消したときに、何が消えて何が残るか。"""
    projects = db.query(AnnotationProject).filter(AnnotationProject.id.in_(project_ids)).all()
    ids = {p.id for p in projects}
    symbol_ids = db.query(AnnotationSymbol.id).filter(AnnotationSymbol.project_id.in_(ids))
    training_runs = [r.id for r in db.query(TrainingRun).all() if ids & set(json.loads(r.project_ids_json or "[]"))]
    return {
        "project_ids": sorted(ids),
        "names": [p.name for p in sorted(projects, key=lambda p: p.id)],
        "missing_ids": sorted(set(project_ids) - ids),
        "counts": {
            "symbols": _count(db, AnnotationSymbol, ids),
            "terminals": int(db.query(func.count(SymbolTerminal.id)).filter(SymbolTerminal.symbol_id.in_(symbol_ids)).scalar() or 0),
            "connections": _count(db, Connection, ids),
            "predictions": _count(db, Prediction, ids),
            "structures": _count(db, StructurePrediction, ids),
            "drawing_sets": _count(db, DrawingSetMember, ids),
            "change_sets": len(_change_sets_touching(db, ids)),
            "image_files": len(_orphan_images(db, ids)),
            "training_runs": len(training_runs),
        },
        "blockers": project_blockers(db, list(ids)),
        "analysis_blockers": project_blockers(db, list(ids), training=False),  # 解析結果だけを消すとき
    }


def _remove_image_files(shas: set[str]) -> None:
    for sha in shas:
        image_path(sha).unlink(missing_ok=True)
        thumbs = IMAGE_DIR / "thumbs"
        if thumbs.is_dir():
            for path in thumbs.glob(f"{sha}_*"):
                path.unlink(missing_ok=True)


def delete_projects(db: Session, project_ids: list[int]) -> list[int]:
    """図面と、その図面にだけ付くデータを消す。消した図面の ID を返す。"""
    reasons = project_blockers(db, project_ids)
    if reasons:
        raise DeletionBlocked(" ".join(reasons))
    targets = db.query(AnnotationProject).filter(AnnotationProject.id.in_(project_ids)).all()
    ids = {p.id for p in targets}
    if not ids:
        return []
    orphans = _orphan_images(db, ids)
    for row in _change_sets_touching(db, ids):
        db.delete(row)
    for p in targets:
        db.delete(p)
    db.commit()
    for pid in ids:
        vector_pdf.remove(pid)
    _remove_image_files(orphans)
    return sorted(ids)


def clear_analysis(db: Session, project_ids: list[int]) -> dict[str, int]:
    """AI の候補と構造解析の結果だけを消す（図面とアノテーションは残す）。"""
    # 学習は始めた時に画像とアノテーションを書き出すので、解析結果を消しても学習には響かない
    reasons = project_blockers(db, project_ids, training=False)
    if reasons:
        raise DeletionBlocked(" ".join(reasons))
    ids = list({int(i) for i in project_ids})
    predictions = db.query(Prediction).filter(Prediction.project_id.in_(ids)).delete(synchronize_session=False)
    structures = db.query(StructurePrediction).filter(StructurePrediction.project_id.in_(ids)).delete(
        synchronize_session=False)
    db.commit()
    return {"predictions": int(predictions), "structures": int(structures)}


# ------------------------------------------------------------------------------------- 履歴


def delete_training_run(db: Session, run_id: int) -> None:
    """学習の履歴と作業フォルダを消す。できたモデルはモデル管理に残す。"""
    run = db.get(TrainingRun, run_id)
    if run is None:
        raise LookupError("学習の履歴が見つかりません")
    if run.status == "running":
        raise DeletionBlocked("学習中の履歴は削除できません。学習が終わってから削除してください。")
    db.delete(run)
    db.commit()
    shutil.rmtree(TRAINING_DIR / f"run_{run_id}", ignore_errors=True)


def delete_change_set(db: Session, change_set_id: int) -> None:
    """回路の変更履歴を 1 件消す。図面はそのまま（この変更は取り消せなくなる）。"""
    row = db.get(CircuitChangeSet, change_set_id)
    if row is None:
        raise LookupError(f"変更 {change_set_id} がありません")
    db.delete(row)
    db.commit()
