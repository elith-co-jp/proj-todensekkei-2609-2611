"""How much hand entry in the editor the AI import replaces, scored on the gold drawings.

Each drawing's structure (eval_wiring_cv.py --work, built from cross-validated detections) is imported
into an empty editor with the product planner (services.ai_candidates), then compared with the gold
annotation people entered by hand in the same editor.

- connections: an imported connection maps each end to its matched gold symbol (class-agnostic,
  holdout_cv.match). Otherwise-unmatched unique destination/connector labels and an unambiguous same-class contact
  whose centres mutually fall inside the boxes recover known annotation-box differences; symbol detection itself
  remains IoU-scored.
  The endpoint then maps to that symbol's nearest gold terminal. It is a hit when a gold connection joins
  the same two gold terminals, in either direction; each gold connection is hit at most once, so a
  duplicate imported connection counts against precision. Gold wire-bend markers (other/分岐点) are
  contracted: annotators boxed them only to route wires. Imported connections touching a gold symbol
  that gold leaves unwired (every relay coil) are not judged and are reported separately.
  Misses and false hits are split by cause (fn_reasons / fp_reasons).
- net_pairs: the same judged connections as electrical nets. Pairs of gold terminals that share a net in
  the import against pairs that share a gold net, so a different route through the same net still counts.
- wire_nodes: the same question per wire, linear in the number of wires. A node is a set of terminals joined
  by wires; links between two terminals of one symbol (a contact's two ends, a card's + to -) do not join nodes,
  a wire inside a panel frame does (DD-0022). A gold wire
  is recalled when the import puts its two terminals in one node; an imported wire is right when gold puts its
  two terminals in one node. Two gold habits are reported apart. Terminals placed on a region-sized box
  (where wires cross the dashed frame of a 2RAXP panel) split one wire into two; the conv recall contracts
  them like wire bends, so contact - frame terminal - dot counts as the one wire contact - dot. Wires drawn
  straight through a symbol gold leaves unwired (relay coils) are left out of the conv recall; a terminal block
  boxed on top of a frame terminal (no terminals of its own, the wires go to the frame terminal) is not such a symbol.
- connector names: gold connectors whose label carries pin numbers (eval_wiring_cv.gold_terminal_numbers)
  against the imported terminal name of the matched symbol, read as DEVICE-NUMBER tokens.
- coils: on gold that follows the coil wiring rule (two terminals per coil, analysis/tools/coil_wiring_gold.py),
  each wired coil is complete when every gold wire on it is recalled, and shorted when the import puts its two
  terminals in one node (the simulation could never energize it).
- rerun: importing again into the imported result must add nothing.
- onto_gold: what the import would add to the finished gold annotation.

Example:
    python analysis/tools/eval_editor_import.py --gold a.zip b.zip --structures runs/wiring/pred
"""

from __future__ import annotations

import argparse
import itertools
import json
import math
import re
import sys
import unicodedata
from collections import Counter
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from analyze_annotation_graph import endpoint_key, parse_member  # noqa: E402
from eval_wiring_cv import gold_terminal_numbers  # noqa: E402
from eval_split import tune_sheets  # noqa: E402
from holdout_cv import gold_items, is_wire_bend, load_gold, match, norm_label, prf, score_symbols, to_box  # noqa: E402

from services.ai_candidates import from_structure, plan_import  # noqa: E402
from services.circuit_model import joins_inside_symbol  # noqa: E402
from services.coil_wiring import COIL_CLASSES  # noqa: E402
from services.detection_rules import detection_kind  # noqa: E402
from symbol_class_policy import canonical_symbol_class_key  # noqa: E402

def apply_plan(symbols: list[dict], connections: list[dict], plan: Any) -> tuple[list[dict], list[dict]]:
    """The editor's merge (frontend mergeAiImport): new terminals on existing symbols, then new symbols and connections."""
    extra: dict[str, list[dict]] = {}
    for t in plan.terminals:
        extra.setdefault(t["symbol_ref"], []).append({k: v for k, v in t.items() if k != "symbol_ref"})
    merged = [{**s, "terminals": [*(s.get("terminals") or []), *extra.get(s["ref"], [])]} for s in symbols]
    return merged + plan.symbols, connections + plan.connections


