"""Why wire-node pairs are wrong: classify every false and missed node pair of eval_wiring_cv by cause.

Every false and missed pair of node_pairs gets one cause. Pairs that flip when the gold is read with
eval_wiring_cv.gold_nodes(conventions=True) (links drawn through relay coils, endpoints without a terminal ref)
are gold_convention; the rest are classified against that corrected gold.
False pairs (predicted in one node, gold in different nodes or not wired):
- gold_unwired_terminal: a predicted terminal maps to a gold terminal that no gold wire touches.
- merge: both terminals are wired in gold, in different nodes. Each from_to link that joins two gold
  nodes is traced through the wire graph and named by the mechanism that made it (a wire-gap bridge,
  a junction turn, a node anchor across a dotless crossing, a plain wire, ...). "owned" counts the
  false pairs that disappear when only the links of that mechanism are removed.
Missed pairs (gold in one node, predicted apart):
- symbol_missed: a symbol of the pair has no matched detection.
- terminal_missing: the symbol is matched but no predicted terminal maps to that gold terminal.
- split: both are present in different predicted nodes. The gold node is labelled wire_gap when a gold
  segment of it has an uncovered stretch longer than WIRE_GAP_PX outside symbol boxes, unlinked_terminal
  when a member's predicted terminal touches no wire, else graph_break.

Example:
    python analysis/tools/wiring_node_errors.py --gold a.zip b.zip --pred-run runs/cv --work runs/wiring \
        --condition pred --split tune --overlay-dir runs/overlays --overlay-sheets ER21216
"""

from __future__ import annotations

import _repo_path  # noqa: F401  (リポジトリの直下を import できるようにする)

import argparse
import io
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from analyze_annotation_graph import endpoint_key  # noqa: E402
from eval_wiring_cv import (  # noqa: E402
    DSU,
    NODE_CLASSES,
    _pairs_of_groups,
    file_predictions,
    gold_nodes,
    oracle_predictions,
    predicted_nodes,
    structure_predictions,
)
from holdout_cv import gold_items, load_gold, score_symbols  # noqa: E402
from analysis.wiring.connectivity import build_direct_from_to  # noqa: E402

WIRE_GAP_PX = 20.0
AXIS_TOLERANCE_PX = 4.0
MECHANISM_ORDER = (
    "wire_gap_bridge", "terminal_terminal_bridge", "terminal_gap_bridge", "anchor_across_crossing",
    "junction_turn_dot", "junction_turn_tee", "wire",
)


def trace_graph(structure: dict[str, Any], image: Image.Image) -> dict[str, Any]:
    trace: dict[str, Any] = {}
    rebuilt = build_direct_from_to(structure, source_image=image, trace=trace)
    stored = {(c["from"]["id"], c["to"]["id"]) for c in structure["from_to"]["connections"]}
    again = {(c["from"]["id"], c["to"]["id"]) for c in rebuilt["connections"]}
    if stored != again:
        raise RuntimeError(f"rebuilt from_to differs from the stored one ({len(stored ^ again)} links)")
    return trace


def link_path(trace: dict[str, Any], left: str, right: str) -> list[str] | None:
    """Shortest graph path from a stop of terminal left to terminal right, under the traversal's stop rules."""
    graph, by_stop, members = trace["graph"], trace["terminal_by_stop"], trace["members_by_graph_node"]
    starts = [s for s, m in by_stop.items() if m == left]
    previous: dict[str, str | None] = {s: None for s in starts}
    queue = list(starts)
    while queue:
        current = queue.pop(0)
        here = by_stop.get(current)
        reached = (here == right) or (right in members.get(current, set()))
        if reached and previous[current] is not None:
            path = [current]
            while previous[path[-1]] is not None:
                path.append(previous[path[-1]])
            return path[::-1]
        if previous[current] is not None and (here is not None or (members.get(current) and left not in members[current])):
            continue
        for nxt in graph.get(current, ()):
            if nxt not in previous:
                previous[nxt] = current
                queue.append(nxt)
    return None


def wire_ends_at(wires: list[dict[str, Any]], x: float, y: float, tol: float = 8.0) -> bool:
    for wire in wires:
        box = wire.get("bbox") or {}
        along, across = ("x", "y") if wire.get("orientation") == "h" else ("y", "x")
        value, other = (x, y) if along == "x" else (y, x)
        if not (box[f"{across}0"] - tol <= other <= box[f"{across}1"] + tol):
            continue
        if min(abs(value - box[f"{along}0"]), abs(value - box[f"{along}1"])) <= tol:
            return True
    return False


