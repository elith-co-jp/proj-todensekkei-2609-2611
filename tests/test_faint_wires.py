"""Faint wires between aligned terminals (DD-0007 §4.22). Generated strokes only."""

import pytest
from PIL import Image, ImageDraw


@pytest.fixture
def bridge():
    from analysis.wiring.faint_lines import bridge_faint_wires

    return bridge_faint_wires


def _symbol(sid, cls, box, terminals):
    return {"id": sid, "class_name": cls, "bbox": dict(zip(("x0", "y0", "x1", "y1"), box, strict=True)),
            "terminals": [{"ref": f"{sid}-P{i}", "name": None, "point": list(p)} for i, p in enumerate(terminals, 1)]}


def _page(tone, dotted=True, x=300):
    image = Image.new("RGB", (1755, 1241), "white")
    draw = ImageDraw.Draw(image)
    for y in range(240, 340, 6 if dotted else 1):
        draw.line((x, y, x, y + (3 if dotted else 1)), fill=(tone, tone, tone))
    return image


def _payload():
    contact = _symbol("c", "contact_a", (280, 180, 360, 245), [(300, 210), (300, 232)])
    connector = _symbol("n", "connector", (270, 340, 340, 380), [(300, 352), (300, 370)])
    return {"symbols": [contact, connector], "from_to": {"connections": [], "connection_count": 0}}


def test_faint_dotted_line_joins_contact_and_connector(bridge):
    payload = _payload()
    added = bridge(payload, _page(225))
    assert [(c["from"]["terminal_ref"], c["to"]["terminal_ref"]) for c in added] == [("c-P2", "n-P1")]
    assert payload["from_to"]["connection_count"] == 1


@pytest.mark.parametrize("image", [
    Image.new("RGB", (1755, 1241), "white"),  # nothing drawn: no wire
    _page(40, dotted=False),                  # a dark line left unconnected by the wiring stays so
])
def test_no_line_or_dark_line_is_not_bridged(bridge, image):
    assert bridge(_payload(), image) == []


def test_already_connected_terminals_are_not_bridged_again(bridge):
    payload = _payload()
    payload["from_to"]["connections"].append({
        "type": "wire", "from": {"symbol_id": "c", "terminal_ref": "c-P2"}, "to": {"symbol_id": "n", "terminal_ref": "n-P1"},
    })
    assert bridge(payload, _page(225)) == []


def test_line_toward_the_contacts_other_terminal_runs_through_it(bridge):
    # The top terminal looks down through its own contact; only the bottom terminal leaves downward.
    payload = _payload()
    payload["symbols"][0]["terminals"] = payload["symbols"][0]["terminals"][:1]
    payload["symbols"][0]["terminals"].append({"ref": "c-P9", "name": None, "point": [330, 232]})
    assert bridge(payload, _page(225)) == []
