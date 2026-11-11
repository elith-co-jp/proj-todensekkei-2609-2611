"""正解付けする図面の候補に順位を付ける（DD-0026）。

正解の無い図面を、同梱の検出（記号用とカード用、アプリと同じ前処理）で低い確信度まで検出し、
次の 3 つで順位を付ける。

- 迷い: 確信度が下限のすぐ下（--low 以上、下限未満）の検出の数。正解付けで学習の材料になる所。
- 見落としやすさ: 下限以上の検出の数を、交差検証で見落とした率（種類ごと）で重み付けした和。
- 新しさ: 正解の図面とすでに選んだ図面のどれにも似ていない度合い（インクの配置の比較）。
  1 枚選ぶたびに計算し直すので、似た図面が続けて選ばれにくい。

3 つは候補の中の順位（0〜1）にしてから足す。評価用に --reserve 枚を図番の順に等間隔で先に取り分け、
順位付けにも学習にも使わない（取り分けた図面でだけ、足した効果を測る）。

usage:
    python analysis/tools/rank_gold_candidates.py --pages DIR --gold a.zip b.zip \\
        --symbols yolo11s_symbols_best.pt --cards yolo11s_cards_best.pt --cv-summary runs/e27_box_s7/summary.json \\
        --names send100_sheet_ocr.json --out ranking.json [--reserve 20 --batch 20]
"""

from __future__ import annotations

# ruff: noqa: E402

import argparse
import io
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

GRID = (24, 34)  # インクの配置を比べる升目（縦・横）。A3 横の図面で 1 升が約 5 mm


def layout_vector(image: Image.Image) -> np.ndarray:
    """インクの濃さを升目ごとに平均し、長さ 1 にしたもの。図面の書き方（枠・行・カードの配置）の比較に使う。"""
    ink = 1.0 - np.asarray(image.convert("L").resize((GRID[1], GRID[0]), Image.BOX), dtype=np.float32) / 255.0
    v = ink.ravel()
    return v / (np.linalg.norm(v) or 1.0)


def miss_rates(cv_summary: dict[str, Any]) -> dict[str, float]:
    """交差検証の記号の種類ごとの見落とし率（fn / (tp + fn)）。"""
    rates = {}
    for name, c in cv_summary.get("per_class", {}).items():
        total = c.get("tp", 0) + c.get("fn", 0)
        if total:
            rates[name] = c.get("fn", 0) / total
    return rates


def page_counts(detections: list[dict[str, Any]], floors: dict[str, float], default_floor: float, low: float) -> dict[str, Counter]:
    """下限以上（confident）と、--low 以上・下限未満（borderline）の種類ごとの数。"""
    confident, borderline = Counter(), Counter()
    for d in detections:
        floor = floors.get(d["class_key"], default_floor)
        if d["confidence"] >= floor:
            confident[d["class_key"]] += 1
        elif d["confidence"] >= low:
            borderline[d["class_key"]] += 1
    return {"confident": confident, "borderline": borderline}


def reserve_for_evaluation(sheets: list[str], k: int, trained: frozenset[str] | set[str] = frozenset()) -> list[str]:
    """図番の順に等間隔で k 枚。同じ候補なら毎回同じ図面になり、番号の範囲（年代・設備）にも偏らない。

    trained（仮ラベルなどで、すでに学習に使ったページ）は評価用に選ばない。"""
    ordered = sorted(set(sheets) - set(trained))
    if k <= 0:
        return []
    if k >= len(ordered):
        return ordered
    step = len(ordered) / k
    return [ordered[int(i * step + step / 2)] for i in range(k)]


def _percentile(values: dict[str, float]) -> dict[str, float]:
    keys = sorted(values, key=lambda s: (values[s], s))
    n = max(len(keys) - 1, 1)
    out, i = {}, 0
    while i < len(keys):
        j = i
        while j + 1 < len(keys) and values[keys[j + 1]] == values[keys[i]]:
            j += 1
        for s in keys[i:j + 1]:
            out[s] = (i + j) / 2 / n  # 同じ値は同じ順位
        i = j + 1
    return out


