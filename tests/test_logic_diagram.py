"""展開接続図から論理図を作り、論理図でシミュレーションする（DD-0045）。紙芝居の EV31031〜EV31036 を正解にする。"""

import pytest

from analysis.synthetic_circuits import kamishibai
from services import circuit_model
from services import circuit_trace as t
from services import logic_diagram as L
from services.circuit_code import CircuitCode
from services.circuit_model import build_circuit, simulate
from tests.conftest import create_project
from tests.test_circuit_model import self_holding_page

A_IN = "D009 B413"        # 主制御装置選択指令
SAM_CPU = "D008 B662"     # 定検保守装置 CPU 正常
START_DDC, STOP_DDC = "D001 B405", "D002 B406"
START_SAM, STOP_SAM = "D001 B855", "D002 B856"
LOCK_DDC, UNLOCK_DDC = "D003 B407", "D004 B408"
LOCK_SAM, UNLOCK_SAM = "D003 B857", "D004 B858"
X6, X5, LK, MB, M83 = "6X P33P22", "5X P33P22", "43LK P33P22", "20MB P33P22", "83M P33P22"


@pytest.fixture(autouse=True)
def builtin(monkeypatch):
    monkeypatch.setattr(circuit_model, "ngspice_path", lambda: None)


@pytest.fixture(scope="module")
def drawing():
    data = kamishibai.build()
    circuit = build_circuit(data["pages"], data["bus_texts"])
    diagram = L.build_logic(circuit, data["pages"], targets=data["outputs"], title=data["title"])
    return data, circuit, diagram


def _as_trace(tree, d: L.LogicDiagram):
    """信号の式 → circuit_trace の式（比べるため）。英字のある信号は英字、ほかは器具番号。"""
    if "const" in tree:
        return tree
    if "sig" in tree:
        s = d.signals[tree["sig"]]
        return {"lit": s.letter or tree["sig"], "kind": "b" if tree.get("neg") else "a"}
    return {"op": tree["op"], "args": [_as_trace(a, d) for a in tree["args"]]}


def _same(tree, d, text):
    return t.equivalent(_as_trace(tree, d), t.parse(text))["equal"]


# ------------------------------------------------------------------------------------------- 図面の読み取り → 論理図


def test_drawing_reads_as_circuit_with_latch_timer_and_pev_bus(drawing):
    _data, circuit, _d = drawing
    assert "P_110500" in circuit.nodes and not any(d["kind"] == "bus_guessed" for d in circuit.diagnostics)
    assert circuit.timers == {MB: {"mode": "on_delay", "delay_s": 1.0}}
    assert len(circuit.latches[LK]["set"]) == 1 and len(circuit.latches[LK]["reset"]) == 1
    # 出力の候補は、接点が M/C へ出ていく 6X・5X（増設の予備のリレー 83DX-2 などは外す）
    assert L.default_targets(circuit) == [X5, X6]


def test_logic_matches_last_page_of_kamishibai(drawing):
    """紙芝居の最終ページ: A=主制御装置選択、B=¬A∧定検保守CPU正常、C=引ロック（S/R）、D=1 秒遅延(¬(A∨B))。"""
    _data, _circuit, d = drawing
    letters = {s.letter: sig for sig, s in d.signals.items() if s.letter}
    assert sorted(letters) == ["A", "B", "C", "D"]
    a, b, c, dd = (d.signals[letters[k]] for k in "ABCD")
    # A: 主制御装置選択指令 D009 → 83D → 83DX・83DX-1（接点 1 つのリレーは別名、同じ条件のリレーは 1 つ）
    assert a.id == A_IN and a.kind == "input"
    assert {"83D P33P22", "83DX P33P22", "83DX-1 P33P22"} <= set(a.devices)
    assert a.name == "主制御装置選択指令" and a.ref_name == "主制御装置選択"
    # B: 83M = ¬83DX ∧ 30M（30M = D008）。83M-1 も同じ信号
    assert b.id == M83 and "83M-1 P33P22" in b.devices and _same(b.expr, d, f'!A & "{SAM_CPU}"')
    # C: 43LK キープリレー
    assert c.id == LK and c.kind == "latch"
    assert _same(c.set_expr, d, f'(A & "{LOCK_DDC}") | (B & "{LOCK_SAM}")')
    assert _same(c.reset_expr, d, f'(A & "{UNLOCK_DDC}") | (B & "{UNLOCK_SAM}")')
    # D: 20MB（1 秒）→ 86T（制御装置故障によるトリップ）
    assert dd.id == MB and dd.kind == "timer" and dd.timer == {"mode": "on_delay", "delay_s": 1.0}
    assert "86T P33P22" in dd.devices and dd.name == "制御装置故障によるトリップ"
    assert _same(dd.expr, d, "!(A | B)")
    # 起動指令 6X・停止指令 5X
    assert d.outputs == [X6, X5]
    assert _same(d.signals[X6].expr, d, f'((A & "{START_DDC}") | (B & "{START_SAM}")) & !C')
    assert _same(d.signals[X5].expr, d, f'(A & "{STOP_DDC}") | (B & "{STOP_SAM}") | C | D | !"80 P33P22"')
    # 80（電源正常）はコイルが母線に直接つながるので「電源」の入力（初期値 ON）
    assert d.signals["80 P33P22"].kind == "power" and d.signals["80 P33P22"].default is True


