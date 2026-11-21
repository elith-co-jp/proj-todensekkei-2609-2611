"""評価の 1 コマンド（DD-0038）: 基準値との比べ方と、スキャン風の劣化。"""

import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

import eval_suite  # noqa: E402

from analysis.synthetic_circuits.scan import scan_variant  # noqa: E402


def test_compare_flags_only_metrics_that_got_worse_beyond_tolerance():
    base = {"drawings.scan.wire_recall": 0.80, "logic.false_alarms": 0, "logic.silent_harm": 10,
            "drawings.clean.errors": 0, "reading.condition_ok": 1.0, "gone": 1.0}
    now = {"drawings.scan.wire_recall": 0.797, "logic.false_alarms": 1, "logic.silent_harm": 9,
           "drawings.clean.errors": 0, "reading.condition_ok": 0.98, "new": 0.1}
    worse = {w["metric"] for w in eval_suite.compare(now, base)}
    assert worse == {"logic.false_alarms", "reading.condition_ok"}


def test_scan_variant_keeps_size_and_is_deterministic():
    image = np.full((60, 80), 255, np.uint8)
    image[30, 10:70] = 0
    a, b = scan_variant(image, 3), scan_variant(image, 3)
    assert a.shape == image.shape and a.dtype == np.uint8
    assert np.array_equal(a, b)
    assert a[30, 40] < 128
