"""Wiring accuracy on the gold drawings, for gold boxes (oracle) and cross-validated detections.

Two connection metrics are printed side by side:

- symbol_pairs: holdout_cv.gold_pairs / predicted_pairs. Gold nets include each contact's internal
  T1-T2 link, so most drawings form one net and merging everything already scores high.
- node_pairs: electrical nodes formed by wires only. Gold drops its same-symbol links; the prediction
  drops same-symbol links of device classes. Each predicted terminal is mapped to the nearest gold
  terminal of its matched gold symbol, and pairs of gold terminals sharing a node are compared.
  Over-merging costs precision here, so it guards symbol_pairs against wins that only merge nets.

Example:
    python analysis/tools/eval_wiring_cv.py --gold a.zip b.zip --pred-run runs/cv_baseline --work runs/wiring
"""

from __future__ import annotations

import argparse
import hashlib
import importlib
import itertools
import json
import pickle
import re
import sys
import time
import unicodedata
from collections import Counter
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from analyze_annotation_graph import endpoint_key  # noqa: E402
from holdout_cv import gold_items, gold_pairs, load_gold, predicted_pairs, prf, run_structure, score_symbols, to_box  # noqa: E402

NODE_CLASSES = {"connector", "junction", "terminal", "power_bus"}
SCORED_CLASSES = ("junction", "connector")
ROLE_OF_CLASS = {"junction": "junction", "connector": "terminal"}
# Same-symbol gold links shorter than this are a wire crossing a box border; card + to - links are about 60 px.
BORDER_CROSSING_PX = 30.0


def gold_terminal_numbers(label: str) -> tuple[list[str], list[tuple[str, str]] | None]:
    """Pin numbers in a gold connector label, and name-number pairs when they line up.

    Labels list the left column (device names, top to bottom) and then the right column
    (pin numbers); a number before the first name is a cable number, not a pin.
    """
    tokens = unicodedata.normalize("NFKC", label or "").upper().split()
    first = next((i for i, t in enumerate(tokens) if re.search("[A-Z]", t)), len(tokens))
    names = [t for t in tokens[first:] if re.search("[A-Z]", t)]
    numbers = [t for t in tokens[first:] if t.isdigit()]
    pairs = list(zip(names, numbers, strict=True)) if names and len(names) == len(numbers) else None
    return numbers, pairs


def oracle_predictions(drawing: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {"id": g["ref"], "class_name": g["class_name"], "confidence": 1.0, "kind": g["kind"], "bbox": g["bbox"],
         **_normalized(g["bbox"], drawing)}
        for g in gold_items(drawing)
    ]


def _normalized(box: dict[str, float], drawing: dict[str, Any]) -> dict[str, float]:
    width, height = int(drawing["project"]["image_width"]), int(drawing["project"]["image_height"])
    return {"cx": (box["x0"] + box["x1"]) / 2 / width, "cy": (box["y0"] + box["y1"]) / 2 / height,
            "w": (box["x1"] - box["x0"]) / width, "h": (box["y1"] - box["y0"]) / height}


def file_predictions(run: Path, drawing: dict[str, Any]) -> list[dict[str, Any]]:
    files = list(run.glob(f"fold*/pred/{drawing['sheet']}.json"))
    if len(files) != 1:
        raise FileNotFoundError(f"expected one prediction file for {drawing['sheet']} in {run}, found {len(files)}")
    width, height = int(drawing["project"]["image_width"]), int(drawing["project"]["image_height"])
    return [{**p, "bbox": to_box(p["cx"], p["cy"], p["w"], p["h"], width, height)} for p in json.loads(files[0].read_text())]


def _structure_job(args: tuple[dict[str, Any], list[dict[str, Any]], str, str]) -> tuple[str, str | None]:
    drawing, pred, out_path, work = args
    try:
        Path(work).mkdir(parents=True, exist_ok=True)
        structure = run_structure(drawing, pred, Path(work))
    except Exception as exc:  # noqa: BLE001  one failing page must not hide the others' scores
        return drawing["sheet"], f"{type(exc).__name__}: {exc}"
    Path(out_path).write_text(json.dumps(structure, ensure_ascii=False), encoding="utf-8")
    return drawing["sheet"], None


