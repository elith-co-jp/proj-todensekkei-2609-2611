"""Accuracy on drawings the detector has not seen, by K-fold cross-validation over drawings.

Each fold trains on the other drawings only and is scored on its own drawings, so every
drawing is evaluated exactly once as an unseen drawing. Detections and gold boxes the app
treats as regions (services.detection_rules) are scored separately from symbols, matching
what the editor shows.

Example:
    python analysis/tools/holdout_cv.py --gold a.zip b.zip --work runs/cv --recipe recipe.json
"""

from __future__ import annotations

import argparse
import hashlib
import io
import itertools
import json
import random
import re
import sys
import time
import unicodedata
import zipfile
from collections import Counter
from pathlib import Path
from typing import Any

from PIL import Image

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from analyze_annotation_graph import build_project_graph, parse_member  # noqa: E402

from services.detection_rules import detection_kind  # noqa: E402
from services.rotation_copies import rotate_box, rotate_image, rotated_copy  # noqa: E402

DEFAULT_RECIPE: dict[str, Any] = {
    "model": "yolo11n.pt",
    "imgsz": 1280,
    "epochs": 150,
    "batch": 4,
    "conf": 0.45,
    "seed": 2609,
    "train_args": {
        "mosaic": 1.0, "translate": 0.1, "scale": 0.5, "fliplr": 0.0, "flipud": 0.0,
        "degrees": 0.0, "hsv_h": 0.0, "hsv_s": 0.0, "hsv_v": 0.0, "copy_paste": 0.0, "erasing": 0.4,
    },
    "predict_args": {"max_det": 1000},
}
MATCH_IOU = 0.5
# Dots are 8-15 px wide, so box IoU mostly measures how big the annotator drew the box.
POINT_CLASSES = {"junction", "power_bus"}
POINT_MATCH_PX = 12.0
# Annotators boxed wire bends as other/分岐点 only to route from-to wiring; they are not symbols.
WIRE_BEND_LABEL = "分岐点"
STRICT_IOU = False


def is_wire_bend(symbol: dict[str, Any]) -> bool:
    return symbol.get("class_key") == "other" and (symbol.get("label") or "").strip() == WIRE_BEND_LABEL


def sheet_of(project: dict[str, Any]) -> str:
    match = re.search(r"[A-Z]{1,4}\d{3,8}", str(project.get("name", "")))
    return match.group(0) if match else str(project.get("name"))


def load_gold(paths: list[Path]) -> tuple[list[str], list[dict[str, Any]]]:
    """Return the class keys in YOLO index order and one record per drawing (image bytes included)."""
    class_keys: list[str] | None = None
    drawings: list[dict[str, Any]] = []
    for path in paths:
        with zipfile.ZipFile(path) as zf:
            bundle = json.loads(zf.read("bundle.json"))
            netlist = json.loads(zf.read("connections/netlist.json")) if "connections/netlist.json" in zf.namelist() else {}
            keys = [c["key"] for c in sorted(bundle["classes"], key=lambda c: c["yolo_index"])]
            if class_keys is None:
                class_keys = keys
            elif keys != class_keys:
                raise ValueError(f"class order differs in {path}")
            images = {n.rsplit("/", 1)[-1]: n for n in zf.namelist() if n.startswith("dataset/images/")}
            nets = {str(p.get("project_id")): p for p in netlist.get("projects", [])}
            for project in bundle["projects"]:
                stem_prefix = f"p{project['id']}_"
                image_name = next((n for n in images if n.startswith(stem_prefix)), None)
                if image_name is None:
                    raise ValueError(f"image missing for project {project['name']} in {path}")
                drawings.append({
                    "sheet": sheet_of(project),
                    "project": project,
                    "netlist": nets.get(str(project["id"])),
                    "image": zf.read(images[image_name]),
                })
    sheets = [d["sheet"] for d in drawings]
    duplicates = [s for s, n in Counter(sheets).items() if n > 1]
    if duplicates:
        raise ValueError(f"duplicate drawings in gold data: {duplicates}")
    return class_keys or [], sorted(drawings, key=lambda d: d["sheet"])


def folds_of(drawings: list[dict[str, Any]], k: int) -> list[list[str]]:
    return [[d["sheet"] for d in drawings[i::k]] for i in range(k)]


