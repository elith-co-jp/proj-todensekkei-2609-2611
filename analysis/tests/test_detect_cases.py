"""線の候補の検出・まとめ・選別の細かい場合（detect）。画像は numpy で描く。"""

from __future__ import annotations

import numpy as np
import pytest

from analysis.wiring import detect
from analysis.wiring.shapes import Box, DetectedLabel, DetectedWire, LineSegment, PageRegions

NONE = PageRegions(None, None, None, None)


def _h(x0, y, x1, thick=1):
    return LineSegment("h", Box(x0, y, x1, y + thick))


def _v(x, y0, y1, thick=1):
    return LineSegment("v", Box(x, y0, x + thick, y1))


def _label(box, accepted=True, lid="L"):
    return DetectedLabel(lid, "X", "X", "device", 0.9, "ocr", box, accepted, "ok")


def test_orientation_must_be_h_or_v():
    with pytest.raises(ValueError, match="orientation"):
        detect.scan_line_segments(np.zeros((5, 5), np.uint8), "d", 2)


def test_support_and_gap_helpers_at_edges():
    binary = np.zeros((20, 20), np.uint8)
    assert detect.support_vector_for_span(binary, "h", 5, 10, 10).size == 0
    assert detect.close_short_1d_gaps(np.array([], dtype=bool), 3).size == 0
    one = np.array([False, True, False])
    assert detect.close_short_1d_gaps(one, 3).tolist() == one.tolist()
    outside = _h(50, 5, 80)                                            # 画像の外の線
    assert detect.line_ink_coverage(binary, outside) == 0.0
    assert detect.looks_like_text_row(binary, outside) is False
    assert detect.gap_has_ink_support(binary, "h", 5, 0, 5) is True    # 短い隙間は確かめない
    assert detect.gap_has_ink_support(binary, "h", 5, 40, 60) is False  # 画像の外
    assert detect.line_gap_span(_h(50, 0, 60), _h(10, 0, 20)) == (20, 50)


def test_ink_band_of_horizontal_lines():
    binary = np.zeros((40, 100), np.uint8)
    binary[22:24, 10:90] = 255
    assert detect.ink_band(binary, LineSegment("h", Box(10, 10, 90, 30))) == (22, 23)
    assert detect.ink_band(binary, _h(10, 2, 90, thick=4)) == (4, 4)         # インクが無ければ軸
    assert detect.split_line_by_ink_support(binary, _h(200, 5, 300), min_length=5, max_empty_run=2) == []


def test_sparse_runs_are_not_lines():
    binary = np.zeros((10, 100), np.uint8)
    binary[5, 10:70:4] = 255                                               # 4 px おきの点
    assert detect.split_line_by_ink_support(binary, _h(10, 5, 70), min_length=10, max_empty_run=3) == []


def test_vertical_merge_keeps_the_longer_spine():
    merged = detect.merge_candidate_lines([_v(10, 0, 100, 2), _v(19, 105, 130, 2)], "v")
    assert merged == [LineSegment("v", Box(10, 0, 12, 130))]


def test_regions_without_a_frame_and_without_regions():
    regions = detect.detect_regions(1000, 800, [], [])
    assert regions.outer_frame == Box(30, 40, 950, 744)
    line = _v(10, 0, 100)
    assert detect.center_in_box(line, None) is True
    assert detect.is_near_outer_frame(line, NONE) is False
    assert detect.is_left_voltage_arrow(line, NONE) is False
    assert detect.is_right_reference_block_line(line, NONE) is False


def test_restore_vertical_columns_splits_far_pieces_and_drops_short_single_ones():
    binary = np.zeros((600, 200), np.uint8)
    pieces = [_v(100, 20, 60), _v(100, 65, 110),                            # 2 本でまとまる（90 px）
              _v(100, 300, 400)]                                           # 離れた 1 本は 120 px 未満で落とす
    for piece in pieces:
        binary[piece.box.y0:piece.box.y1, 100] = 255
    restored = detect.restore_vertical_columns(pieces, binary, NONE, 200, 600)
    assert [(line.box.y0, line.box.y1) for line in restored] == [(20, 110)]


