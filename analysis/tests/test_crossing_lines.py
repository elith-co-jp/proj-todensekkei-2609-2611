"""線の検出: 文字まで含んで広がった線の枠をインクの帯に付け替えて交差の先まで線を続け、横に並ぶ接点の印の縦棒を
列の線にまとめない（DD-0021）。"""

from __future__ import annotations

import numpy as np

from analysis.wiring.detect import merge_candidate_lines, split_line_by_ink_support
from analysis.wiring.shapes import Box, LineSegment


def test_wide_box_follows_the_lines_ink():
    """縦線のインクは x 355〜357 にあり、枠は横の文字（CN20）まで含んで x 335〜360 に広がる（ER21275 の CN20 8）。
    枠の中心（347）で確かめると線が無いとみなされ、交差の先へ続く線を落としていた。"""
    binary = np.zeros((900, 500), dtype=np.uint8)
    binary[500:800, 355:358] = 255
    binary[520:540, 336:345] = 255          # 横の文字の画
    split = split_line_by_ink_support(binary, LineSegment("v", Box(335, 500, 360, 800)), min_length=45, max_empty_run=10,
                                      half_width=5, min_visible_coverage=0.38)
    assert [(s.box.x0, s.box.x1, s.box.y0, s.box.y1) for s in split] == [(354, 358, 500, 800)]


def test_narrow_box_is_left_as_it_is():
    binary = np.zeros((900, 500), dtype=np.uint8)
    binary[500:800, 438:441] = 255
    split = split_line_by_ink_support(binary, LineSegment("v", Box(433, 500, 444, 800)), min_length=45, max_empty_run=10,
                                      half_width=5, min_visible_coverage=0.38)
    assert [(s.box.x0, s.box.x1) for s in split] == [(433, 444)]


def test_contact_bar_beside_the_column_line_is_not_merged_into_it():
    """カードの中の接点: 列の線は接点の C の白丸（y 485）で終わり、横の印の縦棒（x 449〜453）は y 467〜519 にはみ出す
    （ER21277）。まとめると列の線が接点の空白を越えて y 519 まで延び、接点の上の端子が下の黒丸につながった。"""
    column = LineSegment("v", Box(433, 270, 444, 485))
    bar = LineSegment("v", Box(449, 467, 453, 519))
    merged = merge_candidate_lines([column, bar], "v", axis_tolerance=16, max_gap=82)
    assert sorted((m.box.y0, m.box.y1) for m in merged) == [(270, 485), (467, 519)]
