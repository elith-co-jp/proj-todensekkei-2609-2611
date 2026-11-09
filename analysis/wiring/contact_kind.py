"""接点の棒の側で、a 接点と b 接点の検出を直す（DD-0023）。

記号の検出は種類ごとに重なりを除くので、a 接点か b 接点か迷った接点には 2 つの枠がほぼ同じ位置に残る。
回路では閉じたままの b 接点が a 接点と並列に入り、コイルが入力に関係なく励磁した（ER21299 の 43A3、ER21293 の 30CU）。

この図面集の書き方では、a 接点は 2 つの白丸の右に離れて縦の棒、b 接点は白丸の左に棒がある。30 枚の正解の接点
250 個で、棒が右なら a（236 個）、左なら b（12 個）、どちらとも言えないのは 2 個だった。棒の側で残す枠を決め、
決められないときは確信度の高い方を残す。

重ならない接点も、棒の側がはっきりしていれば種類をそれに合わせる。未学習の図面（交差検証）では a・b の取り違えが
8 個あり、棒の側で 7 個が正しくなる。検出が正しい接点で棒の側が逆を示したものは無かった（30 枚・交差検証とも）。
"""

from __future__ import annotations

from typing import Any

import numpy as np

from analysis.relay_links import CONTACT_CLASSES
from analysis.wiring import classes

# 2 つの枠を同じ接点とみなす重なり（IoU）。
SAME_CONTACT_IOU = 0.7
# 白丸の列から見て、棒を探す範囲（px）。左は -LEFT_FAR〜-NEAR、右は NEAR〜RIGHT_FAR。
BAR_NEAR_PX = 3
BAR_LEFT_FAR_PX = 10
BAR_RIGHT_FAR_PX = 12
# 棒の側を決めるのに要る、左右の縦の線の割合の差。
BAR_MARGIN = 0.15
# 枠の上下の外で、線が入る列を探す長さ。
LEAD_PROBE_PX = 8


def _iou(a: dict[str, Any], b: dict[str, Any]) -> float:
    x0, y0, x1, y1 = max(a["x0"], b["x0"]), max(a["y0"], b["y0"]), min(a["x1"], b["x1"]), min(a["y1"], b["y1"])
    inter = max(0.0, x1 - x0) * max(0.0, y1 - y0)
    union = (a["x1"] - a["x0"]) * (a["y1"] - a["y0"]) + (b["x1"] - b["x0"]) * (b["y1"] - b["y0"]) - inter
    return inter / union if union > 0 else 0.0


def _terminal_column(binary: np.ndarray, box: dict[str, Any]) -> int | None:
    """枠の上と下の外のどちらにも線がある列（接点の線が出入りする列）。無ければ None。"""
    height, width = binary.shape
    x0, y0, x1, y1 = (int(round(float(box[k]))) for k in ("x0", "y0", "x1", "y1"))
    above = binary[max(0, y0 - LEAD_PROBE_PX):max(0, y0), max(0, x0):min(width, x1)]
    below = binary[min(height, y1):min(height, y1 + LEAD_PROBE_PX), max(0, x0):min(width, x1)]
    if not above.size or not below.size:
        return None
    score = np.minimum(above.mean(axis=0), below.mean(axis=0))
    return x0 + int(score.argmax()) if score.max() > 0 else None


def bar_side(binary: np.ndarray, box: dict[str, Any]) -> str | None:
    """接点の棒が白丸の列の左（"b"）か右（"a"）か。決められなければ None。"""
    column = _terminal_column(binary, box)
    if column is None:
        return None
    height = float(box["y1"]) - float(box["y0"])
    ya, yb = int(round(float(box["y0"]) + 0.25 * height)), int(round(float(box["y1"]) - 0.25 * height))
    if yb - ya < 4:
        return None

    def strongest(lo: int, hi: int) -> float:
        cols = [binary[ya:yb, c].mean() for c in range(column + lo, column + hi + 1) if 0 <= c < binary.shape[1]]
        return max(cols) if cols else 0.0

    left, right = strongest(-BAR_LEFT_FAR_PX, -BAR_NEAR_PX), strongest(BAR_NEAR_PX, BAR_RIGHT_FAR_PX)
    if left > right + BAR_MARGIN:
        return "b"
    if right > left + BAR_MARGIN:
        return "a"
    return None


def merge_contact_duplicates(symbols: list[dict[str, Any]], binary: np.ndarray) -> list[dict[str, Any]]:
    """a 接点と b 接点の枠が重なる組から 1 つを残した記号の並び（元の並びの順）。"""
    mask = binary > 0
    dropped: set[int] = set()
    contacts = [i for i, s in enumerate(symbols) if s.get("class_name") in classes.CONTACTS]
    for n, i in enumerate(contacts):
        for j in contacts[n + 1:]:
            a, b = symbols[i], symbols[j]
            if i in dropped or j in dropped or a["class_name"] == b["class_name"] or _iou(a["bbox"], b["bbox"]) < SAME_CONTACT_IOU:
                continue
            side = bar_side(mask, a["bbox"])
            if side is not None:
                keep = i if a["class_name"] == f"contact_{side}" else j
            else:
                keep = i if float(a.get("confidence") or 0) >= float(b.get("confidence") or 0) else j
            dropped.add(j if keep == i else i)
    return [s for k, s in enumerate(symbols) if k not in dropped]


def correct_contact_kinds(symbols: list[dict[str, Any]], binary: np.ndarray) -> int:
    """棒の側と違う種類で検出された接点の種類を直し、直した数を返す。元の種類は detected_class に残す。"""
    mask = binary > 0
    fixed = 0
    for symbol in symbols:
        if symbol.get("class_name") not in classes.CONTACTS:
            continue
        side = bar_side(mask, symbol["bbox"])
        kind = f"contact_{side}" if side else None
        if kind is None or kind == symbol["class_name"]:
            continue
        symbol.update(detected_class=symbol["class_name"], class_name=kind, type=kind, class_label=CONTACT_CLASSES[kind],
                      contact_kind_source="bar_side")
        fixed += 1
    return fixed
