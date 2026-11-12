"""リレーコイルと表示灯の判別: synthetic circles only."""

from __future__ import annotations

import cv2
import numpy as np

from analysis.relay_lamp import (
    CircleClass,
    CircleEvidence,
    ReviewStatus,
    classify_circle,
    review_circles,
)


def _box(cx: int, cy: int) -> dict[str, float]:
    return {"x0": cx - 32, "y0": cy - 42, "x1": cx + 32, "y1": cy + 42}


def _symbol(sid: str, cx: int, cy: int, label: str, class_name: str = "relay_coil") -> dict:
    return {"id": sid, "type": class_name, "class_name": class_name, "class_label": "リレーコイル",
            "bbox": _box(cx, cy), "label": label}


def test_classifier_keeps_each_evidence_source_and_conflicts_require_review():
    evidence = CircleEvidence(
        detected_class=CircleClass.RELAY_COIL,
        has_x_mark=True,
        interior_text="RL1",
        nearby_texts=("赤色",),
        relay_table_devices=("RL1",),
        same_sheet_contact_ids=("contact-1",),
    )

    result = classify_circle(evidence)

    assert result.status is ReviewStatus.REVIEW_REQUIRED
    assert result.final_class is CircleClass.RELAY_COIL
    assert result.evidence is evidence
    assert {cue.source for cue in result.cues} == {
        "x_mark",
        "circle_interior_text",
        "nearby_text",
        "relay_table",
        "same_sheet_contacts",
    }


def test_classifier_confirms_consistent_lamp_evidence():
    evidence = CircleEvidence(
        detected_class=CircleClass.RELAY_COIL,
        has_x_mark=False,
        interior_text="RL1",
        nearby_texts=("赤色",),
    )

    result = classify_circle(evidence)

    assert result.status is ReviewStatus.CONFIRMED
    assert result.suggested_class is CircleClass.LAMP
    assert result.final_class is CircleClass.LAMP


def test_interior_ocr_item_is_not_counted_as_second_nearby_cue():
    page = np.zeros((300, 300), np.uint8)
    cv2.circle(page, (150, 150), 22, 255, 2)
    symbols = [_symbol("circle", 150, 150, "RL1")]
    text_result = {"texts": [{"text": "RL1", "box": [140, 145, 160, 155]}]}

    review_circles(page, symbols, text_result)

    review = symbols[0]["symbol_review"]
    assert review["status"] == "要確認"
    assert review["cues"] == [
        {"cue": "circle_interior_text", "suggests": "lamp", "text": "RL1"}
    ]
    assert review["evidence"]["nearby_texts"] == []


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
    assert {c["cue"] for c in coil["symbol_review"]["cues"]} == {"relay_table", "same_sheet_contacts"}
    assert word_only["class_name"] == "relay_coil", "a lamp word alone does not change the class"
    assert word_only["symbol_review"]["class_suggestion"] == "lamp"
    assert word_only["symbol_review"]["status"] == "要確認"
    assert word_only["symbol_review"]["cues"] == [
        {"cue": "nearby_text", "suggests": "lamp", "text": "RL"}
    ]
    assert word_only["symbol_review"]["evidence"] == {
        "circle_interior": {"text": "", "x_mark": False},
        "nearby_texts": ["RL"],
        "relay_table_devices": [],
        "same_sheet_contact_ids": [],
    }
    assert counts == {"reviewed": 3, "確定": 2, "要確認": 1, "changed": 1}

