"""接点の端子番号を、記号ごとの拡大読みとページ全体の読みの文字枠から各端子に付ける処理を確かめる。"""

from __future__ import annotations

# ruff: noqa: I001

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

from terminal_numbers import contact_numbers


def item(value, x0, y0, x1, y1):
    return {"text": value, "score": 0.99, "box": (x0, y0, x1, y1)}


def contact(items, x=520):
    """縦の a 接点。番号は線の左、器具番号は線の右。"""
    return {
        "class_name": "contact_a", "bbox": {"x0": x - 21, "y0": 241, "x1": x + 67, "y1": 297}, "text_items": items,
        "terminals": [{"ref": "P1", "point": [x, 259]}, {"ref": "P2", "point": [x, 279]}],
    }


LABEL = [item("83D-1", 528, 252, 559, 263), item("ESD", 528, 264, 548, 276)]


def test_lower_number_read_together_with_the_left_neighbour_label():
    symbol = contact([item("10", 500, 246, 512, 259), *LABEL, item("(ER21271)6", 446, 272, 515, 291)])
    assert contact_numbers(symbol) == {"P1": "10", "P2": "6"}


def test_digits_on_the_label_side_are_not_terminal_numbers():
    symbol = contact([item("9", 500, 250, 506, 259), item("33", 528, 252, 545, 263), item("HV2111", 528, 264, 560, 276)])
    assert contact_numbers(symbol) == {"P1": "9"}


def test_end_circle_read_as_a_digit_is_dropped():
    symbol = contact([item("125", 506, 248, 526, 260), *LABEL, item("8", 505, 272, 511, 283)])
    assert contact_numbers(symbol) == {"P1": "12", "P2": "8"}


def test_page_reading_fills_an_end_the_symbol_reading_missed():
    symbol = contact([item("9", 503, 250, 509, 259), *LABEL])
    page = [item("9", 503, 250, 509, 259), item("5", 505, 274, 510, 281), item("5", 700, 274, 705, 281)]
    assert contact_numbers(symbol, page) == {"P1": "9", "P2": "5"}
