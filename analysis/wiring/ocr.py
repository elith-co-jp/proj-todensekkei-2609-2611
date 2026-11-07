"""ラベルの文字認識（OCR）の読み込み。"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any


def load_symbol_ocr_reader(engine: str, language: str) -> tuple[Any | None, str, str | None]:
    if engine == "none":
        return None, "none", None
    try:
        if engine == "easyocr":
            import easyocr

            return easyocr.Reader([language], gpu=False, verbose=False), engine, None
        if engine == "rapidocr":
            from rapidocr_onnxruntime import RapidOCR

            return RapidOCR(), engine, None
    except (ImportError, ModuleNotFoundError) as caught:
        warning = f"symbol label OCR '{engine}' is unavailable ({caught}); continuing without symbol label OCR"
        return None, "none", warning
    raise ValueError(f"unsupported symbol OCR engine: {engine}")


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
    from rapidocr_onnxruntime import RapidOCR

    return RapidOCR(rec_model_path=rec_model)
