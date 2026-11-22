"""exe（デスクトップ実行）だけで動く部分: 画面からの合図、自動終了の判断、終了、ビルド済み画面の配信。

os._exit で本当にプロセスを終わらせる箇所は、呼ばれることだけを確かめる（テストのプロセスを終わらせないため）。
"""

from __future__ import annotations

import threading

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import main
from services import analysis_jobs, busy


class Exited(Exception):
    pass


@pytest.fixture()
def desktop(monkeypatch):
    monkeypatch.setattr(main, "_DESKTOP_MODE", True)
    exits: list[int] = []

    def fake_exit(code):
        exits.append(code)
        raise Exited

    monkeypatch.setattr(main.os, "_exit", fake_exit)
    return exits


def test_heartbeat_health_and_shutdown_in_desktop_mode(client, desktop, monkeypatch):
    timers = []

    class FakeTimer:
        def __init__(self, delay, fn):
            self.delay, self.fn, self.daemon = delay, fn, False
            timers.append(self)

        def start(self):
            pass

    monkeypatch.setattr(main.threading, "Timer", FakeTimer)
    before = main._desktop_last_heartbeat
    assert client.post("/api/desktop/heartbeat").json() == {"status": "ok"}
    assert client.get("/api/health").json()["desktop"] is True
    assert main._desktop_last_heartbeat >= before
    assert client.post("/api/desktop/shutdown").json() == {"status": "shutting_down"}
    assert timers[0].daemon and timers[0].delay == 0.5
    with pytest.raises(Exited):
        timers[0].fn()
    assert desktop == [0]


def test_shutdown_is_refused_outside_desktop_mode(client):
    assert client.post("/api/desktop/shutdown").status_code == 404


def test_should_exit_waits_for_background_work():
    idle = main._DESKTOP_IDLE_TIMEOUT_SECONDS
    assert main._should_exit(idle) is True
    assert main._should_exit(idle - 1) is False
    with busy.working("training"):
        assert main._should_exit(idle) is False
        assert main._busy_work() == ["training"]
    release = threading.Event()
    job = analysis_jobs.start([1], 0.25, None, lambda _job: release.wait(5))
    try:
        assert main._busy_work() == ["analysis"]
        assert main._should_exit(idle) is False
    finally:
        release.set()
        for thread in threading.enumerate():
            if thread.name == f"analysis-job-{job.id}":
                thread.join(5)


def test_watchdog_starts_once_and_exits_when_idle(desktop, monkeypatch):
    started = []

    class FakeThread:
        def __init__(self, target, name, daemon):
            self.target, self.name, self.daemon = target, name, daemon

        def start(self):
            started.append(self)

    monkeypatch.setattr(main, "_desktop_watchdog_started", False)
    monkeypatch.setattr(main.threading, "Thread", FakeThread)
    main._start_desktop_watchdog()
    main._start_desktop_watchdog()
    assert len(started) == 1 and started[0].daemon
    sleeps = []
    monkeypatch.setattr(main.time, "sleep", sleeps.append)
    answers = iter([False, True])
    monkeypatch.setattr(main, "_should_exit", lambda _idle: next(answers))
    with pytest.raises(Exited):
        started[0].target()
    assert len(sleeps) == 2 and desktop == [0]


def test_startup_in_desktop_mode_starts_the_watchdog(monkeypatch):
    calls = []
    monkeypatch.setattr(main, "_DESKTOP_MODE", True)
    monkeypatch.setattr(main, "_start_desktop_watchdog", lambda: calls.append("watchdog"))
    with TestClient(main.app):
        pass
    assert calls == ["watchdog"]


def test_broken_origin_is_rejected(client):
    res = client.get("/api/health", headers={"Origin": "http://[::1"})
    assert res.status_code == 403


def test_built_frontend_is_served_with_spa_fallback(tmp_path):
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<html>app</html>", encoding="utf-8")
    (dist / "logo.svg").write_text("<svg/>", encoding="utf-8")
    (dist / "assets" / "app.js").write_text("console.log(1)", encoding="utf-8")
    (tmp_path / "secret.txt").write_text("secret", encoding="utf-8")
    app = FastAPI()
    main._mount_frontend(app, dist)
    main._mount_frontend(app, tmp_path / "missing")  # 無ければ何もしない
    web = TestClient(app)
    assert web.get("/").text == "<html>app</html>"
    assert web.get("/logo.svg").text == "<svg/>"
    assert web.get("/assets/app.js").text == "console.log(1)"
    assert web.get("/projects/3/edit").text == "<html>app</html>"
    assert web.get("/..%2Fsecret.txt").text == "<html>app</html>"
