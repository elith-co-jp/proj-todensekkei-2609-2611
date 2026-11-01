"""文字の読み取り結果（OCR の文字枠）から記号ラベル・リレー表・シート間参照・領域の種類を作る処理を確認する。"""

from __future__ import annotations

from PIL import Image, ImageDraw

from analysis import text_extraction as tx


def item(text, x0, y0, x1, y1, score=0.99):
    return {"text": text, "score": score, "box": (x0, y0, x1, y1)}


def box(x0, y0, x1, y1):
    return {"x0": x0, "y0": y0, "x1": x1, "y1": y1}


def test_coil_label_reads_stacked_lines_and_drops_terminal_numbers():
    items = [
        item("14", 281, 750, 303, 768),
        item("33HB", 285, 774, 321, 790),
        item("FV2306", 280, 787, 327, 803),
        item("13", 283, 811, 301, 826),
    ]
    assert tx.symbol_label(items, box(272, 745, 333, 830), "relay_coil") == "33HB FV2306"


def test_connector_label_reads_left_column_then_right_column():
    items = [
        item("610", 264, 295, 289, 313),
        item("TB16", 261, 309, 293, 325),
        item("101", 313, 309, 334, 323),
        item("CN229", 257, 323, 295, 339),
        item("5", 316, 323, 331, 338),
    ]
    assert tx.symbol_label(items, box(251, 290, 337, 348), "connector") == "610 TB16 CN229 101 5"


def test_label_keeps_only_the_part_of_an_ocr_line_inside_the_box():
    items = [item("TB6 103", 300, 400, 370, 420)]
    assert tx.symbol_label(items, box(295, 395, 332, 425), "connector") == "TB6"


def test_other_label_with_parenthesized_sheet_becomes_reference():
    assert tx.symbol_label([item("(ER21212)", 164, 230, 219, 248)], box(123, 210, 221, 251), "other") == "ER21212へ"


def test_device_token_skips_wire_numbers():
    assert tx.device_token("610 TB16 CN229 101 5") == "TB16"
    assert tx.device_token("33la") == "33LA"


def _relay_block(x, y, device, rows, header=True):
    out = [item("器具番号", x, y, x + 50, y + 18)] if header else []
    for i, (terminal, contact, sheet, position) in enumerate(rows):
        top = y + 20 + 18 * i
        out.append(item(terminal, x + 57, top, x + 100, top + 17))
        for text, x0, x1 in ((contact, x + 107, x + 120), (sheet, x + 130, x + 180), (position, x + 182, x + 202)):
            if text:
                out.append(item(text, x0, top, x1, top + 17))
    for i, line in enumerate(device.split()):
        out.append(item(line, x + 10, y + 38 + 18 * i, x + 45, y + 55 + 18 * i))
    return out


def test_relay_table_rows_are_read_per_block_and_repaired():
    items = _relay_block(60, 960, "33HB FV2306", [
        ("5-9-1", "a", "本", "10D"), ("8-12-4", "a", "ER21237", "140"), ("7-11-3", "", "", ""), ("6-10-2", "", "", ""),
    ]) + _relay_block(60, 1054, "33LA FV2306", [
        ("591", "a", "本", "11D"), ("8-12-4", "", "", ""), ("7-11-3", "", "", ""), ("6-10-2", "", "", ""),
    ])
    rows = tx.parse_relay_table(items, 1755, 1241)
    assert rows[:2] == [
        {"device": "33HB FV2306", "terminal": "5-9-1", "contact": "a", "sheet": "本", "position": "10D"},
        {"device": "33HB FV2306", "terminal": "8-12-4", "contact": "a", "sheet": "ER21237", "position": "14D"},
    ]
    assert rows[4] == {"device": "33LA FV2306", "terminal": "5-9-1", "contact": "a", "sheet": "本", "position": "11D"}
    assert len(rows) == 8


def test_sheet_references_separate_wire_destinations_from_coil_sheets():
    items = [
        item("(ER21212)", 164, 230, 219, 248),
        item("(ER21271)", 290, 280, 350, 296),
        item("ER21011-3へ", 1400, 230, 1480, 248),
        item("(ER21318D07)", 1330, 1000, 1400, 1018),
    ]
    symbols = [{"class_name": "contact_a", "bbox": box(251, 245, 360, 300)}]
    refs = tx.sheet_references(items, symbols)
    assert [(r["target_sheet"], r["kind"]) for r in refs] == [
        ("ER21212", "wire"), ("ER21271", "relay"), ("ER21011-3", "wire"),
    ]


def test_regions_are_typed_by_size_position_and_text():
    items = [item("器具番号", 66, 969, 117, 986), item("シート番号", 1597, 1088, 1654, 1105), item("LOC", 461, 444, 486, 462)]
    assert tx.classify_region(items, box(60, 60, 1660, 910), 1755, 1241) == "drawing_area"
    assert tx.classify_region(items, box(60, 960, 270, 1160), 1755, 1241) == "relay_table"
    assert tx.classify_region(items, box(1310, 970, 1660, 1150), 1755, 1241) == "title_block"
    assert tx.classify_region(items, box(250, 440, 530, 570), 1755, 1241) == "loc"
    assert tx.classify_region(items, box(390, 350, 1290, 430), 1755, 1241) == "panel"


