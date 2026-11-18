"""行先札・来先札で図面をまたいで配線をつなぐ（DD-0030）。"""

import pytest

from services import circuit_code, circuit_model
from services import circuit_whatif as w
from services.circuit_model import build_circuit, simulate
from services.sheet_links import page_sheet, sheet_tag_target
from tests.test_circuit_model import _sym, _wire


@pytest.fixture(autouse=True)
def builtin(monkeypatch):
    monkeypatch.setattr(circuit_model, "ngspice_path", lambda: None)


def _tag(ref, label):
    return _sym(ref, "other", label, terms=1)


def _numbered(a, b, wire_no):
    return _wire(a, b) | {"wire_no": wire_no}


def sender(pid=1, sheet="ER21211", tags=(("T1", "ER21212へ", None),)):
    """P ─ PB<n>(a) ─ 行先札。札ごとに押しボタンを 1 つ。"""
    symbols = [_sym("P", "power_bus", terms=1, cy=0.1), _sym("N", "power_bus", terms=1, cy=0.9)]
    wires = []
    for i, (ref, label, wire_no) in enumerate(tags, start=1):
        symbols += [_sym(f"PB{i}", "contact_a", f"PB{i}"), _tag(ref, label)]
        wires += [_wire(("P", 1), (f"PB{i}", 1)), _numbered((f"PB{i}", 2), (ref, 1), wire_no)]
    return {"id": pid, "name": f"{sheet}.png", "sheet_no": sheet, "image_width": 1000, "image_height": 1000,
            "symbols": symbols, "connections": wires}


def receiver(pid=2, sheet="ER21212", tags=(("R1", "(ER21211)", None),)):
    """来先札 ─ コイル X<n> ─ N。"""
    symbols = [_sym("P", "power_bus", terms=1, cy=0.1), _sym("N", "power_bus", terms=1, cy=0.9)]
    wires = []
    for i, (ref, label, wire_no) in enumerate(tags, start=1):
        symbols += [_tag(ref, label), _sym(f"XC{i}", "relay_coil", f"X{i} R100")]
        wires += [_numbered((ref, 1), (f"XC{i}", 1), wire_no), _wire((f"XC{i}", 2), ("N", 1))]
    return {"id": pid, "name": f"{sheet}.png", "sheet_no": sheet, "image_width": 1000, "image_height": 1000,
            "symbols": symbols, "connections": wires}


def kinds(circuit):
    return [d["kind"] for d in circuit.diagnostics if d["kind"].startswith("sheet_link")]


def test_tag_labels_and_sheet_numbers():
    assert sheet_tag_target(_tag("T", "ER21212へ")) == "ER21212"
    assert sheet_tag_target(_tag("T", "(ER21011-3) 610")) == "ER21011-3"
    assert sheet_tag_target(_tag("T", "ER21212より")) == "ER21212"
    assert sheet_tag_target(_tag("T", "2RAXP (ER21212)")) is None  # 盤の名前
    assert sheet_tag_target(_tag("T", "2B-DIO-1 ER21212")) is None  # カード
    assert sheet_tag_target(_sym("C", "contact_a", "33HB (ER21212)")) is None  # 接点の枠のコイルのシート
    assert page_sheet({"name": "Tif画像 - ER21216", "sheet_no": None}) == "ER21216"
    assert page_sheet({"name": "scan-001.png", "sheet_no": "er21216"}) == "ER21216"


def test_destination_tags_connect_sheets():
    pages = [sender(), receiver()]
    alone = build_circuit([pages[0]]), build_circuit([pages[1]])
    assert "sheet_link_unresolved" in kinds(alone[0])

    circuit = build_circuit(pages)
    assert [link["how"] for link in circuit.sheet_links] == ["guessed"]
    assert kinds(circuit) == ["sheet_link_guessed"]
    assert simulate(circuit, {})["coils"]["X1 R100"]["energized"] is False
    assert simulate(circuit, {"PB1": True})["coils"]["X1 R100"]["energized"] is True


def test_wire_numbers_pick_the_right_partner():
    tx = (("T1", "ER21212へ", "610"), ("T2", "ER21212へ", "611"))
    rx = (("R1", "(ER21211)", "611"), ("R2", "(ER21211)", "610"))  # 並びは逆
    circuit = build_circuit([sender(tags=tx), receiver(tags=rx)])
    assert [link["how"] for link in circuit.sheet_links] == ["matched", "matched"]
    assert kinds(circuit) == []
    on = simulate(circuit, {"PB1": True})["coils"]
    assert on["X2 R100"]["energized"] is True and on["X1 R100"]["energized"] is False


def test_ambiguous_tags_are_left_open_and_reported():
    tx = (("T1", "ER21212へ", None), ("T2", "ER21212へ", None))
    rx = (("R1", "(ER21211)", None), ("R2", "(ER21211)", None))
    circuit = build_circuit([sender(tags=tx), receiver(tags=rx)])
    assert circuit.sheet_links == []
    assert kinds(circuit) == ["sheet_link_unresolved"] * 4
    assert all("1 つに決まりません" in d["message"] for d in circuit.diagnostics if d["kind"] == "sheet_link_unresolved")


def test_tags_pointing_elsewhere_do_not_connect():
    circuit = build_circuit([sender(tags=(("T1", "ER21999へ", None),)), receiver()])
    assert circuit.sheet_links == []
    messages = [d["message"] for d in circuit.diagnostics if d["kind"] == "sheet_link_unresolved"]
    assert any("ER21999 の図面が選ばれていない" in m for m in messages)
    assert any("見つかりません" in m for m in messages)


def test_sheet_ref_connection_acts_as_tag():
    """種別「シート間参照」の配線（external_ref にシート番号）も札として扱い、導線としてもつなぐ。"""
    page = sender()
    page["symbols"][-1] = _sym("T1", "terminal", "TB1", terms=1)
    page["connections"][-1] |= {"kind": "sheet_ref", "external_ref": "ER21212へ 610"}
    rx = receiver(tags=(("R1", "(ER21211)", "610"),))
    circuit = build_circuit([page, rx])
    assert [link["how"] for link in circuit.sheet_links] == ["matched"]
    assert circuit.sheet_links[0]["from"]["label"] == "ER21212へ 610"
    assert simulate(circuit, {"PB1": True})["coils"]["X1 R100"]["energized"] is True


def test_impact_and_code_follow_the_link():
    pages = [sender(), receiver()]
    circuit = build_circuit(pages)
    assert [d["device"] for d in w.impact(circuit, "PB1")["devices"]] == ["X1 R100"]
    again = circuit_code.from_python(circuit_code.to_python(circuit, pages=pages))
    assert simulate(again, {"PB1": True})["coils"]["X1 R100"]["energized"] is True


def test_build_api_returns_sheet_links(client):
    from tests.test_circuit_api import _save

    ids = []
    for page in (sender(), receiver()):
        pid = _save(client, page)
        assert client.put(f"/api/projects/{pid}/meta", json={"sheet_no": page["sheet_no"]}).status_code == 200
        ids.append(pid)
    built = client.post("/api/circuit/build", json={"project_ids": ids}).json()
    assert [(link["how"], link["from"]["page"], link["to"]["page"]) for link in built["sheet_links"]] == [
        ("guessed", "ER21211", "ER21212")]
    on = client.post("/api/circuit/simulate", json={"project_ids": ids, "inputs": {"PB1": True}}).json()
    assert on["coil_states"]["X1 R100"]["energized"] is True
