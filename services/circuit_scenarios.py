"""代表回路の正解（シナリオ）と、機械の判定との照合（DD-0035）。DB を使わない。

東電設計様に記入いただく「代表回路の正解」を 1 件ずつのシナリオにする。

    drawing_set: DS01
    scenarios:
      - id: S1
        question: 起動条件          # 起動条件（逆引き）/ 影響（順引き）。backward / forward も可
        target: "33HB FV2306"       # 逆引きの対象（コイルの器具番号・負荷のラベル）
        condition: 'A & (B | C) & D' # 成立条件。! は b 接点（動作で開）。空白を含む器具番号は "..." で囲む
        pages: [ER21301, ER21302]   # 追う順番とページ（任意）
        cases:                      # 成立する例・しない例・判定不能の例
          - states: {A: true, B: false, C: true, D: true}
            expected: 成立
      - id: S2
        question: 影響
        origin: "STOP01"            # 順引きの起点
        affected: ["33HB FV2306", "RL01"]

照合: 対象が見つかるか、成立条件が同じ論理か（全通りの真理値で比べる）、例ごとの判定が一致するか、
順引きの影響先の適合率・再現率、根拠のページが正解のページを含むか。
"""

from __future__ import annotations

from typing import Any

from services import circuit_trace as t
from services.circuit_model import Circuit, canonical_device

BACKWARD = {"起動条件", "逆引き", "成立条件", "backward", "condition"}
FORWARD = {"影響", "順引き", "影響確認", "forward", "effects"}
STATUS_ALIASES = {"成立": t.ON, "on": t.ON, "true": t.ON, "不成立": t.OFF, "off": t.OFF, "false": t.OFF,
                  "判定不能": t.UNKNOWN, "不明": t.UNKNOWN, "unknown": t.UNKNOWN}


def normalize_status(value: Any) -> str | None:
    return STATUS_ALIASES.get(str(value).strip().lower()) if value is not None else None


def _bool_or_none(v: Any) -> bool | None:
    if v is None or (isinstance(v, str) and v.strip().lower() in ("", "?", "不明", "unknown", "none", "null")):
        return None
    if isinstance(v, str):
        return v.strip().lower() in ("1", "true", "on", "yes", "動作", "閉", "導通")
    return bool(v)


class NameMap:
    """正解に書かれた名前（「33HB」「33HB FV2306」「RL01」）→ 回路の器具番号・負荷の名前。"""

    def __init__(self, tracer: t.Tracer) -> None:
        names = set(tracer.sources) | set(tracer.circuit.coils) | {n for n, _ in tracer.all_loads()}
        self.exact = {canonical_device(n): n for n in names}
        first: dict[str, list[str]] = {}
        for n in names:
            if n.split():
                first.setdefault(canonical_device(n.split()[0]), []).append(n)
        self.first = {k: v[0] for k, v in first.items() if len(v) == 1}
        self.missing: set[str] = set()

    def __call__(self, name: str) -> str:
        c = canonical_device(name.strip())
        if c in self.exact:
            return self.exact[c]
        if c in self.first:
            return self.first[c]
        self.missing.add(name)
        return name


def _pages_of(result: dict[str, Any]) -> list[str]:
    pages = [x["page"] for x in result.get("loads", [])]
    for b in result.get("branches", []):
        pages += [c["page"] for c in b["contacts"]]
    return list(dict.fromkeys(pages))


def _prf(gold: set[str], pred: set[str]) -> dict[str, Any]:
    tp = len(gold & pred)
    return {"tp": tp, "fp": len(pred - gold), "fn": len(gold - pred),
            "precision": round(tp / len(pred), 4) if pred else None,
            "recall": round(tp / len(gold), 4) if gold else None,
            "missed": sorted(gold - pred), "extra": sorted(pred - gold)}


