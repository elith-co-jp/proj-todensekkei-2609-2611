"""ORM モデル。

データ設計で意識した既知リスクと対応方針:
  - `class_id が配列の添字だけで、DB 上の参照先がない`
      -> クラスマスタ `symbol_classes` を用意し、`yolo_index` を UK として明示採番する。
  - `画像を base64 テキストで保存すると、検索・バックアップ・移行の負荷が高い`
      -> 画像実体は data/images/<sha256>.png に置き、DB にはメタデータのみ保持する。
  - `親子関係が FK で追跡できない`
      -> from-to（配線）は symbol / terminal への FK で保持する。

座標はすべて画像サイズで割った正規化値（0.0-1.0）。YOLO 形式と同じ表現のため
エクスポート時の変換が不要。
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import relationship

from database import Base


def utc_now_naive() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


class SymbolClass(Base):
    """シンボルクラスのマスタ。YOLO の class id は `yolo_index` で明示管理する。"""

    __tablename__ = "symbol_classes"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    key = Column(String(64), nullable=False, unique=True)
    label = Column(String(200), nullable=False)
    yolo_index = Column(Integer, nullable=False, unique=True)
    color = Column(String(7), nullable=False, default="#0055a4")
    is_active = Column(Boolean, nullable=False, default=True)
    sort_order = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime, default=utc_now_naive)

    symbols = relationship("AnnotationSymbol", back_populates="symbol_class")


class AnnotationProject(Base):
    """1 図面 = 1 アノテーションプロジェクト。"""

    __tablename__ = "annotation_projects"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    name = Column(String(500), nullable=False)
    sheet_no = Column(String(100), nullable=True)
    page_no = Column(String(50), nullable=True)
    revision = Column(String(50), nullable=True)
    source_file = Column(String(500), nullable=True)
    image_width = Column(Integer, nullable=False, default=0)
    image_height = Column(Integer, nullable=False, default=0)
    status = Column(String(20), nullable=False, default="draft")  # draft / review / done
    assignee = Column(String(100), nullable=True)
    note = Column(Text, nullable=True)
    created_at = Column(DateTime, default=utc_now_naive)
    updated_at = Column(DateTime, default=utc_now_naive, onupdate=utc_now_naive)

    images = relationship(
        "AnnotationImage", back_populates="project", cascade="all, delete-orphan"
    )
    symbols = relationship(
        "AnnotationSymbol", back_populates="project", cascade="all, delete-orphan"
    )
    connections = relationship(
        "Connection", back_populates="project", cascade="all, delete-orphan"
    )
    predictions = relationship(
        "Prediction", back_populates="project", cascade="all, delete-orphan"
    )


class AnnotationImage(Base):
    """図面画像。実体はファイル、DB にはメタデータのみ。"""

    __tablename__ = "annotation_images"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    project_id = Column(
        Integer, ForeignKey("annotation_projects.id", ondelete="CASCADE"), nullable=False
    )
    filename = Column(String(500), nullable=False)
    sha256 = Column(String(64), nullable=False, index=True)
    width = Column(Integer, nullable=False)
    height = Column(Integer, nullable=False)
    created_at = Column(DateTime, default=utc_now_naive)

    project = relationship("AnnotationProject", back_populates="images")


class AnnotationSymbol(Base):
    """シンボルの矩形アノテーション（正規化座標）。"""

    __tablename__ = "annotation_symbols"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    project_id = Column(
        Integer, ForeignKey("annotation_projects.id", ondelete="CASCADE"), nullable=False
    )
    class_id = Column(Integer, ForeignKey("symbol_classes.id"), nullable=False)
    ref = Column(String(50), nullable=False)  # SYM-0001（プロジェクト内で一意）
    label = Column(String(200), nullable=True)  # 33HB / FV2306 など
    cx = Column(Float, nullable=False)
    cy = Column(Float, nullable=False)
    w = Column(Float, nullable=False)
    h = Column(Float, nullable=False)
    note = Column(Text, nullable=True)
    origin = Column(String(20), nullable=False, default="manual", server_default="manual")
    # origin: manual=人手で入力 / inference=AI 推論から生成（人が修正して蓄積する対象）
    confidence = Column(Float, nullable=True)  # 推論由来のときの信頼度 (0-1)
    created_at = Column(DateTime, default=utc_now_naive)

    __table_args__ = (UniqueConstraint("project_id", "ref", name="uq_symbol_ref_per_project"),)

    project = relationship("AnnotationProject", back_populates="symbols")
    symbol_class = relationship("SymbolClass", back_populates="symbols")
    terminals = relationship(
        "SymbolTerminal", back_populates="symbol", cascade="all, delete-orphan"
    )


class SymbolTerminal(Base):
    """シンボルの端子（任意）。端子単位で from-to を登録したい場合に使う。"""

    __tablename__ = "symbol_terminals"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    symbol_id = Column(
        Integer, ForeignKey("annotation_symbols.id", ondelete="CASCADE"), nullable=False
    )
    name = Column(String(50), nullable=False)  # "13" / "NC1" など
    tx = Column(Float, nullable=False)  # 画像に対する正規化座標
    ty = Column(Float, nullable=False)

    symbol = relationship("AnnotationSymbol", back_populates="terminals")


class Connection(Base):
    """シンボル間の配線（from-to）。端子は任意で指定できる。"""

    __tablename__ = "connections"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    project_id = Column(
        Integer, ForeignKey("annotation_projects.id", ondelete="CASCADE"), nullable=False
    )
    from_symbol_id = Column(
        Integer, ForeignKey("annotation_symbols.id", ondelete="CASCADE"), nullable=False
    )
    from_terminal_id = Column(
        Integer, ForeignKey("symbol_terminals.id", ondelete="SET NULL"), nullable=True
    )
    to_symbol_id = Column(
        Integer, ForeignKey("annotation_symbols.id", ondelete="CASCADE"), nullable=False
    )
    to_terminal_id = Column(
        Integer, ForeignKey("symbol_terminals.id", ondelete="SET NULL"), nullable=True
    )
    wire_no = Column(String(50), nullable=True)  # 電線番号（610 等）
    net_id = Column(String(50), nullable=True)  # 同一ネットのグルーピング（任意）
    kind = Column(String(20), nullable=False, default="wire")  # wire / sheet_ref
    external_ref = Column(String(200), nullable=True)  # (610-2F-9) 等のシート間参照
    note = Column(Text, nullable=True)
    created_at = Column(DateTime, default=utc_now_naive)

    project = relationship("AnnotationProject", back_populates="connections")
    from_symbol = relationship("AnnotationSymbol", foreign_keys=[from_symbol_id])
    to_symbol = relationship("AnnotationSymbol", foreign_keys=[to_symbol_id])
    from_terminal = relationship("SymbolTerminal", foreign_keys=[from_terminal_id])
    to_terminal = relationship("SymbolTerminal", foreign_keys=[to_terminal_id])


class MlModel(Base):
    """登録済みの推論モデル（YOLO の .pt）。最新 or 任意のものを active にして推論に使う。"""

    __tablename__ = "ml_models"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    name = Column(String(200), nullable=False)
    version = Column(Integer, nullable=False)  # 連番（全モデル共通）
    file_name = Column(String(500), nullable=False)  # アップロード時のファイル名
    sha256 = Column(String(64), nullable=False, index=True)  # 実体は data/models/<sha256>.pt
    size_bytes = Column(Integer, nullable=False, default=0)
    source = Column(String(20), nullable=False, default="upload")  # upload / trained
    is_active = Column(Boolean, nullable=False, default=False)
    metrics_json = Column(Text, nullable=True)  # 学習時の metrics（results.csv 最終行）
    note = Column(Text, nullable=True)
    created_at = Column(DateTime, default=utc_now_naive)

    predictions = relationship("Prediction", back_populates="model")


class Prediction(Base):
    """推論結果（未確定の検出）。人が修正してアノテーションへ取り込む下書き。"""

    __tablename__ = "predictions"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    project_id = Column(
        Integer, ForeignKey("annotation_projects.id", ondelete="CASCADE"), nullable=False
    )
    model_id = Column(
        Integer, ForeignKey("ml_models.id", ondelete="SET NULL"), nullable=True
    )
    model_label = Column(String(200), nullable=False, default="")  # 表示用（外部取込時はファイル名等）
    class_id = Column(Integer, ForeignKey("symbol_classes.id"), nullable=False)
    cx = Column(Float, nullable=False)
    cy = Column(Float, nullable=False)
    w = Column(Float, nullable=False)
    h = Column(Float, nullable=False)
    confidence = Column(Float, nullable=True)
    created_at = Column(DateTime, default=utc_now_naive)

    project = relationship("AnnotationProject", back_populates="predictions")
    model = relationship("MlModel", back_populates="predictions")
    symbol_class = relationship("SymbolClass")


class TrainingRun(Base):
    """学習ジョブの履歴。完了すると成果物の .pt が MlModel として登録される。"""

    __tablename__ = "training_runs"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    status = Column(String(20), nullable=False, default="running")  # running / success / failed
    project_ids_json = Column(Text, nullable=False, default="[]")
    image_count = Column(Integer, nullable=False, default=0)
    epochs = Column(Integer, nullable=False, default=100)
    imgsz = Column(Integer, nullable=False, default=1280)
    base_model = Column(String(500), nullable=True)  # 学習の起点（モデル名 or パス）
    result_model_id = Column(
        Integer, ForeignKey("ml_models.id", ondelete="SET NULL"), nullable=True
    )
    metrics_json = Column(Text, nullable=True)
    log_tail = Column(Text, nullable=True)
    started_at = Column(DateTime, default=utc_now_naive)
    finished_at = Column(DateTime, nullable=True)

    result_model = relationship("MlModel")
