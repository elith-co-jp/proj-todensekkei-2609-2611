"""印だけを囲んだ記号に、近くの器具番号の文字を結び付ける処理（DD-0049）を確かめる。

文字の位置は、展開接続図 EV31036 を 150 dpi で読んだ OCR の結果（接点の右に「器具番号 / 型式 / (参照先)」の 3 行）に合わせた。
"""

from __future__ import annotations

from collections import Counter

import numpy as np
from PIL import Image, ImageDraw

from analysis import label_link as ll
from analysis import text_extraction as tx


def item(text, x0, y0, x1, y1, score=0.9):
    return {"text": text, "score": score, "box": (x0, y0, x1, y1)}


def contact(sid, x, y, cls="contact_a"):
    """白丸 2 個と棒だけを囲んだ接点の枠（上の白丸の中心が x, y）。"""
    return {"id": sid, "class_name": cls, "bbox": {"x0": x - 5, "y0": y - 5, "x1": x + 7, "y1": y + 23}}


# 2 つの接点が横に並び、それぞれの右に 3 行の文字。左の接点の文字の行末には、右の接点の端子番号 8 がくっついている。
TWO_CONTACTS = [
    item("12", 516, 236, 534, 247),
    item("183DX-112", 544, 232, 602, 259),
    item("IP33P22", 546, 252, 590, 266),
    item("(EV31032)8", 544, 262, 596, 279),
    item("83N-1", 612, 240, 648, 254),
    item("IP33P22", 610, 250, 654, 266),
    item("(EV31033)", 610, 266, 654, 276),
]


def test_each_contact_gets_the_block_on_its_right():
    symbols = [contact("a", 538, 244), contact("b", 602, 244)]
    links = ll.link_labels(TWO_CONTACTS, symbols)
    assert links["a"]["label"].split()[1:] == ["IP33P22", "(EV31032)"]
    assert links["b"]["label"] == "83N-1 IP33P22 (EV31033)"
    assert links["a"]["status"] == links["b"]["status"] == "linked"
    assert links["a"]["direction"] == links["b"]["direction"] == "right"
    # 行末の端子番号 8 は落とし、文字の枠も右の接点の枠に掛からない。
    assert links["a"]["label_box"][2] < symbols[1]["bbox"]["x0"]


def test_page_lexicon_trims_the_contact_bar_and_terminal_number():
    lexicon = Counter({"P33P22": 9, "83DX-1": 3})
    assert ll.trim_glued("IP33P228", lexicon) == "P33P22"
    assert ll.trim_glued("183DX-112", lexicon) == "83DX-1"
    assert ll.trim_glued("43LK", lexicon) == "43LK"
    symbols = [contact("a", 538, 244), contact("b", 602, 244)]
    links = ll.link_labels(TWO_CONTACTS, symbols, lexicon=lexicon)
    assert links["a"]["label"] == "83DX-1 P33P22 (EV31032)"


def test_text_between_two_symbols_needs_review():
    # 2 つのコネクタのちょうど間に 1 つだけ名前がある。どちらの名前か決められない。
    symbols = [
        {"id": "l", "class_name": "connector", "bbox": (100, 100, 110, 110)},
        {"id": "r", "class_name": "connector", "bbox": (150, 100, 160, 110)},
    ]
    links = ll.link_labels([item("TB3", 120, 100, 140, 110)], symbols)
    linked = [k for k, v in links.items() if v["label"]]
    assert len(linked) == 1
    assert links[linked[0]]["status"] == "要確認"
    other = "r" if linked[0] == "l" else "l"
    assert links[other]["status"] == "未検出" and links[other]["candidates"]


def test_far_text_and_names_are_not_linked():
    symbols = [contact("a", 200, 200)]
    items = [
        item("83DX-1", 260, 196, 300, 210),  # 隙間 53 px（遠い）
        item("起動指令", 210, 150, 222, 190),  # 日本語の名称は器具番号にしない
        item("9", 186, 196, 192, 206),  # 端子番号だけの行
    ]
    links = ll.link_labels(items, symbols)
    assert links["a"]["status"] == "未検出" and links["a"]["label"] == ""


def test_stacked_lines_keep_a_short_device_number_on_top():
    # 数字だけの器具番号（80）や、5D を 50 と読んだ行も、型式の行の上にあればまとまりに入れる。
    symbols = [contact("a", 794, 322)]
    items = [item("80", 803, 316, 816, 332), item("P33P22", 800, 330, 842, 343), item("(EV31032)", 800, 342, 846, 354)]
    assert ll.link_labels(items, symbols)["a"]["label"] == "80 P33P22 (EV31032)"