def path_mechanism(path: list[str], trace: dict[str, Any], structure: dict[str, Any]) -> str:
    kinds = {trace["edge_kind"].get(tuple(sorted(pair))) for pair in zip(path, path[1:], strict=False)}
    if "wire_gap_bridge" in kinds:
        return "wire_gap_bridge"
    if "terminal_terminal_bridge" in kinds:
        return "terminal_terminal_bridge"
    if "terminal_gap_bridge" in kinds or path[0] in trace["gap_bridged_stops"] or path[-1] in trace["gap_bridged_stops"]:
        return "terminal_gap_bridge"
    nodes = {n["id"]: n for n in structure["nodes"]}
    graph = trace["graph"]
    for end in (path[0], path[-1]):
        anchored = [g for g in graph.get(end, ()) if trace["edge_kind"].get(tuple(sorted((end, g)))) == "node_anchor"]
        if len({g.split("@")[0] for g in anchored if "@" in g}) == 1 and len(anchored) > 1:
            return "anchor_across_crossing"
    coords = trace["coords"]
    turns = []
    for before, here, after in zip(path, path[1:], path[2:], strict=False):
        node = nodes.get(here.removeprefix("node:")) if here.startswith("node:") and "@" not in here else None
        if not node or node.get("type") != "junction" or before not in coords or after not in coords:
            continue
        (x0, y0), (x1, y1), (x2, y2) = coords[before], coords[here], coords[after]
        horizontal_in = abs(x1 - x0) >= abs(y1 - y0)
        horizontal_out = abs(x2 - x1) >= abs(y2 - y1)
        if horizontal_in != horizontal_out:
            turns.append("junction_turn_tee" if wire_ends_at(structure["wires"], x1, y1) else "junction_turn_dot")
    if "junction_turn_dot" in turns:
        return "junction_turn_dot"
    if turns:
        return "junction_turn_tee"
    return "wire"


def covered_gap(structure: dict[str, Any], blocked: list[dict[str, float]], a: tuple[float, float], b: tuple[float, float]) -> float:
    """Longest stretch of the axis-aligned segment a-b that no detected wire covers, outside symbol boxes."""
    horizontal = abs(b[0] - a[0]) >= abs(b[1] - a[1])
    axis = (a[1] + b[1]) / 2 if horizontal else (a[0] + b[0]) / 2
    lo, hi = sorted((a[0], b[0]) if horizontal else (a[1], b[1]))
    if hi - lo < 1:
        return 0.0
    intervals = []
    for wire in structure["wires"]:
        box = wire["bbox"]
        if (wire["orientation"] == "h") != horizontal:
            continue
        across = ((box["y0"] + box["y1"]) / 2) if horizontal else ((box["x0"] + box["x1"]) / 2)
        if abs(across - axis) <= AXIS_TOLERANCE_PX + 3:
            intervals.append((box["x0"], box["x1"]) if horizontal else (box["y0"], box["y1"]))
    for box in blocked:
        inside = box["y0"] - 4 <= axis <= box["y1"] + 4 if horizontal else box["x0"] - 4 <= axis <= box["x1"] + 4
        if inside:
            intervals.append((box["x0"] - 4, box["x1"] + 4) if horizontal else (box["y0"] - 4, box["y1"] + 4))
    longest, cursor = 0.0, lo
    for start, end in sorted(intervals):
        if end < cursor:
            continue
        if start > cursor:
            longest = max(longest, min(start, hi) - cursor)
        cursor = max(cursor, end)
        if cursor >= hi:
            break
    return max(longest, hi - cursor if cursor < hi else 0.0)


def segment_gap(structure: dict[str, Any], blocked: list[dict[str, float]], p: tuple[float, float], q: tuple[float, float]) -> float:
    if abs(p[0] - q[0]) <= AXIS_TOLERANCE_PX or abs(p[1] - q[1]) <= AXIS_TOLERANCE_PX:
        return covered_gap(structure, blocked, p, q)
    elbows = ((q[0], p[1]), (p[0], q[1]))
    return min(max(covered_gap(structure, blocked, p, e), covered_gap(structure, blocked, e, q)) for e in elbows)


