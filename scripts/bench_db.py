"""DB の読み書きの速さを測る（DD-0015）。図面 N 枚分のデータを一時フォルダの SQLite に作り、API ごとの時間を出す。

    python scripts/bench_db.py [--projects 100] [--repeat 5] [--structure-file 構造解析の JSON]
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def annotation(symbols: int, wires: int) -> dict:
    out_symbols = [
        {"ref": f"SYM-{i:04d}", "class_key": ("relay_coil", "contact_a", "terminal")[i % 3], "label": f"L{i}",
         "cx": (i % 30) / 30 + 0.01, "cy": (i // 30) / 10 + 0.01, "w": 0.01, "h": 0.01,
         "attributes": {"maker": "東電"} if i % 5 == 0 else {},
         "terminals": [{"ref": f"SYM-{i:04d}-T{k}", "name": str(k), "tx": 0.5, "ty": 0.5} for k in (1, 2)]}
        for i in range(1, symbols + 1)
    ]
    out_wires = [
        {"from_symbol_ref": f"SYM-{i % symbols + 1:04d}", "from_terminal_ref": f"SYM-{i % symbols + 1:04d}-T2",
         "to_symbol_ref": f"SYM-{(i + 1) % symbols + 1:04d}", "to_terminal_ref": f"SYM-{(i + 1) % symbols + 1:04d}-T1",
         "wire_no": str(600 + i)}
        for i in range(wires)
    ]
    return {"symbols": out_symbols, "connections": out_wires}


def structure(target_bytes: int) -> dict:
    wire = {"id": "wire_000", "orientation": "v", "polyline": [[1, 2], [1, 200]], "confidence": 0.78, "reason": "long_vertical_wire"}
    wires = [{**wire, "id": f"wire_{i:03d}"} for i in range(max(1, target_bytes // len(json.dumps(wire))))]
    return {"wires": wires, "from_to": {"connection_count": 120, "connections": []}}


def timed(fn, repeat: int) -> float:
    times = []
    for _ in range(repeat):
        start = time.perf_counter()
        fn()
        times.append(time.perf_counter() - start)
    return statistics.median(times) * 1000


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--projects", type=int, default=100)
    parser.add_argument("--symbols", type=int, default=150)
    parser.add_argument("--wires", type=int, default=200)
    parser.add_argument("--structure-kb", type=int, default=430)
    parser.add_argument("--structure-file", type=Path, help="実際の構造解析の JSON（無ければ --structure-kb の大きさで作る）")
    parser.add_argument("--repeat", type=int, default=5)
    args = parser.parse_args()

    tmp = tempfile.mkdtemp(prefix="seqanno_bench_")
    os.environ["SEQANNO_DATA_DIR"] = tmp
    os.environ["SEQANNO_DATABASE_URL"] = f"sqlite:///{Path(tmp) / 'bench.db'}"
    from fastapi.testclient import TestClient

    import database
    from main import app
    from models import AnnotationImage, AnnotationProject, StructurePrediction

    database.init_db()
    body = annotation(args.symbols, args.wires)
    payload_json = args.structure_file.read_text(encoding="utf-8") if args.structure_file else json.dumps(structure(args.structure_kb * 1024))
    with TestClient(app) as client:
        db = database.SessionLocal()
        ids = []
        for i in range(args.projects):
            project = AnnotationProject(name=f"ER{i:05d}.png", image_width=1755, image_height=1240)
            db.add(project)
            db.flush()
            db.add(AnnotationImage(project_id=project.id, filename="x.png", sha256=f"{i:064d}", width=1755, height=1240))
            db.add(StructurePrediction(project_id=project.id, payload_json=payload_json))
            ids.append(project.id)
        db.commit()
        db.close()
        start = time.perf_counter()
        for pid in ids:
            assert client.put(f"/api/projects/{pid}/annotations", json=body).status_code == 200
        fill = (time.perf_counter() - start) / len(ids) * 1000

        pid = ids[0]
        rows = {
            "保存 PUT /projects/{id}/annotations": fill,
            "上書き保存（同上、既存あり）": timed(lambda: client.put(f"/api/projects/{pid}/annotations", json=body).raise_for_status(), args.repeat),
            "一覧 GET /projects": timed(lambda: client.get("/api/projects").raise_for_status(), args.repeat),
            "図面 GET /projects/{id}": timed(lambda: client.get(f"/api/projects/{pid}").raise_for_status(), args.repeat),
            "集計 GET /stats": timed(lambda: client.get("/api/stats").raise_for_status(), args.repeat),
        }
        victims = iter(ids[1:])
        rows["削除 DELETE /projects/{id}"] = timed(
            lambda: client.delete(f"/api/projects/{next(victims)}").raise_for_status(), args.repeat)
    print(f"図面 {args.projects} 枚、1 枚にシンボル {args.symbols}・端子 {args.symbols * 2}・配線 {args.wires}、"
          f"構造解析 {len(payload_json) // 1024} KB（中央値、ミリ秒）")
    for name, ms in rows.items():
        print(f"  {name:<36} {ms:8.1f}")


if __name__ == "__main__":
    main()

