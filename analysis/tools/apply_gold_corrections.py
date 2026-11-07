"""Apply reviewed corrections to a gold annotation export ZIP and write a corrected ZIP the app can import.

Corrections are a JSON list. Each item names the drawing (sheet, e.g. ER21276) and one operation:

- {"op": "relabel", "ref": "SYM-0008", "was": "<label in the ZIP>", "label": "<label printed on the drawing>"}
- {"op": "add_symbol", "class_key": "connector", "label": "...", "bbox_px": [x0, y0, x1, y1]}
  for a symbol the annotators boxed on the other drawings but missed here. It gets no terminals and no wires.

Every item also carries "reason" (what the drawing shows). A relabel whose current label is neither "was" nor
the new label stops the run, so corrections are never applied to a different version of the data. Applying the
same corrections twice changes nothing. The original ZIP is not modified.

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

