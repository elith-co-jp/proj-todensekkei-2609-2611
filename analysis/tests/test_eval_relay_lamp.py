from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

from eval_relay_lamp import summarize_decisions


def test_summary_counts_before_after_confusion_and_missing_class_coverage():
    decisions = [
        {
            "sheet": "ER1",
            "gold_class": "relay_coil",
            "detected_class": "lamp",
            "final_class": "relay_coil",
            "status": "確定",
        },
        {
            "sheet": "ER2",
            "gold_class": "relay_coil",
            "detected_class": "relay_coil",
            "final_class": "relay_coil",
            "status": "要確認",
        },
    ]

    summary = summarize_decisions(decisions)

    assert summary["gold"] == {"relay_coil": 2, "lamp": 0}
    assert summary["before"] == {"errors": 1, "confusion": {"relay_coil->lamp": 1}}
    assert summary["after"] == {"errors": 0, "confusion": {}}
    assert summary["review_required"] == 1
    assert summary["both_classes_scored"] is False
