"""印だけの枠（DD-0049）の学習データを作り、近くの文字からのラベルの当たり率を測る。

2 つの使い方がある。

split: 今の正解データ（記号の枠が印と名前を囲む。DD-0017）を、印だけの枠とラベルの文字の位置（label_box）に分ける。
    枠の中の文字を OCR で読み、ラベルの語の位置を label_box に、残ったインク（白丸・棒・線）を印の枠にする。
    元の ZIP は変えず、アプリで取り込める ZIP を新しく作る（YOLO のラベルも印の枠で作り直す）。図面ごとに、今の枠（灰）・
    印の枠（緑）・ラベルの位置（青）を描いた確認用の画像を出す。

    python analysis/tools/label_link_data.py split --zip gold.zip --out gold_mark.zip --review-dir review/

eval: 印だけの枠の正解データで、近くの文字から作ったラベルが正解のラベルと合うかを測る。
    --split を付けると、今の正解データをその場で分けてから測る（付け直す前に当たり率を見るため）。そのときは、今の枠の中の
    文字から作ったラベル（今のやり方）の当たり率も並べて出す。

    python analysis/tools/label_link_data.py eval --zip gold.zip --split --out eval_label_link.json

どちらも図面全体の OCR（RapidOCR）を使う。--ocr-cache を付けると図面ごとの OCR 結果を保存して使い回す。
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import sys
import zipfile
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from analysis import label_link as ll  # noqa: E402
from analysis import text_extraction as tx  # noqa: E402

# 正解の文字の位置と、結んだ文字のまとまりがこれ以上重なれば「正しい文字を結んだ」とみなす。
LABEL_BOX_IOU = 0.3


def _iou(a: tx.Box, b: tx.Box) -> float:
    x0, y0, x1, y1 = max(a[0], b[0]), max(a[1], b[1]), min(a[2], b[2]), min(a[3], b[3])
    inter = max(0.0, x1 - x0) * max(0.0, y1 - y0)
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / union if union > 0 else 0.0


def px_box(box: dict[str, float], width: float, height: float) -> tx.Box:
    return ((box["cx"] - box["w"] / 2) * width, (box["cy"] - box["h"] / 2) * height,
            (box["cx"] + box["w"] / 2) * width, (box["cy"] + box["h"] / 2) * height)


def norm_box(box: tx.Box, width: float, height: float) -> dict[str, float]:
    x0, y0, x1, y1 = box
    return {"cx": (x0 + x1) / 2 / width, "cy": (y0 + y1) / 2 / height, "w": (x1 - x0) / width, "h": (y1 - y0) / height}


def device_key(label: str | None) -> str:
    """器具番号の照合キー（最初の英字を含む語。O・Q と 0、I と 1 は同じに寄せる）。"""
    words = [w for w in tx.normalize(label or "").split() if any(c.isalpha() for c in w)]
    return tx.match_key(words[0]) if words else ""


# ---------------------------------------------------------------------------------------------- split


def split_project(project: dict[str, Any], gray: Any, items: list[tx.TextItem]) -> dict[str, int]:
    """図面 1 枚の記号の枠を印だけにし、label_box を付ける（project を書き換える）。件数を返す。"""
    width, height = float(project["image_width"]), float(project["image_height"])
    counts: Counter = Counter()
    for symbol in project["symbols"]:
        cls = symbol.get("class_key") or ""
        if cls not in ll.LINK_CLASSES:
            continue
        if symbol.get("label_box"):
            counts["already"] += 1
            continue
        old = px_box(symbol, width, height)
        terminals = [(t["tx"] * width, t["ty"] * height) for t in symbol.get("terminals") or [] if t.get("tx") is not None]
        mark, label_box = ll.split_mark_box(gray, old, items, cls, terminals)
        if label_box is None:
            counts["no_name_inside"] += 1
            continue
        symbol["_old_px"] = old
        symbol.update(norm_box(mark, width, height))
        symbol["label_box"] = norm_box(label_box, width, height)
        counts["split"] += 1
    return dict(counts)


def review_image(image_bytes: bytes, project: dict[str, Any]) -> bytes:
    """今の枠（灰）・印の枠（緑）・ラベルの位置（青）を描いた PNG。"""
    from PIL import Image, ImageDraw

    width, height = float(project["image_width"]), float(project["image_height"])
    image = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    draw = ImageDraw.Draw(image)
    for s in project["symbols"]:
        if s.get("_old_px"):
            draw.rectangle(s["_old_px"], outline=(170, 170, 170))
        if s.get("label_box"):
            draw.rectangle(px_box(s, width, height), outline=(0, 150, 0), width=2)
            draw.rectangle(px_box(s["label_box"], width, height), outline=(0, 70, 255))
    out = io.BytesIO()
    image.save(out, format="PNG")
    return out.getvalue()


# ----------------------------------------------------------------------------------------------- eval


def evaluate_project(
    project: dict[str, Any], items: list[tx.TextItem], baseline: dict[str, str] | None = None,
    image: Any = None, crop_reader: Any = None,
) -> list[dict[str, Any]]:
    """印だけの枠と正解のラベルの図面 1 枚で、近くの文字から作ったラベルを正解と比べる（記号ごとの行）。

    baseline（ref → 今の枠の中から作ったラベル）があれば並べて比べる。image（PIL）と crop_reader があれば、アプリと同じく
    結んだ文字を拡大して読み直す。
    """
    width, height = float(project["image_width"]), float(project["image_height"])
    symbols = [{"id": s["ref"], "class_name": s.get("class_key") or "", "bbox": px_box(s, width, height)}
               for s in project["symbols"]]
    lexicon = tx.page_lexicon(items)
    zones = tx.text_zones(items, int(width), int(height))
    links = ll.link_labels(items, symbols, lexicon=lexicon, zones=zones)
    if image is not None and crop_reader is not None:
        crops = tx._reread_link_crops(image, symbols, ll.owner_boxes(symbols), links, crop_reader, lexicon)
        for symbol in symbols:
            link = links.get(str(symbol["id"]))
            if link and link["label"]:
                link["label"] = tx.linked_label(link["label"], crops.get(id(symbol)), lexicon)
    rows = []
    for s in project["symbols"]:
        if (s.get("class_key") or "") not in ll.LINK_CLASSES or not device_key(s.get("label")):
            continue
        link = links[s["ref"]]
        gold_box = px_box(s["label_box"], width, height) if s.get("label_box") else None
        row = {
            "ref": s["ref"], "class_key": s["class_key"], "gold": s.get("label"), "pred": link["label"],
            "status": link["status"], "direction": link["direction"],
            "label_ok": bool(link["label"]) and device_key(link["label"]) == device_key(s.get("label")),
            "box_ok": (gold_box is not None and link["label_box"] is not None
                       and _iou(tuple(link["label_box"]), gold_box) >= LABEL_BOX_IOU) if gold_box else None,
        }
        if baseline is not None:
            row["baseline"] = baseline.get(s["ref"], "")
            row["baseline_ok"] = device_key(row["baseline"]) == device_key(s.get("label"))
        rows.append(row)
    return rows


def inside_labels(project: dict[str, Any], items: list[tx.TextItem], image: Any = None, crop_reader: Any = None) -> dict[str, str]:
    """今のやり方（印と名前を囲んだ枠の中の文字）で作るラベル。image と crop_reader があればアプリと同じく枠を拡大して読む。"""
    width, height = float(project["image_width"]), float(project["image_height"])
    lexicon = tx.page_lexicon(items)
    out = {}
    for s in project["symbols"]:
        cls = s.get("class_key") or ""
        box = px_box(s, width, height)
        label = tx.symbol_label(items, box, cls, lexicon)
        if image is not None and crop_reader is not None and cls in tx.CROP_OCR_CLASSES:
            crop = tx.symbol_label(tx.read_crop_text(image, box, crop_reader), box, cls, lexicon)
            label = tx.best_label(crop, label, lexicon)
        out[s["ref"]] = label
    return out


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    def rate(keys: list[bool]) -> float | None:
        return round(sum(keys) / len(keys), 4) if keys else None

    out: dict[str, Any] = {
        "symbols": len(rows),
        "status": dict(Counter(r["status"] for r in rows)),
        "label_accuracy": rate([r["label_ok"] for r in rows]),
        "linked_precision": rate([r["label_ok"] for r in rows if r["status"] == "linked"]),
        "box_accuracy": rate([r["box_ok"] for r in rows if r["box_ok"] is not None]),
    }
    if rows and "baseline_ok" in rows[0]:
        out["baseline_label_accuracy"] = rate([r["baseline_ok"] for r in rows])
    out["by_class"] = {
        cls: {"symbols": len(group), "label_accuracy": rate([r["label_ok"] for r in group]),
              "box_accuracy": rate([r["box_ok"] for r in group if r["box_ok"] is not None])}
        for cls in sorted({r["class_key"] for r in rows})
        if (group := [r for r in rows if r["class_key"] == cls])
    }
    return out


# ------------------------------------------------------------------------------------------------ OCR


class PageReader:
    """図面全体の OCR。--ocr-cache があれば画像のハッシュごとに保存して使い回す。"""

    def __init__(self, cache: Path | None) -> None:
        self.cache = cache
        self._reader: Any = None

    def reader(self) -> Any:
        if self._reader is None:
            from rapidocr_onnxruntime import RapidOCR

            self._reader = RapidOCR()
        return self._reader

    def __call__(self, image_bytes: bytes) -> list[tx.TextItem]:
        key = hashlib.sha256(image_bytes).hexdigest()
        path = self.cache / f"{key}.json" if self.cache else None
        if path is not None and path.exists():
            return [{**i, "box": tuple(i["box"])} for i in json.loads(path.read_text(encoding="utf-8"))]
        from PIL import Image

        items = tx.read_page_text(Image.open(io.BytesIO(image_bytes)).convert("RGB"), self.reader())
        if path is not None:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(items, ensure_ascii=False), encoding="utf-8")
        return items


def _drawings(zip_path: Path) -> tuple[bytes, dict[str, Any], list[tuple[dict[str, Any], bytes]]]:
    original = zip_path.read_bytes()
    with zipfile.ZipFile(io.BytesIO(original)) as zf:
        bundle = json.loads(zf.read(next(n for n in zf.namelist() if n.endswith("bundle.json"))))
        images = {n.rsplit("/", 1)[-1]: n for n in zf.namelist() if n.startswith("dataset/images/")}
        pairs = []
        for project in bundle["projects"]:
            name = next((n for n in images if n.startswith(f"p{project['id']}_")), None)
            if name is None:
                raise SystemExit(f"画像がありません: {project.get('name')}")
            pairs.append((project, zf.read(images[name])))
    return original, bundle, pairs


def _gray(image_bytes: bytes) -> Any:
    """濃さの画像。チャンネルの最大値なので、図面に書き込んだ赤や青の印（確認の枠など）はインクにならない。"""
    import numpy as np
    from PIL import Image

    return np.asarray(Image.open(io.BytesIO(image_bytes)).convert("RGB")).max(axis=2)


def run_split(args: argparse.Namespace) -> None:
    from apply_gold_corrections import rebuild_zip

    if args.out.resolve() == args.zip.resolve():
        raise SystemExit("--out は --zip と別のファイルにしてください（元の ZIP は変えません）")
    original, bundle, pairs = _drawings(args.zip)
    read = PageReader(args.ocr_cache)
    totals: Counter = Counter()
    per_sheet = {}
    for project, image in pairs:
        counts = split_project(project, _gray(image), read(image))
        totals.update(counts)
        per_sheet[project.get("name")] = counts
        if args.review_dir:
            args.review_dir.mkdir(parents=True, exist_ok=True)
            (args.review_dir / f"p{project['id']}.png").write_bytes(review_image(image, project))
        for s in project["symbols"]:
            s.pop("_old_px", None)
    args.out.write_bytes(rebuild_zip(original, bundle))
    print(json.dumps({"zip": args.zip.name, "out": str(args.out), "counts": dict(totals), "per_sheet": per_sheet},
                     ensure_ascii=False, indent=2))


def run_eval(args: argparse.Namespace) -> None:
    _original, _bundle, pairs = _drawings(args.zip)
    read = PageReader(args.ocr_cache)
    all_rows = []
    per_sheet = {}
    for project, image in pairs:
        items = read(image)
        baseline = None
        pil = None
        if not args.no_crop:
            from PIL import Image

            pil = Image.open(io.BytesIO(image)).convert("RGB")
        if args.split:
            baseline = inside_labels(project, items, pil, read.reader() if pil is not None else None)
            split_project(project, _gray(image), items)
        rows = evaluate_project(project, items, baseline, pil, read.reader() if pil is not None else None)
        per_sheet[project.get("name")] = summarize(rows)
        all_rows.extend({"sheet": project.get("name"), **r} for r in rows)
    result = {"zip": args.zip.name, "split": bool(args.split), "summary": summarize(all_rows), "per_sheet": per_sheet,
              "errors": [r for r in all_rows if not r["label_ok"]][: args.max_errors]}
    text = json.dumps(result, ensure_ascii=False, indent=2)
    if args.out:
        args.out.write_text(text, encoding="utf-8")
    print(json.dumps(result["summary"], ensure_ascii=False, indent=2))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    split = sub.add_parser("split", help="今の枠を印だけの枠とラベルの文字の位置に分けた ZIP を作る")
    split.add_argument("--zip", type=Path, required=True)
    split.add_argument("--out", type=Path, required=True)
    split.add_argument("--review-dir", type=Path)
    split.add_argument("--ocr-cache", type=Path)
    evaluate = sub.add_parser("eval", help="近くの文字から作ったラベルの当たり率を測る")
    evaluate.add_argument("--zip", type=Path, required=True)
    evaluate.add_argument("--split", action="store_true", help="今の枠の正解データをその場で分けてから測る")
    evaluate.add_argument("--out", type=Path)
    evaluate.add_argument("--ocr-cache", type=Path)
    evaluate.add_argument("--max-errors", type=int, default=200)
    evaluate.add_argument("--no-crop", action="store_true", help="結んだ文字の拡大読み直しをしない（速い）")
    args = parser.parse_args()
    (run_split if args.command == "split" else run_eval)(args)


if __name__ == "__main__":
    main()
