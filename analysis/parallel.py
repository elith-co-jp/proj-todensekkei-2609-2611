"""OCR などの重い読み取りを、結果の順番を保ったままスレッドで並べて実行する。

onnxruntime は推論中に GIL を手放すので、図面の小さな画像を 1 枚ずつ読むより、数枚を同時に読むほうが
CPU を使い切れる（DGX の 1 図面で OCR 34 秒 → 約 22 秒、読んだ文字は同じ）。並べる数は
SEQANNO_OCR_WORKERS（1 で並べない）。評価のように図面をプロセスで並べるときは 1 にする。
"""

from __future__ import annotations

import os
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from typing import TypeVar

T = TypeVar("T")
R = TypeVar("R")

DEFAULT_WORKERS = 4


def workers() -> int:
    configured = os.environ.get("SEQANNO_OCR_WORKERS")
    if configured:
        return max(1, int(configured))
    return max(1, min(DEFAULT_WORKERS, os.cpu_count() or 1))


def ordered_map(function: Callable[[T], R], items: Sequence[T]) -> list[R]:
    """function を items に当てた結果を items の順に返す。"""
    count = min(workers(), len(items))
    if count <= 1:
        return [function(item) for item in items]
    with ThreadPoolExecutor(max_workers=count) as pool:
        return list(pool.map(function, items))


def run_all(tasks: Sequence[Callable[[], R]]) -> list[R]:
    """引数の無い関数をまとめて実行し、結果を並びの順に返す。"""
    return ordered_map(lambda task: task(), tasks)
