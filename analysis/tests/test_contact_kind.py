"""同じ接点に重なった a 接点と b 接点の枠を、棒の側で 1 つにする（DD-0023）。"""

from __future__ import annotations

import numpy as np

from analysis.wiring.contact_kind import bar_side, merge_contact_duplicates

BOX = {"x0": 10, "y0": 15, "x1": 50, "y1": 45}


def _contact(bar_x: int) -> np.ndarray:
    binary = np.zeros((60, 60), dtype=np.uint8)
    binary[0:16, 20] = 255          # 上から入る線
    binary[44:60, 20] = 255         # 下へ出る線
    binary[20:40, bar_x] = 255      # 接点の棒
    return binary


def test_bar_right_of_the_circles_is_an_a_contact_and_left_is_b():
    assert bar_side(_contact(26) > 0, BOX) == "a"
    assert bar_side(_contact(14) > 0, BOX) == "b"
    assert bar_side(np.zeros((60, 60), dtype=bool), BOX) is None


def _pair(conf_a: float, conf_b: float) -> list[dict]:
    return [{"id": "b", "class_name": "contact_b", "bbox": dict(BOX), "confidence": conf_b},
            {"id": "a", "class_name": "contact_a", "bbox": {**BOX, "x1": 49}, "confidence": conf_a},
            {"id": "c", "class_name": "relay_coil", "bbox": dict(BOX), "confidence": 0.9}]


def test_overlapping_a_and_b_keep_the_one_the_bar_shows():
    """ER21299 の 43A3: b 接点の枠の方が確信度が高い（0.65 と 0.53）が、棒は右で a 接点。"""
    kept = merge_contact_duplicates(_pair(conf_a=0.53, conf_b=0.65), _contact(26))
    assert [s["id"] for s in kept] == ["a", "c"]
    kept = merge_contact_duplicates(_pair(conf_a=0.63, conf_b=0.68), _contact(14))
    assert [s["id"] for s in kept] == ["b", "c"]
    kept = merge_contact_duplicates(_pair(conf_a=0.53, conf_b=0.65), np.zeros((60, 60), dtype=np.uint8))
    assert [s["id"] for s in kept] == ["b", "c"]

def test_single_contact_takes_the_kind_its_bar_shows():
    """ER21294 の 10ESD X: 未学習の図面のモデルが b 接点と検出したが、棒は右で a 接点（DD-0023）。"""
    from analysis.wiring.contact_kind import correct_contact_kinds

    symbols = [{"id": "k", "class_name": "contact_b", "type": "contact_b", "class_label": "b接点", "bbox": dict(BOX)}]
    assert correct_contact_kinds(symbols, _contact(26)) == 1
    assert (symbols[0]["class_name"], symbols[0]["detected_class"]) == ("contact_a", "contact_b")
    unclear = [{"id": "k", "class_name": "contact_b", "bbox": dict(BOX)}]
    assert correct_contact_kinds(unclear, np.zeros((60, 60), dtype=np.uint8)) == 0
    assert unclear[0]["class_name"] == "contact_b"