def test_unmerged_keeps_every_relay(drawing):
    data, circuit, _d = drawing
    raw = L.build_logic(circuit, data["pages"], targets=data["outputs"], merge=False)
    assert {"83D P33P22", "83DX P33P22", "83DX-1 P33P22", "86T P33P22"} <= set(raw.signals)
    # まとめなくても、まとめた論理図と同じ動きをする
    ev = [{"at": 1, "inputs": {A_IN: True, START_DDC: True}}]
    merged = L.simulate(L.build_logic(circuit, data["pages"], targets=data["outputs"]), ev)
    plain = L.simulate(raw, ev)
    assert merged["timeline"][-1]["values"][X6] is plain["timeline"][-1]["values"][X6] is True


def test_svg_has_kamishibai_symbols_and_highlight(drawing):
    _data, _circuit, d = drawing
    plain = L.render_svg(d)
    assert plain.startswith("<svg") and "22号 LNG高圧ポンプ IBD" in plain
    for text in ("主制御装置選択指令", "定検保守装置選択", "引ロック", "制御装置故障によるトリップ", "1sec", "起動指令", "停止指令",
                 ">S<", ">R<"):
        assert text in plain
    assert L.HIGHLIGHT not in plain
    on = L.solve(d, {A_IN: True, START_DDC: True})
    assert on["values"][X6] is True
    lit = L.render_svg(d, on["values"])
    assert L.HIGHLIGHT in lit
    text = L.formulas_text(d)
    assert "A 主制御装置選択指令" in text and "S = " in text and "1sec 遅延" in text


# ------------------------------------------------------------------------------------------- シミュレーション


SCENARIO = [
    {"at": 2, "inputs": {A_IN: True}, "label": "主制御装置を選択"},
    {"at": 3, "inputs": {START_DDC: True}},
    {"at": 4, "inputs": {START_DDC: False, LOCK_DDC: True}},
    {"at": 5, "inputs": {LOCK_DDC: False}},
    {"at": 6, "inputs": {START_DDC: True}},
    {"at": 7, "inputs": {UNLOCK_DDC: True}},
    {"at": 8, "inputs": {UNLOCK_DDC: False, A_IN: False, SAM_CPU: True}},
    {"at": 8.5, "inputs": {START_DDC: False, START_SAM: True}},
    {"at": 9, "inputs": {SAM_CPU: False}},
]


def _state(result, t_):
    return L._at(result["timeline"], t_)["values"]


def test_simulation_follows_the_sequence(drawing):
    _data, _circuit, d = drawing
    r = L.simulate(d, SCENARIO, until=12)
    s = lambda t_: _state(r, t_)  # noqa: E731
    # どちらの制御装置も選ばれていない → 1 秒後にトリップ（停止）
    assert s(0.5)[MB] is False and s(1.0)[MB] is True and s(1.0)[X5] is True
    # 主制御装置を選ぶとトリップは解け、個別制御装置の起動指令で起動
    assert s(2)[X5] is False and s(3)[X6] is True
    # 引きロックで停止し、指令を戻しても保持（キープリレー）、起動指令を入れても起動しない
    assert s(4)[LK] is True and s(4)[X5] is True and s(5)[LK] is True and s(6)[X6] is False
    # 解除で起動
    assert s(7)[LK] is False and s(7)[X6] is True
    # 定検保守装置に切り替え: 個別の起動指令では起動せず、定検/保守装置の起動指令で起動
    assert s(8)[M83] is True and s(8)[X6] is False and s(8.5)[X6] is True
    # 定検保守装置の CPU 異常 → 選択が外れ、1 秒後にトリップ
    assert s(9)[M83] is False and s(9.5)[MB] is False and s(10)[MB] is True and s(10)[X5] is True
    assert any("時間が経過" in snap["label"] for snap in r["timeline"])
    assert r["waves"][X6][0] == [0.0, False]


