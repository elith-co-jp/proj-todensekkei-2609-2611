"""記号・配線の属性 API（DD-0010）。"""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from database import get_db
from services.attribute_definitions import all_definitions
from services.object_attributes import export_document, import_document, parse_document

router = APIRouter(prefix="/api/attributes", tags=["attributes"])
MAX_UPLOAD = 20 * 1024 * 1024


class AttributeExportRequest(BaseModel):
    project_ids: list[int] = Field(min_length=1)
    template: bool = False


@router.get("/definitions")
def definitions():
    """記号（共通・種類別）と配線の標準の属性。ここに無い項目も自由に足せる。"""
    return all_definitions()


@router.post("/export")
def export(payload: AttributeExportRequest, db: Session = Depends(get_db)):
    """オブジェクト ID と属性を 1 対 1 で並べた JSON。template なら未入力の項目も null で並べる。"""
    try:
        document = export_document(db, payload.project_ids, payload.template)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    name = "object_attributes_template.json" if payload.template else "object_attributes.json"
    return Response(json.dumps(document, ensure_ascii=False, indent=2), media_type="application/json",
                    headers={"Content-Disposition": f'attachment; filename="{name}"'})


@router.post("/import")
async def import_(file: UploadFile = File(...), db: Session = Depends(get_db)):
    """書き出した JSON（編集後）を取り込み、オブジェクト ID ごとに属性を置き換える。"""
    raw = await file.read(MAX_UPLOAD + 1)
    if len(raw) > MAX_UPLOAD:
        raise HTTPException(413, "ファイルが大きすぎます（20 MB まで）")
    try:
        return import_document(db, parse_document(raw))
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
