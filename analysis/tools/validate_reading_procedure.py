"""図面を読む手順の機械化（DD-0035）を、正解の論理がわかっている合成回路（DD-0032）で検証する。

1. 成立条件: 図面から求めた負荷ごとの成立条件が、正解の論理式（段の直列の項・並列の接点）と同じ論理か（全通りの真理値）。
2. 3 値の判定: 正解の動作（oracle）が落ち着いた状態で、全部の器具の状態を与えて判定すると、正解と一致するか。
3. 判定不能の安全性: 入力をいくつか「不明」にする、またはシートを 1 枚抜いて参照先欠落にしたとき、
   「成立」「不成立」と断定した負荷が正解と食い違わないか（分からないものは判定不能にとどまるか）。
4. 順引き: 影響先が、正解の依存関係からたどった範囲と一致するか。

Example:
    python analysis/tools/validate_reading_procedure.py --seeds 60 --out docs/evidence/DD-0035-reading-procedure-20261120.md
"""

from __future__ import annotations

import argparse
import copy
import random
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from analysis.synthetic_circuits import logic
from services import circuit_model
from services import circuit_trace as t
from services.circuit_model import build_circuit, canonical_device


def spec_condition(spec: logic.Spec, load: str) -> dict[str, Any]:
    terms = []
    for r in spec.rungs:
        if r.load[1] != load:
            continue
        terms.append(" & ".join("(" + " | ".join(("!" if k == "b" else "") + f'"{d}"' for d, k in term) + ")"
                                for term in r.terms))
    return t.parse(" | ".join(f"({x})" for x in terms))


def reach(spec: logic.Spec, device: str) -> set[str]:
    """oracle_reach と同じだが、同じ器具の a・b 接点が直列の段（どの状態でも導通しない）は影響を伝えない。"""
    def live(r: logic.Rung) -> bool:
        kinds: dict[str, set[str]] = {}
        for term in r.terms:
            if len(term) == 1:
                kinds.setdefault(term[0][0], set()).add(term[0][1])
        return all(len(k) == 1 for k in kinds.values())

    deps: dict[str, set[str]] = {}
    for r in spec.rungs:
        if live(r):
            deps.setdefault(r.load[1], set()).update(d for term in r.terms for d, _ in term if d != r.load[1])
    out: set[str] = set()
    frontier = [device]
    while frontier:
        d = frontier.pop()
        for load, used in deps.items():
            if d in used and load not in out:
                out.add(load)
                if load in spec.relays:
                    frontier.append(load)
    return out


def _truth(spec: logic.Spec, rng: random.Random) -> tuple[dict[str, bool], dict[str, Any]]:
    inputs = {i: rng.random() < 0.5 for i in spec.inputs}
    energized = {r: rng.random() < 0.5 for r in spec.relays}
    return inputs, logic.oracle(spec, inputs, energized)


def _expected(spec: logic.Spec, truth: dict[str, Any], load: str) -> bool:
    return truth["coils"][load] if load in truth["coils"] else truth["lamps"][load]


