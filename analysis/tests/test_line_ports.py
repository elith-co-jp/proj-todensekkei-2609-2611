"""接点・コイルの端子が足りないとき、画像の線から 2 端子を決めて配線につなぐ（DD-0009 §8）。"""

from __future__ import annotations

# ruff: noqa: I001

import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

from analysis.wiring.ports import line_ports


def _wire(wid, orientation, x0, y0, x1, y1):
    return {"id": wid, "orientation": orientation, "bbox": {"x0": x0, "y0": y0, "x1": x1, "y1": y1}}


def _page():
    """上の母線（y=40）から縦線が下り、y=60 で黒丸の無い交差を通り、コイル（80〜120）を経て下の母線（y=160）へ。"""
    mask = np.zeros((200, 200), dtype=bool)
    mask[39:42, 20:181] = True      # 上の母線
    mask[159:162, 20:181] = True    # 下の母線
    mask[59:62, 20:181] = True      # 交差するだけの横線
    mask[40:81, 99:102] = True      # コイルの上の縦線（検出されていない）
    mask[120:161, 99:102] = True    # コイルの下の縦線（検出されていない）
    wires = [_wire("top", "h", 20, 39, 180, 41), _wire("bottom", "h", 20, 159, 180, 161),
             _wire("cross", "h", 20, 59, 180, 61)]
    coil = {"id": "c1", "class_name": "relay_coil", "bbox": {"x0": 80, "y0": 80, "x1": 120, "y1": 120}}
    return mask, wires, coil


def test_coil_without_detected_wires_gets_two_ports_on_its_line():
    mask, wires, coil = _page()
    ports = line_ports(coil, mask, wires, [coil])
    assert [p["point"] for p in ports] == [[100.0, 80.0], [100.0, 120.0]]
    # 上は黒丸の無い交差（y=60）を通り過ぎて母線につなぐ。下は下の母線。
    assert [p["wire_id_hint"] for p in ports] == ["top", "bottom"]


def test_no_ports_when_no_line_leaves_the_box():
    mask, wires, coil = _page()
    mask[40:81, 99:102] = False
    assert line_ports(coil, mask, wires, [coil]) == []


def test_line_through_a_dot_joins_the_wire_at_the_dot():
    """y=60 の横線との交点に黒丸（分岐点）があれば、縦線はそこで横線につながる。"""
    mask, wires, coil = _page()
    dot = {"id": "j1", "class_name": "junction", "bbox": {"x0": 95, "y0": 55, "x1": 105, "y1": 65}}
    ports = line_ports(coil, mask, wires, [coil, dot])
    assert [p["wire_id_hint"] for p in ports] == ["cross", "bottom"]


def test_faint_dotted_line_gives_a_coil_its_ports():
    """二値化で消える薄い点線（ER21256）でも、灰色の濃さで見ればコイルの上下の線が分かる。"""
    mask, wires, coil = _page()
    mask[40:81, 99:102] = False
    mask[120:161, 99:102] = False
    mask[39:42, 20:181] = mask[59:62, 20:181] = mask[159:162, 20:181] = True   # 横線は残す
    faint = mask.copy()
    for y in list(range(42, 80, 3)) + list(range(121, 159, 3)):
        faint[y:y + 2, 100] = True          # 2 px 描いて 1 px 空く点線
    faint[70:74, 85:88] = True               # 線の脇の文字の一画
    assert line_ports(coil, mask, wires, [coil]) == []
    ports = line_ports(coil, mask, wires, [coil], faint)
    assert [p["point"] for p in ports] == [[100.0, 80.0], [100.0, 120.0]]
    assert [p["wire_id_hint"] for p in ports] == ["top", "bottom"]

