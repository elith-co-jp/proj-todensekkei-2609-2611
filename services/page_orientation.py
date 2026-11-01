"""横書き（ページ全体を 90° 回して描いた）図面を、登録時に縦向きに直す（#42）。

配線と文字の読取は、文字が左から右へ書かれた向きを前提にしている。検出は回した図面も学習したので向きによらず
読めるが（DD-0007 §4.6）、ページ全体が回っていると配線と文字の読取が崩れる。文字の検出枠の大半が縦長なら
ページが回っているとみなし、縦長の文字を左右どちらに回すと読めるかで向きを決める。
"""

from __future__ import annotations

import io
import logging
from functools import lru_cache
from typing import Any

import numpy as np
from PIL import Image

from services.rotation_copies import rotate_image

MIN_TEXT_PX = 25  # 1 文字の数字は向きの手がかりにしない
MIN_SIDEWAYS_BOXES = 10  # 文字の少ないページ（縦長の文字枠 17〜19 個）も判定する
SIDEWAYS_RATIO = 3.0  # 縦長の文字枠が横長の文字枠の 3 倍以上あればページが回っている
SAMPLE_BOXES = 12


@lru_cache(maxsize=1)
def _reader() -> Any:
    from rapidocr_onnxruntime import RapidOCR

    return RapidOCR()


def _text_boxes(image: Image.Image, reader: Any) -> list[tuple[float, float, float, float]]:
    found, _ = reader(np.asarray(image), use_det=True, use_cls=False, use_rec=False)
    boxes = []
    for quad in found or []:
        xs, ys = [p[0] for p in quad], [p[1] for p in quad]
        boxes.append((min(xs), min(ys), max(xs), max(ys)))
    return boxes


def _read_score(reader: Any, crop: Image.Image) -> float:
    found, _ = reader(np.asarray(crop), use_det=False, use_cls=False, use_rec=True)
    return sum(float(score) for _text, score in found or [])


def upright_turn(png: bytes, reader: Any | None = None) -> int:
    """反時計回りに何度（0・90・270）回せば、文字が左から右に読める向きになるか。"""
    reader = reader or _reader()
    with Image.open(io.BytesIO(png)) as opened:
        image = opened.convert("RGB")
    boxes = [b for b in _text_boxes(image, reader) if max(b[2] - b[0], b[3] - b[1]) >= MIN_TEXT_PX]
    tall = [b for b in boxes if b[3] - b[1] > 1.5 * (b[2] - b[0])]
    wide = sum(1 for b in boxes if b[2] - b[0] > 1.5 * (b[3] - b[1]))
    if len(tall) < MIN_SIDEWAYS_BOXES or len(tall) < SIDEWAYS_RATIO * wide:
        return 0
    clockwise = counter_clockwise = 0.0
    for box in sorted(tall, key=lambda b: b[3] - b[1], reverse=True)[:SAMPLE_BOXES]:
        crop = image.crop(tuple(round(v) for v in box))
        clockwise += _read_score(reader, crop.transpose(Image.Transpose.ROTATE_270))
        counter_clockwise += _read_score(reader, crop.transpose(Image.Transpose.ROTATE_90))
    # 文字を時計回りに回すと読める = ページが反時計回りに 90° 回っている。反時計回りに 270° 回して戻す。
    return 270 if clockwise > counter_clockwise else 90


def upright_page(png: bytes) -> tuple[bytes, int]:
    """横書きのページを縦向きに直した PNG と回した角度。判定できなければ元のまま（0）を返す。"""
    try:
        turn = upright_turn(png)
    except Exception:  # OCR が無い・画像が読めないときも登録は止めない
        logging.getLogger(__name__).exception("page orientation check failed")
        return png, 0
    return (rotate_image(png, turn), turn) if turn else (png, 0)

