"""単一実行ファイル（exe）用ランチャ。

ローカルで uvicorn を起動し、既定ブラウザで UI を開くだけの薄い入口。
外部通信はせず 127.0.0.1 のみで待ち受ける。

    開発:   python desktop.py
    配布物: TodenYOLO.exe をダブルクリック
"""

from __future__ import annotations

import contextlib
import multiprocessing
import os
import socket
import sys
import threading
import time
import traceback
import webbrowser
from pathlib import Path

HOST = "127.0.0.1"
PREFERRED_PORTS = (8010, 8011, 8020, 8080, 8000)
_NULL_STREAMS = []


def _pick_port() -> int:
    """待ち受け可能なポートを選ぶ。候補が全て埋まっていれば OS 任せ。"""
    for port in PREFERRED_PORTS:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                sock.bind((HOST, port))
                return port
            except OSError:
                continue
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind((HOST, 0))
        return sock.getsockname()[1]


def _open_browser_later(url: str) -> None:
    if os.environ.get("SEQANNO_NO_BROWSER") == "1":
        return
    time.sleep(1.2)
    with contextlib.suppress(Exception):  # ブラウザが開けなくても本体は動かす
        webbrowser.open(url)


def _app_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def _show_startup_error(exc: Exception) -> None:
    log_path = _app_dir() / "annotator_error.log"
    try:
        log_path.write_text(traceback.format_exc(), encoding="utf-8")
    except Exception:  # noqa: BLE001
        log_path = None

    message = f"TodenYOLO の起動に失敗しました。\n\n{exc}"
    if log_path:
        message += f"\n\n詳細ログ: {log_path}"

    if sys.platform.startswith("win") and os.environ.get("SEQANNO_NO_DIALOG") != "1":
        try:
            import ctypes

            ctypes.windll.user32.MessageBoxW(None, message, "TodenYOLO", 0x10)
            return
        except Exception:  # noqa: BLE001
            pass
    if sys.stderr is not None:
        print(message, file=sys.stderr)


def _ensure_standard_streams() -> None:
    """Windowed exe の未接続・非 UTF-8 標準ストリームを安全な捨て先へ逃がす。"""
    frozen_windows = getattr(sys, "frozen", False) and sys.platform.startswith("win")
    for name in ("stdout", "stderr"):
        stream = getattr(sys, name, None)
        if frozen_windows or stream is None:
            stream = open(os.devnull, "w", encoding="utf-8", buffering=1)
            setattr(sys, name, stream)
            _NULL_STREAMS.append(stream)
            continue
        # 非凍結でも cp1252 等のストリームは日本語出力で落ちるため UTF-8 化する
        encoding = (getattr(stream, "encoding", None) or "").lower().replace("-", "")
        if encoding not in {"utf8"}:
            with contextlib.suppress(Exception):
                stream.reconfigure(encoding="utf-8", errors="replace")


def main() -> None:
    os.environ.setdefault("SEQANNO_DESKTOP", "1")
    _ensure_standard_streams()

    import uvicorn

    from main import app  # 遅延 import（uvicorn 準備後にアプリを構築）

    port = int(os.environ["SEQANNO_PORT"]) if "SEQANNO_PORT" in os.environ else _pick_port()
    url = f"http://{HOST}:{port}"

    print("=" * 60)
    print(" TodenYOLO")
    print(f"  ブラウザで {url} を開きます。")
    print("  終了するには画面の「アプリを終了」を押してください。")
    print("=" * 60)

    threading.Thread(target=_open_browser_later, args=(url,), daemon=True).start()

    with contextlib.suppress(KeyboardInterrupt):
        uvicorn.run(
            app,
            host=HOST,
            port=port,
            log_level="warning",
            log_config=None,
            access_log=False,
        )


if __name__ == "__main__":
    try:
        multiprocessing.freeze_support()
        main()
    except Exception as exc:  # noqa: BLE001  凍結時はメッセージボックスとログで原因を見せる
        _show_startup_error(exc)
        sys.exit(1)
