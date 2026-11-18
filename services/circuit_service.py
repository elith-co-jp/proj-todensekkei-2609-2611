"""回路シミュレーション（DD-0009）: 保存済みのアノテーションから回路を組み、On/Off を解く。"""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from models import AnnotationProject
from services import circuit_code, circuit_interop, circuit_model, circuit_whatif
from services.annotation_service import WITH_ANNOTATIONS, serialize_project
from services.circuit_model import SUPPLY_V, Circuit, build_circuit, export_netlist, simulate
from services.structure_service import get_structure

LIVE_RATIO = 0.5


def _pages(db: Session, project_ids: list[int]) -> tuple[list[dict[str, Any]], dict[int, list[dict[str, Any]]]]:
    projects = db.query(AnnotationProject).options(*WITH_ANNOTATIONS).filter(AnnotationProject.id.in_(project_ids)).all()
    if not projects:
        raise ValueError("図面を選んでください")
    pages = [serialize_project(db, p, with_image_meta=False) for p in sorted(projects, key=lambda p: p.id)]
    texts = {p["id"]: (get_structure(db, p["id"]) or {}).get("texts") or [] for p in pages}
    return pages, texts


def _label_of(pages: list[dict[str, Any]]) -> dict[tuple[int, str], dict[str, Any]]:
    return {(p["id"], s["ref"]): s for p in pages for s in p["symbols"]}


def _summary(circuit: Circuit, pages: list[dict[str, Any]]) -> dict[str, Any]:
    names = {p["id"]: p.get("sheet_no") or p["name"] for p in pages}
    contacts_of: dict[str, list[dict[str, Any]]] = {}
    for e in circuit.elements:
        if e.kind in ("a", "b"):
            contacts_of.setdefault(e.device, []).append({"project_id": e.project_id, "symbol_ref": e.symbol_ref, "kind": e.kind})
    return {
        "engine": "ngspice" if circuit_model.ngspice_path() else "builtin",
        "pages": [{"id": p["id"], "name": names[p["id"]]} for p in pages],
        "element_count": len(circuit.elements),
        "node_count": len(circuit.nodes),
        "supplies": [n for n in circuit.nodes if n.startswith("P")],
        "coils": [{"device": dev, "symbols": [{"project_id": e.project_id, "symbol_ref": e.symbol_ref} for e in es],
                   "contacts": contacts_of.get(dev, [])} for dev, es in sorted(circuit.coils.items())],
        "inputs": [{"device": dev, "contacts": contacts_of.get(dev, [])} for dev in circuit.inputs],
        "diagnostics": [{**d, "page": names.get(d.get("project_id"))} for d in circuit.diagnostics],
    }


def circuit_summary(db: Session, project_ids: list[int]) -> dict[str, Any]:
    pages, texts = _pages(db, project_ids)
    return _summary(build_circuit(pages, texts), pages)


def run_simulation(db: Session, project_ids: list[int], inputs: dict[str, bool], energized: dict[str, bool]) -> dict[str, Any]:
    pages, texts = _pages(db, project_ids)
    circuit = build_circuit(pages, texts)
    result = simulate(circuit, inputs, energized)
    symbols = _label_of(pages)
    terminal_pos = {(pid, s["ref"], t["ref"]): (t["tx"], t["ty"]) for (pid, _), s in symbols.items() for t in s.get("terminals") or []}
    volts = result["nodes"]
    live = {n for n, v in volts.items() if v >= LIVE_RATIO * SUPPLY_V}
    overlay: dict[int, dict[str, list]] = {p["id"]: {"wires": [], "contacts": [], "coils": []} for p in pages}
    for w in circuit.wires:
        a, b = terminal_pos.get(w["from"]), terminal_pos.get(w["to"])
        if a and b:
            overlay[w["project_id"]]["wires"].append({"x1": a[0], "y1": a[1], "x2": b[0], "y2": b[1],
                                                      "live": circuit.node_of[w["from"]] in live})
    for c in result["contacts"]:
        s = symbols[(c["project_id"], c["symbol_ref"])]
        overlay[c["project_id"]]["contacts"].append({**c, "cx": s["cx"], "cy": s["cy"], "w": s["w"], "h": s["h"], "label": s.get("label")})
    for dev, es in circuit.coils.items():
        for e in es:
            s = symbols[(e.project_id, e.symbol_ref)]
            overlay[e.project_id]["coils"].append({"device": dev, "symbol_ref": e.symbol_ref, "cx": s["cx"], "cy": s["cy"],
                                                   "w": s["w"], "h": s["h"], "energized": result["coils"][dev]["energized"]})
    return {
        **_summary(circuit, pages),
        "engine": result["engine"], "steps": result["steps"], "warnings": result["warnings"],
        "coil_states": result["coils"], "overlay": [{"project_id": pid, **o} for pid, o in overlay.items()],
    }


