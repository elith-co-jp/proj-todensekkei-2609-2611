"""Ask a local vision LLM whether a detection box shows the symbol class it claims.

Used as an independent check of detections on drawings that have no gold annotations. The
verdict is only trusted after calibrate() reports how often it agrees with gold boxes.

Example:
    python analysis/tools/llm_symbol_judge.py calibrate --gold a.zip b.zip --cv-run runs/cv_baseline --per-class 15
"""

from __future__ import annotations

import argparse
import base64
import io
import json
import random
import sys
import time
import urllib.request
from collections import Counter
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).resolve().parent))

from holdout_cv import gold_items, load_gold, match  # noqa: E402

OLLAMA = "http://127.0.0.1:11434/api/chat"
MODEL = "qwen3.6:35b-a3b-mtp-q4_K_M"

CLASS_TEXT = {
    "relay_coil": "リレーコイル（円の中に器具番号が書かれた記号）",
    "contact_a": "a接点（2本の平行な短い縦線、メーク接点）",
    "contact_b": "b接点（a接点に斜線が入ったブレーク接点）",
    "terminal": "端子",
    "junction": "分岐点（配線が分かれる黒い小さな点）",
    "diode": "ダイオード",
    "solenoid": "電磁弁・ソレノイド",
    "connector": "コネクタ・端子台の端子（小さな丸や点と、近くの端子番号・コネクタ名）",
    "lamp": "表示灯（円の中に×印）",
    "push_button": "押しボタン",
    "power_bus": "電源母線の端",
    "other": "その他の機器（四角い枠で囲まれた機器名・カード・装置など）",
}


def crop_with_box(image: Image.Image, box: dict[str, float], scale: float = 3.0, size: int = 448) -> bytes:
    cx, cy = (box["x0"] + box["x1"]) / 2, (box["y0"] + box["y1"]) / 2
    half = max(box["x1"] - box["x0"], box["y1"] - box["y0"], 24) * scale / 2
    region = (int(cx - half), int(cy - half), int(cx + half), int(cy + half))
    crop = image.crop(region).convert("RGB")
    draw = ImageDraw.Draw(crop)
    draw.rectangle((box["x0"] - region[0], box["y0"] - region[1], box["x1"] - region[0], box["y1"] - region[1]),
                   outline=(255, 0, 0), width=2)
    crop = crop.resize((size, size))
    buf = io.BytesIO()
    crop.save(buf, format="PNG")
    return buf.getvalue()


def ask(png: bytes, class_name: str, timeout: float = 120.0) -> tuple[bool | None, str]:
    prompt = (
        "これは電気制御回路のシーケンス図（展開接続図）の一部です。"
        f"赤い枠の中にあるのは「{CLASS_TEXT.get(class_name, class_name)}」の記号1個ですか。"
        "枠が文字だけを囲んでいる、配線の一部だけ、別の種類の記号、記号が枠から大きくはみ出している場合は no です。"
        "yes か no の一語だけで答えてください。"
    )
    body = json.dumps({
        "model": MODEL, "stream": False, "think": False,
        "options": {"temperature": 0, "num_predict": 4},
        "messages": [{"role": "user", "content": prompt, "images": [base64.b64encode(png).decode()]}],
    }).encode()
    req = urllib.request.Request(OLLAMA, data=body, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as res:
        answer = json.loads(res.read())["message"]["content"].strip().lower()
    if answer.startswith("yes"):
        return True, answer
    if answer.startswith("no"):
        return False, answer
    return None, answer


def calibrate(args: argparse.Namespace) -> None:
    """Agreement with gold: true boxes should get yes, unseen-model false positives should get no."""
    _, drawings = load_gold(args.gold)
    rng = random.Random(args.seed)
    positives: dict[str, list] = {}
    negatives: list = []
    for d in drawings:
        image = Image.open(io.BytesIO(d["image"])).convert("RGB")
        gold = [g for g in gold_items(d) if g["kind"] == "symbol"]
        for g in gold:
            positives.setdefault(g["class_name"], []).append((image, g["bbox"], g["class_name"]))
        pred_file = next(args.cv_run.glob(f"fold*/pred/{d['sheet']}.json"), None)
        if pred_file is None:
            continue
        width, height = image.size
        pred = []
        for i, p in enumerate(json.loads(pred_file.read_text())):
            if p["kind"] != "symbol":
                continue
            box = {"x0": (p["cx"] - p["w"] / 2) * width, "y0": (p["cy"] - p["h"] / 2) * height,
                   "x1": (p["cx"] + p["w"] / 2) * width, "y1": (p["cy"] + p["h"] / 2) * height}
            pred.append({"class_name": p["class_name"], "bbox": box, "id": i})
        matched = {pi for _, pi, _ in match(gold, pred, class_aware=True)}
        negatives.extend((image, p["bbox"], p["class_name"]) for pi, p in enumerate(pred) if pi not in matched)
    sample = [(*x, True) for items in positives.values() for x in rng.sample(items, min(args.per_class, len(items)))]
    sample += [(*x, False) for x in rng.sample(negatives, min(args.negatives, len(negatives)))]
    counts: Counter = Counter()
    errors = 0
    started = time.time()
    for image, box, class_name, truth in sample:
        try:
            verdict, _ = ask(crop_with_box(image, box), class_name)
        except Exception as exc:  # noqa: BLE001  a timed-out call is counted, not fatal
            errors += 1
            print(f"[llm error] {exc}", file=sys.stderr)
            continue
        key = ("pos" if truth else "neg", {True: "yes", False: "no", None: "unclear"}[verdict])
        counts[key] += 1
        counts[(class_name, "pos" if truth else "neg", key[1])] += 1
    tp, fn = counts[("pos", "yes")], counts[("pos", "no")] + counts[("pos", "unclear")]
    tn, fp = counts[("neg", "no")], counts[("neg", "yes")] + counts[("neg", "unclear")]
    print(json.dumps({
        "scored": tp + fn + tn + fp, "errors": errors, "seconds": round(time.time() - started, 1),
        "accept_true_boxes": round(tp / (tp + fn), 4) if tp + fn else None,
        "reject_false_positives": round(tn / (tn + fp), 4) if tn + fp else None,
        "by_class": {f"{k[0]}:{k[1]}:{k[2]}": v for k, v in sorted(counts.items()) if len(k) == 3},
    }, ensure_ascii=False, indent=1))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="cmd", required=True)
    cal = sub.add_parser("calibrate")
    cal.add_argument("--gold", type=Path, nargs="+", required=True)
    cal.add_argument("--cv-run", type=Path, required=True)
    cal.add_argument("--per-class", type=int, default=15)
    cal.add_argument("--negatives", type=int, default=80)
    cal.add_argument("--seed", type=int, default=2609)
    args = parser.parse_args()
    if args.cmd == "calibrate":
        calibrate(args)


if __name__ == "__main__":
    main()