def test_simulation_agrees_with_circuit_calculation(drawing):
    _data, circuit, d = drawing
    cmp = L.compare_with_circuit(circuit, d, SCENARIO, until=12)
    assert cmp["agree"], cmp["mismatches"]
    assert cmp["checked"] > 100


def test_power_loss_stops_the_pump(drawing):
    _data, _circuit, d = drawing
    r = L.simulate(d, [{"at": 1, "inputs": {A_IN: True}}, {"at": 2, "inputs": {"80": False}}], until=3)
    assert _state(r, 1.5)[X5] is False and _state(r, 2)[X5] is True


def test_events_must_target_inputs(drawing):
    _data, _circuit, d = drawing
    with pytest.raises(ValueError):
        L.simulate(d, [{"at": 1, "inputs": {"6X": True}}])
    with pytest.raises(ValueError):
        L.simulate(d, [{"at": 1, "inputs": {"存在しない": True}}])


def test_missing_sheet_marks_inputs_as_missing_reference():
    """EV31033（83M・20MB・86T）を受け取っていないと、その接点は「参照先欠落」の入力（点線の六角形）。"""
    data = kamishibai.build(sheets=["EV31032", "EV31034", "EV31035", "EV31036"])
    circuit = build_circuit(data["pages"], data["bus_texts"])
    d = L.build_logic(circuit, data["pages"], targets=data["outputs"])
    missing = {s.id for s in d.signals.values() if s.source == t.MISSING_REFERENCE}
    assert {"83M-1 P33P22", "86T P33P22"} <= missing
    assert any(x["kind"] == t.MISSING_REFERENCE for x in d.diagnostics)
    assert 'stroke-dasharray="4 3"' in L.render_svg(d)


# ------------------------------------------------------------------------------------------- キープリレー・自己保持


def _latch_circuit():
    c = CircuitCode("latch")
    c.contact_a("S", "P", "n1", device="SET", source=(1, "S"))
    c.coil("LO", "n1", "0", device="K", source=(1, "LO"), latch="set")
    c.contact_a("R", "P", "n2", device="RST", source=(1, "R"))
    c.coil("LR", "n2", "0", device="K", source=(1, "LR"), latch="reset")
    c.contact_a("KA", "P", "n3", device="K", source=(1, "KA"))
    c.load("L", "n3", "0", source=(1, "L"), label="RL")
    return c.build()


def test_latch_relay_in_circuit_simulation():
    circuit = _latch_circuit()
    assert simulate(circuit, {"SET": True})["coils"]["K"]["energized"] is True
    held = simulate(circuit, {}, {"K": True})
    lamp = next(x for x in held["loads"] if x["symbol_ref"] == "L")
    assert held["coils"]["K"]["energized"] is True and lamp["energized"] is True
    assert simulate(circuit, {"RST": True}, {"K": True})["coils"]["K"]["energized"] is False
    # 両方が励磁したらリセット優先（既定）
    assert simulate(circuit, {"SET": True, "RST": True}, {"K": True})["coils"]["K"]["energized"] is False


def test_latch_relay_in_trace_three_valued():
    tracer = t.Tracer(_latch_circuit())
    lc = tracer.latch_condition("K")
    assert lc["set"]["formula"] == "SET" and lc["reset"]["formula"] == "RST"
    assert tracer.evaluate("RL", {"SET": True, "RST": False})["status"] == t.ON
    assert tracer.evaluate("RL", {"SET": False, "RST": False}, {"K": True})["status"] == t.ON
    assert tracer.evaluate("RL", {"SET": False, "RST": False})["status"] == t.UNKNOWN  # 前の状態が分からない
    assert tracer.evaluate("RL", {"SET": None, "RST": True})["status"] == t.OFF


