"""記号の端子の位置を決める（配線との交わり・画像の線）。"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from typing import Any, NamedTuple

import numpy as np
from PIL import Image

from analysis.wiring import classes
from analysis.wiring.geometry import clamp, wire_axis_and_range
from analysis.wiring.params import DEFAULT, PortParams

# 中心に 1 つの端子を置く記号。
EXPLICIT_NODE_CLASSES = classes.PASS_THROUGH


# Classes whose terminals sit where a wire stops inside the box (the + and - dots of a card),
# not at fixed fractions of the box. Empty disables these ports.
WIRE_END_PORT_CLASSES: frozenset[str] = frozenset({"other"})


# A two-port contact sits on a wire that runs through its box; a wire passing just beside the box
# (axis_padding_px allows it) belongs to the next contact when the box also covers the label.
CONTACT_PREFERS_WIRES_INSIDE_BOX = True


# Card input contacts are two open circles with a gap: the template points fall in the blank gap and
# fail the ink check. Such a contact takes its ports at the box edges instead, only where a wire
# reaches the edge; a point off the wire would attach to whatever node lies within reach.
CONTACT_EDGE_FALLBACK = True


# A contact boxed together with its label can also cover the return wire drawn just beside the label
# (ER21282: a box drawn around each contact doubles as its wiring). A two-port contact sits on one line,
# so when ports land on parallel lines, keep the line deepest inside the box.
CONTACT_KEEPS_INNERMOST_LINE = True


# 枠をかすめる線の端子を除く記号。コネクタは枠に番号の文字を含み、列の縦線は枠の中を通る。カードも列の線は枠の
# 中にある。黒丸・接点の枠は線にぴったり沿うので、辺の上の端子が普通（対象外）。
GRAZING_PORT_CLASSES: frozenset[str] = frozenset({"connector"})
GRAZING_PORT_OTHER_KINDS: frozenset[str] = frozenset({"card"})


def grazes_box_edge(symbol: dict[str, Any], point: tuple[float, float], orientation: str | None, edge_px: float) -> bool:
    """端子の線が枠の辺に沿って走り、枠を横切らない（辺から edge_px 以内で辺と平行、かつ直交する辺からは離れている）。"""
    if edge_px <= 0 or orientation not in {"h", "v"}:
        return False
    if symbol.get("class_name") not in GRAZING_PORT_CLASSES and symbol.get("other_kind") not in GRAZING_PORT_OTHER_KINDS:
        return False
    box = symbol["bbox"]
    x, y = point
    near_x = min(abs(x - box["x0"]), abs(x - box["x1"])) <= edge_px
    near_y = min(abs(y - box["y0"]), abs(y - box["y1"])) <= edge_px
    return (near_y and not near_x) if orientation == "h" else (near_x and not near_y)


def point_ink_ratio(image: Image.Image, point: tuple[float, float], radius: int) -> float:
    gray = image.convert("L")
    x, y = int(round(point[0])), int(round(point[1]))
    x0 = max(0, x - radius)
    y0 = max(0, y - radius)
    x1 = min(gray.width, x + radius + 1)
    y1 = min(gray.height, y + radius + 1)
    if x0 >= x1 or y0 >= y1:
        return 0.0
    crop = gray.crop((x0, y0, x1, y1))
    histogram = crop.histogram()
    dark_pixels = sum(histogram[:180])
    return dark_pixels / max(1, crop.width * crop.height)


def expected_ratios(class_name: str, box: dict[str, int | float], four_port_height_px: float) -> list[float]:
    height = float(box["y1"]) - float(box["y0"])
    if class_name in {"contact_a", "contact_b"} and height >= four_port_height_px:
        return [0.2, 0.4, 0.6, 0.8]
    if class_name in {"contact_a", "contact_b"}:
        return [0.32, 0.68]
    if class_name in {"solenoid", "relay_coil"}:
        return [0.15, 0.85]
    return [0.32, 0.68]


def is_four_port_symbol(class_name: str, box: dict[str, int | float], four_port_height_px: float) -> bool:
    height = float(box["y1"]) - float(box["y0"])
    return class_name in {"contact_a", "contact_b"} and height >= four_port_height_px


def candidate_key(candidate: dict[str, Any], grid: float = 3.0) -> tuple[int, int]:
    return (
        round(float(candidate["point"][0]) / grid),
        round(float(candidate["point"][1]) / grid),
    )


def infer_ports_for_symbol(
    symbol: dict[str, Any],
    wires: list[dict[str, Any]],
    source_image: Image.Image,
    *,
    config: dict[str, Any],
    params: PortParams = DEFAULT.ports,
) -> list[dict[str, Any]]:
    box = symbol["bbox"]
    x0, y0, x1, y1 = map(float, (box["x0"], box["y0"], box["x1"], box["y1"]))
    width = max(1.0, x1 - x0)
    height = max(1.0, y1 - y0)
    class_name = str(symbol.get("class_name") or symbol.get("type") or "")
    margin_px = float(config["margin_px"])
    axis_padding_px = float(config["axis_padding_px"])
    min_wire_confidence = float(config["min_wire_confidence"])
    min_visible_coverage = float(config["min_visible_coverage"])
    ink_radius = int(config["ink_radius"])
    min_ink_ratio = float(config["min_ink_ratio"])
    mode = str(config["mode"])
    ratios = expected_ratios(class_name, box, float(config["four_port_height_px"]))
    four_port = is_four_port_symbol(class_name, box, float(config["four_port_height_px"]))
    require_point_on_wire_span = bool(config.get("require_point_on_wire_span", True))
    two_port_contact_orientation = str(config.get("two_port_contact_orientation", "all"))
    contact_outer_terminal_margin_px = float(config.get("contact_outer_terminal_margin_px", 0.0))
    contact_max_wire_axes = int(config.get("contact_max_wire_axes", 0) or 0)
    contact_min_terminal_rel_x = config.get("contact_min_terminal_rel_x")
    contact_max_terminal_rel_x = config.get("contact_max_terminal_rel_x")
    wire_end_ports = class_name in config.get("wire_end_port_classes", WIRE_END_PORT_CLASSES)

    candidates: list[dict[str, Any]] = []
    for wire in wires:
        if float(wire.get("confidence", 0.0)) < min_wire_confidence:
            continue
        if float(wire.get("visible_coverage") or 0.0) < min_visible_coverage:
            continue
        orientation, axis, span_start, span_end = wire_axis_and_range(wire)
        if class_name in {"solenoid", "relay_coil"} and orientation != "v":
            continue
        if class_name in {"contact_a", "contact_b"} and not four_port and two_port_contact_orientation == "vertical" and orientation != "v":
            continue

        if orientation == "v":
            if not (x0 - axis_padding_px <= axis <= x1 + axis_padding_px):
                continue
            overlap = min(span_end, y1 + margin_px) - max(span_start, y0 - margin_px)
            if overlap <= 0:
                continue
            inner_ends = [(clamp(axis, x0, x1), end) for end in (span_start, span_end) if y0 + margin_px <= end <= y1 - margin_px]
            if mode == "bbox_edge":
                points = [(clamp(axis, x0, x1), y0), (clamp(axis, x0, x1), y1)]
                if config.get("edge_on_span"):
                    points = [p for p in points if span_start - margin_px <= p[1] <= span_end + margin_px]
            elif class_name in {"contact_a", "contact_b"} and contact_outer_terminal_margin_px > 0 and not four_port:
                y_values = [y0 - contact_outer_terminal_margin_px, y0 + 0.32 * height, y0 + 0.68 * height, y1 + contact_outer_terminal_margin_px]
                points = [
                    (clamp(axis, x0, x1), y)
                    for y in y_values
                    if not require_point_on_wire_span or span_start - margin_px <= y <= span_end + margin_px
                ]
            else:
                points = [
                    (clamp(axis, x0, x1), y0 + ratio * height)
                    for ratio in ratios
                    if not require_point_on_wire_span or span_start - margin_px <= y0 + ratio * height <= span_end + margin_px
                ]
            side = "vertical_axis"
        else:
            if not (y0 - axis_padding_px <= axis <= y1 + axis_padding_px):
                continue
            overlap = min(span_end, x1 + margin_px) - max(span_start, x0 - margin_px)
            if overlap <= 0:
                continue
            inner_ends = [(end, clamp(axis, y0, y1)) for end in (span_start, span_end) if x0 + margin_px <= end <= x1 - margin_px]
            if mode == "bbox_edge":
                points = [(x0, clamp(axis, y0, y1)), (x1, clamp(axis, y0, y1))]
                if config.get("edge_on_span"):
                    points = [p for p in points if span_start - margin_px <= p[0] <= span_end + margin_px]
            elif class_name in {"contact_a", "contact_b"} and contact_outer_terminal_margin_px > 0 and not four_port:
                x_values = [x0 - contact_outer_terminal_margin_px, x0 + 0.32 * width, x0 + 0.68 * width, x1 + contact_outer_terminal_margin_px]
                points = [
                    (x, clamp(axis, y0, y1))
                    for x in x_values
                    if not require_point_on_wire_span or span_start - margin_px <= x <= span_end + margin_px
                ]
            else:
                points = [
                    (x0 + ratio * width, clamp(axis, y0, y1))
                    for ratio in ratios
                    if not require_point_on_wire_span or span_start - margin_px <= x0 + ratio * width <= span_end + margin_px
                ]
            side = "horizontal_axis"
        if wire_end_ports:
            points = [*points, *inner_ends]

        for point in points:
            ink_ratio = point_ink_ratio(source_image, point, ink_radius)
            if ink_ratio < min_ink_ratio:
                continue
            candidates.append(
                {
                    "symbol_id": symbol.get("id"),
                    "gold_ref": symbol.get("gold_ref"),
                    "class_name": class_name,
                    "label": symbol.get("label") or "",
                    "point": [round(point[0], 1), round(point[1], 1)],
                    "wire_id": wire.get("id"),
                    "wire_orientation": orientation,
                    "wire_axis": axis,
                    "wire_confidence": wire.get("confidence"),
                    "wire_visible_coverage": wire.get("visible_coverage"),
                    "reason": f"{mode}:{side}",
                    "ink_ratio": round(ink_ratio, 4),
                    "wire_inside_box": (x0 <= axis <= x1) if orientation == "v" else (y0 <= axis <= y1),
                }
            )

    if CONTACT_PREFERS_WIRES_INSIDE_BOX and class_name in {"contact_a", "contact_b"} and not four_port:
        inside = [candidate for candidate in candidates if candidate["wire_inside_box"]]
        candidates = inside or candidates

    if class_name == "junction" and candidates:
        # 分岐点は横線 1 本と縦線 1 本の上にしか乗らない。枠に触れる隣の平行な線（約 11 px 間隔）は別の配線
        # なので、向きごとに中心に最も近い線だけにする（DD-0009 §8）。
        centre = {"v": (x0 + x1) / 2, "h": (y0 + y1) / 2}
        nearest = {o: min((abs(c["wire_axis"] - centre[o]) for c in candidates if c["wire_orientation"] == o), default=None)
                   for o in ("v", "h")}
        candidates = [c for c in candidates
                      if abs(c["wire_axis"] - centre[c["wire_orientation"]]) <= nearest[c["wire_orientation"]] + params.junction_axis_tolerance_px]

    if CONTACT_KEEPS_INNERMOST_LINE and class_name in {"contact_a", "contact_b"} and not four_port and candidates:
        def cross(candidate: dict[str, Any]) -> float:
            return float(candidate["point"][0] if candidate["wire_orientation"] == "v" else candidate["point"][1])

        def depth(candidate: dict[str, Any]) -> float:
            lo, hi = (x0, x1) if candidate["wire_orientation"] == "v" else (y0, y1)
            return min(cross(candidate) - lo, hi - cross(candidate))

        deepest = max(candidates, key=depth)
        # 2 端子の接点は 1 本の線の上にある。枠の辺をかすめる別の向きの線（ラベルの下線・隣の横線）の端子は捨てる。
        candidates = [c for c in candidates
                      if c["wire_orientation"] == deepest["wire_orientation"] and abs(cross(c) - cross(deepest)) <= 3]

    if class_name in {"contact_a", "contact_b"} and (
        contact_min_terminal_rel_x is not None or contact_max_terminal_rel_x is not None
    ):
        filtered_candidates = []
        for candidate in candidates:
            rel_x = (float(candidate["point"][0]) - x0) / width
            if contact_min_terminal_rel_x is not None and rel_x < float(contact_min_terminal_rel_x):
                continue
            if contact_max_terminal_rel_x is not None and rel_x > float(contact_max_terminal_rel_x):
                continue
            filtered_candidates.append(candidate)
        candidates = filtered_candidates

    if class_name in {"contact_a", "contact_b"} and not four_port and contact_max_wire_axes > 0:
        by_wire: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for candidate in candidates:
            by_wire[str(candidate.get("wire_id") or "")].append(candidate)
        ranked_wires = sorted(
            by_wire.items(),
            key=lambda item: (
                -sum(float(candidate.get("ink_ratio") or 0.0) for candidate in item[1]),
                -max(float(candidate.get("wire_visible_coverage") or 0.0) for candidate in item[1]),
                -len(item[1]),
                item[0],
            ),
        )
        allowed_wire_ids = {wire_id for wire_id, _items in ranked_wires[:contact_max_wire_axes]}
        candidates = [candidate for candidate in candidates if str(candidate.get("wire_id") or "") in allowed_wire_ids]

    deduped: dict[tuple[int, int], dict[str, Any]] = {}
    for candidate in candidates:
        key = candidate_key(candidate)
        previous = deduped.get(key)
        if previous is None or float(candidate["ink_ratio"]) > float(previous["ink_ratio"]):
            deduped[key] = candidate
    if symbol.get("other_kind") == "card":
        for mirrored in card_ports_across(deduped.values(), wires, (x0, y0, x1, y1), margin_px):
            deduped.setdefault(candidate_key(mirrored), mirrored)
    if CONTACT_EDGE_FALLBACK and mode == "template" and class_name in {"contact_a", "contact_b"} and not four_port and len(deduped) < 2:
        return infer_ports_for_symbol(symbol, wires, source_image, config={**config, "mode": "bbox_edge", "edge_on_span": True}, params=params)
    return sorted(deduped.values(), key=lambda item: (str(item["wire_id"]), item["point"][1], item["point"][0]))


def card_ports_across(
    candidates: Iterable[dict[str, Any]], wires: list[dict[str, Any]], box: tuple[float, float, float, float], margin_px: float,
) -> list[dict[str, Any]]:
    """カードを端から端まで貫く線に端子が 1 個しか見つからなければ、枠の中心で折り返した位置にもう 1 個置く。
    カードの入力は + と - の 2 端子で、1 個だけだと線がカードを素通りし、+ 側が下のコネクタにつながる（ER21303、ER21326）。"""
    x0, y0, x1, y1 = box
    by_wire: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for candidate in candidates:
        by_wire[str(candidate.get("wire_id") or "")].append(candidate)
    spans = {str(wire.get("id") or ""): wire_axis_and_range(wire) for wire in wires}
    added = []
    for wire_id, found in by_wire.items():
        if len(found) != 1 or wire_id not in spans:
            continue
        orientation, _axis, span_start, span_end = spans[wire_id]
        lo, hi = (y0, y1) if orientation == "v" else (x0, x1)
        if not (span_start <= lo + margin_px and span_end >= hi - margin_px):
            continue
        (px, py), along = found[0]["point"], 1 if orientation == "v" else 0
        point = [px, round(y0 + y1 - py, 1)] if along else [round(x0 + x1 - px, 1), py]
        added.append({**found[0], "point": point, "reason": "card_mirror", "ink_ratio": 0.0})
    return added


def default_configs() -> list[dict[str, Any]]:
    base = {
        "margin_px": 8,
        "axis_padding_px": 14,
        "min_wire_confidence": 0.0,
        "min_visible_coverage": 0.0,
        "ink_radius": 4,
        "min_ink_ratio": 0.0,
        "four_port_height_px": 65,
        "require_point_on_wire_span": True,
        "two_port_contact_orientation": "all",
    }
    return [
        {**base, "name": "bbox_edge_loose", "mode": "bbox_edge"},
        {**base, "name": "template_loose", "mode": "template"},
        {**base, "name": "template_confident", "mode": "template", "min_wire_confidence": 0.7, "min_visible_coverage": 0.75},
        {**base, "name": "template_ink02", "mode": "template", "min_ink_ratio": 0.02},
        {**base, "name": "template_confident_ink02", "mode": "template", "min_wire_confidence": 0.7, "min_visible_coverage": 0.75, "min_ink_ratio": 0.02},
        {**base, "name": "template_confident_relaxed_span_ink02", "mode": "template", "min_wire_confidence": 0.7, "min_visible_coverage": 0.75, "min_ink_ratio": 0.02, "require_point_on_wire_span": False},
        {**base, "name": "template_confident_v2_vertical_ink02", "mode": "template", "min_wire_confidence": 0.7, "min_visible_coverage": 0.75, "min_ink_ratio": 0.02, "two_port_contact_orientation": "vertical"},
        {**base, "name": "template_confident_v2_vertical_relaxed_span_ink02", "mode": "template", "min_wire_confidence": 0.7, "min_visible_coverage": 0.75, "min_ink_ratio": 0.02, "two_port_contact_orientation": "vertical", "require_point_on_wire_span": False},
        {**base, "name": "template_confident_relaxed_span_ink02_contact_left65", "mode": "template", "min_wire_confidence": 0.7, "min_visible_coverage": 0.75, "min_ink_ratio": 0.02, "require_point_on_wire_span": False, "contact_max_terminal_rel_x": 0.65},
        {**base, "name": "template_confident_v2_vertical_relaxed_span_ink02_contact_two_port", "mode": "template", "min_wire_confidence": 0.7, "min_visible_coverage": 0.75, "min_ink_ratio": 0.02, "two_port_contact_orientation": "vertical", "require_point_on_wire_span": False, "four_port_height_px": 9999},
        {**base, "name": "template_confident_v2_vertical_relaxed_span_ink02_contact_one_axis", "mode": "template", "min_wire_confidence": 0.7, "min_visible_coverage": 0.75, "min_ink_ratio": 0.02, "two_port_contact_orientation": "vertical", "require_point_on_wire_span": False, "four_port_height_px": 9999, "contact_max_wire_axes": 1},
        {**base, "name": "template_ink", "mode": "template", "min_ink_ratio": 0.05},
        {**base, "name": "template_confident_ink", "mode": "template", "min_wire_confidence": 0.7, "min_visible_coverage": 0.75, "min_ink_ratio": 0.05},
        {**base, "name": "template_confident_relaxed_span_contact_outer8_ink02", "mode": "template", "min_wire_confidence": 0.7, "min_visible_coverage": 0.75, "min_ink_ratio": 0.02, "require_point_on_wire_span": False, "contact_outer_terminal_margin_px": 8},
        {**base, "name": "template_confident_relaxed_span_contact_outer12_ink02", "mode": "template", "min_wire_confidence": 0.7, "min_visible_coverage": 0.75, "min_ink_ratio": 0.02, "require_point_on_wire_span": False, "contact_outer_terminal_margin_px": 12},
    ]



# 行き先の札（「ER21276へ」）は、母線がその札の中で終わる点を 1 つの端子にする（DD-0016）。枠の外にこれ以内で
# 終わる配線も札に着いたとみなす。
DESTINATION_REACH_PX = 12.0


# 検出した配線が札に届かないとき、札の左右の辺を外から横切る灰色の線を探す（DD-0022）。辺の外の
# DESTINATION_FAINT_OUTSIDE_PX のうち DESTINATION_FAINT_MIN_SHARE 以上が灰色（GREY_MAX 以下）の行で、
# 札の中へ途切れ（DESTINATION_FAINT_GAP_PX まで）を許してたどった線の終わり（矢印の先）を端子にする。
DESTINATION_FAINT_OUTSIDE_PX = 30
DESTINATION_FAINT_MIN_SHARE = 0.8
DESTINATION_FAINT_GAP_PX = 6
DESTINATION_FAINT_GREY_MAX = 245


def _faint_destination_end(gray: Any, box: dict[str, Any]) -> tuple[float, float] | None:
    """札の左右の辺を外から横切って中へ入る灰色の横線の、札の中での終わり。無ければ None。"""
    height, width = gray.shape
    x0, y0, x1, y1 = (int(round(float(box[k]))) for k in ("x0", "y0", "x1", "y1"))
    out = DESTINATION_FAINT_OUTSIDE_PX
    best: tuple[float, int, tuple[float, float]] | None = None
    for y in range(max(0, y0), min(height, y1 + 1)):
        row = gray[y] <= DESTINATION_FAINT_GREY_MAX
        for edge, step in ((x0, 1), (x1, -1)):
            outside = row[max(0, edge - out):edge] if step == 1 else row[edge + 1:min(width, edge + 1 + out)]
            if outside.size < out or outside.mean() < DESTINATION_FAINT_MIN_SHARE:
                continue
            end, gap, x = edge, 0, edge
            while (x0 <= x <= x1) and gap <= DESTINATION_FAINT_GAP_PX:
                if row[x]:
                    end, gap = x, 0
                else:
                    gap += 1
                x += step
            score = (float(outside.mean()), abs(end - edge), (float(end), float(y)))
            if best is None or score[:2] > best[:2]:
                best = score
    return best[2] if best else None


def destination_port(symbol: dict[str, Any], wires: list[dict[str, Any]], source_image: Image.Image | None = None) -> list[dict[str, Any]]:
    """行き先の札の端子。札の枠（DESTINATION_REACH_PX 広げた範囲）の中で終わる配線の端のうち、枠の中心に最も近いもの。
    テンプレートの位置で端子を置くと、母線の上の 2 点になって札に着く端が分からなかった（ER21275 など 9 枚）。"""
    box = symbol["bbox"]
    reach = DESTINATION_REACH_PX
    cx, cy = (box["x0"] + box["x1"]) / 2, (box["y0"] + box["y1"]) / 2
    ends = [
        (float(p[0]), float(p[1]), wire)
        for wire in wires for p in (wire.get("polyline") or [])[::max(1, len(wire.get("polyline") or []) - 1)]
        if box["x0"] - reach <= p[0] <= box["x1"] + reach and box["y0"] - reach <= p[1] <= box["y1"] + reach
    ]
    if not ends:
        end = _faint_destination_end(np.asarray(source_image.convert("L")), box) if source_image is not None else None
        if end is None:
            return []
        return [{
            "terminal_ref": f"{symbol['id']}-P01", "terminal_name": "P1", "point": [end[0], end[1]],
            "source": "destination_faint_line", "wire_orientation": "h",
        }]
    x, y, wire = min(ends, key=lambda e: (e[0] - cx) ** 2 + (e[1] - cy) ** 2)
    return [{
        "terminal_ref": f"{symbol['id']}-P01", "terminal_name": "P1", "point": [x, y], "source": "destination_wire_end",
        "wire_id_hint": wire.get("id"), "wire_orientation": wire.get("orientation"),
    }]


# Ports are inferred from ink touching each box edge, so any class can take part
# in connectivity when a wire visibly meets its outline.
DEFAULT_INFERRED_TERMINAL_CLASSES = {
    "contact_a", "contact_b", "solenoid", "relay_coil",
    "lamp", "push_button", "diode", "other", "terminal", "power_bus",
    "connector", "junction",
}


def port_inference_config(name: str) -> dict[str, Any]:
    for config in default_configs():
        if str(config.get("name")) == name:
            return config
    raise ValueError(f"unknown inferred terminal config: {name}")


def terminal_points_for_symbol(
    symbol: dict[str, Any],
    *,
    wires: list[dict[str, Any]],
    source_image: Image.Image,
    terminal_source: str,
    inferred_terminal_classes: set[str],
    inferred_terminal_config: dict[str, Any],
) -> list[dict[str, Any]]:
    class_name = str(symbol.get("class_name") or symbol.get("type") or "")
    terminals = symbol.get("terminals") or []

    if terminal_source in {"annotation", "annotation-or-inferred"}:
        points = []
        for index, terminal in enumerate(terminals, start=1):
            point = terminal.get("point")
            if not point:
                continue
            points.append(
                {
                    "terminal_ref": terminal.get("ref") or f"{symbol['id']}-T{index}",
                    "terminal_name": terminal.get("name") or "",
                    "point": [float(point[0]), float(point[1])],
                    "source": "annotation",
                }
            )
        if points:
            return points

    if terminal_source in {"inferred", "annotation-or-inferred"} and class_name in inferred_terminal_classes:
        if symbol.get("other_kind") == "destination":
            return destination_port(symbol, wires, source_image)
        candidates = infer_ports_for_symbol(symbol, wires, source_image, config=inferred_terminal_config)
        if candidates:
            return [
                {
                    "terminal_ref": f"{symbol['id']}-P{index:02d}",
                    "terminal_name": f"P{index}",
                    "point": [float(candidate["point"][0]), float(candidate["point"][1])],
                    "source": "inferred_symbol_port",
                    "wire_id_hint": candidate.get("wire_id"),
                    "wire_orientation": candidate.get("wire_orientation"),
                    "port_reason": candidate.get("reason"),
                    "port_ink_ratio": candidate.get("ink_ratio"),
                }
                for index, candidate in enumerate(candidates, start=1)
            ]

    if class_name in EXPLICIT_NODE_CLASSES:
        # 黒丸が見つかっていれば端子はその中心。枠の中心は番号の文字の分だけ黒丸からずれ、細い線の上の
        # 端子どうしがそろわなかった（ER21281 の 2AT3 14 は 12 px）。
        dot = ((symbol.get("dot_role") or {}).get("dot") or {}).get("center")
        center = dot or symbol["center"]
        return [
            {
                "terminal_ref": f"{symbol['id']}-N01",
                "terminal_name": "N1",
                "point": [float(center[0]), float(center[1])],
                "source": "explicit_node_center",
            }
        ]

    return [
        {
            "terminal_ref": None,
            "terminal_name": None,
            "point": [float(symbol["center"][0]), float(symbol["center"][1])],
            "source": "bbox_center_fallback",
        }
    ]


# 端子が 2 個に満たないとき、画像の線から 2 端子を決める記号（DD-0009 §8）。短い引き出し線や信頼度の低い縦線は
# 配線として検出されず、端子が付かないため回路から外れていた。
LINE_PORT_CLASSES = classes.TWO_TERMINAL_DEVICES
# 細い点線のような薄い灰色の線（ER21256 のコイルの上下、ER21302 の接点の上下）は二値化で消える。
# 接点・コイルでは灰色の濃さで線を探す（DD-0009 §9、DD-0016）。
FAINT_PORT_CLASSES = classes.COILS | classes.CONTACTS


def _ink_run(mask: Any, x: int, y: int, dx: int, dy: int, limit: int, gap: int) -> int:
    """(x, y) から (dx, dy) の向きに続くインクの長さ。線の太さの揺れに 1 px、途切れに gap px を許す。"""
    height, width = mask.shape[:2]
    run = miss = 0
    for i in range(1, limit + 1):
        px, py = x + dx * i, y + dy * i
        if not (0 <= px < width and 0 <= py < height):
            break
        inked = mask[py, max(0, px - 1):px + 2].any() if dx == 0 else mask[max(0, py - 1):py + 2, px].any()
        if inked:
            run, miss = i, 0
        else:
            miss += 1
            if miss > gap:
                break
    return run


class TraceHit(NamedTuple):
    """線をたどって当たったもの。配線か、線が中心を通るコネクタ・端子台の記号か、どちらも無い。"""

    wire_id: str | None = None
    symbol_id: str | None = None


def _trace_to_wire(
    mask: Any, start: tuple[int, int], step: tuple[int, int], wires: list[dict[str, Any]],
    others: list[dict[str, Any]], dot_boxes: list[dict[str, Any]], gap: int, params: PortParams,
) -> TraceHit:
    """端子から外へ線をたどり、最初に当たる配線。黒丸の無い交差（線が向こうへ続く）は通り過ぎ、
    黒丸（分岐点）の上で当たった配線はそこでつながっているとみなす。

    同じ向きの配線が記号の枠を貫いていれば、その配線を返す。両端子が同じ配線に乗り、配線の上の位置で
    順に並ぶので、記号は直列に入る（推定の端子と同じ扱い）。ほかの記号の枠に入ったら止める。
    その記号がコネクタ・端子台で、中心がたどる線の上にあれば、その記号に当たったとする。
    """
    height, width = mask.shape[:2]
    dx, dy = step
    x, y = start
    miss = 0
    for i in range(0, params.line_trace_max_px + 1):
        px, py = x + dx * i, y + dy * i
        if not (0 <= px < width and 0 <= py < height):
            return TraceHit()
        entered = next((s for s in others if i > 3 and _inside(s["bbox"], px, py)), None)
        if entered is not None:
            return TraceHit(symbol_id=_pass_through_on_line(entered, (x, y), step, params))
        for wire in wires:
            box = wire.get("bbox") or {}
            if not box or not (box["x0"] - 2 <= px <= box["x1"] + 2 and box["y0"] - 2 <= py <= box["y1"] + 2):
                continue
            if (wire.get("orientation") == "v") == (dx == 0):
                return TraceHit(wire_id=str(wire["id"]))
            # 直交する配線: 線がその向こうへ続けば黒丸の無い交差なので通り過ぎる。
            on_dot = any(b["x0"] - 2 <= px <= b["x1"] + 2 and b["y0"] - 2 <= py <= b["y1"] + 2 for b in dot_boxes)
            thickness = (box["y1"] - box["y0"]) if dx == 0 else (box["x1"] - box["x0"])
            beyond = int(thickness) + 4
            if not on_dot and _ink_run(mask, px + dx * beyond, py + dy * beyond, dx, dy, 8, params.line_gap_px) >= 6:
                continue
            return TraceHit(wire_id=str(wire["id"]))
        inked = mask[py, max(0, px - 1):px + 2].any() if dx == 0 else mask[max(0, py - 1):py + 2, px].any()
        if inked:
            miss = 0
        else:
            miss += 1
            if miss > gap:
                return TraceHit()
    return TraceHit()


def _inside(box: dict[str, Any], x: float, y: float) -> bool:
    return box["x0"] <= x <= box["x1"] and box["y0"] <= y <= box["y1"]


def _pass_through_on_line(symbol: dict[str, Any], start: tuple[int, int], step: tuple[int, int], params: PortParams) -> str | None:
    """コネクタ・端子台（分岐点を除く）の中心がたどる線から params.line_pass_through_px 以内ならその ID。"""
    if symbol.get("class_name") not in classes.PASS_THROUGH - {classes.JUNCTION} or params.line_pass_through_px <= 0:
        return None
    cx, cy = symbol["center"]
    across = cx - start[0] if step[0] == 0 else cy - start[1]
    return str(symbol["id"]) if abs(across) <= params.line_pass_through_px else None


def _faint_column(faint: Any, x0: int, y0: int, x1: int, y1: int, params: PortParams, *, middle_only: bool) -> int | None:
    """枠の上下の外に薄い線が続く列。無ければ None。

    コイルは線が枠の中央を通るので中央の半分の列だけ見る（middle_only）。接点は線が枠の左寄りを通り
    （右に名前の欄がある）、枠全体を見る。

    枠の外の line_probe_px の行のうち faint_coverage 以上の行に線がある列（近くの文字の列は 0.25 前後）。
    """
    height = faint.shape[0]
    top = faint[max(0, y0 - params.line_probe_px):max(0, y0 - 2)]
    bottom = faint[min(height, y1 + 2):min(height, y1 + params.line_probe_px)]
    if not len(top) or not len(bottom):
        return None
    inset = (x1 - x0) // 4 if middle_only else 2
    best: tuple[float, list[int]] = (params.faint_coverage, [])
    for c in range(x0 + inset, x1 - inset + 1):
        score = min(top[:, max(0, c - 1):c + 2].any(axis=1).mean(), bottom[:, max(0, c - 1):c + 2].any(axis=1).mean())
        if score > best[0] + 1e-9:
            best = (score, [c])
        elif abs(score - best[0]) <= 1e-9 and best[1] and c == best[1][-1] + 1:
            best[1].append(c)
    return best[1][len(best[1]) // 2] if best[1] else None


def _lead_column(mask: Any, x0: int, y0: int, x1: int, y1: int, params: PortParams) -> tuple[int, int, int] | None:
    """接点の枠の左半分で、上と下に引き出し線（縦の画）がある列と、上の線の上端・下の線の下端。無ければ None。

    枠の外の線が薄すぎて見えない接点（ER21302・ER21293）でも、記号の中の引き出し線は濃く描かれる。名前の欄は
    枠の右側にあるので左半分だけ見る。上の線は枠の上から lead_reach、下の線は枠の下から lead_reach の範囲で始まり、
    どちらも枠の高さの lead_min_share 以上続く。
    """
    height = y1 - y0
    if height <= 0:
        return None
    reach, need = params.lead_reach * height, params.lead_min_share * height
    # 線の太さの揺れを 1 px 許して見るので、同じ強さの列が線の幅だけ続く。その真ん中を選ぶ。
    best_score, best = need - 1e-9, []
    for c in range(x0 + 2, (x0 + x1) // 2 + 1):
        inked = mask[y0:y1 + 1, max(0, c - 1):c + 2].any(axis=1)
        rows = np.flatnonzero(inked)
        if rows.size == 0 or rows[0] > reach or rows[-1] < height - reach:
            continue
        top, bottom = _run(inked, int(rows[0]), 1), _run(inked, int(rows[-1]), -1)
        score = min(top, bottom)
        found = (c, y0 + int(rows[0]), y0 + int(rows[-1]))
        if score > best_score + 1e-9:
            best_score, best = score, [found]
        elif abs(score - best_score) <= 1e-9 and best and c == best[-1][0] + 1:
            best.append(found)
    return best[len(best) // 2] if best else None


def _dotted_column(faint: Any, x0: int, y0: int, x1: int, y1: int, params: PortParams) -> int | None:
    """接点の枠の左半分で、枠の上と下の外にどちらも params.dotted_min_dots 個以上の点が並ぶ列（まばらな点線、DD-0023）。

    上部の母線の行の接点（ER21261・ER21302 の 63LL など）は、枠の外の線が 5〜30 px おきの点にしか残らず、
    _faint_column の割合にも、記号の中の引き出し線（_lead_column）にも届かない。
    """
    height = faint.shape[0]
    top = faint[max(0, y0 - params.line_probe_px):max(0, y0 - 2)]
    bottom = faint[min(height, y1 + 2):min(height, y1 + params.line_probe_px)]
    if not len(top) or not len(bottom):
        return None
    best_score, best = params.dotted_min_dots - 1, []
    for c in range(x0 + 2, (x0 + x1) // 2 + 1):
        score = min(_count_runs(top[:, max(0, c - 1):c + 2].any(axis=1)), _count_runs(bottom[:, max(0, c - 1):c + 2].any(axis=1)))
        if score > best_score:
            best_score, best = score, [c]
        elif score == best_score and best and c == best[-1] + 1:
            best.append(c)
    return best[len(best) // 2] if best else None


def _count_runs(inked: Any) -> int:
    return int(np.count_nonzero(np.diff(np.concatenate(([0], inked.astype(np.int8)))) == 1))


def _run(inked: Any, start: int, step: int) -> int:
    """start から step の向きに続くインクの長さ（1 px の途切れは許す）。"""
    length, i, miss = 0, start, 0
    while 0 <= i < len(inked):
        if inked[i]:
            length, miss = abs(i - start) + 1, 0
        else:
            miss += 1
            if miss > 1:
                break
        i += step
    return length


def line_ports(
    symbol: dict[str, Any], mask: Any, wires: list[dict[str, Any]], symbols: list[dict[str, Any]],
    faint: Any | None = None, params: PortParams = DEFAULT.ports,
) -> list[dict[str, Any]]:
    """枠の両側から外へ続く線を探して 2 端子にし、線をたどって最初に当たる配線を端子の配線にする。見つからなければ空。

    faint（灰色が params.faint_gray 未満の画素）を渡すと、コイルで線が見つからないときに薄い線を探してたどる。
    """
    box = symbol["bbox"]
    x0, y0, x1, y1 = (int(round(float(box[k]))) for k in ("x0", "y0", "x1", "y1"))
    axes = ["v"] if symbol.get("class_name") in classes.COILS else ["v", "h"]
    # 線の太さの揺れを 1 px 許して見るので、同じ強さの列が線の幅だけ続く。その真ん中を端子の位置にする。
    best_score, best_axis, best_columns = 0, "v", []
    for axis in axes:
        lo, hi = (x0 + 2, x1 - 2) if axis == "v" else (y0 + 2, y1 - 2)
        for c in range(lo, hi + 1):
            if axis == "v":
                runs = (_ink_run(mask, c, y0, 0, -1, params.line_probe_px, params.line_gap_px),
                        _ink_run(mask, c, y1, 0, 1, params.line_probe_px, params.line_gap_px))
            else:
                runs = (_ink_run(mask, x0, c, -1, 0, params.line_probe_px, params.line_gap_px),
                        _ink_run(mask, x1, c, 1, 0, params.line_probe_px, params.line_gap_px))
            score = min(runs)
            if score < params.line_min_run_px or score < best_score:
                continue
            if score > best_score or axis != best_axis or c != best_columns[-1] + 1:
                best_score, best_axis, best_columns = score, axis, [c]
            else:
                best_columns.append(c)
    trace_mask, gap = mask, params.line_gap_px
    if best_columns:
        axis, c = best_axis, best_columns[len(best_columns) // 2]
    elif faint is not None and symbol.get("class_name") in FAINT_PORT_CLASSES:
        found = _faint_column(faint, x0, y0, x1, y1, params, middle_only=symbol.get("class_name") in classes.COILS)
        lead = _lead_column(mask, x0, y0, x1, y1, params) if found is None and symbol.get("class_name") in classes.CONTACTS else None
        if found is None and lead is None and symbol.get("class_name") in classes.CONTACTS:
            found = _dotted_column(faint, x0, y0, x1, y1, params)
        if found is None and lead is None:
            return []
        if lead is not None:
            # 端子は引き出し線の先に置き、そこから薄い線をたどる。
            found, y0, y1 = lead
        axis, c, trace_mask, gap = "v", found, faint, params.faint_gap_px
    else:
        return []
    live = [s for s in symbols if s is not symbol and not (s.get("dot_role") or {}).get("suppressed")]
    others = [s for s in live if s.get("class_name") != classes.JUNCTION]
    dots = [s["bbox"] for s in live if s.get("class_name") == classes.JUNCTION]
    ends = [((c, y0), (0, -1)), ((c, y1), (0, 1))] if axis == "v" else [((x0, c), (-1, 0)), ((x1, c), (1, 0))]
    ports = []
    for index, (point, step) in enumerate(ends, start=1):
        hit = _trace_to_wire(trace_mask, point, step, wires, others, dots, gap, params)
        ports.append({
            "terminal_ref": f"{symbol['id']}-L{index:02d}",
            "terminal_name": f"P{index}",
            "point": [float(point[0]), float(point[1])],
            "source": "line_port",
            "wire_id_hint": hit.wire_id,
            "symbol_id_hint": hit.symbol_id,
            "wire_orientation": axis,
            "traced": True,
        })
    return ports
