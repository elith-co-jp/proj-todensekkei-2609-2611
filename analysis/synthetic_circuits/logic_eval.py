"""正解つきの合成回路（logic.py）で、回路の理解・シミュレーション・矛盾チェックを検証する（DD-0032）。

1. 再現: 図面から組んだ回路が正解の器具（リレー・入力）をそろえ、行先札を全部つなぐか。
2. 動作: シミュレーション（内蔵の回路計算）と、正解の論理式（oracle）が、ランダムな入力・励磁と操作の手順で一致するか。
   回路のコード（DD-0029）に書き出して実行し直した回路も同じ動作か。
3. 波及範囲: 静的な波及範囲が、正解の依存関係から求めた範囲と一致するか。
4. 矛盾チェック: 誤りの無い図面で矛盾 0 か（誤検出）。誤りを 1 つ入れた図面で、見つけるか・場所を指すか・直し方で
   正解に戻るか・誤った直し方を出さないか。

誤り（fault）は、図面の読み取り（AI の検出・OCR・配線の推定）で起きる種類にする。
"""

from __future__ import annotations

import copy
import random
from collections import Counter, defaultdict
from typing import Any

from analysis.synthetic_circuits import logic
from services import circuit_code, circuit_model, circuit_whatif, consistency
from services.circuit_model import canonical_device

FAULTS = ["flip_ab", "coil_label", "contact_label", "contact_sheet_ref", "drop_contact", "cut_wire",
          "wrong_wire_no", "extra_wire", "tag_target", "flip_ab_input", "contact_label_input", "drop_contact_input"]
FIXABLE = {"flip_ab", "coil_label", "contact_label", "contact_sheet_ref", "tag_target"}
FAULT_LABEL = {
    "flip_ab": "接点の a/b の読み違い",
    "coil_label": "コイルの器具番号の 1 文字の読み違い",
    "contact_label": "接点の器具番号の 1 文字の読み違い",
    "contact_sheet_ref": "接点のシート番号（コイルのあるシート）の読み違い",
    "drop_contact": "接点の見落とし",
    "cut_wire": "配線の途切れ（つなぎ忘れ）",
    "wrong_wire_no": "電線番号の読み違い",
    "extra_wire": "余分な接続（交差を分岐と誤認）",
    "tag_target": "行先札のシート番号の読み違い",
    "flip_ab_input": "入力（押しボタン等）の接点の a/b の読み違い",
    "contact_label_input": "入力の接点の器具番号の 1 文字の読み違い",
    "drop_contact_input": "入力の接点の見落とし",
}


# ------------------------------------------------------------------------------------------ 動作の比較


def _sim_state(circuit: circuit_model.Circuit, inputs: dict[str, bool], energized: dict[str, bool]) -> dict[str, Any]:
    result = circuit_model.simulate(circuit, inputs, energized)
    coils = {canonical_device(d): s["energized"] for d, s in result["coils"].items()}
    lamps = {load["label"]: load["energized"] for load in result["loads"] if load["device"] not in result["coils"]}
    return {"coils": coils, "lamps": lamps, "warnings": result["warnings"]}


def _agree(spec: logic.Spec, sim: dict[str, Any], truth: dict[str, Any]) -> bool:
    coils = {canonical_device(d): v for d, v in truth["coils"].items()}
    return (all(sim["coils"].get(d) == v for d, v in coils.items()) and set(sim["coils"]) == set(coils)
            and all(sim["lamps"].get(k) == v for k, v in truth["lamps"].items()))


