"""図面から組んだ回路の Lcapy・SKiDL への変換（DD-0028）。

文字列の変換はライブラリ無しで確かめる。lcapy / skidl が入っている環境では、読み込んで解いた結果・ネットも照合する。
"""

import re
import subprocess
import sys

import pytest

from services import circuit_interop as ci
from services import circuit_model
from services.circuit_model import build_circuit, simulate
from tests.test_circuit_api import _save
from tests.test_circuit_model import self_holding_page

CASES = [({}, {}), ({"PB1": True}, {}), ({}, {"X R100": True}), ({"STOP": True}, {"X R100": True})]


@pytest.fixture
def page():
    return self_holding_page()


@pytest.fixture
def circuit(page):
    return build_circuit([page])


def test_part_refs_follow_symbol_classes(circuit, page):
    refs = {p.element.symbol_ref: p.ref for p in ci.part_list(circuit, [page])}
    assert refs == {"L": "H1", "PB1": "S1", "STOP": "S2", "XA": "S3", "XC": "K1", "XL": "S4"}


def test_lcapy_netlist_topology_uses_switches(circuit, page):
    text = ci.lcapy_netlist(circuit, pages=[page])
    assert "V1 P 0 dc 110" in text
    assert "SW2 P N001 nc" in text  # STOP は b 接点
    assert re.search(r"^SW1 N001 N002 no$", text, re.MULTILINE)  # PB1 は a 接点
    assert re.search(r"^RK1 N002 0 1000$", text, re.MULTILINE)
    assert "Rleak" in text


def test_lcapy_netlist_fixed_state_uses_wires_and_opens(circuit, page):
    closed = ci.contact_states(circuit, {"PB1": True}, {})
    text = ci.lcapy_netlist(circuit, closed, pages=[page])
    assert "W N001 N002" in text and "W P N001" in text  # PB1 閉、STOP（b 接点・無励磁）閉
    assert "O P N003" in text  # X の a 接点はまだ開
    assert "SW" not in text.replace("# ", "")


@pytest.mark.parametrize(("inputs", "energized"), CASES)
def test_lcapy_solution_matches_builtin_simulation(circuit, monkeypatch, inputs, energized):
    pytest.importorskip("lcapy")
    monkeypatch.setattr(circuit_model, "ngspice_path", lambda: None)
    result = simulate(circuit, inputs, energized)
    state = {dev: c["energized"] for dev, c in result["coils"].items()}
    volts = ci.solve_lcapy(circuit, ci.contact_states(circuit, inputs, state))
    half = circuit_model.SUPPLY_V / 2
    # 浮いたノードの電圧は内蔵の解き方（開いた接点も 1 GΩ の抵抗）と Lcapy（開放）で違うため、生きているかで比べる。
    assert {n: v >= half for n, v in volts.items()} == {n: v >= half for n, v in result["nodes"].items()}


def test_lcapy_reads_topology_netlist(circuit):
    pytest.importorskip("lcapy")
    cct = ci.to_lcapy(circuit)
    normal = cct.replace_switches_before(0)  # 無励磁: a 接点は開、b 接点は閉
    assert float(normal["N001"].V.dc.sympy) == pytest.approx(110)
    assert float(normal["N002"].V.dc.sympy) == pytest.approx(0, abs=1e-3)


def _nets(circuit, page):
    """変換前の回路のネット: ノード名 → {(参照記号, 端子)}。"""
    nets: dict[str, set] = {}
    for p in ci.part_list(circuit, [page]):
        for node, pin in ((p.element.a, "1"), (p.element.b, "2")):
            nets.setdefault("GND" if node == "0" else node, set()).add((p.ref, pin))
    return nets


def _kicad_nets(text):
    nets = {}
    for block in text.split("(net\n")[1:]:
        name = re.search(r'\(name "([^"]+)"\)', block).group(1)
        nets[name] = set(re.findall(r'\(ref "([^"]+)"\)\s*\(pin "([^"]+)"\)', block))
    return nets


def test_skidl_kicad_netlist_matches_circuit(circuit, page):
    pytest.importorskip("skidl")
    text = ci.kicad_netlist(circuit, pages=[page])
    assert _kicad_nets(text) == _nets(circuit, page)
    assert '(value "X R100")' in text and '(name "Device") "X R100"' in text


def test_skidl_script_runs_and_writes_same_netlist(circuit, page, tmp_path):
    pytest.importorskip("skidl")
    script = tmp_path / "circuit_skidl.py"
    script.write_text(ci.skidl_script(circuit, pages=[page]), encoding="utf-8")
    subprocess.run([sys.executable, str(script)], cwd=tmp_path, check=True, capture_output=True, timeout=120)
    assert _kicad_nets((tmp_path / "circuit.net").read_text(encoding="utf-8")) == _nets(circuit, page)


def test_skidl_script_is_valid_python_without_skidl(circuit, page):
    compile(ci.skidl_script(circuit, pages=[page]), "circuit_skidl.py", "exec")


def test_lcapy_and_skidl_api_downloads(client):
    pid = _save(client, self_holding_page())
    res = client.post("/api/circuit/lcapy", json={"project_ids": [pid]})
    assert res.status_code == 200 and "attachment" in res.headers["content-disposition"]
    assert " nc" in res.text and " no" in res.text
    fixed = client.post("/api/circuit/lcapy", json={"project_ids": [pid], "inputs": {"PB1": True}, "fixed": True})
    assert "\nW " in fixed.text and "\nO " in fixed.text
    res = client.post("/api/circuit/skidl", json={"project_ids": [pid]})
    assert res.status_code == 200 and "generate_netlist" in res.text
    assert client.post("/api/circuit/skidl", json={"project_ids": [9999]}).status_code == 400