class _MemoOCR:
    """RapidOCR is deterministic per input array; memoizing it on disk makes wiring-only reruns fast."""

    def __init__(self, reader: Any, cache_dir: Path) -> None:
        self.reader = reader
        self.cache_dir = cache_dir

    def __call__(self, pixels: Any, **kwargs: Any) -> Any:
        import numpy as np

        array = np.ascontiguousarray(pixels)
        digest = hashlib.sha1(array.tobytes() + repr((array.shape, str(array.dtype), sorted(kwargs.items()))).encode()).hexdigest()
        path = self.cache_dir / f"{digest}.pkl"
        if path.exists():
            return pickle.loads(path.read_bytes())
        result = self.reader(pixels, **kwargs)
        tmp = path.with_suffix(f".{id(self)}.tmp")
        tmp.write_bytes(pickle.dumps(result))
        tmp.replace(path)
        return result


def _init_worker(overrides: list[str], ocr_threads: int, ocr_cache: str | None) -> None:
    """Apply MODULE.NAME=VALUE ablations, cap OCR threads so parallel workers do not oversubscribe, memoize OCR."""
    import analysis.runtime as runtime

    for item in overrides:
        target, value = item.split("=", 1)
        module_name, name = target.rsplit(".", 1)
        module = importlib.import_module(module_name)
        if not hasattr(module, name):
            raise AttributeError(f"{module_name} has no {name}")
        setattr(module, name, json.loads(value))

    def limited_reader(engine: str, language: str) -> tuple[Any, str, str | None]:
        from rapidocr_onnxruntime import RapidOCR

        threads = {f"{part}_intra_op_num_threads": ocr_threads for part in ("det", "cls", "rec")}
        reader = RapidOCR(**threads)
        if ocr_cache:
            Path(ocr_cache).mkdir(parents=True, exist_ok=True)
            reader = _MemoOCR(reader, Path(ocr_cache))
        return reader, "rapidocr", None

    runtime.load_symbol_ocr_reader = limited_reader

    from analysis import name_ocr

    name_model = name_ocr.model_path()
    name_reader = name_ocr.make_reader(name_model, ocr_threads) if name_model else None
    runtime.name_ocr_reader = lambda: name_reader


class DSU:
    def __init__(self) -> None:
        self.parent: dict[str, str] = {}

    def find(self, x: str) -> str:
        while self.parent.setdefault(x, x) != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, a: str, b: str) -> None:
        self.parent[self.find(a)] = self.find(b)


def _pairs_of_groups(dsu: DSU, members: dict[str, str]) -> set[tuple[str, str]]:
    groups: dict[str, set[str]] = {}
    for member, key in members.items():
        groups.setdefault(dsu.find(member), set()).add(key)
    return {pair for keys in groups.values() for pair in itertools.combinations(sorted(keys), 2)}


def _through_coil(a: tuple[float, float], b: tuple[float, float], coils: list[dict[str, float]]) -> bool:
    """A gold link drawn straight through the middle of a relay coil box, along its axis."""
    for box in coils:
        mx, my = (box["x1"] - box["x0"]) / 4, (box["y1"] - box["y0"]) / 4
        if abs(a[0] - b[0]) <= 6 and box["x0"] + mx <= a[0] <= box["x1"] - mx \
                and min(a[1], b[1]) <= box["y0"] + my and max(a[1], b[1]) >= box["y1"] - my:
            return True
        if abs(a[1] - b[1]) <= 6 and box["y0"] + my <= a[1] <= box["y1"] - my \
                and min(a[0], b[0]) <= box["x0"] + mx and max(a[0], b[0]) >= box["x1"] - mx:
            return True
    return False


