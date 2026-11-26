"""印だけの枠の学習データ作り（今の枠を分ける）と、近くの文字からのラベルの当たり率の計算（DD-0049）。"""

from __future__ import annotations

# ruff: noqa: I001

import io
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

from label_link_data import device_key, evaluate_project, review_image, split_project, summarize

from analysis.tests.test_label_link import _drawing

W, H = 200, 120


def project():
    """今の取り決め（印と名前と端子番号を囲む）の接点 1 個と、名前の無い分岐点 1 個。"""
    x0, y0, x1, y1 = 42, 28, 112, 72
    return {
        "id": 1, "name": "EV31036", "image_width": W, "image_height": H,
        "symbols": [
            {"ref": "SYM-0001", "class_key": "contact_a", "label": "83DX-1 P33P22",
             "cx": (x0 + x1) / 2 / W, "cy": (y0 + y1) / 2 / H, "w": (x1 - x0) / W, "h": (y1 - y0) / H,
             "terminals": [{"ref": "SYM-0001-T1", "name": "1", "tx": 60 / W, "ty": 37 / H},
                           {"ref": "SYM-0001-T2", "name": "2", "tx": 60 / W, "ty": 63 / H}]},
            {"ref": "SYM-0002", "class_key": "junction", "label": None, "cx": 0.9, "cy": 0.9, "w": 0.02, "h": 0.02,
             "terminals": []},
        ],
        "connections": [],
    }


def test_split_makes_mark_boxes_and_label_boxes():
    gray, texts = _drawing()
    p = project()
    assert split_project(p, gray, texts) == {"split": 1}
    contact = p["symbols"][0]
    assert contact["w"] * W < 20  # 印（白丸と棒）だけの幅
    assert abs(contact["label_box"]["cx"] * W - 89.5) < 1e-6
    assert "label_box" not in p["symbols"][1]
    # 2 回目は分けない（label_box が付いている）
    assert split_project(p, gray, texts) == {"already": 1}


def test_eval_compares_the_linked_label_with_the_gold_label():
    gray, texts = _drawing()
    p = project()
    baseline = {"SYM-0001": "83DX-1 P33P22"}
    split_project(p, gray, texts)
    rows = evaluate_project(p, texts, baseline)
    assert [(r["ref"], r["pred"], r["label_ok"], r["box_ok"], r["baseline_ok"]) for r in rows] == [
        ("SYM-0001", "83DX-1 P33P22", True, True, True)]
    summary = summarize(rows)
    assert summary["label_accuracy"] == summary["box_accuracy"] == summary["baseline_label_accuracy"] == 1.0
    assert summary["status"] == {"linked": 1}


def test_device_key_ignores_wire_numbers_and_confusable_letters():
    assert device_key("128 83DX-1 P33P22") == device_key("83DX-I")
    assert device_key("13") == ""


def test_review_image_is_a_png():
    from PIL import Image

    gray, texts = _drawing()
    p = project()
    split_project(p, gray, texts)
    buf = io.BytesIO()
    Image.fromarray(gray).save(buf, format="PNG")
    assert review_image(buf.getvalue(), p).startswith(b"\x89PNG")
