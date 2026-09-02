from __future__ import annotations

import argparse
import copy
import csv
import itertools
import json
import math
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw

from analyze_annotation_graph import build_project_graph, load_zip_payload, parse_member, symbol_bbox
from build_e2e_demo_outputs import load_font, wire_points


FONT_XS = load_font(12)
FONT_SM = load_font(15)
FONT_MD = load_font(18)
FONT_LG = load_font(26)

OK_COLOR = (37, 99, 235)
MISSING_COLOR = (220, 38, 38)
EXTRA_COLOR = (217, 119, 6)
GRAY_COLOR = (120, 120, 120)

NET_PALETTE = [
    (37, 99, 235),
    (5, 150, 105),
    (217, 119, 6),
    (124, 58, 237),
    (8, 145, 178),
    (190, 24, 93),
    (77, 124, 15),
    (202, 138, 4),
    (79, 70, 229),
    (15, 118, 110),
]


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


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


def terminal_member_key(symbol_ref: str, terminal_ref: str | None) -> str:
    return f"{symbol_ref}:{terminal_ref}" if terminal_ref else symbol_ref


def sorted_pair(left: str, right: str) -> tuple[str, str]:
    return tuple(sorted((left, right)))  # type: ignore[return-value]


def point_distance(a: list[float] | tuple[float, float], b: list[float] | tuple[float, float]) -> float:
    return math.hypot(float(a[0]) - float(b[0]), float(a[1]) - float(b[1]))


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


def box_tuple(box: dict[str, int | float]) -> tuple[int, int, int, int]:
    return int(round(float(box["x0"]))), int(round(float(box["y0"]))), int(round(float(box["x1"]))), int(round(float(box["y1"])))


def text_box(draw: ImageDraw.ImageDraw, xy: tuple[int, int], text: str, *, fill: tuple[int, int, int]) -> None:
    x, y = xy
    text = text[:64]
    bbox = draw.textbbox((x, y), text, font=FONT_XS)
    draw.rectangle((bbox[0] - 2, bbox[1] - 1, bbox[2] + 2, bbox[3] + 1), fill="white", outline=fill)
    draw.text((x, y), text, fill=fill, font=FONT_XS)


def short_label(text: str, max_len: int = 28) -> str:
    text = " ".join(str(text or "").split())
    return text if len(text) <= max_len else text[: max_len - 1] + "."


def member_label(member_key: str, member_info: dict[str, Any]) -> str:
    info = member_info.get(member_key) or {}
    symbol_ref, terminal_ref = parse_member(member_key)
    terminal_name = info.get("terminal_name") or terminal_ref or ""
    label = short_label(info.get("label") or "", 22)
    return f"{symbol_ref}/{terminal_name} {info.get('class_name', '')} {label}".strip()


def make_gold_member_info(project: dict[str, Any], include_classes: set[str]) -> dict[str, dict[str, Any]]:
    width = int(project["image_width"])
    height = int(project["image_height"])
    member_info: dict[str, dict[str, Any]] = {}
    for symbol in project.get("symbols", []):
        class_name = str(symbol.get("class_key") or "")
        if class_name not in include_classes:
            continue
        bbox = symbol_bbox(symbol, width, height)
        terminals = symbol.get("terminals") or []
        if not terminals:
            key = terminal_member_key(symbol["ref"], None)
            member_info[key] = {
                "symbol_ref": symbol["ref"],
                "terminal_ref": None,
                "terminal_name": "",
                "class_name": class_name,
                "class_label": symbol.get("class_label"),
                "label": symbol.get("label") or "",
                "bbox": bbox,
                "point": [round(float(symbol["cx"]) * width, 1), round(float(symbol["cy"]) * height, 1)],
            }
            continue
        for terminal in terminals:
            key = terminal_member_key(symbol["ref"], terminal.get("ref"))
            member_info[key] = {
                "symbol_ref": symbol["ref"],
                "terminal_ref": terminal.get("ref"),
                "terminal_name": terminal.get("name") or "",
                "class_name": class_name,
                "class_label": symbol.get("class_label"),
                "label": symbol.get("label") or "",
                "bbox": bbox,
                "point": [
                    round(float(terminal["tx"]) * width, 1),
                    round(float(terminal["ty"]) * height, 1),
                ],
            }
    return member_info