def test_text_components_filters():
    binary = np.zeros((200, 400), np.uint8)
    binary[10:13, 20:80] = 255           # 横に長い棒
    binary[20:70, 150:153] = 255         # 縦に長い棒
    binary[30:34, 200:280] = 255         # 細長すぎる
    binary[100:102, 300] = 255           # 5 px の L 字（インクが少ない）
    binary[100:103, 301] = 255
    assert detect.detect_text_regions_from_components(binary, NONE) == []
    solid = np.full((18, 95), 255, np.uint8)                               # 枠いっぱいの塗り（文字にしては濃い）
    assert detect.detect_text_regions_from_components(solid, NONE) == []


def test_text_groups_outside_the_drawing_or_too_wide():
    binary = np.zeros((120, 400), np.uint8)
    binary[60:75, 52:67] = 255           # 重心は図面の中、尾を含む枠の中心は外
    binary[66, 10:52] = 255
    regions = PageRegions(None, Box(50, 0, 400, 120), None, None)
    assert detect.detect_text_regions_from_components(binary, regions) == []
    wide = np.zeros((120, 400), np.uint8)
    for x in range(10, 380, 12):         # 文字の並びが 260 px を超える
        wide[50:58, x:x + 6] = 255
    assert detect.detect_text_regions_from_components(wide, NONE) == []
    top = np.zeros((120, 400), np.uint8)
    for x in range(10, 230, 12):         # 画像の上端に沿った低い並び
        top[0:3, x:x + 6] = 255
    assert detect.detect_text_regions_from_components(top, NONE) == []


def test_label_overlap_and_nearby_labels():
    labels = [_label(Box(20, 0, 40, 10)), _label(Box(0, 40, 10, 60), lid="V"),
              _label(Box(20, 0, 40, 10), accepted=False, lid="no"), _label(Box(500, 500, 510, 510), lid="far")]
    assert detect.line_label_overlap_ratio(_h(0, 5, 100), labels) == pytest.approx(0.26)
    assert detect.line_label_overlap_ratio(_v(5, 0, 100), labels) == pytest.approx(0.26)
    assert detect.line_label_overlap_ratio(_h(0, 30, 100), labels) == 0
    assert detect.line_box_distance(_h(0, 5, 10), Box(13, 5, 20, 20)) == pytest.approx(3.0)
    assert detect.nearby_label_ids_for_line(_h(0, 5, 100), labels) == ["L", "V"]


def _draw(binary, *lines):
    for line in lines:
        b = line.box
        binary[b.y0:b.y1, b.x0:b.x1] = 255


def test_detected_wire_rules_for_short_branches_drops_and_text_rows():
    binary = np.zeros((400, 900), np.uint8)
    branch, drop = _h(100, 100, 200, 2), _v(100, 60, 160, 2)               # 短い横の枝と短い縦の落とし
    dashed = _h(100, 300, 700, 2)                                         # 長いが細かく途切れる（文字の並び）
    _draw(binary, branch, drop)
    for x in range(100, 700, 6):
        binary[300:302, x:x + 3] = 255
    wires = detect.build_detected_wires(binary, [branch, dashed], [drop], [], NONE)
    assert {w.reason for w in wires} == {"short_horizontal_branch", "connected_vertical_drop"}
    outside = PageRegions(None, Box(0, 0, 10, 10), None, None)
    assert detect.build_detected_wires(binary, [branch], [drop], [], outside) == []


def _wire(orientation, box, start, end):
    axis = (box.y0 + box.y1) // 2 if orientation == "h" else (box.x0 + box.x1) // 2
    return DetectedWire("w", orientation, box, axis, start, end, end - start, 0.9, 1.0, "t", [])


def test_overhang_trimmed_at_the_start_and_blank_short_wires_kept():
    binary = np.zeros((200, 300), np.uint8)
    binary[100, 40:250] = 255                                             # 自分のインクは x=40 から
    wire = _wire("h", Box(20, 99, 250, 102), 20, 250)
    crossing = [_wire("v", Box(x, 0, x + 2, 200), 0, 200) for x in (24, 32)]
    for c in crossing:
        binary[:, c.box.x0:c.box.x1] = 255
    trimmed = detect.trim_overhangs(binary, [wire, *crossing])
    assert trimmed[0].span_start == 40
    blank = _wire("h", Box(0, 10, 5, 12), 0, 5)                           # インクの無い短い線はそのまま
    assert detect.trim_overhangs(np.zeros((50, 50), np.uint8), [blank]) == [blank]
