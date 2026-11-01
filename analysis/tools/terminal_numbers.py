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
# A contact's lower number is printed right after the left neighbour's label and is read fused with it ("(ER21271)6").
CONTACT_NUMBER = re.compile(r"(?:.*\))?(\d{1,4})[.,]?")
PAGE_PAD_PX = 8
# False leaves terminal names as inferred (P1, P2).
ENABLED = True


def _box_fragments(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "text": re.sub(r"\s+", "", unicodedata.normalize("NFKC", str(item["text"])).upper()),
            "x": (item["box"][0] + item["box"][2]) / 2,
            "y": (item["box"][1] + item["box"][3]) / 2,
            "h": item["box"][3] - item["box"][1],
            "x0": item["box"][0],
            "x1": item["box"][2],
        }
        for item in items
    ]


def _fragments(symbol: dict[str, Any]) -> list[dict[str, Any]]:
    """OCR fragments in image coordinates."""
    if symbol.get("text_items") is not None:
        return _box_fragments(symbol["text_items"])
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
        out.append({"text": text, "x": sum(xs) / len(xs), "y": sum(ys) / len(ys), "h": max(ys) - min(ys), "x0": min(xs), "x1": max(xs)})
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


def _contact_number_fragments(fragments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for f in fragments:
        match = CONTACT_NUMBER.fullmatch(f["text"])
        if not match:
            continue
        digits, x = match.group(1), f["x"]
        if match.start(1) > 0:
            x = f["x1"] - (f["x1"] - f["x0"]) * len(digits) / len(f["text"]) / 2
        out.append({**f, "text": digits, "x": x, "whole": match.span(1) == (0, len(f["text"]))})
    return out


def _without_end_circle(f: dict[str, Any], tx: float, label_right: bool) -> str:
    """The hollow circle at a contact end is read as one more digit when the number touches it (12 + circle = 125)."""
    reaches_axis = f["x1"] > tx if label_right else f["x0"] < tx
    if f["whole"] and reaches_axis and len(f["text"]) >= 2:
        return f["text"][:-1] if label_right else f["text"][1:]
    return f["text"]


def _page_numbers(symbol: dict[str, Any], page_items: list[dict[str, Any]], crop: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Page OCR numbers inside the contact box that the crop OCR did not read (it drops small digits beside the circle)."""
    box = symbol["bbox"]
    inside = [
        f for f in _contact_number_fragments(_box_fragments(page_items))
        if box["x0"] - PAGE_PAD_PX <= f["x"] <= box["x1"] + PAGE_PAD_PX and box["y0"] - PAGE_PAD_PX <= f["y"] <= box["y1"] + PAGE_PAD_PX
    ]
    return [f for f in inside if not any(c["x0"] - 3 <= f["x"] <= c["x1"] + 3 and abs(c["y"] - f["y"]) <= c["h"] / 2 + 3 for c in crop)]


def contact_numbers(symbol: dict[str, Any], page_items: list[dict[str, Any]] | None = None) -> dict[str, str]:
    """Nearest number to each terminal of a contact, keyed by terminal ref.

    Numbers sit on the other side of the contact line from the label; digits on the label side belong to the
    label (33 HV2111) or to the next contact. Page OCR numbers only fill ends the crop OCR left empty.
    """
    fragments = _fragments(symbol)
    numbers = _contact_number_fragments(fragments)
    crop_count = len(numbers)
    if page_items:
        numbers += _page_numbers(symbol, page_items, fragments)
    terminals = symbol.get("terminals") or []
    words = [f["x"] for f in fragments if re.search("[A-Z]", f["text"])]
    label_x = sum(words) / len(words) if words else None
    pairs = sorted(
        (fi >= crop_count, abs(f["y"] - ty) + 0.5 * abs(f["x"] - tx), ti, fi)
        for ti, (tx, ty) in enumerate(t["point"] for t in terminals)
        for fi, f in enumerate(numbers)
        if label_x is None or (f["x"] < tx) == (label_x > tx)
    )
    out: dict[str, str] = {}
    used: set[int] = set()
    for _, distance, ti, fi in pairs:
        ref = str(terminals[ti].get("ref"))
        if distance <= 30 and ref not in out and fi not in used:
            used.add(fi)
            out[ref] = numbers[fi]["text"] if label_x is None else _without_end_circle(
                numbers[fi], terminals[ti]["point"][0], label_x > terminals[ti]["point"][0])
    return out


def attach_terminal_numbers(symbols: list[dict[str, Any]], page_items: list[dict[str, Any]] | None = None) -> dict[str, int] | None:
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
            numbers = contact_numbers(symbol, page_items)
            for terminal in symbol.get("terminals") or []:
                if str(terminal.get("ref")) in numbers:
                    terminal["name"] = numbers[str(terminal.get("ref"))]
                    counts["contact_terminals"] += 1
    return counts