def filter_project_graph_classes(
    project: dict[str, Any],
    expected_netlist: dict[str, Any] | None,
    exclude_classes: set[str],
) -> tuple[dict[str, Any], dict[str, Any] | None, dict[str, Any]]:
    if not exclude_classes:
        return project, expected_netlist, {
            "exclude_through_classes": [],
            "removed_symbol_count": 0,
            "removed_connection_count": 0,
            "removed_netlist_member_count": 0,
            "removed_netlist_count": 0,
        }

    excluded_refs = {
        str(symbol["ref"])
        for symbol in project.get("symbols", [])
        if str(symbol.get("class_key") or "") in exclude_classes
    }
    filtered_project = copy.deepcopy(project)
    original_symbols = filtered_project.get("symbols", [])
    original_connections = filtered_project.get("connections", [])
    filtered_project["symbols"] = [
        symbol for symbol in original_symbols if str(symbol.get("ref") or "") not in excluded_refs
    ]
    filtered_project["connections"] = [
        connection
        for connection in original_connections
        if str(connection.get("from_symbol_ref") or "") not in excluded_refs
        and str(connection.get("to_symbol_ref") or "") not in excluded_refs
    ]

    removed_netlist_member_count = 0
    removed_netlist_count = 0
    filtered_expected_netlist = copy.deepcopy(expected_netlist) if expected_netlist else None
    if filtered_expected_netlist:
        filtered_nets = []
        for net in filtered_expected_netlist.get("nets", []):
            original_members = list(net.get("members", []))
            members = [
                member
                for member in original_members
                if parse_member(str(member))[0] not in excluded_refs
            ]
            removed_netlist_member_count += len(original_members) - len(members)
            if not members:
                removed_netlist_count += 1
                continue
            next_net = copy.deepcopy(net)
            next_net["members"] = members
            filtered_nets.append(next_net)
        filtered_expected_netlist["nets"] = filtered_nets

    return filtered_project, filtered_expected_netlist, {
        "exclude_through_classes": sorted(exclude_classes),
        "removed_symbol_count": len(original_symbols) - len(filtered_project["symbols"]),
        "removed_connection_count": len(original_connections) - len(filtered_project["connections"]),
        "removed_netlist_member_count": removed_netlist_member_count,
        "removed_netlist_count": removed_netlist_count,
    }


def connected_sets_from_components(
    components: list[list[str]],
) -> tuple[set[tuple[str, str]], dict[str, set[str]], dict[str, str]]:
    pair_set: set[tuple[str, str]] = set()
    connected_by_member: dict[str, set[str]] = defaultdict(set)
    component_by_member: dict[str, str] = {}
    for index, members in enumerate(components, start=1):
        unique_members = sorted(set(members))
        component_id = f"net_{index:04d}"
        for member in unique_members:
            component_by_member[member] = component_id
        for left, right in itertools.combinations(unique_members, 2):
            pair_set.add(sorted_pair(left, right))
            connected_by_member[left].add(right)
            connected_by_member[right].add(left)
    return pair_set, connected_by_member, component_by_member


def gold_connection_components(
    project: dict[str, Any],
    expected_netlist: dict[str, Any] | None,
    include_classes: set[str],
) -> list[list[str]]:
    class_by_ref = {symbol["ref"]: str(symbol.get("class_key") or "") for symbol in project.get("symbols", [])}
    graph = build_project_graph(project, expected_netlist)
    components: list[list[str]] = []
    for component in graph.get("components", []):
        members = []
        for raw_member in component.get("members", []):
            symbol_ref, terminal_ref = parse_member(raw_member)
            if class_by_ref.get(symbol_ref) in include_classes:
                members.append(terminal_member_key(symbol_ref, terminal_ref))
        if members:
            components.append(members)
    return components


