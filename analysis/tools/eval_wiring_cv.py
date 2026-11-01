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
import sys
import time
from collections import Counter
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from analyze_annotation_graph import endpoint_key  # noqa: E402
from holdout_cv import evaluate, gold_items, load_gold, prf, run_structure, score_symbols, to_box  # noqa: E402

NODE_CLASSES = {"connector", "junction", "terminal", "power_bus"}
SCORED_CLASSES = ("junction", "connector")


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


def gold_node_pairs(drawing: dict[str, Any], symbol_refs: set[str]) -> set[tuple[str, str]]:
    dsu = DSU()
    members: dict[str, str] = {}
    for conn in drawing["project"]["connections"]:
        if conn["from_symbol_ref"] == conn["to_symbol_ref"]:
            continue
        a = endpoint_key(conn["from_symbol_ref"], conn.get("from_terminal_ref"))
        b = endpoint_key(conn["to_symbol_ref"], conn.get("to_terminal_ref"))
        dsu.union(a, b)
        for key, ref in ((a, conn["from_symbol_ref"]), (b, conn["to_symbol_ref"])):
            if ref in symbol_refs:
                members[key] = key
    return _pairs_of_groups(dsu, members)


def predicted_node_pairs(drawing: dict[str, Any], structure: dict[str, Any], pred_to_gold: dict[str, str]) -> set[tuple[str, str]]:
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
    return _pairs_of_groups(dsu, members)


def structure_predictions(structure: dict[str, Any], pred: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Detections as the structure payload reports them (class and box may be revised there)."""
    symbols = [p for p in pred if p["kind"] == "symbol"]
    out = [p for p in pred if p["kind"] != "symbol"]
    for p, s in zip(symbols, structure.get("symbols", []), strict=True):
        revised = s.get("dot_role") or {}
        if revised.get("suppressed"):
            continue
        out.append({**p, "class_name": s.get("class_name") or p["class_name"], "bbox": revised.get("bbox_f") or p["bbox"],
                    "label": s.get("label") or ""})
    return out


def score_drawing(drawing: dict[str, Any], pred: list[dict[str, Any]], structure: dict[str, Any]) -> dict[str, Any]:
    symbols_in_order = [p for p in pred if p["kind"] == "symbol"]
    labelled = [{**p, "label": s.get("label") or ""} for p, s in zip(symbols_in_order, structure.get("symbols", []), strict=True)]
    pred_l = [p for p in pred if p["kind"] != "symbol"] + labelled
    row = evaluate(drawing, pred_l, structure)
    gold = gold_items(drawing)
    scored = score_symbols(gold, pred_l)
    g_sym = scored["g_sym"]
    sym_ids = [s["id"] for s in structure.get("symbols", [])]
    pred_to_gold = {sym_ids[pi]: g_sym[gi]["ref"] for gi, pi, _ in scored["matches"] if pi < len(sym_ids)}
    gp = gold_node_pairs(drawing, {g["ref"] for g in g_sym})
    pp = predicted_node_pairs(drawing, structure, pred_to_gold)
    row["node_pairs"] = {"tp": len(gp & pp), "fp": len(pp - gp), "fn": len(gp - pp)}
    after = score_symbols(gold, structure_predictions(structure, pred_l))
    row["per_class_after"] = {c: dict(v) for c, v in after["per_class"].items()}
    return row


def aggregate(rows: list[dict[str, Any]]) -> dict[str, Any]:
    tot = {k: Counter() for k in ("pairs", "node_pairs")}
    before: dict[str, Counter] = {c: Counter() for c in SCORED_CLASSES}
    after: dict[str, Counter] = {c: Counter() for c in SCORED_CLASSES}
    for r in rows:
        for k in tot:
            tot[k].update(r[k])
        for c in SCORED_CLASSES:
            before[c].update(r["per_class"].get(c, {}))
            after[c].update(r["per_class_after"].get(c, {}))
    return {
        "drawings": len(rows),
        "symbol_pairs": prf(tot["pairs"]["tp"], tot["pairs"]["fp"], tot["pairs"]["fn"]),
        "node_pairs": prf(tot["node_pairs"]["tp"], tot["node_pairs"]["fp"], tot["node_pairs"]["fn"]),
        "class_f1_detector": {c: prf(v["tp"], v["fp"], v["fn"]) for c, v in before.items()},
        "class_f1_structure": {c: prf(v["tp"], v["fp"], v["fn"]) for c, v in after.items()},
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
    args = parser.parse_args()

    _keys, drawings = load_gold(args.gold)
    if args.sheets:
        drawings = [d for d in drawings if d["sheet"] in set(args.sheets)]
    sheets = [d["sheet"] for d in drawings]
    tune = set(sorted(sheets)[0::2])
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
            row = score_drawing(d, preds[d["sheet"]], json.loads(path.read_text(encoding="utf-8")))
            rows.append(row)
        (out_dir / "rows.json").write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")
        summary[condition] = {
            "errors": len(errors), "error_detail": errors,
            "all": aggregate(rows),
            "tune": aggregate([r for r in rows if r["sheet"] in tune]),
            "report": aggregate([r for r in rows if r["sheet"] not in tune]),
        }
    summary["split"] = {"tune": sorted(tune), "report": sorted(set(sheets) - tune)}
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
            sp, npairs = a["symbol_pairs"], a["node_pairs"]
            print(f"  {part:6s} drawings={a['drawings']} symbol_pairs f1={sp['f1']} tp={sp['tp']} fp={sp['fp']} fn={sp['fn']}"
                  f" | node_pairs f1={npairs['f1']} tp={npairs['tp']} fp={npairs['fp']} fn={npairs['fn']} | {cls}")
    print(f"seconds={summary['seconds']}")


if __name__ == "__main__":
    main()

