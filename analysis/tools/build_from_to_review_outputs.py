from __future__ import annotations

import _repo_path  # noqa: F401  (リポジトリの直下を import できるようにする)

import argparse
import csv
import json
from itertools import pairwise
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw

from analyze_annotation_graph import load_zip_payload, symbol_bbox
from build_e2e_demo_outputs import load_font
from analysis.wiring.connectivity import build_predicted_graph_pairs, sorted_pair  # noqa: E402
from analysis.wiring.geometry import point_distance  # noqa: E402


FONT_XS = load_font(12)
FONT_SM = load_font(15)
FONT_MD = load_font(18)

OK_COLOR = (37, 99, 235)
NG_COLOR = (220, 38, 38)
EXTRA_COLOR = (217, 119, 6)
INDIRECT_COLOR = (115, 115, 115)
TERMINAL_MISSING_COLOR = (22, 163, 74)
GRAY_COLOR = (110, 110, 110)


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
        ("data/private/yolo/", "private/training/yolo/"),
        ("data/private/annotation_fixes/", "private/inputs/annotation_fixes/"),
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
        mapping[key.strip()] = value.strip()
    return mapping


def terminal_member_key(symbol_ref: str, terminal_ref: str | None) -> str:
    return f"{symbol_ref}:{terminal_ref}" if terminal_ref else symbol_ref




















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


def box_area(box: dict[str, int | float]) -> float:
    return max(0.0, float(box["x1"]) - float(box["x0"])) * max(0.0, float(box["y1"]) - float(box["y0"]))


def box_intersection_area(a: dict[str, int | float], b: dict[str, int | float]) -> float:
    x0 = max(float(a["x0"]), float(b["x0"]))
    y0 = max(float(a["y0"]), float(b["y0"]))
    x1 = min(float(a["x1"]), float(b["x1"]))
    y1 = min(float(a["y1"]), float(b["y1"]))
    return max(0.0, x1 - x0) * max(0.0, y1 - y0)


def box_iou(a: dict[str, int | float], b: dict[str, int | float]) -> float:
    intersection = box_intersection_area(a, b)
    union = box_area(a) + box_area(b) - intersection
    return intersection / max(1.0, union)


def min_overlap_ratio(a: dict[str, int | float], b: dict[str, int | float]) -> float:
    return box_intersection_area(a, b) / max(1.0, min(box_area(a), box_area(b)))


def attach_eval_gold_refs(
    payload: dict[str, Any],
    project: dict[str, Any],
    include_classes: set[str],
    exclude_classes: set[str],
    *,
    iou_threshold: float,
    min_overlap_threshold: float,
) -> dict[str, Any]:
    width = int(project["image_width"])
    height = int(project["image_height"])
    gold_by_ref = {
        str(symbol["ref"]): {
            "ref": str(symbol["ref"]),
            "class_name": str(symbol.get("class_key") or ""),
            "label": symbol.get("label") or "",
            "bbox": symbol_bbox(symbol, width, height),
        }
        for symbol in project.get("symbols", [])
        if str(symbol.get("class_key") or "") in include_classes and str(symbol.get("class_key") or "") not in exclude_classes
    }
    used_gold_refs = {str(symbol.get("gold_ref")) for symbol in payload.get("symbols", []) if symbol.get("gold_ref")}
    symbols = []
    mapped = 0
    already_mapped = 0
    unmapped = 0
    for symbol in payload.get("symbols", []):
        class_name = str(symbol.get("class_name") or symbol.get("type") or "")
        if class_name not in include_classes or class_name in exclude_classes:
            symbols.append(symbol)
            continue
        if symbol.get("gold_ref"):
            already_mapped += 1
            symbols.append(symbol)
            continue
        pred_box = symbol.get("bbox")
        if not pred_box:
            symbols.append(symbol)
            unmapped += 1
            continue
        best_ref: str | None = None
        best_iou = 0.0
        best_min_overlap = 0.0
        best_score = -1.0
        for ref, gold in gold_by_ref.items():
            if ref in used_gold_refs:
                continue
            if gold["class_name"] != class_name:
                continue
            iou = box_iou(pred_box, gold["bbox"])
            min_overlap = min_overlap_ratio(pred_box, gold["bbox"])
            if iou < iou_threshold and min_overlap < min_overlap_threshold:
                continue
            score = iou + min_overlap + float(symbol.get("confidence") or 0.0) * 0.01
            if score > best_score:
                best_ref = ref
                best_iou = iou
                best_min_overlap = min_overlap
                best_score = score
        if best_ref is None:
            symbols.append(symbol)
            unmapped += 1
            continue
        used_gold_refs.add(best_ref)
        mapped_symbol = {
            **symbol,
            "gold_ref": best_ref,
            "eval_gold_match": {
                "method": "same_class_bbox_match",
                "iou": round(best_iou, 4),
                "min_overlap": round(best_min_overlap, 4),
                "thresholds": {
                    "iou": iou_threshold,
                    "min_overlap": min_overlap_threshold,
                },
            },
        }
        if not mapped_symbol.get("label"):
            mapped_symbol["label"] = gold_by_ref[best_ref]["label"]
        symbols.append(mapped_symbol)
        mapped += 1
    return {
        **payload,
        "symbols": symbols,
        "eval_symbol_gold_mapping": {
            "method": "evaluation-only same-class bbox matching; predicted bbox/terminals are not replaced",
            "iou_threshold": iou_threshold,
            "min_overlap_threshold": min_overlap_threshold,
            "already_had_gold_ref": already_mapped,
            "newly_mapped_count": mapped,
            "unmapped_count": unmapped,
            "used_gold_ref_count": len(used_gold_refs),
        },
    }


