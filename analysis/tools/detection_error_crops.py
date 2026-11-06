"""Cut out every detection miss and false detection of a cross-validation run for visual review.

Each crop shows the gold boxes in green and the predicted boxes in red around one error, so a
reviewer can tell a model error from a gold-label error. index.json lists the crops.

Example:
    python analysis/tools/detection_error_crops.py --gold a.zip b.zip --work runs/cv --out runs/cv_crops
"""

from __future__ import annotations

import argparse
import io
import json
import sys
from pathlib import Path

from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).resolve().parent))

from holdout_cv import gold_items, load_gold, match, score_symbols, to_box  # noqa: E402

MARGIN_PX = 140
MIN_SIDE_PX = 480


def crop_around(image: Image.Image, box: dict[str, float], gold: list[dict], pred: list[dict]) -> Image.Image:
    x0, y0 = max(0, int(box["x0"]) - MARGIN_PX), max(0, int(box["y0"]) - MARGIN_PX)
    x1, y1 = min(image.width, int(box["x1"]) + MARGIN_PX), min(image.height, int(box["y1"]) + MARGIN_PX)
    view = image.crop((x0, y0, x1, y1)).convert("RGB")
    draw = ImageDraw.Draw(view)
    for items, colour in ((gold, (0, 160, 0)), (pred, (220, 0, 0))):
        for item in items:
            b = item["bbox"]
            if b["x1"] < x0 or b["x0"] > x1 or b["y1"] < y0 or b["y0"] > y1:
                continue
            draw.rectangle((b["x0"] - x0, b["y0"] - y0, b["x1"] - x0, b["y1"] - y0), outline=colour, width=2)
            draw.text((b["x0"] - x0 + 2, b["y1"] - y0 + 1), item["class_name"][:10], fill=colour)
    scale = max(1.0, MIN_SIDE_PX / max(1, min(view.width, view.height)))
    return view.resize((round(view.width * scale), round(view.height * scale))) if scale > 1 else view


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--gold", type=Path, nargs="+", required=True)
    parser.add_argument("--work", type=Path, required=True, help="holdout_cv.py --work with fold*/pred/<SHEET>.json")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()

    _keys, drawings = load_gold(args.gold)
    by_sheet = {d["sheet"]: d for d in drawings}
    args.out.mkdir(parents=True, exist_ok=True)
    index = []
    for pred_file in sorted(args.work.glob("fold*/pred/*.json")):
        drawing = by_sheet[pred_file.stem]
        width, height = int(drawing["project"]["image_width"]), int(drawing["project"]["image_height"])
        pred = json.loads(pred_file.read_text(encoding="utf-8"))
        for p in pred:
            p["bbox"] = to_box(p["cx"], p["cy"], p["w"], p["h"], width, height)
        scored = score_symbols(gold_items(drawing), pred)
        g_sym, p_sym = scored["g_sym"], scored["p_sym"]
        matched_g = {gi for gi, _, _ in scored["matches"]}
        matched_p = {pi for _, pi, _ in scored["matches"]}
        same_place = {gi: pi for gi, pi, _ in match(g_sym, p_sym, class_aware=False)}
        image = Image.open(io.BytesIO(drawing["image"]))
        errors = [("fn", g_sym[gi], gi) for gi in range(len(g_sym)) if gi not in matched_g]
        errors += [("fp", p_sym[pi], pi) for pi in range(len(p_sym)) if pi not in matched_p]
        for kind, item, i in errors:
            name = f"{drawing['sheet']}_{kind}_{item['class_name']}_{i}.png"
            crop_around(image, item["bbox"], g_sym, p_sym).save(args.out / name)
            row = {"crop": name, "sheet": drawing["sheet"], "error": kind, "class": item["class_name"],
                   "bbox": {k: round(v) for k, v in item["bbox"].items()}}
            if kind == "fn":
                row["gold_ref"], row["gold_label"] = item["ref"], item["label"]
                if i in same_place:
                    row["predicted_as"] = p_sym[same_place[i]]["class_name"]
            else:
                row["confidence"] = round(item["confidence"], 3)
                gold_here = next((gi for gi, pi in same_place.items() if pi == i), None)
                if gold_here is not None:
                    row["gold_class_here"] = g_sym[gold_here]["class_name"]
            index.append(row)
    (args.out / "index.json").write_text(json.dumps(index, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({"errors": len(index), "fn": sum(r["error"] == "fn" for r in index),
                      "fp": sum(r["error"] == "fp" for r in index)}))


if __name__ == "__main__":
    main()
