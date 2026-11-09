"""EXE のビルドで同梱するモデルの取得: 配布元の一時的な失敗は間を空けてやり直す。"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

SPEC = importlib.util.spec_from_file_location("fetch_base_model", Path(__file__).resolve().parents[1] / "scripts" / "fetch_base_model.py")
fetch_base_model = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(fetch_base_model)

ASSET = fetch_base_model.ASSETS[0]


def test_transient_failures_are_retried_with_waits(monkeypatch):
    calls, waits = [], []

    def once(asset):
        calls.append(asset.name)
        if len(calls) < 3:
            raise fetch_base_model.TransientError("HTTP 504")

    monkeypatch.setattr(fetch_base_model, "_fetch_once", once)
    fetch_base_model.fetch(ASSET, sleep=waits.append)
    assert len(calls) == 3 and waits == [15, 45]


def test_gives_up_after_the_last_attempt(monkeypatch):
    def once(asset):
        raise fetch_base_model.TransientError("HTTP 504")

    monkeypatch.setattr(fetch_base_model, "_fetch_once", once)
    with pytest.raises(RuntimeError, match="4 回"):
        fetch_base_model.fetch(ASSET, sleep=lambda _s: None)
