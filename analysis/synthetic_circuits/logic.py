"""正解の論理がわかっている複数シートのシーケンス回路を合成する（DD-0032）。

DD-0027 の合成は画像と配線の検出の検証用で、回路は 1 枚ごとの単純な段だった。ここでは回路の理解（DD-0029〜0031）を
検証するため、次を持つ回路を論理（正本）から作る。

- 複数のシート（ER21301…）。シートごとに P・N の母線、PER/NER の文字。
- リレーの段: 停止の b 接点、起動（押しボタンか前のリレーの接点）と自己保持の a 接点の並列、インターロック。
- 表示灯の段、図面をまたぐ段（行先札「ER21302へ」と来先札「(ER21301)」、両側で同じ電線番号）。
- コイルが別のシートにある接点のラベルにはコイルのシート（「33HA FV2301 (ER21302)」）。
- 電線番号（つながりごとに 1 つ）、リレー表（コイルのシートに、接点ごとの行: 器具番号・接点・シート・行列）、図面枠の行列。

出力は serialize_project と同じ形の pages、構造解析と同じ形の structures（relay_table・drawing_grid）、PER/NER の文字、
そして正解（spec）。正解の動作は oracle で、回路の電気の計算と独立に、論理式として求める。
"""

from __future__ import annotations

import copy
import random
from dataclasses import dataclass, field
from typing import Any

WIDTH, HEIGHT = 1755, 1241
GRID = {"columns": [60.0, 70.0], "rows": [[20.0, 60.0, 45.0]]}  # 列 n の x = 60 + 70 n、行 k の y = 60 + 45 k
ROW_LETTERS = "ABCDEFGHJKLMNPQRSTUVWXYZ"
DEVICE_LETTERS = "ACDEFGHJKLMNPRSTUVWXYZ"  # OCR で取り違えやすい B・I・O・Q は使わない
TOP_Y, BOTTOM_Y = 100.0, 1100.0
LOAD_ROW = 18
TAG_ROW = 15


@dataclass
class Rung:
    sheet: int                         # 接点を描くシート（0 始まり）
    load: tuple[str, str]              # ("coil", 器具番号) / ("lamp", ラベル)
    terms: list[list[tuple[str, str]]]  # 直列の項。項は並列の接点 (器具番号, "a"/"b")
    load_sheet: int | None = None      # 負荷が別のシートなら、そのシート（行先札でつなぐ）


@dataclass
class Spec:
    seed: int
    sheets: list[str]
    relays: list[str]                  # 器具番号（コイルのラベル）
    inputs: list[str]
    rungs: list[Rung]
    coil_sheet: dict[str, int] = field(default_factory=dict)

    def depends(self) -> dict[str, set[str]]:
        """負荷 → その段に接点がある器具。"""
        out: dict[str, set[str]] = {}
        for r in self.rungs:
            out.setdefault(r.load[1], set()).update(d for term in r.terms for d, _ in term if d != r.load[1])
        return out


def _relay_name(k: int) -> str:
    return f"{30 + k}H{DEVICE_LETTERS[k % len(DEVICE_LETTERS)]} FV{2301 + k}"


def make_spec(seed: int, *, sheets: int | None = None) -> Spec:
    """回路の正本。リレーは番号順にしか前のリレーの接点を使わない（自己保持を除き、循環しない）。"""
    rng = random.Random(seed)
    n_sheets = sheets or rng.randint(2, 4)
    sheet_names = [f"ER{21301 + 10 * (seed % 50) + i}" for i in range(n_sheets)]
    n_relays = rng.randint(2 * n_sheets, 3 * n_sheets)
    relays = [_relay_name(k) for k in range(n_relays)]
    inputs: list[str] = []
    rungs: list[Rung] = []
    coil_sheet: dict[str, int] = {}
    per_sheet = [0] * n_sheets
    for k, relay in enumerate(relays):
        sheet = min(range(n_sheets), key=lambda s: (per_sheet[s], rng.random()))
        terms: list[list[tuple[str, str]]] = []
        if rng.random() < 0.5:
            stop = f"STOP{len(inputs) + 1:02d}"
            inputs.append(stop)
            terms.append([(stop, "b")])
        if k > 0 and rng.random() < 0.5:
            start = (relays[rng.randrange(k)], "a")
        else:
            start = (f"PB{len(inputs) + 1:02d}", "a")
            inputs.append(start[0])
        terms.append([start, (relay, "a")] if rng.random() < 0.6 else [start])
        if k > 0 and rng.random() < 0.4:
            terms.append([(relays[rng.randrange(k)], rng.choice("ab"))])
        load_sheet = None
        if n_sheets > 1 and rng.random() < 0.35:
            load_sheet = rng.choice([s for s in range(n_sheets) if s != sheet])
        coil_sheet[relay] = sheet if load_sheet is None else load_sheet
        per_sheet[sheet] += 1
        rungs.append(Rung(sheet, ("coil", relay), terms, load_sheet))
        if rng.random() < 0.4:
            lamp_sheet = rng.randrange(n_sheets)
            per_sheet[lamp_sheet] += 1
            rungs.append(Rung(lamp_sheet, ("lamp", f"RL{k + 1:02d}"), [[(relay, rng.choice("ab"))]]))
    # 接点を 1 つも使わないリレーは、実際の図面には無い（リレー表にも行が無い）。表示灯の段で接点を使う。
    used = {d for r in rungs for term in r.terms for d, _ in term}
    for k, relay in enumerate(relays):
        if relay not in used:
            rungs.append(Rung(rng.randrange(n_sheets), ("lamp", f"RL{k + 1:02d}"), [[(relay, "a")]]))
    return Spec(seed, sheet_names, relays, inputs, rungs, coil_sheet)


