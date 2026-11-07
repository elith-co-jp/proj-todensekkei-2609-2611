"""OCR の追加学習の正解の付け方と、拡大読みだけに追加学習したモデルを使う切り替えを確かめる（#50）。"""

from __future__ import annotations

# ruff: noqa: I001

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

from ocr_finetune_data import snap_token, target_for
from run_yolo_e2e_pipeline import load_crop_ocr_reader


def test_a_misread_glyph_is_corrected_to_the_printed_word():
    assert snap_token("T83", {"TB3", "CN11", "30"}) == "TB3"
    assert snap_token("830-5", {"83D-5", "ESD"}) == "83D-5"
    assert snap_token("CN215本", {"CN215", "TB13"}) == "CN215"
    assert snap_token("143E", {"43E", "AMESD"}) == "43E"


def test_case_and_look_alike_glyphs_stay_as_read():
    assert snap_token("33hb", {"33HB"}) == "33hb"
    assert snap_token("430", {"43O", "ARM"}) == "430"


def test_numbers_and_cut_off_ends_are_not_guessed():
    assert snap_token("186", {"136", "TB6", "CN30"}) is None
    assert snap_token("13", {"43", "TB13"}) is None
    assert snap_token("CN21", {"CN214"}) is None
    assert snap_token("(ER21277)", {"(ER21272)"}) is None


def test_a_line_keeps_its_separators_and_needs_every_word_matched():
    assert target_for("T813 39", {"TB13", "39", "CN212"}) == "TB13 39"
    assert target_for("T813 X99", {"TB13", "39"}) is None


def test_crop_reads_use_the_page_reader_unless_a_fine_tuned_model_exists(monkeypatch, tmp_path):
    page_reader = object()
    monkeypatch.delenv("SEQANNO_OCR_REC_MODEL", raising=False)
    assert load_crop_ocr_reader(page_reader) is page_reader
    monkeypatch.setenv("SEQANNO_OCR_REC_MODEL", str(tmp_path / "missing.onnx"))
    assert load_crop_ocr_reader(page_reader) is page_reader
