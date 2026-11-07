"""リレーコイル・電磁弁・表示灯の配線の取り決め（DD-0009 §6）。DB を使わない。

取り決め: コイルには端子を 2 個付け、配線はその端子で止める。コイルの中を通り抜ける配線（両端がコイルの外にあり、
線がコイルの枠を貫く）は使わない。回路のシミュレーションでは、コイルの両側が別のノードでないと励磁を判定できない。

- coil_issues: 取り決めに合わないコイルを返す（エディタの確認カードと評価で使う）。
- split_through_wires: コイルを通り抜ける配線を、コイルの枠に入る点と出る点で 2 本に分け、そこに端子を付ける。
  以前の取り決めで登録した図面（正解データ 30 枚など）を新しい取り決めに移すときに使う。
"""

from __future__ import annotations

from typing import Any

COIL_CLASSES = frozenset({"relay_coil", "solenoid", "lamp"})
# 線がコイルの中心からこの割合（枠の短い辺に対して）より離れて通るときは、かすめているだけとみなす。
CENTER_RATIO = 0.3
INSET_PX = 1.0


def _box(s: dict[str, Any], width: int, height: int) -> tuple[float, float, float, float]:
    cx, cy, w, h = s["cx"] * width, s["cy"] * height, s["w"] * width, s["h"] * height
    return cx - w / 2 + INSET_PX, cy - h / 2 + INSET_PX, cx + w / 2 - INSET_PX, cy + h / 2 - INSET_PX


def _clip(p: tuple[float, float], q: tuple[float, float], box: tuple[float, float, float, float]) -> tuple[float, float] | None:
    """線分 p→q が枠の中にある範囲（t0, t1）。枠に入らなければ None（Liang-Barsky）。"""
    t0, t1 = 0.0, 1.0
    dx, dy = q[0] - p[0], q[1] - p[1]
    for d, lo, hi, v in ((dx, box[0], box[2], p[0]), (dy, box[1], box[3], p[1])):
        if d == 0:
            if not lo < v < hi:
                return None
            continue
        a, b = (lo - v) / d, (hi - v) / d
        t0, t1 = max(t0, min(a, b)), min(t1, max(a, b))
        if t0 >= t1:
            return None
    return t0, t1


def _positions(symbols: list[dict[str, Any]], width: int, height: int) -> dict[tuple[str, str | None], tuple[float, float]]:
    pos: dict[tuple[str, str | None], tuple[float, float]] = {}
    for s in symbols:
        pos[(s["ref"], None)] = (s["cx"] * width, s["cy"] * height)
        for t in s.get("terminals") or []:
            pos[(s["ref"], t["ref"])] = (t["tx"] * width, t["ty"] * height)
    return pos


def through_wires(symbols: list[dict[str, Any]], connections: list[dict[str, Any]], width: int, height: int
                  ) -> dict[str, list[tuple[int, float, float]]]:
    """コイルの ref → そのコイルを通り抜ける配線（connections の添字, 入る t, 出る t）。"""
    pos = _positions(symbols, width, height)
    coils = [s for s in symbols if s["class_key"] in COIL_CLASSES]
    out: dict[str, list[tuple[int, float, float]]] = {}
    for i, c in enumerate(connections):
        if c.get("kind", "wire") != "wire" or c["from_symbol_ref"] == c["to_symbol_ref"]:
            continue
        p = pos.get((c["from_symbol_ref"], c.get("from_terminal_ref")))
        q = pos.get((c["to_symbol_ref"], c.get("to_terminal_ref")))
        if p is None or q is None or p == q:
            continue
        for s in coils:
            if s["ref"] in (c["from_symbol_ref"], c["to_symbol_ref"]):
                continue
            box = _box(s, width, height)
            span = _clip(p, q, box)
            if span is None or span[0] <= 0 or span[1] >= 1:
                continue
            # 線と中心の距離
            cx, cy = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2
            length = ((q[0] - p[0]) ** 2 + (q[1] - p[1]) ** 2) ** 0.5
            dist = abs((q[0] - p[0]) * (p[1] - cy) - (p[0] - cx) * (q[1] - p[1])) / length
            if dist > CENTER_RATIO * min(box[2] - box[0], box[3] - box[1]):
                continue
            out.setdefault(s["ref"], []).append((i, span[0], span[1]))
    return out


