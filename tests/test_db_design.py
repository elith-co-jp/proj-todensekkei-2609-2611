"""DB の設計（DD-0015）: 古い DB の移行、DB の連鎖削除、一覧の件数の列。"""

from __future__ import annotations

import json

from sqlalchemy import inspect, text

import database
import db_migrations
from models import StructurePrediction
from tests.conftest import SAMPLE_ANNOTATION, create_project


def _structure(wires: int, connections: int) -> str:
    return json.dumps({"wires": [{"id": f"w{i}"} for i in range(wires)], "from_to": {"connection_count": connections}})


def _rows(table: str, where: str = "1=1") -> int:
    with database.engine.connect() as conn:
        return conn.execute(text(f"SELECT COUNT(*) FROM {table} WHERE {where}")).scalar_one()


def test_list_shows_structure_counts_saved_with_the_result(client):
    pid = create_project(client)
    db = database.SessionLocal()
    db.add(StructurePrediction(project_id=pid, payload_json=_structure(3, 2)))
    db.commit()
    db.close()
    row = next(p for p in client.get("/api/projects").json() if p["id"] == pid)
    assert (row["analysis_wire_count"], row["analysis_connection_count"]) == (3, 2)


def test_deleting_a_project_removes_every_child_row(client):
    pid = create_project(client)
    assert client.put(f"/api/projects/{pid}/annotations", json=SAMPLE_ANNOTATION).status_code == 200
    db = database.SessionLocal()
    db.add(StructurePrediction(project_id=pid, payload_json=_structure(1, 1)))
    db.commit()
    db.close()

    assert client.delete(f"/api/projects/{pid}").status_code == 200

    for table in ("annotation_symbols", "connections", "annotation_images", "structure_predictions"):
        assert _rows(table, f"project_id = {pid}") == 0, table
    assert _rows("symbol_terminals") == 0


def test_saving_again_replaces_terminals_and_wires(client):
    pid = create_project(client)
    for _ in range(2):
        assert client.put(f"/api/projects/{pid}/annotations", json=SAMPLE_ANNOTATION).status_code == 200
    saved = client.get(f"/api/projects/{pid}").json()
    assert [len(s["terminals"]) for s in saved["symbols"]] == [2, 1, 0]
    assert [c["to_terminal_ref"] for c in saved["connections"]] == ["SYM-0002-T1", None]
    assert _rows("symbol_terminals") == 3
    assert _rows("connections") == 2


def test_old_database_is_brought_up_to_date(client):
    """外部キーが効いていなかった頃の DB: 件数の列が空、配線の参照名の欠けと重複、親の無い端子と配線、索引なし。"""
    pid = create_project(client)
    wires = SAMPLE_ANNOTATION["connections"] + [{**SAMPLE_ANNOTATION["connections"][0], "wire_no": "611"}]
    assert client.put(f"/api/projects/{pid}/annotations", json={**SAMPLE_ANNOTATION, "connections": wires}).status_code == 200
    db = database.SessionLocal()
    db.add(StructurePrediction(project_id=pid, payload_json=_structure(4, 5)))
    db.commit()
    db.close()

    raw = database.engine.raw_connection()
    try:
        cursor = raw.cursor()
        cursor.execute("PRAGMA foreign_keys=OFF")
        cursor.execute("DROP INDEX uq_connection_ref_per_project")
        cursor.execute("UPDATE structure_predictions SET wire_count = NULL, connection_count = NULL")
        cursor.execute("UPDATE connections SET ref = NULL WHERE wire_no = '610'")
        cursor.execute("UPDATE connections SET ref = 'W-0002' WHERE wire_no = '611'")
        cursor.execute("INSERT INTO symbol_terminals (symbol_id, name, tx, ty, origin) VALUES (9999, '1', 0, 0, 'manual')")
        cursor.execute(f"INSERT INTO connections (project_id, from_symbol_id, to_symbol_id, kind, origin)"
                       f" VALUES ({pid}, 9999, 9998, 'wire', 'manual')")
        raw.commit()
    finally:
        raw.cursor().execute("PRAGMA foreign_keys=ON")  # 接続はプールに戻るので元に戻す
        raw.close()

    db_migrations.migrate(database.engine)

    with database.engine.connect() as conn:
        assert conn.execute(text("SELECT wire_count, connection_count FROM structure_predictions")).one() == (4, 5)
        refs = conn.execute(text("SELECT ref FROM connections ORDER BY id")).scalars().all()
    assert len(refs) == 3 and None not in refs and len(set(refs)) == 3
    assert _rows("symbol_terminals", "symbol_id = 9999") == 0
    assert _rows("connections", "from_symbol_id = 9999") == 0
    assert "uq_connection_ref_per_project" in {i["name"] for i in inspect(database.engine).get_indexes("connections")}

