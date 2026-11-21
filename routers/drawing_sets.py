"""図面セット API（DD-0041）。回路の API にはセットの図面の ID（project_ids）を渡す。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from database import get_db
from services import drawing_sets

router = APIRouter(prefix="/api/drawing-sets", tags=["drawing-sets"])


class DrawingSetCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    project_ids: list[int] = []
    note: str | None = None


class DrawingSetUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    project_ids: list[int] | None = None
    note: str | None = None


def _call(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except drawing_sets.NotFound as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get("")
def list_sets(db: Session = Depends(get_db)):
    return drawing_sets.list_sets(db)


@router.post("", status_code=201)
def create_set(payload: DrawingSetCreate, db: Session = Depends(get_db)):
    return _call(drawing_sets.create_set, db, payload.name, payload.project_ids, payload.note)


@router.get("/suggest")
def suggest(db: Session = Depends(get_db)):
    """登録済みの図面を、行先札でつながるまとまりに分けた候補（保存はしない）。"""
    return drawing_sets.suggest(db)


@router.get("/{set_id}")
def get_set(set_id: int, db: Session = Depends(get_db)):
    return _call(drawing_sets.get_set, db, set_id)


@router.put("/{set_id}")
def update_set(set_id: int, payload: DrawingSetUpdate, db: Session = Depends(get_db)):
    return _call(drawing_sets.update_set, db, set_id, name=payload.name, note=payload.note,
                 project_ids=payload.project_ids)


@router.delete("/{set_id}", status_code=204)
def delete_set(set_id: int, db: Session = Depends(get_db)):
    _call(drawing_sets.delete_set, db, set_id)


@router.get("/{set_id}/coverage")
def coverage(set_id: int, db: Session = Depends(get_db)):
    """セットの行先札が指すのに、セットに入っていないシート（登録済みなら追加の候補つき）。"""
    return _call(drawing_sets.coverage, db, set_id)