def rank_candidates(
    counts: dict[str, dict[str, Counter]],
    layouts: dict[str, np.ndarray],
    gold_layouts: list[np.ndarray],
    rates: dict[str, float],
    batch: int,
) -> list[dict[str, Any]]:
    """迷い・見落としやすさ・新しさの順位の和で 1 枚ずつ選ぶ。選んだ図面は次からの新しさの比較に加える。"""
    uncertain = {s: float(sum(c["borderline"].values())) for s, c in counts.items()}
    hard = {s: sum(rates.get(k, 0.0) * n for k, n in c["confident"].items()) for s, c in counts.items()}
    u, h = _percentile(uncertain), _percentile(hard)
    seen = list(gold_layouts)
    remaining = set(counts)
    picked: list[dict[str, Any]] = []
    while remaining and len(picked) < batch:
        novelty = {s: 1.0 - max((float(layouts[s] @ g) for g in seen), default=0.0) for s in remaining}
        n = _percentile(novelty)
        best = max(remaining, key=lambda s: (u[s] + h[s] + n[s], s))
        picked.append({
            "sheet": best,
            "score": round(u[best] + h[best] + n[best], 3),
            "uncertain": int(uncertain[best]),
            "uncertain_by_class": dict(counts[best]["borderline"].most_common()),
            "hard": round(hard[best], 2),
            "confident_by_class": dict(counts[best]["confident"].most_common()),
            "novelty": round(novelty[best], 4),
        })
        seen.append(layouts[best])
        remaining.discard(best)
    return picked


def _detect_page(path: Path, symbols, cards, low: float) -> list[dict[str, Any]]:
    from analysis.runtime import page_for_detection
    from services.inference_service import CARD_MODEL_CLASSES

    page = page_for_detection(path)
    out = []
    for model, keep in ((symbols, lambda k: k not in CARD_MODEL_CLASSES), (cards, lambda k: k in CARD_MODEL_CLASSES)):
        if model is None:
            continue
        for r in model.predict(page, conf=low, verbose=False, imgsz=1280, max_det=1000):
            for score, cls in zip(r.boxes.conf.tolist(), r.boxes.cls.tolist(), strict=True):
                key = model.names[int(cls)]
                if keep(key):
                    out.append({"class_key": key, "confidence": float(score)})
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--pages", type=Path, required=True, help="正解の無い図面の画像のフォルダ")
    parser.add_argument("--gold", type=Path, nargs="+", required=True, help="今の正解の ZIP（新しさの比較に使う）")
    parser.add_argument("--symbols", type=Path, required=True)
    parser.add_argument("--cards", type=Path)
    parser.add_argument("--cv-summary", type=Path, required=True, help="holdout_cv の summary.json（種類ごとの見落とし率）")
    parser.add_argument("--names", type=Path, help="画像名（拡張子あり・なし）-> 図番の JSON（無ければ画像名のまま）")
    parser.add_argument("--names-key", help="--names の JSON の中で対応表がある鍵（例: unseen）")
    parser.add_argument("--trained", type=Path, nargs="*", default=[],
                        help="すでに学習に使ったページの一覧（1 行に画像名か図番。例: b 接点の仮ラベルの b_train.txt）。評価用に選ばない")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--reserve", type=int, default=20)
    parser.add_argument("--batch", type=int, default=20)
    parser.add_argument("--low", type=float, default=0.2)
    parser.add_argument("--floor", type=float, default=0.5, help="アプリの確信度の下限")
    parser.add_argument("--connector-floor", type=float, default=0.6, help="コネクタの確信度の下限")
    args = parser.parse_args()

    from holdout_cv import load_gold
    from ultralytics import YOLO

    names = json.loads(args.names.read_text(encoding="utf-8")) if args.names else {}
    if args.names_key:
        names = names[args.names_key]
    pages = {names.get(p.name, names.get(p.stem, p.stem)): p for p in sorted(args.pages.glob("*.png"))}
    _, gold = load_gold(args.gold)
    gold_sheets = {d["sheet"] for d in gold}
    pages = {s: p for s, p in pages.items() if s not in gold_sheets}
    trained = {names.get(line.strip(), names.get(line.strip().removesuffix(".png"), line.strip()))
               for path in args.trained for line in path.read_text(encoding="utf-8").splitlines() if line.strip()}
    reserved = reserve_for_evaluation(list(pages), args.reserve, trained)
    candidates = {s: p for s, p in pages.items() if s not in reserved}

    symbols = YOLO(str(args.symbols))
    cards = YOLO(str(args.cards)) if args.cards else None
    floors = {"connector": args.connector_floor}
    counts, layouts = {}, {}
    for sheet, path in candidates.items():
        counts[sheet] = page_counts(_detect_page(path, symbols, cards, args.low), floors, args.floor, args.low)
        layouts[sheet] = layout_vector(Image.open(path))
    gold_layouts = [layout_vector(Image.open(io.BytesIO(d["image"]))) for d in gold]
    rates = miss_rates(json.loads(args.cv_summary.read_text(encoding="utf-8")))
    ranking = rank_candidates(counts, layouts, gold_layouts, rates, args.batch)
    result = {
        "reserved_for_evaluation": reserved,
        "trained_pages": sorted(trained & set(pages)),
        "miss_rates": {k: round(v, 4) for k, v in sorted(rates.items())},
        "batch": ranking,
        "candidates": len(candidates),
        "pages": {s: str(p.name) for s, p in pages.items()},
    }
    args.out.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({"reserved": len(reserved), "candidates": len(candidates), "batch": [r["sheet"] for r in ranking]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
