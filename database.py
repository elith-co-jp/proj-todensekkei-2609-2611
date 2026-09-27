"""DB 接続とセッション管理。

既定は SQLite（単体端末・配布運用）。`SEQANNO_DATABASE_URL` を与えれば
MySQL 等へ差し替えられる。
"""

from __future__ import annotations

import os
from pathlib import Path

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from runtime import data_base

DATA_DIR = Path(os.environ.get("SEQANNO_DATA_DIR", data_base() / "data"))
DATA_DIR.mkdir(parents=True, exist_ok=True)
IMAGE_DIR = DATA_DIR / "images"
IMAGE_DIR.mkdir(parents=True, exist_ok=True)
MODEL_DIR = DATA_DIR / "models"
MODEL_DIR.mkdir(parents=True, exist_ok=True)
TRAINING_DIR = DATA_DIR / "training"
TRAINING_DIR.mkdir(parents=True, exist_ok=True)

DATABASE_URL = os.environ.get("SEQANNO_DATABASE_URL", f"sqlite:///{DATA_DIR / 'seqanno.db'}")

_connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}
engine = create_engine(DATABASE_URL, connect_args=_connect_args, pool_pre_ping=True)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class Base(DeclarativeBase):
    pass


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _migrate_columns() -> None:
    """既存 DB への列追加（Alembic なしの軽量マイグレーション）。"""
    inspector = inspect(engine)
    existing = {c["name"] for c in inspector.get_columns("annotation_symbols")}
    statements = []
    if "origin" not in existing:
        statements.append(
            "ALTER TABLE annotation_symbols ADD COLUMN origin VARCHAR(20) NOT NULL DEFAULT 'manual'"
        )
    if "confidence" not in existing:
        statements.append(
            "ALTER TABLE annotation_symbols ADD COLUMN confidence FLOAT"
        )
    if not statements:
        return
    with engine.begin() as conn:
        for stmt in statements:
            conn.execute(text(stmt))


def init_db() -> None:
    """テーブル作成と初期クラスマスタの投入。"""
    from models import SymbolClass  # noqa: PLC0415  循環 import 回避

    Base.metadata.create_all(bind=engine)
    _migrate_columns()
    db = SessionLocal()
    try:
        if db.query(SymbolClass).count() == 0:
            for i, (key, label, color) in enumerate(DEFAULT_SYMBOL_CLASSES):
                db.add(
                    SymbolClass(
                        key=key, label=label, yolo_index=i, color=color, sort_order=i, is_active=True
                    )
                )
            db.commit()
    finally:
        db.close()


# 要件定義書 別紙B の暫定クラス定義（東電設計 シーケンス図電子化 PoC）
DEFAULT_SYMBOL_CLASSES: list[tuple[str, str, str]] = [
    ("relay_coil", "リレーコイル", "#0055a4"),
    ("contact_a", "a接点（メーク）", "#00498e"),
    ("contact_b", "b接点（ブレーク）", "#4a88c1"),
    ("terminal", "端子", "#3f9067"),
    ("junction", "分岐点", "#2f7352"),
    ("diode", "ダイオード", "#b45309"),
    ("solenoid", "電磁弁・ソレノイド", "#c2410c"),
    ("connector", "コネクタ", "#7c3aed"),
    ("lamp", "表示灯", "#db2777"),
    ("push_button", "押しボタン", "#0891b2"),
    ("power_bus", "電源母線端", "#475569"),
    ("other", "その他", "#84b0d8"),
]
