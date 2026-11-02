"""Pseudo-label unlabeled drawings with each fold's model, for semi-supervised training.

Each fold gets labels only from the model trained on that fold's training drawings, so no
evaluation drawing influences its own fold. Output: <out>/fold<k>/<page>.png + .txt (YOLO).
A --train-all run (one model on every drawing, in <run>/all) labels into <out>/all, which
holdout_cv.py --train-all --extra-labels reads.

--reviewed takes contacts whose a/b class a person checked on the image
(JSON list of {"page", "box": [x0, y0, x1, y1] in pixels, "class": "contact_a" | "contact_b"}).
Their class replaces the draft's, and a checked contact the draft missed is added.

Example:
    python analysis/tools/pseudo_label.py --teacher-run runs/cv_baseline --pages data/pages/send100 --out pseudo/v1 --conf 0.6
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from services.detection_rules import detection_kind  # noqa: E402

Row = tuple[int, float, float, float, float]


def _iou(a: Row, b: Row) -> float:
    ax0, ay0, ax1, ay1 = a[1] - a[3] / 2, a[2] - a[4] / 2, a[1] + a[3] / 2, a[2] + a[4] / 2
    bx0, by0, bx1, by1 = b[1] - b[3] / 2, b[2] - b[4] / 2, b[1] + b[3] / 2, b[2] + b[4] / 2
    inter = max(0.0, min(ax1, bx1) - max(ax0, bx0)) * max(0.0, min(ay1, by1) - max(ay0, by0))
    union = a[3] * a[4] + b[3] * b[4] - inter
    return inter / union if union > 0 else 0.0


def apply_reviewed(draft: list[Row], reviewed: list[Row]) -> list[Row]:
    """Give each checked contact its checked class: relabel the draft box it overlaps (IoU >= 0.5) or add it."""
    out = list(draft)
    for r in reviewed:
        best = max(range(len(out)), key=lambda i: _iou(out[i], r), default=None)
        if best is not None and _iou(out[best], r) >= 0.5:
            out[best] = (r[0], *out[best][1:])
        else:
            out.append(r)
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--teacher-run", type=Path, required=True)
    parser.add_argument("--pages", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--conf", type=float, default=0.6)
    parser.add_argument("--imgsz", type=int, default=1280)
    parser.add_argument("--keep-regions", action="store_true")
    parser.add_argument("--reviewed", type=Path, help="image-checked contacts whose class replaces the draft's")
    parser.add_argument("--only", type=Path, help="text file of page names (one per line) to label")
    args = parser.parse_args()

    from ultralytics import YOLO

    pages = sorted(args.pages.glob("*.png"))
    if args.only:
        keep = set(args.only.read_text(encoding="utf-8").split())
        pages = [p for p in pages if p.stem in keep]
    reviewed: dict[str, list[dict]] = {}
    for r in json.loads(args.reviewed.read_text(encoding="utf-8")) if args.reviewed else []:
        reviewed.setdefault(r["page"], []).append(r)
    total = 0
    for weights in sorted(args.teacher_run.glob("*/train/weights/last.pt")):
        fold = weights.parents[2].name
        model = YOLO(str(weights))
        index_of = {name: i for i, name in model.names.items()}
        out = args.out / fold
        out.mkdir(parents=True, exist_ok=True)
        boxes = 0
        for page in pages:
            result = model.predict(str(page), conf=args.conf, imgsz=args.imgsz, max_det=1000, verbose=False)[0]
            rows: list[Row] = []
            for (cx, cy, w, h), cls in zip(result.boxes.xywhn.tolist(), result.boxes.cls.tolist(), strict=True):
                if not args.keep_regions and detection_kind(w, h) == "region":
                    continue
                rows.append((int(cls), cx, cy, w, h))
            if page.stem in reviewed:
                with Image.open(page) as image:
                    width, height = image.size
                checked = [(index_of[r["class"]], (r["box"][0] + r["box"][2]) / 2 / width, (r["box"][1] + r["box"][3]) / 2 / height,
                            (r["box"][2] - r["box"][0]) / width, (r["box"][3] - r["box"][1]) / height) for r in reviewed[page.stem]]
                rows = apply_reviewed(rows, checked)
            lines = [f"{c} {cx:.6f} {cy:.6f} {w:.6f} {h:.6f}" for c, cx, cy, w, h in rows]
            (out / page.name).write_bytes(page.read_bytes())
            (out / page.with_suffix(".txt").name).write_text("\n".join(lines) + "\n", encoding="utf-8")
            boxes += len(lines)
        total += boxes
        print(json.dumps({"fold": fold, "pages": len(pages), "boxes": boxes}))
    print(json.dumps({"errors": 0, "total_boxes": total}))


if __name__ == "__main__":
    main()

