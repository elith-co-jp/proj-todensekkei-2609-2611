"""回路シミュレーション API（DD-0009）。"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from database import get_db
from services.circuit_service import (
    apply_consistency_fixes,
    circuit_code_text,
    circuit_impact,
    circuit_lcapy,
    circuit_netlist,
    circuit_sequence,
    circuit_skidl,
    circuit_summary,
    circuit_what_if,
    consistency_check,
    run_simulation,
)
from services.coil_wiring import split_through_wires

router = APIRouter(prefix="/api/circuit", tags=["circuit"])


class CircuitRequest(BaseModel):
    project_ids: list[int] = Field(min_length=1)
    inputs: dict[str, bool] = {}
    energized: dict[str, bool] = {}


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


class ImpactRequest(BaseModel):
    project_ids: list[int] = Field(min_length=1)
    device: str


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


@router.post("/impact")
def impact(payload: ImpactRequest, db: Session = Depends(get_db)):
    """機器（器具番号）が動いたら影響しうるリレー・負荷と、その機器を動かしうる機器（DD-0029）。"""
    try:
        return circuit_impact(db, payload.project_ids, payload.device)
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
