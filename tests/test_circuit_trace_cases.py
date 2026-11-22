"""成立条件の式・3 値の判定・逆引き・順引きの境目（式の誤り、上限での打ち切り、読めない器具番号など）。"""

from __future__ import annotations

import pytest

from services import circuit_model
from services import circuit_trace as t
from services.circuit_code import CircuitCode
from services.circuit_model import build_circuit
from tests.test_circuit_model import _sym, _wire
from tests.test_circuit_trace import example_circuit


@pytest.fixture(autouse=True)
def builtin(monkeypatch):
    monkeypatch.setattr(circuit_model, "ngspice_path", lambda: None)


def _lits(*names):
    return frozenset((n, "a") for n in names)


def test_factor_extracts_the_most_shared_contact():
    assert t.factor([frozenset()]) == {"const": True}
    tree = t.factor([_lits("A", "X"), _lits("B", "X"), _lits("C")])
    assert t.equivalent(tree, t.parse("X & (A | B) | C"))["equal"]
    assert tree["op"] == "or"
    assert t._and([]) == {"const": True}
    assert t._and([{"const": True}, t._lit("A", "a")]) == t._lit("A", "a")


@pytest.mark.parametrize("text, message", [
    ('"A', "式を読めません"),
    ("(A", "式が途中で終わっています"),
    ("(A B", "括弧が閉じていません"),
    ("&", "位置がおかしい"),
    ("A B", "余分な文字"),
])
def test_parse_reports_where_the_formula_is_broken(text, message):
    with pytest.raises(ValueError, match=message):
        t.parse(text)


def test_negated_groups_constants_and_three_values():
    tree = t.parse("!(A | B) & TRUE & (C | D | (E | F))")
    assert t.to_text(tree) == "¬(A ∨ B) ∧ (C ∨ D ∨ E ∨ F)"
    assert t.to_text(tree, ascii_ops=True) == "!(A | B) & (C | D | E | F)"
    assert t.kleene(t.parse("!(A | B)"), {"A": None, "B": None}.get) is None
    assert t.kleene(t.parse("!(A | B)"), {"A": False, "B": False}.get) is True
    assert t.kleene(t.parse("FALSE | A"), {"A": True}.get) is True


def test_many_variables_are_compared_by_sampling():
    a = t.parse("A & B & C")
    assert t.equivalent(a, t.parse("C & B & A"), max_exact=0, samples=64) == {
        "equal": True, "exhaustive": False, "counterexample": None}
    assert t.equivalent(a, t.parse("A & B"), max_exact=0, samples=64)["equal"] is False


def test_targets_by_location_and_by_confusable_device_number():
    circuit = example_circuit()
    with pytest.raises(ValueError, match="回路にありません"):
        t.resolve_target(circuit, {"project_id": 1, "symbol_ref": "NOPE"})
    c = CircuitCode()
    c.contact_a("A", "P", "n1", device="A", source=(1, "A"))
    c.coil("X", "n1", "0", device="X R100", source=(1, "X"))
    assert t.resolve_target(c.build(), "X R1OO")[0] == "X R100"


def test_push_buttons_and_unreadable_contacts_on_the_drawing():
    symbols = [
        _sym("P", "power_bus", "PER1", terms=1, cy=0.1), _sym("N", "power_bus", "NER1", terms=1, cy=0.9),
        _sym("PB", "push_button", "PB9"), _sym("K", "contact_a", None), _sym("L", "lamp", "RL"),
    ]
    page = {"id": 1, "name": "p", "sheet_no": "ER21001", "image_width": 1000, "image_height": 1000, "symbols": symbols,
            "connections": [_wire(("P", 1), ("PB", 1)), _wire(("PB", 2), ("K", 1)), _wire(("K", 2), ("L", 1)),
                            _wire(("L", 2), ("N", 1))]}
    tracer = t.Tracer(build_circuit([page]), [page])
    push = tracer.sources["PB9"]
    assert push["push_button"] is True and t.source_label(push) == t.PUSH_BUTTON_LABEL
    unreadable = next(v for k, v in tracer.sources.items() if "器具番号なし" in k)
    assert unreadable["source"] == t.UNRESOLVED
    result = tracer.evaluate("RL", {"PB9": True})
    assert result["status"] == t.UNKNOWN
    assert any("接点の器具番号が読めない" in r for r in result["reasons"])


