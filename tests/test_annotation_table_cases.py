"""対応表（機械の答え・人の正解）の書き出しと読み込みの細かい場合（DD-0035）。"""

import io

import pytest
from openpyxl import Workbook

from services import annotation_table as at
from services import circuit_model
from services.circuit_model import build_circuit
from tests.test_sheet_links import receiver, sender


@pytest.fixture(autouse=True)
def builtin(monkeypatch):
    monkeypatch.setattr(circuit_model, "ngspice_path", lambda: None)


def test_export_lists_tag_pairs_and_tags_without_a_partner():
    pages = [sender(tags=(("T1", "ER21212へ", None), ("T2", "ER21213へ", None))), receiver()]
    pages[0]["connections"].append({"from_symbol_ref": "T1", "to_symbol_ref": "T2", "kind": "note"})  # 配線でない線は並べない
    rows = at.export_rows(pages, build_circuit(pages))
    tags = [r for r in rows if r["種別"] == at.SHEET_REF]
    assert any(r["判定"] in ("確定", "推定（要確認）") and r["根拠・備考"] == "行先札・来先札の組" for r in tags)
    assert any(r["判定"] == "未対応" and r["要素ID"].endswith(":T2") for r in tags)
    assert not any(r["要素ID"].endswith("T1") and r["参照・接続先ID"].endswith("T2") for r in rows)


def test_read_xlsx_skips_empty_and_unrelated_sheets():
    wb = Workbook()
    wb.active.title = "空"
    other = wb.create_sheet("メモ")
    other.append(["見出し"])
    table = wb.create_sheet("対応表")
    table.append(["要素ID", "種別"])
    table.append(["P:1", "接点"])
    table.append([None, None])
    buf = io.BytesIO()
    wb.save(buf)
    out = at.read_table(buf.getvalue(), "gold.xlsx")
    assert out == {"対応表": [{"要素ID": "P:1", "種別": "接点"}], "代表回路": []}


def test_states_and_judgements():
    assert at._states("A=1、B=0，C=?, 注記") == {"A": True, "B": False, "C": None}
    assert at._judge("判定不能") == "unknown" and at._judge("要確認（端子）") == "unknown"
    assert at._judge("要確認の接続") == "yes" and at._judge("交差") == "no"
