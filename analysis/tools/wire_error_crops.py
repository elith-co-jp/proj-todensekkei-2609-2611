"""Cut out every missed gold wire and wrong imported wire scored by eval_editor_import.py for visual review.

A missed wire is drawn in green between its two gold terminals; a wrong imported wire is drawn in red
between its two imported terminals. Gold symbols near the wire are boxed in grey with their labels.

Example:
    python analysis/tools/wire_error_crops.py --gold a.zip b.zip --editor-json runs/editor.json --out runs/wire_crops
"""

from __future__ import annotations

import argparse
import io
import json
import sys
from pathlib import Path

from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).resolve().parent))

from holdout_cv import gold_items, load_gold  # noqa: E402

MARGIN_PX = 90
MIN_SIDE_PX = 520


def render(image: Image.Image, points: list, colour: tuple[int, int, int], gold: list[dict]) -> Image.Image:
    xs, ys = [p[0] for p in points], [p[1] for p in points]
    x0, y0 = max(0, int(min(xs)) - MARGIN_PX), max(0, int(min(ys)) - MARGIN_PX)
    x1, y1 = min(image.width, int(max(xs)) + MARGIN_PX), min(image.height, int(max(ys)) + MARGIN_PX)
    view = image.crop((x0, y0, x1, y1)).convert("RGB")
    draw = ImageDraw.Draw(view)
    for g in gold:
        b = g["bbox"]
        if b["x1"] < x0 or b["x0"] > x1 or b["y1"] < y0 or b["y0"] > y1:
            continue
        draw.rectangle((b["x0"] - x0, b["y0"] - y0, b["x1"] - x0, b["y1"] - y0), outline=(150, 150, 150), width=1)
    (ax, ay), (bx, by) = points
    draw.line((ax - x0, ay - y0, bx - x0, by - y0), fill=colour, width=3)
    for px, py in points:
        draw.ellipse((px - x0 - 6, py - y0 - 6, px - x0 + 6, py - y0 + 6), outline=colour, width=3)
    scale = max(1.0, MIN_SIDE_PX / max(1, min(view.width, view.height)))
    return view.resize((round(view.width * scale), round(view.height * scale))) if scale > 1 else view


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--gold", type=Path, nargs="+", required=True)
    parser.add_argument("--editor-json", type=Path, required=True, help="eval_editor_import.py --out")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()

    _keys, drawings = load_gold(args.gold)
    by_sheet = {d["sheet"]: d for d in drawings}
    args.out.mkdir(parents=True, exist_ok=True)
    index = []
    for row in json.loads(args.editor_json.read_text(encoding="utf-8"))["rows"]:
        drawing = by_sheet[row["sheet"]]
        labels = {s["ref"]: (s["class_key"], s.get("label") or "") for s in drawing["project"]["symbols"]}
        image = Image.open(io.BytesIO(drawing["image"]))
        gold = gold_items(drawing)
        for kind, colour in (("missed", (0, 170, 0)), ("wrong", (220, 0, 0))):
            for i, item in enumerate(row["wire_errors"][kind]):
                if None in item["points"]:
                    continue
                name = f"{row['sheet']}_{kind}_{i}.png"
                render(image, item["points"], colour, gold).save(args.out / name)
                ends = [labels.get(str(k).split(":")[0].split("#")[0], ("?", "")) if k else ("?", "") for k in item["gold"]]
                index.append({"crop": name, "sheet": row["sheet"], "error": kind, "gold_keys": item["gold"],
                              "ends": [f"{c} {lab}".strip() for c, lab in ends],
                              "points": [[round(v) for v in p] for p in item["points"]]})
    (args.out / "index.json").write_text(json.dumps(index, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({"crops": len(index), "missed": sum(r["error"] == "missed" for r in index),
                      "wrong": sum(r["error"] == "wrong" for r in index)}))


if __name__ == "__main__":
    main()
