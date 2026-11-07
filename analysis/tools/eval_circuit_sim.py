"""回路シミュレーションの結果を、人の登録（正解）と AI の取り込みで比べる（DD-0009 §7）。

正解はコイルの配線の取り決め（端子 2 個）に移した zip（coil_wiring_gold.py）を使う。図面ごとに、正解のアノテーションと、
構造解析を空のエディタに取り込んだ結果からそれぞれ回路を組み、同じ外部入力の On/Off で解いて、正解のコイルごとに
励磁が一致するかを数える。外部入力は正解の回路の入力を使い、すべて Off・1 つだけ On・すべて On・乱数の組み合わせで解く。

- as_is: AI の取り込みそのまま（器具番号も AI が読んだもの）。
- gold_labels: AI の記号に、対応する正解の記号のラベルを写したもの。配線だけの影響を見る。
- together: 30 枚をまとめて 1 つの回路にする（母線と器具番号で図面をまたいでつながる）。

Example:
    python analysis/tools/eval_circuit_sim.py --gold a_coil.zip b_coil.zip --structures runs/wiring/pred --out sim.json
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from eval_editor_import import apply_plan, imported_items  # noqa: E402
from holdout_cv import gold_items, load_gold, match  # noqa: E402

from services.ai_candidates import from_structure, plan_import  # noqa: E402
from services.circuit_model import build_circuit, simulate  # noqa: E402

RANDOM_SCENARIOS = 10


def ai_page(drawing: dict[str, Any], structure: dict[str, Any], class_keys: list[str], gold_labels: bool) -> dict[str, Any]:
    project = drawing["project"]
    width, height = int(project["image_width"]), int(project["image_height"])
    plan = plan_import(from_structure(structure, class_keys), [], [], width, height)
    symbols, connections = apply_plan([], [], plan)
    if gold_labels:
        g_sym = [g for g in gold_items(drawing) if g["kind"] == "symbol"]
        p_sym = [p for p in imported_items(symbols, width, height) if p["kind"] == "symbol"]
        label = {s["ref"]: s.get("label") for s in project["symbols"]}
        to_gold = {p_sym[pi]["ref"]: g_sym[gi]["ref"] for gi, pi, _ in match(g_sym, p_sym, class_aware=False)}
        symbols = [{**s, "label": label.get(to_gold.get(s["ref"]), s.get("label"))} for s in symbols]
    return {**_page_meta(project), "symbols": symbols, "connections": connections}


def _page_meta(project: dict[str, Any]) -> dict[str, Any]:
    return {"id": project["id"], "name": project["name"], "sheet_no": project.get("sheet_no"),
            "image_width": int(project["image_width"]), "image_height": int(project["image_height"])}


def scenarios(inputs: list[str], seed: int) -> list[dict[str, bool]]:
    rng = random.Random(seed)
    out = [{}] + [{dev: True} for dev in inputs]
    if inputs:
        out.append({dev: True for dev in inputs})
        out += [{dev: rng.random() < 0.5 for dev in inputs} for _ in range(RANDOM_SCENARIOS)]
    return out


def compare(gold_pages: list[dict], ai_pages: list[dict], texts: dict[int, list], seed: int) -> dict[str, Any]:
    gold, ai = build_circuit(gold_pages, texts), build_circuit(ai_pages, texts)
    dropped = Counter(next(s["class_key"] for p in ai_pages for s in p["symbols"] if p["id"] == d["project_id"] and s["ref"] == d["symbol_ref"])
                      for d in ai.diagnostics if d["kind"] == "terminals_missing")
    counts: Counter = Counter()
    disagreements = []
    for scenario in scenarios(gold.inputs, seed):
        g = simulate(gold, scenario)["coils"]
        a = simulate(ai, scenario)["coils"]
        counts["scenarios"] += 1
        all_agree = True
        for dev, state in g.items():
            counts["coil_states"] += 1
            counts["gold_energized"] += state["energized"]
            if dev not in a:
                counts["coil_missing"] += 1
                counts["missing_gold_on"] += state["energized"]
                all_agree = False
                continue
            same = a[dev]["energized"] == state["energized"]
            counts["agree"] += same
            counts["both_on"] += state["energized"] and a[dev]["energized"]
            counts["ai_on"] += a[dev]["energized"]
            counts["gold_on_present"] += state["energized"]
            if not same:
                all_agree = False
                if len(disagreements) < 20:
                    disagreements.append({"inputs_on": sorted(k for k, v in scenario.items() if v)[:8], "coil": dev,
                                          "gold": state["energized"], "ai": a[dev]["energized"]})
        counts["scenarios_all_agree"] += all_agree
    return {
        "gold": {"coils": len(gold.coils), "inputs": len(gold.inputs), "elements": len(gold.elements),
                 "diagnostics": dict(Counter(d["kind"] for d in gold.diagnostics))},
        "ai": {"coils": len(ai.coils), "inputs": len(ai.inputs), "elements": len(ai.elements),
               "diagnostics": dict(Counter(d["kind"] for d in ai.diagnostics)), "terminals_missing_by_class": dict(dropped)},
        "counts": dict(counts), "disagreements": disagreements,
    }


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    c: Counter = Counter()
    for r in rows:
        c.update(r["counts"])
    def rate(n: int, d: int) -> float | None:
        return round(n / d, 4) if d else None

    return {"drawings": len(rows), "coil_states": c["coil_states"], "gold_energized": c["gold_energized"],
            "agree": rate(c["agree"], c["coil_states"]), "coil_missing": c["coil_missing"],
            "on_recall": rate(c["both_on"], c["gold_energized"]), "on_precision": rate(c["both_on"], c["ai_on"]),
            "on_missed_coil_absent": c["missing_gold_on"], "on_missed_coil_off": c["gold_energized"] - c["both_on"] - c["missing_gold_on"],
            "false_on": c["ai_on"] - c["both_on"],
            "scenarios": c["scenarios"], "scenarios_all_agree": rate(c["scenarios_all_agree"], c["scenarios"])}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--gold", type=Path, nargs="+", required=True)
    parser.add_argument("--structures", type=Path, required=True)
    parser.add_argument("--out", type=Path)
    parser.add_argument("--seed", type=int, default=0)
    args = parser.parse_args()
    class_keys, drawings = load_gold(args.gold)
    # 正解の zip ごとに図面 ID が 1 から振られているので、まとめて回路にする前に重ならない ID に振り直す。
    drawings = [{**d, "project": {**d["project"], "id": i}} for i, d in enumerate(drawings, start=1)]
    tune = {d["sheet"] for d in drawings[0::2]}
    structures = {d["sheet"]: json.loads((args.structures / f"{d['sheet']}.json").read_text(encoding="utf-8")) for d in drawings}
    texts = {d["project"]["id"]: structures[d["sheet"]].get("texts") or [] for d in drawings}
    gold_pages = {d["sheet"]: {**_page_meta(d["project"]), "symbols": d["project"]["symbols"],
                               "connections": d["project"]["connections"]} for d in drawings}
    result: dict[str, Any] = {}
    for mode in ("as_is", "gold_labels"):
        ai_pages = {d["sheet"]: ai_page(d, structures[d["sheet"]], class_keys, mode == "gold_labels") for d in drawings}
        rows = [{"sheet": s, **compare([gold_pages[s]], [ai_pages[s]], texts, args.seed)} for s in sorted(gold_pages)]
        together = compare(list(gold_pages.values()), list(ai_pages.values()), texts, args.seed)
        result[mode] = {"tune": summarize([r for r in rows if r["sheet"] in tune]),
                        "report": summarize([r for r in rows if r["sheet"] not in tune]),
                        "all": summarize(rows), "together": {**summarize([together]), "gold": together["gold"], "ai": together["ai"],
                                                             "disagreements": together["disagreements"]},
                        "rows": rows}
        for part in ("tune", "report", "all", "together"):
            print(mode, part, json.dumps({k: v for k, v in result[mode][part].items() if k != "disagreements"}, ensure_ascii=False))
    if args.out:
        args.out.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()

