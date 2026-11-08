"""既存の DB を今のモデルにそろえる（Alembic を使わない軽い移行）。

起動のたびに `init_db` から呼ぶ。どの手順も、すでに済んでいれば何もしない。手順を足すときは
`STEPS` の末尾に足し、文書（docs/ER_DIAGRAM.md と DD）に移行とロールバックを書く。
"""

from __future__ import annotations

import json
from collections.abc import Callable

from sqlalchemy import Connection as DbConnection
from sqlalchemy import Engine, Index, inspect, text

# 列の追加（表, 列, 型と既定値）。古い DB から順に足してきたもの。
ADDED_COLUMNS: list[tuple[str, str, str]] = [
    ("annotation_symbols", "origin", "VARCHAR(20) NOT NULL DEFAULT 'manual'"),
    ("annotation_symbols", "attributes_json", "TEXT"),
    ("annotation_symbols", "confidence", "FLOAT"),
    ("symbol_terminals", "origin", "VARCHAR(20) NOT NULL DEFAULT 'manual'"),
    ("connections", "ref", "VARCHAR(50)"),
    ("connections", "attributes_json", "TEXT"),
    ("connections", "origin", "VARCHAR(20) NOT NULL DEFAULT 'manual'"),
    ("training_runs", "decision", "VARCHAR(20) NOT NULL DEFAULT ''"),
    ("training_runs", "baseline_metrics_json", "TEXT"),
    ("training_runs", "baseline_label", "VARCHAR(500)"),
    ("training_runs", "eval_scope_json", "TEXT"),
    ("predictions", "kind", "VARCHAR(20) NOT NULL DEFAULT 'symbol'"),
    ("structure_predictions", "wire_count", "INTEGER"),
    ("structure_predictions", "connection_count", "INTEGER"),
]


def add_columns(conn: DbConnection) -> None:
    inspector = inspect(conn)
    for table, column, ddl in ADDED_COLUMNS:
        if column not in {c["name"] for c in inspector.get_columns(table)}:
            conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}"))
            if (table, column) == ("predictions", "kind"):
                backfill_prediction_kind(conn)


def backfill_prediction_kind(conn: DbConnection) -> None:
    """列追加前の推論結果にも同じ規則を当て、再推論しなくても件数が揃うようにする。"""
    from services.detection_rules import detection_kind

    rows = conn.execute(text("SELECT id, w, h FROM predictions")).all()
    region_ids = [{"id": row.id} for row in rows if detection_kind(row.w, row.h) == "region"]
    if region_ids:
        conn.execute(text("UPDATE predictions SET kind = 'region' WHERE id = :id"), region_ids)


def backfill_structure_counts(conn: DbConnection) -> None:
    """一覧の件数の列を、列を足す前に保存した構造解析の結果から数える（1 回だけ）。"""
    from models import structure_counts

    ids = conn.execute(text("SELECT project_id FROM structure_predictions WHERE wire_count IS NULL")).scalars().all()
    for project_id in ids:
        raw = conn.execute(text("SELECT payload_json FROM structure_predictions WHERE project_id = :p"), {"p": project_id}).scalar_one()
        wires, connections = structure_counts(json.loads(raw))
        conn.execute(text("UPDATE structure_predictions SET wire_count = :w, connection_count = :c WHERE project_id = :p"),
                     {"w": wires, "c": connections, "p": project_id})


def remove_orphans(conn: DbConnection) -> None:
    """外部キーを有効にする前の SQLite では、親を消しても子が残ることがあった。残った子を片付ける。"""
    for table in ("annotation_images", "annotation_symbols", "connections", "predictions", "structure_predictions"):
        conn.execute(text(f"DELETE FROM {table} WHERE project_id NOT IN (SELECT id FROM annotation_projects)"))
    conn.execute(text("DELETE FROM symbol_terminals WHERE symbol_id NOT IN (SELECT id FROM annotation_symbols)"))
    conn.execute(text("DELETE FROM connections WHERE from_symbol_id NOT IN (SELECT id FROM annotation_symbols)"
                      " OR to_symbol_id NOT IN (SELECT id FROM annotation_symbols)"))
    for side in ("from", "to"):
        conn.execute(text(f"UPDATE connections SET {side}_terminal_id = NULL WHERE {side}_terminal_id IS NOT NULL"
                          f" AND {side}_terminal_id NOT IN (SELECT id FROM symbol_terminals)"))
    for table, column in (("predictions", "model_id"), ("training_runs", "result_model_id")):
        conn.execute(text(f"UPDATE {table} SET {column} = NULL WHERE {column} IS NOT NULL"
                          f" AND {column} NOT IN (SELECT id FROM ml_models)"))


def assign_connection_refs(conn: DbConnection) -> None:
    """参照名の無い配線（属性を入れる前の DB）と重複した参照名に、W-0001 形式の空き番号を振る。一意の索引の前に要る。"""
    from services.annotation_service import connection_refs

    projects = conn.execute(text(
        "SELECT DISTINCT project_id FROM connections WHERE ref IS NULL OR ref = '' OR project_id IN"
        " (SELECT project_id FROM connections GROUP BY project_id, ref HAVING COUNT(*) > 1)"
    )).scalars().all()
    for project_id in projects:
        rows = conn.execute(text("SELECT id, ref FROM connections WHERE project_id = :p ORDER BY id"), {"p": project_id}).all()
        refs = connection_refs([row.ref for row in rows])
        changed = [{"id": row.id, "ref": ref} for row, ref in zip(rows, refs, strict=True) if row.ref != ref]
        if not changed:
            continue
        # 振り直す途中で一時的に重複しないよう、いったん外してから入れる。
        conn.execute(text("UPDATE connections SET ref = NULL WHERE id = :id"), changed)
        conn.execute(text("UPDATE connections SET ref = :ref WHERE id = :id"), changed)


def create_indexes(conn: DbConnection) -> None:
    """モデルに書いた索引のうち、既存の表に無いものを作る（create_all は既存の表に索引を足さない）。"""
    from database import Base

    for table in Base.metadata.sorted_tables:
        for index in table.indexes:
            index.create(conn, checkfirst=True)


def drop_redundant_indexes(conn: DbConnection) -> None:
    """主キーに重ねて作っていた索引（ix_<表>_id）を消す。主キーの索引だけで足りる。"""
    from database import Base

    inspector = inspect(conn)
    for table in Base.metadata.sorted_tables:
        names = {index["name"] for index in inspector.get_indexes(table.name)}
        if (name := f"ix_{table.name}_id") in names:
            Index(name, table.c.id).drop(conn)


STEPS: list[Callable[[DbConnection], None]] = [
    add_columns,
    backfill_structure_counts,
    remove_orphans,
    assign_connection_refs,
    create_indexes,
    drop_redundant_indexes,
]


def migrate(engine: Engine) -> None:
    for step in STEPS:
        with engine.begin() as conn:
            step(conn)

