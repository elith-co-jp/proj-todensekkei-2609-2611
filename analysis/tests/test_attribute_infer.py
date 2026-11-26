"""図面の文字から属性（タイマー・キープリレー・信号名・外部入力）を推定する処理（DD-0050）を確かめる。

文字の書き方は紙芝居 EV31031〜EV31036 を 150 dpi で読んだ OCR に合わせた（1sec を「Isec」、TDPU を「TOPU」と読む）。
"""

from __future__ import annotations

from analysis import attribute_infer as ai
from analysis.synthetic_circuits import sheet_text
from analysis.tools import sheet_text_eval as ev


def item(text, x0, y0, x1, y1):
    return {"text": text, "score": 0.9, "box": (x0, y0, x1, y1)}


def sym(sid, cls, x0, y0, x1, y1, label=""):
    return {"id": sid, "class_name": cls, "bbox": {"x0": x0, "y0": y0, "x1": x1, "y1": y1}, "label": label}


def attrs(result, sid):
    return (result["symbols"].get(sid) or {}).get("attributes") or {}


def test_read_seconds_takes_ocr_confusions_and_skips_the_range():
    assert ai.read_seconds("1sec") == 1.0
    assert ai.read_seconds("Isec") == 1.0
    assert ai.read_seconds("0.5 S") == 0.5
    assert ai.read_seconds("2秒") == 2.0
    assert ai.read_seconds("(0-12sec)") is None
    assert ai.read_seconds("3B8-8F-1") is None


def test_read_timer_mode():
    assert ai.read_timer_mode("TDPU") == "on_delay"
    assert ai.read_timer_mode("TOPU") == "on_delay"
    assert ai.read_timer_mode("TDDO") == "off_delay"
    assert ai.read_timer_mode("52X") is None


def test_timer_coil_gets_seconds_on_its_right_and_mode_below():
    coils = [sym("t", "relay_coil", 400, 300, 430, 330, "2T P33P22"), sym("n", "relay_coil", 500, 300, 530, 330, "3X")]
    items = [item("Isec", 434, 305, 462, 318), item("(0-12sec)", 434, 318, 480, 330), item("TOPU", 398, 360, 432, 372)]
    result = ai.infer_attributes(items, coils)
    assert attrs(result, "t") == {"timer_mode": "on_delay", "delay_s": 1.0}
    # 右のコイルは、左のコイルの右に書いた秒を取らない
    assert attrs(result, "n") == {}


def test_timer_without_mode_defaults_to_on_delay_for_review():
    result = ai.infer_attributes([item("5sec", 434, 305, 462, 318)], [sym("t", "relay_coil", 400, 300, 430, 330)])
    assert attrs(result, "t") == {"timer_mode": "on_delay", "delay_s": 5.0}
    assert result["symbols"]["t"]["review"]


def test_latch_pair_with_one_readable_mark_gets_the_opposite_role_and_the_label():
    coils = [sym("o", "relay_coil", 400, 300, 430, 330, "43LK P33P22"), sym("r", "relay_coil", 480, 300, 510, 330)]
    result = ai.infer_attributes([], coils, crop_items={"o": [item("O", 410, 308, 420, 322)]})
    assert attrs(result, "o") == {"latch_coil": "set"}
    assert attrs(result, "r") == {"latch_coil": "reset"}
    assert result["symbols"]["r"]["review"]
    assert result["labels"]["r"]["label"] == "43LK P33P22"


def test_a_lone_coil_with_a_mark_is_not_a_latch():
    result = ai.infer_attributes([], [sym("o", "relay_coil", 400, 300, 430, 330)],
                                 crop_items={"o": [item("R", 410, 308, 420, 322)]})
    assert attrs(result, "o") == {}


def test_caption_goes_to_the_io_contact_in_its_column_else_the_coil():
    symbols = [
        sym("bus", "power_bus", 100, 200, 900, 206),
        sym("in", "contact_a", 300, 260, 312, 290, "D001 B405"),
        sym("c1", "relay_coil", 296, 500, 326, 530, "3X"),
        sym("c2", "relay_coil", 596, 500, 626, 530, "4X"),
    ]
    texts = [
        {"text": "起動指令", "box": [296, 80, 312, 180]},
        {"text": "（個別制御装置）", "box": [314, 80, 330, 190]},
        {"text": "運転表示", "box": [604, 80, 620, 180]},
        {"text": "注記", "box": [700, 100, 760, 116]},  # 横書きは名前にしない
    ]
    result = ai.infer_attributes([], symbols, texts=texts, page_height=1241)
    assert attrs(result, "in") == {"external_io": "yes", "signal_name": "起動指令(個別制御装置)"}
    assert attrs(result, "c2") == {"signal_name": "運転表示"}
    assert attrs(result, "c1") == {}
    assert result["captions"] == []


def test_synthetic_sheets_without_noise_keep_the_measured_accuracy():
    """合成の図面（ノイズなし）で、ラベルと属性の当たり率が DD-0050 の測定から下がらない。"""
    result = ev.evaluate(range(50000, 50020), sheet_text.SheetConfig(noise=0.0))
    assert result["label"]["accuracy"] >= 0.98
    for name in ev.ATTRIBUTES:
        assert result[name]["precision"] >= 0.95, name
        assert result[name]["recall"] >= 0.95, name


def test_synthetic_sheet_layout_does_not_depend_on_noise():
    a = sheet_text.make_sheet(7, sheet_text.SheetConfig(noise=0.0))
    b = sheet_text.make_sheet(7, sheet_text.SheetConfig(noise=1.0))
    assert [s["gold_label"] for s in a["symbols"]] == [s["gold_label"] for s in b["symbols"]]
    assert [s["bbox"] for s in a["symbols"]] == [s["bbox"] for s in b["symbols"]]
