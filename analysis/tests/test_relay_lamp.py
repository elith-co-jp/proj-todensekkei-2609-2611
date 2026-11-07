"""リレーコイルと表示灯の判別: synthetic circles only."""

from __future__ import annotations

import cv2
import numpy as np

from analysis.relay_lamp import review_circles


def _box(cx: int, cy: int) -> dict[str, float]:
    return {"x0": cx - 32, "y0": cy - 42, "x1": cx + 32, "y1": cy + 42}


def _symbol(sid: str, cx: int, cy: int, label: str, class_name: str = "relay_coil") -> dict:
    return {"id": sid, "type": class_name, "class_name": class_name, "class_label": "リレーコイル",
            "bbox": _box(cx, cy), "label": label}


def test_x_marked_circle_becomes_lamp_and_relay_table_device_stays_relay():
    page = np.zeros((300, 700), np.uint8)
    for cx in (100, 300, 500):
        cv2.circle(page, (cx, 150), 22, 255, 2)
    a = 10
    cv2.line(page, (100 - a, 150 - a), (100 + a, 150 + a), 255, 2)
    cv2.line(page, (100 - a, 150 + a), (100 + a, 150 - a), 255, 2)
    symbols = [
        _symbol("lamp", 100, 150, ""),
        _symbol("coil", 300, 150, "33HB FV2306"),
        _symbol("word_only", 500, 150, ""),
        _symbol("contact", 300, 260, "33HB FV2306", "contact_a"),
    ]
    text_result = {
        "texts": [{"text": "RL", "box": [540, 140, 556, 152]}],
        "relay_table": {"rows": [{"device": "33HB FV2306", "terminal": "5-9-1", "contact": "a", "sheet": "本", "position": "3D"}]},
    }

    counts = review_circles(page, symbols, text_result)

    lamp, coil, word_only = symbols[0], symbols[1], symbols[2]
    assert lamp["class_name"] == "lamp" and lamp["class_label"] == "表示灯" and lamp["detected_class"] == "relay_coil"
    assert lamp["symbol_review"]["status"] == "確定"
    assert coil["class_name"] == "relay_coil" and coil["symbol_review"]["status"] == "確定"
    assert {c["cue"] for c in coil["symbol_review"]["cues"]} == {"relay_table", "same_device_contacts"}
    assert word_only["class_name"] == "relay_coil", "a lamp word alone does not change the class"
    assert word_only["symbol_review"] == {"class_suggestion": "lamp", "status": "要確認",
                                          "cues": [{"cue": "lamp_word", "suggests": "lamp", "text": "RL"}]}
    assert counts == {"reviewed": 3, "確定": 2, "要確認": 1, "changed": 1}

