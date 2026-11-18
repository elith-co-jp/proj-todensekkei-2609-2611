"""記号と配線の属性の定義（DD-0010）。ここに無い項目も自由に足せる。"""

from __future__ import annotations

from typing import Literal, TypedDict

AttributeType = Literal["text", "number"]


class AttributeDef(TypedDict):
    key: str
    label: str
    type: AttributeType
    unit: str | None


def _d(key: str, label: str, kind: AttributeType = "text", unit: str | None = None) -> AttributeDef:
    return {"key": key, "label": label, "type": kind, "unit": unit}


SYMBOL_COMMON: list[AttributeDef] = [
    _d("maker", "メーカー"),
    _d("model", "型式"),
    _d("part_no", "品番"),
    _d("rated_voltage_v", "定格電圧", "number", "V"),
    _d("location", "取付位置"),
]
SYMBOL_BY_CLASS: dict[str, list[AttributeDef]] = {
    "relay_coil": [_d("coil_resistance_ohm", "コイル抵抗", "number", "Ω"), _d("pickup_voltage_v", "動作電圧", "number", "V"),
                   _d("timer_mode", "タイマーの種類（オンディレー / オフディレー）"), _d("delay_s", "タイマーの時間", "number", "s")],
    "solenoid": [_d("coil_resistance_ohm", "コイル抵抗", "number", "Ω"), _d("rated_current_a", "定格電流", "number", "A")],
    "contact_a": [_d("contact_rating_a", "接点定格", "number", "A"), _d("timer_contact", "タイマーの接点（瞬時 = instant）")],
    "contact_b": [_d("contact_rating_a", "接点定格", "number", "A"), _d("timer_contact", "タイマーの接点（瞬時 = instant）")],
    "connector": [_d("pin_count", "ピン数", "number")],
    "terminal": [_d("pin_count", "ピン数", "number")],
    "lamp": [_d("rated_power_w", "定格電力", "number", "W")],
}
WIRE: list[AttributeDef] = [
    _d("length_m", "長さ", "number", "m"),
    _d("wire_type", "電線種別"),
    _d("cross_section_mm2", "断面積", "number", "mm²"),
    _d("color", "色"),
    _d("cable_no", "ケーブル番号"),
    _d("core_no", "心線番号"),
    _d("maker", "メーカー"),
]


def symbol_definitions(class_key: str) -> list[AttributeDef]:
    return SYMBOL_COMMON + SYMBOL_BY_CLASS.get(class_key, [])


def all_definitions() -> dict:
    return {"symbol": {"common": SYMBOL_COMMON, "by_class": SYMBOL_BY_CLASS}, "wire": WIRE}
