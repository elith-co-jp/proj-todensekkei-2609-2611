"""整合性チェック（DD-0031）の個々の突き合わせの細かい場合。test_consistency.py の図面を少しずつ変えて確かめる。"""

from types import SimpleNamespace

import pytest

from services import circuit_model, circuit_whatif, consistency
from tests.test_circuit_model import _wire
from tests.test_consistency import GRID, ROWS, _by, _sym, page, structure


@pytest.fixture(autouse=True)
def builtin(monkeypatch):
    monkeypatch.setattr(circuit_model, "ngspice_path", lambda: None)


def _checker(pages, structures=None, **circuit):
    base = {"elements": [], "coils": {}, "inputs": [], "node_of": {}, "wires": [], "sheet_links": [], "diagnostics": []}
    return consistency.Checker(pages, structures or {}, SimpleNamespace(**{**base, **circuit}))


def test_small_helpers():
    assert consistency._first_token("12 3 33HC (ER21217)") == "33HC"
    assert consistency._near("33HC", "33H") and consistency._near("33H", "33HC")
    assert not consistency._near("33HC", "3C") and not consistency._near("ABCD", "AXCY")
    assert consistency._symbol_trust({"origin": "inference"}) == "AI の検出"
    assert consistency._symbol_trust({"origin": "manual"}) == "人が登録した記号"
    assert consistency.input_kind_by_name("START STOP") is None
    assert consistency.input_kind_by_name("非常停止") == "b" and consistency.input_kind_by_name("運転") == "a"


def test_unique_after_assignment_needs_a_position_and_ignores_other_devices():
    rows = [{"device": "X R100", "position": ""}, {"device": "X R100", "position": "10D"}]
    symbols = consistency._px_symbols(page(xa_kind="contact_a"))
    assert consistency._unique_after_assignment(rows, 0, {}, symbols, GRID) is False
    # 窓の中の X R100 の接点は XA だけ（STOP・PB1 は別の器具、XL は窓の外）
    assert consistency._unique_after_assignment(rows, 1, {1: {"symbol_id": "XA"}}, symbols, GRID) is True


def test_two_contacts_in_one_window_are_settled_by_the_neighbouring_row():
    p = page(xa_kind="contact_b")
    p["symbols"].append(_sym("XB", "contact_a", "X R100", cx=0.55, cy=0.14))
    rows = [ROWS[0], {**ROWS[0], "position": "11D"},
            {"device": "X R100", "contact": "a", "sheet": "本", "position": ""},       # 位置が無い行
            {"device": "X R100", "contact": "a", "sheet": "ER99999", "position": "1A"}]  # 選んでいない図面の行
    result = consistency.check([p], {1: structure(rows)})
    kind = next(i for i in _by(result, "relay_table") if i["title"] == "接点の種類（a/b）が表と違う")
    assert kind["fix"] == {"type": "set_class", "project_id": 1, "symbol_ref": "XA", "value": "contact_a"}


def test_contact_pointing_to_an_unselected_sheet_is_not_compared():
    p = page(xa_kind="contact_a")
    p["symbols"][3]["label"] = "33HC (ER99999)"
    result = consistency.check([p], {})
    assert next(c for c in result["checks"] if c["check"] == "coil_sheet")["status"] == "skipped"


def test_contact_pointing_to_a_sheet_without_any_similar_coil_only_asks_to_check():
    p1 = page(xa_kind="contact_a")
    p1["symbols"][3]["label"] = "Z9 (ER21217)"
    p2 = {"id": 2, "name": "ER21217.png", "sheet_no": "ER21217", "image_width": 1000, "image_height": 1000,
          "symbols": [_sym("P", "power_bus", cy=0.05, terms=1), _sym("N", "power_bus", cy=0.95, terms=1),
                      _sym("C", "relay_coil", "33HG", cy=0.5)],
          "connections": [_wire(("P", 1), ("C", 1)), _wire(("C", 2), ("N", 1))]}
    issue = _by(consistency.check([p1, p2], {}), "coil_sheet")[0]
    assert issue["fix"] is None and issue["severity"] == "warning"
    assert issue["proposal"] == "接点のラベル（器具番号・シート番号）とコイルの図面を確かめる"


