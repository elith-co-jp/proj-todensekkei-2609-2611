"""正解データが記号の枠と端子の取り決めに合っているかを確かめる（DD-0026）。

新しく正解付けした図面を学習と評価に入れる前に流し、取り決めに合わない記号を一覧にする。
取り決めは SPECIFICATION.md の「記号の枠の取り決め」（DD-0017・DD-0025）と「コイルの配線の取り決め」（DD-0009）。

- two_terminals: 接点・リレーコイル・電磁弁の端子がちょうど 2 個。
- oversized: 枠の高さか幅が、同じ種類の記号の中央値の --max-ratio 倍を超える（名前の下の空白まで囲んだ枠など）。
  中央値は読み込んだ正解すべてから取るので、新しい図面は今の正解と一緒に渡す。背の高いコネクタ（横棒と
  上下の目盛り線を囲む）も拾うので、拾った記号は画像で確かめる。
  （枠の縁からインクまでの空白では測れない。コネクタの枠の中を配線が縦に通り、上下の縁にいつもインクがあるため）
- tick_outside: コネクタの黒丸の上下 8〜31 px にある 25〜90 px の横線（目盛り線）が枠の外にある。

usage:
    python analysis/tools/check_gold_conventions.py --gold gold_a_coil.zip gold_b_coil.zip [--out findings.json]
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

TWO_TERMINAL_CLASSES = frozenset({"contact_a", "contact_b", "relay_coil", "solenoid"})
SIZE_CLASSES = frozenset({"contact_a", "contact_b", "relay_coil", "solenoid", "connector"})
TICK_GAP = (8, 31)  # 黒丸の中心から目盛り線までの縦の距離（px）
TICK_LENGTH = (25, 90)  # 目盛り線の長さ（px）。これより長い線は母線や配線


Box = tuple[float, float, float, float]


def size_medians(drawings: list[dict[str, Any]]) -> dict[str, tuple[float, float]]:
    """種類ごとの枠の高さと幅（px）の中央値。"""
    sizes: dict[str, list[tuple[float, float]]] = {}
    for d in drawings:
        width, height = d["project"]["image_width"], d["project"]["image_height"]
        for s in d["project"]["symbols"]:
            sizes.setdefault(s["class_key"], []).append((s["h"] * height, s["w"] * width))
    return {k: (float(np.median([h for h, _ in v])), float(np.median([w for _, w in v]))) for k, v in sizes.items()}


def tick_rows(ink: np.ndarray, x: float, y: float) -> dict[str, int]:
    """黒丸 (x, y) の上と下にある目盛り線の行。長すぎる線（母線・配線）に当たったらその側は無し。"""
    found = {}
    for side, sign in (("above", -1), ("below", 1)):
        for dy in range(TICK_GAP[0], TICK_GAP[1] + 1):
            row_y = round(y + sign * dy)
            if not 0 <= row_y < ink.shape[0] or not ink[row_y, round(x)]:
                continue
            row = ink[row_y]
            left = right = round(x)
            while left > 0 and row[left - 1]:
                left -= 1
            while right < len(row) - 1 and row[right + 1]:
                right += 1
            length = right - left
            if TICK_LENGTH[0] <= length <= TICK_LENGTH[1]:
                found[side] = row_y
            if length >= TICK_LENGTH[0]:
                break
    return found


def check_symbol(symbol: dict[str, Any], box: Box, ink: np.ndarray, dots: list[tuple[float, float, float]],
                 medians: dict[str, tuple[float, float]], max_ratio: float) -> list[dict[str, Any]]:
    """1 つの記号の取り決めに合わない点。dots は (x, y, 大きさ) の黒丸。"""
    found = []
    cls = symbol["class_key"]
    if cls in TWO_TERMINAL_CLASSES and len(symbol.get("terminals") or []) != 2:
        found.append({"rule": "two_terminals", "detail": f"端子 {len(symbol.get('terminals') or [])} 個"})
    if cls in SIZE_CLASSES and cls in medians:
        h, w = box[3] - box[1], box[2] - box[0]
        mh, mw = medians[cls]
        if h > max_ratio * mh or w > max_ratio * mw:
            found.append({"rule": "oversized", "detail": {"height_ratio": round(h / mh, 2), "width_ratio": round(w / mw, 2)}})
    if cls == "connector":
        inside = [d for d in dots if box[0] - 3 <= d[0] <= box[2] + 3 and box[1] - 3 <= d[1] <= box[3] + 3]
        if inside:
            x, y, _ = max(inside, key=lambda d: d[2])
            for side, row_y in tick_rows(ink, x, y).items():
                if (side == "below" and box[3] < row_y - 1) or (side == "above" and box[1] > row_y + 1):
                    found.append({"rule": "tick_outside", "detail": {"side": side, "tick_y": row_y}})
    return found


def check_drawing(drawing: dict[str, Any], medians: dict[str, tuple[float, float]], max_ratio: float) -> list[dict[str, Any]]:
    from analysis.wiring.detect import make_binary_mask
    from analysis.wiring.dot_roles import DotRules, find_dots

    image = Image.open(io.BytesIO(drawing["image"])).convert("RGB")
    ink = make_binary_mask(image) > 0
    dots = [(d.x, d.y, d.size) for d in find_dots((ink * 255).astype(np.uint8), DotRules())]
    width, height = image.size
    out = []
    for s in drawing["project"]["symbols"]:
        box = ((s["cx"] - s["w"] / 2) * width, (s["cy"] - s["h"] / 2) * height,
               (s["cx"] + s["w"] / 2) * width, (s["cy"] + s["h"] / 2) * height)
        for f in check_symbol(s, box, ink, dots, medians, max_ratio):
            out.append({"sheet": drawing["sheet"], "ref": s["ref"], "class_key": s["class_key"], "label": s.get("label"),
                        "bbox_px": [round(v, 1) for v in box], **f})
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--gold", type=Path, nargs="+", required=True)
    parser.add_argument("--max-ratio", type=float, default=1.4,
                        help="枠の高さ・幅の上限（同じ種類の中央値の倍数）。DD-0025 の前の ER21237 のゆるい枠は 1.45〜1.64 倍")
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()

    from holdout_cv import load_gold

    _, drawings = load_gold(args.gold)
    medians = size_medians(drawings)
    findings = [f for d in drawings for f in check_drawing(d, medians, args.max_ratio)]
    if args.out:
        args.out.write_text(json.dumps(findings, ensure_ascii=False, indent=1), encoding="utf-8")
    by_rule = Counter(f["rule"] for f in findings)
    by_sheet = Counter(f["sheet"] for f in findings)
    print(json.dumps({"drawings": len(drawings), "findings": len(findings), "by_rule": by_rule,
                      "by_sheet": dict(by_sheet.most_common())}, ensure_ascii=False))


if __name__ == "__main__":
    main()