def make_gold_member_info(
    project: dict[str, Any],
    include_classes: set[str],
    exclude_classes: set[str],
) -> dict[str, dict[str, Any]]:
    width = int(project["image_width"])
    height = int(project["image_height"])
    member_info: dict[str, dict[str, Any]] = {}
    for symbol in project.get("symbols", []):
        class_name = str(symbol.get("class_key") or "")
        if class_name in exclude_classes or class_name not in include_classes:
            continue
        bbox = symbol_bbox(symbol, width, height)
        terminals = symbol.get("terminals") or []
        if not terminals:
            key = terminal_member_key(str(symbol["ref"]), None)
            member_info[key] = {
                "symbol_ref": symbol["ref"],
                "terminal_ref": None,
                "terminal_name": "",
                "class_name": class_name,
                "label": symbol.get("label") or "",
                "bbox": bbox,
                "point": [round(float(symbol["cx"]) * width, 1), round(float(symbol["cy"]) * height, 1)],
            }
            continue
        for terminal in terminals:
            key = terminal_member_key(str(symbol["ref"]), terminal.get("ref"))
            member_info[key] = {
                "symbol_ref": symbol["ref"],
                "terminal_ref": terminal.get("ref"),
                "terminal_name": terminal.get("name") or "",
                "class_name": class_name,
                "label": symbol.get("label") or "",
                "bbox": bbox,
                "point": [
                    round(float(terminal["tx"]) * width, 1),
                    round(float(terminal["ty"]) * height, 1),
                ],
            }
    return member_info


def make_gold_direct_pairs(
    project: dict[str, Any],
    member_info: dict[str, dict[str, Any]],
    exclude_classes: set[str],
) -> set[tuple[str, str]]:
    class_by_ref = {str(symbol["ref"]): str(symbol.get("class_key") or "") for symbol in project.get("symbols", [])}
    pairs: set[tuple[str, str]] = set()
    for connection in project.get("connections", []):
        from_ref = str(connection.get("from_symbol_ref") or "")
        to_ref = str(connection.get("to_symbol_ref") or "")
        if class_by_ref.get(from_ref) in exclude_classes or class_by_ref.get(to_ref) in exclude_classes:
            continue
        left = terminal_member_key(from_ref, connection.get("from_terminal_ref"))
        right = terminal_member_key(to_ref, connection.get("to_terminal_ref"))
        if left == right:
            continue
        if left not in member_info or right not in member_info:
            continue
        pairs.add(sorted_pair(left, right))
    return pairs


def nearest_gold_member_key(
    symbol_ref: str,
    point: list[float],
    gold_by_symbol: dict[str, list[tuple[str, list[float]]]],
    threshold: float,
) -> str | None:
    candidates = gold_by_symbol.get(symbol_ref, [])
    if not candidates:
        return symbol_ref
    best_key, best_point = min(candidates, key=lambda item: point_distance(point, item[1]))
    if point_distance(point, best_point) <= threshold:
        return best_key
    return None




def make_predicted_terminals(
    payload: dict[str, Any],
    member_info: dict[str, dict[str, Any]],
    include_classes: set[str],
    exclude_classes: set[str],
    terminal_match_threshold: float,
) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    gold_by_symbol: dict[str, list[tuple[str, list[float]]]] = defaultdict(list)
    for key, info in member_info.items():
        gold_by_symbol[str(info["symbol_ref"])].append((key, info["point"]))

    records: list[dict[str, Any]] = []
    pred_member_info: dict[str, dict[str, Any]] = {}
    for symbol in payload.get("symbols", []):
        class_name = str(symbol.get("class_name") or symbol.get("type") or "")
        if class_name in exclude_classes or class_name not in include_classes:
            continue
        gold_ref = symbol.get("gold_ref")
        if not gold_ref:
            continue
        links_by_terminal: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for link in symbol.get("terminal_links", []):
            links_by_terminal[str(link.get("terminal_ref") or "")].append(link)
        for index, terminal in enumerate(symbol.get("terminals") or [], start=1):
            terminal_ref = str(terminal.get("ref") or "")
            point = [float(terminal["point"][0]), float(terminal["point"][1])]
            mapped_key = nearest_gold_member_key(str(gold_ref), point, gold_by_symbol, terminal_match_threshold)
            if not mapped_key:
                mapped_key = f"PRED:{symbol['id']}:{terminal_ref or index}"
            pred_member_info.setdefault(
                mapped_key,
                {
                    "symbol_ref": gold_ref,
                    "terminal_ref": terminal_ref,
                    "terminal_name": terminal.get("name") or "",
                    "class_name": class_name,
                    "label": symbol.get("label") or "",
                    "bbox": symbol.get("bbox"),
                    "point": point,
                },
            )
            links = links_by_terminal.get(terminal_ref) or [{}]
            for link_index, link in enumerate(links, start=1):
                records.append(
                    {
                        "id": f"term_{len(records) + 1:05d}",
                        "member_key": mapped_key,
                        "symbol_id": symbol["id"],
                        "class_name": class_name,
                        "point": point,
                        "wire_id": link.get("wire_id"),
                        "wire_id_hint": terminal.get("wire_id_hint"),
                        "node_id": link.get("node_id"),
                        "link_index": link_index,
                    }
                )
    return records, pred_member_info








def connected_by_member(pairs: set[tuple[str, str]]) -> dict[str, set[str]]:
    connected: dict[str, set[str]] = defaultdict(set)
    for left, right in pairs:
        connected[left].add(right)
        connected[right].add(left)
    return connected


def component_by_member(pairs: set[tuple[str, str]]) -> dict[str, str]:
    parent: dict[str, str] = {}

    def find(item: str) -> str:
        parent.setdefault(item, item)
        if parent[item] != item:
            parent[item] = find(parent[item])
        return parent[item]

    def union(left: str, right: str) -> None:
        left_root = find(left)
        right_root = find(right)
        if left_root != right_root:
            parent[right_root] = left_root

    for left, right in pairs:
        union(left, right)

    groups: dict[str, list[str]] = defaultdict(list)
    for member in sorted(parent):
        groups[find(member)].append(member)

    output: dict[str, str] = {}
    for index, members in enumerate(sorted(groups.values(), key=lambda items: items[0]), start=1):
        component_id = f"component_{index:04d}"
        for member in members:
            output[member] = component_id
    return output


