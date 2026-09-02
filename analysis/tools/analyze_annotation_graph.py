from __future__ import annotations

import argparse
import csv
import io
import json
import zipfile
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


@dataclass
class DisjointSet:
    parent: dict[str, str]

    def add(self, item: str) -> None:
        self.parent.setdefault(item, item)

    def find(self, item: str) -> str:
        self.add(item)
        root = item
        while self.parent[root] != root:
            root = self.parent[root]
        while self.parent[item] != item:
            parent = self.parent[item]
            self.parent[item] = root
            item = parent
        return root

    def union(self, left: str, right: str) -> None:
        left_root = self.find(left)
        right_root = self.find(right)
        if left_root != right_root:
            self.parent[right_root] = left_root


def endpoint_key(symbol_ref: str | None, terminal_ref: str | None) -> str:
    if not symbol_ref:
        raise ValueError("connection endpoint is missing symbol_ref")
    if terminal_ref:
        return f"{symbol_ref}:{terminal_ref}"
    return symbol_ref


def parse_member(member: str) -> tuple[str, str | None]:
    if ":" not in member:
        return member, None
    symbol_ref, terminal_ref = member.split(":", 1)
    return symbol_ref, terminal_ref


def load_zip_payload(zip_path: Path) -> tuple[dict[str, Any], list[dict[str, str]], dict[str, Any]]:
    with zipfile.ZipFile(zip_path) as archive:
        bundle = json.loads(archive.read("bundle.json"))
        connections_csv = list(
            csv.DictReader(io.StringIO(archive.read("connections/connections.csv").decode("utf-8-sig")))
        )
        netlist = json.loads(archive.read("connections/netlist.json"))
    return bundle, connections_csv, netlist


def symbol_bbox(symbol: dict[str, Any], width: int, height: int) -> dict[str, int]:
    cx = float(symbol["cx"]) * width
    cy = float(symbol["cy"]) * height
    w = float(symbol["w"]) * width
    h = float(symbol["h"]) * height
    return {
        "x0": round(cx - w / 2),
        "y0": round(cy - h / 2),
        "x1": round(cx + w / 2),
        "y1": round(cy + h / 2),
    }