def behaviour(spec: logic.Spec, circuit: circuit_model.Circuit, *, trials: int = 20, steps: int = 8,
              seed: int = 0) -> dict[str, int]:
    """ランダムな入力・前の励磁（trials）と、入力を 1 つずつ切り替える操作の手順（steps）で正解と比べる。"""
    rng = random.Random(seed)
    agree = total = 0
    for _ in range(trials):
        inputs = {i: rng.random() < 0.5 for i in spec.inputs}
        energized = {r: rng.random() < 0.5 for r in spec.relays}
        total += 1
        agree += _agree(spec, _sim_state(circuit, inputs, energized), logic.oracle(spec, inputs, energized))
    # 手順: すべて Off から、入力を 1 つずつ切り替え、前の手順の励磁を引き継ぐ。
    inputs = dict.fromkeys(spec.inputs, False)
    truth_state = dict.fromkeys(spec.relays, False)
    sim_state = {canonical_device(r): False for r in circuit.coils}
    for _ in range(steps if spec.inputs else 0):
        name = rng.choice(spec.inputs)
        inputs[name] = not inputs[name]
        truth = logic.oracle(spec, inputs, truth_state)
        sim = _sim_state(circuit, inputs, {d: sim_state.get(canonical_device(d), False) for d in circuit.coils})
        total += 1
        agree += _agree(spec, sim, truth)
        truth_state, sim_state = truth["coils"], sim["coils"]
    return {"agree": agree, "total": total}


def impact_agreement(spec: logic.Spec, circuit: circuit_model.Circuit) -> dict[str, int]:
    """器具ごとの静的な波及範囲（リレーと表示灯）が、正解の依存関係から求めた範囲と一致する数。"""
    agree = total = 0
    for device in spec.relays + spec.inputs:
        if device not in circuit.coils and device not in circuit.inputs:
            continue
        result = circuit_whatif.impact(circuit, device)
        got = {d["device"] for d in result["devices"]} | {x["label"] for x in result["loads"] if x["device"] is None}
        want = logic.oracle_reach(spec, device) - {device}
        total += 1
        agree += got - {device} == want
    return {"agree": agree, "total": total}


# --------------------------------------------------------------------------------------------- 誤り


def _misread(token: str, rng: random.Random) -> str:
    """器具番号の 1 文字を別の文字にする（数字は数字、英字は英字）。"""
    i = rng.choice([k for k, ch in enumerate(token) if ch.isalnum()])
    pool = "0123456789" if token[i].isdigit() else logic.DEVICE_LETTERS
    return token[:i] + rng.choice([c for c in pool if c != token[i]]) + token[i + 1:]


