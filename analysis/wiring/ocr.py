"""ラベルの文字認識（OCR）の読み込み。"""

from __future__ import annotations

import os
import sys
from functools import lru_cache
from pathlib import Path
from typing import Any

from analysis import ocr_cache


def load_symbol_ocr_reader(engine: str, language: str) -> tuple[Any | None, str, str | None]:
    if engine == "none":
        return None, "none", None
    try:
        if engine == "easyocr":
            import easyocr

            return easyocr.Reader([language], gpu=False, verbose=False), engine, None
        if engine == "rapidocr":
            return shared_reader(), engine, None
    except (ImportError, ModuleNotFoundError) as caught:
        warning = f"symbol label OCR '{engine}' is unavailable ({caught}); continuing without symbol label OCR"
        return None, "none", warning
    raise ValueError(f"unsupported symbol OCR engine: {engine}")


@lru_cache(maxsize=1)
def _rapidocr() -> Any:
    """RapidOCR はモデルの読み込みに時間がかかるので、図面ごとに作らず 1 つを使い回す（高速化）。

    読み取りは状態を持たない（ページの OCR も 1 つの reader を複数のスレッドで並べて呼んでいる。analysis.parallel）。
    同じ画素をもう一度読むときは、アプリが置き場を決めていれば前の結果を使う（analysis.ocr_cache、DD-0056）。
    """
    from rapidocr_onnxruntime import RapidOCR

    return ocr_cache.wrap(RapidOCR(), ocr_cache.engine_tag("default"))


def shared_reader() -> Any:
    """既定の RapidOCR（記号のラベル・ページの文字・図面の向き・シート番号で共有する 1 つ）。

    使い回す reader を作った後でも、OCR のライブラリを読み込めない環境なら ImportError にする。
    """
    import rapidocr_onnxruntime  # noqa: F401

    return _rapidocr()


def load_crop_ocr_reader(page_reader: Any) -> Any:
    """The recognizer for the 4x reads of each connector / contact / coil box (the page is read with page_reader).

    SEQANNO_OCR_REC_MODEL names a recognizer fine-tuned on the customer's drawings (ocr_finetune.py), which
    ships outside the repository. It reads only these boxes: relay tables, notes and sheet numbers keep the
    bundled recognizer, which the fine-tuning was not checked against.
    """
    rec_model = os.environ.get("SEQANNO_OCR_REC_MODEL")
    if not rec_model or page_reader is None:
        return page_reader
    if not Path(rec_model).is_file():
        print(f"SEQANNO_OCR_REC_MODEL={rec_model} does not exist; using the bundled recognizer", file=sys.stderr)
        return page_reader
    return _crop_reader(rec_model)


@lru_cache(maxsize=2)
def _crop_reader(rec_model: str) -> Any:
    """差し替えた文字認識の reader も、図面ごとに作らず使い回す。"""
    from rapidocr_onnxruntime import RapidOCR

    return ocr_cache.wrap(RapidOCR(rec_model_path=rec_model), ocr_cache.engine_tag("crop", rec_model))