def _tag_page(pid, sheet, tag_label, wire_no):
    return {"id": pid, "name": f"{sheet}.png", "sheet_no": sheet, "image_width": 1000, "image_height": 1000,
            "symbols": [_sym("T", "sheet_ref", tag_label, terms=1), _sym("A", "contact_a", "A", cx=0.3)],
            "connections": [{**_wire(("T", 1), ("A", 1)), "wire_no": wire_no}]}


def test_paired_tags_with_and_without_wire_numbers():
    a = {"project_id": 1, "symbol_ref": "T", "sheet": "ER1", "label": None}
    b = {"project_id": 2, "symbol_ref": "T", "sheet": "ER2", "label": "行先"}
    pages = [_tag_page(1, "ER1", "ER2", "601"), _tag_page(2, "ER2", "ER1", "602"), _tag_page(3, "ER3", "ER1", None)]
    c = {"project_id": 3, "symbol_ref": "T", "sheet": "ER3", "label": None}
    checker = _checker(pages, sheet_links=[{"from": a, "to": b}, {"from": c, "to": a}])
    checker.sheet_link_numbers()
    [issue] = checker.issues
    assert issue["title"] == "行先札の両側で電線番号が違う" and "T（601）" in issue["message"] and "行先（602）" in issue["message"]
    assert checker.checks[0]["compared"] == 1


def test_unpaired_tags_skip_pages_without_sheet_or_numbers():
    pages = [_tag_page(1, "ER21001", "ER21005", "601"), {**_tag_page(2, "", "ER21005", "601"), "sheet_no": None, "name": "x"},
             _tag_page(3, "ER21003", "ER21005", None)]
    open_tags = [{"kind": "sheet_link_unresolved", "project_id": pid, "symbol_ref": "T", "target": "ER21005"}
                 for pid in (1, 2, 3)]
    checker = _checker(pages, diagnostics=open_tags)
    # 行先 ER21005 は選んでいない図面で、組の候補も無い → 回路の診断に任せて数えるだけ
    assert checker._unpaired_tags({(1, "T"): {"601"}, (2, "T"): {"601"}}) == 1
    assert checker.issues == []


def test_open_terminals_skips_symbols_without_two_terminals_and_reports_isolated_ones():
    p = page(xa_kind="contact_a")
    p["symbols"] += [_sym("Q", "contact_a", "Q", cx=0.9, cy=0.9), _sym("Q2", "contact_a", "Q", cx=0.9, cy=0.9),
                     _sym("R", "contact_a", "R", cx=0.1, cy=0.9), _sym("ONE", "lamp", "ONE", terms=1)]
    result = consistency.check([p], {})
    titles = {i["locations"][0]["symbol_ref"]: i["title"] for i in _by(result, "open_terminals")}
    assert titles["Q"] == titles["Q2"] == "記号が重なっている（二重の検出）"
    assert titles["R"] == "どこにもつながっていない記号"
    assert "ONE" not in titles
    assert consistency.Checker([p], {}, circuit_model.build_circuit([p]))._overlapping(99, p["symbols"][0]) is None


def test_solver_agreement_uses_ngspice_and_reports_disagreement(monkeypatch):
    p = page(xa_kind="contact_a")
    circuit = circuit_model.build_circuit([p])
    monkeypatch.setattr(circuit_model, "ngspice_path", lambda: "ngspice")
    monkeypatch.setattr(circuit_model, "_solve_ngspice", lambda text, path: ({}, None))
    checker = consistency.Checker([p], {}, circuit)
    checker.solver_agreement()
    issue = _by({"issues": checker.issues}, "solvers")[0]
    assert issue["title"] == "回路計算の方法で結果が違う" and "ngspice" in checker.checks[0]["method_b"]
    assert any("電圧ありと解いた方法" in r for r in issue["review"])


def test_solver_agreement_without_any_engine(monkeypatch):
    import builtins

    real_import = builtins.__import__

    def no_lcapy(name, *args, **kwargs):
        if name == "lcapy":
            raise ImportError(name)
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", no_lcapy)
    p = page(xa_kind="contact_a")
    checker = consistency.Checker([p], {}, circuit_model.build_circuit([p]))
    checker.solver_agreement()
    assert checker.checks[0]["skipped_reason"] == "ngspice も Lcapy も入っていません"


