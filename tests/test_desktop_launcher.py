from __future__ import annotations

import sys
from types import SimpleNamespace

import desktop


def test_ensure_standard_streams_replaces_missing_streams(monkeypatch):
    monkeypatch.setattr(sys, "stdout", None)
    monkeypatch.setattr(sys, "stderr", None)

    desktop._ensure_standard_streams()

    assert sys.stdout is not None
    assert sys.stderr is not None


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