def subsample_train(train_sheets: set[str], n: int | None, seed: int, fold_index: int) -> set[str]:
    """Keep n training drawings per fold (a learning curve); the same seed and fold always keep the same drawings."""
    if n is None or n >= len(train_sheets):
        return train_sheets
    return set(random.Random(seed * 100 + fold_index).sample(sorted(train_sheets), n))


def to_box(cx: float, cy: float, w: float, h: float, width: int, height: int) -> dict[str, float]:
    return {"x0": (cx - w / 2) * width, "y0": (cy - h / 2) * height, "x1": (cx + w / 2) * width, "y1": (cy + h / 2) * height}


def iou(a: dict[str, float], b: dict[str, float]) -> float:
    ix = max(0.0, min(a["x1"], b["x1"]) - max(a["x0"], b["x0"]))
    iy = max(0.0, min(a["y1"], b["y1"]) - max(a["y0"], b["y0"]))
    inter = ix * iy
    union = (a["x1"] - a["x0"]) * (a["y1"] - a["y0"]) + (b["x1"] - b["x0"]) * (b["y1"] - b["y0"]) - inter
    return inter / union if union > 0 else 0.0


def gold_items(drawing: dict[str, Any]) -> list[dict[str, Any]]:
    project = drawing["project"]
    width, height = int(project["image_width"]), int(project["image_height"])
    return [
        {
            "ref": s["ref"], "class_name": s["class_key"], "label": s.get("label") or "",
            "kind": detection_kind(s["w"], s["h"]),
            "bbox": to_box(s["cx"], s["cy"], s["w"], s["h"], width, height),
        }
        for s in project["symbols"]
        if not is_wire_bend(s)
    ]


def similarity(g: dict[str, Any], p: dict[str, Any]) -> float:
    """IoU for boxed symbols; for dots, 1 at the same centre falling to MATCH_IOU at POINT_MATCH_PX."""
    if g["class_name"] in POINT_CLASSES and not STRICT_IOU:
        gx, gy = (g["bbox"]["x0"] + g["bbox"]["x1"]) / 2, (g["bbox"]["y0"] + g["bbox"]["y1"]) / 2
        px, py = (p["bbox"]["x0"] + p["bbox"]["x1"]) / 2, (p["bbox"]["y0"] + p["bbox"]["y1"]) / 2
        distance = ((gx - px) ** 2 + (gy - py) ** 2) ** 0.5
        return 1.0 - (1.0 - MATCH_IOU) * distance / POINT_MATCH_PX if distance <= POINT_MATCH_PX else 0.0
    return iou(g["bbox"], p["bbox"])


def match(gold: list[dict[str, Any]], pred: list[dict[str, Any]], *, class_aware: bool) -> list[tuple[int, int, float]]:
    """Greedy one-to-one matching, highest similarity first."""
    pairs = sorted(
        (
            (score, gi, pi)
            for gi, g in enumerate(gold)
            for pi, p in enumerate(pred)
            if (not class_aware or g["class_name"] == p["class_name"]) and (score := similarity(g, p)) >= MATCH_IOU
        ),
        reverse=True,
    )
    used_g: set[int] = set()
    used_p: set[int] = set()
    out = []
    for score, gi, pi in pairs:
        if gi in used_g or pi in used_p:
            continue
        used_g.add(gi)
        used_p.add(pi)
        out.append((gi, pi, score))
    return out


def prf(tp: int, fp: int, fn: int) -> dict[str, Any]:
    p = tp / (tp + fp) if tp + fp else 0.0
    r = tp / (tp + fn) if tp + fn else 0.0
    f = 2 * p * r / (p + r) if p + r else 0.0
    return {"tp": tp, "fp": fp, "fn": fn, "precision": round(p, 4), "recall": round(r, 4), "f1": round(f, 4)}


def norm_label(text: str) -> str:
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", text or "").upper())


