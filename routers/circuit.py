"""回路シミュレーション API（DD-0009）。"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from database import get_db
from services.circuit_service import circuit_netlist, circuit_summary, run_simulation
from services.coil_wiring import split_through_wires

router = APIRouter(prefix="/api/circuit", tags=["circuit"])


class CircuitRequest(BaseModel):
    project_ids: list[int] = Field(min_length=1)
    inputs: dict[str, bool] = {}
    energized: dict[str, bool] = {}


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
