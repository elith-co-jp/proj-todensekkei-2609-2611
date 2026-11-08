"""Apply reviewed corrections to a gold annotation export ZIP and write a corrected ZIP the app can import.

Corrections are a JSON list. Each item names the drawing (sheet, e.g. ER21276) and one operation:

- {"op": "relabel", "ref": "SYM-0008", "was": "<label in the ZIP>", "label": "<label printed on the drawing>"}
- {"op": "add_symbol", "class_key": "connector", "label": "...", "bbox_px": [x0, y0, x1, y1]}
  for a symbol the annotators boxed on the other drawings but missed here. It gets no terminals and no wires.
- {"op": "rebox", "ref": "SYM-0045", "was_bbox_px": [x0, y0, x1, y1], "bbox_px": [x0, y0, x1, y1]}
  for a box drawn to a different convention than the same symbol on the other drawings (DD-0017).
- {"op": "two_terminal_contact", "ref": "SYM-0005", "bbox_px": [x0, y0, x1, y1]}
  for a contact annotated with four terminals (the card's outer dots too). It keeps the two inner terminals,
  moves wires on an outer terminal to the inner terminal on the same side and takes the box of a two-terminal contact.
- {"op": "add_wire", "from": "SYM-0062-T8", "to": "SYM-0009-T1"}
  for a wire drawn on the drawing that the annotators left out (DD-0020). Both terminals must exist.

Every item also carries "reason" (what the drawing shows). A relabel whose current label is neither "was" nor
the new label, or a box that is neither "was_bbox_px" nor the new box, stops the run, so corrections are never
applied to a different version of the data. Applying the same corrections twice changes nothing. The original
ZIP is not modified.

The corrected bundle is imported into a scratch database and exported again with the app's own exporter, so
the YOLO labels, netlist and connection list in the new ZIP match the corrected bundle.

Example:
    python analysis/tools/apply_gold_corrections.py --zip gold.zip --corrections corrections.json --out gold_fixed.zip
"""

from __future__ import annotations

import argparse
import copy
import io
import json
import os
import sys
import tempfile
import zipfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from holdout_cv import sheet_of  # noqa: E402

ADDED_NOTE = "正解データの修正で追加"
OVERLAP_IOU = 0.3
# 確かめた枠と同じとみなすずれ（px）。書き出しで座標を丸めるので、少しずれる。
BOX_TOLERANCE_PX = 1.5


class CorrectionError(ValueError):
    pass


def _iou(a: tuple[float, float, float, float], b: tuple[float, float, float, float]) -> float:
    x0, y0, x1, y1 = max(a[0], b[0]), max(a[1], b[1]), min(a[2], b[2]), min(a[3], b[3])
    inter = max(0.0, x1 - x0) * max(0.0, y1 - y0)
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / union if union > 0 else 0.0


def _box(symbol: dict[str, Any]) -> tuple[float, float, float, float]:
    return (symbol["cx"] - symbol["w"] / 2, symbol["cy"] - symbol["h"] / 2, symbol["cx"] + symbol["w"] / 2, symbol["cy"] + symbol["h"] / 2)


def _next_ref(symbols: list[dict[str, Any]]) -> str:
    numbers = [int(s["ref"].split("-")[-1]) for s in symbols if s["ref"].startswith("SYM-") and s["ref"].split("-")[-1].isdigit()]
    return f"SYM-{max(numbers, default=0) + 1:04d}"


def _box_px(symbol: dict[str, Any], width: float, height: float) -> tuple[float, float, float, float]:
    x0, y0, x1, y1 = _box(symbol)
    return (x0 * width, y0 * height, x1 * width, y1 * height)


def _same_box(a: tuple[float, ...], b: list[float] | tuple[float, ...]) -> bool:
    return all(abs(p - q) <= BOX_TOLERANCE_PX for p, q in zip(a, b, strict=True))


def _set_box(symbol: dict[str, Any], box: list[float], width: float, height: float) -> None:
    x0, y0, x1, y1 = box
    symbol.update(cx=(x0 + x1) / 2 / width, cy=(y0 + y1) / 2 / height, w=(x1 - x0) / width, h=(y1 - y0) / height)


