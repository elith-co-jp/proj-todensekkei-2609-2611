"""エディタの「AI 推論」取り込み: 構造解析の端子・配線を候補として足し、人の入力は変えない。"""

from __future__ import annotations

import io
import json

from sqlalchemy import text

import database
from models import StructurePrediction
from tests.conftest import create_project
from tests.test_ml_cycle import _predictions_zip

W, H = 400, 300


def _port(ref, x, y, name):
    return {"ref": ref, "name": name, "point": [x, y]}


def _structure():
    """コイルの接点 1 つ、コネクタ 1 つ、分岐点と判定したコネクタ 1 つ、重複した黒丸、リレー表の中の文字。"""
    symbols = [
        {"id": "symbol_001", "class_name": "contact_a", "label": "33HB", "confidence": 0.9,
         "bbox": {"x0": 100, "y0": 100, "x1": 120, "y1": 140}, "status": "detected_by_yolo",
         "terminals": [_port("symbol_001-P01", 110, 102, "13"), _port("symbol_001-P02", 110, 138, "P2")]},
        {"id": "symbol_002", "class_name": "connector", "label": "CN1 5", "confidence": 0.8,
         "bbox": {"x0": 90, "y0": 180, "x1": 130, "y1": 200}, "status": "detected_by_yolo",
         "dot_role": {"role": "terminal", "dot": {"center": [110, 190]}, "suppressed": False},
         "terminals": [_port("symbol_002-P01", 110, 184, "CN1-5"), _port("symbol_002-P02", 110, 196, "CN1-5")]},
        {"id": "symbol_003", "class_name": "junction", "confidence": 0.5,
         "bbox": {"x0": 105, "y0": 185, "x1": 115, "y1": 195}, "status": "detected_by_yolo",
         "dot_role": {"role": "terminal", "dot": {"center": [110, 190]}, "suppressed": True, "duplicate_of": "symbol_002"},
         "terminals": []},
        {"id": "symbol_004", "class_name": "connector", "confidence": 0.7,
         "bbox": {"x0": 200, "y0": 100, "x1": 212, "y1": 112}, "status": "detected_by_yolo",
         "dot_role": {"role": "junction", "dot": {"center": [206, 106]}, "suppressed": False},
         "terminals": [_port("symbol_004-P01", 203, 106, "P1"), _port("symbol_004-P02", 209, 106, "P2"),
                       _port("symbol_004-P03", 206, 109, "P3")]},
        {"id": "symbol_005", "class_name": "contact_a", "confidence": 0.6,
         "bbox": {"x0": 300, "y0": 250, "x1": 320, "y1": 270}, "status": "suppressed_text_zone", "terminals": []},
    ]

    def end(symbol, port):
        return {"symbol_id": symbol, "terminal_ref": f"{symbol}-{port}"}

    connections = [
        {"type": "wire", "from": end("symbol_001", "P02"), "to": end("symbol_002", "P01")},
        {"type": "internal", "from": end("symbol_001", "P01"), "to": end("symbol_001", "P02")},
        {"type": "internal", "from": end("symbol_002", "P01"), "to": end("symbol_002", "P02")},
        {"type": "wire", "from": end("symbol_001", "P01"), "to": end("symbol_004", "P01")},
        {"type": "wire", "from": end("symbol_002", "P01"), "to": end("symbol_001", "P02")},
    ]
    return {
        "status": "completed", "image_size": {"width": W, "height": H}, "symbols": symbols, "wires": [],
        "from_to": {"connection_count": len(connections), "connections": connections}, "warnings": [],
    }


def _project_with_structure(client):
    pid = create_project(client, w=W, h=H)
    with database.SessionLocal() as db:
        db.add(StructurePrediction(project_id=pid, payload_json=json.dumps(_structure())))
        db.commit()
    return pid


def _import(client, pid, symbols, connections):
    res = client.post(f"/api/ml/projects/{pid}/ai-import", json={"symbols": symbols, "connections": connections})
    assert res.status_code == 200, res.text
    return res.json()


def _apply(symbols, connections, result):
    """エディタと同じ手順で追加分を足す。"""
    extra: dict[str, list] = {}
    for t in result["terminals"]:
        extra.setdefault(t["symbol_ref"], []).append({k: v for k, v in t.items() if k != "symbol_ref"})
    merged = [{**s, "terminals": [*s["terminals"], *extra.get(s["ref"], [])]} for s in symbols]
    return merged + result["symbols"], connections + result["connections"]


def _ends(connections):
    return {(c["from_symbol_ref"], c["from_terminal_ref"], c["to_symbol_ref"], c["to_terminal_ref"]) for c in connections}