def evaluate_scenario(tracer: t.Tracer, scenario: dict[str, Any]) -> dict[str, Any]:
    names = NameMap(tracer)
    question = str(scenario.get("question") or ("影響" if scenario.get("origin") else "起動条件")).strip()
    out: dict[str, Any] = {"id": scenario.get("id"), "question": question, "ok": True, "checks": []}

    def check(name: str, ok: bool, **detail: Any) -> None:
        out["checks"].append({"check": name, "ok": ok, **detail})
        out["ok"] &= ok

    if question in FORWARD:
        origin = names(str(scenario.get("origin") or ""))
        try:
            eff = tracer.effects(origin)
        except ValueError as exc:
            check("origin_found", False, message=str(exc))
            return out
        pred = {canonical_device(a["target"]) for a in eff["affected"]}
        gold = {canonical_device(names(x)) for x in scenario.get("affected") or []}
        score = _prf(gold, pred)
        check("affected", not score["missed"] and not score["extra"], **score)
        out["machine"] = {"origin": origin, "affected": [a["target"] for a in eff["affected"]]}
    else:
        target = names(str(scenario.get("target") or ""))
        try:
            cond = tracer.condition(target)
        except ValueError as exc:
            check("target_found", False, message=str(exc))
            return out
        out["machine"] = {"target": cond["target"], "formula": cond["formula"], "text": cond["text"],
                          "branch_count": len(cond["branches"])}
        if scenario.get("condition"):
            gold_tree = t.rename(t.parse(str(scenario["condition"])), lambda n: canonical_device(names(n)))
            mine = t.rename(cond["tree"], canonical_device)
            eq = t.equivalent(mine, gold_tree)
            check("condition", eq["equal"], expected=str(scenario["condition"]), actual=cond["text"],
                  counterexample=eq["counterexample"], exhaustive=eq["exhaustive"])
        pages: list[str] = []
        for i, case in enumerate(scenario.get("cases") or [], start=1):
            states = {names(k): _bool_or_none(v) for k, v in (case.get("states") or {}).items()}
            energized = {names(k): bool(v) for k, v in (case.get("energized") or {}).items()}
            r = tracer.evaluate(target, states, energized)
            pages += _pages_of(r)
            expected = normalize_status(case.get("expected"))
            check(f"case_{i}", r["status"] == expected, expected=expected, actual=r["status"], reasons=r["reasons"],
                  note=case.get("note"))
        if scenario.get("pages"):
            if not pages:
                pages = _pages_of(tracer.evaluate(target, {}))
            gold_pages = {str(p) for p in scenario["pages"]}
            found = set(pages)
            check("pages", gold_pages <= found, expected=sorted(gold_pages), actual=sorted(found),
                  missed=sorted(gold_pages - found))
    if names.missing:
        out["unknown_names"] = sorted(names.missing)
    return out


def evaluate(circuit: Circuit, pages: list[dict[str, Any]] | None, scenarios: list[dict[str, Any]], *,
             external: list[str] | tuple[str, ...] = ()) -> dict[str, Any]:
    tracer = t.Tracer(circuit, pages, external=external)
    rows = [evaluate_scenario(tracer, s) for s in scenarios]
    checks = [c for r in rows for c in r["checks"]]
    by_kind: dict[str, dict[str, int]] = {}
    for c in checks:
        kind = "case" if c["check"].startswith("case_") else c["check"]
        k = by_kind.setdefault(kind, {"ok": 0, "total": 0})
        k["total"] += 1
        k["ok"] += int(c["ok"])
    return {"scenarios": rows, "passed": sum(r["ok"] for r in rows), "total": len(rows), "by_check": by_kind}


def load(text: str) -> tuple[str | None, list[dict[str, Any]]]:
    """YAML / JSON のシナリオ。{drawing_set, scenarios: [...]} かシナリオの配列。"""
    import yaml

    data = yaml.safe_load(text)
    if isinstance(data, list):
        return None, data
    if not isinstance(data, dict) or not isinstance(data.get("scenarios"), list):
        raise ValueError("scenarios の配列がありません")  # noqa: TRY004  API で 400 にする
    return data.get("drawing_set"), data["scenarios"]