def predicted_connection_components(
    labeled_connections: dict[str, Any],
    include_classes: set[str],
    member_info: dict[str, dict[str, Any]],
    pred_terminal_match_threshold: float,
) -> tuple[list[list[str]], dict[str, list[str]]]:
    components: list[list[str]] = []
    pred_nets_by_member: dict[str, list[str]] = defaultdict(list)
    for net in labeled_connections.get("nets", []):
        members = []
        for member in net.get("members", []):
            class_name = str(member.get("class_name") or "")
            gold_ref = member.get("gold_ref")
            if class_name not in include_classes or not gold_ref:
                continue
            key = predicted_member_key(member, member_info, pred_terminal_match_threshold)
            members.append(key)
            pred_nets_by_member[key].append(str(net.get("net_id") or ""))
        if members:
            components.append(sorted(set(members)))
    return components, pred_nets_by_member


def predicted_member_key(
    member: dict[str, Any],
    member_info: dict[str, dict[str, Any]],
    pred_terminal_match_threshold: float,
) -> str:
    symbol_ref = str(member.get("gold_ref") or "")
    terminal_ref = member.get("terminal_ref")
    direct_key = terminal_member_key(symbol_ref, terminal_ref)
    if direct_key in member_info:
        return direct_key

    point = member.get("terminal_point")
    if not point:
        return direct_key

    best_key = ""
    best_distance = float("inf")
    for key, info in member_info.items():
        if str(info.get("symbol_ref") or "") != symbol_ref:
            continue
        candidate_point = info.get("point")
        if not candidate_point:
            continue
        distance = point_distance(point, candidate_point)
        if distance < best_distance:
            best_key = key
            best_distance = distance

    if best_key and best_distance <= pred_terminal_match_threshold:
        return best_key
    return direct_key


def compare_connections(
    member_info: dict[str, dict[str, Any]],
    gold_components: list[list[str]],
    pred_components: list[list[str]],
    pred_nets_by_member: dict[str, list[str]],
) -> dict[str, Any]:
    gold_pairs, gold_by_member, gold_net_by_member = connected_sets_from_components(gold_components)
    pred_pairs, pred_by_member, _pred_component_by_member = connected_sets_from_components(pred_components)
    tp = gold_pairs & pred_pairs
    fp = pred_pairs - gold_pairs
    fn = gold_pairs - pred_pairs

    rows = []
    status_counts: Counter[str] = Counter()
    for key in sorted(member_info):
        gold_connected = gold_by_member.get(key, set())
        pred_connected = pred_by_member.get(key, set())
        missing = gold_connected - pred_connected
        extra = pred_connected - gold_connected
        if not missing and not extra:
            status = "ok"
        elif missing and extra:
            status = "missing_and_extra"
        elif missing:
            status = "missing"
        else:
            status = "extra"
        status_counts[status] += 1
        info = member_info[key]
        rows.append(
            {
                "member_key": key,
                "symbol_ref": info["symbol_ref"],
                "terminal_ref": info.get("terminal_ref") or "",
                "terminal_name": info.get("terminal_name") or "",
                "class_name": info["class_name"],
                "label": info.get("label") or "",
                "point": info.get("point"),
                "bbox": info.get("bbox"),
                "status": status,
                "gold_net": gold_net_by_member.get(key, ""),
                "pred_nets": sorted(set(pred_nets_by_member.get(key, []))),
                "gold_connected": sorted(gold_connected),
                "pred_connected": sorted(pred_connected),
                "missing": sorted(missing),
                "extra": sorted(extra),
            }
        )

    return {
        "terminal_pair_metrics": metric(len(tp), len(fp), len(fn)),
        "terminal_pair_counts": {
            "gold_pair_count": len(gold_pairs),
            "pred_pair_count": len(pred_pairs),
        },
        "terminal_status_counts": dict(sorted(status_counts.items())),
        "rows": rows,
        "pair_samples": {
            "tp": [list(item) for item in sorted(tp)[:30]],
            "fp": [list(item) for item in sorted(fp)[:30]],
            "fn": [list(item) for item in sorted(fn)[:30]],
        },
    }


