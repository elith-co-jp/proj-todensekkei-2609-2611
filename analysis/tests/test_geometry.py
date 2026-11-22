"""点・枠・線分の基本計算。"""

from __future__ import annotations

import pytest

from analysis.wiring import geometry as g


def test_spans_centres_and_boxes():
    assert g.merged_span_length([(0, 10), (5, 20), (30, 35)]) == 25
    assert g.merged_span_length([]) == 0
    assert g.box_center({"x0": 0, "y0": 10, "x1": 20, "y1": 30}) == (10.0, 20.0)
    assert g.wire_bbox({"orientation": "h", "axis": 50, "span_start": 10, "span_end": 90}, padding=2) == {
        "x0": 8, "y0": 48, "x1": 92, "y1": 52}
    assert g.wire_bbox({"orientation": "v", "axis": 50, "span_start": 10, "span_end": 90}, padding=2) == {
        "x0": 48, "y0": 8, "x1": 52, "y1": 92}
    assert g.payload_box({"bbox": {"x0": "a", "y0": 0, "x1": 1, "y1": 1}}) is None
    assert g.segment_orientation((0, 0), (0, 10)) == "v"


@pytest.mark.parametrize("wire, point, distance", [
    ({"orientation": "h", "axis": 0, "span_start": 0, "span_end": 10}, (15, 0), 5.0),
    ({"orientation": "h", "axis": 0, "bbox": {"x0": 0, "x1": 10}}, (5, 3), 3.0),
    ({"orientation": "v", "axis": 0, "span_start": 0, "span_end": 10}, (0, -4), 4.0),
    ({"orientation": "v", "axis": 0, "bbox": {"y0": 0, "y1": 10}}, (3, 5), 3.0),
])
def test_distance_to_wires_without_polylines(wire, point, distance):
    assert g.point_to_wire_distance(point, wire) == distance


def test_projection_on_a_zero_length_segment():
    assert g.point_segment_projection((3, 4), (0, 0), (0, 0)) == (0.0, 5.0, (0, 0))


def test_text_and_box_overlap_ratios():
    text = [{"bbox": {"x0": 10, "y0": -5, "x1": 20, "y1": 5}}, {"text": "no box"}]
    assert g.segment_text_overlap_ratio((0, 0), (0, 0), text) == 0.0
    assert g.segment_text_overlap_ratio((0, 0), (100, 0), text) == pytest.approx(0.16)
    vertical = [{"bbox": {"x0": -5, "y0": 10, "x1": 5, "y1": 20}}, {"text": "no box"}]
    assert g.segment_text_overlap_ratio((0, 0), (0, 100), vertical) == pytest.approx(0.16)
    box = {"x0": 10, "y0": -5, "x1": 20, "y1": 5}
    assert g.segment_box_overlap_ratio((0, 0), (0, 0), box) == 0.0
    assert g.segment_box_overlap_ratio((0, 0), (100, 0), box) == pytest.approx(0.1)
    assert g.segment_box_overlap_ratio((0, 50), (100, 50), box) == 0.0
    assert g.segment_box_overlap_ratio((15, -50), (15, 50), box) == pytest.approx(0.1)
    assert g.segment_box_overlap_ratio((50, -50), (50, 50), box) == 0.0
