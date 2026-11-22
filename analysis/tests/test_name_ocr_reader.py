"""名称・注記の読み直しに使う文字認識を、本物の onnx モデルで動かす。

同梱する PP-OCRv5 のモデルはリポジトリに無いので、rapidocr に入っている同じ形式の認識モデル（PP-OCRv4）で、
切り出し・拡大・認識の流れが動くことを確かめる。
"""

from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image, ImageDraw, ImageFont

from analysis import name_ocr

rapidocr = pytest.importorskip("rapidocr_onnxruntime")
REC_MODEL = Path(rapidocr.__file__).parent / "models" / "ch_PP-OCRv4_rec_infer.onnx"


def _page():
    image = Image.new("RGB", (400, 120), "white")
    try:
        font = ImageFont.truetype("DejaVuSans.ttf", 28)
    except OSError:
        font = ImageFont.load_default()
    ImageDraw.Draw(image).text((40, 40), "R100", fill="black", font=font)
    return image


@pytest.mark.skipif(not REC_MODEL.is_file(), reason="rapidocr の認識モデルが無い")
@pytest.mark.parametrize("threads", [None, 1])
def test_reader_crops_and_reads_a_name(threads):
    read = name_ocr.make_reader(REC_MODEL, threads)
    text = read(_page(), (36, 36, 120, 76))
    assert text is not None and "100" in text
    assert read(Image.new("RGB", (60, 40), "white"), (5, 5, 50, 30)) in (None, "")