def score_symbols(gold: list[dict[str, Any]], pred: list[dict[str, Any]]) -> dict[str, Any]:
    g_sym = [g for g in gold if g["kind"] == "symbol"]
    p_sym = [p for p in pred if p["kind"] == "symbol"]
    aware = match(g_sym, p_sym, class_aware=True)
    agnostic = match(g_sym, p_sym, class_aware=False)
    confusion = Counter(
        (g_sym[gi]["class_name"], p_sym[pi]["class_name"]) for gi, pi, _ in agnostic
        if g_sym[gi]["class_name"] != p_sym[pi]["class_name"]
    )
    per_class: dict[str, Counter] = {}
    matched_g = {gi for gi, _, _ in aware}
    matched_p = {pi for _, pi, _ in aware}
    for gi, g in enumerate(g_sym):
        per_class.setdefault(g["class_name"], Counter())["tp" if gi in matched_g else "fn"] += 1
    for pi, p in enumerate(p_sym):
        if pi not in matched_p:
            per_class.setdefault(p["class_name"], Counter())["fp"] += 1
    labelled = [(gi, pi) for gi, pi, _ in aware if g_sym[gi]["label"]]
    label_ok = sum(1 for gi, pi in labelled if norm_label(p_sym[pi].get("label", "")) == norm_label(g_sym[gi]["label"]))
    g_reg = [g for g in gold if g["kind"] == "region"]
    p_reg = [p for p in pred if p["kind"] == "region"]
    reg = match(g_reg, p_reg, class_aware=False)
    return {
        "aware": Counter(tp=len(aware), fp=len(p_sym) - len(aware), fn=len(g_sym) - len(aware)),
        "agnostic": Counter(tp=len(agnostic), fp=len(p_sym) - len(agnostic), fn=len(g_sym) - len(agnostic)),
        "type_correct": sum(1 for gi, pi, _ in agnostic if g_sym[gi]["class_name"] == p_sym[pi]["class_name"]),
        "type_total": len(agnostic),
        "confusion": confusion,
        "per_class": per_class,
        "label_correct": label_ok,
        "label_total": len(labelled),
        "region": Counter(tp=len(reg), fp=len(p_reg) - len(reg), fn=len(g_reg) - len(reg)),
        "matches": aware,
        "g_sym": g_sym,
        "p_sym": p_sym,
    }


def gold_pairs(drawing: dict[str, Any], symbol_refs: set[str]) -> set[tuple[str, str]]:
    graph = build_project_graph(drawing["project"], drawing["netlist"])
    pairs: set[tuple[str, str]] = set()
    for component in graph.get("components", []):
        refs = sorted({parse_member(m)[0] for m in component.get("members", [])} & symbol_refs)
        pairs.update(itertools.combinations(refs, 2))
    return pairs


def predicted_pairs(structure: dict[str, Any], pred_to_gold: dict[str, str]) -> set[tuple[str, str]]:
    parent: dict[str, str] = {}

    def find(x: str) -> str:
        while parent.setdefault(x, x) != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for conn in structure.get("from_to", {}).get("connections", []):
        a, b = conn["from"]["symbol_id"], conn["to"]["symbol_id"]
        if a in pred_to_gold and b in pred_to_gold:
            parent[find(pred_to_gold[a])] = find(pred_to_gold[b])
    groups: dict[str, set[str]] = {}
    for ref in list(parent):
        groups.setdefault(find(ref), set()).add(ref)
    pairs: set[tuple[str, str]] = set()
    for refs in groups.values():
        pairs.update(itertools.combinations(sorted(refs), 2))
    return pairs


def read_extra(extra_labels: Path | None, fold_name: str) -> list[tuple[bytes, str]]:
    folder = extra_labels / fold_name if extra_labels else None
    if folder is None or not folder.exists():
        return []
    return [(png.read_bytes(), png.with_suffix(".txt").read_text(encoding="utf-8")) for png in sorted(folder.glob("*.png"))]