def test_latch_attribute_from_annotation():
    page = {"id": 1, "name": "x", "image_width": 1000, "image_height": 1000, "symbols": [], "connections": []}
    from tests.test_circuit_model import _sym, _wire
    page["symbols"] = [_sym("P", "power_bus", terms=1, cy=0.1), _sym("N", "power_bus", terms=1, cy=0.9),
                       _sym("S", "contact_a", "SET"), _sym("R", "contact_a", "RST"),
                       {**_sym("KO", "relay_coil", "43LK P33P22"), "attributes": {"latch_coil": "O"}},
                       {**_sym("KR", "relay_coil", "43LK P33P22"), "attributes": {"latch_coil": "R"}}]
    page["connections"] = [_wire(("P", 1), ("S", 1)), _wire(("S", 2), ("KO", 1)), _wire(("KO", 2), ("N", 1)),
                           _wire(("P", 1), ("R", 1)), _wire(("R", 2), ("KR", 1)), _wire(("KR", 2), ("N", 1))]
    circuit = build_circuit([page])
    assert circuit.latches["43LK P33P22"]["set"] == ["R_1_KO"] and circuit.latches["43LK P33P22"]["reset"] == ["R_1_KR"]
    assert simulate(circuit, {"SET": True})["coils"]["43LK P33P22"]["energized"] is True


def test_self_holding_becomes_connector_and_holds():
    circuit = build_circuit([self_holding_page()])
    d = L.build_logic(circuit, targets=["X"])
    x = d.signals["X R100"]
    assert x.letter == "A" and _same(x.expr, d, "!STOP & (PB1 | A)")
    r = L.simulate(d, [{"at": 1, "inputs": {"PB1": True}}, {"at": 2, "inputs": {"PB1": False}},
                       {"at": 3, "inputs": {"STOP": True}}])
    assert _state(r, 1)["X R100"] is True and _state(r, 2)["X R100"] is True and _state(r, 3)["X R100"] is False
    assert L.compare_with_circuit(circuit, d, [{"at": 1, "inputs": {"PB1": True}}, {"at": 2, "inputs": {"PB1": False}},
                                               {"at": 3, "inputs": {"STOP": True}}])["agree"]


def test_code_round_trip_keeps_latch():
    from services.circuit_code import from_python, to_python
    again = from_python(to_python(_latch_circuit()))
    assert again.latches["K"]["set"] and again.latches["K"]["reset"]


# ------------------------------------------------------------------------------------------- API


def _save(client, page):
    pid = create_project(client, f"{page['sheet_no']}.png", 1755, 1241)
    symbols = [{k: s[k] for k in ("ref", "class_key", "label", "cx", "cy", "w", "h", "attributes")}
               | {"terminals": [{k: x[k] for k in ("ref", "name", "tx", "ty")} for x in s["terminals"]]}
               for s in page["symbols"]]
    res = client.put(f"/api/projects/{pid}/annotations", json={"symbols": symbols, "connections": page["connections"]})
    assert res.status_code == 200, res.text
    return pid


def test_logic_api(client):
    data = kamishibai.build()
    ids = [_save(client, p) for p in data["pages"]]
    res = client.post("/api/circuit/logic", json={"project_ids": ids})
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["outputs"] == [X5, X6] and body["default_targets"] == [X5, X6]
    assert {b["letter"] for b in body["blocks"] if b["letter"]} == {"A", "B", "C", "D"}
    assert body["svg"].startswith("<svg") and "主制御装置選択" in body["svg"]
    on = client.post("/api/circuit/logic", json={"project_ids": ids, "inputs": {A_IN: True, START_DDC: True}}).json()
    assert on["values"][X6] is True and L.HIGHLIGHT in on["svg"]

    sim = client.post("/api/circuit/logic/simulate", json={"project_ids": ids, "events": SCENARIO, "until": 12,
                                                          "compare": True}).json()
    assert sim["compare"]["agree"] and sim["names"][A_IN] == "主制御装置選択" and sim["letters"][LK] == "C"
    svg = client.post("/api/circuit/logic/svg", json={"project_ids": ids, "values": sim["timeline"][-1]["values"]})
    assert svg.status_code == 200 and svg.headers["content-type"].startswith("image/svg+xml")
    bad = client.post("/api/circuit/logic", json={"project_ids": ids, "targets": ["ZZZ"]})
    assert bad.status_code == 400


# ------------------------------------------------------------------------------------------- デモ（DD-0046）


