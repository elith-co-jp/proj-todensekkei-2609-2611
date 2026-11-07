"""Score how well detectors tell make (a) from break (b) contacts on pages whose contacts a person checked.

The pages must be left out of every model's training. Each checked contact is matched to the detected
contact box it overlaps most (IoU >= 0.5, either class); unmatched checked contacts are misses and
unmatched detected contacts are extra detections.

Example:
    python analysis/tools/eval_contact_classes.py --run runs/cv --pages data/unseen_pages \
        --reviewed contacts_reviewed.json --only test_pages.txt
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

CONTACTS = ("contact_a", "contact_b")


def _iou(a: list[float], b: list[float]) -> float:
    inter = max(0.0, min(a[2], b[2]) - max(a[0], b[0])) * max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / union if union > 0 else 0.0


def score_page(checked: list[dict], detected: list[tuple[str, list[float]]]) -> Counter:
    """Counter of (checked class, detected class | "miss") plus ("extra", detected class)."""
    out: Counter = Counter()
    pairs = sorted(((_iou(c["box"], d[1]), i, j) for i, c in enumerate(checked) for j, d in enumerate(detected)), reverse=True)
    used_c: set[int] = set()
    used_d: set[int] = set()
    for iou, i, j in pairs:
        if iou < 0.5 or i in used_c or j in used_d:
            continue
        used_c.add(i)
        used_d.add(j)
        out[(checked[i]["class"], detected[j][0])] += 1
    for i, c in enumerate(checked):
        if i not in used_c:
            out[(c["class"], "miss")] += 1
    for j, d in enumerate(detected):
        if j not in used_d:
            out[("extra", d[0])] += 1
    return out


def summarize(counts: Counter) -> dict:
    tp = counts[("contact_b", "contact_b")]
    fp = counts[("contact_a", "contact_b")] + counts[("extra", "contact_b")]
    fn = counts[("contact_b", "contact_a")] + counts[("contact_b", "miss")]
    p = tp / (tp + fp) if tp + fp else 0.0
    r = tp / (tp + fn) if tp + fn else 0.0
    return {"b_precision": round(p, 4), "b_recall": round(r, 4), "b_f1": round(2 * p * r / (p + r), 4) if p + r else 0.0,
            "b_as_a": counts[("contact_b", "contact_a")], "a_as_b": counts[("contact_a", "contact_b")],
            "a_correct": counts[("contact_a", "contact_a")], "misses": counts[("contact_a", "miss")] + counts[("contact_b", "miss")],
            "counts": {f"{k[0]}->{k[1]}": v for k, v in sorted(counts.items())}}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--run", type=Path, help="score every */train/weights/last.pt under this run")
    parser.add_argument("--weights", type=Path, nargs="*", default=[])
    parser.add_argument("--pages", type=Path, required=True)
    parser.add_argument("--reviewed", type=Path, required=True)
    parser.add_argument("--only", type=Path, required=True, help="text file of the held-out page names")
    parser.add_argument("--conf", type=float, default=0.5)
    parser.add_argument("--imgsz", type=int, default=1280)
    args = parser.parse_args()

    from ultralytics import YOLO

    keep = set(args.only.read_text(encoding="utf-8").split())
    checked: dict[str, list[dict]] = {}
    for r in json.loads(args.reviewed.read_text(encoding="utf-8")):
        if r["page"] in keep:
            checked.setdefault(r["page"], []).append(r)
    weights = sorted(args.run.glob("*/train/weights/last.pt")) if args.run else []
    weights += args.weights
    total: Counter = Counter()
    per_model = {}
    for w in weights:
        model = YOLO(str(w))
        counts: Counter = Counter()
        for page in sorted(keep):
            result = model.predict(str(args.pages / f"{page}.png"), conf=args.conf, imgsz=args.imgsz, max_det=1000, verbose=False)[0]
            detected = [(model.names[int(c)], b) for b, c in zip(result.boxes.xyxy.tolist(), result.boxes.cls.tolist(), strict=True)
                        if model.names[int(c)] in CONTACTS]
            counts += score_page(checked.get(page, []), detected)
        per_model[str(w)] = summarize(counts)
        total += counts
    print(json.dumps({"pages": len(keep), "checked": sum(len(v) for v in checked.values()),
                      "checked_b": sum(r["class"] == "contact_b" for v in checked.values() for r in v),
                      "models": len(weights), "all_models": summarize(total), "per_model": per_model}, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
