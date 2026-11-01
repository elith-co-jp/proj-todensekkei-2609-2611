"""AI 検出と保存済みアノテーションを突き合わせ、人の確認結果を集計する。"""

from __future__ import annotations

from collections.abc import Sequence
from typing import TypedDict

from sqlalchemy.orm import Session

from models import AnnotationProject
from services.annotation_service import classes_ordered
from services.geometry import denormalize_box

MATCH_IOU = 0.5


class Box(TypedDict):
    class_key: str
    cx: float
    cy: float
    w: float
    h: float


class SavedSymbol(Box):
    origin: str


def _iou(a: Box, b: Box) -> float:
    ax0, ay0, ax1, ay1 = denormalize_box(a["cx"], a["cy"], a["w"], a["h"], 1, 1)
    bx0, by0, bx1, by1 = denormalize_box(b["cx"], b["cy"], b["w"], b["h"], 1, 1)
    ix = min(ax1, bx1) - max(ax0, bx0)
    iy = min(ay1, by1) - max(ay0, by0)
    if ix <= 0 or iy <= 0:
        return 0.0
    inter = ix * iy
    return inter / (a["w"] * a["h"] + b["w"] * b["h"] - inter)


def summarize_review(predictions: Sequence[Box], symbols: Sequence[SavedSymbol]) -> dict:
    """AI 検出（シンボル候補）と保存済みシンボルを IoU の高い組から 1 対 1 で対応付ける。

    IoU が MATCH_IOU 未満の組は対応なしとする。削除件数は AI 検出を取り込んだ図面
    （origin=inference のシンボルがある図面）でだけ数える。
    """
    pairs = sorted(
        (
            (iou, p, s)
            for p, prediction in enumerate(predictions)
            for s, symbol in enumerate(symbols)
            if (iou := _iou(prediction, symbol)) >= MATCH_IOU
        ),
        key=lambda pair: pair[0],
        reverse=True,
    )
    matched: dict[int, int] = {}
    matched_symbols: set[int] = set()
    for _, p, s in pairs:
        if p in matched or s in matched_symbols:
            continue
        matched[p] = s
        matched_symbols.add(s)

    kept = sum(1 for p, s in matched.items() if predictions[p]["class_key"] == symbols[s]["class_key"])
    imported = any(symbol["origin"] == "inference" for symbol in symbols)
    return {
        "imported": imported,
        "kept": kept,
        "relabeled": len(matched) - kept,
        "deleted": len(predictions) - len(matched) if imported else 0,
        "manual_added": sum(
            1 for s, symbol in enumerate(symbols) if symbol["origin"] == "manual" and s not in matched_symbols
        ),
    }


def project_review_summary(db: Session, project: AnnotationProject) -> dict:
    key_by_id = {c.id: c.key for c in classes_ordered(db)}

    def box(row) -> Box:
        return {
            "class_key": key_by_id.get(row.class_id, "other"),
            "cx": row.cx,
            "cy": row.cy,
            "w": row.w,
            "h": row.h,
        }

    predictions = [box(p) for p in project.predictions if p.kind == "symbol"]
    symbols: list[SavedSymbol] = [{**box(s), "origin": s.origin} for s in project.symbols]
    return {
        "project_id": project.id,
        "status": project.status,
        "in_next_training": project.status == "done",
        "ai_detection_count": len(predictions),
        "region_excluded_count": sum(1 for p in project.predictions if p.kind == "region"),
        **summarize_review(predictions, symbols),
    }
