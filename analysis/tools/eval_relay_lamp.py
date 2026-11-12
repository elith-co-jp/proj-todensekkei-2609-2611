"""Count relay-coil and indicator-lamp confusion on fixed evaluation drawings."""

from __future__ import annotations

import _repo_path  # noqa: F401

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any

from analysis.relay_lamp import CIRCLE_CLASSES
from analysis.runtime import analyze_detections
from holdout_cv import gold_items, load_gold, match


def summarize_decisions(decisions: list[dict[str, Any]]) -> dict[str, Any]:
    gold = Counter(decision["gold_class"] for decision in decisions)
    before = Counter(
        f"{decision['gold_class']}->{decision['detected_class']}"
        for decision in decisions
        if decision["gold_class"] != decision["detected_class"]
    )
    after = Counter(
        f"{decision['gold_class']}->{decision['final_class']}"
        for decision in decisions
        if decision["gold_class"] != decision["final_class"]
    )
    return {
        "gold": {class_name: gold[class_name] for class_name in CIRCLE_CLASSES},
        "matched": len(decisions),
        "before": {"errors": sum(before.values()), "confusion": dict(sorted(before.items()))},
        "after": {"errors": sum(after.values()), "confusion": dict(sorted(after.items()))},
        "review_required": sum(decision["status"] == "要確認" for decision in decisions),
        "changed": sum(decision["detected_class"] != decision["final_class"] for decision in decisions),
        "both_classes_scored": all(gold[class_name] > 0 for class_name in CIRCLE_CLASSES),
    }


def evaluate_drawing(drawing: dict[str, Any], pred_path: Path, work: Path) -> tuple[list[dict[str, Any]], int, int]:
    predictions = json.loads(pred_path.read_text(encoding="utf-8"))
    detections = [
        {
            "cx": prediction["cx"],
            "cy": prediction["cy"],
            "w": prediction["w"],
            "h": prediction["h"],
            "class_key": prediction["class_name"],
            "class_label": prediction["class_name"],
            "confidence": prediction["confidence"],
        }
        for prediction in predictions
        if prediction["kind"] == "symbol"
    ]
    image_path = work / f"{drawing['sheet']}.png"
    image_path.write_bytes(drawing["image"])
    structure = analyze_detections(image_path, detections, source={"sheet": drawing["sheet"]})
    gold_circles = [item for item in gold_items(drawing) if item["class_name"] in CIRCLE_CLASSES]
    found_circles = []
    for symbol in structure["symbols"]:
        detected_class = symbol.get("detected_class", symbol["class_name"])
        if detected_class not in CIRCLE_CLASSES and symbol["class_name"] not in CIRCLE_CLASSES:
            continue
        found_circles.append({
            "bbox": symbol["bbox"],
            "detected_class": detected_class,
            "final_class": symbol["class_name"],
            "status": symbol["symbol_review"]["status"],
        })
    pairs = match(gold_circles, found_circles, class_aware=False)
    decisions = [
        {
            "sheet": drawing["sheet"],
            "gold_class": gold_circles[gold_index]["class_name"],
            "detected_class": found_circles[pred_index]["detected_class"],
            "final_class": found_circles[pred_index]["final_class"],
            "status": found_circles[pred_index]["status"],
            "iou": round(score, 4),
        }
        for gold_index, pred_index, score in pairs
    ]
    return decisions, len(gold_circles) - len(pairs), len(found_circles) - len(pairs)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gold", type=Path, nargs="+", required=True)
    parser.add_argument("--pred-run", type=Path, required=True)
    parser.add_argument("--work", type=Path, required=True)
    args = parser.parse_args()

    _, drawings = load_gold(args.gold)
    pred_files = {path.stem: path for path in args.pred_run.glob("fold*/pred/*.json")}
    missing = [drawing["sheet"] for drawing in drawings if drawing["sheet"] not in pred_files]
    if missing:
        raise ValueError(f"missing saved predictions for: {', '.join(missing)}")
    args.work.mkdir(parents=True, exist_ok=True)
    decisions: list[dict[str, Any]] = []
    unmatched_gold = unmatched_predictions = 0
    for drawing in drawings:
        rows, missing_count, extra_count = evaluate_drawing(
            drawing, pred_files[drawing["sheet"]], args.work
        )
        decisions.extend(rows)
        unmatched_gold += missing_count
        unmatched_predictions += extra_count
    summary = summarize_decisions(decisions)
    summary.update({
        "drawings": len(drawings),
        "unmatched_gold": unmatched_gold,
        "unmatched_predictions": unmatched_predictions,
    })
    output = {"summary": summary, "decisions": decisions}
    (args.work / "summary.json").write_text(
        json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
