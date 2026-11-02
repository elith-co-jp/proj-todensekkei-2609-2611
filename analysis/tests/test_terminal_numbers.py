"""接点の端子番号を、記号ごとの拡大読みとページ全体の読みの文字枠から各端子に付ける処理を確かめる。"""

from __future__ import annotations

# ruff: noqa: I001

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

from terminal_numbers import attach_terminal_numbers, connector_numbers, contact_numbers
from analysis.relay_links import apply_row


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


def test_relay_table_confirms_a_full_page_ocr_number_cut_by_the_circle_rule():
    symbol = contact([*LABEL, item("8", 505, 272, 511, 283)])
    symbol.update(id="s1", label="33LA EV2101A", type="contact_a")
    page = [item("12", 499, 248, 527, 265), item("8", 505, 272, 511, 283)]
    attach_terminal_numbers([symbol], page)
    assert [t.get("name") for t in symbol["terminals"]] == ["1", "8"]
    row = {"device": "33LA EV2101A", "terminal": "8-12-4", "contact": "a", "position": "10D", "sheet": "本"}
    apply_row(row, 0, {"device_match": True, "unique": True}, symbol)
    assert [t.get("name") for t in symbol["terminals"]] == ["12", "8"]
    assert symbol["relay_row"]["terminal_check"] == "一致"


def terminal(names_numbers):
    """端子台の黒丸（中心 x=300）。器具名は左、番号は右に 1 行ずつ。"""
    items = []
    for row, (name, number) in enumerate(names_numbers):
        y = 200 + row * 14
        items += [item(name, 250, y, 290, y + 12), item(number, 310, y, 330, y + 12)]
    return {"class_name": "connector", "bbox": {"x0": 240, "y0": 195, "x1": 340, "y1": 230}, "text_items": items,
            "dot_role": {"dot": {"center": [300, 207]}}}


def test_ring_arcs_read_as_digits_beside_pin_numbers_are_dropped():
    # 黒丸を囲む弧が 0・1 に読まれ、番号の前後にくっつく（3 → 30、18 → 118、1 → 01）。
    assert connector_numbers(terminal([("TB13", "3"), ("CN211", "30")])) == [
        {"device": "TB13", "number": "3"}, {"device": "CN211", "number": "3"}]
    assert connector_numbers(terminal([("CN11", "18"), ("TB3", "118")])) == [
        {"device": "CN11", "number": "18"}, {"device": "TB3", "number": "18"}]
    assert connector_numbers(terminal([("CN11", "1"), ("TB3", "01")])) == [
        {"device": "CN11", "number": "1"}, {"device": "TB3", "number": "1"}]


def test_different_pin_numbers_of_the_two_devices_are_kept():
    assert connector_numbers(terminal([("TB13", "42"), ("CN212", "10")])) == [
        {"device": "TB13", "number": "42"}, {"device": "CN212", "number": "10"}]


def test_page_reading_supplies_the_second_row_the_symbol_reading_dropped():
    symbol = terminal([("TB6", "46"), ("CN27", "14")])
    page = [*symbol["text_items"], item("44", 200, 200, 216, 212), item("14", 300, 240, 318, 252)]
    symbol["text_items"] = symbol["text_items"][:2]
    assert connector_numbers(symbol) == [{"device": "TB6", "number": "46"}]
    assert connector_numbers(symbol, page) == [{"device": "TB6", "number": "46"}, {"device": "CN27", "number": "14"}]
