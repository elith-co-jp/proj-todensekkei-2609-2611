"""リレーコイルと表示灯（ランプ）の判別（#39）。

円形の記号を 4 つの手がかりで確かめる: 円の中の×印（表示灯）、近くの表示灯の文字（表示灯）、
リレー表の器具番号（リレー）、同じ器具番号の接点（リレー）。手がかりが一方の種別にそろったときだけ
種別を確定し、検出の種別と違えば書き換える。分かれたとき・手がかりが無いときは検出の種別のまま要確認とする。
"""

from __future__ import annotations

import math
import re
from typing import Any

import cv2
import numpy as np

from analysis.text_extraction import normalize, same_relay_device

CIRCLE_CLASSES = ("relay_coil", "lamp")
CONTACT_CLASSES = {"contact_a", "contact_b"}
CLASS_LABELS = {"relay_coil": "リレーコイル", "lamp": "表示灯"}
NEAR_PX = 40
X_MIN_COVER = 0.85
X_MAX_WEDGE = 0.09
# RL/GL/WL/OL/YL/BL（色の頭文字 + L）、表示灯・回転灯（OCR は簡体字に寄せることがある）、色の漢字。
_LAMP_WORD = re.compile(r"(?:[RGWOYB]L\d{0,2}|表示[灯燈]|回[転转][灯燈]|ランプ|[赤緑绿白橙黄青](?:色|灯)?)")
# 円の中の×印・リレー表は強い手がかり。文字と接点は 1 つだけでは確定しない。
STRONG_CUES = {"x_mark", "relay_table"}


def _circle(binary: np.ndarray, box: dict[str, float]) -> tuple[float, float, float]:
    """The circle drawn in a coil or lamp box; the box also holds the terminal numbers above and below."""
    x0, y0, x1, y1 = (int(round(box[k])) for k in ("x0", "y0", "x1", "y1"))
    crop = binary[max(0, y0):y1, max(0, x0):x1]
    width = x1 - x0
    found = None
    if crop.size and width > 8:
        circles = cv2.HoughCircles(cv2.GaussianBlur(crop, (3, 3), 0), cv2.HOUGH_GRADIENT, dp=1, minDist=width,
                                   param1=100, param2=12, minRadius=int(0.25 * width), maxRadius=int(0.55 * width))
        if circles is not None:
            cx, cy = crop.shape[1] / 2, crop.shape[0] / 2
            found = min(circles[0], key=lambda c: (c[0] - cx) ** 2 + (c[1] - cy) ** 2)
    if found is None:
        return (x0 + x1) / 2, (y0 + y1) / 2, 0.36 * width
    return x0 + float(found[0]), y0 + float(found[1]), float(found[2])


def _diagonal_cover(binary: np.ndarray, cx: float, cy: float, r: float, angle: float) -> float:
    height, width = binary.shape
    hits = []
    for t in np.linspace(-0.65 * r, 0.65 * r, 27):
        x, y = int(round(cx + math.cos(angle) * t)), int(round(cy + math.sin(angle) * t))
        hits.append(bool(binary[max(0, y - 1):min(height, y + 2), max(0, x - 1):min(width, x + 2)].any()))
    return float(np.mean(hits))


def _wedge_ink(binary: np.ndarray, cx: float, cy: float, r: float) -> float:
    """Ink between the arms of an X (around 0, 90, 180 and 270 degrees)."""
    reach = int(r) + 1
    ys, xs = np.mgrid[-reach:reach + 1, -reach:reach + 1]
    radius = np.hypot(xs, ys)
    angle = np.degrees(np.arctan2(ys, xs)) % 90
    picked = (radius >= 0.25 * r) & (radius <= 0.7 * r) & ((angle <= 20) | (angle >= 70))
    yy, xx = ys[picked] + int(round(cy)), xs[picked] + int(round(cx))
    inside = (yy >= 0) & (yy < binary.shape[0]) & (xx >= 0) & (xx < binary.shape[1])
    return float((binary[yy[inside], xx[inside]] > 0).mean()) if inside.any() else 1.0


def x_mark(binary: np.ndarray, box: dict[str, float]) -> bool:
    """An X fills both diagonals of the circle and leaves the space between its arms empty.

    Device numbers inside a relay coil also cross the diagonals, but they fill that space too.
    """
    cx, cy, r = _circle(binary, box)
    diagonal = min(_diagonal_cover(binary, cx, cy, r, math.radians(a)) for a in (45, 135))
    return diagonal >= X_MIN_COVER and _wedge_ink(binary, cx, cy, r) <= X_MAX_WEDGE


def _lamp_words(symbol: dict[str, Any], texts: list[dict[str, Any]]) -> list[str]:
    x0, y0, x1, y1 = (symbol["bbox"][k] for k in ("x0", "y0", "x1", "y1"))
    near = [symbol.get("label") or ""]
    for item in texts:
        bx0, by0, bx1, by1 = item["box"]
        cx, cy = (bx0 + bx1) / 2, (by0 + by1) / 2
        if x0 - NEAR_PX <= cx <= x1 + NEAR_PX and y0 - NEAR_PX <= cy <= y1 + NEAR_PX:
            near.append(item.get("text") or "")
    return [token for text in near for token in normalize(text).split() if _LAMP_WORD.fullmatch(token)]


def review_circles(binary: np.ndarray, symbols: list[dict[str, Any]], text_result: dict[str, Any]) -> dict[str, Any]:
    """Write symbol_review on every relay coil and lamp, and change the class when the cues agree."""
    texts = text_result.get("texts", [])
    rows = text_result.get("relay_table", {}).get("rows", [])
    contacts = [s for s in symbols if s.get("class_name") in CONTACT_CLASSES and s.get("label")]
    counts = {"reviewed": 0, "確定": 0, "要確認": 0, "changed": 0}
    for symbol in symbols:
        detected = symbol.get("class_name")
        if detected not in CIRCLE_CLASSES:
            continue
        label = symbol.get("label") or ""
        cues = []
        if x_mark(binary, symbol["bbox"]):
            cues.append({"cue": "x_mark", "suggests": "lamp"})
        cues += [{"cue": "lamp_word", "suggests": "lamp", "text": word} for word in dict.fromkeys(_lamp_words(symbol, texts))]
        device = next((row["device"] for row in rows if label and same_relay_device(label, row["device"])), None)
        if device:
            cues.append({"cue": "relay_table", "suggests": "relay_coil", "text": device})
        same = [s["id"] for s in contacts if label and same_relay_device(s["label"], label)]
        if same:
            cues.append({"cue": "same_device_contacts", "suggests": "relay_coil", "symbol_ids": same})
        suggested = {cue["suggests"] for cue in cues}
        settled = len(suggested) == 1 and (len(cues) >= 2 or cues[0]["cue"] in STRONG_CUES)
        suggestion = suggested.pop() if len(suggested) == 1 else detected
        symbol["symbol_review"] = {"class_suggestion": suggestion, "cues": cues, "status": "確定" if settled else "要確認"}
        counts["reviewed"] += 1
        counts[symbol["symbol_review"]["status"]] += 1
        if settled and suggestion != detected:
            symbol["detected_class"] = detected
            symbol["type"] = symbol["class_name"] = suggestion
            symbol["class_label"] = CLASS_LABELS[suggestion]
            counts["changed"] += 1
    return counts

