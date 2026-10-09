"""Synthetic cases for disconnected pixels inside a tilted wire's bounding box."""

import numpy as np
import pytest


@pytest.fixture
def detector():
    pytest.importorskip("cv2")
    from analysis import runtime

    assert runtime is not None
    import analyze_pdf_structure

    return analyze_pdf_structure


@pytest.mark.parametrize("orientation", ["h", "v"])
def test_tilted_wire_does_not_swallow_detached_label_strokes(detector, orientation):
    import cv2

    mask = np.zeros((180, 720), dtype=np.uint8)
    cv2.line(mask, (40, 100), (670, 88), 255, 2)
    # Close to the global bbox, but separated from the wire at this actual x.
    mask[102:112, 610:660] = 255
    mask[114:124, 55:105] = 255
    if orientation == "v":
        mask = mask.T.copy()
    lines = detector.scan_line_segments(mask, orientation, 18, isolate_components=True)
    long = [line for line in lines if line.length >= 625]
    assert len(long) == 1
    box = long[0].box
    assert (box.height if orientation == "h" else box.width) <= 16
    assert detector.filter_line_segments(long) == long
    assert len(lines) == 1  # Compact label strokes still fail the existing aspect-ratio check.


@pytest.mark.parametrize("orientation", ["h", "v"])
def test_one_tilted_stroke_remains_one_candidate(detector, orientation):
    import cv2

    mask = np.zeros((180, 720), dtype=np.uint8)
    cv2.line(mask, (40, 100), (670, 88), 255, 2)
    if orientation == "v":
        mask = mask.T.copy()
    lines = detector.scan_line_segments(mask, orientation, 18, isolate_components=True)
    assert len(lines) == 1
    assert lines[0].length >= 625


def test_separate_real_parallel_strokes_remain_separate(detector):
    mask = np.zeros((120, 720), dtype=np.uint8)
    mask[80:82, 30:680] = 255
    mask[84:86, 30:680] = 255
    lines = detector.scan_line_segments(mask, "h", 18, isolate_components=True)
    assert len(lines) == 2
    assert [line.box.y0 for line in lines] == [80, 84]


def test_blank_mask_has_no_candidates(detector):
    mask = np.zeros((50, 50), dtype=np.uint8)
    assert detector.scan_line_segments(mask, "h", 18) == []
    assert detector.scan_line_segments(mask, "v", 18) == []


@pytest.mark.parametrize("orientation", ["h", "v"])
def test_detached_thin_fragments_are_not_promoted_to_extra_wires(detector, orientation):
    import cv2

    mask = np.zeros((180, 720), dtype=np.uint8)
    cv2.line(mask, (40, 100), (670, 88), 255, 2)
    mask[102:104, 510:660] = 255
    mask[104:106, 160:480] = 255
    if orientation == "v":
        mask = mask.T.copy()
    lines = detector.scan_line_segments(mask, orientation, 18, isolate_components=True)
    assert len(lines) == 1
    assert lines[0].length >= 625
    assert (lines[0].box.height if orientation == "h" else lines[0].box.width) <= 16


def test_raw_scan_keeps_existing_region_and_vertical_restore_behavior(detector):
    mask = np.zeros((120, 720), dtype=np.uint8)
    mask[80:82, 30:680] = 255
    mask[84:86, 30:680] = 255
    lines = detector.scan_line_segments(mask, "h", 18)
    assert len(lines) == 1
    assert lines[0].box == detector.Box(30, 80, 680, 86)


def test_partial_spans_keep_existing_recovery_geometry(detector):
    mask = np.zeros((80, 400), dtype=np.uint8)
    mask[20:22, 20:220] = 255
    mask[23:25, 100:350] = 255
    original = detector.scan_line_segments(mask, "h", 18)
    corrected = detector.scan_line_segments(mask, "h", 18, isolate_components=True)
    assert corrected == original
    assert len(corrected) == 1


def test_wire_fix_does_not_change_vertical_scan_paths(detector, monkeypatch):
    calls = []
    scan = detector.scan_line_segments

    def record(mask, orientation, min_length, **options):
        calls.append((orientation, options.get("isolate_components", False)))
        return scan(mask, orientation, min_length, **options)

    monkeypatch.setattr(detector, "scan_line_segments", record)
    binary = np.zeros((100, 200), dtype=np.uint8)
    regions = detector.PageRegions(None, None, None, None)
    detector.build_initial_wire_candidates(binary, regions, 200, 100)
    assert calls == [("h", True), ("v", False), ("v", False)]