def inject(data: dict[str, Any], spec: logic.Spec, kind: str, rng: random.Random) -> dict[str, Any] | None:
    """data（logic.build の結果のコピー）に誤りを 1 つ入れ、入れた場所と元の値を返す。入れられなければ None。"""
    pages = data["pages"]
    on_input = kind.endswith("_input")
    kind = kind.removesuffix("_input")
    contacts = [(p, s) for p in pages for s in p["symbols"] if s["class_key"] in ("contact_a", "contact_b")
                and (s["label"].split()[0] in spec.inputs) == on_input]
    if not contacts:
        return None
    coils = [(p, s) for p in pages for s in p["symbols"] if s["class_key"] == "relay_coil"]
    wires = [(p, c) for p in pages for c in p["connections"]
             if c["from_symbol_ref"] not in ("P", "N") and c["to_symbol_ref"] not in ("P", "N")]

    def ends(c: dict[str, Any]) -> list[str]:
        return [c["from_symbol_ref"], c["to_symbol_ref"]]

    if kind == "flip_ab":
        page, s = rng.choice(contacts)
        old = s["class_key"]
        s["class_key"] = s["class_label"] = "contact_b" if old == "contact_a" else "contact_a"
        return {"page": page["id"], "refs": [s["ref"]], "fix": ("set_class", s["ref"], old)}
    if kind in ("coil_label", "contact_label"):
        # 器具番号（最初の語）の 1 文字。別の器具の番号と重ならないものにする。
        page, s = rng.choice(coils if kind == "coil_label" else contacts)
        old = s["label"]
        known = {canonical_device(r.split()[0]) for r in spec.relays} | {canonical_device(i) for i in spec.inputs}
        token = old.split()[0]
        for _ in range(20):
            new = _misread(token, rng)
            if canonical_device(new) not in known:
                break
        s["label"] = old.replace(token, new, 1)
        return {"page": page["id"], "refs": [s["ref"]], "fix": ("set_label", s["ref"], old)}
    if kind == "contact_sheet_ref":
        refd = [(p, s) for p, s in contacts if "(" in (s["label"] or "")]
        if not refd or len(spec.sheets) < 2:
            return None
        page, s = rng.choice(refd)
        old = s["label"]
        current = old[old.index("(") + 1:old.index(")")]
        s["label"] = old.replace(current, rng.choice([x for x in spec.sheets if x != current]))
        return {"page": page["id"], "refs": [s["ref"]], "fix": ("set_label", s["ref"], old)}
    if kind == "drop_contact":
        page, s = rng.choice(contacts)
        page["symbols"].remove(s)
        # 場所: 隣の記号（配線の相手）か、図面枠の行列の位置。
        neighbours = [r for c in page["connections"] if s["ref"] in ends(c) for r in ends(c) if r != s["ref"]]
        page["connections"] = [c for c in page["connections"] if s["ref"] not in ends(c)]
        column = round((s["cx"] * logic.WIDTH - logic.GRID["columns"][0]) / logic.GRID["columns"][1])
        row = int((s["cy"] * logic.HEIGHT - logic.GRID["rows"][0][1]) / logic.GRID["rows"][0][2])
        return {"page": page["id"], "refs": [s["ref"], *neighbours], "fix": None,
                "position": f"{column}{logic.ROW_LETTERS[row]}"}
    if kind == "cut_wire":
        page, c = rng.choice(wires)
        page["connections"].remove(c)
        return {"page": page["id"], "refs": ends(c), "fix": None}
    if kind == "wrong_wire_no":
        page, c = rng.choice(wires)
        old = c["wire_no"]
        c["wire_no"] = _misread(old, rng)
        return {"page": page["id"], "refs": ends(c), "fix": None}
    if kind == "extra_wire":
        # 同じ図面の、別のつながりにある 2 つの記号の端子をつなぐ（近くの段どうし）。電線番号は片方の線のもの。
        page = rng.choice(pages)
        circuit = circuit_model.build_circuit([page], {page["id"]: data["bus_texts"][page["id"]]})
        node_of = circuit.node_of
        terms = [(s, t) for s in page["symbols"] if s["class_key"] in ("contact_a", "contact_b", "relay_coil", "lamp")
                 for t in s["terminals"]]
        rng.shuffle(terms)
        for (s1, t1) in terms:
            n1 = node_of.get((page["id"], s1["ref"], t1["ref"]))
            for (s2, t2) in terms:
                n2 = node_of.get((page["id"], s2["ref"], t2["ref"]))
                if s1 is s2 or n1 == n2 or n1 is None or n2 is None or circuit_whatif._is_bus(n1) or circuit_whatif._is_bus(n2):
                    continue
                if abs(s1["cx"] - s2["cx"]) * logic.WIDTH > 250:
                    continue
                number = next((c["wire_no"] for c in page["connections"]
                               if s1["ref"] in ends(c) and t1["ref"] in (c["from_terminal_ref"], c["to_terminal_ref"])), None)
                page["connections"].append({"from_symbol_ref": s1["ref"], "from_terminal_ref": t1["ref"],
                                            "to_symbol_ref": s2["ref"], "to_terminal_ref": t2["ref"], "wire_no": number,
                                            "net_id": None, "kind": "wire", "external_ref": None, "note": None,
                                            "origin": "inference"})
                return {"page": page["id"], "refs": [s1["ref"], s2["ref"]], "fix": None}
        return None
    if kind == "tag_target":
        tags = [(p, s) for p in pages for s in p["symbols"] if s["class_key"] == "other" and s["label"].endswith("へ")]
        if not tags or len(spec.sheets) < 3:
            return None
        page, s = rng.choice(tags)
        old = s["label"]
        current = old[:-1]
        s["label"] = rng.choice([x for x in spec.sheets if x not in (current, page["sheet_no"])]) + "へ"
        return {"page": page["id"], "refs": [s["ref"]], "fix": ("set_label", s["ref"], old)}
    raise ValueError(kind)


