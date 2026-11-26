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


LABEL_BOX_COLUMNS = ("label_cx", "label_cy", "label_w", "label_h")


def label_box_columns(box: object) -> dict[str, float | None]:
    """ラベルの文字の位置（{cx, cy, w, h} の dict か、同じ名前の属性を持つもの）を記号の列の値にする（DD-0049）。

    無い・壊れた値は 4 列とも None。座標は sanitize_box と同じく画像の内側に丸める。
    """
    if box is None:
        return dict.fromkeys(LABEL_BOX_COLUMNS)
    get = box.get if isinstance(box, dict) else (lambda k: getattr(box, k, None))
    try:
        values = sanitize_box(*(float(get(k)) for k in ("cx", "cy", "w", "h")))
    except (TypeError, ValueError):
        return dict.fromkeys(LABEL_BOX_COLUMNS)
    return dict(zip(LABEL_BOX_COLUMNS, values, strict=True))


def label_box_dict(row: object) -> dict[str, float] | None:
    """記号の行（ORM・SELECT のどちらでも）からラベルの文字の位置を読む。付けていなければ None。"""
    values = [getattr(row, k, None) for k in LABEL_BOX_COLUMNS]
    if any(v is None for v in values):
        return None
    return dict(zip(("cx", "cy", "w", "h"), values, strict=True))