def check_seed(seed: int, *, trials: int = 10) -> dict[str, Any]:
    spec = logic.make_spec(seed)
    data = logic.build(spec)
    pages = data["pages"]
    circuit = build_circuit(pages, data["bus_texts"])
    tracer = t.Tracer(circuit, pages)
    loads = sorted({r.load[1] for r in spec.rungs})
    out = {"seed": seed, "loads": len(loads), "condition_ok": 0, "decided_ok": 0, "decided": 0,
           "unknown_wrong": 0, "unknown_decided": 0, "unknown_undecided": 0, "missing_wrong": 0,
           "missing_decided": 0, "missing_undecided": 0, "effects_ok": 0, "effects": 0, "failures": []}
    for load in loads:
        mine = t.rename(tracer.condition(load)["tree"], canonical_device)
        truth = t.rename(spec_condition(spec, load), canonical_device)
        if t.equivalent(mine, truth)["equal"]:
            out["condition_ok"] += 1
        else:
            out["failures"].append({"check": "condition", "load": load})
    rng = random.Random(seed)
    for _ in range(trials):
        inputs, truth = _truth(spec, rng)
        if not truth["stable"]:
            continue
        states = {**inputs, **truth["coils"]}
        # 2. 全部の状態を与えた判定
        for load in loads:
            r = tracer.evaluate(load, states)
            out["decided"] += 1
            if (r["status"] == t.ON) == _expected(spec, truth, load) and r["status"] != t.UNKNOWN:
                out["decided_ok"] += 1
            else:
                out["failures"].append({"check": "decided", "load": load, "status": r["status"]})
        # 3a. 入力を一部「不明」に（リレーは導いた値を使う。前の状態は正解の状態）
        hidden = {i: (None if rng.random() < 0.3 else v) for i, v in inputs.items()}
        for load in loads:
            r = tracer.evaluate(load, hidden, truth["coils"])
            if r["status"] == t.UNKNOWN:
                out["unknown_undecided"] += 1
                continue
            out["unknown_decided"] += 1
            if (r["status"] == t.ON) != _expected(spec, truth, load):
                out["unknown_wrong"] += 1
                out["failures"].append({"check": "unknown", "load": load, "status": r["status"]})
    # 3b. シートを 1 枚抜く（参照先欠落）
    if len(spec.sheets) > 1:
        drop = rng.randrange(len(spec.sheets))
        kept = [p for i, p in enumerate(copy.deepcopy(pages)) if i != drop]
        part = t.Tracer(build_circuit(kept, {p["id"]: data["bus_texts"].get(p["id"], []) for p in kept}), kept)
        kept_loads = [n for n, _ in part.all_loads()]
        for _ in range(trials):
            inputs, truth = _truth(spec, rng)
            if not truth["stable"]:
                continue
            for load in kept_loads:
                if canonical_device(load) not in {canonical_device(x) for x in loads}:
                    continue
                name = next(x for x in loads if canonical_device(x) == canonical_device(load))
                r = part.evaluate(load, inputs, truth["coils"])
                if r["status"] == t.UNKNOWN:
                    out["missing_undecided"] += 1
                    continue
                out["missing_decided"] += 1
                if (r["status"] == t.ON) != _expected(spec, truth, name):
                    out["missing_wrong"] += 1
                    out["failures"].append({"check": "missing_sheet", "load": load, "status": r["status"]})
    # 4. 順引き
    for device in list(spec.relays) + list(spec.inputs):
        try:
            eff = tracer.effects(device)
        except ValueError:
            continue
        out["effects"] += 1
        mine_reach = {canonical_device(a["target"]) for a in eff["affected"]}
        truth_reach = {canonical_device(x) for x in reach(spec, device)} - {canonical_device(device)}
        if mine_reach - {canonical_device(device)} == truth_reach:
            out["effects_ok"] += 1
        else:
            out["failures"].append({"check": "effects", "device": device,
                                    "missed": sorted(truth_reach - mine_reach), "extra": sorted(mine_reach - truth_reach)})
    return out


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    keys = [k for k in rows[0] if isinstance(rows[0][k], int) and k != "seed"]
    return {"circuits": len(rows), **{k: sum(r[k] for r in rows) for k in keys}}


def markdown(summary: dict[str, Any], rows: list[dict[str, Any]]) -> str:
    s = summary

    def pct(a: int, b: int) -> str:
        return f"{a}/{b}（{100 * a / b:.1f}%）" if b else "-"

    lines = [
        "# DD-0035 図面を読む手順の機械化: 合成回路での検証", "",
        f"合成回路 {s['circuits']} 個（DD-0032 の正解つき回路、2〜4 シート、自己保持・インターロック・図面をまたぐ段を含む）。", "",
        "| 確かめたこと | 結果 |", "|---|---|",
        f"| 成立条件が正解の論理式と同じ論理 | {pct(s['condition_ok'], s['loads'])} |",
        f"| 全部の状態を与えた 3 値の判定が正解と一致 | {pct(s['decided_ok'], s['decided'])} |",
        f"| 入力の 3 割を不明にしたとき、断定した判定の誤り | {s['unknown_wrong']} 件（断定 {s['unknown_decided']}・判定不能 {s['unknown_undecided']}） |",
        f"| シートを 1 枚抜いたとき、断定した判定の誤り | {s['missing_wrong']} 件（断定 {s['missing_decided']}・判定不能 {s['missing_undecided']}） |",
        f"| 順引きの影響先が正解の依存関係と一致 | {pct(s['effects_ok'], s['effects'])} |",
        "",
        "順引きの正解は、正解の段の依存関係をたどった範囲。ただし同じ器具の a 接点と b 接点が直列の段（どの状態でも",
        "導通しない。合成のインターロックで偶然できる）は影響を伝えないとして数える（機械も伝えない。判定の理由に出す）。",
    ]
    fails = [f for r in rows for f in r["failures"]][:20]
    if fails:
        lines += ["", "## 食い違い（先頭 20 件）", ""] + [f"- {f}" for f in fails]
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--seeds", type=int, default=60)
    parser.add_argument("--trials", type=int, default=10)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    circuit_model.ngspice_path = lambda: None  # 判定は論理だけで、回路計算は使わない
    rows = [check_seed(seed, trials=args.trials) for seed in range(args.seeds)]
    text = markdown(summarize(rows), rows)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text, encoding="utf-8")
    print(text)


if __name__ == "__main__":
    main()