def status_color(status: str) -> tuple[int, int, int]:
    if status == "ok":
        return OK_COLOR
    if status == "extra":
        return EXTRA_COLOR
    if status in {"missing", "missing_and_extra"}:
        return MISSING_COLOR
    return GRAY_COLOR


def fade_source(source: Image.Image, amount: float = 0.56) -> Image.Image:
    base = source.convert("RGB")
    return Image.blend(base, Image.new("RGB", base.size, "white"), amount)


def draw_title(panel: Image.Image, title: str, subtitle: str | None = None) -> Image.Image:
    header_h = 58
    output = Image.new("RGB", (panel.width, panel.height + header_h), "white")
    output.paste(panel, (0, header_h))
    draw = ImageDraw.Draw(output)
    draw.text((14, 8), title, fill=(20, 20, 20), font=FONT_LG)
    if subtitle:
        draw.text((14, 36), subtitle, fill=(75, 75, 75), font=FONT_SM)
    return output


def draw_original_panel(source: Image.Image, sheet_no: str, page: int) -> Image.Image:
    return draw_title(source.convert("RGB"), f"01 Original / page {page:03d}", sheet_no)


def draw_predicted_panel(
    source: Image.Image,
    payload: dict[str, Any],
    rows: list[dict[str, Any]],
) -> Image.Image:
    canvas = fade_source(source)
    draw = ImageDraw.Draw(canvas)
    wires_by_id = {wire["id"]: wire for wire in payload.get("wires", [])}
    net_color_by_id: dict[str, tuple[int, int, int]] = {}
    for index, net in enumerate(payload.get("nets", [])):
        color = NET_PALETTE[index % len(NET_PALETTE)]
        net_color_by_id[str(net["id"])] = color
        for wire_id in net.get("wire_ids", []):
            wire = wires_by_id.get(wire_id)
            if not wire:
                continue
            start, end = wire_points(wire)
            draw.line((start, end), fill=color, width=7)

    for row in rows:
        point = row.get("point")
        if not point:
            continue
        pred_nets = row.get("pred_nets") or []
        color = net_color_by_id.get(pred_nets[0], GRAY_COLOR) if pred_nets else GRAY_COLOR
        x, y = round(point[0]), round(point[1])
        draw.ellipse((x - 7, y - 7, x + 7, y + 7), fill=color, outline="white", width=2)

    legend_lines = ["same color = same predicted net"]
    for net_id, color in list(net_color_by_id.items())[:8]:
        legend_lines.append(net_id)
    x0, y0 = 16, 16
    draw.rectangle((x0 - 6, y0 - 6, x0 + 178, y0 + 20 * len(legend_lines) + 8), fill="white", outline=(90, 90, 90))
    for index, line in enumerate(legend_lines):
        y = y0 + index * 20
        if index == 0:
            draw.text((x0, y), line, fill=(25, 25, 25), font=FONT_SM)
            continue
        color = net_color_by_id[line]
        draw.rectangle((x0, y + 4, x0 + 14, y + 18), fill=color)
        draw.text((x0 + 20, y), line, fill=(25, 25, 25), font=FONT_SM)
    return draw_title(canvas, "02 Predicted connection nets", "same color = same predicted net")