def test_limits_shared_nodes_duplicate_paths_and_contradictions():
    tracer = t.Tracer(example_circuit(), limit=1)
    result = tracer.evaluate("Y", {"A": True, "B": True, "C": True, "D": True})
    assert result["truncated"] and any("1 本で打ち切りました" in r for r in result["reasons"])

    c = CircuitCode()
    c.contact_a("A", "P", "n1", device="A", source=(1, "A"))
    c.coil("Y", "n1", "n2", device="Y", source=(1, "Y"))
    c.contact_a("B", "n2", "n1", device="B", source=(1, "B"))   # コイルと並列の接点: 戻りが上の点を通る
    c.contact_a("C", "n1", "0", device="C", source=(1, "C"))
    c.diode("D1", "P", "m1", source=(1, "D1"))                  # 向きの分からないダイオード 2 本は同じ条件の枝
    c.diode("D2", "P", "m1", source=(1, "D2"))
    c.contact_a("Z1", "m1", "m2", device="Z", source=(1, "Z1"))
    c.contact_b("Z2", "m2", "m3", device="Z", source=(1, "Z2"))  # 同じ器具の a と b が直列
    c.load("L", "m3", "0", source=(1, "L"), label="RL")
    c.contact_a("E", "m1", "m3", device="E", source=(1, "E"))
    tracer = t.Tracer(c.build())
    assert tracer.condition("Y")["branches"] == []
    lamp = tracer.evaluate("RL", {"E": True})
    assert lamp["status"] == t.ON
    assert any("a 接点と b 接点が直列の枝が 1 本" in r for r in lamp["reasons"])
    assert len(lamp["branches"]) == 2


def _chain():
    """入力 A → リレー Y → リレー W → 表示灯。経路の無いリレー Q の接点も表示灯の枝にある。"""
    c = CircuitCode()
    c.contact_a("A", "P", "n1", device="A", source=(1, "A"))
    c.coil("Y", "n1", "0", device="Y", source=(1, "Y"))
    c.contact_a("Ya", "P", "n2", device="Y", source=(1, "Ya"))
    c.coil("W", "n2", "0", device="W", source=(1, "W"))
    c.contact_a("Wa", "P", "n3", device="W", source=(1, "Wa"))
    c.contact_a("Qa", "n3", "n4", device="Q", source=(1, "Qa"))
    c.load("L", "n4", "0", source=(1, "L"), label="RL")
    c.coil("Q", "q1", "q2", device="Q", source=(1, "Q"))
    return t.Tracer(c.build())


def test_depth_limits_and_relays_without_a_path():
    tracer = _chain()
    shallow = tracer.evaluate("RL", {"A": True}, max_depth=0)
    assert shallow["status"] == t.UNKNOWN
    assert any("辿る深さの上限を超えました" in r for r in shallow["reasons"])
    deep = tracer.evaluate("RL", {"A": True})
    assert any("Q のコイルに電源から戻りまでの経路が見つかりません" in r for r in deep["reasons"])
    tree = tracer.explain("RL", max_depth=0)
    assert {"target": "W", "kind": "relay", "note": "深さの上限"} in tree["relays"]
    assert tracer.explain("Q")["note"] == "電源から戻りまでの経路が見つかりません"
    assert tracer.effects("A", max_depth=0)["affected"] == []
    change = tracer.effects("Y", states={"A": True})["state_change"]
    assert change["from"] is True and change["to"] is False