def inside_aliases(project: dict[str, Any]) -> dict[str, str]:
    """Gold terminals of one symbol joined by a wire inside it (a panel frame's bus, a border crossing) are one
    electrical point: each maps to one representative key. Contact ends and a card's + to - stay apart (DD-0022)."""
    width, height = float(project["image_width"]), float(project["image_height"])
    symbols = {s["ref"]: s for s in project["symbols"]}
    parent: dict[str, str] = {}

    def find(x: str) -> str:
        while parent.setdefault(x, x) != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for c in project["connections"]:
        ref = c["from_symbol_ref"]
        symbol = symbols.get(ref)
        if ref != c["to_symbol_ref"] or symbol is None or symbol["class_key"] != "other":
            continue
        if joins_inside_symbol(symbol, c.get("from_terminal_ref"), c.get("to_terminal_ref"), width, height):
            a, b = endpoint_key(ref, c["from_terminal_ref"]), endpoint_key(ref, c["to_terminal_ref"])
            parent[find(a)] = find(b)
    return {key: find(key) for key in parent if find(key) != key}


def gold_edges(project: dict[str, Any]) -> set[frozenset[str]]:
    """Gold connections as unordered terminal pairs, with wire-bend markers contracted away.

    An end without a terminal on a symbol that has exactly one terminal is that terminal. Terminals joined by a wire
    inside their symbol are one key (inside_aliases).
    """
    alias = inside_aliases(project)
    bends = {s["ref"] for s in project["symbols"] if is_wire_bend(s)}
    single = {s["ref"]: s["terminals"][0]["ref"] for s in project["symbols"] if len(s.get("terminals") or []) == 1}
    neighbours: dict[str, set[str]] = {}

    def node(symbol_ref: str, terminal_ref: str | None) -> str:
        if symbol_ref in bends:
            return symbol_ref
        key = endpoint_key(symbol_ref, terminal_ref or single.get(symbol_ref))
        return alias.get(key, key)

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


def gold_nets(edges: set[frozenset[str]]) -> dict[str, str]:
    parent: dict[str, str] = {}

    def find(x: str) -> str:
        while parent.setdefault(x, x) != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for edge in edges:
        a, b = tuple(edge)
        parent[find(a)] = find(b)
    return {key: find(key) for key in parent}


def net_pairs(edges: Iterable[frozenset[str]]) -> set[frozenset[str]]:
    groups: dict[str, set[str]] = {}
    for key, root in gold_nets(set(edges)).items():
        groups.setdefault(root, set()).add(key)
    return {frozenset(p) for keys in groups.values() for p in itertools.combinations(sorted(keys), 2)}


def imported_items(symbols: list[dict], width: int, height: int) -> list[dict]:
    return [
        {"ref": s["ref"], "class_name": s["class_key"], "label": s.get("label") or "", "kind": detection_kind(s["w"], s["h"]),
         "bbox": to_box(s["cx"], s["cy"], s["w"], s["h"], width, height)}
        for s in symbols
    ]


_DESTINATION_LABEL = re.compile(r"[A-Z]{1,3}\d{5}(?:-\d{1,2})?へ")
_CONNECTOR_LABEL = re.compile(r"(?=(?:.*\d){2})[A-Z0-9-]{5,}")
_CONNECTOR_TOKEN = re.compile(r"(?:^|[^A-Z0-9])(?:CN|TB)\d+(?:$|[^A-Z0-9])")


