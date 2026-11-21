"""「A を変えたらどうなるか」と波及範囲（DD-0029）。DB を使わない。

- what_if: 今の状態（外部入力・励磁）から、変更（入力の切り替え、リレーの手動操作・不動作、接点の溶着・接触不良、
  コイル・負荷の断線・短絡）を加えて解き直し、変わったリレー・負荷・接点を返す。
- run_sequence: 操作の手順（押す → 離す → 止める …）を順に解き、各手順で何が変わったかを返す。
- impact: 回路のつながりだけから、ある機器が動いたときに影響が及びうる機器・負荷を段数つきで返す（静的な波及範囲）。
  反対向き（その機器を動かしうる機器）も返す。

静的な波及範囲は、母線（P・0）で区切った「段（ラダーの 1 行）」の中に接点とコイル・負荷が一緒にあるかで決める。
接点の開閉の組み合わせは考えないので「影響しうる」範囲（取りこぼさない側）になる。実際に変わるかは what_if で確かめる。
"""

from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import replace
from typing import Any

from services.circuit_model import GROUND, R_OFF, R_ON, SUPPLY, Circuit, Element, simulate

SymbolKey = tuple[int, str]


def _is_bus(node: str) -> bool:
    return node in (GROUND, SUPPLY) or node.startswith(f"{SUPPLY}_")


def _key(item: dict[str, Any]) -> SymbolKey:
    return int(item["project_id"]), str(item["symbol_ref"])


def _symbol(e: Element) -> dict[str, Any]:
    return {"project_id": e.project_id, "symbol_ref": e.symbol_ref, "label": e.label}


# ------------------------------------------------------------------------------------------- もしも


def apply_change(circuit: Circuit, change: dict[str, Any] | None) -> tuple[Circuit, dict[str, Any]]:
    """変更を回路と simulate の引数に置き換える。

    change のキー（すべて任意）:
      inputs:   {器具番号: bool}                外部入力の切り替え
      devices:  {器具番号: bool}                リレー・入力の動作を固定（手で押し込む / 動かない）
      contacts: [{project_id, symbol_ref, closed}]  接点の固定（溶着 = True、接触不良 = False）
      open:     [{project_id, symbol_ref}]      断線（接点なら開のまま、コイルなら励磁しない、負荷なら電流が流れない）
      short:    [{project_id, symbol_ref}]      短絡（接点なら閉のまま、コイル・負荷なら両端がつながる）
    """
    change = change or {}
    by_symbol = {(e.project_id, e.symbol_ref): e for e in circuit.elements}
    forced_devices = {str(k): bool(v) for k, v in (change.get("devices") or {}).items()}
    forced_contacts: dict[str, bool] = {}
    resistance: dict[str, float] = {}
    broken: set[str] = set()
    unknown: list[dict[str, Any]] = []

    def element(item: dict[str, Any]) -> Element | None:
        e = by_symbol.get(_key(item))
        if e is None:
            unknown.append({"project_id": item.get("project_id"), "symbol_ref": item.get("symbol_ref")})
        return e

    for item in change.get("contacts") or []:
        e = element(item)
        if e is not None and e.kind in ("a", "b"):
            forced_contacts[e.name] = bool(item.get("closed"))
    for item in change.get("open") or []:
        e = element(item)
        if e is None:
            continue
        if e.kind in ("a", "b", "d"):  # ダイオードの断線は開のまま（DD-0037）
            forced_contacts[e.name] = False
        elif e.device in circuit.coils:  # 同じ器具番号のコイルは同じ 1 つのコイル（図面をまたいで描かれる）
            forced_devices[e.device] = False
            broken.add(e.name)
        else:
            resistance[e.name] = R_OFF
            broken.add(e.name)
    for item in change.get("short") or []:
        e = element(item)
        if e is None:
            continue
        if e.kind in ("a", "b", "d"):  # ダイオードの短絡は両向きに流れる
            forced_contacts[e.name] = True
        else:
            resistance[e.name] = R_ON
    if resistance:
        elements = [replace(e, resistance=resistance[e.name]) if e.name in resistance else e for e in circuit.elements]
        index = {e.name: e for e in elements}
        coils = {dev: [index[e.name] for e in es] for dev, es in circuit.coils.items()}
        circuit = replace(circuit, elements=elements, coils=coils)
    options = {"inputs": {str(k): bool(v) for k, v in (change.get("inputs") or {}).items()},
               "forced_devices": forced_devices, "forced_contacts": forced_contacts,
               "broken": broken, "unknown": unknown}
    return circuit, options