# ------------------------------------------------------------------------------------------- 正解の動作


def oracle(spec: Spec, inputs: dict[str, bool], energized: dict[str, bool] | None = None) -> dict[str, Any]:
    """論理式で解いた動作（電気の計算を使わない）。前の励磁から、状態が変わらなくなるまで。"""
    state = {r: bool((energized or {}).get(r)) for r in spec.relays}

    def on(device: str) -> bool:
        return state[device] if device in state else bool(inputs.get(device))

    def live(r: Rung) -> bool:
        return all(any(on(d) == (kind == "a") for d, kind in term) for term in r.terms)

    for _ in range(50):
        new = {relay: any(live(r) for r in spec.rungs if r.load == ("coil", relay)) for relay in spec.relays}
        if new == state:
            break
        state = new
    lamps = {r.load[1]: live(r) for r in spec.rungs if r.load[0] == "lamp"}
    return {"coils": state, "lamps": lamps}


def oracle_reach(spec: Spec, device: str) -> set[str]:
    """device が影響しうる負荷（リレー・表示灯）。段の接点の器具から順にたどる。"""
    deps = spec.depends()
    reach: set[str] = set()
    frontier = [device]
    while frontier:
        d = frontier.pop()
        for load, used in deps.items():
            if d in used and load not in reach:
                reach.add(load)
                if load in spec.relays:
                    frontier.append(load)
    return reach


# ------------------------------------------------------------------------------------------- 図面にする


class _Sheet:
    def __init__(self, index: int, name: str) -> None:
        self.index, self.name = index, name
        self.symbols: list[dict[str, Any]] = []
        self.wires: list[tuple[tuple[str, str], tuple[str, str]]] = []
        self.rows: list[dict[str, str]] = []
        self.column = 2
        self.last_top: tuple[str, str] | None = None
        self.last_bottom: tuple[str, str] | None = None
        self.count = 0

    def ref(self, prefix: str) -> str:
        self.count += 1
        return f"{prefix}-{self.count:03d}"

    def add(self, ref: str, cls: str, label: str, x: float, y: float, w: float, h: float, terms: int) -> dict[str, Any]:
        points = [(x, y)] if terms == 1 else [(x, y - h / 2), (x, y + h / 2)]
        symbol = {"ref": ref, "class_key": cls, "class_label": cls, "label": label, "cx": x / WIDTH, "cy": y / HEIGHT,
                  "w": w / WIDTH, "h": h / HEIGHT, "origin": "inference", "confidence": 0.9, "note": None,
                  "attributes": {},
                  "terminals": [{"ref": f"{ref}-T{i}", "name": str(i), "tx": px / WIDTH, "ty": py / HEIGHT}
                                for i, (px, py) in enumerate(points, start=1)]}
        self.symbols.append(symbol)
        return symbol

    def wire(self, a: tuple[str, str], b: tuple[str, str]) -> None:
        self.wires.append((a, b))


def _x(column: float) -> float:
    return GRID["columns"][0] + GRID["columns"][1] * column


def _y(row: float) -> float:
    return GRID["rows"][0][1] + GRID["rows"][0][2] * row


def _t(symbol: dict[str, Any], i: int) -> tuple[str, str]:
    return symbol["ref"], symbol["terminals"][i - 1]["ref"]


