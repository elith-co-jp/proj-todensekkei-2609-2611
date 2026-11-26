"""画面のボタンから呼ぶ API が 500 にならないこと（2026-11-27 の全画面の点検で見つかったもの）。

- I/O 表に壊れた（Excel でない）.xlsx を登録 → 500 だった。直し方つきの 400 にする。
- 中身が PyTorch でないファイルを .pt としてモデル登録できた → 以後の解析が毎回 500。登録の時点で 400。
- 読み込めないモデルで解析 → 500。直し方つきの 400 にする。
- 「AI 解析（全件）」など複数図面の解析中に、エディタの保存・モデルの採用が 500（database is locked）。
  解析中は書き込みを始めず、全図面の解析が済んでからまとめて書く。
- それでも書き込みが重なって待ちきれないときは 500 ではなく 503（少し待って再実行）。
"""

from __future__ import annotations

import io
import sqlite3
import sys
from types import SimpleNamespace

from openpyxl import Workbook
from sqlalchemy import text
from sqlalchemy.exc import OperationalError

import database
from models import AnnotationProject
from tests.conftest import SAMPLE_ANNOTATION, create_project
from tests.test_ml_cycle import _upload_model
from tests.test_structure_api import _fake_detector, _result


def test_io_table_rejects_broken_excel(client):
    for name, raw in [("io.xlsx", b"not an excel file"), ("io.xlsx", b"PK\x03\x04 broken zip"), ("io.csv", b"PK broken")]:
        res = client.put("/api/circuit/io-table", files={"file": (name, raw, "application/octet-stream")})
        assert res.status_code == 400, (name, res.text)
        assert "Excel" in res.json()["detail"]


def test_io_table_accepts_real_excel_after_the_fix(client):
    book = Workbook()
    sheet = book.active
    sheet.append(["器具番号", "信号名"])
    sheet.append(["D009 B413", "主制御装置選択指令"])
    buf = io.BytesIO()
    book.save(buf)
    res = client.put("/api/circuit/io-table", files={"file": ("io.xlsx", buf.getvalue(), "application/octet-stream")})
    assert res.status_code == 200, res.text
    assert res.json()["rows"] == 1


def test_model_upload_rejects_a_pt_file_that_is_not_pytorch(client):
    res = client.post("/api/ml/models", files={"file": ("best.pt", b"garbage", "application/octet-stream")})
    assert res.status_code == 400
    assert client.get("/api/ml/models").json() == []


def test_analysis_with_unloadable_model_is_400_not_500(client, monkeypatch):
    import services.inference_service as inference

    pid = create_project(client)
    _upload_model(client)

    def broken(_path):
        raise TypeError("not a loadable checkpoint")

    monkeypatch.setattr(inference, "ultralytics_available", lambda: True)
    monkeypatch.setitem(sys.modules, "ultralytics", SimpleNamespace(YOLO=broken))
    for path in ("/api/ml/analysis/run", "/api/ml/inference/run"):
        res = client.post(path, json={"project_ids": [pid]})
        assert res.status_code == 400, res.text
        assert "読み込めません" in res.json()["detail"]


def test_batch_analysis_lets_other_screens_save_while_it_runs(client, monkeypatch):
    """複数図面の解析の途中（2 枚目を解析中）に、ほかの画面からの保存がすぐ通ること。"""
    from services import structure_service

    first, second = create_project(client), create_project(client, "second.png")
    _upload_model(client)
    _fake_detector(monkeypatch)
    saved: list[str] = []

    def analyze(project, *_args):
        if project.id == second:
            # 別の接続から書く。解析側が書き込みの途中なら、待たずに database is locked になる
            with database.SessionLocal() as other:
                other.execute(text("PRAGMA busy_timeout=0"))
                other.get(AnnotationProject, first).note = "解析中に保存"
                other.commit()
                saved.append("ok")
        return _result(project.id)

    monkeypatch.setattr(structure_service, "analyze_project", analyze)
    res = client.post("/api/ml/analysis/run", json={"project_ids": [first, second]})
    assert res.status_code == 200, res.text
    assert saved == ["ok"]
    assert client.get(f"/api/projects/{first}").json()["note"] == "解析中に保存"
    assert client.get(f"/api/ml/projects/{second}/structure").json()["result"] is not None


def test_locked_database_is_503_with_a_message(client, monkeypatch):
    import routers.annotations as annotations

    pid = create_project(client)

    def locked(*_args, **_kwargs):
        raise OperationalError("DELETE FROM connections", {}, sqlite3.OperationalError("database is locked"))

    monkeypatch.setattr(annotations, "replace_annotations", locked)
    res = client.put(f"/api/projects/{pid}/annotations", json=SAMPLE_ANNOTATION)
    assert res.status_code == 503
    assert "もう一度" in res.json()["detail"]