def split_extra_pairs(
    raw_extra: set[tuple[str, str]],
    gold_pairs: set[tuple[str, str]],
    member_info: dict[str, dict[str, Any]],
) -> tuple[set[tuple[str, str]], set[tuple[str, str]]]:
    gold_component_by_member = component_by_member(gold_pairs)
    indirect: set[tuple[str, str]] = set()
    hard_extra: set[tuple[str, str]] = set()
    for left, right in raw_extra:
        if (
            left in member_info
            and right in member_info
            and gold_component_by_member.get(left)
            and gold_component_by_member.get(left) == gold_component_by_member.get(right)
        ):
            indirect.add((left, right))
        else:
            hard_extra.add((left, right))
    return hard_extra, indirect


def gold_path_through_classes(
    left: str,
    right: str,
    gold_pairs: set[tuple[str, str]],
    member_info: dict[str, dict[str, Any]],
    *,
    max_edges: int,
    intermediate_classes: set[str],
    internal_path_classes: set[str],
) -> list[tuple[str, str]]:
    if max_edges < 2:
        return []

    adjacency = connected_by_member(gold_pairs)
    queue: list[list[str]] = [[left]]
    visited: set[tuple[str, int]] = {(left, 0)}
    while queue:
        path = queue.pop(0)
        current = path[-1]
        edge_count = len(path) - 1
        if edge_count >= max_edges:
            continue
        for next_member in sorted(adjacency.get(current, set())):
            if next_member in path:
                continue
            if next_member != right:
                class_name = str(member_info.get(next_member, {}).get("class_name") or "")
                if class_name not in intermediate_classes and not is_allowed_path_intermediate(
                    next_member,
                    left,
                    right,
                    member_info,
                    internal_path_classes=internal_path_classes,
                ):
                    continue
            next_path = [*path, next_member]
            if next_member == right:
                return [sorted_pair(a, b) for a, b in pairwise(next_path)]
            state = (next_member, len(next_path) - 1)
            if state not in visited:
                visited.add(state)
                queue.append(next_path)
    return []


def split_excluded_intermediate_pairs(
    raw_extra: set[tuple[str, str]],
    full_gold_pairs: set[tuple[str, str]],
    full_member_info: dict[str, dict[str, Any]],
    evaluated_member_info: dict[str, dict[str, Any]],
    *,
    intermediate_classes: set[str],
    internal_path_classes: set[str],
    max_edges: int,
) -> set[tuple[str, str]]:
    if max_edges < 2 or not intermediate_classes:
        return set()

    tolerated: set[tuple[str, str]] = set()
    for left, right in raw_extra:
        if left not in evaluated_member_info or right not in evaluated_member_info:
            continue
        path = gold_path_through_classes(
            left,
            right,
            full_gold_pairs,
            full_member_info,
            max_edges=max_edges,
            intermediate_classes=intermediate_classes,
            internal_path_classes=internal_path_classes,
        )
        if path:
            tolerated.add((left, right))
    return tolerated


def is_junction_member(key: str, member_info: dict[str, dict[str, Any]]) -> bool:
    return str(member_info.get(key, {}).get("class_name") or "") == "junction"


def member_symbol_ref(key: str, member_info: dict[str, dict[str, Any]]) -> str:
    return str(member_info.get(key, {}).get("symbol_ref") or key.split(":", 1)[0])


def is_allowed_path_intermediate(
    key: str,
    left: str,
    right: str,
    member_info: dict[str, dict[str, Any]],
    *,
    internal_path_classes: set[str],
) -> bool:
    if is_junction_member(key, member_info):
        return True
    info = member_info.get(key)
    if not info:
        return False
    if str(info.get("class_name") or "") not in internal_path_classes:
        return False
    return str(info.get("symbol_ref") or "") in {
        member_symbol_ref(left, member_info),
        member_symbol_ref(right, member_info),
    }


def gold_path_through_junctions(
    left: str,
    right: str,
    gold_pairs: set[tuple[str, str]],
    member_info: dict[str, dict[str, Any]],
    *,
    max_edges: int,
    internal_path_classes: set[str],
) -> list[tuple[str, str]]:
    if max_edges < 2:
        return []

    adjacency = connected_by_member(gold_pairs)
    queue: list[list[str]] = [[left]]
    visited: set[tuple[str, int]] = {(left, 0)}
    while queue:
        path = queue.pop(0)
        current = path[-1]
        edge_count = len(path) - 1
        if edge_count >= max_edges:
            continue
        for next_member in sorted(adjacency.get(current, set())):
            if next_member in path:
                continue
            if next_member != right and not is_allowed_path_intermediate(
                next_member,
                left,
                right,
                member_info,
                internal_path_classes=internal_path_classes,
            ):
                continue
            next_path = [*path, next_member]
            if next_member == right:
                if len(next_path) <= 2:
                    return []
                return [sorted_pair(a, b) for a, b in pairwise(next_path)]
            state = (next_member, len(next_path) - 1)
            if state not in visited:
                visited.add(state)
                queue.append(next_path)
    return []


def apply_junction_path_tolerance(
    gold_pairs: set[tuple[str, str]],
    pred_pairs: set[tuple[str, str]],
    member_info: dict[str, dict[str, Any]],
    *,
    max_edges: int,
    internal_path_classes: set[str],
) -> dict[str, Any]:
    exact_pairs = gold_pairs & pred_pairs
    covered_gold_pairs = set(exact_pairs)
    tolerated_pred_pairs: set[tuple[str, str]] = set()
    coverage_rows: list[dict[str, Any]] = []

    if max_edges >= 2:
        for left, right in sorted(pred_pairs - gold_pairs):
            if left not in member_info or right not in member_info:
                continue
            path_edges = gold_path_through_junctions(
                left,
                right,
                gold_pairs,
                member_info,
                max_edges=max_edges,
                internal_path_classes=internal_path_classes,
            )
            if not path_edges:
                continue
            tolerated_pred_pairs.add((left, right))
            covered_gold_pairs.update(path_edges)
            coverage_rows.append(
                {
                    "pred_from": left,
                    "pred_to": right,
                    "covered_gold_pairs": [{"from": a, "to": b} for a, b in path_edges],
                }
            )

    return {
        "exact_pairs": exact_pairs,
        "covered_gold_pairs": covered_gold_pairs,
        "junction_path_covered_pairs": covered_gold_pairs - exact_pairs,
        "tolerated_pred_pairs": tolerated_pred_pairs,
        "coverage_rows": coverage_rows,
    }


