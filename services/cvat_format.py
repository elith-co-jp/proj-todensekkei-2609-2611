"""CVAT（CVAT for images 1.1 の XML）との書き出し・取り込み（DD-0035）。DB を使わない。

会議資料 06 の提案: 枠・点・折れ線は CVAT などで付けられるが、ページ横断の対応は別表（対応表・JSON）で持つ。

- 記号: box（label = クラスのキー）。属性 ref（記号 ref）、text（原文 = ラベル）。
- 端子: points（label = "terminal"）。group_id で記号の box に結ぶ。属性 ref、name（端子名）。
- 配線: polyline（label = "wire"）。最初と最後の点を端子に着ける。属性 from・to（「記号 ref/端子 ref」）、
  wire_no（電線番号）、judge（接続 / 非接続 / 不明）。「非接続」の線（単なる交差の例）は取り込みで配線にしない。
- 図面の対応: image の name を「p{図面 ID}_…」にする。取り込みはこの名前か、図面名・シート番号で図面に当てる。

ページをまたぐ関係（行先札の組、コイルと接点の対応）は CVAT に入れない。対応表（annotation_table）で扱う。
"""

from __future__ import annotations

import math
import re
import xml.etree.ElementTree as ET
from typing import Any

TERMINAL = "terminal"
WIRE = "wire"
SNAP_PX = 12.0  # 折れ線の端を端子に着ける距離（画素）


def _f(v: float) -> str:
    return f"{v:.2f}"


def image_name(page: dict[str, Any]) -> str:
    stem = re.sub(r"[^\w\-.]+", "_", str(page.get("sheet_no") or page.get("name") or "page"))
    return f"p{page['id']}_{stem}.png"


def export_xml(pages: list[dict[str, Any]], class_keys: list[str] | None = None) -> str:
    keys = sorted(set(class_keys or []) | {s["class_key"] for p in pages for s in p["symbols"]})
    root = ET.Element("annotations")
    ET.SubElement(root, "version").text = "1.1"
    meta = ET.SubElement(ET.SubElement(root, "meta"), "task")
    ET.SubElement(meta, "name").text = "TodenYOLO export"
    labels = ET.SubElement(meta, "labels")

    def label(name: str, kind: str, attrs: list[str]) -> None:
        el = ET.SubElement(labels, "label")
        ET.SubElement(el, "name").text = name
        ET.SubElement(el, "type").text = kind
        a_root = ET.SubElement(el, "attributes")
        for a in attrs:
            at = ET.SubElement(a_root, "attribute")
            ET.SubElement(at, "name").text = a
            ET.SubElement(at, "mutable").text = "false"
            ET.SubElement(at, "input_type").text = "text"
            ET.SubElement(at, "default_value").text = ""
            ET.SubElement(at, "values").text = ""

    for k in keys:
        label(k, "rectangle", ["ref", "text"])
    label(TERMINAL, "points", ["ref", "name"])
    label(WIRE, "polyline", ["from", "to", "wire_no", "judge", "kind", "external_ref"])

    for i, page in enumerate(pages):
        w, h = float(page.get("image_width") or 0), float(page.get("image_height") or 0)
        img = ET.SubElement(root, "image", id=str(i), name=image_name(page), width=str(int(w)), height=str(int(h)))
        group = {}
        points: dict[tuple[str, str], tuple[float, float]] = {}
        for g, s in enumerate(page["symbols"], start=1):
            group[s["ref"]] = g
            x0, y0 = (s["cx"] - s["w"] / 2) * w, (s["cy"] - s["h"] / 2) * h
            x1, y1 = (s["cx"] + s["w"] / 2) * w, (s["cy"] + s["h"] / 2) * h
            box = ET.SubElement(img, "box", label=s["class_key"], source="manual" if s.get("origin") != "ai" else "auto",
                                occluded="0", xtl=_f(x0), ytl=_f(y0), xbr=_f(x1), ybr=_f(y1), z_order="0", group_id=str(g))
            ET.SubElement(box, "attribute", name="ref").text = s["ref"]
            ET.SubElement(box, "attribute", name="text").text = s.get("label") or ""
            for t in s.get("terminals") or []:
                if t.get("tx") is None:
                    continue
                px, py = t["tx"] * w, t["ty"] * h
                points[(s["ref"], t["ref"])] = (px, py)
                pt = ET.SubElement(img, "points", label=TERMINAL, source="manual", occluded="0",
                                   points=f"{_f(px)},{_f(py)}", z_order="0", group_id=str(g))
                ET.SubElement(pt, "attribute", name="ref").text = t["ref"]
                ET.SubElement(pt, "attribute", name="name").text = str(t.get("name") or "")
        symbols = {s["ref"]: s for s in page["symbols"]}
        for c in page["connections"]:
            ends = []
            for side in ("from", "to"):
                sref, tref = c[f"{side}_symbol_ref"], c.get(f"{side}_terminal_ref")
                if (sref, tref) in points:
                    ends.append(points[(sref, tref)])
                elif sref in symbols:
                    ends.append((symbols[sref]["cx"] * w, symbols[sref]["cy"] * h))
            if len(ends) != 2:
                continue
            line = ET.SubElement(img, "polyline", label=WIRE, source="manual" if c.get("origin") != "ai" else "auto",
                                 occluded="0", points=";".join(f"{_f(x)},{_f(y)}" for x, y in ends), z_order="0")
            for k, v in (("from", f"{c['from_symbol_ref']}/{c.get('from_terminal_ref') or ''}"),
                         ("to", f"{c['to_symbol_ref']}/{c.get('to_terminal_ref') or ''}"),
                         ("wire_no", c.get("wire_no") or ""), ("judge", "接続"), ("kind", c.get("kind") or "wire"),
                         ("external_ref", c.get("external_ref") or "")):
                ET.SubElement(line, "attribute", name=k).text = v
    ET.indent(root)
    return '<?xml version="1.0" encoding="utf-8"?>\n' + ET.tostring(root, encoding="unicode")