def apply_fixes(data: dict[str, Any], issues: list[dict[str, Any]], *, errors_only: bool) -> list[dict[str, Any]]:
    """矛盾チェックの直し方を適用する（circuit_service.apply_consistency_fixes と同じ: 種類とラベルだけ）。"""
    applied = []
    symbols = {(p["id"], s["ref"]): s for p in data["pages"] for s in p["symbols"]}
    for issue in issues:
        fix = issue.get("fix")
        if not fix or (errors_only and issue["severity"] != "error"):
            continue
        s = symbols.get((fix["project_id"], fix["symbol_ref"]))
        if s is None:
            continue
        if fix["type"] == "set_class":
            s["class_key"] = s["class_label"] = fix["value"]
        else:
            s["label"] = fix["value"]
        applied.append(fix)
    return applied


# ------------------------------------------------------------------------------------------- 1 回分


def _check(data: dict[str, Any]) -> dict[str, Any]:
    return consistency.check(copy.deepcopy(data["pages"]), data["structures"], bus_texts=data["bus_texts"], solvers=False)


def _diagnostics(data: dict[str, Any]) -> list[dict[str, Any]]:
    circuit = circuit_model.build_circuit(copy.deepcopy(data["pages"]), data["bus_texts"])
    return [d for d in circuit.diagnostics if d["kind"] != "coil_without_contacts"]


def evaluate_fault(spec: logic.Spec, clean: dict[str, Any], kind: str, seed: int) -> dict[str, Any] | None:
    rng = random.Random(seed)
    data = copy.deepcopy(clean)
    fault = inject(data, spec, kind, rng)
    if fault is None:
        return None
    circuit = circuit_model.build_circuit(copy.deepcopy(data["pages"]), data["bus_texts"])
    before = behaviour(spec, circuit, seed=seed)
    result = _check(data)
    issues = result["issues"]
    clean_diag = {(d["kind"], d.get("message")) for d in _diagnostics(clean)}
    new_diag = [d for d in _diagnostics(data) if (d["kind"], d.get("message")) not in clean_diag]
    at = {(fault["page"], ref) for ref in fault["refs"]}
    located = [i for i in issues if any((loc["project_id"], loc["symbol_ref"]) in at
                                        or (loc["project_id"], loc.get("position")) == (fault["page"], fault.get("position"))
                                        for loc in i["locations"])]
    fixes = [i["fix"] for i in issues if i.get("fix")]
    right_fix = fault["fix"] and any(
        (f["type"], f["symbol_ref"], f["project_id"]) == (fault["fix"][0], fault["fix"][1], fault["page"])
        and _same_value(f["value"], fault["fix"][2]) for f in fixes)
    wrong_fixes = [f for f in fixes if (f["project_id"], f["symbol_ref"]) not in at or not fault["fix"]
                   or not _same_value(f["value"], fault["fix"][2])]
    restored = None
    if fixes:
        fixed = copy.deepcopy(data)
        apply_fixes(fixed, issues, errors_only=False)
        circuit_after = circuit_model.build_circuit(copy.deepcopy(fixed["pages"]), fixed["bus_texts"])
        after = behaviour(spec, circuit_after, seed=seed)
        restored = after["agree"] == after["total"] and not _check(fixed)["issues"]
    return {
        "seed": spec.seed, "fault": kind, "trial": seed, "refs": fault["refs"],
        "behaviour_changed": before["agree"] < before["total"],
        "detected": bool(issues), "detected_by_diagnostics": bool(new_diag), "located": bool(located),
        "checks": sorted({i["check"] for i in issues}), "severities": sorted({i["severity"] for i in issues}),
        "diagnostics": sorted({d["kind"] for d in new_diag}),
        "fix_offered": bool(fixes), "right_fix": bool(right_fix), "wrong_fixes": len(wrong_fixes),
        "restored": restored,
    }