def _as_structure(page, texts):
    """登録した記号・端子・配線を、AI 解析（構造解析）の結果の形にする。PDF を登録して解析しただけの状態の代わり。"""
    W, H = page["image_width"], page["image_height"]
    symbols = []
    for i, s in enumerate(page["symbols"]):
        x0, y0 = (s["cx"] - s["w"] / 2) * W, (s["cy"] - s["h"] / 2) * H
        symbols.append({
            "id": f"symbol_{i:04d}", "class_name": s["class_key"], "label": s["label"], "confidence": 0.9,
            "bbox": {"x0": x0, "y0": y0, "x1": x0 + s["w"] * W, "y1": y0 + s["h"] * H},
            "terminals": [{"ref": t["ref"], "name": t["name"], "point": [t["tx"] * W, t["ty"] * H]} for t in s["terminals"]],
        })
    sid = {s["ref"]: f"symbol_{i:04d}" for i, s in enumerate(page["symbols"])}
    connections = [{"type": "wire", "from": {"symbol_id": sid[c["from_symbol_ref"]], "terminal_ref": c["from_terminal_ref"]},
                    "to": {"symbol_id": sid[c["to_symbol_ref"]], "terminal_ref": c["to_terminal_ref"]}}
                   for c in page["connections"]]
    return {"status": "completed", "image_size": {"width": W, "height": H}, "symbols": symbols, "wires": [],
            "texts": texts, "from_to": {"connection_count": len(connections), "connections": connections}, "warnings": []}


def test_demo_api_links_logic_to_drawing(client):
    data = kamishibai.build()
    ids = [_save(client, p) for p in data["pages"]]
    off = client.post("/api/circuit/demo", json={"project_ids": ids}).json()
    assert off["ready"] and {p["source"] for p in off["pages"]} == {"annotation"}
    logic = off["logic"]
    assert logic["values"][X6] is False and f'data-sig="{X6}"' in logic["svg"] and f'data-sig="{A_IN}"' in logic["svg"]
    # 出力の 6X はコイルと接点、入力は図面の上の接点の位置を持つ
    roles = {loc["role"] for loc in logic["locations"][X6]}
    assert roles == {"coil", "contact"}
    assert all(0 < loc["cx"] < 1 and loc["project_id"] in ids for loc in logic["locations"][A_IN])
    # スイッチを入れると、図面の上の線と論理図の線が同じように通じる
    on = client.post("/api/circuit/demo", json={"project_ids": ids, "inputs": {A_IN: True, START_DDC: True}}).json()
    assert on["simulation"]["coil_states"][X6]["energized"] is True and on["logic"]["values"][X6] is True
    live = sum(w["live"] for o in on["simulation"]["overlay"] for w in o["wires"])
    assert live > sum(w["live"] for o in off["simulation"]["overlay"] for w in o["wires"])


def test_demo_api_builds_from_ai_analysis_only(client):
    """記号を登録していない図面（PDF を登録して AI 解析しただけ）でも、AI の結果で回路と論理図を組んで動かせる。"""
    import json

    import database
    from models import StructurePrediction

    data = kamishibai.build()
    ids = []
    for page in data["pages"]:
        pid = create_project(client, f"{page['sheet_no']}.png", page["image_width"], page["image_height"])
        with database.SessionLocal() as db:
            db.add(StructurePrediction(project_id=pid, payload_json=json.dumps(_as_structure(page, data["bus_texts"][page["id"]]))))
            db.commit()
        ids.append(pid)
    body = client.post("/api/circuit/demo", json={"project_ids": ids, "inputs": {A_IN: True, START_DDC: True}}).json()
    assert body["ready"] and {p["source"] for p in body["pages"]} == {"ai"}
    assert all(p["symbols"] > 0 for p in body["pages"])
    assert body["simulation"]["coil_states"][X6]["energized"] is True
    assert body["logic"]["values"][X6] is True and body["logic"]["locations"][X6]
    # 何も無い図面は、AI 解析を促す
    empty = create_project(client, "empty.png", 100, 100)
    none = client.post("/api/circuit/demo", json={"project_ids": [empty]}).json()
    assert none["ready"] is False and none["pages"][0]["source"] == "none"


def test_deps_trace_upstream_from_6x(drawing):
    """注目点（6X）から上流へ辿ると、紙芝居と同じく A・B・C と起動指令の入力に行き着く（DD-0047）。"""
    _data, _circuit, d = drawing
    deps = d.deps()
    up = {x["signal"] for x in deps[X6]}
    letters = {d.signals[s].letter for s in up if d.signals[s].letter}
    assert {"A", "B", "C"} <= letters and START_DDC in up and START_SAM in up
    assert any(x["negated"] for x in deps[X6] if d.signals[x["signal"]].letter == "C")
    lk = deps[d.resolve(LK)[0]]
    assert {x["port"] for x in lk} == {"S", "R"}
    svg = L.render_svg(d)
    assert svg.count("<g data-block=") == len(d.block_order()) and svg.count("<g") == svg.count("</g>")
