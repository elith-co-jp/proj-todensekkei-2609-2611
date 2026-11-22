"""裏で動いている長い処理（学習・同期の解析）を数える。

EXE は画面からの合図が途切れると自分で終わる（main.py）。学習（CPU では数時間）や解析の途中で終わらないよう、
処理の間はここに印を付け、終わったら外す。裏の解析のジョブ（analysis_jobs）は自分で状態を持つので、ここには入れない。
プロセスの中だけで数える（DB の「実行中」の行は、落ちたあとに残ることがあるので使わない）。
"""

from __future__ import annotations

import threading
from collections import Counter
from collections.abc import Iterator
from contextlib import contextmanager

_lock = threading.Lock()
_counts: Counter[str] = Counter()


@contextmanager
def working(kind: str) -> Iterator[None]:
    """with の間、kind（"training" など）を実行中として数える。"""
    with _lock:
        _counts[kind] += 1
    try:
        yield
    finally:
        with _lock:
            _counts[kind] -= 1
            if _counts[kind] <= 0:
                del _counts[kind]


def active() -> list[str]:
    """実行中の処理の種類（名前の順）。"""
    with _lock:
        return sorted(_counts)