def _two_terminal_contact(project: dict[str, Any], symbol: dict[str, Any], item: dict[str, Any]) -> str:
    """4 端子の接点を内側の 2 端子にする。外側の端子の配線は同じ側の内側の端子へ移し、移して自分どうしになった配線は消す。"""
    width, height = float(project["image_width"]), float(project["image_height"])
    terminals = sorted(symbol.get("terminals") or [], key=lambda t: t["ty"])
    if len(terminals) == 2 and _same_box(_box_px(symbol, width, height), item["bbox_px"]):
        return "already"
    if len(terminals) != 4 or max(t["tx"] for t in terminals) - min(t["tx"] for t in terminals) > 0.01:
        raise CorrectionError(f"{item['sheet']} {item['ref']}: expected four terminals on one vertical line")
    outer_top, inner_top, inner_bottom, outer_bottom = terminals
    moved = {outer_top["ref"]: inner_top["ref"], outer_bottom["ref"]: inner_bottom["ref"]}
    ref = symbol["ref"]
    kept, seen = [], set()
    for conn in project.get("connections") or []:
        conn = dict(conn)
        for side in ("from", "to"):
            if conn[f"{side}_symbol_ref"] == ref and conn.get(f"{side}_terminal_ref") in moved:
                conn[f"{side}_terminal_ref"] = moved[conn[f"{side}_terminal_ref"]]
        ends = frozenset(((conn["from_symbol_ref"], conn.get("from_terminal_ref")), (conn["to_symbol_ref"], conn.get("to_terminal_ref"))))
        if len(ends) == 1 or ends in seen:
            continue
        seen.add(ends)
        kept.append(conn)
    project["connections"] = kept
    symbol["terminals"] = [inner_top, inner_bottom]
    _set_box(symbol, item["bbox_px"], width, height)
    return "applied"


def _add_wire(project: dict[str, Any], item: dict[str, Any]) -> str:
    """図面にあるのに付け忘れた配線を 1 本足す。両端の端子が無ければ止め、同じ 2 端子の配線があれば何もしない。"""
    symbol_of = {t["ref"]: s["ref"] for s in project["symbols"] for t in s.get("terminals") or []}
    for end in (item["from"], item["to"]):
        if end not in symbol_of:
            raise CorrectionError(f"{item['sheet']} {end}: terminal not found")
    ends = {item["from"], item["to"]}
    connections = project.setdefault("connections", [])
    if any({c.get("from_terminal_ref"), c.get("to_terminal_ref")} == ends for c in connections):
        return "already"
    numbers = [int(c["ref"].split("-")[-1]) for c in connections if str(c.get("ref", "")).split("-")[-1].isdigit()]
    connections.append({
        "ref": f"W-{max(numbers, default=0) + 1:04d}",
        "from_symbol_ref": symbol_of[item["from"]], "from_terminal_ref": item["from"],
        "to_symbol_ref": symbol_of[item["to"]], "to_terminal_ref": item["to"],
        "wire_no": None, "net_id": None, "kind": "wire", "external_ref": None, "note": ADDED_NOTE, "origin": "manual",
        "attributes": {},
    })
    return "applied"


