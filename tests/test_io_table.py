"""I/O 表から接点の外部入力と信号名を入れる（DD-0050）。"""

from __future__ import annotations

import io

import pytest
from openpyxl import Workbook

from services import io_table


@pytest.fixture(autouse=True)
def _no_table():
    io_table.clear()
    yield
    io_table.clear()


CSV = "No,器具番号,信号名,入出力\n1,D001 B405,起動指令,入力\n2,D002 B406,運転表示,出力\n3,,空行,入力\n"


def page(source, *symbols):
    return {"source": source, "symbols": list(symbols)}


def contact(label, **attributes):
    return {"class_key": "contact_a", "label": label, "attributes": attributes}


def test_parse_csv_in_shift_jis_and_tabs():
    rows = io_table.parse(CSV.encode("cp932"), "io.csv")
    assert rows == [
        {"device": "D001 B405", "name": "起動指令", "direction": "input"},
        {"device": "D002 B406", "name": "運転表示", "direction": "output"},
    ]
    assert io_table.parse(CSV.replace(",", "\t").encode("utf-8"), "io.tsv") == rows


def test_parse_xlsx_with_a_title_row_above_the_header():
    wb = Workbook()
    ws = wb.active
    ws.append(["I/O 割付表"])
    ws.append(["点番号", "名称"])
    ws.append(["D009 B413", "主制御装置選択指令"])
    buf = io.BytesIO()
    wb.save(buf)
    assert io_table.parse(buf.getvalue(), "io.xlsx") == [{"device": "D009 B413", "name": "主制御装置選択指令", "direction": "input"}]


def test_parse_without_header_fails():
    with pytest.raises(ValueError, match="見出し"):
        io_table.parse(b"a,b\n1,2\n", "x.csv")


def test_apply_fills_contacts_and_keeps_what_people_entered():
    rows = io_table.parse(CSV.encode(), "io.csv")
    human = page("manual", contact("D001 B405 (EV31032)", signal_name="人の名前"), contact("D002 B406"),
                 {"class_key": "relay_coil", "label": "D001 B405", "attributes": {}})
    ai = page("ai", contact("D001 B405", signal_name="図面の文字", external_io=""))
    assert io_table.apply([human, ai], rows) == 3
    assert human["symbols"][0]["attributes"] == {"signal_name": "人の名前", "external_io": "yes"}
    assert human["symbols"][1]["attributes"] == {"signal_name": "運転表示"}
    assert human["symbols"][2]["attributes"] == {}
    assert ai["symbols"][0]["attributes"] == {"signal_name": "起動指令", "external_io": "yes"}
    # もう一度入れても変わらない
    assert io_table.apply([human, ai], rows) == 0


def test_apply_without_table_changes_nothing():
    p = page("ai", contact("D001 B405"))
    assert io_table.apply([p]) == 0
    assert p["symbols"][0]["attributes"] == {}


def test_io_table_api_upload_show_and_remove(client):
    assert client.get("/api/circuit/io-table").json()["loaded"] is False
    res = client.put("/api/circuit/io-table", files={"file": ("io.csv", CSV.encode(), "text/csv")})
    assert res.status_code == 200, res.text
    body = res.json()
    assert (body["loaded"], body["file_name"], body["rows"], body["inputs"]) == (True, "io.csv", 2, 1)
    assert client.get("/api/circuit/io-table").json()["rows"] == 2
    bad = client.put("/api/circuit/io-table", files={"file": ("x.csv", b"a,b\n1,2\n", "text/csv")})
    assert bad.status_code == 400
    assert client.delete("/api/circuit/io-table").json()["loaded"] is False
    assert io_table.load() is None
