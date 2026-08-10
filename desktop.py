"""単一実行ファイル（exe）用ランチャ。

ローカルで uvicorn を起動し、既定ブラウザで UI を開くだけの薄い入口。
外部通信はせず 127.0.0.1 のみで待ち受ける。

    開発:   python desktop.py
    配布物: SeqAnnotator.exe をダブルクリック
"""

from __future__ import annotations

import socket
import sys
import threading
import time
import webbrowser

HOST = "127.0.0.1"
PREFERRED_PORTS = (8010, 8011, 8020, 8080, 8000)


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
    time.sleep(1.2)
    try:
        webbrowser.open(url)
    except Exception:  # noqa: BLE001  ブラウザが開けなくても本体は動かす
        pass


def main() -> None:
    import uvicorn

    from main import app  # 遅延 import（uvicorn 準備後にアプリを構築）

    port = _pick_port()
    url = f"http://{HOST}:{port}"

    print("=" * 60)
    print(" シーケンス図アノテーションツール")
    print(f"  ブラウザで {url} を開きます。")
    print("  終了するにはこのウィンドウを閉じるか Ctrl+C を押してください。")
    print("=" * 60)

    threading.Thread(target=_open_browser_later, args=(url,), daemon=True).start()

    try:
        uvicorn.run(app, host=HOST, port=port, log_level="warning")
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # noqa: BLE001  凍結時にコンソールを残して原因を見せる
        print(f"起動に失敗しました: {exc}", file=sys.stderr)
        input("Enter キーを押すと終了します...")
        raise
