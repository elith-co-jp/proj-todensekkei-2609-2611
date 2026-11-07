from __future__ import annotations

import _repo_path  # noqa: F401  (リポジトリの直下を import できるようにする)

import argparse
import csv
import json
import math
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw

from analyze_annotation_graph import load_zip_payload, symbol_bbox
from build_e2e_demo_outputs import load_font
from analysis.wiring.geometry import wire_points
from analysis.wiring.ports import default_configs, infer_ports_for_symbol  # noqa: E402


TARGET_PARENT_CLASSES = ("contact_a", "contact_b", "solenoid", "relay_coil")

FONT_XS = load_font(12)
FONT_SM = load_font(15)
FONT_MD = load_font(18)

GOLD_COLOR = (37, 99, 235)
TP_COLOR = (22, 163, 74)
FP_COLOR = (217, 119, 6)
FN_COLOR = (220, 38, 38)
SYMBOL_COLOR = (88, 28, 135)


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def resolve_artifact_path(value: str | Path) -> Path:
    path = Path(value)
    if path.exists():
        return path

    normalized = str(value).replace("\\", "/")
    replacements = [
        ("data/private/analysis/", "private/results/pdf_structure/"),
        ("data/private/e2e_yolo/", "private/results/e2e/"),
    ]
    for old, new in replacements:
        if normalized.startswith(old):
            candidate = Path(new + normalized[len(old) :])
            if candidate.exists():
                return candidate
    return path


def parse_mapping(items: list[str]) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for item in items:
        if "=" not in item:
            raise ValueError(f"mapping must be KEY=VALUE: {item}")
        key, value = item.split("=", 1)
        key = key.strip()
        value = value.strip()
        if not key or not value:
            raise ValueError(f"mapping must be KEY=VALUE: {item}")
        mapping[key] = value
    return mapping


def metric(tp: int, fp: int, fn: int) -> dict[str, int | float | None]:
    precision = tp / (tp + fp) if tp + fp else None
    recall = tp / (tp + fn) if tp + fn else None
    f1 = 2 * precision * recall / (precision + recall) if precision and recall else None
    return {
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "precision": round(precision, 4) if precision is not None else None,
        "recall": round(recall, 4) if recall is not None else None,
        "f1": round(f1, 4) if f1 is not None else None,
    }


def point_distance(a: tuple[float, float], b: tuple[float, float]) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


def box_tuple(box: dict[str, int | float]) -> tuple[int, int, int, int]:
    return (
        int(round(float(box["x0"]))),
        int(round(float(box["y0"]))),
        int(round(float(box["x1"]))),
        int(round(float(box["y1"]))),
    )


















