"""回路シミュレーション API（DD-0009）。"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from database import get_db
from services import circuit_edit
from services.circuit_service import (
    EditConflict,
    apply_consistency_fixes,
    circuit_code_text,
    circuit_impact,
    circuit_lcapy,
    circuit_netlist,
    circuit_sequence,
    circuit_skidl,
    circuit_summary,
    circuit_timeline,
    circuit_what_if,
    consistency_check,
    device_index,
    edit_apply,
    edit_context,
    edit_history,
    edit_preview,
    edit_undo,
    logic_diagram_view,
    logic_simulate,
    run_simulation,
    trace,
    trace_targets,
)
from services.coil_wiring import split_through_wires

router = APIRouter(prefix="/api/circuit", tags=["circuit"])


class CircuitRequest(BaseModel):
    project_ids: list[int] = Field(min_length=1)
    inputs: dict[str, bool] = {}
    energized: dict[str, bool] = {}


class DemoRequest(CircuitRequest):
    targets: list[str] = []


class SymbolRef(BaseModel):
    project_id: int
    symbol_ref: str


class ContactFix(SymbolRef):
    closed: bool


class Change(BaseModel):
    """「もしも」の変更（DD-0029）。inputs: 外部入力の切り替え、devices: リレー・入力の動作を固定、
    contacts: 接点の溶着（closed=true）・接触不良（false）、open: 断線、short: 短絡。"""

    inputs: dict[str, bool] = {}
    devices: dict[str, bool] = {}
    contacts: list[ContactFix] = []
    open: list[SymbolRef] = []
    short: list[SymbolRef] = []


class WhatIfRequest(CircuitRequest):
    change: Change = Change()


class SequenceStep(BaseModel):
    label: str | None = None
    inputs: dict[str, bool] = {}


class SequenceRequest(WhatIfRequest):
    steps: list[SequenceStep] = Field(min_length=1, max_length=50)


class TimelineEvent(BaseModel):
    at: float = Field(ge=0, le=86400)
    label: str | None = None
    inputs: dict[str, bool] = {}


class TimelineRequest(WhatIfRequest):
    """時刻つきの操作（DD-0033）。delays: タイマーの時間の変更（器具番号 → 秒）、until: ここまで進める（秒）。"""

    events: list[TimelineEvent] = Field(default=[], max_length=100)
    delays: dict[str, float] = {}
    until: float | None = Field(default=None, ge=0, le=86400)


class ImpactRequest(BaseModel):
    project_ids: list[int] = Field(min_length=1)
    device: str


class TraceRequest(BaseModel):
    """図面を読む手順（DD-0035）。mode: backward（逆引き: target か symbol の成立条件と判定）/ forward（順引き: device の影響先）。
    states: 器具番号 → 動作 true / 非動作 false / 不明 null。energized: リレーの前の状態（自己保持）。
    external: 外部入力（I/O）と決めた器具番号。"""

    project_ids: list[int] = Field(min_length=1)
    mode: str = Field(default="backward", pattern="^(backward|forward)$")
    target: str | None = None
    symbol: SymbolRef | None = None
    device: str | None = None
    states: dict[str, bool | None] = {}
    energized: dict[str, bool] = {}
    external: list[str] = []


class LogicRequest(BaseModel):
    """論理図（DD-0045）。targets: 出力にするコイルの器具番号（省略時は接点が外へ出ていく最後のリレー）。
    names: 器具番号 → 図の名前。merge: 同じ条件のリレー・接点 1 つのリレーをまとめる。values: 信号 → ON/OFF（線の色）。"""

    project_ids: list[int] = Field(min_length=1)
    targets: list[str] = []
    names: dict[str, str] = {}
    external: list[str] = []
    merge: bool = True
    inputs: dict[str, bool] = {}
    energized: dict[str, bool] = {}
    values: dict[str, bool] | None = None


class LogicSimulateRequest(LogicRequest):
    events: list[TimelineEvent] = Field(default=[], max_length=100)
    until: float | None = Field(default=None, ge=0, le=86400)
    compare: bool = False


class ConsistencyRequest(BaseModel):
    project_ids: list[int] = Field(min_length=1)


class ConsistencyApplyRequest(ConsistencyRequest):
    issue_ids: list[str] = Field(min_length=1, max_length=500)


class LcapyRequest(CircuitRequest):
    """fixed: 外部入力と励磁から接点の開閉を決め、閉は導線 W・開は開放 O にする（False なら接点は SW no/nc）。"""

    fixed: bool = False


class CoilWiringRequest(BaseModel):
    """エディタの保存前の状態（PUT /api/projects/{id}/annotations と同じ形）と画像の大きさ。"""

    symbols: list[dict[str, Any]]
    connections: list[dict[str, Any]]
    image_width: int = Field(gt=0)
    image_height: int = Field(gt=0)


@router.post("/split-coil-wires")
def split_coil_wires(payload: CoilWiringRequest):
    """コイルの中を通り抜ける配線を、コイルの枠の出入りの点で 2 本に分けて端子を付けた結果を返す（保存はしない）。"""
    try:
        symbols, connections, counts = split_through_wires(
            payload.symbols, payload.connections, payload.image_width, payload.image_height)
    except (KeyError, TypeError, ValueError) as exc:
        raise HTTPException(400, f"アノテーションの形が正しくありません: {exc}") from exc
    return {"symbols": symbols, "connections": connections, "counts": counts}


@router.post("/build")
def build(payload: CircuitRequest, db: Session = Depends(get_db)):
    """選んだ図面のアノテーションから回路を組み、コイル・外部入力・回路にできなかった箇所を返す。"""
    try:
        return circuit_summary(db, payload.project_ids)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post("/simulate")
def simulate(payload: CircuitRequest, db: Session = Depends(get_db)):
    """外部入力の On/Off と前の励磁状態から回路を解き、コイル・接点・配線の状態を返す。"""
    try:
        return run_simulation(db, payload.project_ids, payload.inputs, payload.energized)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post("/netlist")
def netlist(payload: CircuitRequest, db: Session = Depends(get_db)):
    """LTspice・ngspice で開けるネットリスト（.cir）。"""
    try:
        text = circuit_netlist(db, payload.project_ids, payload.inputs)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return Response(text, media_type="text/plain; charset=utf-8",
                    headers={"Content-Disposition": 'attachment; filename="circuit.cir"'})


def _download(text: str, filename: str) -> Response:
    return Response(text, media_type="text/plain; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{filename}"'})


@router.post("/lcapy")
def lcapy(payload: LcapyRequest, db: Session = Depends(get_db)):
    """Lcapy のネットリスト（DD-0028）。lcapy.Circuit(text) で読み込める。"""
    try:
        return _download(circuit_lcapy(db, payload.project_ids, payload.inputs, payload.energized, payload.fixed),
                         "circuit_lcapy.txt")
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post("/skidl")
def skidl(payload: CircuitRequest, db: Session = Depends(get_db)):
    """SKiDL のスクリプト（DD-0028）。python で実行すると KiCad のネットリスト circuit.net を書き出す。"""
    try:
        return _download(circuit_skidl(db, payload.project_ids), "circuit_skidl.py")
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post("/code")
def code(payload: CircuitRequest, db: Session = Depends(get_db)):
    """回路を書き換えられる Python のコードにしたもの（DD-0029）。リポジトリの中で python で実行できる。"""
    try:
        return _download(circuit_code_text(db, payload.project_ids), "circuit_code.py")
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post("/what-if")
def what_if(payload: WhatIfRequest, db: Session = Depends(get_db)):
    """今の状態（inputs・energized）に変更（change）を加えたら、どのリレー・負荷・接点が変わるか（DD-0029）。"""
    try:
        return circuit_what_if(db, payload.project_ids, payload.inputs, payload.energized, payload.change.model_dump())
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post("/sequence")
def sequence(payload: SequenceRequest, db: Session = Depends(get_db)):
    """操作の手順（押す → 離す → …）を順に解き、各手順で変わったところを返す（DD-0029）。"""
    try:
        return circuit_sequence(db, payload.project_ids, [s.model_dump() for s in payload.steps], payload.inputs,
                                payload.energized, payload.change.model_dump())
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post("/timeline")
def timeline(payload: TimelineRequest, db: Session = Depends(get_db)):
    """時刻つきの操作とタイマー（オンディレー・オフディレー）で、時間の流れを解く（DD-0033）。"""
    try:
        return circuit_timeline(db, payload.project_ids, [e.model_dump() for e in payload.events], payload.inputs,
                                payload.energized, payload.change.model_dump(), payload.delays, payload.until)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post("/impact")
def impact(payload: ImpactRequest, db: Session = Depends(get_db)):
    """機器（器具番号）が動いたら影響しうるリレー・負荷と、その機器を動かしうる機器（DD-0029）。"""
    try:
        return circuit_impact(db, payload.project_ids, payload.device)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post("/trace")
def trace_api(payload: TraceRequest, db: Session = Depends(get_db)):
    """逆引き: 電源から戻りまでの経路から成立条件（直列 AND・並列 OR）を求め、成立・不成立・判定不能を枝ごとの根拠
    （ページ・位置）つきで返す。順引き: 器具の接点を条件に含む出力を段数つきで返す（DD-0035）。"""
    try:
        return trace(db, payload.project_ids, mode=payload.mode, target=payload.target,
                     symbol=payload.symbol.model_dump() if payload.symbol else None, device=payload.device,
                     states=payload.states, energized=payload.energized, external=payload.external)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post("/trace/targets")
def trace_targets_api(payload: ConsistencyRequest, db: Session = Depends(get_db)):
    """逆引きの対象（コイル・負荷）と、順引きの起点（接点の器具と出どころ: リレー・外部入力・参照先欠落・未解決）。"""
    try:
        return trace_targets(db, payload.project_ids)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post("/consistency")
def consistency(payload: ConsistencyRequest, db: Session = Depends(get_db)):
    """複数の解析方法（リレー表・電線番号・行先札・回路計算・波及範囲・シミュレーション）の結果を突き合わせ、
    矛盾と精査の結果・直し方を返す（DD-0031）。"""
    try:
        return consistency_check(db, payload.project_ids)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post("/consistency/apply")
def consistency_apply(payload: ConsistencyApplyRequest, db: Session = Depends(get_db)):
    """選んだ矛盾の直し方を図面のアノテーションに適用し、もう一度チェックした結果を返す（DD-0031）。"""
    try:
        return apply_consistency_fixes(db, payload.project_ids, payload.issue_ids)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


# ------------------------------------------------------------------------------------- 変更（DD-0034）


class EditRequest(BaseModel):
    """図面のコード表現への変更。ops の形は GET /api/circuit/edit/tools の preview_circuit_edit を参照。"""

    project_ids: list[int] = Field(min_length=1)
    ops: list[dict[str, Any]] = Field(default=[], max_length=circuit_edit.MAX_OPS)
    title: str = Field(default="図面の変更", max_length=500)
    reason: str | None = None
    author: str = Field(default="user", max_length=100)
    base_hash: str | None = None


class UndoRequest(BaseModel):
    force: bool = False


@router.get("/edit/tools")
def edit_tools():
    """LLM のツール定義（Anthropic Messages API の tools の形）と、操作ごとの JSON スキーマ（DD-0034）。"""
    return {"tools": circuit_edit.tool_definitions(), "ops": circuit_edit.OPS}


@router.post("/edit/context")
def edit_context_api(payload: CircuitRequest, db: Session = Depends(get_db)):
    """変更する図面の記号・端子・配線・回路コード（LLM に渡す文脈。DD-0034）。"""
    try:
        return edit_context(db, payload.project_ids)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post("/edit/preview")
def edit_preview_api(payload: EditRequest, db: Session = Depends(get_db)):
    """変更を試す: 差分・検証・動作の変化・波及範囲。図面は変えない（DD-0034）。"""
    try:
        return edit_preview(db, payload.project_ids, payload.ops)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post("/edit/apply")
def edit_apply_api(payload: EditRequest, db: Session = Depends(get_db)):
    """変更を適用し、取り消せる変更セットとして残す。base_hash が今の図面と違えば 409（DD-0034）。"""
    try:
        return edit_apply(db, payload.project_ids, payload.ops, title=payload.title, reason=payload.reason,
                          author=payload.author, base_hash=payload.base_hash)
    except EditConflict as exc:
        raise HTTPException(409, str(exc)) from exc
    except circuit_edit.EditError as exc:
        raise HTTPException(422, {"message": str(exc), "errors": exc.errors}) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get("/edit/history")
def edit_history_api(project_ids: str | None = None, db: Session = Depends(get_db)):
    """変更セットの履歴（新しい順）。project_ids はカンマ区切り。"""
    ids = [int(x) for x in (project_ids or "").split(",") if x.strip().isdigit()]
    return {"change_sets": edit_history(db, ids)}


@router.post("/edit/{change_set_id}/undo")
def edit_undo_api(change_set_id: int, payload: UndoRequest | None = None, db: Session = Depends(get_db)):
    """変更セットを取り消す（変更前の図面に戻す）。その後に図面が変わっていれば 409（force で戻せる）。"""
    try:
        return edit_undo(db, change_set_id, force=bool(payload and payload.force))
    except EditConflict as exc:
        raise HTTPException(409, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc


XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


@router.post("/devices")
def devices(payload: ConsistencyRequest, db: Session = Depends(get_db)):
    """機器索引（DD-0044）: 器具ごとのコイルと接点の場所（ページ・位置）、a / b 接点の数、要確認、電線番号の一覧。"""
    try:
        return device_index(db, payload.project_ids)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get("/devices.xlsx")
def devices_xlsx(project_ids: str = Query(pattern=r"^\d+(,\d+)*$"), db: Session = Depends(get_db)):
    """機器索引の Excel（機器一覧・接点の場所・電線番号・要確認の 4 シート）。"""
    from services import device_index as di

    try:
        index = device_index(db, [int(x) for x in project_ids.split(",")])
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return Response(di.to_xlsx(index), media_type=XLSX,
                    headers={"Content-Disposition": 'attachment; filename="device_index.xlsx"'})


def _logic_kwargs(payload: LogicRequest) -> dict[str, Any]:
    return {"targets": payload.targets or None, "names": payload.names, "external": payload.external,
            "merge": payload.merge, "inputs": payload.inputs, "energized": payload.energized}


@router.post("/logic")
def logic_api(payload: LogicRequest, db: Session = Depends(get_db)):
    """出力のコイルから辿った論理図（直列 AND・並列 OR・b 接点 NOT・タイマー・キープリレー）と SVG（DD-0045）。"""
    try:
        return logic_diagram_view(db, payload.project_ids, **_logic_kwargs(payload), values=payload.values)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post("/logic/svg")
def logic_svg_api(payload: LogicRequest, db: Session = Depends(get_db)):
    """論理図の SVG のダウンロード（DD-0045）。"""
    try:
        svg = logic_diagram_view(db, payload.project_ids, **_logic_kwargs(payload), values=payload.values)["svg"]
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return Response(svg, media_type="image/svg+xml", headers={"Content-Disposition": 'attachment; filename="logic.svg"'})


@router.post("/logic/simulate")
def logic_simulate_api(payload: LogicSimulateRequest, db: Session = Depends(get_db)):
    """論理図の上で時刻つきの操作を解く（タイマー・キープリレーを含む）。compare で回路の計算と照合（DD-0045）。"""
    try:
        return logic_simulate(db, payload.project_ids, [e.model_dump() for e in payload.events], **_logic_kwargs(payload),
                              until=payload.until, compare=payload.compare)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post("/demo")
def demo_api(payload: DemoRequest, db: Session = Depends(get_db)):
    """図面と論理図を並べて動かすデモ（DD-0046）。記号の無い図面は AI 解析の結果で組む。"""
    from services.circuit_service import demo_view

    try:
        return demo_view(db, payload.project_ids, inputs=payload.inputs, energized=payload.energized,
                         targets=payload.targets or None)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