def match_connection_symbols(gold: list[dict[str, Any]], pred: list[dict[str, Any]]) -> list[tuple[int, int, float]]:
    """Match symbols used as connection endpoints.

    Box IoU remains the primary rule. A destination tag can vary in height when OCR/detection includes surrounding
    print even though the imported tag has the same sheet reference and wire terminal. For otherwise-unmatched
    ``other`` tags, accept only a unique exact destination label whose two box centres lie inside each other's box.
    Apply the same fallback to a uniquely named connector whose predicted box is slightly shorter than the annotation.
    This does not change symbol detection scoring and cannot match an empty or generic label.
    """
    pairs = match(gold, pred, class_aware=False)
    used_gold = {gi for gi, _, _ in pairs}
    used_pred = {pi for _, pi, _ in pairs}
    gold_by_label: dict[tuple[str, str], list[int]] = {}
    pred_by_label: dict[tuple[str, str], list[int]] = {}

    def stable_label(item: dict[str, Any]) -> tuple[str, str] | None:
        raw_label = str(item.get("label", ""))
        label = norm_label(raw_label)
        class_name = canonical_symbol_class_key(str(item.get("class_name", "")), {"connector", "other"})
        if class_name == "other" and _DESTINATION_LABEL.fullmatch(label) is not None:
            return class_name, label
        connector_text = unicodedata.normalize("NFKC", raw_label).upper()
        if class_name == "connector" and _CONNECTOR_TOKEN.search(connector_text) is not None \
                and _CONNECTOR_LABEL.fullmatch(label) is not None:
            return class_name, label
        return None

    for index, item in enumerate(gold):
        if index not in used_gold and (label := stable_label(item)) is not None:
            gold_by_label.setdefault(label, []).append(index)
    for index, item in enumerate(pred):
        if index not in used_pred and (label := stable_label(item)) is not None:
            pred_by_label.setdefault(label, []).append(index)

    def centre(box: dict[str, float]) -> tuple[float, float]:
        return (box["x0"] + box["x1"]) / 2, (box["y0"] + box["y1"]) / 2

    def contains(box: dict[str, float], point: tuple[float, float]) -> bool:
        return box["x0"] <= point[0] <= box["x1"] and box["y0"] <= point[1] <= box["y1"]

    for label in sorted(gold_by_label.keys() & pred_by_label.keys()):
        gold_indexes, pred_indexes = gold_by_label[label], pred_by_label[label]
        if len(gold_indexes) != 1 or len(pred_indexes) != 1:
            continue
        gi, pi = gold_indexes[0], pred_indexes[0]
        if contains(gold[gi]["bbox"], centre(pred[pi]["bbox"])) and contains(pred[pi]["bbox"], centre(gold[gi]["bbox"])):
            pairs.append((gi, pi, 0.0))

    # A detector box can tightly contain the annotated contact yet miss the 0.5 IoU threshold solely because
    # one box is wider (ER21246).  Recover only an unambiguous same-class pair with mutually contained centres;
    # overlapping stacks with two possible contacts remain unmatched rather than guessed.
    used_gold = {gi for gi, _, _ in pairs}
    used_pred = {pi for _, pi, _ in pairs}
    spatial = [
        (gi, pi)
        for gi, g in enumerate(gold) if gi not in used_gold
        for pi, p in enumerate(pred) if pi not in used_pred
        if g["class_name"] == p["class_name"] and g["class_name"] in {"contact_a", "contact_b"}
        and math.dist(centre(g["bbox"]), centre(p["bbox"])) <= 8.0
        and contains(g["bbox"], centre(p["bbox"]))
        and contains(p["bbox"], centre(g["bbox"]))
    ]
    gold_candidates = Counter(gi for gi, _ in spatial)
    pred_candidates = Counter(pi for _, pi in spatial)
    pairs.extend((gi, pi, 0.0) for gi, pi in spatial if gold_candidates[gi] == pred_candidates[pi] == 1)
    return pairs


def name_pairs(name: str) -> tuple[list[str], list[tuple[str, str]]]:
    numbers, pairs = [], []
    for token in name.split():
        device, _, number = token.rpartition("-")
        if number.isdigit():
            numbers.append(number)
            if device:
                pairs.append((device, number))
    return numbers, pairs


def _crosses(p: tuple[float, float], q: tuple[float, float], box: dict[str, float], inset: float = 2.0) -> bool:
    steps = max(2, int(max(abs(q[0] - p[0]), abs(q[1] - p[1])) / 2))
    for i in range(1, steps):
        x, y = p[0] + (q[0] - p[0]) * i / steps, p[1] + (q[1] - p[1]) * i / steps
        if box["x0"] + inset < x < box["x1"] - inset and box["y0"] + inset < y < box["y1"] - inset:
            return True
    return False


def _contract(edges: set[frozenset[str]], through: set[str]) -> set[frozenset[str]]:
    """Drop the keys in through and join their neighbours directly, as gold_edges does for wire bends."""
    neighbours: dict[str, set[str]] = {}
    for edge in edges:
        a, b = tuple(edge)
        neighbours.setdefault(a, set()).add(b)
        neighbours.setdefault(b, set()).add(a)
    for key in through:
        around = neighbours.pop(key, set()) - {key}
        for n in around:
            neighbours[n].discard(key)
            neighbours[n].update(around - {n})
    return {frozenset((a, b)) for a, ns in neighbours.items() for b in ns if a != b}