def draw_text_box(draw: ImageDraw.ImageDraw, xy: tuple[int, int], lines: list[str]) -> None:
    x, y = xy
    line_h = 22
    width = max(draw.textbbox((0, 0), line, font=FONT_SM)[2] for line in lines)
    draw.rectangle((x - 8, y - 8, x + width + 16, y + line_h * len(lines) + 8), fill="white", outline=(80, 80, 80), width=2)
    for index, line in enumerate(lines):
        draw.text((x, y + index * line_h), line, fill=(20, 20, 20), font=FONT_SM)


def draw_title(panel: Image.Image, title: str, subtitle: str | None = None) -> Image.Image:
    header_h = 54
    output = Image.new("RGB", (panel.width, panel.height + header_h), "white")
    output.paste(panel, (0, header_h))
    draw = ImageDraw.Draw(output)
    draw.text((14, 8), title, fill=(20, 20, 20), font=FONT_MD)
    if subtitle:
        draw.text((14, 31), subtitle, fill=(75, 75, 75), font=FONT_SM)
    return output


def make_side_by_side(left: Image.Image, right: Image.Image) -> Image.Image:
    cell_w = max(left.width, right.width)
    cell_h = max(left.height, right.height)
    sheet = Image.new("RGB", (cell_w * 2, cell_h), (235, 235, 235))
    sheet.paste(left, (0, 0))
    sheet.paste(right, (cell_w, 0))
    return sheet


def draw_from_to_review(
    source: Image.Image,
    out_path: Path,
    *,
    gold_pairs: set[tuple[str, str]],
    pred_pairs: set[tuple[str, str]],
    ok_pairs: set[tuple[str, str]],
    missing_pairs: set[tuple[str, str]],
    hard_extra_pairs: set[tuple[str, str]],
    indirect_pairs: set[tuple[str, str]],
    detected_member_keys: set[str],
    endpoint_status_by_key: dict[str, str],
    member_info: dict[str, dict[str, Any]],
    pred_member_info: dict[str, dict[str, Any]],
    summary: dict[str, Any],
) -> None:
    canvas = Image.blend(source.convert("RGB"), Image.new("RGB", source.size, "white"), 0.18)
    draw = ImageDraw.Draw(canvas)
    all_info = {**pred_member_info, **member_info}

    def point_for(key: str) -> tuple[int, int] | None:
        info = all_info.get(key)
        if not info or not info.get("point"):
            return None
        return round(float(info["point"][0])), round(float(info["point"][1]))

    for left, right in sorted(ok_pairs):
        p1 = point_for(left)
        p2 = point_for(right)
        if p1 and p2:
            draw.line((p1, p2), fill=OK_COLOR, width=2)
    for left, right in sorted(missing_pairs):
        p1 = point_for(left)
        p2 = point_for(right)
        if p1 and p2:
            draw.line((p1, p2), fill=NG_COLOR, width=3)
    for left, right in sorted(hard_extra_pairs):
        p1 = point_for(left)
        p2 = point_for(right)
        if p1 and p2:
            draw.line((p1, p2), fill=EXTRA_COLOR, width=3)

    gold_connected = connected_by_member(gold_pairs)
    pred_connected = connected_by_member(pred_pairs)
    status_counts: Counter[str] = Counter()
    for key in member_info:
        status = endpoint_status_by_key.get(key)
        if not status:
            if key not in detected_member_keys:
                status = "terminal_missing"
            elif gold_connected.get(key, set()) == pred_connected.get(key, set()):
                status = "ok"
            else:
                status = "ng"
        status_counts[status] += 1
        x, y = point_for(key) or (0, 0)
        if status == "ok":
            color = OK_COLOR
        elif status == "terminal_missing":
            color = TERMINAL_MISSING_COLOR
        else:
            color = NG_COLOR
        draw.ellipse((x - 7, y - 7, x + 7, y + 7), fill=color, outline="white", width=2)

    pred_only_keys = (set(pred_connected) - set(member_info)) & {key for pair in hard_extra_pairs for key in pair}
    for key in pred_only_keys:
        point = point_for(key)
        if not point:
            continue
        x, y = point
        draw.ellipse((x - 7, y - 7, x + 7, y + 7), fill=EXTRA_COLOR, outline="white", width=2)

    lines = [
        "line: blue OK / orange extra / red missing",
        "point: blue OK / red NG / green terminal missing",
        f"precision {summary['precision']}  recall {summary['recall']}  f1 {summary['f1']}",
        f"ok/extra/missing/gold-path {len(ok_pairs)} / {summary['hard_fp']} / {len(missing_pairs)} / {summary['excluded_intermediate']}",
        f"terminals ok/ng/missing {status_counts.get('ok', 0)} / {status_counts.get('ng', 0)} / {status_counts.get('terminal_missing', 0)}",
    ]
    draw_text_box(draw, (18, 18), lines)

    original_panel = draw_title(source.convert("RGB"), "Original", "source drawing")
    review_panel = draw_title(canvas, "From-to Review", "blue OK / orange extra / red missing")
    output = make_side_by_side(original_panel, review_panel)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    output.save(out_path)


def write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def member_label(key: str, info: dict[str, dict[str, Any]]) -> str:
    item = info.get(key) or {}
    label = " ".join(str(item.get("label") or "").split())
    return f"{key} {item.get('class_name', '')} {label}".strip()