def match_candidates(
    gold_points: list[dict[str, Any]],
    candidates: list[dict[str, Any]],
    threshold_px: float,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    unmatched_gold = set(range(len(gold_points)))
    matches: list[dict[str, Any]] = []
    false_positives: list[dict[str, Any]] = []

    for candidate in candidates:
        candidate_point = (float(candidate["point"][0]), float(candidate["point"][1]))
        best_index: int | None = None
        best_distance = float("inf")
        for gold_index in unmatched_gold:
            gold = gold_points[gold_index]
            distance = point_distance(candidate_point, (float(gold["point"][0]), float(gold["point"][1])))
            if distance < best_distance:
                best_distance = distance
                best_index = gold_index
        if best_index is None or best_distance > threshold_px:
            false_positives.append({**candidate, "match_status": "fp", "nearest_gold_distance": round(best_distance, 2)})
            continue
        gold = gold_points[best_index]
        unmatched_gold.remove(best_index)
        matches.append(
            {
                **candidate,
                "match_status": "tp",
                "gold_terminal_ref": gold.get("terminal_ref"),
                "gold_terminal_name": gold.get("terminal_name") or "",
                "distance": round(best_distance, 2),
            }
        )

    false_negatives = [{**gold_points[index], "match_status": "fn"} for index in sorted(unmatched_gold)]
    return matches, false_positives, false_negatives


def gold_symbols_from_project(project: dict[str, Any], classes: set[str]) -> list[dict[str, Any]]:
    width = int(project["image_width"])
    height = int(project["image_height"])
    symbols = []
    for symbol in project.get("symbols", []):
        class_name = str(symbol.get("class_key") or "")
        if class_name not in classes:
            continue
        box = symbol_bbox(symbol, width, height)
        gold_points = [
            {
                "gold_ref": symbol["ref"],
                "class_name": class_name,
                "label": symbol.get("label") or "",
                "terminal_ref": terminal.get("ref"),
                "terminal_name": terminal.get("name") or "",
                "point": [round(float(terminal["tx"]) * width, 1), round(float(terminal["ty"]) * height, 1)],
                "bbox": box,
            }
            for terminal in symbol.get("terminals") or []
        ]
        symbols.append(
            {
                "id": f"gold_{symbol['ref']}",
                "gold_ref": symbol["ref"],
                "class_name": class_name,
                "label": symbol.get("label") or "",
                "bbox": box,
                "gold_points": gold_points,
            }
        )
    return symbols


def draw_review(
    source_image: Image.Image,
    symbols: list[dict[str, Any]],
    matches: list[dict[str, Any]],
    false_positives: list[dict[str, Any]],
    false_negatives: list[dict[str, Any]],
    metrics: dict[str, Any],
) -> Image.Image:
    canvas = Image.blend(source_image.convert("RGB"), Image.new("RGB", source_image.size, "white"), 0.35)
    draw = ImageDraw.Draw(canvas)

    for symbol in symbols:
        box = symbol["bbox"]
        draw.rectangle(box_tuple(box), outline=SYMBOL_COLOR, width=2)
        draw.text((int(box["x0"]), max(0, int(box["y0"]) - 14)), f"{symbol['gold_ref']} {symbol['class_name']}", fill=SYMBOL_COLOR, font=FONT_XS)

    for item in false_negatives:
        x, y = map(float, item["point"])
        draw.ellipse((x - 7, y - 7, x + 7, y + 7), outline=FN_COLOR, width=4)
    for item in false_positives:
        x, y = map(float, item["point"])
        draw.rectangle((x - 6, y - 6, x + 6, y + 6), outline=FP_COLOR, width=4)
    for item in matches:
        x, y = map(float, item["point"])
        draw.ellipse((x - 6, y - 6, x + 6, y + 6), fill=TP_COLOR, outline="white", width=2)

    legend = [
        "green: matched inferred port",
        "red circle: missed gold terminal",
        "orange square: extra inferred port",
        f"precision={metrics['precision']} recall={metrics['recall']} f1={metrics['f1']}",
        f"tp/fp/fn={metrics['tp']}/{metrics['fp']}/{metrics['fn']}",
    ]
    x0, y0 = 14, 14
    max_w = max(draw.textbbox((0, 0), line, font=FONT_SM)[2] for line in legend)
    draw.rectangle((x0 - 6, y0 - 6, x0 + max_w + 14, y0 + 22 * len(legend) + 8), fill="white", outline=(80, 80, 80), width=2)
    for index, line in enumerate(legend):
        draw.text((x0, y0 + index * 22), line, fill=(20, 20, 20), font=FONT_SM)
    return canvas


def evaluate_page(
    page_dir: Path,
    project: dict[str, Any],
    *,
    target_classes: set[str],
    config: dict[str, Any],
    match_threshold_px: float,
    out_dir: Path,
) -> dict[str, Any]:
    payload = read_json(page_dir / "final_output.json")
    source_image = Image.open(resolve_artifact_path(payload["source"]["base_image"])).convert("RGB")
    wires = payload.get("wires", [])
    gold_symbols = gold_symbols_from_project(project, target_classes)

    page_matches: list[dict[str, Any]] = []
    page_fp: list[dict[str, Any]] = []
    page_fn: list[dict[str, Any]] = []
    rows: list[dict[str, Any]] = []
    by_class_counts: dict[str, Counter[str]] = defaultdict(Counter)

    for symbol in gold_symbols:
        candidates = infer_ports_for_symbol(symbol, wires, source_image, config=config)
        matches, false_positives, false_negatives = match_candidates(symbol["gold_points"], candidates, match_threshold_px)
        page_matches.extend(matches)
        page_fp.extend(false_positives)
        page_fn.extend(false_negatives)
        class_name = symbol["class_name"]
        by_class_counts[class_name]["tp"] += len(matches)
        by_class_counts[class_name]["fp"] += len(false_positives)
        by_class_counts[class_name]["fn"] += len(false_negatives)
        for item in matches + false_positives:
            rows.append(
                {
                    "status": item["match_status"],
                    "symbol_ref": symbol["gold_ref"],
                    "class_name": class_name,
                    "label": symbol["label"],
                    "terminal_ref": item.get("gold_terminal_ref") or "",
                    "terminal_name": item.get("gold_terminal_name") or "",
                    "x": item["point"][0],
                    "y": item["point"][1],
                    "wire_id": item.get("wire_id") or "",
                    "reason": item.get("reason") or "",
                    "distance": item.get("distance", item.get("nearest_gold_distance", "")),
                    "ink_ratio": item.get("ink_ratio", ""),
                }
            )
        for item in false_negatives:
            rows.append(
                {
                    "status": "fn",
                    "symbol_ref": symbol["gold_ref"],
                    "class_name": class_name,
                    "label": symbol["label"],
                    "terminal_ref": item.get("terminal_ref") or "",
                    "terminal_name": item.get("terminal_name") or "",
                    "x": item["point"][0],
                    "y": item["point"][1],
                    "wire_id": "",
                    "reason": "gold_terminal_not_matched",
                    "distance": "",
                    "ink_ratio": "",
                }
            )

    page_metrics = metric(len(page_matches), len(page_fp), len(page_fn))
    by_class = {
        class_name: metric(counts["tp"], counts["fp"], counts["fn"])
        for class_name, counts in sorted(by_class_counts.items())
    }
    page_output = {
        "page": payload["source"]["page"],
        "sheet_no": project.get("sheet_no"),
        "target_classes": sorted(target_classes),
        "config": config,
        "overall": page_metrics,
        "by_class": by_class,
        "gold_terminal_count": len(page_matches) + len(page_fn),
        "candidate_count": len(page_matches) + len(page_fp),
        "matches": page_matches[:100],
        "false_positives": page_fp[:100],
        "false_negatives": page_fn[:100],
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    write_json(out_dir / "port_inference_summary.json", page_output)
    with (out_dir / "port_inference_candidates.csv").open("w", newline="", encoding="utf-8-sig") as handle:
        fieldnames = [
            "status",
            "symbol_ref",
            "class_name",
            "label",
            "terminal_ref",
            "terminal_name",
            "x",
            "y",
            "wire_id",
            "reason",
            "distance",
            "ink_ratio",
        ]
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    draw_review(source_image, gold_symbols, page_matches, page_fp, page_fn, page_metrics).save(out_dir / "port_inference_review.png")
    return page_output


def aggregate_pages(pages: list[dict[str, Any]]) -> dict[str, Any]:
    counts = Counter()
    by_class_counts: dict[str, Counter[str]] = defaultdict(Counter)
    for page in pages:
        for key in ("tp", "fp", "fn"):
            counts[key] += int(page["overall"][key])
        for class_name, values in page["by_class"].items():
            for key in ("tp", "fp", "fn"):
                by_class_counts[class_name][key] += int(values[key])
    return {
        "overall": metric(counts["tp"], counts["fp"], counts["fn"]),
        "by_class": {
            class_name: metric(values["tp"], values["fp"], values["fn"])
            for class_name, values in sorted(by_class_counts.items())
        },
    }


def run_config(
    *,
    e2e_dir: Path,
    project_by_sheet: dict[str, dict[str, Any]],
    page_sheet_map: dict[str, str],
    pages: list[int],
    target_classes: set[str],
    config: dict[str, Any],
    match_threshold_px: float,
    out_dir: Path,
) -> dict[str, Any]:
    page_results = []
    for page in pages:
        sheet_no = page_sheet_map[str(page)]
        page_results.append(
            evaluate_page(
                e2e_dir / f"page_{page:03d}",
                project_by_sheet[sheet_no],
                target_classes=target_classes,
                config=config,
                match_threshold_px=match_threshold_px,
                out_dir=out_dir / f"page_{page:03d}",
            )
        )
    result = {
        "schema_version": "todensekkei.symbol_port_inference_experiment.v1",
        "e2e_dir": str(e2e_dir),
        "target_classes": sorted(target_classes),
        "match_threshold_px": match_threshold_px,
        "config": config,
        "aggregate": aggregate_pages(page_results),
        "pages": page_results,
    }
    write_json(out_dir / "summary.json", result)
    return result




def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Experiment with inferred symbol ports from detected wires.")
    parser.add_argument("--e2e-dir", type=Path, required=True)
    parser.add_argument("--annotation-zip", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--page-sheet-map", nargs="+", required=True)
    parser.add_argument("--pages", nargs="*", type=int, default=None)
    parser.add_argument("--target-classes", nargs="+", default=list(TARGET_PARENT_CLASSES))
    parser.add_argument("--match-threshold-px", type=float, default=14.0)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    bundle, _csv_rows, _netlist = load_zip_payload(args.annotation_zip)
    project_by_sheet = {str(project["sheet_no"]): project for project in bundle.get("projects", [])}
    page_sheet_map = parse_mapping(args.page_sheet_map)
    pages = args.pages or sorted(int(page) for page in page_sheet_map)
    target_classes = set(args.target_classes)

    results = []
    for config in default_configs():
        config_dir = args.out_dir / str(config["name"])
        results.append(
            run_config(
                e2e_dir=args.e2e_dir,
                project_by_sheet=project_by_sheet,
                page_sheet_map=page_sheet_map,
                pages=pages,
                target_classes=target_classes,
                config=config,
                match_threshold_px=args.match_threshold_px,
                out_dir=config_dir,
            )
        )

    rows = []
    for result in results:
        overall = result["aggregate"]["overall"]
        rows.append(
            {
                "config": result["config"]["name"],
                "precision": overall["precision"],
                "recall": overall["recall"],
                "f1": overall["f1"],
                "tp": overall["tp"],
                "fp": overall["fp"],
                "fn": overall["fn"],
                "by_class": json.dumps(result["aggregate"]["by_class"], ensure_ascii=False),
            }
        )
    args.out_dir.mkdir(parents=True, exist_ok=True)
    with (args.out_dir / "config_summary.csv").open("w", newline="", encoding="utf-8-sig") as handle:
        fieldnames = ["config", "precision", "recall", "f1", "tp", "fp", "fn", "by_class"]
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    write_json(
        args.out_dir / "config_summary.json",
        {
            "schema_version": "todensekkei.symbol_port_inference_config_summary.v1",
            "annotation_zip": str(args.annotation_zip),
            "e2e_dir": str(args.e2e_dir),
            "pages": pages,
            "target_classes": sorted(target_classes),
            "match_threshold_px": args.match_threshold_px,
            "configs": [
                {
                    "name": row["config"],
                    "aggregate": results[index]["aggregate"],
                    "config": results[index]["config"],
                    "summary_path": str(args.out_dir / row["config"] / "summary.json"),
                }
                for index, row in enumerate(rows)
            ],
        },
    )
    print(json.dumps(rows, ensure_ascii=False, indent=2))
    print(f"wrote {args.out_dir}")


if __name__ == "__main__":
    main()