def _run(circuit: Circuit, inputs: dict[str, bool], energized: dict[str, bool], options: dict[str, Any],
         timer_outputs: dict[str, bool] | None = None) -> dict[str, Any]:
    result = simulate(circuit, inputs, energized, forced_devices=options.get("forced_devices"),
                      forced_contacts=options.get("forced_contacts"), timer_outputs=timer_outputs)
    broken = options.get("broken") or set()
    names = {(e.project_id, e.symbol_ref): e.name for e in circuit.elements}
    for load in result["loads"]:
        if names[(load["project_id"], load["symbol_ref"])] in broken:
            load["energized"] = False  # 断線した負荷は電圧がかかっても動かない
    return result


def state_of(result: dict[str, Any]) -> dict[str, Any]:
    """比べるための状態: リレー・負荷・接点。"""
    return {
        "coils": {dev: s["energized"] for dev, s in result["coils"].items()},
        "loads": {(x["project_id"], x["symbol_ref"]): x for x in result["loads"]},
        "contacts": {(x["project_id"], x["symbol_ref"]): x for x in result["contacts"]},
    }


def diff_results(before: dict[str, Any], after: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    """2 つのシミュレーション結果で変わったところ。"""
    a, b = state_of(before), state_of(after)
    coils = [{"device": dev, "before": a["coils"].get(dev), "after": b["coils"][dev]}
             for dev in sorted(b["coils"]) if a["coils"].get(dev) != b["coils"][dev]]
    loads = [{"project_id": k[0], "symbol_ref": k[1], "label": x["label"], "device": x["device"],
              "before": a["loads"][k]["energized"] if k in a["loads"] else None, "after": x["energized"]}
             for k, x in sorted(b["loads"].items()) if k not in a["loads"] or a["loads"][k]["energized"] != x["energized"]]
    contacts = [{"project_id": k[0], "symbol_ref": k[1], "device": x["device"], "kind": x["kind"],
                 "before": a["contacts"][k]["closed"] if k in a["contacts"] else None, "after": x["closed"]}
                for k, x in sorted(b["contacts"].items())
                if k not in a["contacts"] or a["contacts"][k]["closed"] != x["closed"]]
    return {"coils": coils, "loads": loads, "contacts": contacts}


def _brief(result: dict[str, Any]) -> dict[str, Any]:
    return {"coils": {dev: s["energized"] for dev, s in result["coils"].items()},
            "loads": [{k: x[k] for k in ("project_id", "symbol_ref", "label", "energized")} for x in result["loads"]],
            "warnings": result["warnings"], "steps": result["steps"], "engine": result["engine"]}


def what_if(circuit: Circuit, inputs: dict[str, bool], energized: dict[str, bool] | None,
            change: dict[str, Any] | None) -> dict[str, Any]:
    """今の状態（inputs・energized から解いた状態）に change を加えたら何が変わるか。"""
    before = simulate(circuit, inputs, energized)
    now = {dev: s["energized"] for dev, s in before["coils"].items()}
    changed_circuit, options = apply_change(circuit, change)
    after = _run(changed_circuit, {**inputs, **options["inputs"]}, now, options)
    return {"before": _brief(before), "after": _brief(after), "changes": diff_results(before, after),
            "unknown_symbols": options["unknown"], "warnings": after["warnings"], "result": after}


def run_sequence(circuit: Circuit, steps: list[dict[str, Any]], *, inputs: dict[str, bool] | None = None,
                 energized: dict[str, bool] | None = None, change: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """操作の手順を順に解く。各手順は {"inputs": {器具番号: bool}, "label": 任意} で、入力は前の手順から引き継ぐ。
    change（故障など）は全手順にかける。"""
    changed_circuit, options = apply_change(circuit, change)
    current = dict(inputs or {})
    previous = _run(changed_circuit, current, dict(energized or {}), options)
    out = [{"label": "初期状態", "inputs": dict(current), **_brief(previous), "changes": diff_results(previous, previous)}]
    for i, step in enumerate(steps, start=1):
        current = {**current, **{str(k): bool(v) for k, v in (step.get("inputs") or {}).items()}}
        state = {dev: s["energized"] for dev, s in previous["coils"].items()}
        result = _run(changed_circuit, current, state, options)
        out.append({"label": step.get("label") or f"手順 {i}", "inputs": dict(current), **_brief(result),
                    "changes": diff_results(previous, result)})
        previous = result
    return out


# --------------------------------------------------------------------------------------------- 波及範囲


def rungs(circuit: Circuit) -> dict[str, int]:
    """素子名 → 段の番号。母線（P・0）を通らずにつながる素子を同じ段にする。"""
    parent = {e.name: e.name for e in circuit.elements}

    def find(x: str) -> str:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    at_node: dict[str, list[str]] = defaultdict(list)
    for e in circuit.elements:
        for n in {e.a, e.b}:
            if not _is_bus(n):
                at_node[n].append(e.name)
    for names in at_node.values():
        for other in names[1:]:
            parent[find(other)] = find(names[0])
    roots: dict[str, int] = {}
    return {e.name: roots.setdefault(find(e.name), len(roots) + 1) for e in circuit.elements}


def dependency_graph(circuit: Circuit) -> list[dict[str, Any]]:
    """機器 → 影響しうる負荷の辺。接点（器具番号 X）と同じ段にあるコイル・負荷を X の影響先にする。"""
    rung = rungs(circuit)
    loads_in: dict[int, list[Element]] = defaultdict(list)
    for e in circuit.elements:
        if e.kind == "load":
            loads_in[rung[e.name]].append(e)
    edges = []
    for e in circuit.elements:
        if e.kind not in ("a", "b") or not e.device:
            continue
        for load in loads_in[rung[e.name]]:
            edges.append({"from": e.device, "contact": _symbol(e) | {"kind": e.kind},
                          "load": _symbol(load), "to": load.device if load.device in circuit.coils else None,
                          "rung": rung[e.name]})
    return edges


def impact(circuit: Circuit, device: str, *, max_depth: int = 20) -> dict[str, Any]:
    """device が動いたら影響しうる機器・負荷（下流）と、device を動かしうる機器（上流）。"""
    known = set(circuit.coils) | set(circuit.inputs)
    if device not in known:
        raise ValueError(f"器具番号 {device} が回路にありません")
    edges = dependency_graph(circuit)
    by_from: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for edge in edges:
        by_from[edge["from"]].append(edge)

    devices: dict[str, dict[str, Any]] = {}
    loads: dict[SymbolKey, dict[str, Any]] = {}
    queue = deque([(device, 0)])
    seen = {device}
    while queue:
        dev, depth = queue.popleft()
        if depth >= max_depth:
            continue
        for edge in by_from.get(dev, []):
            key = _key(edge["load"])
            if key not in loads:
                loads[key] = {**edge["load"], "depth": depth + 1, "device": edge["to"], "via": dev,
                              "contact": edge["contact"]}
            target = edge["to"]
            if target and target not in seen:
                seen.add(target)
                devices[target] = {"device": target, "depth": depth + 1, "via": dev, "contact": edge["contact"]}
                queue.append((target, depth + 1))

    coil_names = {e.name for e in circuit.coils.get(device, [])}
    rung = rungs(circuit)
    own_rungs = {rung[n] for n in coil_names}
    upstream = sorted({e.device for e in circuit.elements
                       if e.kind in ("a", "b") and e.device and rung[e.name] in own_rungs and e.device != device})
    return {
        "device": device,
        "kind": "relay" if device in circuit.coils else "input",
        "devices": sorted(devices.values(), key=lambda d: (d["depth"], d["device"])),
        "loads": sorted(loads.values(), key=lambda x: (x["depth"], x["project_id"], x["symbol_ref"])),
        "upstream": upstream,
        "contacts": [_symbol(e) | {"kind": e.kind} for e in circuit.elements if e.kind in ("a", "b") and e.device == device],
    }
