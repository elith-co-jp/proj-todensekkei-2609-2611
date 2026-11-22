"""リレーコイルと表示灯の判別（#39）を測る合成図面と評価（DD-0043）。

受領図面には表示灯の正解が無い（固定 30 枚・未学習 100 ページとも表示灯 0 件）。正解の種別がわかっている
スキャン風の合成図面で、4 つの手がかり（円の中の×印・表示灯の文字・リレー表・同じ器具番号の接点）が
本番の解析経路（analysis.runtime.analyze_detections）でどれだけ取り違えを直し、どれだけ「要確認」に回すかを数える。

図面の書き方は実図面に合わせる:
  - リレーコイルは円の中に器具番号を書く（DD-0023）。接点の名前は棒の右、枠の中に書く。
  - 表示灯は円の中に×印、近くに色記号（RL・GL など）。×印の無い書き方（円の中に色記号だけ）も混ぜる。
  - 図面の下にリレー表（器具番号・端子・接点・シート・行列）。他の図面にコイルがある器具の行も混ぜる。
  - 解析の前にスキャン風の劣化（analysis.synthetic_circuits.scan）を掛ける。

記号の位置は正解を使い（検出の位置ずれは測らない）、検出の種別を 2 通りで与える:
  correct  検出が正しい。手がかりが正しい種別を誤って書き換えないか（害）を測る
  swapped  リレーコイル↔表示灯をすべて取り違えた検出。手がかりがどれだけ直すか（ご指摘 No.3）を測る
"""

from __future__ import annotations

import json
import random
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from analysis.synthetic_circuits.scan import scan_variant

WIDTH, HEIGHT = 1755, 1241
TOP_Y, CONTACT_YS, LOAD_Y, BOTTOM_Y = 130, (270, 390), 540, 700
CIRCLE = 64
FONT_SIZE = 18
CIRCLE_CLASSES = ("relay_coil", "lamp")
CLASS_LABEL = {
    "relay_coil": "リレーコイル", "lamp": "表示灯", "contact_a": "a接点（メーク）", "contact_b": "b接点（ブレーク）",
    "junction": "接続点", "power_bus": "電源母線",
}
OTHER = {"relay_coil": "lamp", "lamp": "relay_coil"}

# 書き方ごとの正解の種別と、手がかりから期待する判定（確定 / 要確認）。
STYLES: dict[str, tuple[str, str]] = {
    # 円の中に器具番号、リレー表の行、同じ図面に同じ器具番号の接点
    "relay_table_contact": ("relay_coil", "確定"),
    # リレー表の行だけ（接点は他の図面）
    "relay_table": ("relay_coil", "確定"),
    # 同じ図面の接点だけ（リレー表に行が無い）。弱い手がかり 1 つなので要確認
    "relay_contact": ("relay_coil", "要確認"),
    # 手がかり無し
    "relay_bare": ("relay_coil", "要確認"),
    # 円の中に×印、近くに色記号
    "lamp_x_color": ("lamp", "確定"),
    # 円の中に×印だけ（近くの文字は器具番号）
    "lamp_x": ("lamp", "確定"),
    # ×印なし、円の中に色記号、近くにも色記号（別々の手がかり 2 つ）
    "lamp_color_twice": ("lamp", "確定"),
    # ×印なし、円の中に色記号だけ。弱い手がかり 1 つなので要確認
    "lamp_color_inside": ("lamp", "要確認"),
}
SCENARIOS = ("correct", "swapped")
_LETTERS = "ACEHKMNPRTUWX"
_COLORS = ("RL", "GL", "WL", "OL", "YL")


@dataclass(frozen=True)
class CirclePage:
    page_id: str
    image: Image.Image
    symbols: list[dict[str, Any]]  # 正解: ref, class_key, bbox(px), label, style（円のみ）
    relay_table: list[str]


def _font() -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    # Pillow 同梱の字体（どの PC でも同じ図面になる）。
    return ImageFont.load_default(size=FONT_SIZE)


def _device(rng: random.Random, used: set[str]) -> str:
    while True:
        name = f"{rng.randint(11, 98)}{rng.choice(_LETTERS)}{rng.choice(_LETTERS)}"
        if name not in used:
            used.add(name)
            return name


def _box(cx: float, cy: float, x0: float, y0: float, x1: float, y1: float) -> dict[str, float]:
    return {"x0": cx + x0, "y0": cy + y0, "x1": cx + x1, "y1": cy + y1}


