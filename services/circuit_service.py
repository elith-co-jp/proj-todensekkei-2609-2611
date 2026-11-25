"""回路シミュレーション（DD-0009）: 保存済みのアノテーションから回路を組み、On/Off を解く。"""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from models import AnnotationImage, AnnotationProject
from services import (
    circuit_code,
    circuit_edit,
    circuit_interop,
    circuit_model,
    circuit_timing,
    circuit_whatif,
    consistency,
    vector_pdf,
)
from services.annotation_service import load_pages
from services.circuit_model import SUPPLY_V, Circuit, build_circuit, export_netlist, simulate
from services.jst import iso_jst
from services.structure_service import get_structure_texts, get_structures

LIVE_RATIO = 0.5


def _pages(db: Session, project_ids: list[int], *, use_ai: bool = False
           ) -> tuple[list[dict[str, Any]], dict[int, list[dict[str, Any]]]]:
    pages = load_pages(db, project_ids)
    if not pages:
        raise ValueError("図面を選んでください")
    if use_ai:
        _fill_from_ai(db, pages)
    ids = [p["id"] for p in pages]
    texts = get_structure_texts(db, ids)
    if missing := [pid for pid in ids if pid not in texts]:
        shas: dict[int, str] = {}
        for pid, sha in db.execute(select(AnnotationImage.project_id, AnnotationImage.sha256)
                                   .where(AnnotationImage.project_id.in_(missing)).order_by(AnnotationImage.id)):
            shas.setdefault(pid, sha)
        for pid in missing:
            texts[pid] = _vector_texts(pid, shas.get(pid))
    return pages, {pid: texts[pid] for pid in ids}


def _fill_from_ai(db: Session, pages: list[dict[str, Any]]) -> None:
    """記号を 1 つも登録していない図面は、AI 解析の結果（記号・端子・配線）をそのまま使う（DD-0046）。

    保存はしない。読むたびに同じ候補から同じ記号番号（SYM-0001…）で組むので、図面の上の位置も毎回同じになる。
    各図面の source は annotation（人が登録・確認した内容）/ ai（AI 解析の結果だけ）/ none（どちらも無い）。
    """
    from services.ai_candidates import from_structure, plan_import
    from services.annotation_service import classes_ordered

    for p in pages:
        p["source"] = "annotation" if p["symbols"] else "none"
    empty = [p for p in pages if not p["symbols"]]
    if not empty:
        return
    found = get_structures(db, [p["id"] for p in empty])
    classes = classes_ordered(db)
    keys = [c.key for c in classes]
    label_of = {c.key: c.label for c in classes}
    for p in empty:
        structure = found.get(p["id"])
        if structure is None:
            continue
        plan = plan_import(from_structure(structure, keys), [], [], p["image_width"], p["image_height"])
        p["symbols"] = [{**s, "class_label": label_of.get(s["class_key"], "その他"), "attributes": {}} for s in plan.symbols]
        p["connections"] = [{**c, "ref": f"AI-{i:04d}", "attributes": {}} for i, c in enumerate(plan.connections, start=1)]
        p["source"] = "ai"


def _structures(db: Session, pages: list[dict[str, Any]]) -> dict[int, Any]:
    """図面ごとの構造解析の結果（無い図面は None）。1 回のクエリで読む。"""
    found = get_structures(db, [p["id"] for p in pages])
    return {p["id"]: found.get(p["id"]) for p in pages}


def _vector_texts(project_id: int, image_sha256: str | None) -> list[dict[str, Any]]:
    """構造解析の文字が無ければ、ベクター PDF の印字（DD-0035）で母線の PER/NER を決める。"""
    result = vector_pdf.load(project_id, image_sha256)
    return vector_pdf.structure_texts(result) if result and result.get("kind") != "scan" else []


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
    return _simulation(build_circuit(pages, texts), pages, inputs, energized)


def _simulation(circuit: Circuit, pages: list[dict[str, Any]], inputs: dict[str, bool],
                energized: dict[str, bool]) -> dict[str, Any]:
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
    structures = _structures(db, pages)
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
    return pages, texts, _structures(db, pages)


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


# ------------------------------------------------------------------ 図面を読む手順の機械化（DD-0035）