def test_impact_reports_changes_outside_the_static_reach(monkeypatch):
    p = page(xa_kind="contact_a")
    monkeypatch.setattr(circuit_whatif, "impact", lambda circuit, dev: {"devices": [], "loads": []})
    result = consistency.check([p], {})
    issue = _by(result, "impact")[0]
    assert issue["severity"] == "error" and issue["locations"]


def test_oscillation_with_a_b_contact_confirmed_by_the_table_points_to_extra_wiring():
    p = page(xa_kind="contact_b")
    checker = consistency.Checker([p], {}, circuit_model.build_circuit([p]))
    checker.table_kind[(1, "XA")] = "b"
    checker.stability()
    issue = _by({"issues": checker.issues}, "stability")[0]
    assert issue["fix"] is None and "余分な接続" in issue["proposal"]


def test_conflicting_fixes_for_one_symbol_go_to_a_person():
    p = page(xa_kind="contact_a")
    checker = consistency.Checker([p], {}, circuit_model.build_circuit([p]))
    fix = {"type": "set_label", "project_id": 1, "symbol_ref": "XA"}
    for step in ("relay_table", "coil_sheet", "wire_numbers", "sheet_link_numbers", "open_terminals", "input_names",
                 "solver_agreement", "impact_vs_whatif", "stability"):
        setattr(checker, step, lambda: None)
    checker.add("relay_table", "error", "a", "m", [], [], "p", {**fix, "value": "X"})
    checker.add("coil_sheet", "error", "b", "m", [], [], "p", {**fix, "value": "Y"})
    result = checker.run()
    assert all(i["fix"] is None and "人が判断する" in i["review"][-1] for i in result["issues"])


def _saved_pages(client, pages, sheets):
    from tests.test_circuit_api import _save

    ids = []
    for drawing, sheet in zip(pages, sheets, strict=True):
        pid = _save(client, drawing)
        meta = client.get(f"/api/projects/{pid}").json()
        assert client.put(f"/api/projects/{pid}/meta", json={"name": meta["name"], "sheet_no": sheet}).status_code == 200
        ids.append(pid)
    return ids


def test_apply_relabels_a_coil_and_skips_classes_missing_from_the_master(client):
    from database import SessionLocal
    from models import SymbolClass

    p1 = page(xa_kind="contact_b")
    p1["symbols"][3]["label"] = "33HC FV2306 (ER21217)"
    p1["symbols"].append(_sym("Y", "contact_a", "33HC FV2306", cx=0.9, cy=0.9))
    p2 = {"id": 2, "name": "ER21217.png", "symbols": [
        _sym("P", "power_bus", cy=0.05, terms=1), _sym("N", "power_bus", cy=0.95, terms=1),
        _sym("C", "relay_coil", "33HG FV2306", cy=0.5)],
        "connections": [_wire(("P", 1), ("C", 1)), _wire(("C", 2), ("N", 1))]}
    ids = _saved_pages(client, [p1, p2], ["ER21216", "ER21217"])
    result = client.post("/api/circuit/consistency", json={"project_ids": ids}).json()
    label_fix = next(i for i in result["issues"] if (i["fix"] or {}).get("type") == "set_label")
    class_fix = next(i for i in result["issues"] if (i["fix"] or {}).get("type") == "set_class")
    applied = client.post("/api/circuit/consistency/apply", json={"project_ids": ids, "issue_ids": [label_fix["id"]]}).json()
    assert [a["type"] for a in applied["applied"]] == ["set_label"]
    coil = next(s for s in client.get(f"/api/projects/{ids[1]}").json()["symbols"] if s["ref"] == "C")
    assert coil["label"] == label_fix["fix"]["value"] and "整合性チェック" in coil["note"]
    # クラスマスタで a 接点の種類の名前を変えた後は、a 接点にする直し方を適用しない
    db = SessionLocal()
    db.query(SymbolClass).filter(SymbolClass.key == "contact_a").update({"key": "contact_make"})
    db.commit()
    db.close()
    applied = client.post("/api/circuit/consistency/apply", json={"project_ids": ids, "issue_ids": [class_fix["id"]]}).json()
    assert applied["applied"] == [] and applied["not_applied"] == []
