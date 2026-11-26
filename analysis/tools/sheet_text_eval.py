"""合成の展開接続図（analysis.synthetic_circuits.sheet_text）で、ラベルの結び付けと属性の推定の当たり率を測る（DD-0050）。

    python analysis/tools/sheet_text_eval.py --sheets 200 --noise 0.5 --out runs/sheet_text/summary.json

1 枚ごとに、解析（extract_text_structure）と同じ順でラベルを決める。枠の中に器具番号が読めればそれ（コイルの丸の中）、
読めなければ近くの文字のまとまり（label_link）。その後で属性を推定し（attribute_infer）、正解と比べる。

- label: 器具番号（先頭 2 語の照合キー）が正解と同じ記号の割合。box は結んだ文字のまとまりが正解の位置と IoU 0.3 以上の割合。
- 属性: 種類ごとの適合率（入れたもののうち正しい）と再現率（正解のうち入れた）。タイマーは種類と秒の両方、信号名は
  正しい記号に付けたか（文字の読み違いは数えない）で比べる。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from analysis import attribute_infer  # noqa: E402
from analysis import label_link as ll  # noqa: E402
from analysis import text_extraction as tx  # noqa: E402
from analysis.synthetic_circuits import sheet_text  # noqa: E402

ATTRIBUTES = ("timer", "latch_coil", "signal_name", "external_io")


def read_sheet(sheet: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """合成の 1 枚を解析と同じ順で読む。返り値は (記号（label・label_box・attributes つき）, 属性の推定の結果)。"""
    items = sheet["items"]
    symbols = sheet_text.detected(sheet)
    lexicon = tx.page_lexicon(items)
    inside = {id(s): tx.symbol_label(items, s["bbox"], s["class_name"], lexicon) for s in symbols}
    unnamed = ll.link_targets(symbols, inside)
    targets = {id(s) for s in unnamed}
    links = ll.link_labels(items, symbols, lexicon=lexicon, targets=unnamed)
    for s in symbols:
        link = links.get(str(s["id"]))
        if id(s) in targets and link and link["label"] and link["status"] != "未検出":
            s["label"], s["label_box"], s["label_status"] = link["label"], link["label_box"], link["status"]
        else:
            s["label"], s["label_box"], s["label_status"] = inside[id(s)], None, "inside"
    result = attribute_infer.infer_attributes(items, symbols, crop_items=sheet.get("crop_items"), page_height=sheet["height"])
    for sid, fix in result["labels"].items():
        s = next(x for x in symbols if str(x["id"]) == sid)
        s["label"], s["label_box"] = fix["label"], fix.get("label_box")
    for s in symbols:
        s["attributes"] = dict((result["symbols"].get(str(s["id"])) or {}).get("attributes") or {})
    return symbols, result


def _key(label: str | None) -> tuple[str, ...]:
    """器具番号と型式（先頭 2 語、参照先は除く）の照合キー。数字だけの器具番号（52、80）も残す。"""
    words = [w for w in tx.normalize(re.sub(rf"\(?{tx.SHEET_REF}\)?", " ", tx.normalize(label or ""))).split()]
    return tuple(tx.match_key(w) for w in words[:2])


def same_device(got: str | None, gold: str | None, class_name: str = "") -> bool:
    """器具番号が同じで、型式は読めたときだけ比べる（行が 1 つ抜けたラベルも、回路では器具番号で対になる）。

    コネクタは名前の上の電線番号（387 CN7 の 387）を飛ばして名前（CN7）で比べる。
    """
    a, b = _key(got), _key(gold)
    if class_name == "connector":
        a, b = tuple(w for w in a if not w.isdigit()) or a, tuple(w for w in b if not w.isdigit()) or b
    return bool(a) and a[0] == b[0] and (len(a) < 2 or len(b) < 2 or a[1] == b[1])


def _iou(a: Any, b: Any) -> float:
    if not a or not b:
        return 0.0
    a, b = tx._as_box(a), tx._as_box(b)
    ix = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = ix * iy
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / union if union > 0 else 0.0


def _value(attributes: dict[str, Any], name: str) -> Any:
    if name == "timer":
        if "timer_mode" not in attributes:
            return None
        return (attributes.get("timer_mode"), attributes.get("delay_s"))
    value = attributes.get(name)
    return "yes" if name == "signal_name" and value else value


def score_sheet(sheet: dict[str, Any], symbols: list[dict[str, Any]]) -> dict[str, Counter]:
    gold = {s["id"]: s for s in sheet["symbols"]}
    c: dict[str, Counter] = {"label": Counter(), **{a: Counter() for a in ATTRIBUTES}}
    for s in symbols:
        g = gold[s["id"]]
        if s["class_name"] in ll.LINK_CLASSES and g["gold_label"]:
            ok = same_device(s["label"], g["gold_label"], s["class_name"])
            c["label"]["total"] += 1
            c["label"]["correct"] += ok
            c["label"][f"status:{s['label_status']}"] += 1
            if g["gold_label_box"] is not None and s["label_status"] != "inside":
                c["label"]["box_total"] += 1
                c["label"]["box_correct"] += _iou(s["label_box"], g["gold_label_box"]) >= 0.3
            if not ok:
                c["label"][f"wrong:{s['class_name']}"] += 1
        for name in ATTRIBUTES:
            want, got = _value(g["gold_attributes"], name), _value(s.get("attributes") or {}, name)
            if want is not None:
                c[name]["gold"] += 1
            if got is not None:
                c[name]["predicted"] += 1
                c[name]["correct"] += got == want
    return c


def summarize(counts: dict[str, Counter]) -> dict[str, Any]:
    lab = counts["label"]
    out: dict[str, Any] = {
        "label": {
            "symbols": lab["total"],
            "accuracy": round(lab["correct"] / lab["total"], 4) if lab["total"] else None,
            "box_accuracy": round(lab["box_correct"] / lab["box_total"], 4) if lab["box_total"] else None,
            "status": {k.split(":", 1)[1]: v for k, v in sorted(lab.items()) if k.startswith("status:")},
            "wrong": {k.split(":", 1)[1]: v for k, v in sorted(lab.items()) if k.startswith("wrong:")},
        },
    }
    for name in ATTRIBUTES:
        a = counts[name]
        out[name] = {
            "gold": a["gold"], "predicted": a["predicted"],
            "precision": round(a["correct"] / a["predicted"], 4) if a["predicted"] else None,
            "recall": round(a["correct"] / a["gold"], 4) if a["gold"] else None,
        }
    return out


def evaluate(seeds: range | list[int], config: sheet_text.SheetConfig) -> dict[str, Any]:
    total: dict[str, Counter] = {"label": Counter(), **{a: Counter() for a in ATTRIBUTES}}
    for seed in seeds:
        sheet = sheet_text.make_sheet(seed, config)
        symbols, _ = read_sheet(sheet)
        for k, v in score_sheet(sheet, symbols).items():
            total[k].update(v)
    return {"sheets": len(seeds), "noise": config.noise, "label_side": config.label_side, **summarize(total)}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--sheets", type=int, default=200)
    parser.add_argument("--seed", type=int, default=50000, help="最初の seed（調整に使った seed 1〜999 と分ける）")
    parser.add_argument("--noise", type=float, nargs="+", default=[0.0, 0.5, 1.0])
    parser.add_argument("--side", default="mixed", choices=["mixed", "right", "above", "below"])
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    seeds = range(args.seed, args.seed + args.sheets)
    results = [evaluate(seeds, sheet_text.SheetConfig(noise=n, label_side=args.side)) for n in args.noise]
    text = json.dumps(results, ensure_ascii=False, indent=2)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
