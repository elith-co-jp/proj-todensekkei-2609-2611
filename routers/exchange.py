"""東電設計様との正解のやり取り（DD-0035）: 対応表（Excel / CSV）、代表回路のシナリオ、CVAT の XML。"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from database import get_db
from models import AnnotationProject
from schemas import AnnotationUpdatePayload
from services import annotation_table, circuit_scenarios, cvat_format
from services.annotation_service import WITH_ANNOTATIONS, replace_annotations, serialize_project
from services.circuit_service import annotation_table_rows, evaluate_scenarios

router = APIRouter(prefix="/api/exchange", tags=["exchange"])

XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _ids(project_ids: str) -> list[int]:
    try:
        ids = [int(x) for x in project_ids.split(",") if x.strip()]
    except ValueError as exc:
        raise HTTPException(400, "project_ids は数字をカンマで区切って指定してください") from exc
    if not ids:
        raise HTTPException(400, "図面を選んでください")
    return ids


def _projects(db: Session, ids: list[int]) -> list[dict[str, Any]]:
    projects = db.query(AnnotationProject).options(*WITH_ANNOTATIONS).filter(AnnotationProject.id.in_(ids)).all()
    if not projects:
        raise HTTPException(404, "図面がありません")
    return [serialize_project(db, p, with_image_meta=False) for p in sorted(projects, key=lambda p: p.id)]


@router.get("/table")
def export_table(project_ids: str, drawing_set: str = "", format: str = Query("xlsx", pattern="^(xlsx|csv|json)$"),
                 db: Session = Depends(get_db)):
    """対応表（図面セットID／ページID／要素ID／種別／原文／参照・接続先ID／判定／根拠・備考）と代表回路の記入例。"""
    try:
        rows = annotation_table_rows(db, _ids(project_ids), drawing_set)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    if format == "json":
        return {"columns": annotation_table.COLUMNS, "rows": rows}
    if format == "csv":
        return Response(annotation_table.to_csv(rows), media_type="text/csv; charset=utf-8",
                        headers={"Content-Disposition": 'attachment; filename="annotation_table.csv"'})
    return Response(annotation_table.to_xlsx(rows), media_type=XLSX,
                    headers={"Content-Disposition": 'attachment; filename="annotation_table.xlsx"'})


@router.post("/table/compare")
async def compare_table(project_ids: str = Form(...), file: UploadFile = File(...), db: Session = Depends(get_db)):
    """東電設計様が判定を直した対応表（と代表回路のシート）を、いまの図面の機械の表と照合する。"""
    ids = _ids(project_ids)
    try:
        table = annotation_table.read_table(await file.read(), file.filename or "")
        drawing_set = next((r.get("図面セットID") for r in table["対応表"] if r.get("図面セットID")), "")
        mine = annotation_table_rows(db, ids, drawing_set)
        out: dict[str, Any] = {"relations": annotation_table.compare(table["対応表"], mine), "rows": len(table["対応表"])}
        scenarios = annotation_table.scenarios_from_rows(table["代表回路"])
        if scenarios:
            out["scenarios"] = evaluate_scenarios(db, ids, scenarios)
        return out
    except (ValueError, KeyError) as exc:
        raise HTTPException(400, f"対応表を読めません: {exc}") from exc


class ScenarioRequest(BaseModel):
    project_ids: list[int] = Field(min_length=1)
    scenarios: list[dict[str, Any]] | None = Field(default=None, max_length=500)
    text: str | None = Field(default=None, max_length=1_000_000)  # YAML / JSON
    external: list[str] = []


@router.post("/scenarios/evaluate")
def scenarios_evaluate(payload: ScenarioRequest, db: Session = Depends(get_db)):
    """代表回路の正解（成立条件・成立する例・しない例・判定不能の例・影響先）と機械の判定を照合する。"""
    try:
        scenarios = payload.scenarios
        if scenarios is None:
            if not payload.text:
                raise ValueError("scenarios か text（YAML / JSON）を指定してください")
            _, scenarios = circuit_scenarios.load(payload.text)
        return evaluate_scenarios(db, payload.project_ids, scenarios, payload.external)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get("/cvat")
def export_cvat(project_ids: str, db: Session = Depends(get_db)):
    """CVAT for images 1.1 の XML（記号の枠・端子の点・配線の折れ線）。画像は /api/projects/{id}/image から。"""
    pages = _projects(db, _ids(project_ids))
    return Response(cvat_format.export_xml(pages).encode("utf-8"), media_type="application/xml",
                    headers={"Content-Disposition": 'attachment; filename="annotations.xml"'})


@router.post("/cvat/import")
async def import_cvat(file: UploadFile = File(...), project_ids: str = Form(""), apply: bool = Form(False),
                      db: Session = Depends(get_db)):
    """CVAT の XML を読み、図面に当てる。apply=false なら取り込む内容の確認だけ、true なら図面のアノテーションを置き換える。"""
    try:
        parsed = cvat_format.parse_xml((await file.read()).decode("utf-8"))
    except Exception as exc:
        raise HTTPException(400, f"CVAT の XML を読めません: {exc}") from exc
    ids = _ids(project_ids) if project_ids.strip() else [p["project_id"] for p in parsed if p.get("project_id")]
    projects = _projects(db, ids) if ids else []
    matched = cvat_format.match_pages(parsed, projects)
    out = []
    for item, target in matched:
        row = {"image": item["name"], "project_id": target["id"] if target else None, "symbols": len(item["symbols"]),
               "connections": len(item["connections"]), "warnings": item["warnings"]}
        if apply and target is not None:
            try:
                payload = AnnotationUpdatePayload(symbols=item["symbols"], connections=item["connections"])
            except ValueError as exc:
                raise HTTPException(400, f"{item['name']}: {exc}") from exc
            project = db.get(AnnotationProject, target["id"])
            replace_annotations(db, project, payload)
            row["applied"] = True
        out.append(row)
    return {"images": out, "applied": apply}
