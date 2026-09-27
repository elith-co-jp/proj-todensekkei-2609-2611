"""アノテーション API。エラーは日本語メッセージで返す。"""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse, Response
from sqlalchemy import func
from sqlalchemy.orm import Session

from database import get_db
from models import (
    AnnotationImage,
    AnnotationProject,
    AnnotationSymbol,
    Connection,
    Prediction,
    SymbolClass,
    SymbolTerminal,
)
from routers.common import get_project_or_404, safe_upload_name
from schemas import (
    AnnotationUpdatePayload,
    BulkIdsRequest,
    ProjectMetaPayload,
    SymbolClassPayload,
    YoloExportRequest,
)
from services.annotation_service import (
    classes_ordered,
    ensure_class,
    image_path,
    replace_annotations,
    serialize_project,
    store_image_bytes,
)
from services.export_service import import_zip, write_export_zip
from services.pdf_service import render_pdf_pages

router = APIRouter(prefix="/api", tags=["annotation"])


def _stamp() -> str:
    return datetime.now(UTC).strftime("%Y%m%d_%H%M%S")


def _collect(db: Session, ids: list[int]) -> list[AnnotationProject]:
    q = db.query(AnnotationProject)
    if ids:
        q = q.filter(AnnotationProject.id.in_(ids))
    projects = q.order_by(AnnotationProject.id.asc()).all()
    if not projects:
        raise HTTPException(404, "対象のプロジェクトがありません")
    return projects


# ---------------------------------------------------------------- クラスマスタ
@router.get("/classes")
def list_classes(db: Session = Depends(get_db)):
    return [
        {
            "id": c.id,
            "key": c.key,
            "label": c.label,
            "yolo_index": c.yolo_index,
            "color": c.color,
            "is_active": bool(c.is_active),
            "sort_order": c.sort_order,
        }
        for c in classes_ordered(db)
    ]


@router.post("/classes")
def create_class(payload: SymbolClassPayload, db: Session = Depends(get_db)):
    if db.query(SymbolClass).filter(SymbolClass.key == payload.key).count():
        raise HTTPException(400, "同じキーのクラスが既に存在します")
    cls = ensure_class(db, payload.key, payload.label, payload.color)
    cls.sort_order = payload.sort_order
    cls.is_active = payload.is_active
    db.commit()
    return {"id": cls.id, "key": cls.key, "label": cls.label, "yolo_index": cls.yolo_index}


@router.put("/classes/{class_id}")
def update_class(class_id: int, payload: SymbolClassPayload, db: Session = Depends(get_db)):
    cls = db.query(SymbolClass).filter(SymbolClass.id == class_id).one_or_none()
    if cls is None:
        raise HTTPException(404, "クラスが見つかりません")
    dup = db.query(SymbolClass).filter(SymbolClass.key == payload.key, SymbolClass.id != class_id)
    if dup.count():
        raise HTTPException(400, "同じキーのクラスが既に存在します")
    cls.key, cls.label, cls.color = payload.key, payload.label, payload.color
    cls.is_active, cls.sort_order = payload.is_active, payload.sort_order
    db.commit()
    return {"id": cls.id, "key": cls.key, "yolo_index": cls.yolo_index}


# ---------------------------------------------------------------- プロジェクト
@router.post("/projects")
async def create_project(
    files: list[UploadFile] = File(...),
    db: Session = Depends(get_db),
):
    """画像または PDF をアップロードし、1 ページ = 1 プロジェクトとして登録する。"""
    if not files:
        raise HTTPException(400, "画像が含まれていません")
    created = []
    for f in files:
        raw = await f.read()
        name = safe_upload_name(f, "図面")
        is_pdf = (
            name.lower().endswith(".pdf")
            or (f.content_type or "").lower() == "application/pdf"
            or raw[:1024].lstrip().startswith(b"%PDF-")
        )
        try:
            pages = render_pdf_pages(raw) if is_pdf else [raw]
            stem = name.rsplit(".", 1)[0] or "図面"
            for index, page in enumerate(pages, start=1):
                digest, w, h = store_image_bytes(page)
                page_name = f"{stem} - {index:03d}" if is_pdf else stem
                image_name = f"{stem}_page_{index:03d}.png" if is_pdf else name
                p = AnnotationProject(
                    name=page_name,
                    page_no=str(index) if is_pdf else None,
                    source_file=name,
                    image_width=w,
                    image_height=h,
                    status="draft",
                )
                db.add(p)
                db.flush()
                db.add(
                    AnnotationImage(
                        project_id=p.id,
                        filename=image_name,
                        sha256=digest,
                        width=w,
                        height=h,
                    )
                )
                created.append(p)
        except ValueError as exc:
            db.rollback()
            raise HTTPException(400, str(exc)) from exc
    db.commit()
    return {"project_ids": [p.id for p in created], "count": len(created)}


