"""点線・ラベルの罫線・カードの枠を配線から外す処理の細かい場合（filter_wire_decorations）。"""

from __future__ import annotations

import numpy as np
from PIL import Image

from analysis.wiring import filter_wire_decorations as fwd
from analysis.wiring.detect import Box, LineSegment


def test_card_rule_can_be_disabled(monkeypatch):
    card = {"class_name": "other", "other_kind": "card", "bbox": {"x0": 0, "y0": 0, "x1": 10, "y1": 10}}
    assert len(fwd._card_boxes([card])) == 1
    monkeypatch.setattr(fwd, "CARD_FRAME_MIN_CONTACTS", 0)
    assert fwd._card_boxes([card]) == []


def test_card_frame_needs_the_line_inside_the_card():
    binary = np.zeros((100, 200), np.uint8)
    binary[50, 20:180] = 255
    line = LineSegment("h", Box(20, 50, 180, 51))
    assert fwd._is_card_frame(binary, line, [Box(100, 0, 150, 100)], 1.0) is False   # 線が枠からはみ出す
    assert fwd._is_card_frame(binary, line, [Box(10, 60, 190, 90)], 1.0) is False    # 行が枠の外
    assert fwd._is_card_frame(binary, line, [Box(10, 40, 190, 90)], 1.0) is True


def test_label_rule_pairs_that_are_not_label_boxes():
    binary = np.zeros((300, 300), np.uint8)
    binary[10, 10:200] = binary[15, 10:200] = 255      # 近すぎる 2 本
    binary[100, 10:200] = binary[120, 60:200] = 255    # 始まりがそろわない
    binary[200, 10:50] = binary[230, 10:50] = 255      # 間に比べて短い
    image = Image.fromarray(255 - binary).convert("RGB")
    rules, candidates = fwd._label_rules(image, binary, [], 1.0, lambda crop: True)
    assert (rules, candidates) == ([], 0)


def test_rapidocr_confirmation_reads_alphanumerics():
    seen = []

    def reader(pixels, use_cls):
        seen.append(pixels.shape)
        return [([], "AB-12", 0.9), ([], "-", 0.99)], None

    confirm = fwd.rapidocr_text_confirmation(reader)
    assert confirm(Image.new("L", (40, 10), 255)) is True
    assert seen == [((10 + 16) * 4, (40 + 16) * 4, 3)]
    assert fwd.rapidocr_text_confirmation(lambda pixels, use_cls: (None, None))(Image.new("L", (4, 4))) is False


def test_rule_cuts_skip_short_and_solid_parallel_spans_and_merge_overlaps():
    binary = np.zeros((100, 320), np.uint8)
    binary[45, 200:280] = 255                          # 罫線の横に続く実線（配線）
    line = LineSegment("h", Box(0, 40, 300, 60))
    rules = [fwd.Rule(51, 0, 10), fwd.Rule(51, 20, 100), fwd.Rule(51, 80, 150), fwd.Rule(51, 200, 280)]
    assert fwd._rule_cuts(binary, line, rules, 1.0) == [(20, 150)]
    assert fwd._rule_cuts(binary, LineSegment("v", Box(0, 0, 1, 50)), rules, 1.0) == []
