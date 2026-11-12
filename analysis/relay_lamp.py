"""リレーコイルと表示灯（ランプ）の判別（#39）。

円形の記号を 4 つの手がかりで確かめる: 円の中の×印（表示灯）、近くの表示灯の文字（表示灯）、
リレー表の器具番号（リレー）、同じ器具番号の接点（リレー）。手がかりが一方の種別にそろったときだけ
種別を確定し、検出の種別と違えば書き換える。分かれたとき・手がかりが無いときは検出の種別のまま要確認とする。
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

import cv2
import numpy as np

from analysis.text_extraction import normalize, same_relay_device
from analysis.wiring import classes

class CircleClass(StrEnum):
    RELAY_COIL = "relay_coil"
    LAMP = "lamp"


class ReviewStatus(StrEnum):
    CONFIRMED = "確定"
    REVIEW_REQUIRED = "要確認"


@dataclass(frozen=True)
class CircleEvidence:
    detected_class: CircleClass
    has_x_mark: bool
    interior_text: str
    nearby_texts: tuple[str, ...] = ()
    relay_table_devices: tuple[str, ...] = ()
    same_sheet_contact_ids: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "circle_interior": {"text": self.interior_text, "x_mark": self.has_x_mark},
            "nearby_texts": list(self.nearby_texts),
            "relay_table_devices": list(self.relay_table_devices),
            "same_sheet_contact_ids": list(self.same_sheet_contact_ids),
        }


@dataclass(frozen=True)
class EvidenceCue:
    source: str
    suggests: CircleClass
    text: str | None = None
    symbol_ids: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        cue: dict[str, Any] = {"cue": self.source, "suggests": self.suggests.value}
        if self.text is not None:
            cue["text"] = self.text
        if self.symbol_ids:
            cue["symbol_ids"] = list(self.symbol_ids)
        return cue


@dataclass(frozen=True)
class CircleClassification:
    evidence: CircleEvidence
    suggested_class: CircleClass
    status: ReviewStatus
    cues: tuple[EvidenceCue, ...]

    @property
    def final_class(self) -> CircleClass:
        if self.status is ReviewStatus.CONFIRMED:
            return self.suggested_class
        return self.evidence.detected_class

    def to_review_dict(self) -> dict[str, Any]:
        return {
            "class_suggestion": self.suggested_class.value,
            "cues": [cue.to_dict() for cue in self.cues],
            "evidence": self.evidence.to_dict(),
            "status": self.status.value,
        }


CIRCLE_CLASSES = tuple(circle_class.value for circle_class in CircleClass)
CONTACT_CLASSES = classes.CONTACTS
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


def _lamp_words(text: str) -> list[str]:
    return [token for token in normalize(text).split() if _LAMP_WORD.fullmatch(token)]


def _nearby_texts(symbol: dict[str, Any], texts: list[dict[str, Any]]) -> tuple[str, ...]:
    x0, y0, x1, y1 = (symbol["bbox"][k] for k in ("x0", "y0", "x1", "y1"))
    near = []
    for item in texts:
        bx0, by0, bx1, by1 = item["box"]
        cx, cy = (bx0 + bx1) / 2, (by0 + by1) / 2
        is_near = x0 - NEAR_PX <= cx <= x1 + NEAR_PX and y0 - NEAR_PX <= cy <= y1 + NEAR_PX
        is_inside = x0 <= cx <= x1 and y0 <= cy <= y1
        if is_near and not is_inside:
            near.append(item.get("text") or "")
    return tuple(dict.fromkeys(text for text in near if text))


def classify_circle(evidence: CircleEvidence) -> CircleClassification:
    cues: list[EvidenceCue] = []
    if evidence.has_x_mark:
        cues.append(EvidenceCue("x_mark", CircleClass.LAMP))
    cues.extend(
        EvidenceCue("circle_interior_text", CircleClass.LAMP, text=word)
        for word in dict.fromkeys(_lamp_words(evidence.interior_text))
    )
    cues.extend(
        EvidenceCue("nearby_text", CircleClass.LAMP, text=word)
        for text in evidence.nearby_texts
        for word in dict.fromkeys(_lamp_words(text))
    )
    cues.extend(
        EvidenceCue("relay_table", CircleClass.RELAY_COIL, text=device)
        for device in evidence.relay_table_devices
    )
    if evidence.same_sheet_contact_ids:
        cues.append(
            EvidenceCue(
                "same_sheet_contacts",
                CircleClass.RELAY_COIL,
                symbol_ids=evidence.same_sheet_contact_ids,
            )
        )
    suggestions = {cue.suggests for cue in cues}
    settled = len(suggestions) == 1 and (
        len(cues) >= 2 or (cues and cues[0].source in STRONG_CUES)
    )
    suggested = next(iter(suggestions)) if len(suggestions) == 1 else evidence.detected_class
    status = ReviewStatus.CONFIRMED if settled else ReviewStatus.REVIEW_REQUIRED
    return CircleClassification(evidence, suggested, status, tuple(cues))


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
        nearby_texts = _nearby_texts(symbol, texts)
        relay_table_devices = tuple(dict.fromkeys(
            row["device"] for row in rows if label and same_relay_device(label, row["device"])
        ))
        same_sheet_contact_ids = tuple(
            s["id"] for s in contacts if label and same_relay_device(s["label"], label)
        )
        evidence = CircleEvidence(
            detected_class=CircleClass(detected),
            has_x_mark=x_mark(binary, symbol["bbox"]),
            interior_text=label,
            nearby_texts=nearby_texts,
            relay_table_devices=relay_table_devices,
            same_sheet_contact_ids=same_sheet_contact_ids,
        )
        classification = classify_circle(evidence)
        symbol["symbol_review"] = classification.to_review_dict()
        counts["reviewed"] += 1
        counts[classification.status.value] += 1
        if classification.final_class.value != detected:
            symbol["detected_class"] = detected
            symbol["type"] = symbol["class_name"] = classification.final_class.value
            symbol["class_label"] = CLASS_LABELS[classification.final_class.value]
            counts["changed"] += 1
    return counts