def wire_miss_detail(
    edge: frozenset[str], gold_pos: dict[str, tuple[float, float]], gold_symbols: dict[str, dict[str, Any]],
    matched_gold_refs: set[str], ai_of_gold: dict[str, set[str]],
    imported_of_gold: dict[str, list[dict[str, Any]]] | None = None,
) -> dict[str, Any]:
    """Explain the first failed stage for one unrecalled gold wire without changing scoring."""
    keys = sorted(edge)
    endpoints = []
    for key in keys:
        symbol_ref, _terminal_ref = parse_member(key)
        symbol = gold_symbols[symbol_ref]
        endpoints.append({
            "gold_key": key,
            "class_key": symbol["class_key"],
            "label": symbol.get("label") or "",
            "symbol_matched": symbol_ref in matched_gold_refs,
            "terminal_mapped": key in ai_of_gold,
            "ai_nodes": sorted(ai_of_gold.get(key, set())),
            "mapped_imported": (imported_of_gold or {}).get(key, []),
        })
    if not all(endpoint["symbol_matched"] for endpoint in endpoints):
        reason = "symbol_unmatched"
    elif not all(endpoint["terminal_mapped"] for endpoint in endpoints):
        reason = "terminal_unmapped"
    else:
        reason = "different_ai_nodes"
    a, b = (gold_pos.get(key) for key in keys)
    geometry = None
    if a is not None and b is not None:
        dx, dy = abs(a[0] - b[0]), abs(a[1] - b[1])
        geometry = {
            "dx": round(dx, 1), "dy": round(dy, 1), "distance": round(math.hypot(dx, dy), 1),
            "orientation": "vertical" if dx <= 8 else "horizontal" if dy <= 8 else "diagonal",
            "alignment_error": round(min(dx, dy), 1),
        }
    return {"reason": reason, "endpoints": endpoints, "geometry": geometry}


