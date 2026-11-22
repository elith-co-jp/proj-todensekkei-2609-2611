"""展開接続図を読む手順の機械化: 成立条件の逆引き・順引きと 3 値の判定（DD-0035）。"""

import pytest

from analysis.synthetic_circuits import logic
from services import circuit_model
from services import circuit_trace as t
from services.circuit_code import CircuitCode
from services.circuit_model import build_circuit, canonical_device
from tests.test_circuit_model import _sym, _wire, self_holding_page

X = "X R100"


@pytest.fixture(autouse=True)
def builtin(monkeypatch):
    monkeypatch.setattr(circuit_model, "ngspice_path", lambda: None)


def example_circuit():
    """会議資料の簡略例: 電源 → A →［B の枝 または C の枝］→ コイル Y → D → 戻り。"""
    c = CircuitCode("example")
    c.contact_a("A", "P", "n1", device="A", source=(1, "A"))
    c.contact_a("B", "n1", "n2", device="B", source=(1, "B"))
    c.contact_a("C", "n1", "n2", device="C", source=(1, "C"))
    c.coil("Y", "n2", "n3", device="Y", source=(1, "Y"))
    c.contact_a("D", "n3", "0", device="D", source=(1, "D"))
    return c.build()


def test_example_condition_includes_return_side_contact():
    tracer = t.Tracer(example_circuit())
    cond = tracer.condition("Y")
    assert t.equivalent(cond["tree"], t.parse("A & (B | C) & D"))["equal"]
    assert cond["text"] == "A ∧ D ∧ (B ∨ C)"
    assert len(cond["branches"]) == 2
    # D はコイルより下（戻り側）
    sides = {e.symbol_ref: s for b in cond["branches"] for e, s in zip(b.contacts, b.sides, strict=False)}
    assert sides["D"] == t.SIDE_RETURN and sides["A"] == t.SIDE_SUPPLY


@pytest.mark.parametrize("states, expected", [
    ({"A": True, "B": False, "C": True, "D": True}, t.ON),        # C 側の枝で通電。B は閉じなくてよい
    ({"A": True, "B": True, "C": True, "D": False}, t.OFF),       # 戻り側が切れているため不成立
    ({"A": True, "B": True, "C": False, "D": True}, t.ON),        # C が開でも B の枝で成立を維持
    ({"A": True, "B": False, "C": False, "D": True}, t.OFF),
    ({"A": True, "B": False, "C": None, "D": True}, t.UNKNOWN),   # C が不明 → 不成立と断定しない
    ({"A": True, "B": True, "C": None, "D": True}, t.ON),         # C が不明でも B の枝で成立
])
def test_example_three_valued(states, expected):
    r = t.Tracer(example_circuit()).evaluate("Y", states)
    assert r["status"] == expected
    if expected == t.OFF:
        assert all(b["open"] for b in r["branches"])


def test_unknown_reports_reason_and_location():
    r = t.Tracer(example_circuit()).evaluate("Y", {"A": True, "B": False, "D": True})
    assert r["status"] == t.UNKNOWN
    assert any("C" in reason and "外部入力" in reason for reason in r["reasons"])
    branch = next(b for b in r["branches"] if b["status"] == t.UNKNOWN)
    assert branch["unknown"][0]["symbol_ref"] == "C" and branch["unknown"][0]["page"] == "1"


def test_partial_branch_is_not_conduction():
    """枝の一部だけ成立しても通電としない（A・B は導通、D が開）。"""
    r = t.Tracer(example_circuit()).evaluate("Y", {"A": True, "B": True, "C": True, "D": False})
    assert r["status"] == t.OFF
    assert {c["symbol_ref"] for b in r["branches"] for c in b["open"]} == {"D"}


def test_parse_and_render_round_trip():
    tree = t.parse('A ∧ (B ∨ ¬"33HB FV2306") AND NOT D')
    assert t.variables(tree) == {"A", "B", "33HB FV2306", "D"}
    again = t.parse(t.to_text(tree, ascii_ops=True))
    assert t.equivalent(tree, again)["equal"]
    assert not t.equivalent(t.parse("A & B"), t.parse("A | B"))["equal"]
    with pytest.raises(ValueError):
        t.parse("A & (B")


