"""学習データの量と質を確かめる（#24）。

- クラス別の件数: 学習に使う図面（完了）にクラスごとの正解が何件あるか。少ないクラスを先に増やすため。
- 難例: AI の検出と、人が直した後の正解の食い違いが大きい図面。正解を優先して見直し・追加するため。
"""

from __future__ import annotations

from sqlalchemy import func
from sqlalchemy.orm import Session

from models import AnnotationProject, AnnotationSymbol
from services.annotation_service import classes_ordered
from services.review_service import review_boxes, summarize_review

# 正解データ 30 枚で b 接点（13 件）の検出 F1 は 0.38〜0.53 にとどまった（DD-0007 §4.11）。
# 30 件未満のクラスは精度が安定しにくい目安として示す。
THIN_CLASS_COUNT = 30
# AI の検出と直した後の正解の一致（種別も含めた F1）がこれ未満の図面を難例とする。
HARD_DRAWING_F1 = 0.9


def class_stats(db: Session) -> dict:
    """クラスごとの正解の件数と、その記号がある図面の数。学習に使う完了の図面と全図面の両方。"""

    def counts(done_only: bool) -> tuple[dict[int, int], dict[int, int]]:
        query = db.query(
            AnnotationSymbol.class_id,
            func.count(AnnotationSymbol.id),
            func.count(func.distinct(AnnotationSymbol.project_id)),
        ).join(AnnotationProject, AnnotationProject.id == AnnotationSymbol.project_id)
        if done_only:
            query = query.filter(AnnotationProject.status == "done")
        rows = query.group_by(AnnotationSymbol.class_id).all()
        return {c: n for c, n, _ in rows}, {c: d for c, _, d in rows}

    done, done_drawings = counts(True)
    total, total_drawings = counts(False)
    classes = [
        {
            "key": c.key,
            "label": c.label,
            "color": c.color,
            "done_count": done.get(c.id, 0),
            "done_drawings": done_drawings.get(c.id, 0),
            "total_count": total.get(c.id, 0),
            "total_drawings": total_drawings.get(c.id, 0),
            "thin": done.get(c.id, 0) < THIN_CLASS_COUNT,
        }
        for c in classes_ordered(db)
        if c.is_active
    ]
    return {
        "done_drawings": db.query(AnnotationProject).filter(AnnotationProject.status == "done").count(),
        "thin_threshold": THIN_CLASS_COUNT,
        "classes": classes,
    }


def hard_drawings(db: Session) -> dict:
    """完了の図面ごとに、AI の検出と保存済みの正解の一致を求め、一致の低い順に並べる。"""
    rows = []
    for project in db.query(AnnotationProject).filter(AnnotationProject.status == "done").all():
        predictions, symbols, _analysis = review_boxes(db, project)
        if not predictions or not symbols:
            continue
        review = summarize_review(predictions, symbols)
        matched = review["kept"] + review["relabeled"]
        f1 = 2 * review["kept"] / (len(predictions) + len(symbols))
        rows.append({
            "project_id": project.id,
            "name": project.name,
            "sheet_no": project.sheet_no,
            "f1": round(f1, 4),
            "ai_count": len(predictions),
            "gold_count": len(symbols),
            "missed": len(symbols) - matched,
            "false_detections": len(predictions) - matched,
            "relabeled": review["relabeled"],
            "hard": f1 < HARD_DRAWING_F1,
        })
    rows.sort(key=lambda row: (row["f1"], row["project_id"]))
    return {"threshold": HARD_DRAWING_F1, "drawings": rows}

