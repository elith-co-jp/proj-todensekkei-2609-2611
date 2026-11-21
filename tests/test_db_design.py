"""DB の設計（DD-0015）: 古い DB の移行、DB の連鎖削除、一覧の件数の列。"""

from __future__ import annotations

import json

from sqlalchemy import UniqueConstraint, inspect, text

import database
import db_migrations
import models
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



def test_bulk_read_matches_serialize_project(client):
    """回路・対応表・図面セットで使う一括の読み込み（DD-0042）は、図面 1 枚の読み込みと同じ dict を返す。"""
    from services.annotation_service import WITH_ANNOTATIONS, load_pages, serialize_project

    first = create_project(client)
    second = create_project(client, "ER21217.png")
    empty = create_project(client, "ER21218.png")
    assert client.put(f"/api/projects/{first}/annotations", json=SAMPLE_ANNOTATION).status_code == 200
    # 参照名の並びと id の並びを逆にし、属性・未知のクラス・端子なしの配線も入れる
    symbols = [{**s, "ref": f"SYM-{9 - i:04d}", "terminals": [{**t, "ref": f"SYM-{9 - i:04d}-T{k}"}
                                                              for k, t in enumerate(s.get("terminals", []), 1)],
                "attributes": {"maker": "東電", "rating": i}} for i, s in enumerate(SAMPLE_ANNOTATION["symbols"])]
    symbols[2]["class_key"] = "new_kind"
    wires = [{"from_symbol_ref": "SYM-0009", "from_terminal_ref": "SYM-0009-T2", "to_symbol_ref": "SYM-0008",
              "to_terminal_ref": "SYM-0008-T1", "wire_no": "1", "attributes": {"length": 2.5}},
             {"from_symbol_ref": "SYM-0007", "to_symbol_ref": "SYM-0009", "kind": "sheet_ref", "external_ref": "(1-2)"}]
    assert client.put(f"/api/projects/{second}/annotations",
                      json={"symbols": symbols, "connections": wires}).status_code == 200

    db = database.SessionLocal()
    try:
        ids = [second, empty, first, 9999]
        bulk = load_pages(db, ids)
        projects = (db.query(models.AnnotationProject).options(*WITH_ANNOTATIONS)
                    .filter(models.AnnotationProject.id.in_(ids)).order_by(models.AnnotationProject.id).all())
        assert bulk == [serialize_project(db, p, with_image_meta=False) for p in projects]
        assert [p["id"] for p in bulk] == [first, second, empty]
        assert bulk[1]["symbols"][0]["attributes"] == {"maker": "東電", "rating": 2}
        assert load_pages(db, []) == [] and load_pages(db, [9999]) == []
    finally:
        db.close()


def test_structure_texts_are_kept_beside_the_payload(client):
    """回路が毎回使う文字は列からだけ読む。JSON と食い違わず、列を知らない古い版の行も読め、起動時に埋まる（DD-0042）。"""
    from services.structure_service import get_structure_texts

    with_texts, no_texts, old_row = (create_project(client, f"{n}.png") for n in "abc")
    texts = [{"text": "PER2100100", "box": [1, 2, 3, 4]}]
    db = database.SessionLocal()
    db.add(StructurePrediction(project_id=with_texts, payload_json=json.dumps({"wires": [], "texts": texts})))
    db.add(StructurePrediction(project_id=no_texts, payload_json=_structure(1, 1)))
    db.add(StructurePrediction(project_id=old_row, payload_json=json.dumps({"texts": texts})))
    db.commit()
    db.close()
    with database.engine.begin() as conn:  # 列を知らない古い版が書いた行
        conn.execute(text("UPDATE structure_predictions SET texts_json = NULL WHERE project_id = :p"), {"p": old_row})
    assert _rows("structure_predictions", f"project_id = {no_texts} AND texts_json = '[]'") == 1

    db = database.SessionLocal()
    try:
        assert get_structure_texts(db, [with_texts, no_texts, old_row, 9999]) == {with_texts: texts, old_row: texts}
    finally:
        db.close()
    db_migrations.migrate(database.engine)
    assert _rows("structure_predictions", "texts_json IS NULL") == 0
    assert client.get(f"/api/ml/projects/{with_texts}/structure").json() == {
        "project_id": with_texts, "result": {"wires": [], "texts": texts}}
    assert client.get(f"/api/ml/projects/{create_project(client)}/structure").json()["result"] is None


def test_every_foreign_key_has_an_index():
    """外部キーの列には索引がある（SQLite は外部キーに索引を自動で作らない。無いと連鎖削除で子の表を全件走査する）。"""
    missing = []
    for table in database.Base.metadata.sorted_tables:
        leading = {tuple(c.name for c in index.columns)[0] for index in table.indexes}
        leading |= {tuple(c.name for c in u.columns)[0] for u in table.constraints
                    if isinstance(u, UniqueConstraint)}
        leading.add(next(iter(table.primary_key.columns)).name)
        missing += [f"{table.name}.{fk.parent.name}" for fk in table.foreign_keys if fk.parent.name not in leading]
    assert missing == []