# ------------------------------------------------------------------------------------- 自己保持と繰り返し


def test_self_holding_relay_condition_and_previous_state():
    tracer = t.Tracer(build_circuit([self_holding_page()]), [self_holding_page()])
    cond = tracer.condition(X)
    assert t.equivalent(cond["tree"], t.parse(f'!STOP & (PB1 | "{X}")'))["equal"]
    assert tracer.evaluate(X, {"STOP": False, "PB1": True})["status"] == t.ON
    # 押していない: 前の状態しだい
    assert tracer.evaluate(X, {"STOP": False, "PB1": False})["status"] == t.UNKNOWN
    assert tracer.evaluate(X, {"STOP": False, "PB1": False}, {X: True})["status"] == t.ON
    assert tracer.evaluate(X, {"STOP": True, "PB1": True})["status"] == t.OFF


def test_lamp_follows_relay_recursively():
    pages = [self_holding_page()]
    tracer = t.Tracer(build_circuit(pages), pages)
    r = tracer.evaluate("RL", {"STOP": False, "PB1": True})
    assert r["status"] == t.ON
    assert r["devices"][0]["how"] == "derived"
    tree = tracer.explain("RL")
    assert tree["relays"][0]["target"] == X
    assert {i["device"] for i in tree["relays"][0]["inputs"]} == {"STOP", "PB1"}


def test_effects_forward_with_role_and_state_change():
    pages = [self_holding_page()]
    tracer = t.Tracer(build_circuit(pages), pages)
    out = tracer.effects("PB1", states={"STOP": False, "PB1": False}, energized={})
    names = {a["target"]: a for a in out["affected"]}
    assert names[X]["depth"] == 1 and names[X]["role"].startswith("一部")
    assert names["RL"]["depth"] == 2
    stop = {a["target"]: a for a in tracer.effects("STOP")["affected"]}
    assert stop[X]["role"].startswith("必須")
    changes = {c["target"]: c for c in out["state_change"]["targets"]}
    assert changes[X]["after"] == t.ON


# ------------------------------------------------------------------------------------- 参照先の区別


def _two_sheet_pages(with_second: bool):
    """ER21001 の接点 K1(ER21002) で Y が動く。K1 のコイルは ER21002 にある。"""
    p1 = {"id": 1, "name": "ER21001", "sheet_no": "ER21001", "image_width": 1000, "image_height": 1000,
          "symbols": [_sym("P", "power_bus", terms=1, cy=0.1), _sym("N", "power_bus", terms=1, cy=0.9),
                      _sym("K", "contact_a", "K1 R200 (ER21002)"), _sym("Y", "relay_coil", "Y R300")],
          "connections": [_wire(("P", 1), ("K", 1)), _wire(("K", 2), ("Y", 1)), _wire(("Y", 2), ("N", 1))]}
    p2 = {"id": 2, "name": "ER21002", "sheet_no": "ER21002", "image_width": 1000, "image_height": 1000,
          "symbols": [_sym("P", "power_bus", terms=1, cy=0.1), _sym("N", "power_bus", terms=1, cy=0.9),
                      _sym("PB", "contact_a", "PB9"), _sym("KC", "relay_coil", "K1 R200")],
          "connections": [_wire(("P", 1), ("PB", 1)), _wire(("PB", 2), ("KC", 1)), _wire(("KC", 2), ("N", 1))]}
    return [p1, p2] if with_second else [p1]


def test_missing_reference_is_undecidable():
    pages = _two_sheet_pages(False)
    tracer = t.Tracer(build_circuit(pages), pages)
    assert tracer.sources["K1 R200"]["source"] == t.MISSING_REFERENCE
    r = tracer.evaluate("Y R300", {})
    assert r["status"] == t.UNKNOWN and "ER21002" in r["reasons"][0]
    # 参照先を受け取れば、リレーとして辿れる
    pages = _two_sheet_pages(True)
    tracer = t.Tracer(build_circuit(pages), pages)
    assert tracer.sources["K1 R200"]["source"] == t.RELAY
    assert tracer.evaluate("Y R300", {"PB9": True})["status"] == t.ON
    assert tracer.evaluate("Y R300", {"PB9": False})["status"] == t.OFF


