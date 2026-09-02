from __future__ import annotations

import argparse
import itertools
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from analyze_annotation_graph import build_project_graph, load_zip_payload, parse_member, symbol_bbox, write_json


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


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


def box_iou(a: dict[str, int | float], b: dict[str, int | float]) -> float:
    ax0, ay0, ax1, ay1 = float(a["x0"]), float(a["y0"]), float(a["x1"]), float(a["y1"])
    bx0, by0, bx1, by1 = float(b["x0"]), float(b["y0"]), float(b["x1"]), float(b["y1"])
    ix0 = max(ax0, bx0)
    iy0 = max(ay0, by0)
    ix1 = min(ax1, bx1)
    iy1 = min(ay1, by1)
    intersection = max(0.0, ix1 - ix0) * max(0.0, iy1 - iy0)
    area_a = max(0.0, ax1 - ax0) * max(0.0, ay1 - ay0)
    area_b = max(0.0, bx1 - bx0) * max(0.0, by1 - by0)
    union = area_a + area_b - intersection
    return intersection / union if union else 0.0


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


def class_metric(
    classes: list[str],
    matches: list[dict[str, Any]],
    false_positives: list[dict[str, Any]],
    false_negatives: list[dict[str, Any]],
) -> dict[str, Any]:
    tp_by_class = Counter(match["class_name"] for match in matches)
    fp_by_class = Counter(item["class_name"] for item in false_positives)
    fn_by_class = Counter(item["class_name"] for item in false_negatives)
    by_class: dict[str, Any] = {}
    for class_name in classes:
        class_matches = [match for match in matches if match["class_name"] == class_name]
        by_class[class_name] = {
            **metric(tp_by_class[class_name], fp_by_class[class_name], fn_by_class[class_name]),
            "mean_iou": round(sum(match["iou"] for match in class_matches) / len(class_matches), 4)
            if class_matches
            else None,
        }
    return {
        "overall": {
            **metric(len(matches), len(false_positives), len(false_negatives)),
            "mean_iou": round(sum(match["iou"] for match in matches) / len(matches), 4) if matches else None,
        },
        "by_class": by_class,
    }


def make_gold_symbols(project: dict[str, Any], classes: set[str]) -> list[dict[str, Any]]:
    width = int(project["image_width"])
    height = int(project["image_height"])
    symbols = []
    for symbol in project.get("symbols", []):
        class_name = str(symbol.get("class_key") or "")
        if class_name not in classes:
            continue
        symbols.append(
            {
                "ref": symbol["ref"],
                "class_name": class_name,
                "bbox": symbol_bbox(symbol, width, height),
                "label": symbol.get("label") or "",
            }
        )
    return symbols


def make_pred_symbols(payload: dict[str, Any], classes: set[str]) -> list[dict[str, Any]]:
    symbols = []
    for symbol in payload.get("symbols", []):
        class_name = str(symbol.get("class_name") or symbol.get("type") or "")
        if class_name not in classes:
            continue
        box = symbol.get("bbox")
        if not box:
            continue
        symbols.append(
            {
                "id": symbol["id"],
                "class_name": class_name,
                "bbox": box,
                "confidence": float(symbol.get("confidence", 0.0)),
            }
        )
    return symbols


