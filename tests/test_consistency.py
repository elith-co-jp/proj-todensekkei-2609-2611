"""複数の解析方法の結果の矛盾チェックと、精査・直し方（DD-0031）。"""

import pytest

from services import circuit_model, consistency
from tests.test_circuit_model import _wire

# 図面枠の行列: 列 n の x = 50 n（画素）、行 k の y = 40 k（A が 0）。1000 x 1000 の図面。
GRID = {"columns": [0.0, 50.0], "rows": [[0.0, 0.0, 40.0]]}


@pytest.fixture(autouse=True)
def builtin(monkeypatch):
    monkeypatch.setattr(circuit_model, "ngspice_path", lambda: None)


def _sym(ref, cls, label=None, cx=0.5, cy=0.5, terms=2, origin="inference", confidence=0.6):
    return {"ref": ref, "class_key": cls, "class_label": cls, "label": label, "cx": cx, "cy": cy, "w": 0.02, "h": 0.02,
            "origin": origin, "confidence": confidence,
            "terminals": [{"ref": f"{ref}-T{i}", "name": str(i), "tx": cx, "ty": cy} for i in range(1, terms + 1)]}


def page(xa_kind="contact_b", coil_label="X R100"):
    """自己保持の回路。X の自己保持の接点 XA（行列 10D）を AI が b 接点と読み違えた。"""
    symbols = [
        _sym("P", "power_bus", cy=0.05, terms=1), _sym("N", "power_bus", cy=0.95, terms=1),
        _sym("STOP", "contact_b", "STOP", cx=0.2, cy=0.3), _sym("PB1", "contact_a", "PB1", cx=0.3, cy=0.4),
        _sym("XA", xa_kind, "X R100", cx=0.5, cy=0.14), _sym("XC", "relay_coil", coil_label, cx=0.4, cy=0.6),
        _sym("XL", "contact_a", "X R100", cx=0.7, cy=0.22), _sym("L", "lamp", "RL", cx=0.7, cy=0.6),
    ]
    wires = [
        _wire(("P", 1), ("STOP", 1)), _wire(("STOP", 2), ("PB1", 1)), _wire(("STOP", 2), ("XA", 1)),
        _wire(("PB1", 2), ("XC", 1)), _wire(("XA", 2), ("XC", 1)), _wire(("XC", 2), ("N", 1)),
        _wire(("P", 1), ("XL", 1)), _wire(("XL", 2), ("L", 1)), _wire(("L", 2), ("N", 1)),
    ]
    return {"id": 1, "name": "ER21216.png", "sheet_no": "ER21216", "image_width": 1000, "image_height": 1000,
            "symbols": symbols, "connections": wires}


def structure(rows):
    return {"relay_table": {"rows": rows}, "drawing_grid": GRID}


ROWS = [
    {"device": "X R100", "terminal": "5-9-1", "contact": "a", "sheet": "本", "position": "10D"},
    {"device": "X R100", "terminal": "6-10-2", "contact": "a", "sheet": "本", "position": "14F"},
]


def _by(result, check):
    return [i for i in result["issues"] if i["check"] == check]


def test_relay_table_and_simulation_agree_on_the_misread_contact():
    result = consistency.check([page()], {1: structure(ROWS)})
    kinds = _by(result, "relay_table")
    assert [i["title"] for i in kinds] == ["接点の種類（a/b）が表と違う"]
    assert kinds[0]["fix"] == {"type": "set_class", "project_id": 1, "symbol_ref": "XA", "value": "contact_a"}
    assert any("1 つ" in r for r in kinds[0]["review"])
    # 発振の精査も同じ直し方を出すが、1 つにまとめる。
    loop = _by(result, "stability")
    assert loop and loop[0]["fix"] is None and "同じ直し方" in loop[0]["proposal"] + " ".join(loop[0]["review"])
    assert result["summary"]["fixable"] == 1
    status = {c["check"]: c["status"] for c in result["checks"]}
    assert status["relay_table"] == "issues" and status["impact"] == "ok" and status["solvers"] in ("ok", "skipped")


def test_consistent_drawing_has_no_issues():
    result = consistency.check([page(xa_kind="contact_a")], {1: structure(ROWS)})
    assert result["issues"] == []
    assert {c["check"]: c["status"] for c in result["checks"]}["relay_table"] == "ok"