def wire_nodes(
    project: dict[str, Any], edges: set[frozenset[str]], symbols: list[dict], connections: list[dict],
    key_of: Callable[[str, str | None], str | None], width: int, height: int, matched_gold_refs: set[str],
) -> dict[str, Any]:
    """Per-wire electrical agreement (module docstring: wire_nodes). key_of maps an imported terminal to a gold key."""
    gold_pos = {endpoint_key(s["ref"], t["ref"]): (t["tx"] * width, t["ty"] * height)
                for s in project["symbols"] for t in (s.get("terminals") or [])}
    wired = {parse_member(k)[0] for e in edges for k in e}
    region_refs = {s["ref"] for s in project["symbols"] if detection_kind(s["w"], s["h"]) == "region"}
    frame_points = [gold_pos[endpoint_key(s["ref"], t["ref"])] for s in project["symbols"]
                    if s["ref"] in region_refs or s["class_key"] == "other" for t in (s.get("terminals") or [])]

    def on_frame_terminal(box: dict[str, float]) -> bool:
        return any(box["x0"] - 2 <= x <= box["x1"] + 2 and box["y0"] - 2 <= y <= box["y1"] + 2 for x, y in frame_points)

    unwired_boxes = [box for s in project["symbols"]
                     if s["ref"] not in wired and not is_wire_bend(s) and detection_kind(s["w"], s["h"]) == "symbol"
                     and not on_frame_terminal(box := to_box(s["cx"], s["cy"], s["w"], s["h"], width, height))]
    gold_wires = {e for e in edges if len({parse_member(k)[0] for k in e}) == 2}
    region_keys = {k for e in gold_wires for k in e if parse_member(k)[0] in region_refs}
    conv_wires = _contract(gold_wires, region_keys)
    gold_node = gold_nets(gold_wires)
    ai_wires = [c for c in connections if c["from_symbol_ref"] != c["to_symbol_ref"]]
    ai_node = gold_nets({frozenset((endpoint_key(c["from_symbol_ref"], c["from_terminal_ref"]),
                                    endpoint_key(c["to_symbol_ref"], c["to_terminal_ref"]))) for c in ai_wires})
    ai_of_gold: dict[str, set[str]] = {}
    imported_of_gold: dict[str, list[dict[str, Any]]] = {}
    for s in symbols:
        for t in s["terminals"]:
            key = key_of(s["ref"], t["ref"])
            if key is not None:
                root = ai_node.get(endpoint_key(s["ref"], t["ref"]))
                ai_of_gold.setdefault(key, set()).add(root or endpoint_key(s["ref"], t["ref"]))
                imported_of_gold.setdefault(key, []).append({
                    "symbol_ref": s["ref"], "class_key": s["class_key"], "label": s.get("label") or "",
                    "terminal_ref": t["ref"], "terminal_name": t.get("name") or "",
                    "point": [round(t["tx"] * width, 1), round(t["ty"] * height, 1)],
                })
    out: dict[str, Any] = {
        "recall": Counter(), "precision": Counter(), "missed": [], "wrong": [], "miss_reasons": Counter(),
    }
    gold_symbols = {symbol["ref"]: symbol for symbol in project["symbols"]}
    imported_pos = {endpoint_key(s["ref"], t["ref"]): (t["tx"] * width, t["ty"] * height)
                    for s in symbols for t in s["terminals"]}

    def recalled(edge: frozenset[str]) -> bool:
        a, b = tuple(edge)
        return bool(ai_of_gold.get(a, set()) & ai_of_gold.get(b, set()))

    for edge in gold_wires:
        out["recall"].update({"gold": 1, "hit": int(recalled(edge)),
                              "gold_region_terminal": int(bool(edge & region_keys))})
    out["coils"] = Counter()
    out["coil_details"] = []
    for s in project["symbols"]:
        terms = [endpoint_key(s["ref"], t["ref"]) for t in s.get("terminals") or []]
        on_coil = [e for e in gold_wires if s["ref"] in {parse_member(k)[0] for k in e}]
        if s["class_key"] not in COIL_CLASSES or len(terms) != 2 or not on_coil:
            continue
        complete = all(recalled(e) for e in on_coil)
        shorted = bool(ai_of_gold.get(terms[0], set()) & ai_of_gold.get(terms[1], set()))
        out["coils"].update({"gold": 1, "complete": int(complete), "shorted": int(shorted),
                             "usable": int(complete and not shorted)})
        if not complete or shorted:
            out["coil_details"].append({
                "ref": s["ref"], "label": s.get("label") or "", "complete": complete, "shorted": shorted,
                "box": [round(v) for v in (s["cx"] * width, s["cy"] * height, s["w"] * width, s["h"] * height)],
                "imported_terminals": [k for k in terms if k in ai_of_gold],
                "missed": [sorted(e) for e in on_coil if not recalled(e)]})
    for edge in conv_wires:
        a, b = sorted(edge)
        if a in gold_pos and b in gold_pos and any(_crosses(gold_pos[a], gold_pos[b], box) for box in unwired_boxes):
            out["recall"]["conv_through_unwired"] += 1
            continue
        hit = recalled(edge)
        out["recall"].update({"conv_gold": 1, "conv_hit": int(hit)})
        if not hit:
            detail = wire_miss_detail(
                edge, gold_pos, gold_symbols, matched_gold_refs, ai_of_gold, imported_of_gold,
            )
            out["miss_reasons"][detail["reason"]] += 1
            out["missed"].append({"gold": [a, b], "points": [gold_pos.get(a), gold_pos.get(b)], **detail})
    for c in ai_wires:
        a, b = key_of(c["from_symbol_ref"], c["from_terminal_ref"]), key_of(c["to_symbol_ref"], c["to_terminal_ref"])
        if a is None or b is None or not {parse_member(a)[0], parse_member(b)[0]} <= wired:
            out["precision"]["not_judged"] += 1
            continue
        right = a == b or (gold_node.get(a) is not None and gold_node.get(a) == gold_node.get(b))
        out["precision"].update({"judged": 1, "right": int(right)})
        if not right:
            out["wrong"].append({"gold": [a, b], "points": [
                imported_pos.get(endpoint_key(c["from_symbol_ref"], c["from_terminal_ref"])),
                imported_pos.get(endpoint_key(c["to_symbol_ref"], c["to_terminal_ref"]))]})
    return out


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
    to_gold = {p_sym[pi]["ref"]: g_sym[gi]["ref"] for gi, pi, _ in match_connection_symbols(g_sym, p_sym)}
    gold_symbols = {s["ref"]: s for s in project["symbols"]}
    position = {t["ref"]: (t["tx"] * width, t["ty"] * height) for s in symbols for t in s["terminals"]}

    alias = inside_aliases(project)

    def gold_key(symbol_ref: str, terminal_ref: str | None) -> str | None:
        gold_ref = to_gold.get(symbol_ref)
        if gold_ref is None:
            return None
        terminals = gold_symbols[gold_ref].get("terminals") or []
        if not terminals or terminal_ref is None:
            return gold_ref
        px, py = position[terminal_ref]
        nearest = min(terminals, key=lambda t: (t["tx"] * width - px) ** 2 + (t["ty"] * height - py) ** 2)
        key = endpoint_key(gold_ref, nearest["ref"])
        return alias.get(key, key)

    edges = gold_edges(project)
    wired = {parse_member(k)[0] for e in edges for k in e}
    nets = gold_nets(edges)
    ai_neighbours: dict[str, set[str]] = {}
    fp: Counter = Counter()
    not_judged = 0
    hit: set[frozenset[str]] = set()
    joined: list[frozenset[str]] = []
    for c in connections:
        ai_neighbours.setdefault(c["from_symbol_ref"], set()).add(c["to_symbol_ref"])
        ai_neighbours.setdefault(c["to_symbol_ref"], set()).add(c["from_symbol_ref"])
        a, b = gold_key(c["from_symbol_ref"], c["from_terminal_ref"]), gold_key(c["to_symbol_ref"], c["to_terminal_ref"])
        if a is not None and b is not None and not {parse_member(a)[0], parse_member(b)[0]} <= wired:
            not_judged += 1
            continue
        pair = frozenset((a, b)) if a is not None and b is not None and a != b else None
        if pair is not None:
            joined.append(pair)
        if pair in edges and pair not in hit:
            hit.add(pair)
        elif a is None or b is None:
            fp["unmatched_symbol"] += 1
        elif a == b:
            fp["same_gold_terminal"] += 1
        elif pair in edges:
            fp["duplicate"] += 1
        elif nets.get(a) is not None and nets.get(a) == nets.get(b):
            fp["same_net_other_route"] += 1
        else:
            fp["other_net"] += 1
    from_gold = {g: p for p, g in to_gold.items()}
    unwired = {p for p, g in to_gold.items() if g not in wired}
    fn: Counter = Counter()
    for edge in edges - hit:
        refs = [parse_member(k)[0] for k in edge]
        if any(detection_kind(gold_symbols[r]["w"], gold_symbols[r]["h"]) == "region" for r in refs):
            fn["region_symbol"] += 1
        elif any(r not in from_gold for r in refs):
            fn["missed_symbol"] += 1
        elif ai_neighbours.get(from_gold[refs[0]], set()) & ai_neighbours.get(from_gold[refs[-1]], set()) & unwired:
            fn["through_unwired_symbol"] += 1
        else:
            fn["route"] += 1
    gold_net_pairs, import_net_pairs = net_pairs(edges), net_pairs(joined)
    nodes = wire_nodes(project, edges, symbols, connections, gold_key, width, height, set(from_gold))
    names: Counter = Counter()
    by_ref = {s["ref"]: s for s in symbols}
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
        "connections": {"tp": len(hit), "fp": sum(fp.values()), "fn": len(edges - hit)},
        "net_pairs": {"tp": len(gold_net_pairs & import_net_pairs), "fp": len(import_net_pairs - gold_net_pairs),
                      "fn": len(gold_net_pairs - import_net_pairs)},
        "wire_nodes": {"recall": dict(nodes["recall"]), "precision": dict(nodes["precision"])},
        "coils": dict(nodes["coils"]),
        "coil_details": nodes["coil_details"],
        "wire_errors": {"missed": nodes["missed"], "wrong": nodes["wrong"]},
        "wire_miss_reasons": dict(nodes["miss_reasons"]),
        "not_judged": not_judged,
        "fp_reasons": dict(fp),
        "fn_reasons": dict(fn),
        "imported": {"symbols": len(symbols), "terminals": len(position), "connections": len(connections)},
        "skipped": candidates.skipped,
        "detection": dict(detection["aware"]),
        "class_agree": detection["type_correct"], "class_scored": detection["type_total"],
        "label_agree": detection["label_correct"], "label_scored": detection["label_total"],
        "names": dict(names),
        "rerun_added": len(again.symbols) + len(again.terminals) + len(again.connections),
        "onto_gold": {"symbols": len(onto.symbols), "terminals": len(onto.terminals), "connections": len(onto.connections)},
    }