def trace(db: Session, project_ids: list[int], *, mode: str, target: str | None = None,
          symbol: dict[str, Any] | None = None, device: str | None = None, states: dict[str, bool | None] | None = None,
          energized: dict[str, bool] | None = None, external: list[str] | None = None) -> dict[str, Any]:
    """逆引き（成立条件と 3 値の判定・繰り返し辿った木）か順引き（影響先）。"""
    from services import circuit_trace

    circuit, pages = _circuit(db, project_ids)
    tracer = circuit_trace.Tracer(circuit, pages, external=external or [])
    if mode == "forward":
        if not device:
            raise ValueError("順引きの起点（器具番号）を指定してください")
        return {"mode": mode, **tracer.effects(device, states=states, energized=energized)}
    goal = symbol or target
    if not goal:
        raise ValueError("逆引きの対象（コイルの器具番号・負荷のラベル・記号）を指定してください")
    result = tracer.evaluate(goal, states or {}, energized)
    return {"mode": "backward", **result, "explain": tracer.explain(goal),
            "sources": {d["device"]: d for d in result["devices"]}}


def trace_targets(db: Session, project_ids: list[int]) -> dict[str, Any]:
    """逆引きの対象（コイル・負荷）と、順引きの起点（接点の器具）の一覧と出どころ。"""
    from services import circuit_trace

    circuit, pages = _circuit(db, project_ids)
    tracer = circuit_trace.Tracer(circuit, pages)
    return {"loads": [{"name": name, **ref} for name, ref in tracer.all_loads()],
            "devices": [{k: v for k, v in src.items() if k not in ("contacts", "coils")}
                        | {"source_label": circuit_trace.SOURCE_LABEL[src["source"]], "contact_count": len(src.get("contacts", []))}
                        for _, src in sorted(tracer.sources.items())]}


def evaluate_scenarios(db: Session, project_ids: list[int], scenarios: list[dict[str, Any]],
                       external: list[str] | None = None) -> dict[str, Any]:
    """代表回路の正解（シナリオ）と機械の判定の照合。"""
    from services import circuit_scenarios

    circuit, pages = _circuit(db, project_ids)
    return circuit_scenarios.evaluate(circuit, pages, scenarios, external=external or [])


def annotation_table_rows(db: Session, project_ids: list[int], drawing_set: str = "") -> list[dict[str, str]]:
    from services import annotation_table

    circuit, pages = _circuit(db, project_ids)
    return annotation_table.export_rows(pages, circuit, drawing_set=drawing_set)


def device_index(db: Session, project_ids: list[int]) -> dict[str, Any]:
    """器具ごとのコイルと接点の相互参照、電線番号の一覧（DD-0044）。"""
    from services import device_index as di

    circuit, pages = _circuit(db, project_ids)
    return di.build_index(pages, circuit)


# ------------------------------------------------------------------ 論理図（DD-0045）


def _logic(db: Session, project_ids: list[int], targets: list[str] | None, names: dict[str, str] | None,
           external: list[str] | None, merge: bool) -> tuple[Circuit, list[dict[str, Any]], Any]:
    from services import logic_diagram

    circuit, pages = _circuit(db, project_ids)
    title = "、".join(p.get("sheet_no") or p["name"] for p in pages)
    diagram = logic_diagram.build_logic(circuit, pages, targets=targets or None, external=external or [], names=names,
                                        merge=merge, title=title)
    return circuit, pages, diagram


def logic_diagram_view(db: Session, project_ids: list[int], *, targets: list[str] | None = None,
                       names: dict[str, str] | None = None, external: list[str] | None = None, merge: bool = True,
                       inputs: dict[str, bool] | None = None, energized: dict[str, bool] | None = None,
                       values: dict[str, bool] | None = None) -> dict[str, Any]:
    """出力のコイルから辿った論理図（AND・OR・NOT・タイマー・キープリレー）と SVG。

    values（信号 ID → ON/OFF。シミュレーションの記録から）か inputs で状態を決め、成立している線をピンクで示す。"""
    from services import circuit_trace, logic_diagram

    circuit, _pages_, diagram = _logic(db, project_ids, targets, names, external, merge)
    if values is None:
        solved = logic_diagram.solve(diagram, inputs or {}, energized or {})
        values = solved["values"]
        warnings = solved["warnings"]
    else:
        warnings = []
    tracer = circuit_trace.Tracer(circuit)
    return {**diagram.json(), "svg": logic_diagram.render_svg(diagram, values), "formulas": logic_diagram.formulas_text(diagram),
            "values": values, "warnings": warnings,
            "candidates": [{"device": d, "default": d in diagram.outputs or any(diagram.alias.get(d, ("",))[0] == o
                                                                                  for o in diagram.outputs)}
                           for d in sorted(circuit.coils)],
            "default_targets": logic_diagram.default_targets(circuit, tracer)}