def test_connector_name_above_and_wire_number_not_a_block():
    symbols = [{"id": "c", "class_name": "connector", "bbox": (300, 320, 316, 336)}]
    items = [item("387", 290, 290, 310, 302), item("CN22", 296, 305, 322, 317)]
    link = ll.link_labels(items, symbols)["c"]
    assert link["label"] == "387 CN22"
    assert link["direction"] == "above"


def test_link_targets_follow_the_box_convention_of_the_page():
    coil = {"id": 1, "class_name": "relay_coil", "bbox": (0, 0, 40, 40)}
    named = {"id": 2, "class_name": "contact_a", "bbox": (0, 0, 40, 40)}
    empty = {"id": 3, "class_name": "contact_b", "bbox": (0, 0, 40, 40)}
    connector = {"id": 4, "class_name": "connector", "bbox": (0, 0, 40, 40)}
    symbols = [coil, named, empty, connector]
    # 印と名前を囲んだ図面: 名前の読めた接点・コイルが多い。空の接点だけ結び、コネクタの番号だけの枠は結ばない。
    old = {id(coil): "6X P33P22", id(named): "83DX-1 P33P22", id(empty): "", id(connector): "13"}
    assert ll.link_targets(symbols, old) == [empty]
    # 印だけを囲んだ図面: 枠の中に英字の語が無い記号すべて（コイルの丸の中の名前は枠の中で読める）。
    new = {id(coil): "6X P33P22", id(named): "9 5", id(empty): "", id(connector): "13"}
    assert ll.link_targets(symbols, new) == [named, empty, connector]


def _drawing():
    """白丸 2 個と縦棒の接点（端子は上下の白丸）、その右に 2 行の文字、左に端子番号を描いた図面。"""
    image = Image.new("L", (200, 120), 255)
    draw = ImageDraw.Draw(image)
    draw.line([(60, 0), (60, 34)], fill=0, width=1)
    draw.ellipse([57, 34, 63, 40], outline=0)
    draw.ellipse([57, 60, 63, 66], outline=0)
    draw.line([(60, 66), (60, 120)], fill=0, width=1)
    draw.line([(66, 32), (66, 68)], fill=0, width=1)  # 接点の棒（文字より背が高い）
    texts = []
    for text, x, y in (("83DX-1", 72, 36), ("P33P22", 72, 52), ("9", 46, 34), ("5", 46, 60)):
        for k in range(len(text)):
            draw.rectangle([x + 6 * k, y, x + 6 * k + 4, y + 9], fill=0)  # 1 文字 = 高さ 10 px の塊
        texts.append(item(text, x, y, x + 6 * len(text) - 1, y + 10))
    return np.asarray(image), texts


def test_split_mark_box_keeps_the_mark_and_moves_the_name_to_the_label_box():
    gray, texts = _drawing()
    old_box = (42, 28, 112, 72)  # 印と名前と端子番号を囲んだ今の枠
    mark, label_box = ll.split_mark_box(gray, old_box, texts, "contact_a", terminals=[(60, 37), (60, 63)])
    assert label_box == (72, 36, 107, 62)
    # 印（白丸と棒）だけになり、端子番号 9・5 と器具番号は枠の外。上下は今の枠のまま（線が通る）。
    assert mark[0] >= 53 and mark[2] <= 70
    assert mark[1] == 28 and mark[3] == 72


def test_split_mark_box_leaves_a_box_without_a_name():
    gray, _texts = _drawing()
    box = (54, 30, 70, 70)
    assert ll.split_mark_box(gray, box, [], "contact_a") == (box, None)


def test_inside_name_still_wins_over_nearby_text():
    # 名前まで囲んだ枠は今までどおり枠の中の文字を使う（近くの文字は使わない）。
    assert ll.inside_label_is_name("33HB FV2306", "relay_coil")
    assert not ll.inside_label_is_name("13 14", "connector")
    assert not ll.inside_label_is_name("", "contact_a")


def test_symbol_label_and_link_agree_on_a_split_box():
    # 今の枠で読んだラベルと、分けた枠＋近くの文字で読んだラベルが同じになる。
    gray, texts = _drawing()
    old_box = (42, 28, 112, 72)
    inside = tx.symbol_label(texts, old_box, "contact_a")
    mark, _ = ll.split_mark_box(gray, old_box, texts, "contact_a", terminals=[(60, 37), (60, 63)])
    links = ll.link_labels(texts, [{"id": "a", "class_name": "contact_a", "bbox": mark}])
    assert inside == links["a"]["label"] == "83DX-1 P33P22"
