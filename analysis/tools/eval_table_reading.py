"""表の読み取り（リレー表・表題欄、DD-0050）の当たり率を、正解つきの合成の図面で測る。

欄ごとの当たり（正解の文字の中心が入る格子の欄の文字が正解と同じか）と、リレー表の行の項目ごとの当たりを出す。
規則の調整は tune（偶数の種）だけで行い、report（奇数の種）で報告する。

例:
    python analysis/tools/eval_table_reading.py --seeds 40 --mode both --out runs/table_eval
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from analysis import text_extraction as tx  # noqa: E402
from analysis.synthetic_tables import SyntheticTable, make_table  # noqa: E402

FIELDS = ("device", "terminal", "contact", "sheet", "position")


def norm(text: str) -> str:
    return unicodedata.normalize("NFKC", text or "").replace(" ", "").upper()


def run(table: SyntheticTable, reader: Any, mode: str) -> tuple[dict[str, dict[str, Any]], list[dict[str, str]]]:
    """1 枚を読み、表ごとの格子とリレー表の行を返す。mode: baseline（ページの OCR だけ）/ reread（欄ごとに読み直す。解析の既定）。"""
    os.environ["SEQANNO_TABLE_REREAD"] = "1" if mode == "reread" else "0"
    result = tx.extract_text_structure(table.image, [], [], reader)
    rows = result["relay_table"]["rows"]
    return result["tables"], rows


def _cell_at(grid: dict[str, Any], box: tuple[float, float, float, float]) -> str:
    cx, cy = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2
    for cell in grid["cells"]:
        x0, y0, x1, y1 = cell["box"]
        if x0 - 3 <= cx <= x1 + 3 and y0 - 3 <= cy <= y1 + 3:
            return cell["text"]
    return ""


def score(table: SyntheticTable, grids: dict[str, dict[str, Any]], rows: list[dict[str, str]]) -> dict[str, Any]:
    cells: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    misses = []
    for cell in table.cells:
        grid = grids.get("title_block" if cell.kind.startswith("title") else "relay_table")
        got = _cell_at(grid, cell.box) if grid else ""
        ok = norm(got) == norm(cell.text)
        cells[cell.kind][0] += ok
        cells[cell.kind][1] += 1
        if not ok:
            misses.append({"kind": cell.kind, "truth": cell.text, "got": got})
    by_key = {(r["device"], r["terminal"]): r for r in rows}
    fields: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    for truth in table.relay_rows:
        got = by_key.get((truth["device"], truth["terminal"])) or next(
            (r for r in rows if r["terminal"] == truth["terminal"] and norm(r["device"]).startswith(norm(truth["device"]).split()[0][:2])), {})
        for f in FIELDS:
            fields[f][0] += norm(got.get(f, "")) == norm(truth[f])
            fields[f][1] += 1
    return {"cells": dict(cells), "fields": dict(fields), "misses": misses, "row_count": len(rows)}


def _sum(parts: list[dict[str, list[int]]]) -> dict[str, list[int]]:
    total: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    for part in parts:
        for key, (ok, n) in part.items():
            total[key][0] += ok
            total[key][1] += n
    return dict(total)


def _rate(counts: dict[str, list[int]]) -> dict[str, float]:
    out = {k: round(ok / n, 4) for k, (ok, n) in sorted(counts.items()) if n}
    ok, n = sum(v[0] for v in counts.values()), sum(v[1] for v in counts.values())
    out["all"] = round(ok / n, 4) if n else 0.0
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--seeds", type=int, default=40)
    parser.add_argument("--mode", choices=["baseline", "reread", "both"], default="both")
    parser.add_argument("--split", choices=["tune", "report", "all"], default="all")
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    from rapidocr_onnxruntime import RapidOCR

    reader = RapidOCR()
    modes = ["baseline", "reread"] if args.mode == "both" else [args.mode]
    seeds = [s for s in range(args.seeds) if args.split == "all" or (s % 2 == 0) == (args.split == "tune")]
    results: dict[str, dict[str, list[Any]]] = {m: {"cells": [], "fields": [], "misses": []} for m in modes}
    elapsed: dict[str, float] = Counter()
    for seed in seeds:
        table = make_table(seed)
        for mode in modes:
            start = time.perf_counter()
            grids, rows = run(table, reader, mode)
            elapsed[mode] += time.perf_counter() - start
            scored = score(table, grids, rows)
            results[mode]["cells"].append(scored["cells"])
            results[mode]["fields"].append(scored["fields"])
            results[mode]["misses"].extend({**m, "seed": seed} for m in scored["misses"])
    report = {}
    for mode in modes:
        misses = Counter((m["kind"], m["truth"], m["got"]) for m in results[mode]["misses"])
        report[mode] = {
            "cells": _rate(_sum(results[mode]["cells"])),
            "relay_fields": _rate(_sum(results[mode]["fields"])),
            "seconds_per_page": round(elapsed[mode] / len(seeds), 2),
            "top_misses": [list(k) + [v] for k, v in misses.most_common(25)],
        }
    print(json.dumps({"split": args.split, "pages": len(seeds), **report}, ensure_ascii=False, indent=1))
    if args.out:
        args.out.mkdir(parents=True, exist_ok=True)
        (args.out / f"table_eval_{args.split}.json").write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