def test_import_into_empty_editor_adds_terminals_and_wires_as_ai_candidates(client):
    pid = _project_with_structure(client)
    result = _import(client, pid, [], [])

    assert result["source"] == "structure"
    assert result["counts"] == {"existing_symbols": 0, "region_excluded": 0, "duplicate_dots": 1, "text_zone": 1}
    by_ref = {s["ref"]: s for s in result["symbols"]}
    assert [(s["ref"], s["class_key"], s["label"], s["origin"]) for s in result["symbols"]] == [
        ("SYM-0001", "contact_a", "33HB", "inference"),
        ("SYM-0002", "connector", "CN1 5", "inference"),
        ("SYM-0003", "junction", None, "inference"),
    ]
    assert [(t["ref"], t["name"], t["origin"]) for t in by_ref["SYM-0001"]["terminals"]] == [
        ("SYM-0001-T1", "13", "inference"), ("SYM-0001-T2", "2", "inference"),
    ]
    connector_terminal = by_ref["SYM-0002"]["terminals"]
    assert [(t["name"], t["tx"], t["ty"]) for t in connector_terminal] == [("CN1-5", 110 / W, 190 / H)]
    assert [t["name"] for t in by_ref["SYM-0003"]["terminals"]] == ["1"]
    assert _ends(result["connections"]) == {
        ("SYM-0001", "SYM-0001-T2", "SYM-0002", "SYM-0002-T1"),
        ("SYM-0001", "SYM-0001-T1", "SYM-0001", "SYM-0001-T2"),
        ("SYM-0001", "SYM-0001-T1", "SYM-0003", "SYM-0003-T1"),
    }
    assert {c["origin"] for c in result["connections"]} == {"inference"}
    assert result["terminals"] == []


def test_saved_import_keeps_origin_and_second_import_adds_nothing(client):
    pid = _project_with_structure(client)
    symbols, connections = _apply([], [], _import(client, pid, [], []))
    assert client.put(f"/api/projects/{pid}/annotations", json={"symbols": symbols, "connections": connections}).status_code == 200

    saved = client.get(f"/api/projects/{pid}").json()
    assert {t["origin"] for s in saved["symbols"] for t in s["terminals"]} == {"inference"}
    assert {c["origin"] for c in saved["connections"]} == {"inference"}
    again = _import(client, pid, saved["symbols"], saved["connections"])
    assert (again["symbols"], again["terminals"], again["connections"]) == ([], [], [])
    assert again["counts"]["existing_symbols"] == 3


def test_import_reuses_moved_relabeled_and_hand_drawn_symbols_without_changing_them(client):
    pid = _project_with_structure(client)
    symbols = [
        # AI の接点を少し動かして種別を b 接点に直し、端子を 1 つだけ手で置いたもの
        {"ref": "SYM-0001", "class_key": "contact_b", "label": None, "cx": 112 / W, "cy": 120 / H,
         "w": 20 / W, "h": 40 / H, "note": None, "origin": "inference", "confidence": 0.9,
         "terminals": [{"ref": "SYM-0001-T1", "name": "1", "tx": 110 / W, "ty": 104 / H, "origin": "manual"}]},
        # コネクタの黒丸を、人が大きめの分岐点として描いたもの
        {"ref": "SYM-0002", "class_key": "junction", "label": None, "cx": 111 / W, "cy": 191 / H,
         "w": 24 / W, "h": 24 / H, "note": None, "origin": "manual", "confidence": None,
         "terminals": [{"ref": "SYM-0002-T1", "name": "1", "tx": 111 / W, "ty": 191 / H, "origin": "manual"}]},
    ]
    connections = [{"from_symbol_ref": "SYM-0001", "from_terminal_ref": None, "to_symbol_ref": "SYM-0002",
                    "to_terminal_ref": None, "wire_no": "610", "net_id": None, "kind": "wire",
                    "external_ref": None, "note": None, "origin": "manual"}]

    result = _import(client, pid, symbols, connections)

    assert result["counts"]["existing_symbols"] == 2
    assert [(s["ref"], s["class_key"]) for s in result["symbols"]] == [("SYM-0003", "junction")]
    assert [(t["symbol_ref"], t["ref"], t["name"]) for t in result["terminals"]] == [("SYM-0001", "SYM-0001-T2", "2")]
    assert _ends(result["connections"]) == {
        ("SYM-0001", "SYM-0001-T1", "SYM-0001", "SYM-0001-T2"),
        ("SYM-0001", "SYM-0001-T1", "SYM-0003", "SYM-0003-T1"),
    }

    merged, merged_connections = _apply(symbols, connections, result)
    assert client.put(f"/api/projects/{pid}/annotations", json={"symbols": merged, "connections": merged_connections}).status_code == 200
    saved = {s["ref"]: s for s in client.get(f"/api/projects/{pid}").json()["symbols"]}
    assert (saved["SYM-0001"]["class_key"], saved["SYM-0001"]["cx"], saved["SYM-0001"]["label"]) == ("contact_b", 112 / W, None)
    assert saved["SYM-0001"]["terminals"][0] == {"ref": "SYM-0001-T1", "name": "1", "tx": 110 / W, "ty": 104 / H, "origin": "manual"}
    assert (saved["SYM-0002"]["class_key"], saved["SYM-0002"]["origin"], len(saved["SYM-0002"]["terminals"])) == ("junction", "manual", 1)


