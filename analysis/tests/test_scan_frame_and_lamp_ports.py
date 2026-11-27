"""スキャンで枠の細線が消えた図面の描画範囲と、表示灯の推定の端子の位置（DD-0053）。"""

from __future__ import annotations

from analysis.wiring.detect import detect_regions
from analysis.wiring.ports import expected_ratios
from analysis.wiring.shapes import Box, LineSegment

W, H = 1755, 1241


def _h(x0, y, x1):
    return LineSegment("h", Box(x0, y, x1, y + 4))


def _v(x, y0, y1):
    return LineSegment("v", Box(x, y0, x + 3, y1))


def test_frame_lines_still_make_the_outer_frame():
    frame = [_h(44, 44, 1713), _h(44, 1196, 1713)]
    buses = [_h(105, 144, 1532), _h(225, 1095, 1652)]
    regions = detect_regions(W, H, [*frame, *buses], [_v(44, 44, 1199), _v(1710, 44, 1199)])
    assert regions.outer_frame == Box(44, 44, 1713, 1200)


def test_frame_lines_without_side_lines_need_aligned_ends():
    regions = detect_regions(W, H, [_h(44, 44, 1713), _h(46, 1196, 1711)], [])
    assert (regions.outer_frame.y0, regions.outer_frame.y1) == (44, 1200)


def test_two_buses_are_not_taken_for_the_frame():
    # 枠が消え、上下の母線（左右の端が 120 px ずれる）だけが長い横線として残った図面。
    regions = detect_regions(W, H, [_h(105, 144, 1532), _h(225, 1095, 1652)], [])
    outer = regions.outer_frame
    assert outer.y0 < 144 - 18 and regions.drawing_area.contains_point(800, 500)


def test_single_bus_does_not_collapse_the_drawing_area():
    # 長い横線が下の母線 1 本だけ。以前は枠の上下がその 1 本になり、描画範囲の高さが 0 で配線が 0 本だった。
    regions = detect_regions(W, H, [_h(225, 1096, 1652)], [])
    assert regions.drawing_area.height > H * 0.5


def test_lamp_ports_sit_near_the_circle_rim_like_coils():
    box = {"x0": 0, "y0": 840, "x1": 58, "y1": 898}
    assert expected_ratios("lamp", box, 65) == expected_ratios("relay_coil", box, 65) == [0.15, 0.85]