def test_missing_coil_with_one_letter_misread_is_relabelled():
    result = consistency.check([page(xa_kind="contact_a", coil_label="X R10O")],
                               {1: structure([{**ROWS[0], "device": "33HC"}])})
    coil = next(i for i in result["issues"] if i["title"] == "リレー表の器具のコイルが無い")
    assert coil["fix"] is None  # X R10O は 33HC の 1 文字違いではない
    result = consistency.check([page(xa_kind="contact_a", coil_label="33HG")],
                               {1: structure([{**ROWS[0], "device": "33HC", "position": "1A"}])})
    coil = next(i for i in result["issues"] if i["title"] == "リレー表の器具のコイルが無い")
    assert coil["fix"] == {"type": "set_label", "project_id": 1, "symbol_ref": "XC", "value": "33HC"}


def test_table_position_without_contact_and_contact_without_row():
    rows = [ROWS[0], {**ROWS[1], "position": "2B"}]  # 2B（x=100, y=40 付近）には接点が無い
    result = consistency.check([page(xa_kind="contact_a")], {1: structure(rows)})
    titles = sorted(i["title"] for i in _by(result, "relay_table"))
    assert titles == ["リレー表に無い接点", "リレー表の位置に接点が無い"]
    extra = next(i for i in result["issues"] if i["title"] == "リレー表に無い接点")
    assert extra["locations"][0]["symbol_ref"] == "XL"


def test_wire_numbers_contradict_connectivity():
    p = page(xa_kind="contact_a")
    p["connections"][0]["wire_no"] = "601"     # P-STOP（P）
    p["connections"][3]["wire_no"] = "610"     # PB1-XC
    p["connections"][4]["wire_no"] = "610"     # XA-XC: 同じノード → 矛盾なし
    p["connections"][7]["wire_no"] = "610"     # XL-L: 別のノードに 610 → 矛盾
    p["connections"][1]["wire_no"] = "602"     # STOP-PB1
    p["connections"][2]["wire_no"] = "603"     # STOP-XA: 同じノードに 602 と 603 → 矛盾
    result = consistency.check([p], {})
    titles = sorted(i["title"] for i in _by(result, "wire_numbers"))
    assert titles == ["1 つのつながりに電線番号が 2 つ以上", "同じ電線番号が別々のつながりにある"]
    split = next(i for i in result["issues"] if i["title"] == "同じ電線番号が別々のつながりにある")
    assert {loc["symbol_ref"] for loc in split["locations"]} == {"XL", "L"}  # 少ない方を先に確かめる


def test_contact_label_sheet_points_to_missing_coil():
    p1 = page(xa_kind="contact_a")
    p1["symbols"][3]["label"] = "33HC FV2306 (ER21217)"  # PB1 の接点は ER21217 のコイルで動くと書かれている
    p2 = {"id": 2, "name": "ER21217.png", "sheet_no": "ER21217", "image_width": 1000, "image_height": 1000,
          "symbols": [_sym("P", "power_bus", cy=0.05, terms=1), _sym("N", "power_bus", cy=0.95, terms=1),
                      _sym("C", "relay_coil", "33HG FV2306", cy=0.5)],
          "connections": [_wire(("P", 1), ("C", 1)), _wire(("C", 2), ("N", 1))]}
    result = consistency.check([p1, p2], {})
    issue = _by(result, "coil_sheet")[0]
    assert issue["fix"]["value"].startswith("33HG") and issue["fix"]["symbol_ref"] == "PB1"


def test_solvers_compared_when_lcapy_is_installed():
    pytest.importorskip("lcapy")
    result = consistency.check([page(xa_kind="contact_a")], {})
    solvers = next(c for c in result["checks"] if c["check"] == "solvers")
    assert solvers["status"] == "ok" and solvers["compared"] == 3  # 全 OFF と入力 2 つ


def test_check_and_apply_api(client):
    from database import SessionLocal
    from services.structure_service import save_structure
    from tests.test_circuit_api import _save

    pid = _save(client, page())
    db = SessionLocal()
    try:
        save_structure(db, pid, structure(ROWS))
        db.commit()
    finally:
        db.close()
    result = client.post("/api/circuit/consistency", json={"project_ids": [pid]}).json()
    issue = next(i for i in result["issues"] if i["fix"])
    assert issue["fix"]["symbol_ref"] == "XA" and issue["locations"][0]["page"]
    applied = client.post("/api/circuit/consistency/apply", json={"project_ids": [pid], "issue_ids": [issue["id"], "nope"]}).json()
    assert [a["value"] for a in applied["applied"]] == ["contact_a"] and applied["not_applied"] == ["nope"]
    assert [i for i in applied["issues"] if i["check"] in ("relay_table", "stability")] == []
    project = client.get(f"/api/projects/{pid}").json()
    xa = next(s for s in project["symbols"] if s["ref"] == "XA")
    assert xa["class_key"] == "contact_a" and "整合性チェック" in xa["note"]
