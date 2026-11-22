"""解析の途中で文字の読み取りが失敗しても、検出できた配線を返し、何が失敗したかを警告に出す。"""

from __future__ import annotations

import pytest
from PIL import Image, ImageDraw

from tests.test_wire_decorations import ruled_label


@pytest.fixture()
def runtime():
    pytest.importorskip("cv2")
    from analysis import runtime

    return runtime


def _page(tmp_path):
    image = Image.new("RGB", (1200, 800), "white")
    draw = ImageDraw.Draw(image)
    draw.rectangle((40, 40, 1160, 760), outline="black", width=3)
    draw.line([(150, 250), (1000, 250)], fill="black", width=3)
    draw.line([(500, 150), (500, 650)], fill="black", width=3)
    path = tmp_path / "page.png"
    image.save(path)
    return path


def test_ocr_crash_keeps_wires_and_warns(tmp_path, monkeypatch, runtime):
    def crash(*_args, **_kwargs):
        raise RuntimeError("onnxruntime died")

    monkeypatch.setattr(runtime, "load_symbol_ocr_reader", crash)
    result = runtime.analyze_detections(_page(tmp_path), [], source={})
    assert result["wires"]
    assert "ラベル読み取りに失敗しました。シンボル・配線の結果は確認できます。" in result["warnings"]


def test_failed_label_rule_check_is_reported(tmp_path, monkeypatch, runtime):
    image, _payload = ruled_label()
    path = tmp_path / "card.png"
    image.save(path)

    def page_reader(_array, **_kwargs):
        return [], None

    def confirm_fails(_reader):
        def confirm(_crop):
            raise RuntimeError("OCR unavailable")
        return confirm

    monkeypatch.setattr(runtime, "load_symbol_ocr_reader", lambda *_: (page_reader, "rapidocr", None))
    monkeypatch.setattr(runtime, "rapidocr_text_confirmation", confirm_fails)
    card = {"class_key": "other", "class_label": "Other", "cx": 0.215, "cy": 0.3, "w": 0.27, "h": 0.4, "confidence": 0.9}
    result = runtime.analyze_detections(path, [card], source={})
    assert result["quality"]["wire_decoration_filter"]["label_rule_ocr_failed"]
    assert "罫線の文字確認に失敗したため、判定できない罫線は配線候補として残しています。" in result["warnings"]
