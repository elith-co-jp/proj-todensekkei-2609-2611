"""推論モデル（YOLO .pt）の登録・保管。

モデル実体は画像と同じく `data/models/<sha256>.pt` に置き、DB にはメタデータのみ保持する。
`is_active` が立っているモデルを推論・学習の起点として使う。
"""

from __future__ import annotations

import json
from pathlib import Path

from sqlalchemy.orm import Session

from database import MODEL_DIR
from models import MlModel
from services.blob_store import store_blob

MAX_MODEL_BYTES = 1024 * 1024 * 1024  # 1 GiB


def model_path(sha256: str) -> Path:
    return MODEL_DIR / f"{sha256}.pt"


def store_model_bytes(raw: bytes, file_name: str) -> tuple[str, int]:
    """モデルファイルを data/models/<sha256>.pt へ保存し、(sha256, size) を返す。"""
    if not raw:
        raise ValueError("モデルファイルが空です")
    if len(raw) > MAX_MODEL_BYTES:
        raise ValueError("モデルサイズが上限（1 GiB）を超えています")
    # .pt / .torchscript 等の拡張子チェックは緩め、内容は PyTorch 系のバイナリであることだけ要求
    if not (file_name.lower().endswith((".pt", ".pth", ".torchscript")) or raw[:2] == b"PK"):
        raise ValueError("YOLO モデル（.pt）を指定してください")
    return store_blob(MODEL_DIR, raw, ".pt")


def next_version(db: Session) -> int:
    current = db.query(MlModel).order_by(MlModel.version.desc()).first()
    return (current.version if current else 0) + 1


def active_model(db: Session) -> MlModel | None:
    return db.query(MlModel).filter(MlModel.is_active.is_(True)).one_or_none()


def activate(db: Session, model: MlModel) -> None:
    for m in db.query(MlModel).filter(MlModel.is_active.is_(True)).all():
        m.is_active = False
    model.is_active = True
    db.flush()


def serialize_model(m: MlModel) -> dict:
    metrics = None
    if m.metrics_json:
        try:
            metrics = json.loads(m.metrics_json)
        except (TypeError, json.JSONDecodeError):
            metrics = None
    return {
        "id": m.id,
        "name": m.name,
        "version": m.version,
        "file_name": m.file_name,
        "sha256": m.sha256,
        "size_bytes": m.size_bytes,
        "source": m.source,
        "is_active": bool(m.is_active),
        "metrics": metrics,
        "note": m.note,
        "created_at": m.created_at.isoformat() if m.created_at else None,
    }


def delete_model(db: Session, model: MlModel) -> None:
    """レコードを削除し、他から参照されていなければ実ファイルも消す。"""
    sha = model.sha256
    db.delete(model)
    db.flush()
    still_used = db.query(MlModel).filter(MlModel.sha256 == sha).count()
    if not still_used:
        path = model_path(sha)
        if path.exists():
            path.unlink()
