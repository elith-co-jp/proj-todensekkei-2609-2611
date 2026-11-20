"""高速化した処理が、元の処理と 1 ビットまで同じ結果を返すこと。"""

import copy
import random

import numpy as np
import pytest

from analysis.synthetic_circuits import logic
from services import circuit_model
from services.circuit_model import _MnaPlan, build_circuit, netlist, solve


@pytest.fixture(autouse=True)
def builtin(monkeypatch):
    monkeypatch.setattr(circuit_model, "ngspice_path", lambda: None)


@pytest.mark.parametrize("seed", range(6))
def test_mna_plan_solves_exactly_like_the_netlist_text(seed):
    for timers in (False, True):
        data = logic.build(logic.make_spec(seed, timers=timers))
        circuit = build_circuit(copy.deepcopy(data["pages"]), data["bus_texts"])
        plan = _MnaPlan.build(circuit)
        assert plan is not None
        rng = random.Random(seed)
        contacts = [e.name for e in circuit.elements if e.kind in ("a", "b")]
        for _ in range(20):
            closed = {name: rng.random() < 0.5 for name in contacts}
            volts, current, _engine = solve(netlist(circuit, closed))
            assert plan.solve(closed) == (volts, current)


def test_mna_plan_leaves_unusual_names_to_the_netlist_text():
    data = logic.build(logic.make_spec(0))
    circuit = build_circuit(copy.deepcopy(data["pages"]), data["bus_texts"])
    circuit.elements[0].label = "改行\nを含む"
    # 文字にすると行の分かれ方が変わる回路は、従来どおり文字を経由して解く（同じ結果になる保証ができないため）。
    assert _MnaPlan.build(circuit) is None


def _scan_by_pixels(mask, orientation, min_length):
    """高速化する前の 1 画素ずつの走査。"""
    out = []
    lines = mask if orientation == "h" else mask.T
    for i, row in enumerate(lines):
        x = 0
        while x < len(row):
            while x < len(row) and row[x] == 0:
                x += 1
            start = x
            while x < len(row) and row[x] != 0:
                x += 1
            if x - start >= min_length:
                out.append((i, start, x))
    return out


def test_runs_match_the_pixel_scan():
    pytest.importorskip("cv2")
    from analysis.wiring.detect import _runs

    rng = np.random.default_rng(0)
    for _ in range(200):
        h, w = rng.integers(1, 80, 2)
        mask = ((rng.random((h, w)) < rng.random()) * 255).astype(np.uint8)
        for orientation in ("h", "v"):
            min_length = int(rng.integers(1, 20))
            assert _runs(mask if orientation == "h" else mask.T, min_length) == _scan_by_pixels(mask, orientation, min_length)