def analyze_drawing(drawing: dict[str, Any], pred: list[dict[str, Any]], structure: dict[str, Any]) -> dict[str, Any]:
    project = drawing["project"]
    width, height = int(project["image_width"]), int(project["image_height"])
    image = Image.open(io.BytesIO(drawing["image"])).convert("RGB")
    gold = gold_items(drawing)
    active = structure_predictions(structure, pred)
    scored = score_symbols(gold, active)
    g_sym, p_sym = scored["g_sym"], scored["p_sym"]
    pred_to_gold = {p_sym[pi]["structure_id"]: g_sym[gi]["ref"] for gi, pi, _ in scored["matches"]}
    refs = {g["ref"] for g in g_sym}
    matched_refs = set(pred_to_gold.values())

    gdsu, gmembers = gold_nodes(drawing, refs)
    gn = _pairs_of_groups(gdsu, gmembers)
    gdsu, gmembers = gold_nodes(drawing, refs, conventions=True)
    gold_node = {key: gdsu.find(key) for key in gmembers}
    gc = _pairs_of_groups(gdsu, gmembers)
    pdsu, pmembers = predicted_nodes(drawing, structure, pred_to_gold)
    pn = _pairs_of_groups(pdsu, pmembers)
    key_groups: dict[str, set[str]] = defaultdict(set)
    for terminal_id, key in pmembers.items():
        key_groups[key].add(pdsu.find(terminal_id))

    def ref_of(key: str) -> str:
        return key.split(":", 1)[0]

    fp: Counter = Counter()
    for a, b in pn - gn:
        if (a, b) in gc:
            fp["gold_convention"] += 1
        elif a not in gold_node or b not in gold_node:
            fp["gold_unwired_terminal"] += 1
        else:
            fp["merge_collision" if len(key_groups[a]) > 1 or len(key_groups[b]) > 1 else "merge"] += 1
    fn: Counter = Counter()
    split_pairs_by_node: Counter = Counter()
    for a, b in gn - pn:
        if (a, b) not in gc:
            fn["gold_convention"] += 1
        elif ref_of(a) not in matched_refs or ref_of(b) not in matched_refs:
            fn["symbol_missed"] += 1
        elif a not in key_groups or b not in key_groups:
            fn["terminal_missing"] += 1
        else:
            fn["split"] += 1
            split_pairs_by_node[gold_node[a]] += 1

    gold_points: dict[str, tuple[float, float]] = {}
    for s in project["symbols"]:
        cx, cy = s["cx"] * width, s["cy"] * height
        gold_points[s["ref"]] = (cx, cy)
        for t in s.get("terminals") or []:
            gold_points[endpoint_key(s["ref"], t["ref"])] = (t["tx"] * width, t["ty"] * height)
    blocked = [g["bbox"] for g in gold]
    segments_by_node: dict[str, list[tuple[tuple[float, float], tuple[float, float], float]]] = defaultdict(list)
    for conn in project["connections"]:
        if conn["from_symbol_ref"] == conn["to_symbol_ref"]:
            continue
        a = endpoint_key(conn["from_symbol_ref"], conn.get("from_terminal_ref"))
        b = endpoint_key(conn["to_symbol_ref"], conn.get("to_terminal_ref"))
        if a in gold_points and b in gold_points:
            p, q = gold_points[a], gold_points[b]
            segments_by_node[gdsu.find(a)].append((p, q, segment_gap(structure, blocked, p, q)))

    from_to = structure["from_to"]
    terminals = {t["id"]: t for t in from_to["terminals"]}
    split_causes: Counter = Counter()
    split_detail = []
    for node, pairs in split_pairs_by_node.items():
        gaps = [s for s in segments_by_node.get(node, []) if s[2] > WIRE_GAP_PX]
        member_terminals = [t for t, key in pmembers.items() if gold_node.get(key) == node]
        unlinked = [t for t in member_terminals if not terminals[t]["wire_ids"] and not terminals[t]["node_ids"]]
        cause = "wire_gap" if gaps else "unlinked_terminal" if unlinked else "graph_break"
        split_causes[cause] += pairs
        groups = sorted({pdsu.find(t) for t in member_terminals})
        split_detail.append({"cause": cause, "pairs": pairs, "gaps": [(p, q, round(g)) for p, q, g in gaps],
                             "members": [(terminals[t]["point"], groups.index(pdsu.find(t))) for t in member_terminals]})

    trace = trace_graph(structure, image)
    wired_refs = {c[f"{side}_symbol_ref"] for c in project["connections"] for side in ("from", "to")}
    links = [c for c in from_to["connections"]
             if not (c["from"]["symbol_id"] == c["to"]["symbol_id"] and c["from"].get("class_name") not in NODE_CLASSES)]

    def node_of(terminal_id: str) -> str | None:
        return gold_node.get(pmembers.get(terminal_id, ""))

    def hub_kind(terminal_id: str) -> str:
        terminal = terminals[terminal_id]
        ref = pred_to_gold.get(terminal["symbol_id"])
        status = "unmatched_detection" if ref is None else "gold_unwired_symbol" if ref not in wired_refs else "gold_unwired_terminal"
        return f"via_{status}:{terminal['class_name']}"

    # Terminals no gold node claims still join nodes in the scorer; a run of them touching two gold nodes is a hub.
    loose = DSU()
    for conn in links:
        a, b = conn["from"]["id"], conn["to"]["id"]
        if node_of(a) is None and node_of(b) is None:
            loose.union(a, b)
    hub_nodes: dict[str, set[str]] = defaultdict(set)
    hub_links: dict[str, list[str]] = defaultdict(list)
    hub_kinds: dict[str, Counter] = defaultdict(Counter)
    for conn in links:
        a, b = conn["from"]["id"], conn["to"]["id"]
        for end, other in ((a, b), (b, a)):
            if node_of(end) is None and node_of(other) is not None:
                hub = loose.find(end)
                hub_nodes[hub].add(node_of(other))
                hub_links[hub].append(conn["id"])
                hub_kinds[hub][hub_kind(end)] += 1
    hubs = []
    for hub, touched in hub_nodes.items():
        if len(touched) > 1:
            kind = hub_kinds[hub].most_common(1)[0][0]
            ends = [terminals[t]["point"] for t in loose.parent if loose.find(t) == hub] or [terminals[hub]["point"]]
            hubs.append({"kind": kind, "links": hub_links[hub], "gold_nodes": len(touched), "points": ends})
    bad_links = []
    for conn in links:
        left, right = conn["from"], conn["to"]
        ka, kb = pmembers.get(left["id"]), pmembers.get(right["id"])
        if ka is None or kb is None or ka not in gold_node or kb not in gold_node or gold_node[ka] == gold_node[kb]:
            continue
        path = link_path(trace, left["id"], right["id"])
        mechanism = path_mechanism(path, trace, structure) if path else "untraced"
        bad_links.append({"id": conn["id"], "mechanism": mechanism, "from": left["point"], "to": right["point"],
                          "path": [trace["coords"].get(n) for n in path or [] if n in trace["coords"]],
                          "keys": [ka, kb]})

    conv_fp = len(pn - gc)

    def fp_without(drop: set[str]) -> int:
        trimmed = {**structure, "from_to": {**from_to, "connections": [c for c in from_to["connections"] if c["id"] not in drop]}}
        return len(_pairs_of_groups(*predicted_nodes(drawing, trimmed, pred_to_gold)) - gc)

    owned: Counter = Counter()
    culprits = [(b["mechanism"], {b["id"]}) for b in bad_links] + [(h["kind"], set(h["links"])) for h in hubs]
    for name in sorted({name for name, _ in culprits}):
        owned[name] = conv_fp - fp_without(set().union(*(ids for n, ids in culprits if n == name)))
    owned["all_culprits"] = conv_fp - fp_without(set().union(*(ids for _, ids in culprits))) if culprits else 0

    return {
        "sheet": drawing["sheet"],
        "tp": len(gn & pn), "fp": len(pn - gn), "fn": len(gn - pn), "conv_fp": conv_fp,
        "fp_causes": dict(fp), "fn_causes": dict(fn), "split_causes": dict(split_causes),
        "bad_link_mechanisms": dict(Counter(b["mechanism"] for b in bad_links) + Counter(h["kind"] for h in hubs)),
        "fp_owned_by_mechanism": dict(owned),
        "bad_links": bad_links, "hubs": hubs, "splits": split_detail,
        "gold_wired_terminals": len(gold_node),
        "unwired_mapped_terminals": sorted({k for k in pmembers.values() if k not in gold_node}),
        "gold_points": {k: gold_points[k] for k in pmembers.values() if k in gold_points and k not in gold_node},
    }


