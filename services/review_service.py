"""AI 検出と保存済みアノテーションを突き合わせ、人の確認結果を集計する。"""

from __future__ import annotations

from collections.abc import Sequence
from typing import TypedDict

from sqlalchemy.orm import Session

from models import AnnotationProject
from services.ai_candidates import AiCandidates, plan_import
from services.ai_import_service import project_candidates
from services.annotation_service import classes_ordered, serialize_project
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


def summarize_wiring(
    candidates: AiCandidates, symbols: Sequence[dict], connections: Sequence[dict], width: int, height: int
) -> dict:
    """AI の端子・配線のうち、保存済みの内容に残っているもの・消されたもの・人が足したものを数える。

    AI の件数は、空の図面に取り込んだときに追加される件数。残っているかは取り込みと同じ対応付けで判定する。
    削除件数は AI の端子・配線を取り込んだ図面（origin=inference のものがある図面）でだけ数える。
    """
    empty = plan_import(candidates, [], [], width, height)
    current = plan_import(candidates, symbols, connections, width, height)
    terminals = [t for s in symbols for t in s.get("terminals") or []]
    ai_terminals = sum(len(s["terminals"]) for s in empty.symbols)
    ai_connections = len(empty.connections)
    terminals_imported = any(t.get("origin") == "inference" for t in terminals)
    connections_imported = any(c.get("origin") == "inference" for c in connections)
    return {
        "terminals": {
            "ai_count": ai_terminals,
            "imported": terminals_imported,
            "kept": current.kept_terminals,
            "deleted": ai_terminals - current.kept_terminals if terminals_imported else 0,
            "manual_added": sum(
                1 for t in terminals
                if t.get("origin", "manual") == "manual" and t["ref"] not in current.reused_terminal_refs
            ),
        },
        "connections": {
            "ai_count": ai_connections,
            "imported": connections_imported,
            "kept": current.kept_connections,
            "deleted": ai_connections - current.kept_connections if connections_imported else 0,
            "manual_added": sum(
                1 for i, c in enumerate(connections)
                if c.get("origin", "manual") == "manual" and i not in current.reused_connection_indexes
            ),
        },
    }


def project_review_summary(db: Session, project: AnnotationProject) -> dict:
    """構造解析の結果があれば、取り込みで候補にしたシンボル（重複した黒丸などを除いたもの）と比べる。"""
    key_by_id = {c.id: c.key for c in classes_ordered(db)}

    def box(row) -> Box:
        return {
            "class_key": key_by_id.get(row.class_id, "other"),
            "cx": row.cx,
            "cy": row.cy,
            "w": row.w,
            "h": row.h,
        }

    candidates = project_candidates(db, project)
    analysis = candidates if candidates is not None and candidates.source == "structure" else None
    if analysis is not None:
        predictions: list[Box] = [
            {"class_key": a.class_key, "cx": a.cx, "cy": a.cy, "w": a.w, "h": a.h} for a in analysis.symbols
        ]
    else:
        predictions = [box(p) for p in project.predictions if p.kind == "symbol"]
    symbols: list[SavedSymbol] = [{**box(s), "origin": s.origin} for s in project.symbols]
    wiring = None
    if analysis is not None:
        saved = serialize_project(db, project, with_image_meta=False)
        wiring = summarize_wiring(analysis, saved["symbols"], saved["connections"], project.image_width, project.image_height)
    return {
        "project_id": project.id,
        "status": project.status,
        "in_next_training": project.status == "done",
        "ai_detection_count": len(predictions),
        "region_excluded_count": sum(1 for p in project.predictions if p.kind == "region"),
        **summarize_review(predictions, symbols),
        "wiring": wiring,
    }