def _same_value(a: str, b: str) -> bool:
    return canonical_device(" ".join(a.split())) == canonical_device(" ".join(b.split()))


def evaluate(seeds: range, *, faults_per_kind: int = 2, trials: int = 20, solvers: int = 0) -> dict[str, Any]:
    """seeds の回路ごとに、誤り無しの検証と、誤りの種類ごとに faults_per_kind 回の注入。

    solvers: 最初の何件の回路で、回路計算どうし（内蔵・ngspice・Lcapy）の突き合わせも流すか（遅い）。
    """
    clean_rows, fault_rows = [], []
    for n, seed in enumerate(seeds):
        spec = logic.make_spec(seed)
        data = logic.build(spec)
        circuit = circuit_model.build_circuit(copy.deepcopy(data["pages"]), data["bus_texts"])
        sim = behaviour(spec, circuit, trials=trials, seed=seed)
        code = circuit_code.to_python(circuit, pages=data["pages"], title=f"synthetic {seed}")
        again = behaviour(spec, circuit_code.from_python(code), trials=trials, seed=seed)
        result = _check(data)
        solver_check = None
        if n < solvers:
            full = consistency.check(copy.deepcopy(data["pages"]), data["structures"], bus_texts=data["bus_texts"])
            solver_check = next(c for c in full["checks"] if c["check"] == "solvers")
            result["issues"] += [i for i in full["issues"] if i["check"] == "solvers"]
        clean_rows.append({
            "solvers": solver_check,
            "seed": seed, "sheets": len(spec.sheets), "relays": len(spec.relays), "inputs": len(spec.inputs),
            "rungs": len(spec.rungs), "tag_pairs": data["tag_pairs"],
            "devices_match": sorted(map(canonical_device, circuit.coils)) == sorted(map(canonical_device, spec.relays))
            and sorted(circuit.inputs) == sorted(spec.inputs),
            "links": len(circuit.sheet_links), "sim": sim, "code_roundtrip": again,
            "impact": impact_agreement(spec, circuit),
            "issues": [{"check": i["check"], "title": i["title"], "severity": i["severity"]} for i in result["issues"]],
            "compared": {c["check"]: c["compared"] for c in result["checks"]},
        })
        for kind in FAULTS:
            for k in range(faults_per_kind):
                row = evaluate_fault(spec, data, kind, seed * 1000 + FAULTS.index(kind) * 10 + k)
                if row:
                    fault_rows.append(row)
    return {"clean": clean_rows, "faults": fault_rows, "summary": summarize(clean_rows, fault_rows)}


def _rate(n: int, d: int) -> str:
    return f"{n}/{d}" + (f"（{100 * n / d:.0f}%）" if d else "")