def process_page(
    page_dir: Path,
    *,
    page: int,
    sheet_no: str,
    project: dict[str, Any],
    include_classes: set[str],
    exclude_classes: set[str],
    terminal_match_threshold: float,
    internal_bridge_classes: set[str],
    node_anchor_classes: set[str],
    node_anchor_threshold: float,
    terminal_gap_bridge_classes: set[str],
    terminal_gap_bridge_max: float,
    wire_gap_bridge_max: float,
    gap_bridge_align_threshold: float,
    wire_gap_bridge_terminal_anchor_threshold: float,
    terminal_terminal_bridge_classes: set[str],
    terminal_terminal_bridge_max: float,
    gap_bridge_text_overlap_max: float,
    gap_bridge_symbol_overlap_max: float,
    junction_path_tolerance_max_edges: int,
    path_tolerance_internal_classes: set[str],
    excluded_intermediate_classes: set[str],
    excluded_intermediate_tolerance_max_edges: int,
    endpoint_status_mode: str,
    eval_symbol_match_iou: float,
    eval_symbol_match_min_overlap: float,
    nonconductive_anchor_policy: str,
    out_dir: Path,
) -> dict[str, Any]:
    payload = read_json(page_dir / "final_output.json")
    payload = attach_eval_gold_refs(
        payload,
        project,
        include_classes,
        exclude_classes,
        iou_threshold=eval_symbol_match_iou,
        min_overlap_threshold=eval_symbol_match_min_overlap,
    )
    source = Image.open(resolve_artifact_path(payload["source"]["base_image"])).convert("RGB")
    member_info = make_gold_member_info(project, include_classes, exclude_classes)
    gold_pairs = make_gold_direct_pairs(project, member_info, exclude_classes)
    full_member_info = make_gold_member_info(project, include_classes | excluded_intermediate_classes, set())
    full_gold_pairs = make_gold_direct_pairs(project, full_member_info, set())
    terminal_records, pred_member_info = make_predicted_terminals(
        payload,
        member_info,
        include_classes,
        exclude_classes,
        terminal_match_threshold,
    )
    detected_member_keys = set(pred_member_info) & set(member_info)
    bridge_stats: Counter[str] = Counter()
    pred_pairs = build_predicted_graph_pairs(
        payload,
        terminal_records,
        internal_bridge_classes=internal_bridge_classes,
        node_anchor_classes=node_anchor_classes,
        node_anchor_threshold=node_anchor_threshold,
        terminal_gap_bridge_classes=terminal_gap_bridge_classes,
        terminal_gap_bridge_max=terminal_gap_bridge_max,
        wire_gap_bridge_max=wire_gap_bridge_max,
        gap_bridge_align_threshold=gap_bridge_align_threshold,
        wire_gap_bridge_terminal_anchor_threshold=wire_gap_bridge_terminal_anchor_threshold,
        terminal_terminal_bridge_classes=terminal_terminal_bridge_classes,
        terminal_terminal_bridge_max=terminal_terminal_bridge_max,
        gap_bridge_text_overlap_max=gap_bridge_text_overlap_max,
        gap_bridge_symbol_overlap_max=gap_bridge_symbol_overlap_max,
        nonconductive_anchor_policy=nonconductive_anchor_policy,
        bridge_stats=bridge_stats,
    )
    pred_pairs = {
        pair
        for pair in pred_pairs
        if pair[0] in member_info or pair[0] in pred_member_info
        if pair[1] in member_info or pair[1] in pred_member_info
    }

    direct_tp = gold_pairs & pred_pairs
    direct_raw_extra_pairs = pred_pairs - gold_pairs
    direct_fn = gold_pairs - pred_pairs
    coverage = apply_junction_path_tolerance(
        gold_pairs,
        pred_pairs,
        member_info,
        max_edges=junction_path_tolerance_max_edges,
        internal_path_classes=path_tolerance_internal_classes,
    )
    tp = coverage["covered_gold_pairs"]
    raw_extra_pairs = pred_pairs - gold_pairs - coverage["tolerated_pred_pairs"]
    excluded_intermediate_pairs = split_excluded_intermediate_pairs(
        raw_extra_pairs,
        full_gold_pairs,
        full_member_info,
        member_info,
        intermediate_classes=excluded_intermediate_classes,
        internal_path_classes=path_tolerance_internal_classes,
        max_edges=excluded_intermediate_tolerance_max_edges,
    )
    raw_extra_pairs = raw_extra_pairs - excluded_intermediate_pairs
    hard_extra_pairs, indirect_pairs = split_extra_pairs(raw_extra_pairs, gold_pairs, member_info)
    fn = gold_pairs - tp
    pair_metric = metric(len(tp), len(hard_extra_pairs), len(fn))
    strict_metric = metric(len(direct_tp), len(direct_raw_extra_pairs), len(direct_fn))
    gold_connected = connected_by_member(gold_pairs)
    pred_connected = connected_by_member(pred_pairs)
    covered_connected = connected_by_member(tp)
    hard_extra_connected = connected_by_member(hard_extra_pairs)
    endpoint_status: Counter[str] = Counter()
    endpoint_status_by_key: dict[str, str] = {}
    for key in member_info:
        if key not in detected_member_keys:
            status = "terminal_missing"
        elif endpoint_status_mode == "covered":
            missing_partners = gold_connected.get(key, set()) - covered_connected.get(key, set())
            hard_extra_partners = hard_extra_connected.get(key, set())
            status = "ok" if not missing_partners and not hard_extra_partners else "ng"
        elif gold_connected.get(key, set()) == pred_connected.get(key, set()):
            status = "ok"
        else:
            status = "ng"
        endpoint_status[status] += 1
        endpoint_status_by_key[key] = status

    page_out = out_dir / f"page_{page:03d}"
    review_png = page_out / "from_to_error_review.png"
    draw_summary = {
        **pair_metric,
        "hard_fp": len(hard_extra_pairs),
        "indirect": len(indirect_pairs),
        "excluded_intermediate": len(excluded_intermediate_pairs),
    }
    draw_from_to_review(
        source,
        review_png,
        gold_pairs=gold_pairs,
        pred_pairs=pred_pairs,
        ok_pairs=set(direct_tp) | set(coverage["junction_path_covered_pairs"]),
        missing_pairs=fn,
        hard_extra_pairs=hard_extra_pairs,
        indirect_pairs=indirect_pairs,
        detected_member_keys=detected_member_keys,
        endpoint_status_by_key=endpoint_status_by_key,
        member_info=member_info,
        pred_member_info=pred_member_info,
        summary=draw_summary,
    )

    edge_rows = []
    all_info = {**pred_member_info, **member_info}
    for status, pairs in (
        ("ok", sorted(direct_tp)),
        ("junction_path_covered", sorted(coverage["junction_path_covered_pairs"])),
        ("missing", sorted(fn)),
        ("extra", sorted(hard_extra_pairs)),
        ("indirect", sorted(indirect_pairs)),
        ("excluded_intermediate", sorted(excluded_intermediate_pairs)),
    ):
        for left, right in pairs:
            edge_rows.append(
                {
                    "page": page,
                    "sheet_no": sheet_no,
                    "status": status,
                    "from": left,
                    "to": right,
                    "from_label": member_label(left, all_info),
                    "to_label": member_label(right, all_info),
                }
            )
    write_csv(
        page_out / "from_to_edges.csv",
        edge_rows,
        ["page", "sheet_no", "status", "from", "to", "from_label", "to_label"],
    )
    write_json(
        page_out / "from_to_edges.json",
        {
            "schema_version": "todensekkei.from_to_edges.v1",
            "page": page,
            "sheet_no": sheet_no,
            "definition": "direct from-to pairs; ok/missing/extra/indirect are compared against annotation connections",
            "status_legend": {
                "ok": "predicted direct from-to pair matches annotation",
                "junction_path_covered": "annotation direct pair was covered by a predicted pair across a gold junction-only path",
                "missing": "annotation direct from-to pair was not predicted",
                "extra": "predicted direct from-to pair is not in annotation and not explainable as same gold component",
                "indirect": "predicted direct pair is not direct in annotation but belongs to the same gold connected component",
                "excluded_intermediate": "predicted pair is explained by a gold path through allowed intermediate symbols",
            },
            "junction_path_tolerance": {
                "max_edges": junction_path_tolerance_max_edges,
                "internal_classes": sorted(path_tolerance_internal_classes),
                "covered_pair_count": len(coverage["junction_path_covered_pairs"]),
                "tolerated_pred_pair_count": len(coverage["tolerated_pred_pairs"]),
                "coverage_examples": coverage["coverage_rows"][:100],
            },
            "edges": edge_rows,
        },
    )

    endpoint_rows = []
    for key in sorted(member_info):
        status = endpoint_status_by_key[key]
        endpoint_rows.append(
            {
                "page": page,
                "sheet_no": sheet_no,
                "member_key": key,
                "class_name": member_info[key].get("class_name"),
                "label": member_info[key].get("label"),
                "status": status,
                "gold_connected_to": " ; ".join(member_label(item, member_info) for item in sorted(gold_connected.get(key, set()))),
                "pred_connected_to": " ; ".join(member_label(item, all_info) for item in sorted(pred_connected.get(key, set()))),
                "covered_connected_to": " ; ".join(
                    member_label(item, member_info) for item in sorted(covered_connected.get(key, set()))
                ),
            }
        )
    write_csv(
        page_out / "from_to_endpoint_status.csv",
        endpoint_rows,
        [
            "page",
            "sheet_no",
            "member_key",
            "class_name",
            "label",
            "status",
            "gold_connected_to",
            "pred_connected_to",
            "covered_connected_to",
        ],
    )
    write_json(
        page_out / "from_to_endpoint_status.json",
        {
            "schema_version": "todensekkei.from_to_endpoint_status.v1",
            "page": page,
            "sheet_no": sheet_no,
            "definition": "endpoint-level comparison of expected and predicted direct connection partners",
            "status_legend": {
                "ok": "all direct connection partners match annotation",
                "ng": "endpoint was detected but direct connection partners differ from annotation",
                "terminal_missing": "endpoint could not be matched to an annotation terminal",
            },
            "endpoints": endpoint_rows,
        },
    )

    summary = {
        "page": page,
        "sheet_no": sheet_no,
        "review_png": str(review_png),
        "edge_csv": str(page_out / "from_to_edges.csv"),
        "edge_json": str(page_out / "from_to_edges.json"),
        "endpoint_csv": str(page_out / "from_to_endpoint_status.csv"),
        "endpoint_json": str(page_out / "from_to_endpoint_status.json"),
        "gold_edge_count": len(gold_pairs),
        "pred_edge_count": len(pred_pairs),
        "endpoint_count": len(member_info),
        "detected_endpoint_count": len(detected_member_keys),
        "endpoint_status_counts": dict(sorted(endpoint_status.items())),
        "raw_extra": len(raw_extra_pairs),
        "hard_fp": len(hard_extra_pairs),
        "indirect": len(indirect_pairs),
        "excluded_intermediate": len(excluded_intermediate_pairs),
        "junction_path_covered": len(coverage["junction_path_covered_pairs"]),
        "junction_path_tolerated_pred": len(coverage["tolerated_pred_pairs"]),
        "bridge_stats": dict(sorted(bridge_stats.items())),
        "eval_symbol_gold_mapping": payload.get("eval_symbol_gold_mapping", {}),
        "endpoint_status_mode": endpoint_status_mode,
        "nonconductive_anchor_policy": nonconductive_anchor_policy,
        "strict_metrics": strict_metric,
        **pair_metric,
    }
    write_json(page_out / "from_to_review_summary.json", summary)
    return summary


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build one-panel from-to review outputs from E2E JSON.")
    parser.add_argument("--e2e-dir", type=Path, required=True)
    parser.add_argument("--annotation-zip", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--page-sheet-map", nargs="+", required=True)
    parser.add_argument("--pages", nargs="*", type=int, default=None)
    parser.add_argument("--include-classes", nargs="+", default=["connector", "contact_a", "contact_b", "solenoid", "junction"])
    parser.add_argument("--exclude-classes", nargs="*", default=["other"])
    parser.add_argument("--terminal-match-threshold", type=float, default=18.0)
    parser.add_argument("--internal-bridge-classes", nargs="*", default=["contact_a", "contact_b", "solenoid"])
    parser.add_argument("--node-anchor-classes", nargs="*", default=["connector", "junction", "terminal", "power_bus"])
    parser.add_argument("--node-anchor-threshold", type=float, default=8.0)
    parser.add_argument("--terminal-gap-bridge-classes", nargs="*", default=["connector", "contact_a", "contact_b", "solenoid", "junction"])
    parser.add_argument("--terminal-gap-bridge-max", type=float, default=0.0)
    parser.add_argument("--wire-gap-bridge-max", type=float, default=0.0)
    parser.add_argument("--gap-bridge-align-threshold", type=float, default=8.0)
    parser.add_argument("--wire-gap-bridge-terminal-anchor-threshold", type=float, default=0.0)
    parser.add_argument("--terminal-terminal-bridge-classes", nargs="*", default=[])
    parser.add_argument("--terminal-terminal-bridge-max", type=float, default=0.0)
    parser.add_argument(
        "--gap-bridge-text-overlap-max",
        type=float,
        default=1.0,
        help="reject terminal/wire gap bridges whose bridge segment overlaps text-region boxes above this ratio",
    )
    parser.add_argument(
        "--gap-bridge-symbol-overlap-max",
        type=float,
        default=1.0,
        help="reject synthetic gap bridges whose bridge segment crosses non-endpoint symbol boxes above this ratio",
    )
    parser.add_argument(
        "--junction-path-tolerance-max-edges",
        type=int,
        default=0,
        help="treat a predicted pair across a short gold path as covering the direct annotation edges on that path",
    )
    parser.add_argument(
        "--path-tolerance-internal-classes",
        nargs="*",
        default=[],
        help="allow terminals of these endpoint-side symbol classes as path-tolerance intermediates",
    )
    parser.add_argument(
        "--excluded-intermediate-classes",
        nargs="*",
        default=[],
        help="do not count predicted pairs as extra when they are connected through these excluded annotation classes",
    )
    parser.add_argument("--excluded-intermediate-tolerance-max-edges", type=int, default=0)
    parser.add_argument(
        "--endpoint-status-mode",
        choices=("exact", "covered"),
        default="exact",
        help="exact compares direct partners; covered marks endpoints OK when gold partners are covered and no hard extra is incident",
    )
    parser.add_argument("--eval-symbol-match-iou", type=float, default=0.25)
    parser.add_argument("--eval-symbol-match-min-overlap", type=float, default=0.65)
    parser.add_argument(
        "--nonconductive-anchor-policy",
        choices=("all", "preferred"),
        default="all",
        help="when a terminal is linked to a non-conductive geometric junction, attach to all incident wires or only a preferred/nearest wire",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    page_sheet_map = parse_mapping(args.page_sheet_map)
    include_classes = set(args.include_classes)
    exclude_classes = set(args.exclude_classes)
    internal_bridge_classes = set(args.internal_bridge_classes)
    node_anchor_classes = set(args.node_anchor_classes)
    terminal_gap_bridge_classes = set(args.terminal_gap_bridge_classes)
    terminal_terminal_bridge_classes = set(args.terminal_terminal_bridge_classes)
    path_tolerance_internal_classes = set(args.path_tolerance_internal_classes)
    excluded_intermediate_classes = set(args.excluded_intermediate_classes)
    bundle, _csv_rows, _netlist = load_zip_payload(args.annotation_zip)
    project_by_sheet = {str(project["sheet_no"]): project for project in bundle.get("projects", [])}
    pages = args.pages or sorted(int(page) for page in page_sheet_map)

    page_summaries = []
    totals = Counter()
    endpoint_totals: Counter[str] = Counter()
    for page in pages:
        sheet_no = page_sheet_map[str(page)]
        summary = process_page(
            args.e2e_dir / f"page_{page:03d}",
            page=page,
            sheet_no=sheet_no,
            project=project_by_sheet[sheet_no],
            include_classes=include_classes,
            exclude_classes=exclude_classes,
            terminal_match_threshold=args.terminal_match_threshold,
            internal_bridge_classes=internal_bridge_classes,
            node_anchor_classes=node_anchor_classes,
            node_anchor_threshold=args.node_anchor_threshold,
            terminal_gap_bridge_classes=terminal_gap_bridge_classes,
            terminal_gap_bridge_max=args.terminal_gap_bridge_max,
            wire_gap_bridge_max=args.wire_gap_bridge_max,
            gap_bridge_align_threshold=args.gap_bridge_align_threshold,
            wire_gap_bridge_terminal_anchor_threshold=args.wire_gap_bridge_terminal_anchor_threshold,
            terminal_terminal_bridge_classes=terminal_terminal_bridge_classes,
            terminal_terminal_bridge_max=args.terminal_terminal_bridge_max,
            gap_bridge_text_overlap_max=args.gap_bridge_text_overlap_max,
            gap_bridge_symbol_overlap_max=args.gap_bridge_symbol_overlap_max,
            junction_path_tolerance_max_edges=args.junction_path_tolerance_max_edges,
            path_tolerance_internal_classes=path_tolerance_internal_classes,
            excluded_intermediate_classes=excluded_intermediate_classes,
            excluded_intermediate_tolerance_max_edges=args.excluded_intermediate_tolerance_max_edges,
            endpoint_status_mode=args.endpoint_status_mode,
            eval_symbol_match_iou=args.eval_symbol_match_iou,
            eval_symbol_match_min_overlap=args.eval_symbol_match_min_overlap,
            nonconductive_anchor_policy=args.nonconductive_anchor_policy,
            out_dir=args.out_dir,
        )
        page_summaries.append(summary)
        for key in ("tp", "fp", "fn"):
            totals[key] += int(summary[key])
        for key in ("raw_extra", "hard_fp", "indirect"):
            totals[key] += int(summary[key])
        totals["excluded_intermediate"] += int(summary["excluded_intermediate"])
        for key in ("junction_path_covered", "junction_path_tolerated_pred"):
            totals[key] += int(summary[key])
        endpoint_totals.update(summary["endpoint_status_counts"])

    aggregate = {
        "schema_version": "todensekkei.from_to_review.v1",
        "e2e_dir": str(args.e2e_dir),
        "annotation_zip": str(args.annotation_zip),
        "include_classes": sorted(include_classes),
        "exclude_classes": sorted(exclude_classes),
        "terminal_match_threshold": args.terminal_match_threshold,
        "internal_bridge_classes": sorted(internal_bridge_classes),
        "node_anchor_classes": sorted(node_anchor_classes),
        "node_anchor_threshold": args.node_anchor_threshold,
        "terminal_gap_bridge_classes": sorted(terminal_gap_bridge_classes),
        "terminal_gap_bridge_max": args.terminal_gap_bridge_max,
        "wire_gap_bridge_max": args.wire_gap_bridge_max,
        "gap_bridge_align_threshold": args.gap_bridge_align_threshold,
        "wire_gap_bridge_terminal_anchor_threshold": args.wire_gap_bridge_terminal_anchor_threshold,
        "terminal_terminal_bridge_classes": sorted(terminal_terminal_bridge_classes),
        "terminal_terminal_bridge_max": args.terminal_terminal_bridge_max,
        "gap_bridge_text_overlap_max": args.gap_bridge_text_overlap_max,
        "gap_bridge_symbol_overlap_max": args.gap_bridge_symbol_overlap_max,
        "junction_path_tolerance_max_edges": args.junction_path_tolerance_max_edges,
        "path_tolerance_internal_classes": sorted(path_tolerance_internal_classes),
        "excluded_intermediate_classes": sorted(excluded_intermediate_classes),
        "excluded_intermediate_tolerance_max_edges": args.excluded_intermediate_tolerance_max_edges,
        "endpoint_status_mode": args.endpoint_status_mode,
        "nonconductive_anchor_policy": args.nonconductive_anchor_policy,
        "eval_symbol_gold_mapping": {
            "method": "evaluation-only same-class bbox matching; predicted bbox/terminals are not replaced",
            "iou_threshold": args.eval_symbol_match_iou,
            "min_overlap_threshold": args.eval_symbol_match_min_overlap,
        },
        "aggregate_metrics": metric(totals["tp"], totals["fp"], totals["fn"]),
        "aggregate_strict_metrics": metric(totals["tp"], totals["raw_extra"], totals["fn"]),
        "aggregate_extra_counts": {
            "raw_extra": totals["raw_extra"],
            "hard_fp": totals["hard_fp"],
            "indirect": totals["indirect"],
            "excluded_intermediate": totals["excluded_intermediate"],
            "junction_path_covered": totals["junction_path_covered"],
            "junction_path_tolerated_pred": totals["junction_path_tolerated_pred"],
        },
        "aggregate_endpoint_status_counts": dict(sorted(endpoint_totals.items())),
        "pages": page_summaries,
    }
    args.out_dir.mkdir(parents=True, exist_ok=True)
    write_json(args.out_dir / "from_to_review_summary.json", aggregate)
    write_csv(
        args.out_dir / "from_to_review_summary.csv",
        [
            {
                "page": item["page"],
                "sheet_no": item["sheet_no"],
                "gold_edge_count": item["gold_edge_count"],
                "pred_edge_count": item["pred_edge_count"],
                "tp": item["tp"],
                "fp": item["fp"],
                "raw_extra": item["raw_extra"],
                "hard_fp": item["hard_fp"],
                "indirect": item["indirect"],
                "excluded_intermediate": item["excluded_intermediate"],
                "junction_path_covered": item["junction_path_covered"],
                "junction_path_tolerated_pred": item["junction_path_tolerated_pred"],
                "fn": item["fn"],
                "precision": item["precision"],
                "recall": item["recall"],
                "f1": item["f1"],
                "endpoint_count": item["endpoint_count"],
                "detected_endpoint_count": item["detected_endpoint_count"],
                "endpoint_status_counts": json.dumps(item["endpoint_status_counts"], ensure_ascii=False),
                "bridge_stats": json.dumps(item["bridge_stats"], ensure_ascii=False),
                "eval_symbol_gold_mapping": json.dumps(item["eval_symbol_gold_mapping"], ensure_ascii=False),
                "review_png": item["review_png"],
                "edge_csv": item["edge_csv"],
                "edge_json": item["edge_json"],
                "endpoint_csv": item["endpoint_csv"],
                "endpoint_json": item["endpoint_json"],
            }
            for item in page_summaries
        ],
        [
            "page",
            "sheet_no",
            "gold_edge_count",
            "pred_edge_count",
            "tp",
            "fp",
            "raw_extra",
            "hard_fp",
            "indirect",
            "excluded_intermediate",
            "junction_path_covered",
            "junction_path_tolerated_pred",
            "fn",
            "precision",
            "recall",
            "f1",
            "endpoint_count",
            "detected_endpoint_count",
            "endpoint_status_counts",
            "bridge_stats",
            "eval_symbol_gold_mapping",
            "review_png",
            "edge_csv",
            "edge_json",
            "endpoint_csv",
            "endpoint_json",
        ],
    )
    print(json.dumps(aggregate["aggregate_metrics"], ensure_ascii=False, indent=2))
    print(f"wrote {args.out_dir}")


if __name__ == "__main__":
    main()