def build(spec: Spec) -> dict[str, Any]:
    """spec を図面（pages）・構造解析（structures）・母線の文字（bus_texts）にする。"""
    sheets = [_Sheet(i, name) for i, name in enumerate(spec.sheets)]
    for s in sheets:
        p = s.add("P", "power_bus", "PER2100100", 30, TOP_Y, 18, 18, 1)
        n = s.add("N", "power_bus", "NER2100100", WIDTH - 50, BOTTOM_Y, 18, 18, 1)
        s.last_top, s.last_bottom = _t(p, 1), _t(n, 1)
    contacts_of: dict[str, list[tuple[int, str, str, str]]] = {}  # 器具 → (シート, ref, 種類, 行列)
    tag_pairs: list[tuple[tuple[int, tuple[str, str]], tuple[int, tuple[str, str]]]] = []

    def top_junction(s: _Sheet, column: float) -> tuple[str, str]:
        j = s.add(s.ref("JT"), "junction", "", _x(column), TOP_Y, 12, 12, 1)
        s.wire(s.last_top, _t(j, 1))
        s.last_top = _t(j, 1)
        return _t(j, 1)

    def bottom_junction(s: _Sheet, column: float) -> tuple[str, str]:
        j = s.add(s.ref("JB"), "junction", "", _x(column), BOTTOM_Y, 12, 12, 1)
        s.wire(_t(j, 1), s.last_bottom)
        s.last_bottom = _t(j, 1)
        return _t(j, 1)

    def contact(s: _Sheet, device: str, kind: str, column: float, row: int) -> dict[str, Any]:
        label = device
        if device in spec.coil_sheet and spec.coil_sheet[device] != s.index:
            label = f"{device} ({spec.sheets[spec.coil_sheet[device]]})"
        c = s.add(s.ref("C"), f"contact_{kind}", label, _x(column), _y(row + 0.5), 40, 60, 2)
        contacts_of.setdefault(device, []).append((s.index, c["ref"], kind, f"{column}{ROW_LETTERS[row]}"))
        return c

    for rung in spec.rungs:
        s = sheets[rung.sheet]
        column = s.column
        s.column += 2
        point = top_junction(s, column)
        for t, term in enumerate(rung.terms):
            row = 3 + 3 * t
            if len(term) == 1:
                c = contact(s, *term[0], column, row)
                s.wire(point, _t(c, 1))
                point = _t(c, 2)
            else:
                ja = s.add(s.ref("J"), "junction", "", _x(column), _y(row - 0.3), 12, 12, 1)
                jb = s.add(s.ref("J"), "junction", "", _x(column), _y(row + 1.3), 12, 12, 1)
                s.wire(point, _t(ja, 1))
                for i, (device, kind) in enumerate(term):
                    c = contact(s, device, kind, column + i, row)
                    s.wire(_t(ja, 1), _t(c, 1))
                    s.wire(_t(c, 2), _t(jb, 1))
                point = _t(jb, 1)
        if rung.load_sheet is not None:
            target = sheets[rung.load_sheet]
            tag = s.add(s.ref("TAG"), "other", f"{target.name}へ", _x(column), _y(TAG_ROW), 70, 30, 1)
            s.wire(point, _t(tag, 1))
            load_sheet = target
            lcol = target.column
            target.column += 2
            back = target.add(target.ref("TAG"), "other", f"({s.name})", _x(lcol), _y(2), 70, 30, 1)
            tag_pairs.append(((s.index, _t(tag, 1)), (target.index, _t(back, 1))))
            point, column, s = _t(back, 1), lcol, load_sheet
        kind, name = rung.load
        load = s.add(s.ref("K" if kind == "coil" else "H"), "relay_coil" if kind == "coil" else "lamp", name,
                     _x(column), _y(LOAD_ROW + 0.5), 50, 50, 2)
        s.wire(point, _t(load, 1))
        s.wire(_t(load, 2), bottom_junction(s, column))

    # 電線番号: つながり（ノード）ごとに 1 つ。行先札の両側は同じ番号。
    parent: dict[Any, Any] = {}

    def find(x: Any) -> Any:
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for s in sheets:
        for a, b in s.wires:
            parent[find((s.index, *a))] = find((s.index, *b))
    for (sa, ta), (sb, tb) in tag_pairs:
        parent[find((sa, *ta))] = find((sb, *tb))
    numbers: dict[Any, str] = {}
    pages, structures, bus_texts = [], {}, {}
    for s in sheets:
        pid = s.index + 1
        connections = []
        for a, b in s.wires:
            root = find((s.index, *a))
            number = numbers.setdefault(root, str(601 + len(numbers)))
            connections.append({"from_symbol_ref": a[0], "from_terminal_ref": a[1], "to_symbol_ref": b[0],
                                "to_terminal_ref": b[1], "wire_no": number, "net_id": None, "kind": "wire",
                                "external_ref": None, "note": None, "origin": "inference"})
        pages.append({"id": pid, "name": f"{s.name}.png", "sheet_no": s.name, "image_width": WIDTH,
                      "image_height": HEIGHT, "symbols": s.symbols, "connections": connections})
        bus_texts[pid] = [{"text": "PER2100100", "box": [20, TOP_Y - 25, 160, TOP_Y - 10]},
                          {"text": "NER2100100", "box": [WIDTH - 200, BOTTOM_Y - 25, WIDTH - 40, BOTTOM_Y - 10]}]
    for relay in spec.relays:
        coil = spec.coil_sheet[relay]
        rows = []
        for sheet, _ref, kind, position in contacts_of.get(relay, []):
            rows.append({"device": relay, "terminal": "5-9-1" if kind == "a" else "6-10-2", "contact": kind,
                         "sheet": "本" if sheet == coil else spec.sheets[sheet], "position": position})
        sheets[coil].rows += rows
    for s in sheets:
        structures[s.index + 1] = {"relay_table": {"rows": s.rows}, "drawing_grid": copy.deepcopy(GRID)}
    return {"pages": pages, "structures": structures, "bus_texts": bus_texts, "tag_pairs": len(tag_pairs)}