def match_symbols(
    gold_symbols: list[dict[str, Any]],
    pred_symbols: list[dict[str, Any]],
    iou_threshold: float,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    unmatched_gold = set(range(len(gold_symbols)))
    matches: list[dict[str, Any]] = []
    false_positives: list[dict[str, Any]] = []

    for pred in sorted(pred_symbols, key=lambda item: item["confidence"], reverse=True):
        best_index: int | None = None
        best_iou = 0.0
        for gold_index in unmatched_gold:
            gold = gold_symbols[gold_index]
            if gold["class_name"] != pred["class_name"]:
                continue
            iou = box_iou(pred["bbox"], gold["bbox"])
            if iou > best_iou:
                best_iou = iou
                best_index = gold_index
        if best_index is None or best_iou < iou_threshold:
            false_positives.append(pred)
            continue
        gold = gold_symbols[best_index]
        unmatched_gold.remove(best_index)
        matches.append(
            {
                "pred_id": pred["id"],
                "gold_ref": gold["ref"],
                "class_name": gold["class_name"],
                "iou": round(best_iou, 4),
                "confidence": pred["confidence"],
            }
        )

    false_negatives = [gold_symbols[index] for index in sorted(unmatched_gold)]
    return matches, false_positives, false_negatives


def pair_key(left: str, right: str) -> tuple[str, str]:
    return tuple(sorted((left, right)))  # type: ignore[return-value]


def gold_connected_pairs(project: dict[str, Any], include_classes: set[str], expected_netlist: dict[str, Any] | None) -> set[tuple[str, str]]:
    graph = build_project_graph(project, expected_netlist)
    class_by_ref = {symbol["ref"]: symbol["class_key"] for symbol in project.get("symbols", [])}
    pairs: set[tuple[str, str]] = set()
    for component in graph.get("components", []):
        symbol_refs = sorted(
            {
                parse_member(member)[0]
                for member in component.get("members", [])
                if class_by_ref.get(parse_member(member)[0]) in include_classes
            }
        )
        for left, right in itertools.combinations(symbol_refs, 2):
            pairs.add(pair_key(left, right))
    return pairs


def predicted_connected_pairs(
    payload: dict[str, Any],
    pred_to_gold: dict[str, str],
) -> set[tuple[str, str]]:
    pairs: set[tuple[str, str]] = set()
    for net in payload.get("nets", []):
        symbol_refs = sorted({pred_to_gold[symbol_id] for symbol_id in net.get("symbol_ids", []) if symbol_id in pred_to_gold})
        for left, right in itertools.combinations(symbol_refs, 2):
            pairs.add(pair_key(left, right))
    return pairs


def validate_prediction_refs(payload: dict[str, Any]) -> dict[str, Any]:
    symbol_ids = {symbol["id"] for symbol in payload.get("symbols", [])}
    node_ids = {node["id"] for node in payload.get("nodes", [])}
    edge_ids = {edge["id"] for edge in payload.get("edges", [])}
    wire_ids = {wire["id"] for wire in payload.get("wires", [])}
    missing: Counter[str] = Counter()

    for link in payload.get("symbol_node_links", []):
        if link.get("symbol_id") not in symbol_ids:
            missing["symbol_node_links.symbol_id"] += 1
        if link.get("node_id") not in node_ids:
            missing["symbol_node_links.node_id"] += 1

    for link in payload.get("symbol_wire_links", []):
        if link.get("symbol_id") not in symbol_ids:
            missing["symbol_wire_links.symbol_id"] += 1
        if link.get("wire_id") not in wire_ids:
            missing["symbol_wire_links.wire_id"] += 1

    for net in payload.get("nets", []):
        for symbol_id in net.get("symbol_ids", []):
            if symbol_id not in symbol_ids:
                missing["nets.symbol_ids"] += 1
        for node_id in net.get("node_ids", []):
            if node_id not in node_ids:
                missing["nets.node_ids"] += 1
        for edge_id in net.get("edge_ids", []):
            if edge_id not in edge_ids:
                missing["nets.edge_ids"] += 1
        for wire_id in net.get("wire_ids", []):
            if wire_id not in wire_ids:
                missing["nets.wire_ids"] += 1

    return {
        "valid": not missing,
        "missing_reference_counts": dict(sorted(missing.items())),
    }


def page_from_payload(payload: dict[str, Any]) -> str:
    source = payload.get("source") or {}
    page = source.get("page")
    if page is None:
        raise ValueError("prediction JSON does not contain source.page")
    return str(int(page))


def evaluate_one(
    prediction_json: Path,
    *,
    project_by_sheet: dict[str, dict[str, Any]],
    expected_netlist_by_project: dict[str, Any],
    page_sheet_map: dict[str, str],
    split_map: dict[str, str],
    include_classes: list[str],
    symbol_iou_thresholds: list[float],
    graph_iou_threshold: float,
) -> dict[str, Any]:
    payload = read_json(prediction_json)
    page = page_from_payload(payload)
    sheet_no = page_sheet_map.get(page)
    if not sheet_no:
        raise ValueError(f"missing page-sheet mapping for page {page}")
    if sheet_no not in project_by_sheet:
        raise ValueError(f"sheet not found in annotation ZIP: {sheet_no}")

    project = project_by_sheet[sheet_no]
    include_set = set(include_classes)
    gold = make_gold_symbols(project, include_set)
    pred = make_pred_symbols(payload, include_set)

    symbol_metrics: dict[str, Any] = {}
    matches_by_threshold: dict[float, list[dict[str, Any]]] = {}
    for threshold in symbol_iou_thresholds:
        matches, false_positives, false_negatives = match_symbols(gold, pred, threshold)
        matches_by_threshold[threshold] = matches
        symbol_metrics[str(threshold)] = class_metric(include_classes, matches, false_positives, false_negatives)

    graph_matches = matches_by_threshold.get(graph_iou_threshold)
    if graph_matches is None:
        graph_matches, _, _ = match_symbols(gold, pred, graph_iou_threshold)
    pred_to_gold = {match["pred_id"]: match["gold_ref"] for match in graph_matches}

    expected_netlist = expected_netlist_by_project.get(str(project["id"]))
    gold_pairs = gold_connected_pairs(project, include_set, expected_netlist)
    pred_pairs = predicted_connected_pairs(payload, pred_to_gold)
    pair_tp = gold_pairs & pred_pairs
    pair_fp = pred_pairs - gold_pairs
    pair_fn = gold_pairs - pred_pairs

    return {
        "prediction_json": str(prediction_json),
        "page": int(page),
        "sheet_no": sheet_no,
        "split": split_map.get(page, "unknown"),
        "include_classes": include_classes,
        "gold_symbol_count": len(gold),
        "pred_symbol_count": len(pred),
        "pred_quality": payload.get("quality", {}),
        "symbol_metrics_by_iou": symbol_metrics,
        "graph_evaluation": {
            "basis": "symbol-pair connectivity after IoU-based symbol matching",
            "symbol_match_iou_threshold": graph_iou_threshold,
            "gold_pair_count": len(gold_pairs),
            "pred_pair_count": len(pred_pairs),
            **metric(len(pair_tp), len(pair_fp), len(pair_fn)),
            "tp_sample": [list(item) for item in sorted(pair_tp)[:30]],
            "fp_sample": [list(item) for item in sorted(pair_fp)[:30]],
            "fn_sample": [list(item) for item in sorted(pair_fn)[:30]],
        },
        "reference_validation": validate_prediction_refs(payload),
    }


def aggregate_pages(pages: list[dict[str, Any]], include_classes: list[str], symbol_thresholds: list[float]) -> dict[str, Any]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    groups["all"] = pages
    for page in pages:
        groups[page["split"]].append(page)

    output: dict[str, Any] = {}
    for group_name, group_pages in sorted(groups.items()):
        if not group_pages:
            continue
        group_result: dict[str, Any] = {"page_count": len(group_pages)}
        for threshold in symbol_thresholds:
            threshold_key = str(threshold)
            overall_counts = Counter()
            class_counts: dict[str, Counter[str]] = {class_name: Counter() for class_name in include_classes}
            for page in group_pages:
                metrics = page["symbol_metrics_by_iou"][threshold_key]
                for key in ("tp", "fp", "fn"):
                    overall_counts[key] += int(metrics["overall"][key])
                for class_name in include_classes:
                    class_metric_item = metrics["by_class"][class_name]
                    for key in ("tp", "fp", "fn"):
                        class_counts[class_name][key] += int(class_metric_item[key])
            group_result.setdefault("symbol_metrics_by_iou", {})[threshold_key] = {
                "overall": metric(overall_counts["tp"], overall_counts["fp"], overall_counts["fn"]),
                "by_class": {
                    class_name: metric(counts["tp"], counts["fp"], counts["fn"]) for class_name, counts in class_counts.items()
                },
            }

        graph_counts = Counter()
        for page in group_pages:
            graph = page["graph_evaluation"]
            for key in ("tp", "fp", "fn"):
                graph_counts[key] += int(graph[key])
        group_result["graph_evaluation"] = metric(graph_counts["tp"], graph_counts["fp"], graph_counts["fn"])
        group_result["graph_evaluation"]["basis"] = "aggregated symbol-pair connectivity"
        output[group_name] = group_result
    return output


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Evaluate YOLO + wire graph JSON against seq-annotator gold data.")
    parser.add_argument("--annotation-zip", type=Path, required=True)
    parser.add_argument("--prediction-json", type=Path, nargs="+", required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--out-name", default="evaluation.json")
    parser.add_argument("--include-classes", nargs="+", required=True)
    parser.add_argument("--page-sheet-map", nargs="+", required=True, help="Example: 3=SHEET001")
    parser.add_argument("--split-map", nargs="*", default=[], help="Example: 18=test")
    parser.add_argument("--symbol-iou-thresholds", nargs="+", type=float, default=[0.3, 0.5])
    parser.add_argument("--graph-iou-threshold", type=float, default=0.3)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    bundle, _csv_rows, netlist = load_zip_payload(args.annotation_zip)
    projects = bundle.get("projects", [])
    project_by_sheet = {str(project["sheet_no"]): project for project in projects}
    expected_netlist_by_project = {str(project["project_id"]): project for project in netlist.get("projects", [])}
    page_sheet_map = parse_mapping(args.page_sheet_map)
    split_map = parse_mapping(args.split_map)

    page_results = [
        evaluate_one(
            path,
            project_by_sheet=project_by_sheet,
            expected_netlist_by_project=expected_netlist_by_project,
            page_sheet_map=page_sheet_map,
            split_map=split_map,
            include_classes=args.include_classes,
            symbol_iou_thresholds=args.symbol_iou_thresholds,
            graph_iou_threshold=args.graph_iou_threshold,
        )
        for path in args.prediction_json
    ]
    result = {
        "schema_version": "todensekkei.inferred_graph_evaluation.v1",
        "annotation_zip": str(args.annotation_zip),
        "include_classes": args.include_classes,
        "symbol_iou_thresholds": args.symbol_iou_thresholds,
        "graph_iou_threshold": args.graph_iou_threshold,
        "page_results": sorted(page_results, key=lambda item: item["page"]),
        "aggregate": aggregate_pages(page_results, args.include_classes, args.symbol_iou_thresholds),
        "notes": [
            "Symbol detection is evaluated by class-aware IoU matching.",
            "Graph evaluation is a baseline symbol-level pair metric, not terminal-level from-to scoring.",
            "A predicted connection is counted when two correctly matched symbols appear in the same predicted net.",
        ],
    }
    output_path = args.out_dir / args.out_name
    write_json(output_path, result)
    print(json.dumps(result["aggregate"], ensure_ascii=False, indent=2))
    print(f"wrote {output_path}")


if __name__ == "__main__":
    main()