def apply_corrections(bundle: dict[str, Any], corrections: list[dict[str, Any]]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Return a corrected copy of the bundle and one result per correction whose sheet is in the bundle."""
    bundle = copy.deepcopy(bundle)
    projects = {sheet_of(p): p for p in bundle["projects"]}
    classes = {c["key"]: c for c in bundle["classes"]}
    results = []
    for item in corrections:
        project = projects.get(item["sheet"])
        if project is None:
            continue
        symbols = project["symbols"]
        if item["op"] == "relabel":
            symbol = next((s for s in symbols if s["ref"] == item["ref"]), None)
            if symbol is None:
                raise CorrectionError(f"{item['sheet']} {item['ref']}: symbol not found")
            current = symbol.get("label") or ""
            if current == item["label"]:
                status = "already"
            elif current == item["was"]:
                symbol["label"], status = item["label"], "applied"
            else:
                raise CorrectionError(f"{item['sheet']} {item['ref']}: label is {current!r}, expected {item['was']!r}")
        elif item["op"] == "add_symbol":
            width, height = float(project["image_width"]), float(project["image_height"])
            x0, y0, x1, y1 = item["bbox_px"]
            new = {"cx": (x0 + x1) / 2 / width, "cy": (y0 + y1) / 2 / height, "w": (x1 - x0) / width, "h": (y1 - y0) / height}
            same = [s for s in symbols if s["class_key"] == item["class_key"] and _iou(_box(s), _box(new)) >= OVERLAP_IOU]
            if any((s.get("label") or "") == item["label"] for s in same):
                status = "already"
            elif same:
                raise CorrectionError(f"{item['sheet']}: a {item['class_key']} already covers {item['bbox_px']} ({same[0]['ref']})")
            else:
                cls = classes[item["class_key"]]
                symbols.append({
                    "ref": _next_ref(symbols), "class_key": cls["key"], "class_label": cls["label"], "yolo_index": cls["yolo_index"],
                    "label": item["label"], **new, "note": ADDED_NOTE, "terminals": [],
                })
                status = "applied"
        elif item["op"] == "rebox":
            symbol = next((s for s in symbols if s["ref"] == item["ref"]), None)
            if symbol is None:
                raise CorrectionError(f"{item['sheet']} {item['ref']}: symbol not found")
            width, height = float(project["image_width"]), float(project["image_height"])
            current = _box_px(symbol, width, height)
            if _same_box(current, item["bbox_px"]):
                status = "already"
            elif _same_box(current, item["was_bbox_px"]):
                _set_box(symbol, item["bbox_px"], width, height)
                status = "applied"
            else:
                raise CorrectionError(f"{item['sheet']} {item['ref']}: box is {[round(v, 1) for v in current]}, "
                                      f"expected {item['was_bbox_px']}")
        elif item["op"] == "two_terminal_contact":
            symbol = next((s for s in symbols if s["ref"] == item["ref"]), None)
            if symbol is None:
                raise CorrectionError(f"{item['sheet']} {item['ref']}: symbol not found")
            status = _two_terminal_contact(project, symbol, item)
        elif item["op"] == "add_wire":
            status = _add_wire(project, item)
        else:
            raise CorrectionError(f"unknown op {item['op']!r}")
        results.append({**item, "status": status})
    return bundle, results


def rebuild_zip(original: bytes, bundle: dict[str, Any]) -> bytes:
    """Import the corrected bundle into a scratch database and export it with the app's exporter."""
    with tempfile.TemporaryDirectory(prefix="gold-fix-") as tmp:
        os.environ["SEQANNO_DATA_DIR"] = tmp
        os.environ["SEQANNO_DATABASE_URL"] = f"sqlite:///{Path(tmp) / 'fix.db'}"
        import database
        from models import AnnotationProject
        from services.export_service import BUNDLE_FILENAME, import_zip, write_export_zip

        database.init_db()
        staged = io.BytesIO()
        with zipfile.ZipFile(io.BytesIO(original)) as src, zipfile.ZipFile(staged, "w", zipfile.ZIP_DEFLATED) as out:
            for name in src.namelist():
                out.writestr(name, json.dumps(bundle, ensure_ascii=False) if name.endswith(BUNDLE_FILENAME) else src.read(name))
        with database.SessionLocal() as db:
            import_zip(db, staged.getvalue())
            db.commit()
            projects = db.query(AnnotationProject).order_by(AnnotationProject.id.asc()).all()
            return write_export_zip(db, projects)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--zip", type=Path, required=True)
    parser.add_argument("--corrections", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    if args.out.resolve() == args.zip.resolve():
        raise SystemExit("--out must differ from --zip (the original ZIP is kept unchanged)")
    original = args.zip.read_bytes()
    with zipfile.ZipFile(io.BytesIO(original)) as zf:
        bundle = json.loads(zf.read(next(n for n in zf.namelist() if n.endswith("bundle.json"))))
    corrected, results = apply_corrections(bundle, json.loads(args.corrections.read_text(encoding="utf-8")))
    args.out.write_bytes(rebuild_zip(original, corrected))
    counts: dict[str, int] = {}
    for r in results:
        counts[f"{r['op']}:{r['status']}"] = counts.get(f"{r['op']}:{r['status']}", 0) + 1
    print(json.dumps({"zip": args.zip.name, "out": str(args.out), "results": counts}, ensure_ascii=False))


if __name__ == "__main__":
    main()

