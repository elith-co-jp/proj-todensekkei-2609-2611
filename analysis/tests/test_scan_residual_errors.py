"""強いスキャンで残った誤りの直し（DD-0055）。"""

from __future__ import annotations

import numpy as np
from PIL import Image

from analysis.wiring.faint_lines import paper_gray
from analysis.wiring.filter_wire_decorations import _is_device_outline


def test_lines_inside_a_coil_box_are_its_outline():
    """電磁弁の四角の上下の辺（枠の中の短い横線）は記号の絵で、配線ではない。"""
    solenoid = {"x0": 525, "y0": 840, "x1": 583, "y1": 898}
    top_edge = {"orientation": "h", "bbox": {"x0": 524, "y0": 839, "x1": 584, "y1": 842}}
    through = {"orientation": "v", "bbox": {"x0": 552, "y0": 139, "x1": 559, "y1": 1102}}
    wires = [top_edge, through]
    assert _is_device_outline(top_edge, [solenoid], 1.0, wires)
    assert not _is_device_outline(through, [solenoid], 1.0, wires)
    assert not _is_device_outline(top_edge, [], 1.0, wires)
    # 記号を貫く配線が無ければ（配線が記号の中で終わる）、枠の中の線も残す。
    assert not _is_device_outline(top_edge, [solenoid], 1.0, [top_edge])


def test_paper_gray_whitens_a_yellowed_page_only():
    page = np.full((50, 50), 235, dtype=np.uint8)
    page[20:23, :] = 120
    out = paper_gray(Image.fromarray(page))
    assert out[0, 0] == 255 and out[21, 0] < 140
    white = np.full((50, 50), 255, dtype=np.uint8)
    white[20:23, :] = 120
    assert np.array_equal(paper_gray(Image.fromarray(white)), white)
