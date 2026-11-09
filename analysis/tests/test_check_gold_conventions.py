"""正解の取り決めのチェック（check_gold_conventions）が、端子の数・大きすぎる枠・枠の外の目盛り線を拾い、取り決めどおりの記号は拾わないことを確かめる。"""

from __future__ import annotations

# ruff: noqa: I001

import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

from check_gold_conventions import check_symbol

MEDIANS = {"connector": (40.0, 60.0), "contact_a": (45.0, 60.0)}


def rules(symbol, box, ink=None, dots=()):
    ink = np.zeros((200, 300), dtype=bool) if ink is None else ink
    return [f["rule"] for f in check_symbol(symbol, box, ink, list(dots), MEDIANS, 1.4)]


def connector_with_tick(tick_length):
    ink = np.zeros((200, 300), dtype=bool)
    ink[40:61, 99:102] = True  # 黒丸を通る縦の配線
    ink[65, 100 - tick_length // 2:100 + tick_length // 2 + 1] = True  # 黒丸の 15 px 下の横線
    return ink


def test_a_contact_needs_exactly_two_terminals():
    assert rules({"class_key": "contact_a", "terminals": [{}]}, (0, 0, 60, 45)) == ["two_terminals"]
    assert rules({"class_key": "contact_a", "terminals": [{}, {}]}, (0, 0, 60, 45)) == []


def test_a_box_much_taller_than_others_of_its_kind_is_flagged():
    assert rules({"class_key": "connector"}, (0, 0, 60, 70)) == ["oversized"]
    assert rules({"class_key": "connector"}, (0, 0, 60, 50)) == []


def test_a_tick_line_below_the_dot_must_be_inside_the_connector_box():
    ink = connector_with_tick(50)
    dots = [(100.0, 50.0, 8.0)]
    assert rules({"class_key": "connector"}, (70, 30, 130, 60), ink, dots) == ["tick_outside"]
    assert rules({"class_key": "connector"}, (70, 30, 130, 67), ink, dots) == []


def test_a_long_line_under_the_dot_is_a_bus_and_not_a_tick():
    ink = connector_with_tick(200)
    assert rules({"class_key": "connector"}, (70, 30, 130, 60), ink, [(100.0, 50.0, 8.0)]) == []