def circuit_netlist(db: Session, project_ids: list[int], inputs: dict[str, bool]) -> str:
    pages, texts = _pages(db, project_ids)
    return export_netlist(build_circuit(pages, texts), inputs)


def circuit_lcapy(db: Session, project_ids: list[int], inputs: dict[str, bool], energized: dict[str, bool],
                  fixed: bool) -> str:
    """Lcapy のネットリスト（DD-0028）。fixed なら外部入力と励磁から接点の開閉を決めて、そのまま直流解析できる形にする。"""
    pages, texts = _pages(db, project_ids)
    circuit = build_circuit(pages, texts)
    closed = circuit_interop.contact_states(circuit, inputs, energized) if fixed else None
    return circuit_interop.lcapy_netlist(circuit, closed, pages=pages)


def circuit_skidl(db: Session, project_ids: list[int]) -> str:
    """実行すると KiCad のネットリストを書き出す SKiDL のスクリプト（DD-0028）。"""
    pages, texts = _pages(db, project_ids)
    return circuit_interop.skidl_script(build_circuit(pages, texts), pages=pages)


def _circuit(db: Session, project_ids: list[int]) -> tuple[Circuit, list[dict[str, Any]]]:
    pages, texts = _pages(db, project_ids)
    return build_circuit(pages, texts), pages


def circuit_code_text(db: Session, project_ids: list[int]) -> str:
    """回路を書き換えられる Python のコードにする（DD-0029）。"""
    circuit, pages = _circuit(db, project_ids)
    title = "、".join(p.get("sheet_no") or p["name"] for p in pages)
    return circuit_code.to_python(circuit, pages=pages, title=title)


def _with_pages(changes: dict[str, list[dict[str, Any]]], pages: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    names = {p["id"]: p.get("sheet_no") or p["name"] for p in pages}
    return {k: [{**x, "page": names.get(x.get("project_id"))} if "project_id" in x else x for x in v]
            for k, v in changes.items()}


def circuit_what_if(db: Session, project_ids: list[int], inputs: dict[str, bool], energized: dict[str, bool],
                    change: dict[str, Any]) -> dict[str, Any]:
    """今の状態に変更を加えたら何が変わるか（DD-0029）。"""
    circuit, pages = _circuit(db, project_ids)
    result = circuit_whatif.what_if(circuit, inputs, energized, change)
    result.pop("result")
    result["changes"] = _with_pages(result["changes"], pages)
    return result


def circuit_sequence(db: Session, project_ids: list[int], steps: list[dict[str, Any]], inputs: dict[str, bool],
                     energized: dict[str, bool], change: dict[str, Any]) -> dict[str, Any]:
    """操作の手順を順に解く（DD-0029）。"""
    circuit, pages = _circuit(db, project_ids)
    out = circuit_whatif.run_sequence(circuit, steps, inputs=inputs, energized=energized, change=change)
    return {"steps": [{**s, "changes": _with_pages(s["changes"], pages)} for s in out]}


def circuit_impact(db: Session, project_ids: list[int], device: str) -> dict[str, Any]:
    """機器が動いたら影響しうる範囲（DD-0029）。"""
    circuit, pages = _circuit(db, project_ids)
    result = circuit_whatif.impact(circuit, device)
    names = {p["id"]: p.get("sheet_no") or p["name"] for p in pages}
    result["loads"] = [{**x, "page": names.get(x["project_id"])} for x in result["loads"]]
    return result
