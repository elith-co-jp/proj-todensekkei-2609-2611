"""サービスの細かい場合: 「もしも」の変更・回路コード・行先札・属性 API・構造解析の文書・モデル・図面セット・
機器索引・コイルの配線・解析ジョブ。"""

from __future__ import annotations

import threading
import time

import pytest

from services import analysis_jobs, circuit_code, circuit_model, coil_wiring, model_service, sheet_links
from services import circuit_whatif as w
from services import device_index as di
from services.circuit_model import build_circuit
from services.structure_service import document_structure, get_structure_texts, get_structures
from tests.test_circuit_model import _sym, _wire, self_holding_page

X = "X R100"


@pytest.fixture(autouse=True)
def builtin(monkeypatch):
    monkeypatch.setattr(circuit_model, "ngspice_path", lambda: None)


# ------------------------------------------------------------------------------------------ もしも


def test_open_and_short_of_contacts_and_unknown_symbols():
    circuit = build_circuit([self_holding_page()])
    changed, info = w.apply_change(circuit, {
        "open": [{"project_id": 1, "symbol_ref": "STOP"}, {"project_id": 1, "symbol_ref": "NOPE"}],
        "short": [{"project_id": 1, "symbol_ref": "PB1"}, {"project_id": 9, "symbol_ref": "NOPE"}],
    })
    assert {(u["project_id"], u["symbol_ref"]) for u in info["unknown"]} == {(1, "NOPE"), (9, "NOPE")}
    stop, pb1 = (next(e.name for e in circuit.elements if e.symbol_ref == ref) for ref in ("STOP", "PB1"))
    assert info["forced_contacts"] == {stop: False, pb1: True}


def test_impact_stops_at_the_depth_limit():
    circuit = build_circuit([self_holding_page()])
    assert w.impact(circuit, "PB1", max_depth=0)["devices"] == []


# ------------------------------------------------------------------------------------------ 回路コード


def test_circuit_code_rejects_duplicates_and_unknown_timers_and_keeps_bare_nodes():
    c = circuit_code.CircuitCode("t")
    c.contact_a("S1", "P", "A", device="PB1", source=(1, "S1"))
    with pytest.raises(ValueError, match="重なって"):
        c.contact_a("S1", "P", "A", device="PB1", source=(1, "S2"))
    with pytest.raises(ValueError, match="on_delay か off_delay"):
        c.timer("PB1", "later", 1)
    c.nodes("T1", "T1", "T2")
    circuit = c.build()
    assert {"T1", "T2"} <= set(circuit.nodes)
    code = circuit_code.to_python(circuit)
    assert "c.nodes('T1', 'T2')" in code
    assert set(circuit_code.from_python(code).nodes) == set(circuit.nodes)


# ------------------------------------------------------------------------------------------ 行先札


def test_tags_from_symbols_and_wires():
    page = {"id": 1, "sheet_no": "ER21211", "name": "a"}
    assert sheet_links.symbol_tag(page, _sym("X", "contact_a", "X"), None, set()) is None
    assert sheet_links.connection_tag(page, {"external_ref": "どこか", "to_symbol_ref": "T"}, None) is None
    tag = sheet_links.connection_tag(page, {"external_ref": "ER21212", "to_symbol_ref": "T", "wire_no": "601"}, None)
    assert tag.target == "ER21212" and "601" in tag.idents


def test_tag_on_a_page_without_sheet_number_is_reported():
    tag = sheet_links.Tag(1, "T", None, "ER21212", None, set(), "symbol", "ER21212へ")
    pairs, diagnostics = sheet_links.link_tags([tag], {1: None})
    assert pairs == [] and "シート番号が分からない" in diagnostics[0]["message"]


# ------------------------------------------------------------------------------------------ 属性 API


def test_attribute_api_definitions_and_errors(client, monkeypatch):
    from routers import attributes

    assert "symbol" in client.get("/api/attributes/definitions").json()
    assert client.post("/api/attributes/export", json={"project_ids": [999999]}).status_code == 400
    monkeypatch.setattr(attributes, "MAX_UPLOAD", 4)
    res = client.post("/api/attributes/import", files={"file": ("a.json", b"{}12345", "application/json")})
    assert res.status_code == 413


# ------------------------------------------------------------------------------------------ 構造解析


def test_structure_reads_with_no_projects(client):
    from database import SessionLocal

    db = SessionLocal()
    try:
        assert get_structures(db, []) == {} and get_structure_texts(db, []) == {}
    finally:
        db.close()


def test_structure_analysis_needs_exactly_one_image():
    from types import SimpleNamespace

    from services.structure_service import analyze_project

    with pytest.raises(RuntimeError, match="1画像"):
        analyze_project(SimpleNamespace(images=[]), SimpleNamespace(), [])


def test_document_structure_fills_terminal_names_and_classes_of_wire_ends():
    page = {"symbols": [{"id": "S1", "class_name": "relay_coil", "terminals": [{"ref": "S1-T1", "name": "A1"}]}],
            "from_to": {"connections": [{"from": {"symbol_id": "S1", "terminal_ref": "S1-T1", "class_name": "?"},
                                         "to": {"symbol_id": "GONE"}}]}}
    document_structure([page])
    end = page["from_to"]["connections"][0]["from"]
    assert end["terminal_name"] == "A1" and end["class_name"] == "relay_coil"


# ------------------------------------------------------------------------------------------ モデル


