"""リレー表の行を図面枠の行列で接点に結び付ける処理を、OCR の文字枠と記号の枠だけで確かめる。"""

from __future__ import annotations

from analysis.relay_links import drawing_grid, grid_coordinates, link_document_relay_rows, link_relay_rows

LETTERS = "ABCDEFGHJKLMNPQRSTUVWXYZ"


def text(value, x, y):
    return {"text": value, "score": 0.99, "box": (x - 5, y - 6, x + 5, y + 6)}


def border_labels():
    """列番号は上辺、行記号は左右の辺。スキャンが少し傾き、右の辺は左より 10 px 上にある。"""
    labels = [text(str(n), 51 + 64 * n, 45) for n in range(1, 25)]
    labels += [text(c, 41, 108 + 42.5 * k) for k, c in enumerate(LETTERS)]
    labels += [text(c, 1670, 98 + 42.5 * k) for k, c in enumerate(LETTERS)]
    labels[30] = text("8", 1670, 140)  # 読み違えた行記号（B を 8）
    return labels


def contact(sid, label, column, row, names, class_name="contact_a"):
    x, y = 51 + 64 * column, 108 + 42.5 * row
    return {
        "id": sid, "class_name": class_name, "type": class_name, "label": label,
        "bbox": {"x0": x - 20, "y0": y - 20, "x1": x + 40, "y1": y + 20},
        "terminals": [{"ref": f"{sid}-P1", "name": names[0], "point": [x - 18, y - 8]},
                      {"ref": f"{sid}-P2", "name": names[1], "point": [x - 18, y + 8]}],
    }


def table(*rows):
    rows = [{"device": d, "terminal": t, "contact": c, "sheet": s, "position": p} for d, t, c, s, p in rows]
    devices = list(dict.fromkeys(r["device"] for r in rows))
    return {"rows": rows, "links": [{"device": d, "rows": [i for i, r in enumerate(rows) if r["device"] == d]} for d in devices]}


def test_grid_maps_border_labels_to_page_coordinates_despite_a_misread_label():
    grid = drawing_grid(border_labels(), 1755, 1241)
    column, row = grid_coordinates(grid, 51 + 64 * 10, 108 + 42.5 * 3)
    assert abs(column - 10) < 0.05
    assert 2.8 < row < 3.1


def test_rows_link_to_the_contact_at_their_position_and_check_terminal_numbers():
    symbols = [contact("s1", "33HB FV2306", 10.3, 3.9, ["9", "5"]), contact("s2", "33LA FV2306", 10.6, 3.9, ["9", "6"])]
    result = {"texts": border_labels(),
              "relay_table": table(("33HB FV2306", "5-9-1", "a", "本", "10D"), ("33LA FV2306", "5-9-1", "a", "本", "11D"))}
    counts = link_relay_rows(result, symbols, 1755, 1241)
    links = {link["row"]: link for device in result["relay_table"]["links"] for link in device["row_links"]}
    assert counts == {"rows": 2, "linked": 2}
    assert (links[0]["symbol_id"], links[0]["status"]) == ("s1", "linked")
    assert symbols[0]["relay_row"]["table_terminal_numbers"] == ["9", "5"]
    assert (links[1]["symbol_id"], links[1]["status"], links[1]["reasons"]) == ("s2", "要確認", ["端子番号が表と違う"])


def test_table_type_corrects_the_class_only_when_the_row_is_unambiguous():
    named = contact("s1", "33HB FV2306", 10.0, 3.9, ["9", "5"], "contact_b")
    unnamed = contact("s2", "", 14.0, 3.9, ["12", "8"], "contact_b")
    result = {"texts": border_labels(),
              "relay_table": table(("33HB FV2306", "5-9-1", "a", "本", "10D"), ("33HB FV2306", "8-12-4", "a", "本", "14D"))}
    link_relay_rows(result, [named, unnamed], 1755, 1241)
    assert (named["class_name"], named["detected_class"], named["class_label"]) == ("contact_a", "contact_b", "a接点（メーク）")
    assert unnamed["class_name"] == "contact_b"
    assert unnamed["relay_row"]["status"] == "要確認"
    assert "接点の種類（a/b）が表と違う" in unnamed["relay_row"]["reasons"]


def test_rows_pointing_to_another_sheet_link_when_both_pages_are_exported():
    coil_page = {"relay_table": table(("33HB HV305", "5-9-1", "a", "ER21237", "3D"), ("33HB HV305", "8-12-4", "a", "ER21999", "9D"))}
    contact_page = {"relay_table": table(), "symbols": [contact("s9", "33HB HV305 (ER21236)", 3.6, 3.9, ["9", "5"])]}
    result = {"texts": border_labels(), "relay_table": contact_page["relay_table"]}
    link_relay_rows(result, contact_page["symbols"], 1755, 1241)
    contact_page["drawing_grid"] = result["drawing_grid"]
    assert link_document_relay_rows([("ER21236", 1, coil_page), ("ER21237", 2, contact_page)]) == (1, 1)
    linked = contact_page["symbols"][0]["relay_row"]
    assert (linked["project_id"], linked["row"], linked["status"]) == (1, 0, "linked")
    assert coil_page["relay_table"]["links"][0]["row_links"][0]["project_id"] == 2