def test_reference_present_but_coil_missing_is_unresolved():
    pages = _two_sheet_pages(True)
    pages[1]["symbols"][3]["label"] = "K7 R200"
    tracer = t.Tracer(build_circuit(pages), pages)
    assert tracer.sources["K1 R200"]["source"] == t.UNRESOLVED


def test_external_io_attribute_marks_input():
    pages = _two_sheet_pages(False)
    pages[0]["symbols"][2]["attributes"] = {"external_io": "yes"}
    tracer = t.Tracer(build_circuit(pages), pages)
    assert tracer.sources["K1 R200"]["source"] == t.INPUT
    assert tracer.evaluate("Y R300", {"K1 R200": True})["status"] == t.ON


def test_no_path_is_undecidable():
    page = self_holding_page()
    page["connections"] = [c for c in page["connections"] if not (c["from_symbol_ref"] == "XC" and c["to_symbol_ref"] == "N")]
    tracer = t.Tracer(build_circuit([page]), [page])
    r = tracer.evaluate(X, {"STOP": False, "PB1": True})
    assert r["status"] == t.UNKNOWN and "経路が見つかりません" in r["reasons"][0]


# ------------------------------------------------------------------------------------- 合成回路で検証


def _spec_condition(rungs, load):
    terms = []
    for r in rungs:
        if r.load[1] != load:
            continue
        terms.append(" & ".join("(" + " | ".join(("!" if k == "b" else "") + f'"{d}"' for d, k in term) + ")"
                                for term in r.terms))
    return t.parse(" | ".join(f"({x})" for x in terms))


@pytest.mark.parametrize("seed", range(8))
def test_conditions_match_synthetic_truth(seed):
    """正解の論理（段ごとの直列の項・並列の接点）と、図面から求めた成立条件が同じ論理になる。"""
    spec = logic.make_spec(seed)
    data = logic.build(spec)
    pages = data["pages"]
    circuit = build_circuit(pages, data["bus_texts"])
    tracer = t.Tracer(circuit, pages)
    for load in {r.load[1] for r in spec.rungs}:
        cond = tracer.condition(load)
        mine = t.rename(cond["tree"], canonical_device)
        truth = t.rename(_spec_condition(spec.rungs, load), canonical_device)
        assert t.equivalent(mine, truth)["equal"], (seed, load, cond["text"])


# ------------------------------------------------------------------------------------- 代表回路の正解・対応表

from services import annotation_table, circuit_scenarios  # noqa: E402


def test_scenarios_compare_condition_cases_and_effects():
    pages = [self_holding_page()]
    circuit = build_circuit(pages)
    scenarios = [
        {"id": "S1", "question": "起動条件", "target": "X", "condition": '!STOP & (PB1 | X)',
         "cases": [{"states": {"STOP": False, "PB1": True}, "expected": "成立"},
                   {"states": {"STOP": True, "PB1": True}, "expected": "不成立"},
                   {"states": {"STOP": False, "PB1": False}, "expected": "判定不能"}]},
        {"id": "S2", "question": "起動条件", "target": "X", "condition": "PB1 & !STOP"},
        {"id": "S3", "question": "影響", "origin": "STOP", "affected": ["X", "RL"]},
    ]
    out = circuit_scenarios.evaluate(circuit, pages, scenarios)
    rows = {r["id"]: r for r in out["scenarios"]}
    assert rows["S1"]["ok"], rows["S1"]
    assert not rows["S2"]["ok"]
    assert rows["S2"]["checks"][0]["counterexample"] is not None
    assert rows["S3"]["ok"]
    assert out["passed"] == 2 and out["by_check"]["case"] == {"ok": 3, "total": 3}