def gold_nodes(drawing: dict[str, Any], symbol_refs: set[str], *, conventions: bool = False) -> tuple[DSU, dict[str, str]]:
    """Wire-only electrical nodes of the gold drawing: a union-find over terminal keys, and the scored keys.

    conventions=True removes three annotation habits no prediction can follow: every relay coil is bridged by
    a link drawn straight through it (gold never wires coils); a link endpoint without a terminal ref on a
    one-terminal symbol forms a key of its own, apart from that terminal; and a wire crossing the border of a
    panel box is drawn as a link between two terminals of that box, which the same-symbol rule would cut.
    """
    project = drawing["project"]
    width, height = int(project["image_width"]), int(project["image_height"])
    single = {s["ref"]: s["terminals"][0]["ref"] for s in project["symbols"] if len(s.get("terminals") or []) == 1}
    points = {s["ref"]: (s["cx"] * width, s["cy"] * height) for s in project["symbols"]}
    points.update({t["ref"]: (t["tx"] * width, t["ty"] * height) for s in project["symbols"] for t in s.get("terminals") or []})
    coils = [to_box(s["cx"], s["cy"], s["w"], s["h"], width, height) for s in project["symbols"] if s["class_key"] == "relay_coil"]
    boxes = {s["ref"] for s in project["symbols"] if s["class_key"] == "other"}
    dsu = DSU()
    members: dict[str, str] = {}
    for conn in project["connections"]:
        ends = [(conn[f"{side}_symbol_ref"], conn.get(f"{side}_terminal_ref")) for side in ("from", "to")]
        if conn["from_symbol_ref"] == conn["to_symbol_ref"]:
            a, b = (points.get(terminal or ref) for ref, terminal in ends)
            crossing = conventions and conn["from_symbol_ref"] in boxes and a and b and min(abs(a[0] - b[0]), abs(a[1] - b[1])) <= 6 \
                and abs(a[0] - b[0]) + abs(a[1] - b[1]) <= BORDER_CROSSING_PX
            if not crossing:
                continue
        if conventions:
            ends = [(ref, terminal or single.get(ref)) for ref, terminal in ends]
            if _through_coil(*(points[terminal or ref] for ref, terminal in ends), coils):
                continue
        a, b = (endpoint_key(ref, terminal) for ref, terminal in ends)
        dsu.union(a, b)
        for key, ref in ((a, conn["from_symbol_ref"]), (b, conn["to_symbol_ref"])):
            if ref in symbol_refs:
                members[key] = key
    return dsu, members


def gold_node_pairs(drawing: dict[str, Any], symbol_refs: set[str], *, conventions: bool = False) -> set[tuple[str, str]]:
    return _pairs_of_groups(*gold_nodes(drawing, symbol_refs, conventions=conventions))


def predicted_nodes(drawing: dict[str, Any], structure: dict[str, Any], pred_to_gold: dict[str, str]) -> tuple[DSU, dict[str, str]]:
    """Predicted nodes as a union-find over from_to terminal ids, and each scored terminal's nearest gold terminal key."""
    width, height = int(drawing["project"]["image_width"]), int(drawing["project"]["image_height"])
    gold_terminals = {
        s["ref"]: [(endpoint_key(s["ref"], t["ref"]), (t["tx"] * width, t["ty"] * height)) for t in s.get("terminals") or []]
        for s in drawing["project"]["symbols"]
    }
    # Gold leaves some symbols unwired (every relay coil); their links cannot be judged.
    wired = {c[f"{side}_symbol_ref"] for c in drawing["project"]["connections"] for side in ("from", "to")}
    from_to = structure.get("from_to", {})
    dsu = DSU()
    members: dict[str, str] = {}
    for terminal in from_to.get("terminals", []):
        gold_ref = pred_to_gold.get(terminal["symbol_id"])
        if gold_ref is None or gold_ref not in wired:
            continue
        candidates = gold_terminals.get(gold_ref) or [(gold_ref, None)]
        if candidates[0][1] is None:
            members[terminal["id"]] = gold_ref
            continue
        px, py = terminal["point"]
        members[terminal["id"]] = min(candidates, key=lambda c: (c[1][0] - px) ** 2 + (c[1][1] - py) ** 2)[0]
    for conn in from_to.get("connections", []):
        left, right = conn["from"], conn["to"]
        if left["symbol_id"] == right["symbol_id"] and left.get("class_name") not in NODE_CLASSES:
            continue
        dsu.union(left["id"], right["id"])
    return dsu, members


def predicted_node_pairs(drawing: dict[str, Any], structure: dict[str, Any], pred_to_gold: dict[str, str]) -> set[tuple[str, str]]:
    return _pairs_of_groups(*predicted_nodes(drawing, structure, pred_to_gold))