def test_without_structure_only_detected_symbols_are_offered(client):
    pid = create_project(client)
    res = client.post("/api/ml/inference/import", files={
        "archive": ("p.zip", _predictions_zip(pid, ["0 0.10 0.50 0.05 0.05 0.9", "0 0.50 0.50 0.95 0.90 0.7"]), "application/zip"),
    })
    assert res.status_code == 200, res.text

    result = _import(client, pid, [], [])
    assert result["source"] == "predictions"
    assert [(s["class_key"], s["cx"], s["terminals"]) for s in result["symbols"]] == [("relay_coil", 0.10, [])]
    assert result["counts"]["region_excluded"] == 1
    assert _import(client, pid, result["symbols"], [])["symbols"] == []


def test_without_any_ai_result_nothing_is_offered(client):
    pid = create_project(client)
    assert _import(client, pid, [], [])["source"] is None
    assert client.post("/api/ml/projects/999/ai-import", json={"symbols": [], "connections": []}).status_code == 404


def test_review_summary_counts_ai_terminals_and_wires(client):
    pid = _project_with_structure(client)
    symbols, connections = _apply([], [], _import(client, pid, [], []))
    connections = [c for c in connections if c["to_symbol_ref"] != "SYM-0003"]
    connections.append({"from_symbol_ref": "SYM-0002", "from_terminal_ref": "SYM-0002-T1", "to_symbol_ref": "SYM-0003",
                        "to_terminal_ref": "SYM-0003-T1", "kind": "wire"})
    assert client.put(f"/api/projects/{pid}/annotations", json={"symbols": symbols, "connections": connections}).status_code == 200

    summary = client.get(f"/api/ml/projects/{pid}/review-summary").json()
    assert (summary["ai_detection_count"], summary["kept"], summary["deleted"]) == (3, 3, 0)
    assert summary["wiring"] == {
        "terminals": {"ai_count": 4, "imported": True, "kept": 4, "deleted": 0, "manual_added": 0},
        "connections": {"ai_count": 3, "imported": True, "kept": 2, "deleted": 1, "manual_added": 1},
    }


def test_bundle_roundtrip_keeps_ai_origin_of_terminals_and_wires(client):
    pid = _project_with_structure(client)
    symbols, connections = _apply([], [], _import(client, pid, [], []))
    client.put(f"/api/projects/{pid}/annotations", json={"symbols": symbols, "connections": connections})
    blob = client.get(f"/api/projects/{pid}/export").content

    res = client.post("/api/import", files={"archive": ("bundle.zip", io.BytesIO(blob), "application/zip")})
    assert res.status_code == 200, res.text
    restored = client.get(f"/api/projects/{max(res.json()['project_ids'])}").json()
    assert {t["origin"] for s in restored["symbols"] for t in s["terminals"]} == {"inference"}
    assert {c["origin"] for c in restored["connections"]} == {"inference"}


def test_existing_database_gets_origin_columns_defaulting_to_manual(client):
    pid = create_project(client)
    annotations = {"symbols": [{"ref": "SYM-0001", "class_key": "junction", "cx": 0.5, "cy": 0.5, "w": 0.02, "h": 0.02,
                                "terminals": [{"ref": "SYM-0001-T1", "name": "1", "tx": 0.5, "ty": 0.5}]},
                               {"ref": "SYM-0002", "class_key": "junction", "cx": 0.6, "cy": 0.5, "w": 0.02, "h": 0.02}],
                   "connections": [{"from_symbol_ref": "SYM-0001", "to_symbol_ref": "SYM-0002"}]}
    client.put(f"/api/projects/{pid}/annotations", json=annotations)
    with database.engine.begin() as conn:
        conn.execute(text("ALTER TABLE symbol_terminals DROP COLUMN origin"))
        conn.execute(text("ALTER TABLE connections DROP COLUMN origin"))

    database._migrate_columns()

    saved = client.get(f"/api/projects/{pid}").json()
    assert saved["symbols"][0]["terminals"][0]["origin"] == "manual"
    assert saved["connections"][0]["origin"] == "manual"