def test_scenarios_from_yaml_and_table_rows():
    drawing_set, scenarios = circuit_scenarios.load("drawing_set: DS01\nscenarios:\n  - id: S1\n    target: Y\n")
    assert drawing_set == "DS01" and scenarios[0]["target"] == "Y"
    parsed = annotation_table.scenarios_from_rows(annotation_table.scenario_template())
    s1 = next(s for s in parsed if s["id"] == "S1")
    assert len(s1["cases"]) == 3 and s1["cases"][2]["states"]["C"] is None
    out = circuit_scenarios.evaluate(example_circuit(), None, parsed)
    # 架空の例のページ P03 は図面に無いので、ページの照合だけ外れる
    assert {k: v["ok"] == v["total"] for k, v in out["by_check"].items()} == {
        "condition": True, "case": True, "pages": False, "affected": True}


def test_annotation_table_round_trip_and_compare():
    pages = [self_holding_page()]
    circuit = build_circuit(pages)
    rows = annotation_table.export_rows(pages, circuit, drawing_set="DS01")
    kinds = {r["種別"] for r in rows}
    assert {"配線", "コイルと接点の対応"} <= kinds
    coil_rows = [r for r in rows if r["種別"] == "コイルと接点の対応" and r["判定"] == "対応"]
    assert {r["要素ID"] for r in coil_rows} == {"test:XA", "test:XL"}
    blob = annotation_table.to_xlsx(rows)
    back = annotation_table.read_table(blob, "a.xlsx")
    assert len(back["対応表"]) == len(rows) and len(back["代表回路"]) == 4
    assert annotation_table.read_table(annotation_table.to_csv(rows), "a.csv")["対応表"][0]["要素ID"] == rows[0]["要素ID"]
    # 正解: 1 本の配線は実は交差（非接続）、ラベルの 1 つは読み違い
    gold = [dict(r) for r in rows]
    wire = next(r for r in gold if r["種別"] == "配線")
    wire["判定"] = "非接続"
    next(r for r in gold if r["要素ID"] == "test:PB1")["原文"] = "PB2"
    score = annotation_table.compare(gold, rows)
    assert score["配線"]["wrong_connections"] == 1 and score["配線"]["recall"] == 1.0
    assert score["配線"]["precision"] < 1.0
    assert score["コイルと接点の対応"]["precision"] == 1.0
    assert score["原文"]["exact"] == score["原文"]["compared"] - 1


def test_validation_tool_on_synthetic_circuits():
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "analysis/tools"))
    from validate_reading_procedure import check_seed, summarize

    s = summarize([check_seed(seed, trials=3) for seed in range(3)])
    assert s["condition_ok"] == s["loads"] and s["decided_ok"] == s["decided"]
    assert s["unknown_wrong"] == 0 and s["missing_wrong"] == 0 and s["effects_ok"] == s["effects"]


def test_scenarios_with_unknown_names_string_states_and_pages_without_cases():
    pages = [self_holding_page()]
    circuit = build_circuit(pages)
    scenarios = [
        {"id": "F", "question": "影響", "origin": "NOPE", "affected": []},
        {"id": "A", "question": "影響", "origin": "STOP", "affected": ["X", "GHOST"]},
        {"id": "B", "target": "NOPE2"},
        {"id": "S", "target": "X", "cases": [{"states": {"STOP": "0", "PB1": "動作"}, "expected": "成立"}]},
        {"id": "P", "target": "X", "pages": ["1"]},
    ]
    rows = {r["id"]: r for r in circuit_scenarios.evaluate(circuit, pages, scenarios)["scenarios"]}
    assert rows["F"]["checks"][0]["check"] == "origin_found" and not rows["F"]["ok"]
    assert rows["A"]["unknown_names"] == ["GHOST"]
    assert rows["B"]["checks"][0]["check"] == "target_found"
    assert rows["S"]["ok"], rows["S"]
    assert rows["P"]["checks"][0]["check"] == "pages"
    assert circuit_scenarios.load("- id: S1\n  target: X\n") == (None, [{"id": "S1", "target": "X"}])
    with pytest.raises(ValueError, match="scenarios の配列"):
        circuit_scenarios.load("drawing_set: DS01\n")