def write_fold_dataset(root: Path, drawings: list[dict[str, Any]], train_sheets: set[str], extra: list[tuple[bytes, str]]) -> Path:
    images, labels = root / "images" / "train", root / "labels" / "train"
    images.mkdir(parents=True, exist_ok=True)
    labels.mkdir(parents=True, exist_ok=True)
    index_of = {k: i for i, k in enumerate(CLASS_KEYS)}
    for d in drawings:
        if d["sheet"] not in train_sheets:
            continue
        lines = [
            f"{index_of[s['class_key']]} {s['cx']:.6f} {s['cy']:.6f} {s['w']:.6f} {s['h']:.6f}"
            for s in d["project"]["symbols"]
            if not (recipe_drops_wire_bends() and is_wire_bend(s))
        ]
        label_text = "\n".join(lines) + "\n"
        (images / f"{d['sheet']}.png").write_bytes(d["image"])
        (labels / f"{d['sheet']}.txt").write_text(label_text, encoding="utf-8")
        # Rotated copies of training drawings only: a test drawing never reaches training in any orientation.
        for degrees in RECIPE.get("rotated_copies", ()):
            png, rotated = rotated_copy(d["image"], label_text, degrees)
            (images / f"{d['sheet']}_r{degrees}.png").write_bytes(png)
            (labels / f"{d['sheet']}_r{degrees}.txt").write_text(rotated, encoding="utf-8")
    for i, (png, label_text) in enumerate(extra):
        (images / f"extra_{i:04d}.png").write_bytes(png)
        (labels / f"extra_{i:04d}.txt").write_text(label_text, encoding="utf-8")
        for degrees in RECIPE.get("rotated_copies", ()):
            rotated_png, rotated = rotated_copy(png, label_text, degrees)
            (images / f"extra_{i:04d}_r{degrees}.png").write_bytes(rotated_png)
            (labels / f"extra_{i:04d}_r{degrees}.txt").write_text(rotated, encoding="utf-8")
    scan_copies = int(RECIPE.get("synthetic_scan_copies", 0))
    if scan_copies:
        from synthetic_scan_data import add_scan_copies

        add_scan_copies(images, labels, index_of["contact_b"], scan_copies, seed=int(RECIPE["seed"]))
    data_yaml = root / "data.yaml"
    data_yaml.write_text(
        json.dumps({"path": str(root), "train": "images/train", "val": "images/train",
                    "names": dict(enumerate(CLASS_KEYS))}),
        encoding="utf-8",
    )
    return data_yaml


def train_fold(recipe: dict[str, Any], data_yaml: Path, out: Path) -> Path:
    from ultralytics import YOLO

    out = out.resolve()
    weights = out / "train" / "weights" / "last.pt"
    if weights.exists():
        return weights
    YOLO(recipe["model"]).train(
        data=str(data_yaml), epochs=recipe["epochs"], imgsz=recipe["imgsz"], batch=recipe["batch"],
        seed=recipe["seed"], deterministic=True, val=False, plots=False, project=str(out), name="train",
        exist_ok=True, verbose=False, workers=recipe.get("workers", 2), **recipe.get("train_args", {}),
    )
    return weights


def fine_tune_weights(source: Path, folds: list[list[str]], fold_index: int) -> Path:
    """Only a cross-fitted model trained without this fold may initialize its fine-tune."""
    summary = json.loads((source / "summary.json").read_text(encoding="utf-8"))
    if summary.get("mode") != "cross-validation" or summary.get("folds") != folds:
        raise ValueError("fine-tune source must use the same drawing-level cross-validation folds")
    weights = source / f"fold{fold_index}" / "train" / "weights" / "last.pt"
    if not weights.is_file():
        raise FileNotFoundError(weights)
    return weights


def predict(weights: Path, recipe: dict[str, Any], drawing: dict[str, Any], conf: float) -> list[dict[str, Any]]:
    from ultralytics import YOLO

    model = YOLO(str(weights))
    image = Image.open(io.BytesIO(drawing["image"])).convert("RGB")
    width, height = image.size
    result = model.predict(image, conf=conf, imgsz=recipe["imgsz"], verbose=False, **recipe.get("predict_args", {}))[0]
    names = model.names
    out = []
    for i, ((cx, cy, w, h), score, cls) in enumerate(zip(result.boxes.xywhn.tolist(), result.boxes.conf.tolist(), result.boxes.cls.tolist(), strict=True)):
        out.append({
            "id": f"p{i}", "class_name": names[int(cls)], "confidence": float(score),
            "cx": cx, "cy": cy, "w": w, "h": h, "kind": detection_kind(w, h),
            "bbox": to_box(cx, cy, w, h, width, height),
        })
    return out


