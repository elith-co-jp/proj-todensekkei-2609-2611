"""リレーコイルと表示灯の判別を測る合成図面（DD-0043）。"""

from __future__ import annotations

import numpy as np

from analysis.synthetic_circuits import circles


def test_pages_are_deterministic_and_hold_both_circle_classes():
    a, b = circles.make_page(1, 39), circles.make_page(1, 39)
    assert np.array_equal(np.asarray(a.image), np.asarray(b.image))
    kinds = {s["class_key"] for s in a.symbols if s["class_key"] in circles.CIRCLE_CLASSES}
    assert kinds == {"relay_coil", "lamp"}
    for symbol in a.symbols:
        if symbol["class_key"] in circles.CIRCLE_CLASSES:
            assert circles.STYLES[symbol["style"]][0] == symbol["class_key"]
    # リレー表に載せる器具は、リレー表の手がかりを持つ書き方のコイル（と他の図面の器具）。
    table_coils = {s["label"] for s in a.symbols if s.get("style") in {"relay_table_contact", "relay_table"}}
    assert table_coils <= set(a.relay_table)


def test_swapped_scenario_exchanges_only_relay_coils_and_lamps():
    page = circles.make_page(2, 39)
    correct = circles.detections(page, "correct")
    swapped = circles.detections(page, "swapped")
    for gold, c, s in zip(page.symbols, correct, swapped, strict=True):
        assert c["class_key"] == gold["class_key"]
        assert s["class_key"] == circles.OTHER.get(gold["class_key"], gold["class_key"])
        assert (c["cx"], c["cy"], c["w"], c["h"]) == (s["cx"], s["cy"], s["w"], s["h"])


def test_x_marked_lamps_detected_as_relay_coils_are_corrected_without_ocr(tmp_path):
    report = circles.evaluate(1, tmp_path, scenarios=("swapped",), start=3)
    assert not report["errors"]
    rows = report["decisions"]
    x_lamps = [r for r in rows if r["style"] in {"lamp_x_color", "lamp_x"}]
    assert x_lamps and all(r["final"] == "lamp" and r["status"] == "確定" for r in x_lamps)
    # 文字を読まないと他の手がかりは無いので、残りは種別を変えずに要確認にする（誤って確定しない）。
    assert report["scenarios"]["swapped"]["confirmed_wrong"] == 0
    assert all(r["status"] == "要確認" for r in rows if r["final"] != r["gold"])