def coil_issues(symbols: list[dict[str, Any]], connections: list[dict[str, Any]], width: int, height: int) -> list[dict[str, Any]]:
    """取り決めに合わないコイル。kind は terminals（端子が 2 個でない）、unwired（配線の無い端子）、through（通り抜ける配線）。"""
    wired: set[tuple[str, str | None]] = set()
    for c in connections:
        if c.get("kind", "wire") == "wire" and c["from_symbol_ref"] != c["to_symbol_ref"]:
            wired.add((c["from_symbol_ref"], c.get("from_terminal_ref")))
            wired.add((c["to_symbol_ref"], c.get("to_terminal_ref")))
    through = through_wires(symbols, connections, width, height)
    issues = []
    for s in symbols:
        if s["class_key"] not in COIL_CLASSES:
            continue
        terms = s.get("terminals") or []
        base = {"symbol_ref": s["ref"], "class_key": s["class_key"], "label": s.get("label") or ""}
        if s["ref"] in through:
            issues.append({**base, "kind": "through", "message": "配線がコイルの中を通り抜けています。コイルの上下（左右）に端子を付け、配線を分けてください"})
        elif len(terms) != 2:
            issues.append({**base, "kind": "terminals", "message": f"端子が {len(terms)} 個です（2 個必要）"})
        elif unwired := [t["ref"] for t in terms if (s["ref"], t["ref"]) not in wired]:
            issues.append({**base, "kind": "unwired", "terminal_refs": unwired, "message": "配線の無い端子があります"})
    return issues


def split_through_wires(symbols: list[dict[str, Any]], connections: list[dict[str, Any]], width: int, height: int
                        ) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, int]]:
    """端子の無いコイルを 1 本だけ通り抜ける配線を、コイルの枠の出入りの点で分けて端子を付ける。

    戻り値は（記号, 配線, 件数）。件数は split（分けたコイル）、multiple（通り抜ける配線が 2 本以上で分けなかった）、
    none（通り抜ける配線も端子も無い）、has_terminals（もとから端子がある）。
    """
    through = through_wires(symbols, connections, width, height)
    pos = _positions(symbols, width, height)
    counts = {"split": 0, "multiple": 0, "none": 0, "has_terminals": 0}
    new_symbols = [dict(s) for s in symbols]
    drop: set[int] = set()
    added: list[dict[str, Any]] = []
    for s in new_symbols:
        if s["class_key"] not in COIL_CLASSES:
            continue
        if s.get("terminals"):
            counts["has_terminals"] += 1
            continue
        wires = [w for w in through.get(s["ref"], []) if w[0] not in drop]
        if len(wires) != 1:
            counts["multiple" if wires else "none"] += 1
            continue
        i, t0, t1 = wires[0]
        c = connections[i]
        p = pos[(c["from_symbol_ref"], c.get("from_terminal_ref"))]
        q = pos[(c["to_symbol_ref"], c.get("to_terminal_ref"))]
        ends = []
        for n, t in enumerate((t0, t1), start=1):
            x, y = p[0] + (q[0] - p[0]) * t, p[1] + (q[1] - p[1]) * t
            ends.append({"ref": f"{s['ref']}-T{n}", "name": str(n), "tx": x / width, "ty": y / height,
                         "origin": c.get("origin", "manual")})
        s["terminals"] = ends
        common = {k: v for k, v in c.items() if k not in ("from_symbol_ref", "from_terminal_ref", "to_symbol_ref", "to_terminal_ref")}
        added.append({**common, "from_symbol_ref": c["from_symbol_ref"], "from_terminal_ref": c.get("from_terminal_ref"),
                      "to_symbol_ref": s["ref"], "to_terminal_ref": ends[0]["ref"]})
        added.append({**common, "from_symbol_ref": s["ref"], "from_terminal_ref": ends[1]["ref"],
                      "to_symbol_ref": c["to_symbol_ref"], "to_terminal_ref": c.get("to_terminal_ref")})
        drop.add(i)
        counts["split"] += 1
    new_connections = [c for i, c in enumerate(connections) if i not in drop] + added
    return new_symbols, new_connections, counts