def run_structure(drawing: dict[str, Any], pred: list[dict[str, Any]], work: Path) -> dict[str, Any]:
    from analysis.runtime import analyze_detections

    image_path = work / f"{drawing['sheet']}.png"
    image_path.write_bytes(drawing["image"])
    detections = [
        {"cx": p["cx"], "cy": p["cy"], "w": p["w"], "h": p["h"], "class_key": p["class_name"],
         "class_label": p["class_name"], "confidence": p["confidence"]}
        for p in pred if p["kind"] == "symbol"
    ]
    return analyze_detections(image_path, detections, source={"sheet": drawing["sheet"]})


def summarize(per_drawing: list[dict[str, Any]]) -> dict[str, Any]:
    tot: dict[str, Counter] = {k: Counter() for k in ("aware", "agnostic", "region", "pairs")}
    confusion: Counter = Counter()
    per_class: dict[str, Counter] = {}
    type_c = type_t = label_c = label_t = 0
    for d in per_drawing:
        for k in ("aware", "agnostic", "region", "pairs"):
            tot[k].update(d.get(k, {}))
        confusion.update({tuple(k.split("->")): v for k, v in d["confusion"].items()})
        for c, cnt in d["per_class"].items():
            per_class.setdefault(c, Counter()).update(cnt)
        type_c += d["type_correct"]
        type_t += d["type_total"]
        label_c += d["label_correct"]
        label_t += d["label_total"]
    out = {
        "drawings": len(per_drawing),
        "symbol_f1": prf(tot["aware"]["tp"], tot["aware"]["fp"], tot["aware"]["fn"]),
        "localization_f1": prf(tot["agnostic"]["tp"], tot["agnostic"]["fp"], tot["agnostic"]["fn"]),
        "type_accuracy": round(type_c / type_t, 4) if type_t else 0.0,
        "label_accuracy": round(label_c / label_t, 4) if label_t else 0.0,
        "label_total": label_t,
        "region_f1": prf(tot["region"]["tp"], tot["region"]["fp"], tot["region"]["fn"]),
        "per_class": {c: prf(v["tp"], v["fp"], v["fn"]) for c, v in sorted(per_class.items())},
        "confusion": {f"{g}->{p}": n for (g, p), n in confusion.most_common()},
    }
    if tot["pairs"]:
        out["connection_f1"] = prf(tot["pairs"]["tp"], tot["pairs"]["fp"], tot["pairs"]["fn"])
    return out


def evaluate(drawing: dict[str, Any], pred: list[dict[str, Any]], structure: dict[str, Any] | None) -> dict[str, Any]:
    gold = gold_items(drawing)
    scored = score_symbols(gold, pred)
    row = {
        "sheet": drawing["sheet"],
        "aware": dict(scored["aware"]), "agnostic": dict(scored["agnostic"]), "region": dict(scored["region"]),
        "type_correct": scored["type_correct"], "type_total": scored["type_total"],
        "label_correct": scored["label_correct"], "label_total": scored["label_total"],
        "confusion": {f"{g}->{p}": n for (g, p), n in scored["confusion"].items()},
        "per_class": {c: dict(v) for c, v in scored["per_class"].items()},
    }
    if structure is not None:
        g_sym = scored["g_sym"]
        sym_ids = [s["id"] for s in structure.get("symbols", [])]
        pred_to_gold = {sym_ids[pi]: g_sym[gi]["ref"] for gi, pi, _ in scored["matches"] if pi < len(sym_ids)}
        gp = gold_pairs(drawing, {g["ref"] for g in g_sym})
        pp = predicted_pairs(structure, pred_to_gold)
        row["pairs"] = {"tp": len(gp & pp), "fp": len(pp - gp), "fn": len(gp - pp)}
    return row


CLASS_KEYS: list[str] = []
RECIPE: dict[str, Any] = {}


def recipe_drops_wire_bends() -> bool:
    return bool(RECIPE.get("drop_wire_bends"))


def rotated_drawing(drawing: dict[str, Any], degrees: int) -> dict[str, Any]:
    """The drawing turned counter-clockwise by degrees (90 or 270), gold boxes turned with it."""
    project = dict(drawing["project"])
    project["image_width"], project["image_height"] = project["image_height"], project["image_width"]
    project["symbols"] = [
        {**s, **dict(zip(("cx", "cy", "w", "h"), rotate_box(s["cx"], s["cy"], s["w"], s["h"], degrees), strict=True))}
        for s in project["symbols"]
    ]
    return {**drawing, "image": rotate_image(drawing["image"], degrees), "project": project}