def draw_gold_panel(
    source: Image.Image,
    member_info: dict[str, dict[str, Any]],
    gold_components: list[list[str]],
) -> Image.Image:
    canvas = fade_source(source)
    draw = ImageDraw.Draw(canvas)
    for index, members in enumerate(gold_components):
        color = NET_PALETTE[index % len(NET_PALETTE)]
        points = [member_info[member]["point"] for member in members if member in member_info and member_info[member].get("point")]
        if not points:
            continue
        hub = (round(sum(point[0] for point in points) / len(points)), round(sum(point[1] for point in points) / len(points)))
        for point in points:
            x, y = round(point[0]), round(point[1])
            draw.line((x, y, hub[0], hub[1]), fill=color, width=2)
            draw.ellipse((x - 7, y - 7, x + 7, y + 7), fill=color, outline="white", width=2)
        draw.ellipse((hub[0] - 5, hub[1] - 5, hub[0] + 5, hub[1] + 5), fill=color)
        text_box(draw, (hub[0] + 8, hub[1] - 8), f"G{index + 1:02d}", fill=color)
    return draw_title(canvas, "03 Gold logical nets", "same color = same annotated connection group")


def draw_diff_panel(
    source: Image.Image,
    rows: list[dict[str, Any]],
    summary: dict[str, Any],
) -> Image.Image:
    canvas = fade_source(source, amount=0.48)
    draw = ImageDraw.Draw(canvas)
    worst_by_symbol: dict[str, dict[str, Any]] = {}
    rank = {"ok": 0, "extra": 1, "missing": 2, "missing_and_extra": 3}

    for row in rows:
        symbol_ref = row["symbol_ref"]
        current = worst_by_symbol.get(symbol_ref)
        if current is None or rank[row["status"]] > rank[current["status"]]:
            worst_by_symbol[symbol_ref] = row

    for row in worst_by_symbol.values():
        box = row.get("bbox")
        if not box:
            continue
        color = status_color(row["status"])
        draw.rectangle(box_tuple(box), outline=color, width=4)

    for row in rows:
        point = row.get("point")
        if not point:
            continue
        color = status_color(row["status"])
        x, y = round(point[0]), round(point[1])
        draw.ellipse((x - 9, y - 9, x + 9, y + 9), fill=color, outline="white", width=2)

    metrics = summary["terminal_pair_metrics"]
    status_counts = summary["terminal_status_counts"]
    lines = [
        "blue OK / red missing / orange extra",
        f"precision: {metrics['precision']}  recall: {metrics['recall']}  f1: {metrics['f1']}",
        f"tp/fp/fn: {metrics['tp']} / {metrics['fp']} / {metrics['fn']}",
        "terminals: "
        + ", ".join(f"{key}={value}" for key, value in sorted(status_counts.items())),
    ]
    x0, y0 = 16, 16
    line_h = 20
    max_w = max(draw.textbbox((0, 0), line, font=FONT_MD if index == 0 else FONT_SM)[2] for index, line in enumerate(lines))
    draw.rectangle((x0 - 6, y0 - 6, x0 + max_w + 14, y0 + line_h * len(lines) + 8), fill="white", outline=(80, 80, 80), width=2)
    for index, line in enumerate(lines):
        draw.text((x0, y0 + index * line_h), line, fill=(20, 20, 20), font=FONT_MD if index == 0 else FONT_SM)
    return draw_title(canvas, "04 Difference review", "terminal-level comparison against annotation")