def summarize(clean: list[dict[str, Any]], faults: list[dict[str, Any]]) -> dict[str, Any]:
    total = Counter()
    for r in clean:
        total["circuits"] += 1
        total["sheets"] += r["sheets"]
        total["relays"] += r["relays"]
        total["devices_match"] += r["devices_match"]
        total["links"] += r["links"]
        total["tag_pairs"] += r["tag_pairs"]
        for key in ("sim", "code_roundtrip", "impact"):
            total[f"{key}_agree"] += r[key]["agree"]
            total[f"{key}_total"] += r[key]["total"]
        if r.get("solvers"):
            total["solver_runs"] += 1
            total["solver_ok"] += r["solvers"]["status"] == "ok"
            total["solver_compared"] += r["solvers"]["compared"]
            total["solver_methods"] = r["solvers"]["method_b"]
        total["false_alarms"] += len(r["issues"])
        total["false_alarm_circuits"] += bool(r["issues"])
    by_kind: dict[str, Counter] = defaultdict(Counter)
    for f in faults:
        c = by_kind[f["fault"]]
        c["n"] += 1
        c["behaviour_changed"] += f["behaviour_changed"]
        c["detected"] += f["detected"]
        c["detected_any"] += f["detected"] or f["detected_by_diagnostics"]
        c["located"] += f["located"]
        c["silent_harm"] += f["behaviour_changed"] and not (f["detected"] or f["detected_by_diagnostics"])
        c["fix_expected"] += f["fault"] in FIXABLE
        c["right_fix"] += f["right_fix"]
        c["wrong_fixes"] += f["wrong_fixes"]
        c["restored"] += bool(f["restored"])
        c["fix_offered"] += f["fix_offered"]
    return {"clean": dict(total), "by_kind": {k: dict(v) for k, v in by_kind.items()}}


def markdown(report: dict[str, Any]) -> str:
    s = report["summary"]
    c = s["clean"]
    lines = [
        "# 合成データによる回路の理解・矛盾チェックの検証（DD-0032）", "",
        f"正解つきの合成回路 {c['circuits']} 件（シート計 {c['sheets']}、リレー計 {c['relays']}、行先札の組 {c['tag_pairs']}）。", "",
        "## 誤りの無い図面", "",
        "| 項目 | 結果 |", "|------|------|",
        f"| 器具（リレー・入力）が正解とそろう | {_rate(c['devices_match'], c['circuits'])} |",
        f"| 行先札を図面間でつないだ組 | {_rate(c['links'], c['tag_pairs'])} |",
        f"| シミュレーションが正解の論理と一致（入力・励磁・操作手順） | {_rate(c['sim_agree'], c['sim_total'])} |",
        f"| 回路のコードに書き出して実行し直した回路も一致 | {_rate(c['code_roundtrip_agree'], c['code_roundtrip_total'])} |",
        f"| 静的な波及範囲が正解の依存関係と一致 | {_rate(c['impact_agree'], c['impact_total'])} |",
        *([(f"| 回路計算どうし（内蔵と {c['solver_methods']}）が一致した回路（比べた状態 {c['solver_compared']}） | "
            f"{_rate(c['solver_ok'], c['solver_runs'])} |")] if c.get("solver_runs") else []),
        f"| 矛盾チェックの誤検出（指摘の数・指摘が出た回路） | {c['false_alarms']} 件・{_rate(c['false_alarm_circuits'], c['circuits'])} |",
        "", "## 誤りを 1 つ入れた図面", "",
        "| 誤りの種類 | 件数 | 動作が変わる | 矛盾チェックで検出 | 回路の診断も含めて検出 | 場所を指す | 動作が変わるのに見逃し | 正しい直し方 | 直して正解に戻る | 誤った直し方 |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for kind in FAULTS:
        v = s["by_kind"].get(kind)
        if not v:
            continue
        n = v["n"]
        fix = _rate(v["right_fix"], n) if v["fix_expected"] else "（対象外）"
        lines.append(f"| {FAULT_LABEL[kind]} | {n} | {_rate(v['behaviour_changed'], n)} | {_rate(v['detected'], n)} | "
                     f"{_rate(v['detected_any'], n)} | {_rate(v['located'], n)} | {v['silent_harm']} | {fix} | "
                     f"{_rate(v['restored'], n)} | {v['wrong_fixes']} |")
    lines += ["", "- 「直して正解に戻る」は、出た直し方をすべて適用した後、動作が正解と一致し、矛盾が 0 になった割合。",
              "- 「誤った直し方」は、誤りを入れた記号以外への直し方か、元の値に戻らない直し方の数。", ""]
    return "\n".join(lines)
