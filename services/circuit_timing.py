"""時間の流れのシミュレーション: タイマー（オンディレー・オフディレー）と、時刻つきの操作（DD-0033）。

タイマーはコイルの属性 ``timer_mode``（on_delay / off_delay、オンディレー・オフディレー・限時動作・限時復帰）と
``delay_s``（秒）で表す（circuit_model.timer_setting）。限時接点は次の出力に従い、瞬時接点（属性 timer_contact = instant）は
コイルの励磁に従う。

- オンディレー: コイルが励磁し続けて delay_s たつと出力が ON。コイルが切れたらすぐ OFF。
- オフディレー: コイルが励磁したらすぐ ON。コイルが切れて delay_s たつと OFF（その間に励磁し直したら OFF にならない）。

解き方は出来事（操作の時刻・タイマーの満了）の順に進め、出来事ごとに回路を解き直す（circuit_model.simulate）。
タイマーの出力が変わったら、同じ時刻でもう一度解く（瞬時の連鎖）。同じ時刻の操作とタイマーの満了は、操作を先に解き、
それでもコイルの状態が続いているタイマーだけを満了させる（競合したら記録に警告を出す）。
"""

from __future__ import annotations

from typing import Any

from services import circuit_whatif
from services.circuit_model import Circuit

MAX_EVENTS = 500
MAX_CHAIN = 50
EPS = 1e-9


def _settle(circuit: Circuit, t: float, inputs: dict[str, bool], coils: dict[str, bool], outputs: dict[str, bool],
            pending: dict[str, float], timers: dict[str, dict[str, Any]], options: dict[str, Any]) -> dict[str, Any]:
    """時刻 t で、タイマーの出力の瞬時の変化が止まるまで解く。pending（満了の時刻）を更新する。"""
    result: dict[str, Any] = {}
    for _ in range(MAX_CHAIN):
        result = circuit_whatif._run(circuit, {**inputs, **options["inputs"]}, coils, options, timer_outputs=outputs)
        coils.update({dev: s["energized"] for dev, s in result["coils"].items()})
        changed = False
        for dev, cfg in timers.items():
            on = coils.get(dev, False)
            if cfg["mode"] == "on_delay":
                if not on:
                    pending.pop(dev, None)
                    if outputs[dev]:
                        outputs[dev], changed = False, True
                elif not outputs[dev]:
                    if cfg["delay_s"] <= EPS:
                        outputs[dev], changed = True, True
                    else:
                        pending.setdefault(dev, t + cfg["delay_s"])
            else:  # off_delay
                if on:
                    pending.pop(dev, None)
                    if not outputs[dev]:
                        outputs[dev], changed = True, True
                elif outputs[dev]:
                    if cfg["delay_s"] <= EPS:
                        outputs[dev], changed = False, True
                    else:
                        pending.setdefault(dev, t + cfg["delay_s"])
        if not changed:
            break
    return result


def _snapshot(t: float, inputs: dict[str, bool], result: dict[str, Any], outputs: dict[str, bool],
              pending: dict[str, float], label: str) -> dict[str, Any]:
    return {
        "t": round(t, 6), "label": label, "inputs": dict(inputs),
        "coils": {dev: s["energized"] for dev, s in result["coils"].items()},
        "timers": {dev: {"output": outputs[dev], "due": round(pending[dev], 6) if dev in pending else None} for dev in outputs},
        "loads": [{k: x[k] for k in ("project_id", "symbol_ref", "label", "device", "energized")} for x in result["loads"]],
        "warnings": result["warnings"], "_result": result,
    }


