"""表の読み取りの確認 API（DD-0051）。リレー表・表題欄の OCR を元の並びで返し、人が直した文字を保存・確定する。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from database import get_db
from routers.common import get_project_or_404
from services import table_review

router = APIRouter(prefix="/api/projects/{project_id}/tables", tags=["tables"])


class CellValue(BaseModel):
    row: int = Field(ge=0)
    col: int = Field(ge=0)
    text: str = Field(default="", max_length=200)


class TableSave(BaseModel):
    values: list[CellValue] = Field(default_factory=list, max_length=20000)
    confirm: bool = False


def _call(fn, *args):
    try:
        return fn(*args)
    except table_review.NotFound as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get("")
def list_tables(project_id: int, db: Session = Depends(get_db)):
    get_project_or_404(db, project_id)
    return table_review.list_tables(db, project_id)


@router.put("/{kind}")
def save_table(project_id: int, kind: str, payload: TableSave, db: Session = Depends(get_db)):
    get_project_or_404(db, project_id)
    return _call(table_review.save_table, db, project_id, kind, [v.model_dump() for v in payload.values], payload.confirm)


@router.delete("/{kind}")
def reset_table(project_id: int, kind: str, db: Session = Depends(get_db)):
    get_project_or_404(db, project_id)
    if kind not in table_review.TABLE_KINDS:
        raise HTTPException(404, f"表の種類が違います: {kind}")
    return table_review.reset_table(db, project_id, kind)
