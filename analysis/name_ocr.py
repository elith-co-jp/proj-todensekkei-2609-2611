"""日本語の名称・注記を、日本語と中国語を一緒に学習した文字認識モデル（PP-OCRv5）で読み直す。

ページ全体の OCR（PP-OCRv4 の中国語モデル）は日本語を簡体字に寄せて読む（盤→盐、記→记）。
器具番号・端子番号・シート番号は従来のモデルのほうが正確なので、名称・注記と判定した文字枠だけを読み直す。
モデルファイルは exe に同梱する（scripts/fetch_base_model.py）。見つからなければ読み直さない。
"""

from __future__ import annotations

import os
from functools import cache
from pathlib import Path
from typing import Any

from PIL import Image

from analysis.text_extraction import LabelReader, _as_box

MODEL_NAME = "ch_PP-OCRv5_rec_mobile.onnx"
# 調整用の 15 図面で決めた（倍率 2・余白 2 px）。
CROP_SCALE = 2
CROP_PADDING_PX = 2


def model_path() -> Path | None:
    from runtime import resource_base

    candidates = [Path(os.environ["NAME_OCR_MODEL"])] if os.environ.get("NAME_OCR_MODEL") else []
    candidates += [resource_base() / "models" / MODEL_NAME, resource_base() / ".build-assets" / MODEL_NAME]
    return next((p for p in candidates if p.is_file()), None)


def make_reader(path: Path, threads: int | None = None) -> LabelReader:
    """threads caps the onnxruntime threads (parallel evaluation workers); None leaves the library default."""
    import numpy as np
    from rapidocr_onnxruntime import RapidOCR

    limits = {f"{part}_intra_op_num_threads": threads for part in ("det", "cls", "rec")} if threads else {}
    engine = RapidOCR(rec_model_path=str(path), **limits)

    def read(image: Image.Image, box: Any) -> str | None:
        x0, y0, x1, y1 = _as_box(box)
        crop = image.crop((int(x0) - CROP_PADDING_PX, int(y0) - CROP_PADDING_PX,
                           int(x1) + CROP_PADDING_PX, int(y1) + CROP_PADDING_PX)).convert("RGB")
        crop = crop.resize((crop.width * CROP_SCALE, crop.height * CROP_SCALE), Image.Resampling.LANCZOS)
        result, _ = engine(np.asarray(crop), use_det=False, use_cls=False, use_rec=True)
        return str(result[0][0]) if result else None

    return read


@cache
def default_reader() -> LabelReader | None:
    path = model_path()
    return make_reader(path) if path else None

