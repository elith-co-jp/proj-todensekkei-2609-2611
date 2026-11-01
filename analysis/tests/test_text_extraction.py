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