def run_timeline(circuit: Circuit, events: list[dict[str, Any]], *, inputs: dict[str, bool] | None = None,
                 energized: dict[str, bool] | None = None, until: float | None = None,
                 change: dict[str, Any] | None = None, delays: dict[str, float] | None = None) -> dict[str, Any]:
    """時刻つきの操作 events（{"at": 秒, "inputs": {器具番号: bool}, "label": 任意}）を順に解く。

    until: ここまで進める（省略時は最後の操作のあと、タイマーが落ち着くまで）。
    change: もしもの変更（circuit_whatif.apply_change と同じ）。delays: タイマーの時間の変更（器具番号 → 秒）。
    戻り値の timeline は、状態が変わった時刻ごとの記録（changes は前の記録からの差）。
    """
    changed_circuit, options = circuit_whatif.apply_change(circuit, change)
    timers = {dev: dict(cfg) for dev, cfg in circuit.timers.items()}
    for dev, delay in (delays or {}).items():
        if dev not in timers:
            raise ValueError(f"タイマー {dev} が回路にありません")
        timers[dev]["delay_s"] = max(0.0, float(delay))
    current = dict(inputs or {})
    coils = {dev: bool((energized or {}).get(dev)) for dev in circuit.coils}
    outputs = {dev: coils[dev] for dev in timers}  # 前の励磁が続いていた（落ち着いた状態）とみなす
    pending: dict[str, float] = {}
    queue = sorted(({**e, "at": float(e.get("at") or 0)} for e in events), key=lambda e: e["at"])

    result = _settle(changed_circuit, 0.0, current, coils, outputs, pending, timers, options)
    timeline = [_snapshot(0.0, current, result, outputs, pending, "初期状態")]
    t = 0.0
    for _ in range(MAX_EVENTS):
        next_event = queue[0]["at"] if queue else None
        next_due = min(pending.values()) if pending else None
        candidates = [x for x in (next_event, next_due) if x is not None]
        if not candidates:
            break
        t_next = max(t, min(candidates))
        if until is not None and t_next > until + EPS:
            break
        t = t_next
        labels: list[str] = []
        races: list[str] = []
        due_now = {dev for dev, due in pending.items() if due <= t + EPS}
        # 同じ時刻なら操作を先に入れて解く。満了するタイマーのコイルが操作で切れた（戻った）ら、タイマーは動かない。
        if queue and queue[0]["at"] <= t + EPS:
            while queue and queue[0]["at"] <= t + EPS:
                event = queue.pop(0)
                current.update({str(k): bool(v) for k, v in (event.get("inputs") or {}).items()})
                labels.append(event.get("label") or "、".join(f"{k}={'ON' if v else 'OFF'}"
                                                             for k, v in (event.get("inputs") or {}).items()))
            result = _settle(changed_circuit, t, current, coils, outputs, pending, timers, options)
            races = [dev for dev in sorted(due_now) if dev not in pending]
        fired = [dev for dev in sorted(due_now) if dev in pending and pending[dev] <= t + EPS]
        for dev in fired:
            outputs[dev] = timers[dev]["mode"] == "on_delay"
            del pending[dev]
        labels = [f"{dev} の時間が経過" for dev in fired] + labels
        if fired or not labels:
            result = _settle(changed_circuit, t, current, coils, outputs, pending, timers, options)
        snap = _snapshot(t, current, result, outputs, pending, " / ".join(x for x in labels if x))
        snap["changes"] = circuit_whatif.diff_results(timeline[-1]["_result"], result)
        snap["warnings"] = snap["warnings"] + [
            f"{dev} の時間の経過と同じ時刻に操作でコイルが{'切れ' if timers[dev]['mode'] == 'on_delay' else '励磁し'}、"
            "タイマーは動きませんでした（ぎりぎりの競合。実機では順序が決まらない）" for dev in races]
        timeline.append(snap)
    if until is not None and (not timeline or timeline[-1]["t"] < until):
        timeline.append({**timeline[-1], "t": round(until, 6), "label": "終了", "changes": {}})
    for snap in timeline:
        snap.pop("_result", None)
        snap.setdefault("changes", {})
    return {"timers": timers, "timeline": timeline, "unknown": options["unknown"]}


def state_at(timeline: list[dict[str, Any]], t: float) -> dict[str, Any]:
    """時刻 t の状態（t 以前の最後の記録）。"""
    last = timeline[0]
    for snap in timeline:
        if snap["t"] <= t + EPS:
            last = snap
    return last