def structure_predictions(structure: dict[str, Any], pred: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Detections as the structure payload reports them: suppressed duplicates dropped, structure ids attached."""
    symbols = [p for p in pred if p["kind"] == "symbol"]
    out = [p for p in pred if p["kind"] != "symbol"]
    for p, s in zip(symbols, structure.get("symbols", []), strict=True):
        if (s.get("dot_role") or {}).get("suppressed"):
            continue
        out.append({**p, "label": s.get("label") or "", "structure_id": s["id"], "dot_role": (s.get("dot_role") or {}).get("role")})
    return out


def contact_number_outcomes(
    contacts: list[dict[str, Any]], g_sym: list[dict[str, Any]], p_sym: list[dict[str, Any]],
    matches: list[tuple[int, int, float]], symbols: dict[str, dict[str, Any]],
) -> Counter:
    """Each ground-truth contact end: its number read at that end, a wrong number, no number, or the contact undetected.

    Predicted terminals are assigned to the nearest ground-truth end of the matched contact. An end whose truth is
    null has no number printed; it counts as blank_ok when nothing is read there and blank_spurious otherwise.
    """
    gold_index = {g["ref"]: gi for gi, g in enumerate(g_sym)}
    pred_of = {gi: p_sym[pi]["structure_id"] for gi, pi, _ in matches}
    out: Counter = Counter()
    for contact in contacts:
        ends = contact["terminals"]
        numbered = [e for e in ends if e["number"] is not None]
        out.update(contacts=bool(numbered), ends=len(numbered), blank_ends=len(ends) - len(numbered))
        symbol_id = pred_of.get(gold_index.get(contact["gold_ref"], -1))
        if symbol_id is None:
            out.update(ends_undetected=len(numbered), blank_undetected=len(ends) - len(numbered))
            continue
        read: list[set[str]] = [set() for _ in ends]
        for terminal in symbols[symbol_id].get("terminals") or []:
            name = str(terminal.get("name") or "")
            if not name.isdigit():
                continue
            px, py = terminal["point"]
            read[min(range(len(ends)), key=lambda i: (ends[i]["point"][0] - px) ** 2 + (ends[i]["point"][1] - py) ** 2)].add(name)
        for end, names in zip(ends, read, strict=True):
            if end["number"] is None:
                out["blank_spurious" if names else "blank_ok"] += 1
            else:
                out["ends_correct" if end["number"] in names else "ends_wrong" if names else "ends_unread"] += 1
        if numbered:
            out["contacts_pair_correct"] += set().union(*read) == {e["number"] for e in numbered}
    return out


def score_drawing(
    drawing: dict[str, Any], pred: list[dict[str, Any]], structure: dict[str, Any],
    contacts: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    gold = gold_items(drawing)
    detector = score_symbols(gold, pred)
    active = structure_predictions(structure, pred)
    scored = score_symbols(gold, active)
    g_sym, p_sym = scored["g_sym"], scored["p_sym"]
    pred_to_gold = {p_sym[pi]["structure_id"]: g_sym[gi]["ref"] for gi, pi, _ in scored["matches"]}
    refs = {g["ref"] for g in g_sym}
    gp, pp = gold_pairs(drawing, refs), predicted_pairs(structure, pred_to_gold)
    gn, pn = gold_node_pairs(drawing, refs), predicted_node_pairs(drawing, structure, pred_to_gold)
    gc = gold_node_pairs(drawing, refs, conventions=True)
    roles = Counter(
        f"{g_sym[gi]['class_name']}->{p_sym[pi]['dot_role']}" for gi, pi, _ in scored["matches"]
        if g_sym[gi]["class_name"] in ROLE_OF_CLASS and p_sym[pi].get("dot_role")
    )
    symbols = {s["id"]: s for s in structure.get("symbols", [])}
    numbers: Counter = Counter()
    for gi, pi, _ in scored["matches"]:
        if g_sym[gi]["class_name"] != "connector":
            continue
        gold_numbers, gold_pairs_ = gold_terminal_numbers(g_sym[gi]["label"])
        if not gold_numbers:
            continue
        read = symbols[p_sym[pi]["structure_id"]].get("terminal_numbers") or []
        numbers["scored"] += 1
        numbers["numbers_agree"] += sorted(p["number"] for p in read) == sorted(gold_numbers)
        if gold_pairs_:
            numbers["pairs_scored"] += 1
            numbers["pairs_agree"] += sorted((p["device"], p["number"]) for p in read) == sorted(gold_pairs_)
    return {
        "sheet": drawing["sheet"],
        "pairs": {"tp": len(gp & pp), "fp": len(pp - gp), "fn": len(gp - pp)},
        "node_pairs": {"tp": len(gn & pn), "fp": len(pn - gn), "fn": len(gn - pn)},
        "node_pairs_conv": {"tp": len(gc & pn), "fp": len(pn - gc), "fn": len(gc - pn)},
        "per_class": {c: dict(v) for c, v in detector["per_class"].items()},
        "per_class_after": {c: dict(v) for c, v in scored["per_class"].items()},
        "dot_roles": dict(roles),
        "terminal_numbers": dict(numbers),
        "contact_numbers": dict(contact_number_outcomes(contacts or [], g_sym, p_sym, scored["matches"], symbols)),
    }


def aggregate(rows: list[dict[str, Any]]) -> dict[str, Any]:
    tot = {k: Counter() for k in ("pairs", "node_pairs", "node_pairs_conv")}
    before: dict[str, Counter] = {c: Counter() for c in SCORED_CLASSES}
    after: dict[str, Counter] = {c: Counter() for c in SCORED_CLASSES}
    roles: Counter = Counter()
    numbers: Counter = Counter()
    contact_numbers: Counter = Counter()
    for r in rows:
        for k in tot:
            tot[k].update(r[k])
        for c in SCORED_CLASSES:
            before[c].update(r["per_class"].get(c, {}))
            after[c].update(r["per_class_after"].get(c, {}))
        roles.update(r["dot_roles"])
        numbers.update(r["terminal_numbers"])
        contact_numbers.update(r.get("contact_numbers", {}))
    agree = sum(n for k, n in roles.items() if k.split("->")[1] == ROLE_OF_CLASS[k.split("->")[0]])
    ends, contacts = contact_numbers["ends"], contact_numbers["contacts"]
    return {
        "drawings": len(rows),
        "symbol_pairs": prf(tot["pairs"]["tp"], tot["pairs"]["fp"], tot["pairs"]["fn"]),
        "node_pairs": prf(tot["node_pairs"]["tp"], tot["node_pairs"]["fp"], tot["node_pairs"]["fn"]),
        "node_pairs_conv": prf(tot["node_pairs_conv"]["tp"], tot["node_pairs_conv"]["fp"], tot["node_pairs_conv"]["fn"]),
        "class_f1_detector": {c: prf(v["tp"], v["fp"], v["fn"]) for c, v in before.items()},
        "class_f1_structure": {c: prf(v["tp"], v["fp"], v["fn"]) for c, v in after.items()},
        "dot_role_accuracy": round(agree / sum(roles.values()), 4) if roles else None,
        "dot_role_scored": sum(roles.values()),
        "dot_role_confusion": dict(roles.most_common()),
        "terminal_numbers": {
            "connectors_scored": numbers["scored"],
            "numbers_agree": round(numbers["numbers_agree"] / numbers["scored"], 4) if numbers["scored"] else None,
            "pairs_scored": numbers["pairs_scored"],
            "pairs_agree": round(numbers["pairs_agree"] / numbers["pairs_scored"], 4) if numbers["pairs_scored"] else None,
        },
        "contact_terminal_numbers": {
            "contacts": contacts, "ends": ends,
            "end_accuracy": round(contact_numbers["ends_correct"] / ends, 4) if ends else None,
            "pair_accuracy": round(contact_numbers["contacts_pair_correct"] / contacts, 4) if contacts else None,
            **{k: contact_numbers[k] for k in ("ends_correct", "ends_wrong", "ends_unread", "ends_undetected", "contacts_pair_correct")},
            **{k: contact_numbers[k] for k in ("blank_ends", "blank_ok", "blank_spurious", "blank_undetected") if contact_numbers["blank_ends"]},
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--gold", type=Path, nargs="+", required=True)
    parser.add_argument("--pred-run", type=Path, help="holdout_cv work dir with fold*/pred/<SHEET>.json")
    parser.add_argument("--work", type=Path, required=True)
    parser.add_argument("--conditions", nargs="+", default=["oracle", "pred"], choices=["oracle", "pred"])
    parser.add_argument("--jobs", type=int, default=6)
    parser.add_argument("--ocr-threads", type=int, default=2)
    parser.add_argument("--ocr-cache", type=Path, help="directory memoizing OCR results across runs")
    parser.add_argument("--override", nargs="*", default=[], metavar="MODULE.NAME=JSON",
                        help="ablation: set a module constant in every worker, e.g. filter_wire_decorations.DASHED_RULE_MIN_LENGTH=0")
    parser.add_argument("--rescore", action="store_true", help="score structures already in --work without rerunning analysis")
    parser.add_argument("--sheets", nargs="*", help="restrict to these sheets (debugging)")
    parser.add_argument("--contact-gt", type=Path, nargs="*", default=[],
                        help="contact terminal-number ground truth JSON ({contacts: [{sheet, gold_ref, terminals: [{point, number|null}]}]})")
    args = parser.parse_args()

    _keys, drawings = load_gold(args.gold)
    # The split comes from all gold sheets so a --sheets subset keeps each sheet's tune/report side.
    tune = set(sorted(d["sheet"] for d in drawings)[0::2])
    if args.sheets:
        drawings = [d for d in drawings if d["sheet"] in set(args.sheets)]
    sheets = [d["sheet"] for d in drawings]
    contacts_of: dict[str, list[dict[str, Any]]] = {}
    for gt_path in args.contact_gt:
        for contact in json.loads(gt_path.read_text(encoding="utf-8"))["contacts"]:
            contacts_of.setdefault(contact["sheet"], []).append(contact)
    started = time.time()
    summary: dict[str, Any] = {}
    for condition in args.conditions:
        out_dir = (args.work / condition).resolve()
        out_dir.mkdir(parents=True, exist_ok=True)
        preds = {d["sheet"]: oracle_predictions(d) if condition == "oracle" else file_predictions(args.pred_run, d) for d in drawings}
        errors: dict[str, str] = {}
        if not args.rescore:
            jobs = [(d, preds[d["sheet"]], str(out_dir / f"{d['sheet']}.json"), str(out_dir / "tmp")) for d in drawings]
            initargs = (args.override, args.ocr_threads, str(args.ocr_cache.resolve()) if args.ocr_cache else None)
            with ProcessPoolExecutor(max_workers=args.jobs, initializer=_init_worker, initargs=initargs) as pool:
                for sheet, error in pool.map(_structure_job, jobs):
                    if error:
                        errors[sheet] = error
        rows = []
        for d in drawings:
            path = out_dir / f"{d['sheet']}.json"
            if d["sheet"] in errors or not path.exists():
                errors.setdefault(d["sheet"], "structure missing")
                continue
            row = score_drawing(d, preds[d["sheet"]], json.loads(path.read_text(encoding="utf-8")), contacts_of.get(d["sheet"]))
            rows.append(row)
        (out_dir / "rows.json").write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")
        summary[condition] = {
            "errors": len(errors), "error_detail": errors,
            "all": aggregate(rows),
            "tune": aggregate([r for r in rows if r["sheet"] in tune]),
            "report": aggregate([r for r in rows if r["sheet"] not in tune]),
        }
    summary["split"] = {"tune": sorted(tune & set(sheets)), "report": sorted(set(sheets) - tune)}
    summary["seconds"] = round(time.time() - started, 1)
    (args.work / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
    for condition in args.conditions:
        s = summary[condition]
        print(f"[{condition}] errors={s['errors']}")
        for part in ("tune", "report", "all"):
            a = s[part]
            cls = " ".join(
                f"{c}={a['class_f1_detector'][c]['f1']}->{a['class_f1_structure'][c]['f1']}"
                f"(fp{a['class_f1_structure'][c]['fp']}/fn{a['class_f1_structure'][c]['fn']})"
                for c in SCORED_CLASSES
            )
            sp, npairs, conv = a["symbol_pairs"], a["node_pairs"], a["node_pairs_conv"]
            print(f"  {part:6s} drawings={a['drawings']} symbol_pairs f1={sp['f1']} tp={sp['tp']} fp={sp['fp']} fn={sp['fn']}"
                  f" | node_pairs f1={npairs['f1']} tp={npairs['tp']} fp={npairs['fp']} fn={npairs['fn']} | {cls}"
                  f" | node_pairs_conv f1={conv['f1']} tp={conv['tp']} fp={conv['fp']} fn={conv['fn']}"
                  f" | dot_role acc={a['dot_role_accuracy']} n={a['dot_role_scored']}"
                  f" | terminal_numbers {a['terminal_numbers']}")
            if args.contact_gt:
                print(f"         contact_terminal_numbers {a['contact_terminal_numbers']}")
    print(f"seconds={summary['seconds']}")


if __name__ == "__main__":
    main()

