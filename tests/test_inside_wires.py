"""同じ記号の端子どうしを結ぶ配線の扱い（DD-0022）: 盤の枠の中の線は導線、カードの + と - と機器の両端は素子。"""

from services.circuit_model import joins_inside_symbol


def _other(points, label="2RAXP"):
    return {"ref": "S", "class_key": "other", "label": label,
            "terminals": [{"ref": f"S-T{i}", "tx": x / 1000, "ty": y / 1000} for i, (x, y) in enumerate(points, 1)]}


def test_bus_and_border_crossing_inside_a_panel_frame_are_wires():
    """ER21315 の 2RAXP: 黒丸 41 から母線へ下りる短い線（14 px）と、母線に沿った横の線（83 px）。"""
    panel = _other([(673, 479), (676, 493), (759, 493)])
    assert joins_inside_symbol(panel, "S-T1", "S-T2", 1000, 1000)
    assert joins_inside_symbol(panel, "S-T2", "S-T3", 1000, 1000)


def test_card_plus_to_minus_and_contact_ends_are_elements():
    """2B-2RSAM のカードの列の上の黒丸と下の黒丸（+ と -、約 60 px の縦の線）は入力の素子。接点の両端も素子。"""
    card = _other([(1017, 502), (1018, 559)], label="2B-2RSAM 628")
    assert not joins_inside_symbol(card, "S-T1", "S-T2", 1000, 1000)
    contact = {**_other([(500, 100), (500, 110)]), "class_key": "contact_a"}
    assert not joins_inside_symbol(contact, "S-T1", "S-T2", 1000, 1000)
    assert not joins_inside_symbol(_other([(673, 479), (676, 493)]), "S-T1", "S-T2", 0, 0)
