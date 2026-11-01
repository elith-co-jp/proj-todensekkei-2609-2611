"""図面下部の文字からシート番号の候補を選ぶ。

候補を返すだけで sheet_no は書き換えない。確定は利用者が画面で行う。
"""

from __future__ import annotations

import re
import unicodedata
from collections import Counter
from pathlib import Path
from typing import Literal, TypedDict

from PIL import Image

OCR_CROP_RATIO = 0.30
SHEET_NO_PATTERN = re.compile(r"^[A-Z]{1,4}[0-9]{3,8}(?:-[0-9A-Z]{1,4})?$")
LABEL_WORDS = ("シート", "SHEET", "図番", "DWG")
# OCR は同じ行で近いラベルと番号を 1 つの文字列（例: DWGNO.ER21226）にまとめて返す。
_LABEL_PREFIX = re.compile(r"^(?:シート(?:番号|NO\.?)?|SHEET(?:NO\.?)?|図番|DWG(?:NO\.?)?)[:.]?")

Region = Literal["title_block", "margin"]


class OcrItem(TypedDict):
    text: str
    score: float
    box: tuple[float, float, float, float]


class SheetNumberCandidate(TypedDict):
    text: str
    score: float
    region: Region


def _normalize(text: str) -> str:
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", text).upper())


def _region(box: tuple[float, float, float, float], width: int, height: int) -> Region | None:
    cx, cy = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2
    if cx >= 0.55 * width and cy >= 0.75 * height:
        return "title_block"
    if cy >= 0.90 * height:
        return "margin"
    return None


def _sheet_number(text: str) -> tuple[str, bool] | None:
    """(番号, ラベルと同じ文字列だったか)。番号として読めなければ None。"""
    if SHEET_NO_PATTERN.match(text):
        return text, False
    stripped = _LABEL_PREFIX.sub("", text, count=1)
    if stripped != text and SHEET_NO_PATTERN.match(stripped):
        return stripped, True
    return None


def _is_label_for(label_box: tuple, number_box: tuple, width: int, height: int) -> bool:
    lx0, ly0, lx1, ly1 = label_box
    nx0, ny0, nx1, ny1 = number_box
    left = (lx0 + lx1) / 2 < nx0 and nx0 - lx1 <= 0.12 * width and ly0 < ny1 and ly1 > ny0
    above = (ly0 + ly1) / 2 < ny0 and ny0 - ly1 <= 0.06 * height and lx0 < nx1 and lx1 > nx0
    return left or above


def pick_sheet_number_candidates(
    items: list[OcrItem], width: int, height: int
) -> list[SheetNumberCandidate]:
    """OCR 結果からシート番号らしい文字列を最大 3 件、確からしい順に返す。

    表題欄（右下）にあるもの、同じ番号が複数箇所にあるもの、「シート」「SHEET」「図番」
    「DWG」のラベルが左か上にあるものほど高く評価する。
    """
    label_boxes = [i["box"] for i in items if any(word in _normalize(i["text"]) for word in LABEL_WORDS)]
    found = []
    for item in items:
        region = _region(item["box"], width, height)
        parsed = _sheet_number(_normalize(item["text"])) if region else None
        if parsed is not None:
            found.append((*parsed, region, item))
    occurrences = Counter(number for number, *_ in found)

    best: dict[str, SheetNumberCandidate] = {}
    for number, labelled, region, item in found:
        score = 0.5 * item["score"]
        if region == "title_block":
            score += 0.3
        if occurrences[number] >= 2:
            score += 0.2
        if labelled or any(_is_label_for(box, item["box"], width, height) for box in label_boxes):
            score += 0.2
        score = round(min(score, 1.0), 3)
        if number not in best or score > best[number]["score"]:
            best[number] = {"text": number, "score": score, "region": region}
    return sorted(best.values(), key=lambda c: c["score"], reverse=True)[:3]


def read_ocr_items(path: Path) -> tuple[list[OcrItem], int, int]:
    """ページ下部 30% を 1 回だけ OCR し、文字枠をページのピクセル座標で返す。"""
    try:
        import numpy as np
        from rapidocr_onnxruntime import RapidOCR
    except ImportError as exc:
        raise RuntimeError(
            "文字認識（OCR）を利用できない環境のため、シート番号を読み取れません。手入力してください。"
        ) from exc
    with Image.open(path) as opened:
        page = opened.convert("RGB")
    width, height = page.size
    top = int(height * (1 - OCR_CROP_RATIO))
    results, _ = RapidOCR()(np.asarray(page.crop((0, top, width, height))))
    items: list[OcrItem] = []
    for points, text, score in results or []:
        xs = [float(x) for x, _ in points]
        ys = [float(y) + top for _, y in points]
        items.append({"text": text, "score": float(score), "box": (min(xs), min(ys), max(xs), max(ys))})
    return items, width, height


def suggest_sheet_numbers(path: Path) -> list[SheetNumberCandidate]:
    items, width, height = read_ocr_items(path)
    return pick_sheet_number_candidates(items, width, height)
