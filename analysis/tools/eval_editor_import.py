"""How much hand entry in the editor the AI import replaces, scored on the gold drawings.

Each drawing's structure (eval_wiring_cv.py --work, built from cross-validated detections) is imported
into an empty editor with the product planner (services.ai_candidates), then compared with the gold
annotation people entered by hand in the same editor.

- connections: an imported connection maps each end to its matched gold symbol (class-agnostic,
  holdout_cv.match) and that symbol's nearest gold terminal. It is a hit when a gold connection joins
  the same two gold terminals, in either direction; each gold connection is hit at most once, so a
  duplicate imported connection counts against precision. Gold wire-bend markers (other/分岐点) are
  contracted: annotators boxed them only to route wires. Imported connections touching a gold symbol
  that gold leaves unwired (every relay coil) are not judged and are reported separately.
- connector names: gold connectors whose label carries pin numbers (eval_wiring_cv.gold_terminal_numbers)
  against the imported terminal name of the matched symbol, read as DEVICE-NUMBER tokens.
- rerun: importing again into the imported result must add nothing.
- onto_gold: what the import would add to the finished gold annotation.

Example:
    python analysis/tools/eval_editor_import.py --gold a.zip b.zip --structures runs/wiring/pred
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from analyze_annotation_graph import endpoint_key, parse_member  # noqa: E402
from eval_wiring_cv import gold_terminal_numbers  # noqa: E402
from holdout_cv import gold_items, is_wire_bend, load_gold, match, prf, score_symbols, to_box  # noqa: E402

from services.ai_candidates import from_structure, plan_import  # noqa: E402
from services.detection_rules import detection_kind  # noqa: E402


def apply_plan(symbols: list[dict], connections: list[dict], plan: Any) -> tuple[list[dict], list[dict]]:
    """The editor's merge (frontend mergeAiImport): new terminals on existing symbols, then new symbols and connections."""
    extra: dict[str, list[dict]] = {}
    for t in plan.terminals:
        extra.setdefault(t["symbol_ref"], []).append({k: v for k, v in t.items() if k != "symbol_ref"})
    merged = [{**s, "terminals": [*(s.get("terminals") or []), *extra.get(s["ref"], [])]} for s in symbols]
    return merged + plan.symbols, connections + plan.connections


def gold_edges(project: dict[str, Any]) -> set[frozenset[str]]:
    """Gold connections as unordered terminal pairs, with wire-bend markers contracted away.

    An end without a terminal on a symbol that has exactly one terminal is that terminal.
    """
    bends = {s["ref"] for s in project["symbols"] if is_wire_bend(s)}
    single = {s["ref"]: s["terminals"][0]["ref"] for s in project["symbols"] if len(s.get("terminals") or []) == 1}
    neighbours: dict[str, set[str]] = {}

    def node(symbol_ref: str, terminal_ref: str | None) -> str:
        if symbol_ref in bends:
            return symbol_ref
        return endpoint_key(symbol_ref, terminal_ref or single.get(symbol_ref))

    for c in project["connections"]:
        a, b = node(c["from_symbol_ref"], c.get("from_terminal_ref")), node(c["to_symbol_ref"], c.get("to_terminal_ref"))
        if a != b:
            neighbours.setdefault(a, set()).add(b)
            neighbours.setdefault(b, set()).add(a)
    for bend in bends:
        around = neighbours.pop(bend, set()) - {bend}
        for n in around:
            neighbours[n].discard(bend)
            neighbours[n].update(around - {n})
    return {frozenset((a, b)) for a, ns in neighbours.items() for b in ns}


def imported_items(symbols: list[dict], width: int, height: int) -> list[dict]:
    return [
        {"ref": s["ref"], "class_name": s["class_key"], "label": s.get("label") or "", "kind": detection_kind(s["w"], s["h"]),
         "bbox": to_box(s["cx"], s["cy"], s["w"], s["h"], width, height)}
        for s in symbols
    ]