def draw_overlay(drawing: dict[str, Any], structure: dict[str, Any], row: dict[str, Any], path: Path) -> None:
    image = Image.open(io.BytesIO(drawing["image"])).convert("RGB")
    faded = Image.blend(image, Image.new("RGB", image.size, "white"), 0.45)
    draw = ImageDraw.Draw(faded)
    for wire in structure["wires"]:
        (x0, y0), (x1, y1) = wire["polyline"][0], wire["polyline"][-1]
        draw.line((x0, y0, x1, y1), fill=(120, 160, 255), width=1)
    palette = [(255, 140, 0), (0, 170, 170), (150, 0, 220), (90, 90, 90), (200, 160, 0), (0, 90, 200)]
    for split in sorted(row["splits"], key=lambda s: -s["pairs"])[:3]:
        for (x, y), group in split["members"]:
            color = palette[group % len(palette)]
            draw.ellipse((x - 6, y - 6, x + 6, y + 6), outline=color, width=3)
            draw.text((x + 6, y + 2), str(group), fill=color)
        for p, q, _gap in split["gaps"]:
            draw.line((*p, *q), fill=(200, 0, 200), width=3)
    for link in row["bad_links"]:
        points = link["path"] or [link["from"], link["to"]]
        draw.line([tuple(p) for p in points], fill=(230, 0, 0), width=3)
        x, y = link["from"]
        draw.text((x + 4, y - 12), link["mechanism"], fill=(230, 0, 0))
    for hub in row["hubs"]:
        for x, y in hub["points"]:
            draw.line((x - 7, y - 7, x + 7, y + 7), fill=(200, 0, 120), width=3)
            draw.line((x - 7, y + 7, x + 7, y - 7), fill=(200, 0, 120), width=3)
        x, y = hub["points"][0]
        draw.text((x + 8, y - 14), hub["kind"].removeprefix("via_"), fill=(200, 0, 120))
    for x, y in row["gold_points"].values():
        draw.rectangle((x - 4, y - 4, x + 4, y + 4), outline=(0, 150, 0), width=2)
    path.parent.mkdir(parents=True, exist_ok=True)
    faded.save(path)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--gold", type=Path, nargs="+", required=True)
    parser.add_argument("--pred-run", type=Path)
    parser.add_argument("--work", type=Path, required=True, help="eval_wiring_cv work dir holding <condition>/<SHEET>.json")
    parser.add_argument("--condition", choices=["oracle", "pred"], default="pred")
    parser.add_argument("--split", choices=["tune", "report", "all"], default="tune")
    parser.add_argument("--out", type=Path)
    parser.add_argument("--overlay-dir", type=Path)
    parser.add_argument("--overlay-sheets", nargs="*", default=[])
    args = parser.parse_args()

    _keys, drawings = load_gold(args.gold)
    sheets = sorted(d["sheet"] for d in drawings)
    chosen = {"tune": set(sheets[0::2]), "report": set(sheets[1::2]), "all": set(sheets)}[args.split]
    rows = []
    for drawing in drawings:
        if drawing["sheet"] not in chosen:
            continue
        structure = json.loads((args.work / args.condition / f"{drawing['sheet']}.json").read_text(encoding="utf-8"))
        pred = oracle_predictions(drawing) if args.condition == "oracle" else file_predictions(args.pred_run, drawing)
        row = analyze_drawing(drawing, pred, structure)
        rows.append(row)
        if args.overlay_dir and drawing["sheet"] in args.overlay_sheets:
            draw_overlay(drawing, structure, row, args.overlay_dir / f"{args.condition}_{drawing['sheet']}.png")
    total = {name: Counter() for name in ("fp_causes", "fn_causes", "split_causes", "bad_link_mechanisms", "fp_owned_by_mechanism")}
    for row in rows:
        for name, counter in total.items():
            counter.update(row[name])
    summary = {
        "condition": args.condition, "split": args.split, "drawings": len(rows),
        "tp": sum(r["tp"] for r in rows), "fp": sum(r["fp"] for r in rows), "fn": sum(r["fn"] for r in rows),
        "conv_fp": sum(r["conv_fp"] for r in rows),
        **{name: dict(counter.most_common()) for name, counter in total.items()},
        "per_sheet": {r["sheet"]: {k: r[k] for k in ("tp", "fp", "fn", "fp_causes", "fn_causes", "split_causes", "bad_link_mechanisms")}
                      for r in rows},
    }
    if args.out:
        args.out.write_text(json.dumps({"summary": summary, "rows": rows}, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({k: v for k, v in summary.items() if k != "per_sheet"}, ensure_ascii=False, indent=1))
    for sheet, r in summary["per_sheet"].items():
        print(sheet, f"tp={r['tp']} fp={r['fp']} fn={r['fn']}", r["fp_causes"], r["fn_causes"], r["bad_link_mechanisms"])


if __name__ == "__main__":
    main()