def build_project_graph(project: dict[str, Any], expected_netlist: dict[str, Any] | None) -> dict[str, Any]:
    width = int(project["image_width"])
    height = int(project["image_height"])
    symbols = {symbol["ref"]: symbol for symbol in project.get("symbols", [])}
    terminal_lookup: dict[str, dict[str, Any]] = {}
    node_meta: dict[str, dict[str, Any]] = {}
    issues: list[dict[str, Any]] = []

    for symbol in project.get("symbols", []):
        symbol_ref = symbol["ref"]
        terminals = symbol.get("terminals") or []
        if not terminals:
            node_meta[symbol_ref] = {
                "id": symbol_ref,
                "symbol_ref": symbol_ref,
                "terminal_ref": None,
                "terminal_name": None,
                "class_key": symbol["class_key"],
                "class_label": symbol.get("class_label"),
                "label": symbol.get("label") or "",
                "bbox": symbol_bbox(symbol, width, height),
                "point": [round(float(symbol["cx"]) * width), round(float(symbol["cy"]) * height)],
            }
            continue

        for terminal in terminals:
            key = endpoint_key(symbol_ref, terminal["ref"])
            terminal_lookup[key] = terminal
            node_meta[key] = {
                "id": key,
                "symbol_ref": symbol_ref,
                "terminal_ref": terminal["ref"],
                "terminal_name": terminal.get("name") or "",
                "class_key": symbol["class_key"],
                "class_label": symbol.get("class_label"),
                "label": symbol.get("label") or "",
                "bbox": symbol_bbox(symbol, width, height),
                "point": [
                    round(float(terminal["tx"]) * width),
                    round(float(terminal["ty"]) * height),
                ],
            }

    dsu = DisjointSet(parent={})
    edges: list[dict[str, Any]] = []
    used_node_ids: set[str] = set()
    for index, connection in enumerate(project.get("connections", []), start=1):
        from_node = endpoint_key(connection.get("from_symbol_ref"), connection.get("from_terminal_ref"))
        to_node = endpoint_key(connection.get("to_symbol_ref"), connection.get("to_terminal_ref"))
        used_node_ids.update((from_node, to_node))
        for node_id, side in ((from_node, "from"), (to_node, "to")):
            symbol_ref, _terminal_ref = parse_member(node_id)
            if symbol_ref not in symbols:
                issues.append({"type": "missing_symbol_ref", "side": side, "node_id": node_id})
            if node_id not in node_meta:
                symbol = symbols.get(symbol_ref)
                node_meta[node_id] = {
                    "id": node_id,
                    "symbol_ref": symbol_ref,
                    "terminal_ref": _terminal_ref,
                    "terminal_name": None,
                    "class_key": symbol.get("class_key") if symbol else None,
                    "class_label": symbol.get("class_label") if symbol else None,
                    "label": symbol.get("label") if symbol else "",
                    "bbox": symbol_bbox(symbol, width, height) if symbol else None,
                    "point": [
                        round(float(symbol["cx"]) * width),
                        round(float(symbol["cy"]) * height),
                    ]
                    if symbol
                    else None,
                    "note": "node was referenced by a connection but not present as a declared terminal",
                }
        dsu.union(from_node, to_node)
        edges.append(
            {
                "id": f"EDGE-{index:04d}",
                "from": from_node,
                "to": to_node,
                "kind": connection.get("kind") or "wire",
                "wire_no": connection.get("wire_no"),
                "net_id": connection.get("net_id"),
                "external_ref": connection.get("external_ref"),
                "note": connection.get("note"),
            }
        )

    grouped: dict[str, list[str]] = defaultdict(list)
    for edge in edges:
        grouped[dsu.find(edge["from"])].append(edge["from"])
        grouped[dsu.find(edge["to"])].append(edge["to"])

    components = []
    for index, members in enumerate(sorted((sorted(set(items)) for items in grouped.values()), key=lambda x: x[0]), start=1):
        class_counts = Counter(str(node_meta.get(member, {}).get("class_key")) for member in members)
        components.append(
            {
                "id": f"NET-{index:04d}",
                "members": members,
                "member_count": len(members),
                "class_counts": dict(sorted(class_counts.items())),
            }
        )

    expected_components = []
    if expected_netlist:
        for net in expected_netlist.get("nets", []):
            expected_components.append(
                {
                    "id": net["id"],
                    "wire_no": net.get("wire_no"),
                    "members": sorted(net.get("members", [])),
                }
            )

    generated_sets = {frozenset(component["members"]): component["id"] for component in components}
    expected_sets = {frozenset(component["members"]): component["id"] for component in expected_components}
    missing_expected = sorted(expected_sets.keys() - generated_sets.keys(), key=lambda item: sorted(item)[0])
    unexpected_generated = sorted(generated_sets.keys() - expected_sets.keys(), key=lambda item: sorted(item)[0])

    return {
        "project_id": project["id"],
        "sheet_no": project["sheet_no"],
        "source_file": project.get("source_file"),
        "image_size": {"width": width, "height": height},
        "symbol_count": len(project.get("symbols", [])),
        "terminal_count": sum(len(symbol.get("terminals") or []) for symbol in project.get("symbols", [])),
        "connection_count": len(edges),
        "metadata_node_count": len(node_meta),
        "connected_node_count": len(used_node_ids),
        "isolated_metadata_node_count": len(set(node_meta) - used_node_ids),
        "isolated_metadata_node_ids": sorted(set(node_meta) - used_node_ids),
        "component_count": len(components),
        "nodes": sorted(node_meta.values(), key=lambda node: node["id"]),
        "edges": edges,
        "components": components,
        "expected_netlist": {
            "component_count": len(expected_components),
            "components": expected_components,
        },
        "netlist_match": {
            "exact": not missing_expected and not unexpected_generated,
            "missing_expected_count": len(missing_expected),
            "unexpected_generated_count": len(unexpected_generated),
            "missing_expected": [
                {"net_id": expected_sets[items], "members": sorted(items)} for items in missing_expected
            ],
            "unexpected_generated": [
                {"component_id": generated_sets[items], "members": sorted(items)} for items in unexpected_generated
            ],
        },
        "issues": issues,
    }