def make_page(index: int, seed: int) -> CirclePage:
    """1 枚の図面: 4〜6 本の回路（接点 2 つ → 負荷 1 つ）と、下のリレー表。"""
    rng = random.Random(seed * 1_000_003 + index)
    used: set[str] = set()
    rungs = rng.randint(4, 6)
    styles = list(STYLES)
    # どの図面にもリレーコイルと表示灯の両方を置く。
    chosen = [rng.choice(styles[:4]), rng.choice(styles[4:])] + [rng.choice(styles) for _ in range(rungs - 2)]
    rng.shuffle(chosen)
    loads: list[dict[str, Any]] = []
    for style in chosen:
        gold, _expected = STYLES[style]
        load: dict[str, Any] = {"class_key": gold, "style": style}
        if gold == "relay_coil":
            load["label"] = _device(rng, used)
        else:
            load["label"] = rng.choice(_COLORS) + str(rng.randint(1, 9))
            near = {"lamp_x_color": load["label"], "lamp_color_twice": load["label"], "lamp_x": _device(rng, used)}
            load["near"] = near.get(style, "")
        loads.append(load)
    # 接点の器具番号: 同じ図面に接点が要るリレーを先に、残りは押しボタン・リミットスイッチ等。
    needs_contact = [load["label"] for load in loads if load["style"] in {"relay_table_contact", "relay_contact"}]
    others = [f"{rng.choice(('PB', 'LS', 'TS', 'CS'))}{rng.randint(1, 99)}" for _ in range(2 * rungs)]
    slots = needs_contact + others[: 2 * rungs - len(needs_contact)]
    rng.shuffle(slots)
    # 他の図面のリレーの接点（リレー表・同じ図面の接点の手がかりに混ざらない名前）。
    table_devices = [load["label"] for load in loads if load["style"] in {"relay_table_contact", "relay_table"}]
    table_devices += [_device(rng, used) for _ in range(rng.randint(1, 3))]
    rng.shuffle(table_devices)

    left, right = 260, WIDTH - 300
    xs = [round(left + i * (right - left) / (rungs - 1)) for i in range(rungs)]
    symbols: list[dict[str, Any]] = []

    def add(ref: str, class_key: str, cx: float, cy: float, box: dict[str, float], label: str = "", **extra: Any) -> None:
        symbols.append({"ref": ref, "class_key": class_key, "cx": cx, "cy": cy, "bbox": box, "label": label, **extra})

    add("P", "power_bus", 110, TOP_Y, _box(110, TOP_Y, -9, -9, 9, 9), "PER00001")
    add("N", "power_bus", WIDTH - 110, BOTTOM_Y, _box(WIDTH - 110, BOTTOM_Y, -9, -9, 9, 9), "NER00001")
    for rung, (x, load) in enumerate(zip(xs, loads, strict=True), 1):
        add(f"JT{rung}", "junction", x, TOP_Y, _box(x, TOP_Y, -6, -6, 6, 6))
        for slot, cy in enumerate(CONTACT_YS):
            kind = rng.choice(("contact_a", "contact_a", "contact_b"))
            # 接点の名前は棒の右、枠の中（text_extraction.symbol_label）。
            add(f"C{rung}-{slot + 1}", kind, x, cy, _box(x, cy, -24, -33, 70, 33), slots[2 * (rung - 1) + slot])
        half = CIRCLE / 2
        add(f"L{rung}", load["class_key"], x, LOAD_Y, _box(x, LOAD_Y, -half, -half, half, half), load["label"],
            style=load["style"], near=load.get("near", ""))
        add(f"JB{rung}", "junction", x, BOTTOM_Y, _box(x, BOTTOM_Y, -6, -6, 6, 6))
    return CirclePage(f"CIR{index:04d}", _render(symbols, table_devices, xs, rng), symbols, table_devices)


