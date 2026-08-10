"""API のリクエスト／レスポンス型。"""

from __future__ import annotations

from pydantic import BaseModel, Field


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
    terminals: list[TerminalPayload] = []


class ConnectionPayload(BaseModel):
    from_symbol_ref: str
    from_terminal_ref: str | None = None
    to_symbol_ref: str
    to_terminal_ref: str | None = None
    wire_no: str | None = None
    net_id: str | None = None
    kind: str = "wire"
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
    status: str | None = None
    assignee: str | None = None
    note: str | None = None


class BulkIdsRequest(BaseModel):
    ids: list[int]


class YoloExportRequest(BaseModel):
    ids: list[int] = []
