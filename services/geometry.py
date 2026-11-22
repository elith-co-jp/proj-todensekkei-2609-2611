"""座標変換のユーティリティ。

ピクセル座標と正規化座標（YOLO 形式）の変換をこの 1 ファイルに集約する。
エクスポート・インポート・UI 保存のすべてがここを共有すること。
"""

from __future__ import annotations

MIN_SIZE = 1e-4


def clamp01(v: float) -> float:
    return 0.0 if v < 0.0 else (min(v, 1.0))


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