def test_model_bytes_limits_and_broken_metrics(monkeypatch, tmp_path):
    with pytest.raises(ValueError, match="空"):
        model_service.store_model_bytes(b"", "m.pt")
    monkeypatch.setattr(model_service, "MAX_MODEL_BYTES", 3)
    with pytest.raises(ValueError, match="上限"):
        model_service.store_model_bytes(b"1234", "m.pt")
    from types import SimpleNamespace

    from models import utc_now_naive

    row = SimpleNamespace(id=1, name="m", version="1", file_name="m.pt", sha256="x" * 64, size_bytes=1,
                          metrics_json="{壊れた", is_active=False, source="upload", note=None, created_at=utc_now_naive())
    assert model_service.serialize_model(row)["metrics"] is None


def test_frozen_exe_without_a_bundled_model(monkeypatch, tmp_path):
    monkeypatch.setattr(model_service, "is_frozen", lambda: True)
    monkeypatch.setattr(model_service, "resource_base", lambda: tmp_path)
    assert model_service.ensure_bundled_model(None) is None


# ------------------------------------------------------------------------------------------ 図面セット


def test_drawing_set_needs_a_name_and_keeps_an_empty_note(client):
    res = client.post("/api/drawing-sets", json={"name": "  "})
    assert res.status_code == 400 and "名前" in res.json()["detail"]
    created = client.post("/api/drawing-sets", json={"name": "DS", "note": "メモ"}).json()
    updated = client.put(f"/api/drawing-sets/{created['id']}", json={"note": ""}).json()
    assert updated["note"] is None


# ------------------------------------------------------------------------------------------ 機器索引


def test_excel_rows_list_every_note():
    page = self_holding_page()
    page["sheet_no"] = "ER21236"
    page["symbols"] += [_sym("V", "contact_a", "V R500 (ER99998)"), _sym("Q", "contact_a", None, cy=0.7)]
    page["connections"] += [_wire(("V", 1), ("P", 1)), _wire(("Q", 1), ("P", 1))]
    page["connections"][3]["wire_no"] = "R 2"
    page["connections"][8]["wire_no"] = "R2"
    index = di.build_index([page], build_circuit([page], {}))
    v = next(d for d in index["devices"] if d["device"] == "V R500")
    assert [n["kind"] for n in v["notes"]] == ["coil_outside_set"]
    notes = di.rows(index)["要確認"]
    assert any("選んだ図面の外" in r[3] for r in notes)
    assert any(r[0] == "Q（a 接点）" for r in notes)
    assert any(r[0] == "電線番号 R 2" for r in notes)


# ------------------------------------------------------------------------------------------ コイルの配線


def test_clip_and_through_wires_edge_cases():
    box = (0.0, 0.0, 10.0, 10.0)
    assert coil_wiring._clip((-5, 30), (30, -5), box) is None         # 斜めの線が枠の角の外を通る
    assert coil_wiring._clip((-5, 5), (-1, 6), box) is None           # 枠に届かない
    coil = _sym("C", "relay_coil", "C", terms=0)
    a, b = _sym("A", "lamp", "A", terms=1), _sym("B", "lamp", "B", terms=1)
    a["terminals"][0].update(tx=0.5, ty=0.5)
    b["terminals"][0].update(tx=0.5, ty=0.5)
    # 端点が同じ位置の線・位置の分からない端点はコイルを通り抜けない
    wires = [_wire(("A", 1), ("B", 1)), {"from_symbol_ref": "A", "from_terminal_ref": "?", "to_symbol_ref": "B",
                                          "to_terminal_ref": "B-T1", "kind": "wire"}]
    assert coil_wiring.through_wires([coil, a, b], wires, 100, 100) == {}


def test_split_counts_coils_with_terminals_and_with_several_wires():
    with_terms = _sym("C1", "relay_coil", "C1")
    lone = {**_sym("C2", "relay_coil", "C2", terms=0), "cx": 0.5, "cy": 0.5, "w": 0.2, "h": 0.2}
    left, right = _sym("L", "junction", terms=1), _sym("R", "junction", terms=1)
    left["terminals"][0].update(tx=0.1, ty=0.5)
    right["terminals"][0].update(tx=0.9, ty=0.5)
    up, down = _sym("U", "junction", terms=1), _sym("D", "junction", terms=1)
    up["terminals"][0].update(tx=0.5, ty=0.1)
    down["terminals"][0].update(tx=0.5, ty=0.9)
    _, _, counts = coil_wiring.split_through_wires(
        [with_terms, lone, left, right, up, down], [_wire(("L", 1), ("R", 1)), _wire(("U", 1), ("D", 1))], 100, 100)
    assert counts == {"split": 0, "multiple": 1, "none": 0, "has_terminals": 1}


# ------------------------------------------------------------------------------------------ 解析ジョブ


def _wait(job):
    for _ in range(200):
        if analysis_jobs.get(job.id).status != "running":
            return analysis_jobs.get(job.id)
        time.sleep(0.01)
    raise AssertionError("ジョブが終わらない")


def test_failed_jobs_report_only_runtime_errors_and_old_jobs_are_forgotten(monkeypatch):
    def fail(message, kind):
        def work(job):
            raise kind(message)
        return work

    runtime_error = _wait(analysis_jobs.start([1], 0.5, None, fail("モデルがありません", RuntimeError)))
    assert (runtime_error.status, runtime_error.error) == ("failed", "モデルがありません")
    other = _wait(analysis_jobs.start([1], 0.5, None, fail("内部の詳細", KeyError)))
    assert other.error == "解析に失敗しました"

    gate = threading.Event()
    monkeypatch.setattr(analysis_jobs, "KEEP_JOBS", 2)
    long_running = analysis_jobs.start([1], 0.5, None, lambda job: gate.wait(5))
    for _ in range(3):
        _wait(analysis_jobs.start([1], 0.5, None, lambda job: None))
    ids = {j.id for j in analysis_jobs.recent()}
    assert long_running.id in ids and runtime_error.id not in ids  # 動いているジョブは消さない
    gate.set()
    _wait(long_running)
