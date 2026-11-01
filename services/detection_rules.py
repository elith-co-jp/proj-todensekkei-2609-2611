"""検出枠をシンボル候補と領域枠に分ける規則。"""

from __future__ import annotations

from typing import Literal

DetectionKind = Literal["symbol", "region"]

REGION_MIN_AREA = 0.04
REGION_MIN_SPAN = 0.40


def detection_kind(w: float, h: float) -> DetectionKind:
    """正規化サイズ (w, h) の検出枠がシンボル候補か領域枠かを返す。

    12 クラスのマスタにこの大きさのシンボルは無く、ここまで大きい枠は作図領域・DI 範囲・
    雲マーク・LOC の枠である。LOC や他盤の範囲は将来の配線情報になるため、捨てずに
    kind="region" として残す。
    """
    if w * h >= REGION_MIN_AREA or max(w, h) >= REGION_MIN_SPAN:
        return "region"
    return "symbol"
