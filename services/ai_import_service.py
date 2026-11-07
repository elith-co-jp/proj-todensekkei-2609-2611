"""エディタの「AI 推論」取り込み。図面に保存された AI の結果から候補を作る。

構造解析（AI 解析）の結果があればシンボル・端子・配線を、無ければ推論のシンボルだけを候補にする。
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from models import AnnotationProject
from services.ai_candidates import AiCandidates, from_predictions, from_structure, plan_import
from services.annotation_service import classes_ordered
from services.structure_service import get_structure


def project_candidates(db: Session, project: AnnotationProject) -> AiCandidates | None:
    classes = classes_ordered(db)
    structure = get_structure(db, project.id)
    if structure is not None:
        return from_structure(structure, [c.key for c in classes])
    rows = sorted((p for p in project.predictions if p.kind == "symbol"), key=lambda p: p.id)
    if not rows:
        return None
    key_by_id = {c.id: c.key for c in classes}
    return from_predictions(
        ({"class_key": key_by_id.get(p.class_id, "other"), "cx": p.cx, "cy": p.cy, "w": p.w, "h": p.h,
          "confidence": p.confidence} for p in rows),
        key_by_id.values(),
    )


def import_payload(db: Session, project: AnnotationProject, symbols: list[dict], connections: list[dict]) -> dict:
    """エディタに足す候補と件数。source は取り込み元（structure / predictions / None）。"""
    candidates = project_candidates(db, project)
    counts = {
        "existing_symbols": 0,
        "region_excluded": sum(1 for p in project.predictions if p.kind == "region"),
        "duplicate_dots": 0,
        "text_zone": 0,
    }
    if candidates is None:
        return {"source": None, "symbols": [], "terminals": [], "connections": [], "counts": counts}
    plan = plan_import(candidates, symbols, connections, project.image_width, project.image_height)
    counts.update(existing_symbols=plan.matched_symbols, **candidates.skipped)
    return {
        "source": candidates.source,
        "symbols": plan.symbols,
        "terminals": plan.terminals,
        "connections": plan.connections,
        "counts": counts,
    }
