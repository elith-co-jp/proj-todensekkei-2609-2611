"""回路シミュレーション API（DD-0009）。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from database import get_db
from services.circuit_service import circuit_netlist, circuit_summary, run_simulation

router = APIRouter(prefix="/api/circuit", tags=["circuit"])


class CircuitRequest(BaseModel):
    project_ids: list[int] = Field(min_length=1)
    inputs: dict[str, bool] = {}
    energized: dict[str, bool] = {}


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