def score_rotated(args: argparse.Namespace, drawings: list[dict[str, Any]], degrees: int) -> None:
    """Score each fold's saved model on its own test drawings turned by degrees (customer: 横書きの図面)."""
    by_sheet = {d["sheet"]: d for d in drawings}
    tot: Counter = Counter()
    per_class: dict[str, Counter] = {}
    for fold_index, test_sheets in enumerate(folds_of(drawings, args.folds)):
        if args.only_folds and fold_index not in args.only_folds:
            continue
        weights = args.work / f"fold{fold_index}" / "train" / "weights" / "last.pt"
        for sheet in test_sheets:
            turned = rotated_drawing(by_sheet[sheet], degrees)
            scored = score_symbols(gold_items(turned), predict(weights, RECIPE, turned, RECIPE["conf"]))
            tot.update({k: scored["aware"][k] for k in ("tp", "fp", "fn")})
            for name, counts in scored["per_class"].items():
                per_class.setdefault(name, Counter()).update(counts)
    print(json.dumps({
        "run": args.work.name, "degrees": degrees, "symbol_f1": prf(tot["tp"], tot["fp"], tot["fn"]),
        "per_class_f1": {name: prf(c["tp"], c["fp"], c["fn"])["f1"] for name, c in sorted(per_class.items())},
    }, ensure_ascii=False))


def rescore(args: argparse.Namespace, drawings: list[dict[str, Any]]) -> None:
    """Score saved predictions of a finished run again, e.g. under --strict-iou."""
    rows = []
    by_sheet = {d["sheet"]: d for d in drawings}
    for pred_file in sorted(args.work.glob("fold*/pred/*.json")):
        drawing = by_sheet[pred_file.stem]
        width, height = int(drawing["project"]["image_width"]), int(drawing["project"]["image_height"])
        pred = json.loads(pred_file.read_text(encoding="utf-8"))
        for p in pred:
            p["bbox"] = to_box(p["cx"], p["cy"], p["w"], p["h"], width, height)
        row = evaluate(drawing, pred, None)
        row["fold"] = int(pred_file.parent.parent.name.removeprefix("fold"))
        rows.append(row)
    summary = summarize(rows)
    print(json.dumps({"drawings": summary["drawings"], "strict_iou": STRICT_IOU, "symbol_f1": summary["symbol_f1"],
                      "type_accuracy": summary["type_accuracy"], "per_class": summary["per_class"],
                      "confusion": summary["confusion"]}, ensure_ascii=False))