def aggregate(rows: list[dict[str, Any]]) -> dict[str, Any]:
    tot: Counter = Counter()
    nets: Counter = Counter()
    det: Counter = Counter()
    names: Counter = Counter()
    imported: Counter = Counter()
    onto: Counter = Counter()
    skipped: Counter = Counter()
    fp: Counter = Counter()
    fn: Counter = Counter()
    rec: Counter = Counter()
    pre: Counter = Counter()
    coils: Counter = Counter()
    wire_miss_reasons: Counter = Counter()
    for r in rows:
        tot.update(r["connections"])
        nets.update(r["net_pairs"])
        rec.update(r["wire_nodes"]["recall"])
        pre.update(r["wire_nodes"]["precision"])
        coils.update(r.get("coils", {}))
        wire_miss_reasons.update(r.get("wire_miss_reasons", {}))
        det.update(r["detection"])
        names.update(r["names"])
        imported.update(r["imported"])
        onto.update(r["onto_gold"])
        skipped.update(r["skipped"])
        fp.update(r["fp_reasons"])
        fn.update(r["fn_reasons"])
    class_scored = sum(r["class_scored"] for r in rows)
    label_scored = sum(r["label_scored"] for r in rows)

    def rate(n: int, d: int) -> float | None:
        return round(n / d, 4) if d else None

    wire_nodes_summary = {
        "recall": rate(rec["hit"], rec["gold"]), "precision": rate(pre["right"], pre["judged"]),
        "gold_wires": rec["gold"], "judged_imported": pre["judged"], "not_judged_imported": pre["not_judged"],
        "wrong_imported": pre["judged"] - pre["right"],
        "gold_wires_on_region_terminals": rec["gold_region_terminal"],
        "conv_recall": rate(rec["conv_hit"], rec["conv_gold"]),
        "conv_hit": rec["conv_hit"], "conv_gold_wires": rec["conv_gold"],
        "conv_through_unwired": rec["conv_through_unwired"],
    }

    return {
        "drawings": len(rows),
        "connections": prf(tot["tp"], tot["fp"], tot["fn"]),
        "gold_connections": tot["tp"] + tot["fn"],
        "net_pairs": prf(nets["tp"], nets["fp"], nets["fn"]),
        "wire_nodes": wire_nodes_summary,
        "wire_miss_reasons": dict(wire_miss_reasons.most_common()),
        "coils": {"gold": coils["gold"], "complete": rate(coils["complete"], coils["gold"]),
                  "shorted": coils["shorted"], "usable": rate(coils["usable"], coils["gold"])},
        "not_judged": sum(r["not_judged"] for r in rows),
        "fp_reasons": dict(fp.most_common()),
        "fn_reasons": dict(fn.most_common()),
        "imported": dict(imported),
        "skipped": dict(skipped),
        "symbol_detection": prf(det["tp"], det["fp"], det["fn"]),
        "class_agreement": {"scored": class_scored, "agree": rate(sum(r["class_agree"] for r in rows), class_scored)},
        "label_agreement": {"scored": label_scored, "agree": rate(sum(r["label_agree"] for r in rows), label_scored)},
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
    tune = tune_sheets(d["sheet"] for d in drawings)
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
        c, n, net = a["connections"], a["connector_names"], a["net_pairs"]
        print(f"  {part:6s} drawings={a['drawings']} connections f1={c['f1']} p={c['precision']} r={c['recall']}"
              f" tp={c['tp']} fp={c['fp']} fn={c['fn']} gold={a['gold_connections']} not_judged={a['not_judged']}"
              f" | net_pairs f1={net['f1']} p={net['precision']} r={net['recall']} tp={net['tp']} fp={net['fp']} fn={net['fn']}"
              f"\n         wire_nodes {a['wire_nodes']}"
              f"\n         coils {a['coils']}"
              f" | names numbers={n['numbers_agree']} pairs={n['pairs_agree']} scored={n['scored']}/{n['gold']}"
              f" | symbols f1={a['symbol_detection']['f1']} class={a['class_agreement']['agree']}"
              f" label={a['label_agreement']['agree']}"
              f" | imported={a['imported']} rerun_added={a['rerun_added']} onto_gold={a['onto_gold_added']}"
              f"\n         fp={a['fp_reasons']} fn={a['fn_reasons']}")


if __name__ == "__main__":
    main()
