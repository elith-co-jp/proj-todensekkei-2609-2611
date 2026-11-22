"""画面のテストが使う API の応答の見本（frontend/src/test/fixtures/*.json）を、実際の API の応答と突き合わせる。

画面のテストはモックで API を置き換える。モックの形が実際の応答とずれると、テストは通るのに画面が落ちる
（2026-11-22 の点検: タイマーの「初期状態」の changes が {} で、画面は配列として読んで落ちた）。
見本は実際の API で作り、ここで一致を確かめる。API の形を変えたら、次で見本を作り直して画面のテストも直す:

    SEQANNO_UPDATE_FIXTURES=1 python -m pytest tests/test_api_contract.py
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from services import circuit_model
from tests.test_circuit_timing import timer_page

FIXTURES = Path(__file__).resolve().parents[1] / "frontend" / "src" / "test" / "fixtures"


@pytest.fixture(autouse=True)
def builtin(monkeypatch):
    monkeypatch.setattr(circuit_model, "ngspice_path", lambda: None)


def _check(name: str, data) -> None:
    path = FIXTURES / name
    text = json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if os.environ.get("SEQANNO_UPDATE_FIXTURES") == "1":
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    assert path.exists(), f"{path} がありません。SEQANNO_UPDATE_FIXTURES=1 で作ってください"
    assert json.loads(path.read_text(encoding="utf-8")) == json.loads(text), (
        f"{name} が実際の API の応答とずれています。SEQANNO_UPDATE_FIXTURES=1 で作り直し、画面のテストも直してください")


def _timer_project(client) -> int:
    from tests.conftest import create_project

    pid = create_project(client, "timer.png", 1000, 1000)
    symbols = [{k: s[k] for k in ("ref", "class_key", "label", "cx", "cy", "w", "h")} | {"attributes": s.get("attributes") or {}}
               | {"terminals": [{k: t[k] for k in ("ref", "name", "tx", "ty")} for t in s["terminals"]]}
               for s in timer_page()["symbols"]]
    res = client.put(f"/api/projects/{pid}/annotations", json={"symbols": symbols, "connections": timer_page()["connections"]})
    assert res.status_code == 200, res.text
    return pid


def test_circuit_timeline_contract(client):
    pid = _timer_project(client)
    summary = client.post("/api/circuit/build", json={"project_ids": [pid]})
    assert summary.status_code == 200, summary.text
    timeline = client.post("/api/circuit/timeline", json={
        "project_ids": [pid], "events": [{"at": 1, "inputs": {"PB1": True}}], "until": 10})
    assert timeline.status_code == 200, timeline.text
    _check("circuit-summary-timer.json", summary.json())
    _check("circuit-timeline.json", timeline.json())


def test_every_frontend_api_path_exists():
    """frontend/src/api/client.ts の呼び先の URL が、すべてサーバに実在する（打ち間違い・消した API を呼ばない）。"""
    import re

    from main import app

    source = (FIXTURES.parents[1] / "api" / "client.ts").read_text(encoding="utf-8")
    # /api/projects${q ? ...} のような、パスの後ろに付ける問い合わせは除く
    paths = {re.sub(r"\$\{[^}]+\}", "X", re.sub(r"(?<!/)\$\{.*$", "", m)) for m in re.findall(r"[`'](/api/(?:[^`'?$]|\$\{[^}]*\})*)", source)}
    assert len(paths) > 50
    routes = [r for r in app.routes if getattr(r, "path", "").startswith("/api/")]
    missing = [p for p in sorted(paths) if not any(r.path_regex.match(p.replace("X", "1")) for r in routes)]
    assert missing == []
