"""Terminal numbers read from the label OCR around terminals and contacts.

A terminal (connector) dot on a vertical wire has device names on its left (CN229, TB16) and
the pin number of each device on its right, one row per device. A contact has its terminal
numbers beside its two ends. Numbers are attached to the symbol's terminals so from-to rows
carry them.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any

DEVICE = re.compile(r"[A-Z0-9-]*[A-Z][A-Z0-9-]*")
NUMBER = re.compile(r"\d{1,4}")
# Tightly packed terminal blocks fuse a pin number with the next terminal's name ("17CN2").
FUSED_NUMBER = re.compile(r"(\d{1,4})[A-Z].*")
DEVICE_PARTS = re.compile(r"(\d*[A-Z]+)([A-Z0-9]*)")
CONTACT_CLASSES = {"contact_a", "contact_b"}
# False leaves terminal names as inferred (P1, P2).
ENABLED = True


def _fragments(symbol: dict[str, Any]) -> list[dict[str, Any]]:
    """OCR fragments in image coordinates."""
    ocr = symbol.get("label_ocr") or {}
    crop, size = ocr.get("crop_box"), ocr.get("processed_size")
    if not crop or not size:
        return []
    sx = size[0] / max(1, crop[2] - crop[0])
    sy = size[1] / max(1, crop[3] - crop[1])
    out = []
    for fragment in ocr.get("fragments") or []:
        text = re.sub(r"\s+", "", unicodedata.normalize("NFKC", str(fragment.get("text") or "")).upper())
        xs = [crop[0] + p[0] / sx for p in fragment["bbox"]]
        ys = [crop[1] + p[1] / sy for p in fragment["bbox"]]
        out.append({"text": text, "x": sum(xs) / len(xs), "y": sum(ys) / len(ys), "h": max(ys) - min(ys)})
    return out


def device_name(text: str) -> str:
    """Undo common OCR confusions in names like TB16 and CN10 (T86, CNIO)."""
    text = re.sub(r"[^A-Z0-9-]", "", text)
    if text.startswith("T8"):
        text = "TB" + text[2:]
    parts = DEVICE_PARTS.fullmatch(text)
    if not parts:
        return text
    prefix, rest = parts.groups()
    return prefix + rest.replace("O", "0").replace("I", "1")


def connector_numbers(symbol: dict[str, Any]) -> list[dict[str, str]]:
    dot = (symbol.get("dot_role") or {}).get("dot")
    box = symbol["bbox"]
    split_x = dot["center"][0] if dot else (box["x0"] + box["x1"]) / 2
    fragments = _fragments(symbol)
    names = [{**f, "text": device_name(f["text"])} for f in fragments if f["x"] < split_x]
    names = [f for f in names if DEVICE.fullmatch(f["text"])]
    numbers = []
    for f in fragments:
        if f["x"] <= split_x:
            continue
        fused = FUSED_NUMBER.fullmatch(f["text"])
        if NUMBER.fullmatch(f["text"]) or fused:
            numbers.append({**f, "text": fused.group(1) if fused else f["text"]})
    if len(names) == 1 and len(numbers) == 1:
        return [{"device": names[0]["text"], "number": numbers[0]["text"]}]
    pairs = []
    for number in sorted(numbers, key=lambda f: f["y"]):
        row = [n for n in names if abs(n["y"] - number["y"]) <= max(6.0, 0.6 * number["h"])]
        device = min(row, key=lambda n: abs(n["y"] - number["y"]))["text"] if row else ""
        pairs.append({"device": device, "number": number["text"]})
    # A number with no device on its row is a wire number printed under the terminal label.
    if any(p["device"] for p in pairs):
        pairs = [p for p in pairs if p["device"]]
    return pairs


def contact_numbers(symbol: dict[str, Any]) -> dict[str, str]:
    """Nearest number to each terminal of a contact, keyed by terminal ref."""
    numbers = [f for f in _fragments(symbol) if NUMBER.fullmatch(f["text"])]
    out: dict[str, str] = {}
    used: set[int] = set()
    for terminal in symbol.get("terminals") or []:
        tx, ty = terminal["point"]
        ranked = sorted((abs(f["y"] - ty) + 0.5 * abs(f["x"] - tx), i) for i, f in enumerate(numbers) if i not in used)
        if ranked and ranked[0][0] <= 30:
            used.add(ranked[0][1])
            out[str(terminal.get("ref"))] = numbers[ranked[0][1]]["text"]
    return out


def attach_terminal_numbers(symbols: list[dict[str, Any]]) -> dict[str, int] | None:
    if not ENABLED:
        return None
    counts = {"connectors": 0, "contact_terminals": 0}
    for symbol in symbols:
        if (symbol.get("dot_role") or {}).get("suppressed"):
            continue
        if symbol.get("class_name") == "connector":
            pairs = connector_numbers(symbol)
            if not pairs:
                continue
            symbol["terminal_numbers"] = pairs
            name = " ".join(f"{p['device']}-{p['number']}" if p["device"] else p["number"] for p in pairs)
            for terminal in symbol.get("terminals") or []:
                terminal["name"] = name
            counts["connectors"] += 1
        elif symbol.get("class_name") in CONTACT_CLASSES:
            numbers = contact_numbers(symbol)
            for terminal in symbol.get("terminals") or []:
                if str(terminal.get("ref")) in numbers:
                    terminal["name"] = numbers[str(terminal.get("ref"))]
                    counts["contact_terminals"] += 1
    return counts