def make_review_sheet(panels: list[Image.Image]) -> Image.Image:
    if len(panels) != 4:
        raise ValueError("exactly four panels are required")
    cell_w = max(panel.width for panel in panels)
    cell_h = max(panel.height for panel in panels)
    sheet = Image.new("RGB", (cell_w * 2, cell_h * 2), (235, 235, 235))
    for index, panel in enumerate(panels):
        x = (index % 2) * cell_w
        y = (index // 2) * cell_h
        sheet.paste(panel, (x, y))
    return sheet


def write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def stringify_members(members: list[str], member_info: dict[str, dict[str, Any]]) -> str:
    return " ; ".join(member_label(member, member_info) for member in members)


def process_page(
    page_dir: Path,
    *,
    page: int,
    sheet_no: str,
    project: dict[str, Any],
    expected_netlist: dict[str, Any] | None,
    include_classes: set[str],
    exclude_through_classes: set[str],
    out_dir: Path,
    pred_terminal_match_threshold: float,
) -> dict[str, Any]:
    payload = read_json(page_dir / "final_output.json")
    labeled_connections = read_json(page_dir / "labeled_connections.json")
    source = Image.open(payload["source"]["base_image"]).convert("RGB")

    graph_project, graph_expected_netlist, graph_filter = filter_project_graph_classes(
        project,
        expected_netlist,
        exclude_through_classes,
    )
    member_info = make_gold_member_info(graph_project, include_classes)
    gold_components = gold_connection_components(graph_project, graph_expected_netlist, include_classes)
    pred_components, pred_nets_by_member = predicted_connection_components(
        labeled_connections,
        include_classes,
        member_info,
        pred_terminal_match_threshold,
    )
    comparison = compare_connections(member_info, gold_components, pred_components, pred_nets_by_member)
    comparison["graph_filter"] = graph_filter

    page_out = out_dir / f"page_{page:03d}"
    page_out.mkdir(parents=True, exist_ok=True)
    panels = [
        draw_original_panel(source, sheet_no, page),
        draw_predicted_panel(source, payload, comparison["rows"]),
        draw_gold_panel(source, member_info, gold_components),
        draw_diff_panel(source, comparison["rows"], comparison),
    ]
    review_path = page_out / "connection_review.png"
    make_review_sheet(panels).save(review_path)

    detail_rows = []
    for row in comparison["rows"]:
        detail_rows.append(
            {
                "page": page,
                "sheet_no": sheet_no,
                "member_key": row["member_key"],
                "symbol_ref": row["symbol_ref"],
                "class_name": row["class_name"],
                "label": row["label"],
                "terminal_ref": row["terminal_ref"],
                "terminal_name": row["terminal_name"],
                "status": row["status"],
                "gold_net": row["gold_net"],
                "pred_nets": ",".join(row["pred_nets"]),
                "gold_connected_count": len(row["gold_connected"]),
                "pred_connected_count": len(row["pred_connected"]),
                "missing_count": len(row["missing"]),
                "extra_count": len(row["extra"]),
                "gold_connected": stringify_members(row["gold_connected"], member_info),
                "pred_connected": stringify_members(row["pred_connected"], member_info),
                "missing": stringify_members(row["missing"], member_info),
                "extra": stringify_members(row["extra"], member_info),
            }
        )

    write_csv(
        page_out / "connection_terminal_review.csv",
        detail_rows,
        [
            "page",
            "sheet_no",
            "member_key",
            "symbol_ref",
            "class_name",
            "label",
            "terminal_ref",
            "terminal_name",
            "status",
            "gold_net",
            "pred_nets",
            "gold_connected_count",
            "pred_connected_count",
            "missing_count",
            "extra_count",
            "gold_connected",
            "pred_connected",
            "missing",
            "extra",
        ],
    )
    write_json(page_out / "connection_review_summary.json", comparison)

    return {
        "page": page,
        "sheet_no": sheet_no,
        "review_png": str(review_path),
        "terminal_csv": str(page_out / "connection_terminal_review.csv"),
        "terminal_count": len(comparison["rows"]),
        **comparison["terminal_pair_counts"],
        **comparison["terminal_pair_metrics"],
        "terminal_status_counts": comparison["terminal_status_counts"],
        "pred_terminal_match_threshold": pred_terminal_match_threshold,
        "graph_filter": graph_filter,
        "_detail_rows": detail_rows,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build visual and tabular connection review outputs from E2E JSON.")
    parser.add_argument("--e2e-dir", type=Path, required=True)
    parser.add_argument("--annotation-zip", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--page-sheet-map", nargs="+", required=True, help="Example: 3=SHEET001")
    parser.add_argument("--pages", nargs="*", type=int, default=None)
    parser.add_argument("--include-classes", nargs="+", default=["connector", "contact_a"])
    parser.add_argument("--exclude-through-classes", nargs="*", default=[])
    parser.add_argument("--pred-terminal-match-threshold", type=float, default=18.0)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    page_sheet_map = parse_mapping(args.page_sheet_map)
    include_classes = set(args.include_classes)
    exclude_through_classes = set(args.exclude_through_classes)
    bundle, _csv_rows, netlist = load_zip_payload(args.annotation_zip)
    project_by_sheet = {str(project["sheet_no"]): project for project in bundle.get("projects", [])}
    expected_netlist_by_project = {str(project["project_id"]): project for project in netlist.get("projects", [])}
    pages = args.pages or sorted(int(page) for page in page_sheet_map)

    page_summaries = []
    all_detail_rows = []
    for page in pages:
        sheet_no = page_sheet_map[str(page)]
        project = project_by_sheet[sheet_no]
        expected_netlist = expected_netlist_by_project.get(str(project["id"]))
        page_summary = process_page(
            args.e2e_dir / f"page_{page:03d}",
            page=page,
            sheet_no=sheet_no,
            project=project,
            expected_netlist=expected_netlist,
            include_classes=include_classes,
            exclude_through_classes=exclude_through_classes,
            out_dir=args.out_dir,
            pred_terminal_match_threshold=args.pred_terminal_match_threshold,
        )
        all_detail_rows.extend(page_summary.pop("_detail_rows"))
        page_summaries.append(page_summary)

    total = Counter()
    status_total: Counter[str] = Counter()
    for summary in page_summaries:
        for key in ("tp", "fp", "fn"):
            total[key] += int(summary[key])
        status_total.update(summary["terminal_status_counts"])

    aggregate = {
        "schema_version": "todensekkei.connection_review.v1",
        "e2e_dir": str(args.e2e_dir),
        "annotation_zip": str(args.annotation_zip),
        "include_classes": sorted(include_classes),
        "exclude_through_classes": sorted(exclude_through_classes),
        "aggregate_terminal_pair_metrics": metric(total["tp"], total["fp"], total["fn"]),
        "aggregate_terminal_status_counts": dict(sorted(status_total.items())),
        "pages": page_summaries,
    }

    args.out_dir.mkdir(parents=True, exist_ok=True)
    write_json(args.out_dir / "connection_review_summary.json", aggregate)
    write_csv(
        args.out_dir / "connection_review_summary.csv",
        [
            {
                "page": item["page"],
                "sheet_no": item["sheet_no"],
                "terminal_count": item["terminal_count"],
                "gold_pair_count": item["gold_pair_count"],
                "pred_pair_count": item["pred_pair_count"],
                "tp": item["tp"],
                "fp": item["fp"],
                "fn": item["fn"],
                "precision": item["precision"],
                "recall": item["recall"],
                "f1": item["f1"],
                "terminal_status_counts": json.dumps(item["terminal_status_counts"], ensure_ascii=False),
                "graph_filter": json.dumps(item["graph_filter"], ensure_ascii=False),
                "review_png": item["review_png"],
                "terminal_csv": item["terminal_csv"],
            }
            for item in page_summaries
        ],
        [
            "page",
            "sheet_no",
            "terminal_count",
            "gold_pair_count",
            "pred_pair_count",
            "tp",
            "fp",
            "fn",
            "precision",
            "recall",
            "f1",
            "terminal_status_counts",
            "graph_filter",
            "review_png",
            "terminal_csv",
        ],
    )
    write_csv(
        args.out_dir / "connection_terminal_review_all.csv",
        all_detail_rows,
        [
            "page",
            "sheet_no",
            "member_key",
            "symbol_ref",
            "class_name",
            "label",
            "terminal_ref",
            "terminal_name",
            "status",
            "gold_net",
            "pred_nets",
            "gold_connected_count",
            "pred_connected_count",
            "missing_count",
            "extra_count",
            "gold_connected",
            "pred_connected",
            "missing",
            "extra",
        ],
    )
    print(json.dumps(aggregate["aggregate_terminal_pair_metrics"], ensure_ascii=False, indent=2))
    print(f"wrote {args.out_dir}")


if __name__ == "__main__":
    main()
