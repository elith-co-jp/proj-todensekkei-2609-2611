from __future__ import annotations

import io
import sys
from types import SimpleNamespace

import pytest

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


def test_pick_port_skips_ports_in_use(monkeypatch):
    import socket

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as busy:
        busy.bind((desktop.HOST, 0))
        busy.listen()
        taken = busy.getsockname()[1]
        monkeypatch.setattr(desktop, "PREFERRED_PORTS", (taken,))
        port = desktop._pick_port()
        assert port != taken and port > 0  # 候補が全部埋まっていれば OS に選ばせる
        monkeypatch.setattr(desktop, "PREFERRED_PORTS", (taken, port))
        assert desktop._pick_port() == port  # 空いている候補があればそれを使う（埋まった候補は飛ばす）


def test_browser_opens_unless_disabled(monkeypatch):
    opened = []
    monkeypatch.setattr(desktop.time, "sleep", lambda _s: None)
    monkeypatch.setattr(desktop.webbrowser, "open", opened.append)
    monkeypatch.setenv("SEQANNO_NO_BROWSER", "1")
    desktop._open_browser_later("http://127.0.0.1:8010")
    assert opened == []
    monkeypatch.delenv("SEQANNO_NO_BROWSER")
    desktop._open_browser_later("http://127.0.0.1:8010")
    assert opened == ["http://127.0.0.1:8010"]

    def no_browser(_url):
        raise RuntimeError("no browser")

    monkeypatch.setattr(desktop.webbrowser, "open", no_browser)
    desktop._open_browser_later("http://127.0.0.1:8010")  # 開けなくても止まらない


def test_app_dir_next_to_the_exe(monkeypatch, tmp_path):
    from pathlib import Path

    assert desktop._app_dir() == Path(desktop.__file__).resolve().parent
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(tmp_path / "TodenYOLO.exe"))
    assert desktop._app_dir() == tmp_path


def test_startup_error_is_logged_and_shown(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(desktop, "_app_dir", lambda: tmp_path)
    try:
        raise RuntimeError("port in use")
    except RuntimeError as exc:
        desktop._show_startup_error(exc)
    err = capsys.readouterr().err
    assert "起動に失敗しました" in err and "annotator_error.log" in err
    assert "RuntimeError: port in use" in (tmp_path / "annotator_error.log").read_text(encoding="utf-8")

    monkeypatch.setattr(desktop, "_app_dir", lambda: tmp_path / "missing" / "dir")
    desktop._show_startup_error(RuntimeError("x"))
    assert "詳細ログ" not in capsys.readouterr().err

    monkeypatch.setattr(sys, "stderr", None)
    desktop._show_startup_error(RuntimeError("x"))  # 標準エラーが無い windowed exe でも落ちない


def test_startup_error_uses_a_message_box_on_windows(monkeypatch, tmp_path):
    import ctypes

    shown = []
    monkeypatch.setattr(desktop, "_app_dir", lambda: tmp_path)
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.delenv("SEQANNO_NO_DIALOG", raising=False)
    user32 = SimpleNamespace(MessageBoxW=lambda _h, text, title, _flags: shown.append((title, text)))
    monkeypatch.setattr(ctypes, "windll", SimpleNamespace(user32=user32), raising=False)
    desktop._show_startup_error(RuntimeError("boom"))
    assert shown and shown[0][0] == "TodenYOLO"

    monkeypatch.setattr(ctypes, "windll", SimpleNamespace(), raising=False)  # MessageBoxW が使えない
    desktop._show_startup_error(RuntimeError("boom"))


def test_running_the_launcher_file_starts_the_server_or_reports_the_failure(monkeypatch, tmp_path):
    import runpy

    runs = []

    def fake_run(app, **kwargs):
        runs.append(kwargs["port"])

    monkeypatch.setenv("SEQANNO_PORT", "8123")
    monkeypatch.setenv("SEQANNO_NO_BROWSER", "1")
    monkeypatch.setenv("SEQANNO_NO_DIALOG", "1")
    monkeypatch.setitem(sys.modules, "uvicorn", SimpleNamespace(run=fake_run))
    monkeypatch.setitem(sys.modules, "main", SimpleNamespace(app=object()))
    runpy.run_path(desktop.__file__, run_name="__main__")
    assert runs == [8123]

    def broken_run(app, **kwargs):
        raise OSError("address in use")

    monkeypatch.setitem(sys.modules, "uvicorn", SimpleNamespace(run=broken_run))
    monkeypatch.setattr(sys, "executable", str(tmp_path / "TodenYOLO.exe"))
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    with pytest.raises(SystemExit) as exit_info:
        runpy.run_path(desktop.__file__, run_name="__main__")
    assert exit_info.value.code == 1
    assert "address in use" in (tmp_path / "annotator_error.log").read_text(encoding="utf-8")
