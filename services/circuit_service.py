"""回路シミュレーション（DD-0009）: 保存済みのアノテーションから回路を組み、On/Off を解く。"""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy.orm import Session

from models import AnnotationProject
from services import (
    circuit_code,
    circuit_edit,
    circuit_interop,
    circuit_model,
    circuit_timing,
    circuit_whatif,
    consistency,
)
from services.annotation_service import WITH_ANNOTATIONS, serialize_project
from services.circuit_model import SUPPLY_V, Circuit, build_circuit, export_netlist, simulate
from services.jst import iso_jst
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
        # 行先札・来先札でつないだ図面間の組（DD-0030）
        "sheet_links": [{"how": link["how"], "from": {**link["from"], "page": names.get(link["from"]["project_id"])},
                         "to": {**link["to"], "page": names.get(link["to"]["project_id"])}} for link in circuit.sheet_links],
        # タイマー（DD-0033）: コイルの属性 timer_mode・delay_s
        "timers": [{"device": dev, **cfg} for dev, cfg in sorted(circuit.timers.items())],
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


def circuit_timeline(db: Session, project_ids: list[int], events: list[dict[str, Any]], inputs: dict[str, bool],
                     energized: dict[str, bool], change: dict[str, Any], delays: dict[str, float],
                     until: float | None) -> dict[str, Any]:
    """時刻つきの操作とタイマーで、時間の流れを解く（DD-0033）。"""
    circuit, pages = _circuit(db, project_ids)
    out = circuit_timing.run_timeline(circuit, events, inputs=inputs, energized=energized, until=until, change=change,
                                      delays=delays)
    out["timeline"] = [{**s, "changes": _with_pages(s["changes"], pages)} for s in out["timeline"]]
    out["timers"] = [{"device": dev, **cfg} for dev, cfg in sorted(out["timers"].items())]
    return out


def circuit_impact(db: Session, project_ids: list[int], device: str) -> dict[str, Any]:
    """機器が動いたら影響しうる範囲（DD-0029）。"""
    circuit, pages = _circuit(db, project_ids)
    result = circuit_whatif.impact(circuit, device)
    names = {p["id"]: p.get("sheet_no") or p["name"] for p in pages}
    result["loads"] = [{**x, "page": names.get(x["project_id"])} for x in result["loads"]]
    return result


FIX_CLASSES = frozenset({"contact_a", "contact_b", "relay_coil"})


def consistency_check(db: Session, project_ids: list[int]) -> dict[str, Any]:
    """複数の解析方法の結果の矛盾と、精査の結果・直し方（DD-0031）。"""
    pages, texts = _pages(db, project_ids)
    structures = {p["id"]: get_structure(db, p["id"]) for p in pages}
    return consistency.check(pages, structures, bus_texts=texts)


def apply_consistency_fixes(db: Session, project_ids: list[int], issue_ids: list[str]) -> dict[str, Any]:
    """選んだ矛盾の直し方（記号の種類・ラベルの変更）を適用し、もう一度チェックした結果を返す（DD-0031）。

    直し方はクライアントから受け取らず、いまの図面でチェックし直して出た直し方だけを使う（古い画面からの適用を防ぐ）。
    """
    from models import AnnotationSymbol
    from services.annotation_service import class_map_by_key

    current = consistency_check(db, project_ids)
    wanted = set(issue_ids)
    fixes = [i["fix"] for i in current["issues"] if i["id"] in wanted and i.get("fix")]
    classes = class_map_by_key(db)
    applied = []
    for fix in fixes:
        row = db.query(AnnotationSymbol).filter(AnnotationSymbol.project_id == fix["project_id"],
                                                AnnotationSymbol.ref == fix["symbol_ref"]).one_or_none()
        if row is None:
            continue
        if fix["type"] == "set_class":
            if fix["value"] not in FIX_CLASSES or fix["value"] not in classes:
                continue
            before = next((k for k, c in classes.items() if c.id == row.class_id), "")
            row.class_id = classes[fix["value"]].id
        elif fix["type"] == "set_label":
            before = row.label or ""
            row.label = str(fix["value"])[:200]
        else:
            continue
        note = f"整合性チェック（DD-0031）で {before or '空'} → {fix['value']} に変更"
        row.note = f"{row.note}\n{note}" if row.note else note
        applied.append({**fix, "before": before})
    db.commit()
    db.expire_all()
    result = consistency_check(db, project_ids)
    result["applied"] = applied
    result["not_applied"] = sorted(wanted - {i["id"] for i in current["issues"] if i.get("fix")})
    return result


# --------------------------------------------------------------------------------------- 変更（DD-0034）


def _edit_inputs(db: Session, project_ids: list[int]) -> tuple[list[dict[str, Any]], dict[int, Any], dict[int, Any]]:
    pages, texts = _pages(db, project_ids)
    structures = {p["id"]: get_structure(db, p["id"]) for p in pages}
    return pages, texts, structures


def edit_context(db: Session, project_ids: list[int]) -> dict[str, Any]:
    """LLM に渡す図面の文脈（記号・端子・配線・回路コード・器具の一覧・図面のハッシュ）。"""
    pages, texts, _ = _edit_inputs(db, project_ids)
    return circuit_edit.context(pages, texts)


def edit_preview(db: Session, project_ids: list[int], ops: list[dict[str, Any]]) -> dict[str, Any]:
    """変更を試す（図面は変えない）。"""
    pages, texts, structures = _edit_inputs(db, project_ids)
    result = circuit_edit.preview(pages, ops, structures=structures, bus_texts=texts)
    result.pop("after_pages", None)
    return result