def _attrs(el: ET.Element) -> dict[str, str]:
    return {a.get("name", ""): (a.text or "").strip() for a in el.findall("attribute")}


def _pts(text: str) -> list[tuple[float, float]]:
    out = []
    for p in (text or "").split(";"):
        if "," in p:
            x, y = p.split(",", 1)
            out.append((float(x), float(y)))
    return out


def parse_xml(text: str) -> list[dict[str, Any]]:
    """CVAT の XML → 図面ごとの {name, width, height, symbols, connections, warnings}（PUT annotations と同じ形）。"""
    root = ET.fromstring(text)
    out = []
    for img in root.findall("image"):
        w, h = float(img.get("width") or 0), float(img.get("height") or 0)
        if w <= 0 or h <= 0:
            raise ValueError(f"画像 {img.get('name')} の大きさがありません")
        warnings: list[str] = []
        symbols: list[dict[str, Any]] = []
        by_group: dict[str, dict[str, Any]] = {}
        for seq, box in enumerate(img.findall("box"), start=1):
            a = _attrs(box)
            x0, y0, x1, y1 = (float(box.get(k)) for k in ("xtl", "ytl", "xbr", "ybr"))
            ref = a.get("ref") or f"CV-{seq:04d}"
            s = {"ref": ref, "class_key": box.get("label") or "other", "label": a.get("text") or None,
                 "cx": (x0 + x1) / 2 / w, "cy": (y0 + y1) / 2 / h, "w": (x1 - x0) / w, "h": (y1 - y0) / h,
                 "origin": "manual", "terminals": [], "_px": (x0, y0, x1, y1)}
            symbols.append(s)
            if box.get("group_id"):
                by_group[box.get("group_id")] = s
        terminal_xy: list[tuple[dict[str, Any], dict[str, Any], float, float]] = []
        for pt in img.findall("points"):
            if pt.get("label") != TERMINAL:
                continue
            a = _attrs(pt)
            for px, py in _pts(pt.get("points", "")):
                owner = by_group.get(pt.get("group_id") or "")
                if owner is None:
                    inside = [s for s in symbols if s["_px"][0] - 3 <= px <= s["_px"][2] + 3 and s["_px"][1] - 3 <= py <= s["_px"][3] + 3]
                    owner = min(inside, key=lambda s: (s["_px"][2] - s["_px"][0]) * (s["_px"][3] - s["_px"][1]), default=None)
                if owner is None:
                    warnings.append(f"端子 ({px:.0f}, {py:.0f}) がどの記号にも入っていません")
                    continue
                n = len(owner["terminals"]) + 1
                t = {"ref": a.get("ref") or f"{owner['ref']}-T{n}", "name": a.get("name") or str(n),
                     "tx": px / w, "ty": py / h, "origin": "manual"}
                owner["terminals"].append(t)
                terminal_xy.append((owner, t, px, py))
        refs = {(s["ref"], t["ref"]) for s in symbols for t in s["terminals"]}
        connections = []
        for line in img.findall("polyline"):
            if line.get("label") != WIRE:
                continue
            a = _attrs(line)
            if a.get("judge") in ("非接続", "交差"):
                continue
            pts = _pts(line.get("points", ""))
            ends = []
            for side, (px, py) in (("from", pts[0]), ("to", pts[-1])) if len(pts) >= 2 else ():
                named = a.get(side, "")
                if "/" in named:
                    sref, tref = named.split("/", 1)
                    if (sref, tref) in refs or (tref == "" and any(s["ref"] == sref for s in symbols)):
                        ends.append((sref, tref or None))
                        continue
                near = min(terminal_xy, key=lambda x: math.hypot(x[2] - px, x[3] - py), default=None)
                if near is None or math.hypot(near[2] - px, near[3] - py) > SNAP_PX:
                    warnings.append(f"配線の端 ({px:.0f}, {py:.0f}) の近くに端子がありません")
                    break
                ends.append((near[0]["ref"], near[1]["ref"]))
            if len(ends) != 2:
                continue
            connections.append({"from_symbol_ref": ends[0][0], "from_terminal_ref": ends[0][1],
                                "to_symbol_ref": ends[1][0], "to_terminal_ref": ends[1][1],
                                "wire_no": a.get("wire_no") or None, "kind": a.get("kind") or "wire",
                                "external_ref": a.get("external_ref") or None,
                                "note": "判定: 不明（CVAT）" if a.get("judge") == "不明" else None, "origin": "manual"})
        for s in symbols:
            s.pop("_px")
        m = re.match(r"p(\d+)_", img.get("name") or "")
        out.append({"name": img.get("name"), "project_id": int(m.group(1)) if m else None, "width": int(w), "height": int(h),
                    "symbols": symbols, "connections": connections, "warnings": warnings})
    return out


def match_pages(parsed: list[dict[str, Any]], projects: list[dict[str, Any]]) -> list[tuple[dict[str, Any], dict[str, Any] | None]]:
    """取り込んだ画像を図面に当てる: 名前の p{ID}_、無ければ画像名に図面名・シート番号が入っているもの。"""
    by_id = {p["id"]: p for p in projects}
    out = []
    for item in parsed:
        target = by_id.get(item.get("project_id"))
        if target is None:
            stem = (item.get("name") or "").rsplit(".", 1)[0]
            hits = [p for p in projects if stem and (stem == p.get("name") or (p.get("sheet_no") and p["sheet_no"] in stem))]
            target = hits[0] if len(hits) == 1 else None
        if target is not None and (abs(target.get("image_width", 0) - item["width"]) > 2
                                   or abs(target.get("image_height", 0) - item["height"]) > 2):
            item["warnings"].append(f"画像の大きさが図面と違います（CVAT {item['width']}×{item['height']}、"
                                    f"図面 {target.get('image_width')}×{target.get('image_height')}）")
        out.append((item, target))
    return out
