"""機器索引: コイルと接点の相互参照・電線番号の一覧・Excel 出力（DD-0044）。"""

import io

import pytest

from services import circuit_model
from services import device_index as di
from services.circuit_model import build_circuit
from tests.test_circuit_api import _save
from tests.test_circuit_model import _sym, _wire, self_holding_page

X = "X R100"


@pytest.fixture(autouse=True)
def builtin(monkeypatch):
    monkeypatch.setattr(circuit_model, "ngspice_path", lambda: None)


def _index(*pages):
    pages = list(pages)
    return di.build_index(pages, build_circuit(pages, {}))


def _device(index, name):
    return next(d for d in index["devices"] if d["device"] == name)


def test_relay_lists_coil_and_contacts_with_page_and_area():
    page = self_holding_page()
    page["sheet_no"] = "ER21236"
    by_ref = {s["ref"]: s for s in page["symbols"]}
    by_ref["XC"].update(cx=0.8, cy=0.2)     # コイルは右上
    by_ref["XA"].update(cx=0.1, cy=0.5)     # a 接点は左中
    by_ref["XL"].update(cx=0.5, cy=0.9)     # もう 1 つの a 接点は中下
    index = _index(page)
    x = _device(index, X)
    assert x["role"] == di.RELAY and x["role_label"] == "リレー"
    assert [(c["page"], c["area"]) for c in x["coils"]] == [("ER21236", "右上")]
    assert [c["symbol_ref"] for c in x["contacts"]] == ["XA", "XL"]          # ページ内は上から順
    assert [c["area"] for c in x["contacts"]] == ["左中", "中下"]
    assert (x["a_count"], x["b_count"]) == (2, 0)
    assert x["notes"] == []
    stop = _device(index, "STOP")
    assert stop["role"] == di.INPUT and stop["b_count"] == 1
    assert index["counts"]["relays"] == 1 and index["counts"]["inputs"] == 2
    # 表示灯は負荷として出る
    assert _device(index, "RL")["role"] == di.LOAD


def test_area_name():
    assert di.area_name(0.1, 0.1) == "左上"
    assert di.area_name(0.5, 0.5) == "中央"
    assert di.area_name(0.9, 0.5) == "右中"
    assert di.area_name(0.5, 0.95) == "中下"


def test_notes_for_reading_mistakes():
    page = self_holding_page()
    page["sheet_no"] = "ER21236"
    # コイルの無いリレー表示の接点（Y）、表記ゆれ（X R1O0 を X に寄せる）、コイルが 2 個（Z）、器具番号なしの接点
    page["symbols"] += [
        _sym("YC", "relay_coil", "Y R200", cy=0.3), _sym("XO", "contact_b", "X R1O0 (ER21236)", cy=0.6),
        _sym("Z1", "relay_coil", "Z R300", cy=0.2), _sym("Z2", "relay_coil", "Z R300", cy=0.8),
        _sym("ZA", "contact_a", "Z R300 (ER99999)"), _sym("Q", "contact_a", None, cy=0.7),
        _sym("W", "contact_a", "W R400 (ER21236)"),
    ]
    page["connections"] += [_wire(("P", 1), ("YC", 1)), _wire(("YC", 2), ("N", 1)), _wire(("XO", 1), ("P", 1)),
                            _wire(("Z1", 1), ("ZA", 1)), _wire(("Z2", 1), ("ZA", 2)), _wire(("Q", 1), ("P", 1)),
                            _wire(("W", 1), ("P", 1))]
    index = _index(page)
    kinds = lambda name: {n["kind"] for n in _device(index, name)["notes"]}  # noqa: E731
    assert "no_contacts" in kinds("Y R200")
    assert "label_variants" in kinds(X) and _device(index, X)["variants"] == ["X R1O0"]
    assert _device(index, X)["contacts"][-1]["coil_sheet"] == "ER21236"
    assert {"duplicate_coil", "coil_sheet_mismatch"} <= kinds("Z R300")
    # 選んだ図面（ER21236）にコイルがあるはずなのに無い → 読み違いの可能性
    assert "coil_missing_in_set" in kinds("W R400")
    assert [c["symbol_ref"] for c in index["unlabeled_contacts"]] == ["Q"]


def test_wire_numbers_group_pages_and_flag_split_nodes():
    page = self_holding_page()
    page["connections"][0]["wire_no"] = "101"
    page["connections"][6]["wire_no"] = "101"     # P─XL も同じ電源ノード
    page["connections"][3]["wire_no"] = "R 2"
    page["connections"][8]["wire_no"] = "R2"       # L─N（別のノード）
    page["connections"][4]["wire_no"] = "10"
    index = _index(page)
    numbers = [w["wire_no"] for w in index["wire_numbers"]]
    assert numbers == ["10", "101", "R 2"]       # 数字の順。空白の違いは同じ番号
    w101 = next(w for w in index["wire_numbers"] if w["wire_no"] == "101")
    assert w101["connections"] == 2 and w101["node_count"] == 1 and w101["note"] == ""
    # ラベルの無い母線の点は並べず、STOP・XL がつながる記号として並ぶ
    assert {e["symbol_ref"] for e in w101["ends"]} == {"STOP", "XL"}
    w2 = index["wire_numbers"][-1]
    assert w2["node_count"] == 2 and "別のノード" in w2["note"] and "書き方が違う" in w2["note"]


def test_xlsx_has_four_sheets_and_no_formula():
    from openpyxl import load_workbook

    page = self_holding_page()
    page["connections"][0]["wire_no"] = "=1+1"
    blob = di.to_xlsx(_index(page))
    wb = load_workbook(io.BytesIO(blob))
    assert wb.sheetnames == ["機器一覧", "接点の場所", "電線番号", "要確認"]
    devices = [r[0] for r in wb["機器一覧"].iter_rows(min_row=2, values_only=True)]
    assert devices[0] == X
    assert wb["電線番号"]["A2"].value == "=1+1" and wb["電線番号"]["A2"].data_type == "s"


def test_api_json_and_xlsx(client):
    pid = _save(client, self_holding_page())
    r = client.post("/api/circuit/devices", json={"project_ids": [pid]})
    assert r.status_code == 200, r.text
    assert _device(r.json(), X)["a_count"] == 2
    x = client.get(f"/api/circuit/devices.xlsx?project_ids={pid}")
    assert x.status_code == 200 and x.content.startswith(b"PK")
    assert client.get("/api/circuit/devices.xlsx?project_ids=a").status_code == 422
    assert client.post("/api/circuit/devices", json={"project_ids": [999999]}).status_code == 400


def test_coil_sheet_ignores_branch_number_and_unknown_sheet():
    """接点の枠の ER21236 は、枝番つきの ER21236-1 の図面のコイルと食い違いにしない。シート番号が分からない図面とは比べない。"""
    page = self_holding_page()
    page["sheet_no"] = "ER21236-1"
    for s in page["symbols"]:
        if s["ref"] == "XL":
            s["label"] = "X R100 (ER21236)"
    kinds = {n["kind"] for n in _device(_index(page), X)["notes"]}
    assert "coil_sheet_mismatch" not in kinds
    page["sheet_no"] = None
    page["name"] = "scan.png"
    kinds = {n["kind"] for n in _device(_index(page), X)["notes"]}
    assert "coil_sheet_mismatch" not in kinds
