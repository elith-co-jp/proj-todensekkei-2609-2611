"""文字まわりの精度（ラベル・リレー表・シート間参照・シート番号・文字の誤検出・領域の種類）を測る。

正解は seq-annotator の書き出し ZIP（30 図面）と、人が目で確認した補助の正解ファイル。
規則の調整は tune（シート番号順で偶数番目の 15 図面）だけで行い、report（奇数番目）で報告する。

例:
    python analysis/tools/evaluate_text_extraction.py --gold a.zip b.zip --cache runs/ocr \
        --unseen-pages runs/unseen --sheet-gt sheet_gt.json --relay-gt relay_gt --cv-preds runs/cv --split all
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import re
import sys
import tempfile
from collections import Counter, defaultdict
from collections.abc import Callable
from pathlib import Path
from typing import Any

from PIL import Image

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from holdout_cv import gold_items, load_gold, norm_label, prf, score_symbols, to_box  # noqa: E402

from analysis import text_extraction as tx  # noqa: E402



def split_of(sheets: list[str], split: str) -> set[str]:
    ordered = sorted(sheets)
    if split == "tune":
        return set(ordered[0::2])
    if split == "report":
        return set(ordered[1::2])
    return set(ordered)


class Ocr:
    def __init__(self, cache: Path) -> None:
        self.cache = cache
        self.cache.mkdir(parents=True, exist_ok=True)
        self._reader = None

    def reader(self) -> Any:
        if self._reader is None:
            from rapidocr_onnxruntime import RapidOCR

            self._reader = RapidOCR()
        return self._reader

    def page(self, png: bytes) -> list[tx.TextItem]:
        path = self.cache / f"{hashlib.sha256(png).hexdigest()[:16]}_page-{tx.PAGE_OCR_VERSION}.json"
        if path.exists():
            return [{**i, "box": tuple(i["box"])} for i in json.loads(path.read_text())]
        items = tx.read_page_text(Image.open(io.BytesIO(png)), self.reader())
        path.write_text(json.dumps(items, ensure_ascii=False))
        return items

    def crops(self, png: bytes, boxes: list[dict[str, float]]) -> list[list[tx.TextItem]]:
        path = self.cache / f"{hashlib.sha256(png).hexdigest()[:16]}_crop-{tx.CROP_OCR_VERSION}.json"
        cached = json.loads(path.read_text()) if path.exists() else {}
        keys = [",".join(str(round(v)) for v in b.values()) for b in boxes]
        missing = [k for k in keys if k not in cached]
        if missing:
            image = Image.open(io.BytesIO(png)).convert("RGB")
            for key, box in zip(keys, boxes, strict=True):
                if key not in cached:
                    cached[key] = tx.read_crop_text(image, box, self.reader())
            path.write_text(json.dumps(cached, ensure_ascii=False))
        return [[{**i, "box": tuple(i["box"])} for i in cached[k]] for k in keys]


def runtime_labels(ocr: Ocr, png: bytes, gold: list[dict[str, Any]]) -> list[str]:
    """現行ランタイムと同じ読み方（connector・contact_a だけ、記号ごとに切り出して OCR）。"""
    path = ocr.cache / f"{hashlib.sha256(png).hexdigest()[:16]}_runtime_labelmap.json"
    keys = [f"{g['class_name']}|{','.join(str(round(v)) for v in g['bbox'].values())}" for g in gold]
    cached = json.loads(path.read_text()) if path.exists() else {}
    if all(k in cached for k in keys):
        return [cached[k] for k in keys]
    from run_yolo_e2e_pipeline import OCR_ALLOWLIST, enrich_symbols_with_label_ocr

    image = Image.open(io.BytesIO(png)).convert("RGB")
    symbols = [{"class_name": g["class_name"], "bbox": {k: round(v) for k, v in g["bbox"].items()}} for g in gold]
    enrich_symbols_with_label_ocr(
        image, symbols, reader=ocr.reader(), engine="rapidocr", classes={"connector", "contact_a"},
        padding=8, preprocess="gray", scale=4, allowlist=OCR_ALLOWLIST, label_source="structured_match",
    )
    labels = [s.get("label", "") for s in symbols]
    cached.update(zip(keys, labels, strict=True))
    path.write_text(json.dumps(cached, ensure_ascii=False))
    return labels


def eval_labels(drawings: list[dict[str, Any]], ocr: Ocr, baseline: bool) -> dict[str, Any]:
    stats: dict[str, Counter] = defaultdict(Counter)
    misses: list[str] = []
    for d in drawings:
        gold = [g for g in gold_items(d) if g["label"]]
        if baseline:
            preds = runtime_labels(ocr, d["image"], gold)
        else:
            items = ocr.page(d["image"])
            lexicon = tx.page_lexicon(items)
            crop_gold = [g for g in gold if g["class_name"] in tx.CROP_OCR_CLASSES]
            crop_items = dict(zip(map(id, crop_gold), ocr.crops(d["image"], [g["bbox"] for g in crop_gold]), strict=True))
            preds = []
            for g in gold:
                page_label = tx.symbol_label(items, g["bbox"], g["class_name"], lexicon)
                if id(g) in crop_items:
                    crop_label = tx.symbol_label(crop_items[id(g)], g["bbox"], g["class_name"], lexicon)
                    preds.append(tx.best_label(crop_label, page_label, lexicon))
                else:
                    preds.append(page_label)
        for g, pred in zip(gold, preds, strict=True):
            group = g["kind"]
            key = f"{group}:{g['class_name']}"
            device_ok = tx.match_key(tx.device_token(pred)) == tx.match_key(tx.device_token(g["label"]))
            strict_ok = tx.device_token(pred) == tx.device_token(g["label"])
            full_ok = norm_label(pred) == norm_label(g["label"])
            for k in (key, f"{group}:ALL"):
                stats[k].update(n=1, device=int(device_ok), strict=int(strict_ok), full=int(full_ok))
            if group == "symbol" and not device_ok:
                misses.append(f"{d['sheet']} {g['class_name']} gold={g['label']!r} pred={pred!r}")

    def rate(c: Counter) -> dict[str, Any]:
        return {"n": c["n"], "device_acc": round(c["device"] / c["n"], 4),
                "device_acc_strict": round(c["strict"] / c["n"], 4), "full_acc": round(c["full"] / c["n"], 4)}

    return {"scored": stats["symbol:ALL"]["n"], "errors": 0, "by_group": {k: rate(v) for k, v in sorted(stats.items())},
            "device_misses": misses}


def sheet_service_top1(png: bytes, cache: Path) -> str:
    from services.sheet_number_service import suggest_sheet_numbers

    path = cache / f"{hashlib.sha256(png).hexdigest()[:16]}_sheet_service.json"
    if path.exists():
        return json.loads(path.read_text())
    with tempfile.NamedTemporaryFile(suffix=".png") as tmp:
        tmp.write(png)
        tmp.flush()
        candidates = suggest_sheet_numbers(Path(tmp.name))
    top = candidates[0]["text"] if candidates else ""
    path.write_text(json.dumps(top))
    return top


def eval_sheet(pages: list[tuple[str, str, bytes]], ocr: Ocr, baseline: bool) -> dict[str, Any]:
    correct, wrong = 0, []
    for name, truth, png in pages:
        if baseline:
            top = sheet_service_top1(png, ocr.cache)
        else:
            with Image.open(io.BytesIO(png)) as im:
                width, height = im.size
            candidates = tx.sheet_number_candidates(ocr.page(png), width, height)
            top = candidates[0]["text"] if candidates else ""
        if top == truth:
            correct += 1
        else:
            wrong.append(f"{name} truth={truth} top1={top!r}")
    return {"scored": len(pages), "errors": 0, "top1": round(correct / len(pages), 4) if pages else 0.0, "wrong": wrong}


def gold_refs(d: dict[str, Any]) -> list[tuple[str, dict[str, float]]]:
    out = []
    for g in gold_items(d):
        if g["class_name"] != "other":
            continue
        m = re.search(rf"\(({tx.SHEET_REF})\)|({tx.SHEET_REF})\s*(?:へ|より)", tx.normalize(g["label"]).replace(" ", ""))
        if m:
            out.append((m.group(1) or m.group(2), g["bbox"]))
    return out


def eval_refs(drawings: list[dict[str, Any]], ocr: Ocr, preds_for: Callable[[str], list[dict[str, Any]]], baseline: bool) -> dict[str, Any]:
    inst = Counter()
    pairs = Counter()
    misses: list[str] = []
    for d in drawings:
        gold = gold_refs(d)
        if baseline:
            pred: list[dict[str, Any]] = []
        else:
            with Image.open(io.BytesIO(d["image"])) as im:
                width, height = im.size
            refs = tx.sheet_references(ocr.page(d["image"]), symbols_from_preds(preds_for(d["sheet"]), width, height))
            pred = [r for r in refs if r["kind"] == "wire"]
        used: set[int] = set()
        for target, box in gold:
            hit = next((i for i, p in enumerate(pred) if i not in used and p["target_sheet"] == target
                        and tx._inside(tx._center(tx._as_box(p["box"])), tx._as_box(box), 12)), None)
            if hit is None:
                misses.append(f"{d['sheet']} FN {target}")
            else:
                used.add(hit)
        for i, p in enumerate(pred):
            if i not in used:
                misses.append(f"{d['sheet']} FP {p['target_sheet']} at {[round(v) for v in tx._as_box(p['box'])]}")
        inst.update(tp=len(used), fp=len(pred) - len(used), fn=len(gold) - len(used))
        g_set = {t for t, _ in gold}
        p_set = {p["target_sheet"] for p in pred}
        pairs.update(tp=len(g_set & p_set), fp=len(p_set - g_set), fn=len(g_set - p_set))
    return {"scored": inst["tp"] + inst["fn"], "errors": 0, "instance": prf(inst["tp"], inst["fp"], inst["fn"]),
            "per_page_set": prf(pairs["tp"], pairs["fp"], pairs["fn"]), "misses": misses}


def symbols_from_preds(preds: list[dict[str, Any]], width: int, height: int) -> list[dict[str, Any]]:
    return [{"class_name": p["class_name"], "bbox": to_box(p["cx"], p["cy"], p["w"], p["h"], width, height)}
            for p in preds if p["kind"] == "symbol"]


def relay_key(label: str) -> tuple[str, ...]:
    text = re.sub(rf"\(?{tx.SHEET_REF}\)?", " ", tx.normalize(label))
    return tuple(tx.match_key(t) for t in text.split()[:2])


def eval_relay(drawings: list[dict[str, Any]], ocr: Ocr, relay_gt: Path | None, baseline: bool) -> dict[str, Any]:
    link = Counter()
    rows_c = Counter()
    gt_drawings = 0
    misses: list[str] = []
    for d in drawings:
        with Image.open(io.BytesIO(d["image"])) as im:
            width, height = im.size
        rows = [] if baseline else tx.parse_relay_table(ocr.page(d["image"]), width, height)
        gt_path = relay_gt / f"{d['sheet']}.json" if relay_gt else None
        gt_rows = json.loads(gt_path.read_text())["rows"] if gt_path and gt_path.exists() else None
        devices = _devices(rows)
        gt_devices = _devices(gt_rows) if gt_rows is not None else None
        for g in gold_items(d):
            if g["class_name"] not in {"relay_coil", "contact_a", "contact_b"} or not g["label"] or g["kind"] != "symbol":
                continue
            key = relay_key(g["label"])
            found = _found(key, devices)
            group = "cross_sheet_label" if re.search(tx.SHEET_REF, tx.normalize(g["label"])) else "on_sheet_label"
            role = "coil" if g["class_name"] == "relay_coil" else "contact"
            for k in ("all", group, role):
                link.update({f"{k}_n": 1, f"{k}_found": int(found)})
            if gt_devices is not None:
                for k in ("gt_drawings_parsed", f"gt_drawings_parsed_{role}"):
                    link.update({f"{k}_n": 1, f"{k}_found": int(found)})
                for k in ("gt_drawings_ceiling", f"gt_drawings_ceiling_{role}"):
                    link.update({f"{k}_n": 1, f"{k}_found": int(_found(key, gt_devices))})
            if not found:
                misses.append(f"{d['sheet']} {g['class_name']} {g['label']!r} ({group})")
        if gt_rows is not None:
            gt_drawings += 1
            truth = Counter(_row_key(r) for r in gt_rows)
            pred = Counter(_row_key(r) for r in rows)
            tp = sum((truth & pred).values())
            rows_c.update(tp=tp, fp=sum(pred.values()) - tp, fn=sum(truth.values()) - tp)
            misses.extend(f"{d['sheet']} row FN {k}" for k in (truth - pred))
            misses.extend(f"{d['sheet']} row FP {k}" for k in (pred - truth))
    rate = lambda k: round(link[f"{k}_found"] / link[f"{k}_n"], 4) if link[f"{k}_n"] else 0.0  # noqa: E731
    return {
        "scored": link["all_n"], "errors": 0,
        "link": {k: {"n": link[f"{k}_n"], "found": link[f"{k}_found"], "rate": rate(k)}
                 for k in ("all", "on_sheet_label", "cross_sheet_label", "coil", "contact",
                           "gt_drawings_parsed", "gt_drawings_ceiling", "gt_drawings_parsed_coil", "gt_drawings_ceiling_coil",
                           "gt_drawings_parsed_contact", "gt_drawings_ceiling_contact")},
        "rows": {"drawings": gt_drawings, **prf(rows_c["tp"], rows_c["fp"], rows_c["fn"])},
        "misses": misses,
    }


def _devices(rows: list[dict[str, str]]) -> set[tuple[str, ...]]:
    return {tuple(tx.match_key(t) for t in tx.normalize(r["device"]).split()[:2]) for r in rows}


def _found(key: tuple[str, ...], devices: set[tuple[str, ...]]) -> bool:
    return any(dev[:1] == key[:1] and (len(key) < 2 or dev[1:2] == key[1:2]) for dev in devices)


def _row_key(row: dict[str, str]) -> tuple[str, ...]:
    return tuple(tx.match_key(row.get(k, "")) for k in ("device", "terminal", "contact", "sheet", "position"))


def eval_text_fp(drawings: list[dict[str, Any]], ocr: Ocr, preds_for: Callable[[str], list[dict[str, Any]]], baseline: bool) -> dict[str, Any]:
    before, after = Counter(), Counter()
    removed_tp = removed_fp = 0
    for d in drawings:
        with Image.open(io.BytesIO(d["image"])) as im:
            width, height = im.size
        gold = gold_items(d)
        pred = [{**p, "bbox": to_box(p["cx"], p["cy"], p["w"], p["h"], width, height)} for p in preds_for(d["sheet"])]
        s0 = score_symbols(gold, pred)
        before.update(s0["aware"])
        zones = [] if baseline else tx.text_zones(ocr.page(d["image"]), width, height)
        keep = [p for p in pred if p["kind"] != "symbol" or tx.zone_of(p["bbox"], zones) is None]
        s1 = score_symbols(gold, keep)
        after.update(s1["aware"])
        dropped = {id(p) for p in pred} - {id(p) for p in keep}
        matched = {pi for _, pi, _ in s0["matches"]}
        p_sym = s0["p_sym"]
        removed_tp += sum(1 for i, p in enumerate(p_sym) if id(p) in dropped and i in matched)
        removed_fp += sum(1 for i, p in enumerate(p_sym) if id(p) in dropped and i not in matched)
    return {"scored": before["tp"] + before["fn"], "errors": 0,
            "before": prf(before["tp"], before["fp"], before["fn"]), "after": prf(after["tp"], after["fp"], after["fn"]),
            "removed_tp": removed_tp, "removed_fp": removed_fp}


def gold_region_type(label: str) -> str:
    return "drawing_area" if label.startswith("位置記号") else "panel"


def eval_regions(drawings: list[dict[str, Any]], ocr: Ocr, preds_for: Callable[[str], list[dict[str, Any]]], baseline: bool) -> dict[str, Any]:
    c = Counter()
    confusion = Counter()
    pred_c = Counter()
    unmatched = Counter()
    for d in drawings:
        with Image.open(io.BytesIO(d["image"])) as im:
            width, height = im.size
        gold = [g for g in gold_items(d) if g["kind"] == "region"]
        items = [] if baseline else ocr.page(d["image"])

        def classify(box: dict[str, float], items: list[tx.TextItem] = items, width: int = width, height: int = height) -> str:
            return "unknown" if baseline else tx.classify_region(items, box, width, height)

        for g in gold:
            truth = gold_region_type(g["label"])
            pred = classify(g["bbox"])
            c.update(n=1, ok=int(pred == truth))
            confusion[f"{truth}->{pred}"] += 1
        pred_regions = [{**p, "bbox": to_box(p["cx"], p["cy"], p["w"], p["h"], width, height)} for p in preds_for(d["sheet"]) if p["kind"] == "region"]
        used: set[int] = set()
        for p in pred_regions:
            best = max(((iou_box(p["bbox"], g["bbox"]), i) for i, g in enumerate(gold) if i not in used), default=(0.0, -1))
            pred = classify(p["bbox"])
            if best[0] >= 0.5:
                used.add(best[1])
                pred_c.update(n=1, ok=int(pred == gold_region_type(gold[best[1]]["label"])))
            else:
                unmatched[pred] += 1
    return {"scored": c["n"], "errors": 0, "accuracy": round(c["ok"] / c["n"], 4) if c["n"] else 0.0,
            "confusion": dict(confusion),
            "predicted_boxes": {"matched": pred_c["n"], "accuracy": round(pred_c["ok"] / pred_c["n"], 4) if pred_c["n"] else 0.0,
                                "unmatched_types": dict(unmatched)}}


def iou_box(a: dict[str, float], b: dict[str, float]) -> float:
    from holdout_cv import iou

    return iou(a, b)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--gold", type=Path, nargs="+", required=True)
    parser.add_argument("--cache", type=Path, required=True, help="OCR 結果のキャッシュ置き場")
    parser.add_argument("--split", choices=["tune", "report", "all"], default="all")
    parser.add_argument("--metrics", default="labels,relay,refs,sheet,text_fp,regions")
    parser.add_argument("--unseen-pages", type=Path, help="正解のない未学習図面の PNG（シート番号だけ測る）")
    parser.add_argument("--sheet-gt", type=Path, help="未学習図面のシート番号の正解 JSON")
    parser.add_argument("--relay-gt", type=Path, help="リレー表の行の正解 JSON（<SHEET>.json）の置き場")
    parser.add_argument("--cv-preds", type=Path, help="交差検証の検出結果（fold*/pred/<SHEET>.json）")
    parser.add_argument("--baseline", action="store_true", help="現行の製品と同じ処理で測る")
    parser.add_argument("--details", action="store_true", help="外れた項目の一覧も出す")
    args = parser.parse_args()

    _, drawings = load_gold(args.gold)
    chosen = split_of([d["sheet"] for d in drawings], args.split)
    drawings = [d for d in drawings if d["sheet"] in chosen]
    ocr = Ocr(args.cache)
    preds: dict[str, list[dict[str, Any]]] = {}
    if args.cv_preds:
        for path in args.cv_preds.glob("fold*/pred/*.json"):
            preds[path.stem] = json.loads(path.read_text())
    preds_for = lambda sheet: preds.get(sheet, [])  # noqa: E731
    metrics = set(args.metrics.split(","))
    out: dict[str, Any] = {"split": args.split, "drawings": len(drawings), "baseline": args.baseline}
    if "labels" in metrics:
        out["labels"] = eval_labels(drawings, ocr, args.baseline)
    if "relay" in metrics:
        out["relay"] = eval_relay(drawings, ocr, args.relay_gt, args.baseline)
    if "refs" in metrics:
        out["refs"] = eval_refs(drawings, ocr, preds_for, args.baseline)
    if "sheet" in metrics:
        out["sheet_gold"] = eval_sheet([(d["sheet"], d["sheet"], d["image"]) for d in drawings], ocr, args.baseline)
        if args.unseen_pages and args.sheet_gt and args.split != "tune":
            truth = json.loads(args.sheet_gt.read_text())["unseen"]
            pages = [(n, s, (args.unseen_pages / f"{n}.png").read_bytes()) for n, s in sorted(truth.items())]
            out["sheet_unseen"] = eval_sheet(pages, ocr, args.baseline)
    if "text_fp" in metrics and args.cv_preds:
        missing = [d["sheet"] for d in drawings if d["sheet"] not in preds]
        out["text_fp"] = eval_text_fp(drawings, ocr, preds_for, args.baseline)
        out["text_fp"]["errors"] = len(missing)
    if "regions" in metrics:
        out["regions"] = eval_regions(drawings, ocr, preds_for, args.baseline)
    if not args.details:
        for value in out.values():
            if isinstance(value, dict):
                for key in ("device_misses", "misses", "wrong"):
                    if key in value:
                        value[key] = len(value[key])
    print(json.dumps(out, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()

