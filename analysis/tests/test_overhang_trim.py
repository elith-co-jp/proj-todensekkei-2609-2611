"""配線の端が、自分のインク無しで直交する配線を 2 本以上越えて伸びていたら縮める（DD-0013）。"""

from __future__ import annotations

import numpy as np

from analysis.wiring.detect import trim_overhangs
from analysis.wiring.shapes import Box, DetectedWire


def _wire(wid: str, orientation: str, axis: int, start: int, end: int) -> DetectedWire:
    box = Box(axis - 1, start, axis + 1, end) if orientation == "v" else Box(start, axis - 1, end, axis + 1)
    return DetectedWire(wid, orientation, box, axis, start, end, end - start, 0.9, 1.0, "test", [])


def _draw(binary: np.ndarray, wire: DetectedWire, start: int | None = None, end: int | None = None) -> None:
    lo, hi = start if start is not None else wire.span_start, end if end is not None else wire.span_end
    if wire.orientation == "v":
        binary[lo:hi + 1, wire.axis - 1:wire.axis + 2] = 255
    else:
        binary[wire.axis - 1:wire.axis + 2, lo:hi + 1] = 255


def _page(vertical_ink_end: int, vertical_end: int):
    """縦線（x=100）が y=0 から下り、y=60・72・84 の 3 本の横線（x=20〜180）が横切る。縦線のインクは vertical_ink_end まで。"""
    binary = np.zeros((120, 200), dtype=np.uint8)
    horizontals = [_wire(f"h{y}", "h", y, 20, 180) for y in (60, 72, 84)]
    for wire in horizontals:
        _draw(binary, wire)
    vertical = _wire("v", "v", 100, 0, vertical_end)
    _draw(binary, vertical, 0, vertical_ink_end)
    return binary, vertical, horizontals


def test_end_running_past_its_corner_over_other_lines_is_trimmed():
    """縦線は y=60 の横線で曲がるのに、検出は y=84 の横線まで伸びた（ER21277）。y=60 の先は自分のインクが無い。"""
    binary, vertical, horizontals = _page(vertical_ink_end=60, vertical_end=84)
    trimmed = {w.id: w for w in trim_overhangs(binary, [vertical, *horizontals])}["v"]
    assert trimmed.span_end < 60


def test_end_meeting_one_line_is_kept():
    """y=84 の横線（母線など）に T 字で着く縦線は、途中の横線を自分のインクで横切るので縮めない。"""
    binary, vertical, horizontals = _page(vertical_ink_end=84, vertical_end=84)
    trimmed = {w.id: w for w in trim_overhangs(binary, [vertical, *horizontals])}["v"]
    assert trimmed.span_end == 84


def test_line_broken_by_a_symbol_is_kept():
    """端の先で線が長く途切れる（接点で 90 px ほど途切れる母線からの線）のは、はみ出しではない。"""
    binary, vertical, horizontals = _page(vertical_ink_end=30, vertical_end=84)
    trimmed = {w.id: w for w in trim_overhangs(binary, [vertical, *horizontals])}["v"]
    assert trimmed.span_end == 84

