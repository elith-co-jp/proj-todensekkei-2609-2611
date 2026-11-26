"""表の欄を読み直して書式で直す処理（DD-0050 §6）。OCR を使わない部分を確かめる。"""

from __future__ import annotations

import numpy as np
from PIL import Image, ImageDraw

from analysis import table_reading as tr


def test_fix_repairs_common_misreads_by_column_format():
    assert tr.fix("1OD", "position") == "10D"
    assert tr.fix("140", "position") == "14D"
    assert tr.fix("5~9-l", "terminal") == "5-9-1"
    assert tr.fix("8", "contact") == "B"
    assert tr.fix("EV31O36", "sheet") == "EV31036"
    assert tr.fix("木", "sheet") == "本"


def test_headers_snap_to_the_fixed_words_even_with_lookalike_kanji():
    assert tr.snap("器具蛋号", tr.RELAY_HEADERS) == "器具番号"
    assert tr.snap("シ一卜", tr.RELAY_HEADERS) == "シート"
    assert tr.snap("卜番号", tr.TITLE_KEYS) == "シート番号"
    assert tr.snap("EV31036", tr.RELAY_HEADERS) is None


def test_column_kinds_follow_the_block_order_around_terminal_columns():
    grid = {"cols": [{}] * 6}
    values = {
        (1, 0): ["33HB"], (1, 1): ["5-9-1"], (1, 2): [""], (1, 3): ["本"], (1, 4): ["1OD"],
        (2, 1): ["8-12-4"], (2, 3): ["EV31036"], (2, 4): ["2B"], (3, 1): ["7-11-3"], (1, 5): ["x"],
    }
    kinds = tr.column_kinds(grid, values, "relay_table")
    # 接点の列は読みが 1 つも無くても、端子の右の列なので接点
    assert [kinds[c] for c in range(5)] == ["device", "terminal", "contact", "sheet", "position"]


def test_crop_drops_ruling_lines_and_glyphs_cut_at_the_cell_edge():
    image = Image.new("L", (120, 40), 255)
    draw = ImageDraw.Draw(image)
    draw.line((0, 5, 119, 5), fill=0)          # 横の罫線
    draw.line((60, 0, 60, 39), fill=0)         # 縦の罫線
    draw.rectangle((20, 14, 28, 26), fill=0)   # 欄の中の字
    draw.rectangle((55, 14, 66, 26), fill=0)   # 罫線をまたいだ隣の欄の字
    gray = np.asarray(image)
    crop = tr.crop_cell(gray, (2, 2, 60, 36))
    assert crop is not None
    ink = np.asarray(Image.fromarray(crop).convert("L")) < 128
    rows, cols = np.nonzero(ink)
    # 残ったのは欄の中の字だけ（拡大して余白 12 px を付けている）
    assert cols.min() >= 12 and rows.min() >= 12
    assert tr.crop_cell(gray, (70, 8, 118, 38)) is None  # 字の無い欄


def _cell(row, col, text, kind):
    return {"row": row, "col": col, "colspan": 1, "text": text, "score": 0.9, "box": [0, 0, 1, 1], "kind": kind}


def test_rows_are_read_from_column_kinds_and_repeated_values_fix_one_offs():
    cells = [
        _cell(0, 0, "器具番号", "header"), _cell(0, 1, "端子", "header"),
        _cell(1, 0, "33HB", "device"), _cell(1, 1, "5-9-1", "terminal"), _cell(1, 2, "A", "contact"),
        _cell(1, 3, "本", "sheet"), _cell(1, 4, "10D", "position"),
        _cell(2, 0, "FV2306", "device"), _cell(2, 1, "8-12-4", "terminal"),
        _cell(3, 1, "7-11-3", "terminal"),
        _cell(4, 0, "器具番号", "header"),
        _cell(5, 0, "33LA", "device"), _cell(5, 1, "5-9-1", "terminal"), _cell(6, 0, "FV2306", "device"),
        _cell(6, 1, "8-12-4", "terminal"), _cell(7, 1, "7-11-8", "terminal"), _cell(8, 1, "7-11-3", "terminal"),
    ]
    tr._vote_repeats(cells)
    assert next(c for c in cells if (c["row"], c["col"]) == (7, 1))["text"] == "7-11-3"
    grid = {"reread": True, "rows": [{}] * 9, "cols": [{}] * 5, "cells": cells, "bbox": [0, 0, 1, 1]}
    rows = tr.relay_rows_from_grids([], {"relay_table": grid}, 1755, 1241)
    assert rows[0] == {"device": "33HB FV2306", "terminal": "5-9-1", "contact": "a", "sheet": "本", "position": "10D"}
    assert [r["device"] for r in rows] == ["33HB FV2306"] * 3 + ["33LA FV2306"] * 4


class _FakeReader:
    """文字の認識だけの呼び出しに、決まった読みを返す。"""

    def __init__(self, text):
        self.text, self.calls = text, 0

    def __call__(self, image, use_det=True, use_cls=True, use_rec=True):
        self.calls += 1
        return [[self.text, 0.99]], [0.0]


def test_reread_keeps_the_page_reading_when_the_crop_reading_does_not_fit_the_format():
    image = Image.new("RGB", (200, 60), "white")
    draw = ImageDraw.Draw(image)
    draw.rectangle((20, 20, 40, 32), fill="black")
    grid = {"bbox": [10, 10, 190, 50], "rows": [{"y0": 18, "y1": 34}], "cols": [{"x0": 18, "x1": 60}],
            "cells": [{"row": 0, "col": 0, "colspan": 1, "text": "本", "score": 0.6, "box": [20, 20, 40, 32], "parts": 1}]}
    reader = _FakeReader("ER2123")
    out = tr.reread_grids(image, {"title_block": grid}, reader)["title_block"]
    assert reader.calls == 1
    assert out["reread"] is True and out["cells"][0]["page_text"] == "本"