def name_pairs(name: str) -> tuple[list[str], list[tuple[str, str]]]:
    numbers, pairs = [], []
    for token in name.split():
        device, _, number = token.rpartition("-")
        if number.isdigit():
            numbers.append(number)
            if device:
                pairs.append((device, number))
    return numbers, pairs


def score_drawing(drawing: dict[str, Any], structure: dict[str, Any], class_keys: list[str]) -> dict[str, Any]:
    project = drawing["project"]
    width, height = int(project["image_width"]), int(project["image_height"])
    candidates = from_structure(structure, class_keys)
    plan = plan_import(candidates, [], [], width, height)
    symbols, connections = apply_plan([], [], plan)
    again = plan_import(candidates, symbols, connections, width, height)
    onto = plan_import(candidates, project["symbols"], project["connections"], width, height)

    gold = gold_items(drawing)
    pred = imported_items(symbols, width, height)
    detection = score_symbols(gold, pred)
    g_sym = [g for g in gold if g["kind"] == "symbol"]
    p_sym = [p for p in pred if p["kind"] == "symbol"]
    to_gold = {p_sym[pi]["ref"]: g_sym[gi]["ref"] for gi, pi, _ in match(g_sym, p_sym, class_aware=False)}
    gold_symbols = {s["ref"]: s for s in project["symbols"]}
    position = {t["ref"]: (t["tx"] * width, t["ty"] * height) for s in symbols for t in s["terminals"]}

    def gold_key(symbol_ref: str, terminal_ref: str | None) -> str | None:
        gold_ref = to_gold.get(symbol_ref)
        if gold_ref is None:
            return None
        terminals = gold_symbols[gold_ref].get("terminals") or []
        if not terminals or terminal_ref is None:
            return gold_ref
        px, py = position[terminal_ref]
        nearest = min(terminals, key=lambda t: (t["tx"] * width - px) ** 2 + (t["ty"] * height - py) ** 2)
        return endpoint_key(gold_ref, nearest["ref"])

    edges = gold_edges(project)
    wired = {parse_member(k)[0] for e in edges for k in e}
    counts: Counter = Counter()
    hit: set[frozenset[str]] = set()
    for c in connections:
        a, b = gold_key(c["from_symbol_ref"], c["from_terminal_ref"]), gold_key(c["to_symbol_ref"], c["to_terminal_ref"])
        if a is not None and b is not None and not {parse_member(a)[0], parse_member(b)[0]} <= wired:
            counts["not_judged"] += 1
            continue
        counts["judged"] += 1
        pair = frozenset((a, b)) if a is not None and b is not None and a != b else None
        if pair in edges and pair not in hit:
            hit.add(pair)
        elif a is None or b is None:
            counts["unmatched_symbol"] += 1
    names: Counter = Counter()
    by_ref = {s["ref"]: s for s in symbols}
    from_gold = {g: p for p, g in to_gold.items()}
    for g in g_sym:
        if g["class_name"] != "connector":
            continue
        gold_numbers, gold_pairs_ = gold_terminal_numbers(g["label"])
        if not gold_numbers:
            continue
        names["gold"] += 1
        imported = by_ref.get(from_gold.get(g["ref"], ""))
        if imported is None:
            continue
        numbers, pairs = name_pairs(" ".join(t["name"] for t in imported["terminals"]))
        names["scored"] += 1
        names["numbers_agree"] += sorted(numbers) == sorted(gold_numbers)
        if gold_pairs_:
            names["pairs_scored"] += 1
            names["pairs_agree"] += sorted(pairs) == sorted(gold_pairs_)
    return {
        "sheet": drawing["sheet"],
        "connections": {"tp": len(hit), "fp": counts["judged"] - len(hit), "fn": len(edges - hit)},
        "not_judged": counts["not_judged"],
        "unmatched_symbol": counts["unmatched_symbol"],
        "imported": {"symbols": len(symbols), "terminals": len(position), "connections": len(connections)},
        "skipped": candidates.skipped,
        "detection": dict(detection["aware"]),
        "class_agree": detection["type_correct"], "class_scored": detection["type_total"],
        "names": dict(names),
        "rerun_added": len(again.symbols) + len(again.terminals) + len(again.connections),
        "onto_gold": {"symbols": len(onto.symbols), "terminals": len(onto.terminals), "connections": len(onto.connections)},
    }


