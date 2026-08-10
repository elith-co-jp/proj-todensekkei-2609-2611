"""実行環境（開発 / PyInstaller で凍結した exe）の差異を吸収するヘルパ。

PyInstaller で 1 ファイル化すると、同梱リソースは一時展開先（``sys._MEIPASS``）に、
書き込みたいデータ（DB・画像）は exe 本体と同じ場所に置きたい。この 2 つを分けて返す。
"""

from __future__ import annotations

import sys
from pathlib import Path


def is_frozen() -> bool:
    """PyInstaller などで実行ファイル化されているか。"""
    return bool(getattr(sys, "frozen", False))


def resource_base() -> Path:
    """同梱リソース（``frontend/dist`` など読み取り専用ファイル）の基準ディレクトリ。"""
    if is_frozen():
        # onefile なら _MEIPASS（一時展開先）、onedir なら exe と同じ場所
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).resolve().parent))
    return Path(__file__).resolve().parent


def data_base() -> Path:
    """DB・画像など書き込み先の基準ディレクトリ。

    凍結時は exe 本体と同じ場所に置く（USB 等で持ち運んでもデータが残り、
    一時展開先に消えないようにするため）。開発時はリポジトリ直下。
    """
    if is_frozen():
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent
