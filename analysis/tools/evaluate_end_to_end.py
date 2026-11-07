"""未学習図面で、AI の検出枠から読んだラベルが正解と合う割合を測る（製品の出力そのものの正解率）。

evaluate_text_extraction.py の labels は正解の枠の位置で読む。ここでは交差検証の検出結果（その図面を学習していない
モデルの検出）の枠で読み、検出漏れ・種別の誤り・枠のずれも外れに数える。

Example:
    python analysis/tools/evaluate_end_to_end.py --gold a.zip b.zip --cv-preds runs/cv --cache runs/ocr_cache
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from evaluate_text_extraction import LlmLabels, Ocr, _device_ok, read_labels, split_of, symbols_from_preds  # noqa: E402
from holdout_cv import gold_items, load_gold, match  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--gold", type=Path, nargs="+", required=True)
    parser.add_argument("--cv-preds", type=Path, required=True, help="交差検証の検出結果（fold*/pred/<SHEET>.json）")
    parser.add_argument("--cache", type=Path, required=True, help="OCR 結果のキャッシュ置き場")
    parser.add_argument("--split", choices=["tune", "report", "all"], default="all")
    parser.add_argument("--label-llm", help="器具番号の形でないラベルを読み直すローカル LLM の URL（任意）")
    parser.add_argument("--label-llm-model", default="qwen3.6:35b-a3b-mtp-q4_K_M")
    parser.add_argument("--details", action="store_true")
    args = parser.parse_args()

    _, drawings = load_gold(args.gold)
    chosen = split_of([d["sheet"] for d in drawings], args.split)
    preds = {p.stem: json.loads(p.read_text()) for p in args.cv_preds.glob("fold*/pred/*.json")}
    ocr = Ocr(args.cache)
    llm = LlmLabels(args.cache, args.label_llm, args.label_llm_model) if args.label_llm else None
    stats: dict[str, Counter] = defaultdict(Counter)
    misses: list[str] = []
    missing = 0
    for d in drawings:
        if d["sheet"] not in chosen:
            continue
        if d["sheet"] not in preds:
            missing += 1
            continue
        width, height = int(d["project"]["image_width"]), int(d["project"]["image_height"])
        gold = [g for g in gold_items(d) if g["kind"] == "symbol"]
        boxes = symbols_from_preds(preds[d["sheet"]], width, height)
        labels = [second or label for label, second in read_labels(d, boxes, ocr, llm)]
        matched = {gi: pi for gi, pi, _ in match(gold, boxes, class_aware=False)}
        for gi, g in enumerate(gold):
            if not g["label"]:
                continue
            pi = matched.get(gi)
            detected = pi is not None
            class_ok = detected and boxes[pi]["class_name"] == g["class_name"]
            label_ok = detected and _device_ok(labels[pi], g["label"])
            for key in (g["class_name"], "ALL"):
                stats[key].update(n=1, detected=int(detected), class_ok=int(class_ok), label_ok=int(label_ok),
                                  all_ok=int(class_ok and label_ok))
            if not (class_ok and label_ok):
                got = f"{boxes[pi]['class_name']} {labels[pi]!r}" if detected else "未検出"
                misses.append(f"{d['sheet']} {g['class_name']} gold={g['label']!r} pred={got}")

    def rate(c: Counter) -> dict[str, Any]:
        return {"n": c["n"], "detected": round(c["detected"] / c["n"], 4), "class_ok": round(c["class_ok"] / c["n"], 4),
                "device_number_ok": round(c["label_ok"] / c["n"], 4), "class_and_device_ok": round(c["all_ok"] / c["n"], 4)}

    out = {"split": args.split, "drawings": len(chosen) - missing, "errors": missing,
           "labelled_symbols": {k: rate(v) for k, v in sorted(stats.items())},
           "misses": misses if args.details else len(misses)}
    print(json.dumps(out, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()

