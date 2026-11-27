"""表の欄の文字を元の表と同じ行 × 列に並べる（DD-0051）。"""

from __future__ import annotations

from analysis.table_grid import build_grid, cell_box


def item(text, x0, y0, x1, y1, score=0.99):
    return {"text": text, "score": score, "box": (x0, y0, x1, y1)}


def test_items_are_placed_in_rows_and_columns_like_the_table():
    items = [
        item("器具番号", 60, 960, 110, 978),
        item("5-9-1", 117, 980, 160, 997), item("A", 167, 980, 180, 997, 0.6), item("本", 190, 980, 240, 997),
        item("33HB", 70, 998, 105, 1015), item("8-12-4", 117, 998, 160, 1015), item("ER21237", 190, 998, 240, 1015),
        item("14D", 242, 998, 262, 1015),
        item("遠くの文字", 900, 100, 960, 118),
    ]
    grid = build_grid(items, {"x0": 50, "y0": 950, "x1": 300, "y1": 1100})
    assert len(grid["rows"]) == 3
    # 器具番号の見出しと 33HB が同じ列、隙間 2 px の「シート」と「行列」は別の列
    assert len(grid["cols"]) == 5
    by_text = {c["text"]: (c["row"], c["col"]) for c in grid["cells"]}
    assert by_text == {
        "器具番号": (0, 0), "5-9-1": (1, 1), "A": (1, 2), "本": (1, 3),
        "33HB": (2, 0), "8-12-4": (2, 1), "ER21237": (2, 3), "14D": (2, 4),
    }
    assert next(c for c in grid["cells"] if c["text"] == "A")["score"] == 0.6
    # 文字の無い欄（2 行目の接点）も行と列の範囲から位置が決まる
    assert cell_box(grid, 2, 2) == (167.0, 998.0, 180.0, 1015.0)


def test_split_words_in_one_cell_are_joined_and_wide_headers_span_columns():
    items = [
        item("リレー表 （器具・端子・接点）", 60, 940, 260, 956),
        item("33HB", 60, 960, 90, 976), item("5-9", 100, 960, 125, 976), item("-1", 127, 960, 140, 976, 0.5),
        item("A", 160, 960, 172, 976),
        item("33LA", 60, 980, 90, 996), item("5-9-1", 100, 980, 140, 996), item("B", 160, 980, 172, 996),
    ]
    grid = build_grid(items, (50, 930, 300, 1000))
    header = next(c for c in grid["cells"] if c["row"] == 0)
    assert (header["col"], header["colspan"]) == (0, 3)
    joined = next(c for c in grid["cells"] if c["row"] == 1 and c["col"] == 1)
    assert (joined["text"], joined["score"], joined["parts"]) == ("5-9 -1", 0.5, 2)


def test_empty_area_gives_an_empty_grid():
    assert build_grid([item("X", 0, 0, 5, 5)], (100, 100, 200, 200)) == {
        "bbox": [100.0, 100.0, 200.0, 200.0], "rows": [], "cols": [], "cells": []}


def test_rules_add_an_unread_column_split_merged_headers_and_rows_between_lines_hold_merged_cells():
    from PIL import Image, ImageDraw

    from analysis.table_grid import ruling_lines

    # 器具番号 | 端子 | 接点（A・B を OCR が読み落とした） | シート。見出しの「接点 シート」は 1 語に読まれた
    image = Image.new("L", (400, 200), 255)
    draw = ImageDraw.Draw(image)
    xs, top, pitch = [60, 110, 160, 180, 240], 20, 18
    draw.rectangle((60, top, 240, top + pitch * 4), outline=0)
    for x in xs[1:-1]:
        draw.line((x, top, x, top + pitch * 4), fill=0)
    for i in range(1, 4):
        draw.line((110, top + pitch * i, 240, top + pitch * i), fill=0)
    items = [
        item("端子", 113, 23, 140, 36), item("接点シート", 163, 23, 225, 36),
        item("5-9-1", 113, 41, 150, 54), item("本", 183, 41, 195, 54),
        item("33HB", 63, 50, 100, 63),   # 2 行をまとめた器具番号の欄（行の間に書かれる）
        item("8-12-4", 113, 59, 156, 72), item("EV31033", 183, 59, 230, 72),
        item("7-11-3", 113, 77, 156, 90),
    ]
    rules = ruling_lines(image, (55, 15, 245, 100), 13)
    assert {round(x) for x, _, _ in rules["x"]} >= {60, 110, 160, 180, 240}
    grid = build_grid(items, (55, 15, 245, 100), rules)
    assert [(c["x0"], c["x1"]) for c in grid["cols"]] == [(63.0, 100.0), (113.0, 156.0), (163.0, 180.0), (180.0, 230.0)]
    assert len(grid["rows"]) == 4
    cells = {c["text"]: (c["row"], c["col"], c["colspan"]) for c in grid["cells"]}
    assert cells["接点シート"] == (0, 2, 2)
    assert cells["33HB"][:2] in {(1, 0), (2, 0)}
    assert cells["EV31033"] == (2, 3, 1)