def main() -> None:
    global CLASS_KEYS, RECIPE, STRICT_IOU
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--gold", type=Path, nargs="+", required=True)
    parser.add_argument("--work", type=Path, required=True)
    parser.add_argument("--recipe", type=Path, help="JSON overriding DEFAULT_RECIPE")
    parser.add_argument("--folds", type=int, default=5)
    parser.add_argument("--only-folds", type=int, nargs="*", help="run a subset of folds (debugging)")
    parser.add_argument("--train-drawings", type=int,
                        help="train each fold on this many of its training drawings (learning curve; test drawings unchanged)")
    parser.add_argument("--weights", type=Path, help="score one fixed model on every drawing instead of training per fold")
    parser.add_argument("--structure", action="store_true", help="also run wire analysis and score connection pairs")
    parser.add_argument("--extra-labels", type=Path,
                        help="directory of <fold>/<name>.png + .txt pseudo labels per fold (all/ for --train-all)")
    parser.add_argument("--rescore", action="store_true", help="score the saved predictions under --work without training")
    parser.add_argument("--strict-iou", action="store_true", help="match dots by box IoU too (the pre-2026-11 rule)")
    parser.add_argument("--train-all", action="store_true", help="train one model on every drawing with the recipe (no scoring)")
    parser.add_argument("--fine-tune-from", type=Path,
                        help="cross-validation run whose matching fold weights initialize each training fold")
    parser.add_argument("--score-rotated", type=int, choices=(90, 270),
                        help="score the saved fold models on their test drawings turned by this many degrees (no training)")
    args = parser.parse_args()
    if args.weights and args.fine_tune_from:
        parser.error("--weights and --fine-tune-from cannot be combined")

    recipe = {**DEFAULT_RECIPE, **(json.loads(args.recipe.read_text()) if args.recipe else {})}
    RECIPE = recipe
    STRICT_IOU = args.strict_iou
    args.work = args.work.resolve()
    CLASS_KEYS, drawings = load_gold(args.gold)
    if args.rescore:
        rescore(args, drawings)
        return
    if args.score_rotated:
        score_rotated(args, drawings, args.score_rotated)
        return
    if args.train_all:
        if args.fine_tune_from:
            parser.error("--fine-tune-from requires cross-validation, not --train-all")
        extra = read_extra(args.extra_labels, "all")
        data_yaml = write_fold_dataset(args.work / "all" / "dataset", drawings, {d["sheet"] for d in drawings}, extra)
        weights = train_fold(recipe, data_yaml, args.work / "all")
        print(json.dumps({"weights": str(weights), "drawings": len(drawings), "extra_pages": len(extra), "recipe": recipe},
                         ensure_ascii=False))
        return
    by_sheet = {d["sheet"]: d for d in drawings}
    folds = folds_of(drawings, args.folds)
    args.work.mkdir(parents=True, exist_ok=True)
    started = time.time()
    rows: list[dict[str, Any]] = []
    errors = 0
    for fold_index, test_sheets in enumerate(folds):
        if args.only_folds and fold_index not in args.only_folds:
            continue
        fold_dir = args.work / f"fold{fold_index}"
        if args.weights:
            weights = args.weights
        else:
            train_sheets = {d["sheet"] for d in drawings} - set(test_sheets)
            if train_sheets & set(test_sheets):
                raise AssertionError("test drawing leaked into training")
            train_sheets = subsample_train(train_sheets, args.train_drawings, int(recipe["seed"]), fold_index)
            extra = read_extra(args.extra_labels, f"fold{fold_index}")
            data_yaml = write_fold_dataset(fold_dir / "dataset", drawings, train_sheets, extra)
            fold_recipe = recipe
            if args.fine_tune_from:
                fold_recipe = {**recipe, "model": str(fine_tune_weights(args.fine_tune_from, folds, fold_index))}
            weights = train_fold(fold_recipe, data_yaml, fold_dir)
        for sheet in test_sheets:
            drawing = by_sheet[sheet]
            pred = predict(weights, recipe, drawing, recipe["conf"])
            structure = None
            if args.structure:
                try:
                    structure = run_structure(drawing, pred, fold_dir / "structure")
                except Exception as exc:  # noqa: BLE001  one failing page must not hide the others' scores
                    errors += 1
                    print(f"[structure error] {sheet}: {exc}", file=sys.stderr)
                if structure is not None:
                    symbols_in_order = [p for p in pred if p["kind"] == "symbol"]
                    for p, s in zip(symbols_in_order, structure.get("symbols", []), strict=False):
                        p["label"] = s.get("label") or ""
            row = evaluate(drawing, pred, structure)
            row["fold"] = fold_index
            rows.append(row)
            (fold_dir / "pred").mkdir(parents=True, exist_ok=True)
            (fold_dir / "pred" / f"{sheet}.json").write_text(json.dumps([
                {k: v for k, v in p.items() if k != "bbox"} for p in pred]), encoding="utf-8")
    summary = summarize(rows)
    summary.update({
        "recipe": recipe,
        "recipe_sha": hashlib.sha256(json.dumps(recipe, sort_keys=True).encode()).hexdigest()[:12],
        "folds": folds,
        "train_drawings": args.train_drawings,
        "mode": "fixed-weights" if args.weights else "cross-validation",
        "fine_tune_from": str(args.fine_tune_from) if args.fine_tune_from else None,
        "errors": errors,
        "seconds": round(time.time() - started, 1),
        "per_drawing": rows,
    })
    (args.work / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    brief = {k: summary[k] for k in ("mode", "drawings", "errors", "symbol_f1", "localization_f1", "type_accuracy",
                                     "label_accuracy", "region_f1", "seconds")}
    if "connection_f1" in summary:
        brief["connection_f1"] = summary["connection_f1"]
    brief["confusion_top"] = dict(list(summary["confusion"].items())[:8])
    print(json.dumps(brief, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()

