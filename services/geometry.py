"""座標変換のユーティリティ。

ピクセル座標と正規化座標（YOLO 形式）の変換をこの 1 ファイルに集約する。
エクスポート・インポート・UI 保存のすべてがここを共有すること。
"""

from __future__ import annotations

MIN_SIZE = 1e-4


def clamp01(v: float) -> float:
    return 0.0 if v < 0.0 else (min(v, 1.0))


def normalize_box(x1: float, y1: float, x2: float, y2: float, w: int, h: int) -> tuple[float, float, float, float]:
    """ピクセル座標の矩形（左上・右下）を正規化 (cx, cy, bw, bh) に変換する。"""
    if w <= 0 or h <= 0:
        raise ValueError("画像サイズが不正です")
    left, right = (x1, x2) if x1 <= x2 else (x2, x1)
    top, bottom = (y1, y2) if y1 <= y2 else (y2, y1)
    cx = clamp01(((left + right) / 2.0) / w)
    cy = clamp01(((top + bottom) / 2.0) / h)
    bw = max(MIN_SIZE, min(1.0, (right - left) / w))
    bh = max(MIN_SIZE, min(1.0, (bottom - top) / h))
    return cx, cy, bw, bh


def denormalize_box(cx: float, cy: float, bw: float, bh: float, w: int, h: int) -> tuple[float, float, float, float]:
    """正規化 (cx, cy, bw, bh) をピクセル座標の (x1, y1, x2, y2) に戻す。"""
    px, py = cx * w, cy * h
    pw, ph = bw * w, bh * h
    return px - pw / 2.0, py - ph / 2.0, px + pw / 2.0, py + ph / 2.0


def sanitize_box(cx: float, cy: float, bw: float, bh: float) -> tuple[float, float, float, float]:
    """外部入力の正規化座標を安全な範囲に丸める。"""
    bw = max(MIN_SIZE, min(1.0, float(bw)))
    bh = max(MIN_SIZE, min(1.0, float(bh)))
    return clamp01(float(cx)), clamp01(float(cy)), bw, bh