def _save_pages(db: Session, pages: list[dict[str, Any]], note: str | None = None) -> None:
    from schemas import AnnotationUpdatePayload
    from services.annotation_service import replace_annotations

    for page in pages:
        project = db.get(AnnotationProject, page["id"])
        symbols = []
        for s in page["symbols"]:
            item = {k: s.get(k) for k in ("ref", "class_key", "label", "cx", "cy", "w", "h", "note", "origin", "confidence",
                                          "attributes")}
            item["attributes"] = item["attributes"] or {}
            item["origin"] = item["origin"] if item["origin"] in ("manual", "inference") else "manual"
            item["terminals"] = [{k: t.get(k) for k in ("ref", "name", "tx", "ty")} | {"origin": "manual" if t.get("origin")
                                  not in ("manual", "inference") else t["origin"]} for t in s.get("terminals") or []]
            symbols.append(item)
        connections = [{k: c.get(k) for k in ("ref", "from_symbol_ref", "from_terminal_ref", "to_symbol_ref", "to_terminal_ref",
                                              "wire_no", "net_id", "kind", "external_ref", "note", "origin")}
                       | {"attributes": c.get("attributes") or {}} for c in page["connections"]]
        for c in connections:
            c["origin"] = c["origin"] if c["origin"] in ("manual", "inference") else "manual"
            c["kind"] = c["kind"] or "wire"
        replace_annotations(db, project, AnnotationUpdatePayload.model_validate({"symbols": symbols, "connections": connections}))
    db.expire_all()


def _changed_pages(before: list[dict[str, Any]], after: list[dict[str, Any]]) -> list[dict[str, Any]]:
    old = {p["id"]: circuit_edit.pages_hash([p]) for p in before}
    return [p for p in after if circuit_edit.pages_hash([p]) != old.get(p["id"])]


def _change_set_out(row: Any) -> dict[str, Any]:
    return {"id": row.id, "title": row.title, "reason": row.reason, "author": row.author, "status": row.status,
            "project_ids": json.loads(row.project_ids_json), "ops": json.loads(row.ops_json),
            "notes": json.loads(row.notes_json or "[]"), "summary": json.loads(row.summary_json or "{}"),
            "created_at": iso_jst(row.created_at),
            "undone_at": iso_jst(row.undone_at)}


def edit_apply(db: Session, project_ids: list[int], ops: list[dict[str, Any]], *, title: str, reason: str | None,
               author: str, base_hash: str | None) -> dict[str, Any]:
    """変更を適用し、変更セットとして残す。base_hash（試した時の図面のハッシュ）が今の図面と違えば適用しない。"""
    from models import CircuitChangeSet

    pages, texts, structures = _edit_inputs(db, project_ids)
    current = circuit_edit.pages_hash(pages)
    if base_hash and base_hash != current:
        raise EditConflict("試した後に図面が変わっています。もう一度試してから適用してください")
    result = circuit_edit.preview(pages, ops, structures=structures, bus_texts=texts)
    if not result["ok"]:
        raise circuit_edit.EditError(result["errors"])
    after_pages = result.pop("after_pages")
    changed = _changed_pages(pages, after_pages)
    before_changed = [p for p in pages if p["id"] in {c["id"] for c in changed}]
    _save_pages(db, changed)
    summary = {"behaviour": result["behaviour"][:20], "impact": result["impact"], "circuit": result["circuit"],
               "validation": {k: v for k, v in result["validation"].items() if k != "diagnostics_added"},
               "netlist_diff": result["netlist_diff"]}
    row = CircuitChangeSet(title=title[:500], reason=reason, author=(author or "user")[:100],
                           project_ids_json=json.dumps(sorted(project_ids)), ops_json=json.dumps(ops, ensure_ascii=False),
                           notes_json=json.dumps(result["notes"], ensure_ascii=False),
                           summary_json=json.dumps(summary, ensure_ascii=False, default=str),
                           before_json=json.dumps(before_changed, ensure_ascii=False, default=str),
                           after_hash=circuit_edit.pages_hash(_pages(db, project_ids)[0]))
    db.add(row)
    db.commit()
    return {"change_set": _change_set_out(row), "preview": result}


class EditConflict(ValueError):
    """変更の前提（図面の状態）が違う。"""


def edit_history(db: Session, project_ids: list[int] | None = None, limit: int = 50) -> list[dict[str, Any]]:
    from models import CircuitChangeSet

    rows = db.query(CircuitChangeSet).order_by(CircuitChangeSet.id.desc()).limit(500).all()
    wanted = set(project_ids or [])
    out = [_change_set_out(r) for r in rows if not wanted or wanted & set(json.loads(r.project_ids_json))]
    return out[:limit]


def edit_undo(db: Session, change_set_id: int, *, force: bool = False) -> dict[str, Any]:
    """変更セットを取り消す（変更前の図面に戻す）。その後に図面が変わっていれば、force でない限り取り消さない。"""
    from models import CircuitChangeSet, utc_now_naive

    row = db.get(CircuitChangeSet, change_set_id)
    if row is None:
        raise ValueError(f"変更 {change_set_id} がありません")
    if row.status != "applied":
        raise EditConflict(f"変更 {change_set_id} はもう取り消されています")
    project_ids = json.loads(row.project_ids_json)
    current = circuit_edit.pages_hash(_pages(db, project_ids)[0])
    if current != row.after_hash and not force:
        raise EditConflict("この変更の後に図面が変わっています。後の変更を先に取り消すか、force で戻してください")
    _save_pages(db, json.loads(row.before_json))
    row.status = "undone"
    row.undone_at = utc_now_naive()
    db.commit()
    return _change_set_out(row)
