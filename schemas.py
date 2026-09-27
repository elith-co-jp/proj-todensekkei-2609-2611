"""API のリクエスト／レスポンス型。"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

SymbolOrigin = Literal["manual", "inference"]
ProjectStatus = Literal["draft", "review", "done"]
ConnectionKind = Literal["wire", "sheet_ref"]


# ---------- クラスマスタ ----------
class SymbolClassPayload(BaseModel):
    key: str = Field(min_length=1, max_length=64)
    label: str = Field(min_length=1, max_length=200)
    color: str = "#0055a4"
    is_active: bool = True
    sort_order: int = 0


class SymbolClassOut(SymbolClassPayload):
    id: int
    yolo_index: int


# ---------- アノテーション ----------
class TerminalPayload(BaseModel):
    """端子。`ref` はプロジェクト内で一意な文字列（省略時はサーバで採番）。"""

    ref: str | None = None
    name: str = Field(min_length=1, max_length=50)
    tx: float
    ty: float


class SymbolPayload(BaseModel):
    ref: str | None = None
    class_key: str
    label: str | None = None
    cx: float
    cy: float
    w: float
    h: float
    note: str | None = None
    origin: SymbolOrigin = "manual"  # manual / inference（AI 推論由来）
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    terminals: list[TerminalPayload] = []


class ConnectionPayload(BaseModel):
    from_symbol_ref: str
    from_terminal_ref: str | None = None
    to_symbol_ref: str
    to_terminal_ref: str | None = None
    wire_no: str | None = None
    net_id: str | None = None
    kind: ConnectionKind = "wire"
    external_ref: str | None = None
    note: str | None = None


class AnnotationUpdatePayload(BaseModel):
    """アノテーションの一括置換（送られた内容で全置換するモデル）。"""

    symbols: list[SymbolPayload] = []
    connections: list[ConnectionPayload] = []


class ProjectMetaPayload(BaseModel):
    name: str | None = None
    sheet_no: str | None = None
    page_no: str | None = None
    revision: str | None = None
    status: ProjectStatus | None = None
    assignee: str | None = None
    note: str | None = None


class BulkIdsRequest(BaseModel):
    ids: list[int]


class YoloExportRequest(BaseModel):
    ids: list[int] = []


# ---------- AI 改善サイクル（推論・学習） ----------
class InferenceRunRequest(BaseModel):
    """対象プロジェクトへ active モデルで推論を実行する。"""

    project_ids: list[int] = []  # 空 = 全プロジェクト
    conf: float = Field(default=0.25, ge=0.0, le=1.0)


class TrainingRunRequest(BaseModel):
    """蓄積したアノテーションで学習を実行する。"""

    project_ids: list[int] = []  # 空 = only_done / 全件の判定に従う
    only_done: bool = True  # True なら status=done の図面のみを学習データに使う
    epochs: int = Field(default=100, ge=1, le=1000)
    imgsz: int = Field(default=1280, ge=320, le=4096)
    base_model: str | None = None  # None = active モデル（無ければ yolov8n.pt）