def _render(symbols: list[dict[str, Any]], table_devices: list[str], xs: list[int], rng: random.Random) -> Image.Image:
    image = Image.new("L", (WIDTH, HEIGHT), 255)
    draw = ImageDraw.Draw(image)
    font = _font()
    draw.rectangle((45, 45, WIDTH - 45, HEIGHT - 45), outline=0, width=2)
    draw.line((110, TOP_Y, xs[-1], TOP_Y), fill=0, width=2)
    draw.line((xs[0], BOTTOM_Y, WIDTH - 110, BOTTOM_Y), fill=0, width=2)
    for x in xs:
        draw.line((x, TOP_Y, x, CONTACT_YS[0] - 33), fill=0, width=2)
        draw.line((x, CONTACT_YS[0] + 33, x, CONTACT_YS[1] - 33), fill=0, width=2)
        draw.line((x, CONTACT_YS[1] + 33, x, LOAD_Y - CIRCLE // 2), fill=0, width=2)
        draw.line((x, LOAD_Y + CIRCLE // 2, x, BOTTOM_Y), fill=0, width=2)
    for symbol in symbols:
        key, cx, cy = symbol["class_key"], round(symbol["cx"]), round(symbol["cy"])
        if key == "junction":
            draw.ellipse((cx - 5, cy - 5, cx + 5, cy + 5), fill=0)
        elif key == "power_bus":
            draw.rectangle((cx - 5, cy - 5, cx + 5, cy + 5), fill=0)
            draw.text((cx + 12, cy - 24), symbol["label"], fill=0, font=font)
        elif key in {"contact_a", "contact_b"}:
            mid = cy
            draw.line((cx, cy - 33, cx, mid - 8), fill=0, width=2)
            draw.line((cx, mid + 8, cx, cy + 33), fill=0, width=2)
            side = 1 if key == "contact_a" else -1
            draw.line((cx, mid - 8, cx + side * 15, mid + 4), fill=0, width=2)
            if key == "contact_b":
                draw.line((cx, mid + 8, cx - 15, mid - 2), fill=0, width=2)
            draw.ellipse((cx - 3, mid - 11, cx + 3, mid - 5), fill=255, outline=0, width=2)
            draw.ellipse((cx - 3, mid + 5, cx + 3, mid + 11), fill=255, outline=0, width=2)
            draw.text((cx + 20, mid - 9), symbol["label"], fill=0, font=font)
        elif key in CIRCLE_CLASSES:
            _draw_circle(draw, font, symbol)
    _draw_relay_table(draw, font, table_devices, rng)
    return image


def _draw_circle(draw: ImageDraw.ImageDraw, font: Any, symbol: dict[str, Any]) -> None:
    cx, cy, r = symbol["cx"], symbol["cy"], CIRCLE / 2
    draw.ellipse((cx - r, cy - r, cx + r, cy + r), outline=0, width=2)
    style = symbol["style"]
    if style.startswith("lamp_x"):
        d = r * 0.7071
        draw.line((cx - d, cy - d, cx + d, cy + d), fill=0, width=2)
        draw.line((cx + d, cy - d, cx - d, cy + d), fill=0, width=2)
    if style.startswith("relay") or style.startswith("lamp_color"):
        # 器具番号・色記号は円の中、真ん中に書く。
        draw.text((cx, cy), symbol["label"], fill=0, font=font, anchor="mm")
    if symbol.get("near"):
        draw.text((cx + r + 10, cy - 9), symbol["near"], fill=0, font=font)


def _draw_relay_table(draw: ImageDraw.ImageDraw, font: Any, devices: list[str], rng: random.Random) -> None:
    """図面の下のリレー表。器具ごとに端子の行を 2 行（器具番号は左、接点・シートは右）。罫線で欄を区切る。"""
    top, row_h, gap = 900, 26, 42
    draw.rectangle((90, top - 30, 1290, HEIGHT - 60), outline=0, width=1)
    columns = (110, 410, 710, 1010)
    per_column = 3
    for i, device in enumerate(devices[: len(columns) * per_column]):
        x = columns[i // per_column]
        y = top + (i % per_column) * (2 * row_h + gap)
        draw.text((x, y + 2), device, fill=0, font=font)
        for row in range(2):
            ry = y + row * row_h
            draw.text((x + 76, ry + 2), f"{rng.randint(1, 9)}-{rng.randint(1, 12)}-{rng.randint(1, 9)}", fill=0, font=font)
            draw.text((x + 150, ry + 2), rng.choice("AB"), fill=0, font=font)
            draw.text((x + 192, ry + 2), f"FV{rng.randint(10000, 99999)}", fill=0, font=font)
            draw.line((x + 64, ry + row_h, x + 280, ry + row_h), fill=0, width=1)
        for vx in (x + 64, x + 140, x + 180):
            draw.line((vx, y, vx, y + 2 * row_h), fill=0, width=1)


def detections(page: CirclePage, scenario: str) -> list[dict[str, Any]]:
    """正解の記号の位置。scenario が swapped ならリレーコイルと表示灯の種別を入れ替える。"""
    out = []
    for symbol in page.symbols:
        box = symbol["bbox"]
        key = symbol["class_key"]
        if scenario == "swapped":
            key = OTHER.get(key, key)
        out.append({
            "class_key": key, "class_label": CLASS_LABEL[key], "confidence": 1.0,
            "cx": (box["x0"] + box["x1"]) / 2 / WIDTH, "cy": (box["y0"] + box["y1"]) / 2 / HEIGHT,
            "w": (box["x1"] - box["x0"]) / WIDTH, "h": (box["y1"] - box["y0"]) / HEIGHT,
        })
    return out


def _empty() -> dict[str, Any]:
    return {"circles": 0, "before_errors": 0, "after_errors": 0, "confirmed_wrong": 0, "fixed": 0, "broken": 0,
            "review_required": 0, "expected_status_ok": 0, "confusion_after": Counter()}


def evaluate(count: int, work: Path, *, seed: int = 39, enable_ocr: bool = False,
             scenarios: tuple[str, ...] = SCENARIOS, start: int = 1) -> dict[str, Any]:
    """start 番から count 枚の合成図面を解析し、場面ごと・書き方ごとの取り違え件数を数える。"""
    from analysis import runtime

    work.mkdir(parents=True, exist_ok=True)
    totals = {scenario: _empty() for scenario in scenarios}
    by_style = {scenario: {style: _empty() for style in STYLES} for scenario in scenarios}
    decisions: list[dict[str, Any]] = []
    errors: dict[str, str] = {}
    saved = runtime.load_symbol_ocr_reader
    if not enable_ocr:
        runtime.load_symbol_ocr_reader = lambda *_a, **_k: (None, "none", "disabled for circle evaluation")
    try:
        for index in range(start, start + count):
            page = make_page(index, seed)
            path = work / f"{page.page_id}.png"
            Image.fromarray(scan_variant(np.asarray(page.image), seed + index)).save(path)
            circles = [(i, s) for i, s in enumerate(page.symbols) if s["class_key"] in CIRCLE_CLASSES]
            for scenario in scenarios:
                try:
                    result = runtime.analyze_detections(path, detections(page, scenario), source={"synthetic": page.page_id})
                except Exception as exc:  # noqa: BLE001 - 1 枚の失敗で残りを止めない（失敗は件数に出す）
                    errors[f"{page.page_id}/{scenario}"] = f"{type(exc).__name__}: {exc}"
                    continue
                found = result["symbols"]
                for i, gold in circles:
                    # 記号は検出の順に symbol_001… になる（runtime._symbols_from_detections）。
                    symbol = next(s for s in found if s["id"] == f"symbol_{i + 1:03d}")
                    detected = symbol.get("detected_class", symbol["class_name"])
                    final = symbol["class_name"]
                    review = symbol.get("symbol_review") or {}
                    status = review.get("status", "")
                    row = {
                        "page": page.page_id, "ref": gold["ref"], "scenario": scenario, "style": gold["style"],
                        "gold": gold["class_key"], "detected": detected, "final": final, "status": status,
                        "expected_status": STYLES[gold["style"]][1], "label_read": symbol.get("label", ""),
                        "cues": [cue.get("cue") for cue in review.get("cues", [])],
                    }
                    decisions.append(row)
                    for bucket in (totals[scenario], by_style[scenario][gold["style"]]):
                        bucket["circles"] += 1
                        bucket["before_errors"] += detected != gold["class_key"]
                        bucket["after_errors"] += final != gold["class_key"]
                        bucket["confirmed_wrong"] += final != gold["class_key"] and status == "確定"
                        bucket["fixed"] += detected != gold["class_key"] and final == gold["class_key"]
                        bucket["broken"] += detected == gold["class_key"] and final != gold["class_key"]
                        bucket["review_required"] += status == "要確認"
                        bucket["expected_status_ok"] += status == row["expected_status"]
                        if final != gold["class_key"]:
                            bucket["confusion_after"][f"{gold['class_key']}->{final}"] += 1
    finally:
        runtime.load_symbol_ocr_reader = saved

    def plain(bucket: dict[str, Any]) -> dict[str, Any]:
        return {**bucket, "confusion_after": dict(sorted(bucket["confusion_after"].items()))}

    report = {
        "pages": count, "seed": seed, "ocr": enable_ocr, "errors": errors,
        "gold": dict(Counter(d["gold"] for d in decisions if d["scenario"] == scenarios[0])),
        "scenarios": {s: plain(totals[s]) for s in scenarios},
        "by_style": {s: {style: plain(b) for style, b in by_style[s].items() if b["circles"]} for s in scenarios},
        "decisions": decisions,
    }
    (work / "circles.json").write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    return report
