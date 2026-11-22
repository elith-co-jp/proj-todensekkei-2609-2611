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


def test_lamp_without_detected_leads_gets_two_ports_on_its_line():
    """表示灯もコイルと同じ2端子負荷なので、枠まで届く線を上下の端子として回復する。"""
    mask, wires, lamp = _page()
    lamp["class_name"] = "lamp"

    ports = line_ports(lamp, mask, wires, [lamp])

    assert [p["point"] for p in ports] == [[100.0, 80.0], [100.0, 120.0]]
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


def _connector(cx, cy):
    return {"id": "cn", "class_name": "connector", "center": [cx, cy],
            "bbox": {"x0": cx - 20, "y0": cy - 8, "x1": cx + 20, "y1": cy + 8}}


def test_line_ending_in_a_connector_on_the_line_joins_that_connector():
    """コイルの上の線がコネクタの枠に入り、その中心が線の上にあれば、そのコネクタにつなぐ（ER21315）。
    コネクタの間の短い線は配線として検出されない。"""
    mask, wires, coil = _page()
    connector = _connector(100, 70)
    ports = line_ports(coil, mask, wires, [coil, connector])
    assert [(p["wire_id_hint"], p["symbol_id_hint"]) for p in ports] == [(None, "cn"), ("bottom", None)]


def test_connector_beside_the_line_is_not_joined():
    """線の脇（中心が線から外れた）コネクタの枠に入っても、そのコネクタにはつながない。"""
    mask, wires, coil = _page()
    connector = _connector(115, 70)
    ports = line_ports(coil, mask, wires, [coil, connector])
    assert [(p["wire_id_hint"], p["symbol_id_hint"]) for p in ports] == [(None, None), ("bottom", None)]



def test_contact_ports_from_sparse_dots_or_leads_and_nothing_found():
    """接点の枠の外に線が見えないとき、まばらな点線（DD-0023）か、枠の中の引き出し線で端子を決める。"""
    from PIL import Image

    from analysis.wiring.params import DEFAULT
    from analysis.wiring.ports import (
        TraceHit, _dotted_column, _faint_column, _ink_run, _lead_column, _pass_through_on_line, _trace_to_wire,
        point_ink_ratio,
    )

    mask, wires, _coil = _page()
    mask[40:81, 99:102] = mask[120:161, 99:102] = False
    contact = {"id": "k", "class_name": "contact_a", "bbox": {"x0": 90, "y0": 80, "x1": 150, "y1": 120}}
    faint = mask.copy()
    for y in (45, 55, 66, 125, 140, 150):
        faint[y:y + 2, 100] = True                   # 5〜15 px おきの点
    dotted = line_ports(contact, mask, wires, [contact], faint)
    assert [p["point"][1] for p in dotted] == [80.0, 120.0] and all(abs(p["point"][0] - 100) <= 1 for p in dotted)
    assert line_ports(contact, mask, wires, [contact], mask.copy()) == []
    leads = mask.copy()
    leads[80:92, 100] = leads[108:121, 100] = True     # 枠の中の上下の引き出し線
    ported = line_ports(contact, leads, wires, [contact], mask.copy())
    assert [p["point"][1] for p in ported] == [80.0, 120.0] and all(abs(p["point"][0] - 100) <= 1 for p in ported)

    edge = np.zeros((40, 40), dtype=bool)
    assert _faint_column(edge, 5, 0, 30, 20, DEFAULT.ports, middle_only=False) is None    # 枠の上に余地が無い
    assert _dotted_column(edge, 5, 0, 30, 20, DEFAULT.ports) is None
    assert _lead_column(edge, 5, 10, 30, 10, DEFAULT.ports) is None                       # 高さ 0
    assert _ink_run(np.ones((10, 10), dtype=bool), 8, 5, 1, 0, 20, 2) == 1                # 画像の端で止まる
    assert point_ink_ratio(Image.new("L", (10, 10), 255), (50, 50), 3) == 0.0
    assert _pass_through_on_line({"class_name": "relay_coil"}, (0, 0), (0, 1), DEFAULT.ports) is None
    line = np.zeros((50, 50), dtype=bool)
    line[:, 10] = True
    assert _trace_to_wire(line, (10, 40), (0, 1), [], [], [], 2, DEFAULT.ports) == TraceHit()    # 画像の外へ出た
    short = DEFAULT.ports.__class__(line_trace_max_px=5)
    assert _trace_to_wire(line, (10, 10), (0, 1), [], [], [], 2, short) == TraceHit()           # たどれる長さの上限