def logic_simulate(db: Session, project_ids: list[int], events: list[dict[str, Any]], *, targets: list[str] | None = None,
                   names: dict[str, str] | None = None, external: list[str] | None = None, merge: bool = True,
                   inputs: dict[str, bool] | None = None, energized: dict[str, bool] | None = None,
                   until: float | None = None, compare: bool = False) -> dict[str, Any]:
    """論理図の上で時刻つきの操作を解く。compare なら同じ操作を回路の計算でも解き、リレーごとに照合する。"""
    from services import logic_diagram

    circuit, _pages_, diagram = _logic(db, project_ids, targets, names, external, merge)
    out = logic_diagram.simulate(diagram, events, inputs=inputs, energized=energized, until=until)
    out["names"] = {sig: diagram.ref_label(sig) for sig in diagram.signals}
    out["letters"] = {sig: s.letter for sig, s in diagram.signals.items() if s.letter}
    if compare:
        out["compare"] = logic_diagram.compare_with_circuit(circuit, diagram, events, inputs=inputs, energized=energized,
                                                            until=until)
    return out


def logic_svg(db: Session, project_ids: list[int], **kwargs: Any) -> str:
    return logic_diagram_view(db, project_ids, **kwargs)["svg"]


# ------------------------------------------------------------------ デモ（DD-0046）


def demo_view(db: Session, project_ids: list[int], *, inputs: dict[str, bool] | None = None,
              energized: dict[str, bool] | None = None, targets: list[str] | None = None) -> dict[str, Any]:
    """図面と論理図を並べて動かすデモの 1 画面分。

    人が登録した記号の無い図面は AI 解析の結果で組む（PDF を登録して解析しただけで動かせる）。
    同じ入力で回路の計算（図面の上の通電）と論理図（成立している線）を解き、論理図の信号ごとに
    図面の上の位置（コイル・接点）を返す。"""
    from services import logic_diagram

    inputs = inputs or {}
    energized = energized or {}
    pages, texts = _pages(db, project_ids, use_ai=True)
    page_info = [{"id": p["id"], "name": p.get("sheet_no") or p["name"], "source": p.get("source", "annotation"),
                  "symbols": len(p["symbols"]), "connections": len(p["connections"]),
                  "width": p["image_width"], "height": p["image_height"]} for p in pages]
    if not any(p["symbols"] for p in pages):
        return {"pages": page_info, "ready": False,
                "message": "記号が登録されておらず、AI 解析の結果もありません。AI 解析を実行してください。"}
    circuit = build_circuit(pages, texts)
    sim = _simulation(circuit, pages, inputs, energized)
    title = "、".join(p.get("sheet_no") or p["name"] for p in pages)
    out: dict[str, Any] = {"pages": page_info, "ready": True, "simulation": sim}
    try:
        diagram = logic_diagram.build_logic(circuit, pages, targets=targets or None, title=title)
    except ValueError as e:
        return {**out, "logic": None, "message": str(e)}
    def known(values: dict[str, bool]) -> dict[str, bool]:
        # 論理図に描いていない（出力に関係しない）器具は除く
        out = {}
        for k, v in values.items():
            try:
                diagram.resolve(k)
            except ValueError:
                continue
            out[k] = v
        return out

    energized_now = {dev: bool(st["energized"]) for dev, st in sim["coil_states"].items()}
    solved = logic_diagram.solve(diagram, known(inputs), known(energized_now))
    out["logic"] = {**diagram.json(), "svg": logic_diagram.render_svg(diagram, solved["values"]),
                    "values": solved["values"], "warnings": solved["warnings"],
                    "locations": logic_diagram.locations(circuit, diagram, pages)}
    return out
