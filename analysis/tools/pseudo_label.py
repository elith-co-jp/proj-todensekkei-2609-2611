"""Pseudo-label unlabeled drawings with each fold's model, for semi-supervised training.

Each fold gets labels only from the model trained on that fold's training drawings, so no
evaluation drawing influences its own fold. Output: <out>/fold<k>/<page>.png + .txt (YOLO).

Example:
    python analysis/tools/pseudo_label.py --teacher-run runs/cv_baseline --pages data/pages/send100 --out pseudo/v1 --conf 0.6
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from services.detection_rules import detection_kind  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--teacher-run", type=Path, required=True)
    parser.add_argument("--pages", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--conf", type=float, default=0.6)
    parser.add_argument("--imgsz", type=int, default=1280)
    parser.add_argument("--keep-regions", action="store_true")
    args = parser.parse_args()

    from ultralytics import YOLO

    pages = sorted(args.pages.glob("*.png"))
    total = 0
    for weights in sorted(args.teacher_run.glob("fold*/train/weights/last.pt")):
        fold = weights.parents[2].name
        model = YOLO(str(weights))
        out = args.out / fold
        out.mkdir(parents=True, exist_ok=True)
        boxes = 0
        for page in pages:
            result = model.predict(str(page), conf=args.conf, imgsz=args.imgsz, max_det=1000, verbose=False)[0]
            lines = []
            for (cx, cy, w, h), cls in zip(result.boxes.xywhn.tolist(), result.boxes.cls.tolist(), strict=True):
                if not args.keep_regions and detection_kind(w, h) == "region":
                    continue
                lines.append(f"{int(cls)} {cx:.6f} {cy:.6f} {w:.6f} {h:.6f}")
            (out / page.name).write_bytes(page.read_bytes())
            (out / page.with_suffix(".txt").name).write_text("\n".join(lines) + "\n", encoding="utf-8")
            boxes += len(lines)
        total += boxes
        print(json.dumps({"fold": fold, "pages": len(pages), "boxes": boxes}))
    print(json.dumps({"errors": 0, "total_boxes": total}))


if __name__ == "__main__":
    main()

