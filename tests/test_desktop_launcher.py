from __future__ import annotations

import sys
import io
from types import SimpleNamespace

import desktop


def test_ensure_standard_streams_replaces_missing_streams(monkeypatch):
    monkeypatch.setattr(sys, "stdout", None)
    monkeypatch.setattr(sys, "stderr", None)

    desktop._ensure_standard_streams()

    assert sys.stdout is not None
    assert sys.stderr is not None


def test_frozen_windows_uses_utf8_streams(monkeypatch):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(sys, "stdout", io.TextIOWrapper(io.BytesIO(), encoding="cp1252"))
    monkeypatch.setattr(sys, "stderr", io.TextIOWrapper(io.BytesIO(), encoding="cp1252"))

    desktop._ensure_standard_streams()

    assert sys.stdout.encoding == "utf-8"
    assert sys.stderr.encoding == "utf-8"
    print("ブラウザで開きます")


def test_unfrozen_non_utf8_streams_are_reconfigured(monkeypatch):
    """cp1252 コンソールからの非凍結起動でも日本語バナーで落ちない。"""
    stream = io.TextIOWrapper(io.BytesIO(), encoding="cp1252")
    monkeypatch.setattr(sys, "frozen", False, raising=False)
    monkeypatch.setattr(sys, "stdout", stream)
    monkeypatch.setattr(sys, "stderr", io.TextIOWrapper(io.BytesIO(), encoding="cp1252"))

    desktop._ensure_standard_streams()

    assert sys.stdout is stream
    assert sys.stdout.encoding == "utf-8"


def test_main_disables_uvicorn_default_log_config(monkeypatch):
    calls = {}

    def fake_run(app, **kwargs):
        calls["app"] = app
        calls["kwargs"] = kwargs

    class FakeThread:
        def __init__(self, *args, **kwargs):
            pass

        def start(self):
            pass

    monkeypatch.setattr(sys, "stdout", None)
    monkeypatch.setattr(sys, "stderr", None)
    monkeypatch.setitem(sys.modules, "uvicorn", SimpleNamespace(run=fake_run))
    monkeypatch.setitem(sys.modules, "main", SimpleNamespace(app=object()))
    monkeypatch.setattr(desktop, "_pick_port", lambda: 8010)
    monkeypatch.setattr(desktop.threading, "Thread", FakeThread)

    desktop.main()

    assert calls["kwargs"]["log_config"] is None
    assert calls["kwargs"]["access_log"] is False