def test_erase_dots_removes_filled_dots_but_keeps_text_strokes():
    image = Image.new("RGB", (120, 60), "white")
    draw = ImageDraw.Draw(image)
    draw.ellipse((50, 20, 61, 31), fill="black")
    draw.line((10, 10, 10, 50), fill="black", width=2)
    draw.rectangle((90, 20, 92, 40), fill="black")
    out = tx.erase_dots(image).convert("L")
    assert out.getpixel((55, 25)) == 255
    assert out.getpixel((10, 30)) == 0
    assert out.getpixel((91, 30)) == 0


def test_sheet_number_prefers_full_margin_number_over_torn_title_block():
    items = [
        item("シート番号", 1603, 1087, 1650, 1102),
        item("ER2107", 1603, 1098, 1651, 1116),
        item("ER21070", 30, 1162, 120, 1186),
        item("ER21070003", 1326, 1022, 1394, 1040),
    ]
    assert tx.sheet_number_candidates(items, 1755, 1241)[0]["text"] == "ER21070"


def test_texts_are_classified_and_japanese_names_attach_to_symbols():
    items = [
        item("(ER21212)", 164, 230, 219, 248),
        item("33HB", 285, 774, 321, 790),
        item("101", 313, 309, 334, 323),
        item("盤異常", 300, 500, 340, 515),
        item("位置記号無き器具は", 1320, 70, 1440, 86),
        item("5-9-1", 125, 988, 165, 1007),
    ]
    symbols = [{"id": "symbol_001", "bbox": box(272, 745, 333, 830)}, {"id": "symbol_002", "bbox": box(280, 470, 330, 495)}]
    texts = tx.classify_texts(items, symbols, [])
    assert [(t["kind"], t["symbol_id"]) for t in texts] == [
        ("destination", None), ("device_number", "symbol_001"), ("number", None),
        ("name", "symbol_002"), ("note", None), ("relay_terminals", None),
    ]


def test_contact_labels_repair_d_read_as_zero():
    items = [item("830-1", 300, 400, 340, 412), item("ESD", 300, 414, 330, 426),
             item("0010", 400, 400, 432, 412), item("B834", 400, 414, 432, 426)]
    assert tx.symbol_label(items, box(295, 395, 345, 430), "contact_a") == "83D-1 ESD"
    assert tx.symbol_label(items, box(395, 395, 437, 430), "contact_a") == "D010 B834"
    # コネクタの番号（610 など）は直さない
    assert tx.symbol_label([item("0610", 300, 400, 340, 412)], box(295, 395, 345, 430), "connector") == "0610"


def test_contact_labels_that_are_not_device_numbers_are_reread():
    asked = []

    def reader(image, symbol_box):
        asked.append(symbol_box)
        return "83D-1 ESD (ER21271)"

    image, b = Image.new("RGB", (40, 40), "white"), box(5, 5, 30, 30)
    # D を 0 と読んだ番号・端子番号がくっついた番号だけを読み直す
    assert tx.reread_label(image, b, "contact_a", "830-1 ESD (ER21271)", reader) == "83D-1 ESD (ER21271)"
    assert tx.reread_label(image, b, "relay_coil", "0010 B834", reader) == "83D-1 ESD (ER21271)"
    assert tx.reread_label(image, b, "contact_a", "33HB FV2306", reader) is None
    assert tx.reread_label(image, b, "connector", "0010", reader) is None
    # 日本語の名称は器具番号として読み直さない
    assert tx.reread_label(image, b, "contact_a", "津谱完了", reader) is None
    assert len(asked) == 2


def test_reread_answer_that_is_not_a_device_number_keeps_the_ocr_label():
    image, b = Image.new("RGB", (40, 40), "white"), box(5, 5, 30, 30)
    assert tx.reread_label(image, b, "contact_a", "143E AMESD", lambda *_: "?") is None
    assert tx.reread_label(image, b, "contact_a", "143E AMESD", lambda *_: None) is None


def test_japanese_names_outside_tables_are_reread():
    texts = [
        {"text": "腻路檬域灯制御盐", "kind": "name", "symbol_id": "symbol_001", "zone": None, "box": [0, 0, 80, 12]},
        {"text": "端子", "kind": "note", "symbol_id": None, "zone": "relay_table", "box": [0, 20, 20, 32]},
        {"text": "常", "kind": "name", "symbol_id": "symbol_002", "zone": None, "box": [0, 40, 12, 52]},
    ]
    answers = iter(["航路標識灯制御盤", "C1"])
    assert tx.reread_texts(Image.new("RGB", (100, 60), "white"), texts, lambda *_: next(answers)) == 1
    assert [(t["text"], t.get("ocr_text")) for t in texts] == [("航路標識灯制御盤", "腻路檬域灯制御盐"), ("端子", None), ("常", None)]


def test_unreachable_label_llm_returns_no_answer():
    from analysis.label_llm import make_reader

    read = make_reader("http://127.0.0.1:9", "any-model", timeout=1.0)
    assert read(Image.new("RGB", (40, 40), "white"), box(5, 5, 30, 30)) is None