def compare_csv_to_bundle(projects: list[dict[str, Any]], csv_rows: list[dict[str, str]]) -> dict[str, Any]:
    bundle_counter: Counter[tuple[str, str, str, str, str, str, str | None]] = Counter()
    csv_counter: Counter[tuple[str, str, str, str, str, str, str | None]] = Counter()
    project_sheet_by_id = {str(project["id"]): str(project["sheet_no"]) for project in projects}

    for project in projects:
        sheet_no = str(project["sheet_no"])
        for connection in project.get("connections", []):
            bundle_counter[
                (
                    sheet_no,
                    str(connection.get("from_symbol_ref") or ""),
                    str(connection.get("from_terminal_ref") or ""),
                    str(connection.get("to_symbol_ref") or ""),
                    str(connection.get("to_terminal_ref") or ""),
                    str(connection.get("kind") or ""),
                    connection.get("wire_no"),
                )
            ] += 1

    for row in csv_rows:
        sheet_no = row.get("sheet_no") or project_sheet_by_id.get(row.get("project_id", ""), "")
        csv_counter[
            (
                sheet_no,
                row.get("from_symbol_ref") or "",
                row.get("from_terminal") or "",
                row.get("to_symbol_ref") or "",
                row.get("to_terminal") or "",
                row.get("kind") or "",
                row.get("wire_no") or None,
            )
        ] += 1

    missing = list((bundle_counter - csv_counter).elements())
    extra = list((csv_counter - bundle_counter).elements())
    return {
        "exact": not missing and not extra,
        "bundle_connection_count": sum(bundle_counter.values()),
        "csv_connection_count": sum(csv_counter.values()),
        "missing_in_csv_count": len(missing),
        "extra_in_csv_count": len(extra),
        "missing_in_csv_sample": missing[:20],
        "extra_in_csv_sample": extra[:20],
    }


def analyze(zip_path: Path, out_dir: Path) -> dict[str, Any]:
    bundle, csv_rows, netlist = load_zip_payload(zip_path)
    out_dir.mkdir(parents=True, exist_ok=True)
    projects = bundle.get("projects", [])
    expected_by_project = {project["project_id"]: project for project in netlist.get("projects", [])}

    project_summaries = []
    aggregate_class_counts: Counter[str] = Counter()
    aggregate_terminal_counts: Counter[str] = Counter()
    aggregate_connection_kinds: Counter[str] = Counter()
    netlist_exact = True
    issue_count = 0

    for project in projects:
        for symbol in project.get("symbols", []):
            aggregate_class_counts[symbol["class_key"]] += 1
            aggregate_terminal_counts[symbol["class_key"]] += len(symbol.get("terminals") or [])
        for connection in project.get("connections", []):
            aggregate_connection_kinds[connection.get("kind") or ""] += 1

        graph = build_project_graph(project, expected_by_project.get(project["id"]))
        netlist_exact = netlist_exact and bool(graph["netlist_match"]["exact"])
        issue_count += len(graph["issues"])
        graph_path = out_dir / "graphs" / f"{project['sheet_no']}.json"
        write_json(graph_path, graph)
        project_summaries.append(
            {
                "project_id": graph["project_id"],
                "sheet_no": graph["sheet_no"],
                "source_file": graph["source_file"],
                "symbol_count": graph["symbol_count"],
                "terminal_count": graph["terminal_count"],
                "connection_count": graph["connection_count"],
                "metadata_node_count": graph["metadata_node_count"],
                "connected_node_count": graph["connected_node_count"],
                "isolated_metadata_node_count": graph["isolated_metadata_node_count"],
                "component_count": graph["component_count"],
                "expected_component_count": graph["expected_netlist"]["component_count"],
                "netlist_exact": graph["netlist_match"]["exact"],
                "issue_count": len(graph["issues"]),
                "graph_json": str(graph_path),
            }
        )

    csv_compare = compare_csv_to_bundle(projects, csv_rows)
    summary = {
        "schema_version": "todensekkei.annotation_graph_analysis.v1",
        "source_zip": str(zip_path),
        "bundle_schema_version": bundle.get("schema_version"),
        "tool": bundle.get("tool"),
        "exported_at": bundle.get("exported_at"),
        "project_count": len(projects),
        "class_counts": dict(sorted(aggregate_class_counts.items())),
        "terminal_counts_by_symbol_class": dict(sorted(aggregate_terminal_counts.items())),
        "connection_kind_counts": dict(sorted(aggregate_connection_kinds.items())),
        "project_summaries": project_summaries,
        "csv_compare": csv_compare,
        "netlist_compare": {
            "all_projects_exact": netlist_exact,
            "total_graph_issue_count": issue_count,
        },
    }
    write_json(out_dir / "summary.json", summary)
    return summary


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Analyze seq-annotator from-to and netlist graph consistency.")
    parser.add_argument("zip_path", type=Path)
    parser.add_argument("--out-dir", type=Path, default=Path("data/private/annotation_graph"))
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if not args.zip_path.exists():
        raise SystemExit(f"ZIP not found: {args.zip_path}")
    summary = analyze(args.zip_path, args.out_dir)
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