def aggregate(rows: list[dict[str, Any]]) -> dict[str, Any]:
    tot: Counter = Counter()
    det: Counter = Counter()
    names: Counter = Counter()
    imported: Counter = Counter()
    onto: Counter = Counter()
    skipped: Counter = Counter()
    for r in rows:
        tot.update(r["connections"])
        det.update(r["detection"])
        names.update(r["names"])
        imported.update(r["imported"])
        onto.update(r["onto_gold"])
        skipped.update(r["skipped"])
    class_scored = sum(r["class_scored"] for r in rows)

    def rate(n: int, d: int) -> float | None:
        return round(n / d, 4) if d else None

    return {
        "drawings": len(rows),
        "connections": prf(tot["tp"], tot["fp"], tot["fn"]),
        "gold_connections": tot["tp"] + tot["fn"],
        "not_judged": sum(r["not_judged"] for r in rows),
        "unmatched_symbol": sum(r["unmatched_symbol"] for r in rows),
        "imported": dict(imported),
        "skipped": dict(skipped),
        "symbol_detection": prf(det["tp"], det["fp"], det["fn"]),
        "class_agreement": {"scored": class_scored, "agree": rate(sum(r["class_agree"] for r in rows), class_scored)},
        "connector_names": {
            "gold": names["gold"], "scored": names["scored"],
            "numbers_agree": rate(names["numbers_agree"], names["scored"]),
            "pairs_scored": names["pairs_scored"], "pairs_agree": rate(names["pairs_agree"], names["pairs_scored"]),
            "numbers_agree_of_gold": rate(names["numbers_agree"], names["gold"]),
        },
        "rerun_added": sum(r["rerun_added"] for r in rows),
        "onto_gold_added": dict(onto),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--gold", type=Path, nargs="+", required=True)
    parser.add_argument("--structures", type=Path, required=True, help="directory of <SHEET>.json structures")
    parser.add_argument("--out", type=Path, help="write the summary JSON here")
    args = parser.parse_args()

    class_keys, drawings = load_gold(args.gold)
    tune = {d["sheet"] for d in drawings[0::2]}
    rows, errors = [], {}
    for d in drawings:
        path = args.structures / f"{d['sheet']}.json"
        if not path.exists():
            errors[d["sheet"]] = "structure missing"
            continue
        rows.append(score_drawing(d, json.loads(path.read_text(encoding="utf-8")), class_keys))
    summary = {
        "errors": len(errors), "error_detail": errors,
        "tune": aggregate([r for r in rows if r["sheet"] in tune]),
        "report": aggregate([r for r in rows if r["sheet"] not in tune]),
        "all": aggregate(rows),
        "split": {"tune": sorted(tune), "report": sorted({d["sheet"] for d in drawings} - tune)},
        "rows": rows,
    }
    if args.out:
        args.out.write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"errors={len(errors)}")
    for part in ("tune", "report", "all"):
        a = summary[part]
        c, n = a["connections"], a["connector_names"]
        print(f"  {part:6s} drawings={a['drawings']} connections f1={c['f1']} p={c['precision']} r={c['recall']}"
              f" tp={c['tp']} fp={c['fp']} fn={c['fn']} gold={a['gold_connections']} not_judged={a['not_judged']}"
              f" | names numbers={n['numbers_agree']} pairs={n['pairs_agree']} scored={n['scored']}/{n['gold']}"
              f" | symbols f1={a['symbol_detection']['f1']} class={a['class_agreement']['agree']}"
              f" | imported={a['imported']} rerun_added={a['rerun_added']} onto_gold={a['onto_gold_added']}")


if __name__ == "__main__":
    main()
