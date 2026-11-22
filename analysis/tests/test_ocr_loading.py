"""ラベルの文字認識の読み込み: 使えない・知らないエンジン、本物の RapidOCR、図面に合わせた認識モデル。"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from analysis.wiring import ocr

rapidocr = pytest.importorskip("rapidocr_onnxruntime")
REC_MODEL = Path(rapidocr.__file__).parent / "models" / "ch_PP-OCRv4_rec_infer.onnx"


def test_engines_none_missing_fake_and_unknown(monkeypatch):
    assert ocr.load_symbol_ocr_reader("none", "en") == (None, "none", None)
    monkeypatch.setitem(sys.modules, "easyocr", None)  # 入っていない
    reader, engine, warning = ocr.load_symbol_ocr_reader("easyocr", "en")
    assert reader is None and engine == "none" and "unavailable" in warning
    monkeypatch.setitem(sys.modules, "easyocr", SimpleNamespace(Reader=lambda langs, **_k: ("easy", tuple(langs))))
    assert ocr.load_symbol_ocr_reader("easyocr", "ja") == (("easy", ("ja",)), "easyocr", None)
    with pytest.raises(ValueError, match="unsupported"):
        ocr.load_symbol_ocr_reader("tesseract", "en")


def test_rapidocr_is_loaded_once():
    ocr._rapidocr.cache_clear()
    try:
        first, engine, warning = ocr.load_symbol_ocr_reader("rapidocr", "en")
        assert engine == "rapidocr" and warning is None
        assert ocr.load_symbol_ocr_reader("rapidocr", "en")[0] is first
    finally:
        ocr._rapidocr.cache_clear()  # ほかのテストが「RapidOCR が無い」場合を試せるよう、覚えた reader を捨てる


@pytest.mark.skipif(not REC_MODEL.is_file(), reason="rapidocr の認識モデルが無い")
def test_crop_reader_uses_the_fine_tuned_model_when_it_exists(monkeypatch):
    page_reader = object()
    monkeypatch.setenv("SEQANNO_OCR_REC_MODEL", str(REC_MODEL))
    crop = ocr.load_crop_ocr_reader(page_reader)
    assert crop is not page_reader and type(crop).__name__ == "RapidOCR"
