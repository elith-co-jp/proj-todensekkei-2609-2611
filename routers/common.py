"""ルータ共通の小さなヘルパ。"""

from __future__ import annotations

from fastapi import HTTPException, UploadFile
from sqlalchemy.orm import Session
from sqlalchemy.orm.interfaces import LoaderOption

from models import AnnotationProject


def get_project_or_404(db: Session, project_id: int, *options: LoaderOption) -> AnnotationProject:
    """options には一緒に読む関連（例: annotation_service.WITH_ANNOTATIONS）を渡す。"""
    p = (
        db.query(AnnotationProject)
        .options(*options)
        .filter(AnnotationProject.id == project_id)
        .one_or_none()
    )
    if p is None:
        raise HTTPException(404, "アノテーションプロジェクトが見つかりません")
    return p


def safe_upload_name(file: UploadFile, fallback: str) -> str:
    """アップロードファイル名からパス要素を除いた basename を返す。"""
    name = (file.filename or fallback).rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
    return name or fallback