@router.get("/projects")
def list_projects(q: str | None = Query(default=None), db: Session = Depends(get_db)):
    query = db.query(AnnotationProject)
    if q:
        if q.isdigit():
            query = query.filter(AnnotationProject.id == int(q))
        else:
            like = f"%{q}%"
            query = query.filter(
                AnnotationProject.name.ilike(like) | AnnotationProject.sheet_no.ilike(like)
            )
    # 件数は行ごとの relationship 読み込みではなく集計クエリで取る（N+1 回避）
    def _counts(model, key_col):
        return dict(
            db.query(key_col, func.count(model.id)).group_by(key_col).all()
        )

    symbol_counts = _counts(AnnotationSymbol, AnnotationSymbol.project_id)
    connection_counts = _counts(Connection, Connection.project_id)
    prediction_counts = _counts(Prediction, Prediction.project_id)
    terminal_counts = dict(
        db.query(AnnotationSymbol.project_id, func.count(SymbolTerminal.id))
        .join(SymbolTerminal, SymbolTerminal.symbol_id == AnnotationSymbol.id)
        .group_by(AnnotationSymbol.project_id)
        .all()
    )
    out = []
    for p in query.order_by(AnnotationProject.id.desc()).all():
        out.append(
            {
                "id": p.id,
                "name": p.name,
                "sheet_no": p.sheet_no,
                "page_no": p.page_no,
                "revision": p.revision,
                "status": p.status,
                "assignee": p.assignee,
                "image_width": p.image_width,
                "image_height": p.image_height,
                "symbol_count": symbol_counts.get(p.id, 0),
                "connection_count": connection_counts.get(p.id, 0),
                "terminal_count": terminal_counts.get(p.id, 0),
                "prediction_count": prediction_counts.get(p.id, 0),
                "updated_at": p.updated_at.isoformat() if p.updated_at else None,
            }
        )
    return out


@router.get("/projects/{project_id}")
def get_project(project_id: int, db: Session = Depends(get_db)):
    return serialize_project(db, get_project_or_404(db, project_id))


@router.put("/projects/{project_id}/meta")
def update_meta(project_id: int, payload: ProjectMetaPayload, db: Session = Depends(get_db)):
    p = get_project_or_404(db, project_id)
    for field in ("name", "sheet_no", "page_no", "revision", "status", "assignee", "note"):
        value = getattr(payload, field)
        if value is not None:
            setattr(p, field, value)
    db.commit()
    return {"id": p.id, "status": p.status}


@router.put("/projects/{project_id}/annotations")
def put_annotations(
    project_id: int, payload: AnnotationUpdatePayload, db: Session = Depends(get_db)
):
    p = get_project_or_404(db, project_id)
    result = replace_annotations(db, p, payload)
    return {
        "id": p.id,
        "symbol_count": len(p.symbols),
        "connection_count": len(p.connections),
        "skipped": result["skipped"],
    }


@router.delete("/projects/{project_id}")
def delete_project(project_id: int, db: Session = Depends(get_db)):
    p = get_project_or_404(db, project_id)
    db.delete(p)
    db.commit()
    return {"deleted": project_id}


@router.post("/projects/bulk-delete")
def bulk_delete(payload: BulkIdsRequest, db: Session = Depends(get_db)):
    if not payload.ids:
        raise HTTPException(400, "削除対象が指定されていません")
    targets = db.query(AnnotationProject).filter(AnnotationProject.id.in_(payload.ids)).all()
    for p in targets:
        db.delete(p)
    db.commit()
    return {"deleted": [p.id for p in targets]}


@router.get("/projects/{project_id}/image")
def get_image(project_id: int, db: Session = Depends(get_db)):
    p = get_project_or_404(db, project_id)
    if not p.images:
        raise HTTPException(404, "画像が登録されていません")
    path = image_path(p.images[0].sha256)
    if not path.exists():
        raise HTTPException(404, "画像ファイルが見つかりません")
    return FileResponse(path, media_type="image/png")


# ---------------------------------------------------------------- 入出力
@router.get("/projects/{project_id}/export")
def export_one(
    project_id: int,
    db: Session = Depends(get_db),
):
    p = get_project_or_404(db, project_id)
    blob = write_export_zip(db, [p])
    return Response(
        content=blob,
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="seqanno_project_{project_id}_{_stamp()}.zip"'
        },
    )


@router.post("/export")
def export_bulk(payload: YoloExportRequest, db: Session = Depends(get_db)):
    projects = _collect(db, payload.ids)
    blob = write_export_zip(db, projects)
    return Response(
        content=blob,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="seqanno_export_{_stamp()}.zip"'},
    )


@router.post("/import")
async def import_bundle(archive: UploadFile = File(...), db: Session = Depends(get_db)):
    name = (archive.filename or "").lower()
    if not name.endswith(".zip"):
        raise HTTPException(400, "ZIP ファイルを指定してください")
    blob = await archive.read()
    if not blob:
        raise HTTPException(400, "ファイルが空です")
    try:
        return import_zip(db, blob)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get("/stats")
def stats(db: Session = Depends(get_db)):
    projects = db.query(AnnotationProject).all()
    symbols = db.query(AnnotationSymbol).count()
    connections = db.query(Connection).count()
    by_status: dict[str, int] = {}
    for p in projects:
        by_status[p.status] = by_status.get(p.status, 0) + 1
    class_key_by_id = {c.id: c.key for c in classes_ordered(db)}
    by_class: dict[str, int] = {}
    for class_id, n in (
        db.query(AnnotationSymbol.class_id, func.count(AnnotationSymbol.id))
        .group_by(AnnotationSymbol.class_id)
        .all()
    ):
        key = class_key_by_id.get(class_id)
        if key and n:
            by_class[key] = n
    by_origin: dict[str, int] = dict(
        db.query(AnnotationSymbol.origin, func.count(AnnotationSymbol.id))
        .group_by(AnnotationSymbol.origin)
        .all()
    )
    prediction_total = db.query(Prediction).count()
    return {
        "project_count": len(projects),
        "symbol_count": symbols,
        "connection_count": connections,
        "by_status": by_status,
        "by_class": by_class,
        "by_origin": by_origin,
        "prediction_count": prediction_total,
    }
