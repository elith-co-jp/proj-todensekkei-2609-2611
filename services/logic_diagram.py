"""展開接続図から論理図（ロジック図）を作り、論理図の上で時間の流れをシミュレーションする（DD-0045）。DB を使わない。

2026-11-25 に受け取った紙芝居（図面の読み方）の最終ページの図を、図面の読み取り結果（回路）から機械で作る。

手順は紙芝居と同じ。

1. 出力（起動指令 6X・停止指令 5X など）のコイルから、電源側・戻り側の接点を辿って成立条件を求める（DD-0035 の逆引き）。
   直列は AND、並列は OR、b 接点は NOT（⊠）。
2. 接点を動かす元へ繰り返し辿る。リレーは同じ手順でその条件を求め、外部入力（他盤からの信号・I/O）に着いたら止める。
3. 図を読みやすくまとめる。
   - 同じ条件で動くリレー（83DX と 83DX-1・-2・-3 のような増設のリレー）は 1 つの信号にする。
   - 接点 1 つで動くだけのリレー（83D ← D009、83DX ← 83D）は、元の信号の別名にする（線を 1 本にする）。
   - 電源に直接つながるリレー（80 電源正常）は「電源が来ていれば ON」の入力にする（電源の喪失を試せる）。
   - オンディレー・オフディレーのタイマー（20MB 1sec）は遅延の記号、キープリレー（43LK の O・R コイル）は S/R の記号にする。
   - 何か所でも使う信号・タイマー・キープリレーは、紙芝居の A・B・C・D のようにコネクタ（丸に英字）で定義と参照を分ける。
     1 か所でしか使わないリレーの条件は、使う側の図にそのまま描き込む。
4. 図（SVG）にする。AND は縦棒と小さい四角、OR は縦棒と小さい丸、NOT は線の上の ⊠、外部入力は六角形。
   状態を与えると、成立している線を紙芝居と同じようにピンクで示す。

シミュレーションは信号の論理で解く（回路の電圧は解かない）。時刻つきの操作（外部入力の ON/OFF）を順に入れ、
タイマーの満了・キープリレーのセット・リセットを含めて、状態が落ち着くまで解く。同じ操作を回路の計算
（circuit_timing.run_timeline）でも解いて、リレーごとに結果が一致するかを照合できる（compare_with_circuit）。
"""

from __future__ import annotations

import html
import math
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

from services.circuit_model import Circuit, canonical_device, latch_next
from services.circuit_trace import INPUT, RELAY, SOURCE_LABEL, Tracer, to_text

MAX_SETTLE = 200
MAX_EVENTS = 500
EPS = 1e-9
LETTERS = "ABCDEFGHJKLMNPQRSTUVWXYZ"

# ------------------------------------------------------------------------------------------- 信号


@dataclass
class Signal:
    """論理図の 1 本の信号。kind は input（外部入力）/ power（電源が来ていれば ON）/ logic（リレー）/ timer / latch。"""

    id: str
    kind: str
    devices: list[str]                       # この信号にまとめた器具番号（別名・増設のリレー）
    name: str = ""                           # 図に出す名前（属性 signal_name・names。無ければ器具番号）
    ref_name: str = ""                       # コネクタで参照するときの名前（入力の別名のリレーの名前。例「主制御装置選択」）
    expr: dict[str, Any] | None = None       # logic・timer の入力の式（葉は {"sig": 信号 ID}）
    set_expr: dict[str, Any] | None = None   # latch のセット
    reset_expr: dict[str, Any] | None = None  # latch のリセット
    timer: dict[str, Any] | None = None      # {"mode": on_delay / off_delay, "delay_s": 秒}
    priority: str = "reset"                  # latch でセットとリセットが同時のとき
    default: bool = False                    # 入力の初期値（power は True）
    source: str = ""                         # 入力の出どころ（circuit_trace の INPUT / MISSING_REFERENCE / UNRESOLVED）
    source_label: str = ""
    letter: str | None = None                # コネクタの英字（A・B…）
    output: bool = False                     # 図の出力（対象）
    note: str = ""

    def json(self) -> dict[str, Any]:
        out = {"id": self.id, "kind": self.kind, "devices": self.devices, "name": self.name,
               "ref_name": self.ref_name or self.name, "letter": self.letter,
               "output": self.output, "default": self.default}
        if self.expr is not None:
            out["expr"] = self.expr
            out["text"] = expr_text(self.expr)
        if self.kind == "latch":
            out |= {"set": self.set_expr, "reset": self.reset_expr, "set_text": expr_text(self.set_expr or {"const": False}),
                    "reset_text": expr_text(self.reset_expr or {"const": False}), "priority": self.priority}
        if self.timer is not None:
            out["timer"] = self.timer
        if self.source:
            out |= {"source": self.source, "source_label": self.source_label}
        if self.note:
            out["note"] = self.note
        return out


def _short(device: str) -> str:
    """「83DX P33P22」→「83DX」。器具番号の先頭の語。"""
    return (device or "").split()[0] if (device or "").split() else device


def expr_text(tree: dict[str, Any], names: dict[str, str] | None = None) -> str:
    """式を ∧ ∨ ¬ の文字にする。names があれば信号 ID をその名前に。"""
    if "const" in tree:
        return "1" if tree["const"] else "0"
    if "sig" in tree:
        name = (names or {}).get(tree["sig"], tree["sig"])
        return ("¬" if tree.get("neg") else "") + name
    if tree["op"] == "not":
        return "¬(" + expr_text(tree["args"][0], names) + ")"
    sep = " ∧ " if tree["op"] == "and" else " ∨ "
    parts = []
    for a in tree["args"]:
        t = expr_text(a, names)
        parts.append(f"({t})" if a.get("op") in ("and", "or") and a["op"] != tree["op"] else t)
    return sep.join(parts)


def evaluate(tree: dict[str, Any], values: dict[str, bool]) -> bool:
    if "const" in tree:
        return bool(tree["const"])
    if "sig" in tree:
        v = bool(values.get(tree["sig"], False))
        return not v if tree.get("neg") else v
    vals = [evaluate(a, values) for a in tree["args"]]
    if tree["op"] == "not":
        return not vals[0]
    return all(vals) if tree["op"] == "and" else any(vals)


def _refs(tree: dict[str, Any] | None) -> list[str]:
    if not tree:
        return []
    if "sig" in tree:
        return [tree["sig"]]
    return [r for a in tree.get("args", []) for r in _refs(a)]


def _map_tree(tree: dict[str, Any], resolve) -> dict[str, Any]:
    """circuit_trace の式（葉は {"lit", "kind"}）→ 信号の式（葉は {"sig", "neg"}）。resolve(器具番号) → (信号 ID, 反転)。"""
    if "const" in tree:
        return dict(tree)
    if "lit" in tree:
        sig, neg = resolve(tree["lit"])
        return {"sig": sig, "neg": neg != (tree.get("kind") == "b")}
    if "sig" in tree:
        sig, neg = resolve(tree["sig"])
        return {"sig": sig, "neg": neg != bool(tree.get("neg"))}
    return {"op": tree["op"], "args": [_map_tree(a, resolve) for a in tree["args"]]}


# ------------------------------------------------------------------------------------------- 論理図を組む


@dataclass
class LogicDiagram:
    signals: dict[str, Signal]
    outputs: list[str]
    alias: dict[str, tuple[str, bool]]       # 器具番号 → (信号 ID, 反転)
    diagnostics: list[dict[str, Any]] = field(default_factory=list)
    title: str = ""

    # 名前 ---------------------------------------------------------------------------------------------
    def label(self, sig: str) -> str:
        s = self.signals[sig]
        return s.name or _short(s.id)

    def ref_label(self, sig: str) -> str:
        """コネクタの名前（入力なら別名のリレーの名前を優先）。"""
        return self.signals[sig].ref_name or self.label(sig)

    def names(self) -> dict[str, str]:
        return {k: (f"{s.letter}:" + self.ref_label(k) if s.letter else self.label(k)) for k, s in self.signals.items()}

    def resolve(self, device: str) -> tuple[str, bool]:
        """器具番号・信号 ID・名前 → (信号 ID, 反転)。"""
        if device in self.alias:
            return self.alias[device]
        if device in self.signals:
            return device, False
        canon = canonical_device(device)
        for dev, hit in self.alias.items():
            if canonical_device(dev) == canon or canonical_device(_short(dev)) == canon:
                return hit
        for sig, s in self.signals.items():
            if s.name == device or s.letter == device:
                return sig, False
        raise ValueError(f"{device} という信号・器具番号が論理図にありません")

    def inputs(self) -> list[Signal]:
        return [s for s in self.signals.values() if s.kind in ("input", "power")]

    def json(self) -> dict[str, Any]:
        names = self.names()
        blocks = []
        for sig in self.block_order():
            s = self.signals[sig]
            item = {"signal": sig, "letter": s.letter, "name": self.ref_label(sig), "kind": s.kind, "output": s.output,
                    "devices": s.devices}
            if s.kind == "latch":
                item["text"] = (f"S = {expr_text(s.set_expr or {'const': False}, names)}、"
                                f"R = {expr_text(s.reset_expr or {'const': False}, names)}")
            elif s.kind == "timer":
                item["text"] = (f"{_timer_label(s.timer)}（{expr_text(s.expr or {'const': False}, names)}）")
            elif s.kind in ("input", "power"):
                item["text"] = f"{'電源' if s.kind == 'power' else '入力'}「{self.label(sig)}」"
            else:
                item["text"] = expr_text(s.expr or {"const": False}, names)
            blocks.append(item)
        return {"title": self.title, "outputs": self.outputs, "signals": [s.json() for s in self.signals.values()],
                "blocks": blocks, "alias": {k: {"signal": v[0], "negated": v[1]} for k, v in sorted(self.alias.items())},
                "inputs": [{"signal": s.id, "name": self.label(s.id), "devices": s.devices, "default": s.default,
                            "kind": s.kind, "source": s.source, "source_label": s.source_label} for s in self.inputs()],
                "diagnostics": self.diagnostics}

    def block_order(self) -> list[str]:
        """図に描くまとまりの順: コネクタ（A, B, …）の定義、続いて出力。"""
        lettered = sorted((s for s in self.signals.values() if s.letter and not s.output), key=lambda s: _letter_key(s.letter))
        outs = [self.signals[o] for o in self.outputs]
        return [s.id for s in lettered] + [s.id for s in outs]


def _letter_key(letter: str | None) -> tuple[int, str]:
    return (len(letter or ""), letter or "")


def _timer_label(timer: dict[str, Any] | None) -> str:
    if not timer:
        return ""
    delay = timer.get("delay_s") or 0
    text = f"{delay:g}sec"
    return text if timer.get("mode") == "on_delay" else f"OFF {text}"


def _letters():
    for n in range(1, 3):
        for i in range(len(LETTERS) ** n):
            s, k = "", i
            for _ in range(n):
                s = LETTERS[k % len(LETTERS)] + s
                k //= len(LETTERS)
            yield s


def default_targets(circuit: Circuit, tracer: Tracer | None = None) -> list[str]:
    """出力の候補: 図面セットの中で、ほかのリレーの条件に接点が出てこないリレー（最後に動くリレー）。"""
    tracer = tracer or Tracer(circuit)
    sinks = _sinks(circuit, tracer)
    # 接点が図面のどこかで使われている（M/C・他盤・表示灯へ出ていく）リレーを出力にする。増設の予備のリレー（接点の無い
    # 83DX-2 など）は外す。どれにも接点が無ければ、最後に動くリレーを全部。
    with_contacts = {e.device for e in circuit.elements if e.kind in ("a", "b")}
    picked = [d for d in sinks if d in with_contacts]
    return picked or sinks


def _sinks(circuit: Circuit, tracer: Tracer) -> list[str]:
    used: set[str] = set()
    for dev in circuit.coils:
        if dev in circuit.latches:
            lc = tracer.latch_condition(dev)
            used |= set(lc["set"]["devices"]) | set(lc["reset"]["devices"])
        else:
            used |= {d for d in tracer.condition(dev)["devices"] if d != dev}
    return sorted(d for d in circuit.coils if d not in used)


def build_logic(circuit: Circuit, pages: list[dict[str, Any]] | None = None, *, targets: list[str] | None = None,
                external: list[str] | tuple[str, ...] = (), names: dict[str, str] | None = None,
                merge: bool = True, title: str = "") -> LogicDiagram:
    """回路から論理図を組む。targets は出力にするコイルの器具番号（省略時は default_targets）。

    names: 器具番号 → 図に出す名前（例 {"D009 B413": "主制御装置選択指令"}）。記号の属性 signal_name より優先。
    merge: 同じ条件のリレーを 1 つの信号にまとめ、接点 1 つで動くだけのリレーを別名にする（False なら器具ごと）。
    """
    tracer = Tracer(circuit, pages, external=external)
    diagnostics: list[dict[str, Any]] = []
    if targets is None:
        targets = default_targets(circuit, tracer)
    resolved_targets: list[str] = []
    for t in targets:
        canon = canonical_device(t)
        hit = next((d for d in circuit.coils if d == t or canonical_device(d) == canon), None)
        if hit is None:
            hit = next((d for d in circuit.coils if canonical_device(_short(d)) == canon), None)
        if hit is None:
            raise ValueError(f"出力のコイル {t} が回路にありません")
        resolved_targets.append(hit)

    # 1. 出力から接点の元へ辿って、関係するリレーと入力を集める ------------------------------------
    raw: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    stack = list(reversed(resolved_targets))
    inputs: set[str] = set()
    while stack:
        dev = stack.pop()
        if dev in raw:
            continue
        if dev in circuit.latches:
            lc = tracer.latch_condition(dev)
            raw[dev] = {"kind": "latch", "set": lc["set"]["tree"], "reset": lc["reset"]["tree"],
                        "priority": lc["priority"], "devices": lc["set"]["devices"] + lc["reset"]["devices"],
                        "truncated": lc["set"]["truncated"] or lc["reset"]["truncated"]}
        else:
            cond = tracer.condition(dev)
            raw[dev] = {"kind": "timer" if dev in circuit.timers else "logic", "tree": cond["tree"],
                        "devices": cond["devices"], "truncated": cond["truncated"], "paths": len(cond["branches"])}
            if not cond["branches"]:
                diagnostics.append({"kind": "no_path", "device": dev,
                                    "message": f"{dev} のコイルに電源から戻りまでの経路が見つかりません（常に OFF として描きます）"})
        if raw[dev]["truncated"]:
            diagnostics.append({"kind": "truncated", "device": dev, "message": f"{dev} の経路が多いため途中で打ち切りました"})
        order.append(dev)
        for d in raw[dev]["devices"]:
            src = tracer.sources.get(d, {}).get("source", RELAY if d in circuit.coils else INPUT)
            if src == RELAY and d in circuit.coils:
                stack.append(d)
            else:
                inputs.add(d)

    # 2. まとめる: 別名（接点 1 つで動くリレー）と、同じ条件のリレー ------------------------------
    parent: dict[str, tuple[str, bool]] = {}   # 器具番号 → (代表, 反転)

    def find(dev: str) -> tuple[str, bool]:
        neg = False
        seen = set()
        while dev in parent and dev not in seen:
            seen.add(dev)
            dev, n = parent[dev]
            neg ^= n
        return dev, neg

    power: set[str] = set()
    protected = set(resolved_targets)
    if merge:
        for _ in range(len(raw) + 1):
            changed = False
            # 別名: タイマー・キープリレーでなく、条件が a 接点 1 つだけのリレー（出力は除く）。
            for dev in order:
                r = raw[dev]
                if dev in parent or dev in protected or r["kind"] != "logic":
                    continue
                tree = r["tree"]
                if "lit" in tree and tree.get("kind", "a") == "a":
                    rep, neg = find(tree["lit"])
                    if rep != dev:
                        parent[dev] = (rep, neg)
                        changed = True
            # 同じ条件: 式（代表に置き換えた形）と種類が同じリレーを 1 つに。
            groups: dict[tuple[Any, ...], list[str]] = defaultdict(list)
            for dev in order:
                if dev in parent:
                    continue
                r = raw[dev]
                key: tuple[Any, ...]
                if r["kind"] == "latch":
                    key = ("latch", _key(r["set"], find), _key(r["reset"], find), r["priority"])
                else:
                    timer = circuit.timers.get(dev)
                    key = (r["kind"], _key(r["tree"], find), tuple(sorted((timer or {}).items())))
                groups[key].append(dev)
            for devs in groups.values():
                if len(devs) < 2:
                    continue
                keep = [d for d in devs if d in protected]
                rep = keep[0] if keep else min(devs, key=lambda d: (len(_short(d)), d))
                for d in devs:
                    if d != rep and d not in protected:
                        parent[d] = (rep, False)
                        changed = True
            if not changed:
                break
    for dev in order:
        r = raw[dev]
        if dev not in parent and r["kind"] == "logic" and r["tree"].get("const") is True:
            power.add(dev)

    # 3. 信号にする ---------------------------------------------------------------------------------
    names = dict(names or {})
    attr_names = _attribute_names(pages or [], circuit)
    signals: dict[str, Signal] = {}
    alias: dict[str, tuple[str, bool]] = {}
    for dev in sorted(inputs):
        src = tracer.sources.get(dev, {})
        kind = src.get("source", INPUT)
        signals[dev] = Signal(dev, "input", [dev], source=kind, source_label=SOURCE_LABEL.get(kind, ""),
                              note=src.get("reason", ""))
    for dev in order:
        if dev in parent:
            continue
        r = raw[dev]
        if dev in power:
            signals[dev] = Signal(dev, "power", [dev], default=True, source="power",
                                  source_label="電源（コイルが母線に直接つながる）",
                                  note="接点を通らずに電源につながるコイル。電源が来ていれば ON")
        elif r["kind"] == "latch":
            signals[dev] = Signal(dev, "latch", [dev], priority=r["priority"])
        else:
            signals[dev] = Signal(dev, r["kind"], [dev], timer=dict(circuit.timers[dev]) if dev in circuit.timers else None)
    for dev in list(parent) + list(signals):
        rep, neg = find(dev)
        alias[dev] = (rep, neg)
        if dev != rep and rep in signals and dev not in signals[rep].devices:
            signals[rep].devices.append(dev)

    def resolve(dev: str) -> tuple[str, bool]:
        if dev in alias:
            return alias[dev]
        return find(dev)

    for sig, s in signals.items():
        r = raw.get(sig)
        if r is None or s.kind == "power":
            continue
        if s.kind == "latch":
            s.set_expr = _tidy(_map_tree(r["set"], resolve))
            s.reset_expr = _tidy(_map_tree(r["reset"], resolve))
        else:
            s.expr = _tidy(_map_tree(r["tree"], resolve))

    # 名前: names（器具番号でも先頭の語でも）> 記号の属性 signal_name > 器具番号の先頭の語。
    for sig, s in signals.items():
        for dev in [sig, *s.devices]:
            name = names.get(dev) or names.get(_short(dev)) or attr_names.get(dev)
            if name:
                s.name = name
                break
        if not s.name:
            s.name = _short(sig)
        if s.kind in ("input", "power"):
            for dev in s.devices[1:]:
                name = names.get(dev) or names.get(_short(dev)) or attr_names.get(dev)
                if name and name != s.name:
                    s.ref_name = name
                    break
        if len(s.devices) > 1:
            s.devices = [sig] + sorted(d for d in s.devices if d != sig)

    outputs: list[str] = []
    for t in resolved_targets:
        rep, neg = alias.get(t, (t, False))
        if rep not in outputs:
            outputs.append(rep)
            signals[rep].output = True

    diagram = LogicDiagram(signals, outputs, alias, diagnostics + _input_notes(signals), title)
    _assign_connectors(diagram)
    return diagram


def _key(tree: dict[str, Any], find) -> str:
    return expr_text(_tidy(_map_tree(tree, find)))


def _tidy(tree: dict[str, Any]) -> dict[str, Any]:
    """平らにして、同じ項を除く（並びは図面のまま）。"""
    if "args" not in tree:
        return tree
    args = [_tidy(a) for a in tree["args"]]
    if tree["op"] == "not":
        inner = args[0]
        if "sig" in inner:
            return {"sig": inner["sig"], "neg": not inner.get("neg")}
        return {"op": "not", "args": [inner]}
    flat: list[dict[str, Any]] = []
    for a in args:
        flat += a["args"] if a.get("op") == tree["op"] else [a]
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for a in flat:
        k = expr_text(a)
        if k not in seen:
            seen.add(k)
            out.append(a)
    # 並びは式の木が先、単独の信号が後（紙芝居の図と同じく、まとまりを上に描く）。
    out = [a for a in out if "op" in a] + [a for a in out if "op" not in a]
    if tree["op"] == "and":
        if any(a.get("const") is False for a in out):
            return {"const": False}
        out = [a for a in out if a.get("const") is not True]
    else:
        if any(a.get("const") is True for a in out):
            return {"const": True}
        out = [a for a in out if a.get("const") is not False]
    if not out:
        return {"const": tree["op"] == "and"}
    return out[0] if len(out) == 1 else {"op": tree["op"], "args": out}


def _attribute_names(pages: list[dict[str, Any]], circuit: Circuit) -> dict[str, str]:
    """記号の属性 signal_name → 器具番号ごとの名前（コイルの属性を優先）。"""
    by_symbol = {(e.project_id, e.symbol_ref): e for e in circuit.elements}
    coil_names: dict[str, str] = {}
    contact_names: dict[str, str] = {}
    for p in pages:
        for s in p.get("symbols") or []:
            name = str((s.get("attributes") or {}).get("signal_name") or "").strip()
            e = by_symbol.get((p["id"], s["ref"]))
            if not name or e is None or not e.device:
                continue
            (coil_names if e.kind == "load" else contact_names).setdefault(e.device, name)
    return contact_names | coil_names


def _input_notes(signals: dict[str, Signal]) -> list[dict[str, Any]]:
    out = []
    for s in signals.values():
        if s.kind == "input" and s.source not in ("", INPUT):
            out.append({"kind": s.source, "device": s.id, "message": f"{s.id}: {s.source_label}（{s.note}）"})
    return out


def _assign_connectors(d: LogicDiagram) -> None:
    """何か所でも使う信号・タイマー・キープリレー・自己保持の信号にコネクタの英字を付ける。"""
    uses: dict[str, int] = defaultdict(int)
    deps: dict[str, set[str]] = {}
    for sig, s in d.signals.items():
        refs = _refs(s.expr) + _refs(s.set_expr) + _refs(s.reset_expr)
        deps[sig] = set(refs)
        for r in refs:
            uses[r] += 1
    cyclic = _cyclic(deps)
    used_letters = set()
    letters = _letters()
    # 入力から順に（紙芝居の A = 主制御装置選択が先頭）: 依存の浅い順、同じ深さは器具番号の順。
    depth = _depth(deps)
    appear: dict[str, int] = {}

    def visit(sig: str) -> None:
        if sig in appear:
            return
        appear[sig] = len(appear)
        s = d.signals[sig]
        for r in _refs(s.expr) + _refs(s.set_expr) + _refs(s.reset_expr):
            visit(r)

    for o in d.outputs:
        visit(o)
    for sig in sorted(d.signals, key=lambda k: (depth.get(k, 0), appear.get(k, len(appear)), k)):
        s = d.signals[sig]
        need = (uses[sig] >= 2 or s.kind in ("timer", "latch") or sig in cyclic) and uses[sig] >= 1
        if s.output and uses[sig] == 0:
            need = False
        if need:
            letter = next(letters)
            while letter in used_letters:
                letter = next(letters)
            used_letters.add(letter)
            s.letter = letter


def _cyclic(deps: dict[str, set[str]]) -> set[str]:
    """自己保持など、条件が自分に戻ってくる信号。"""
    out = set()
    for start in deps:
        stack, seen = list(deps.get(start, ())), set()
        while stack:
            n = stack.pop()
            if n == start:
                out.add(start)
                break
            if n in seen:
                continue
            seen.add(n)
            stack += list(deps.get(n, ()))
    return out


def _depth(deps: dict[str, set[str]]) -> dict[str, int]:
    memo: dict[str, int] = {}

    def go(n: str, path: frozenset[str]) -> int:
        if n in memo:
            return memo[n]
        if n in path:
            return 0
        children = deps.get(n, set())
        v = 0 if not children else 1 + max(go(c, path | {n}) for c in children)
        memo[n] = v
        return v

    for n in deps:
        go(n, frozenset())
    return memo


# ------------------------------------------------------------------------------------------- シミュレーション


def _settle(d: LogicDiagram, values: dict[str, bool], timer_out: dict[str, bool], pending: dict[str, float],
            t: float) -> list[str]:
    """値が変わらなくなるまで解く。タイマーの入力の変化で満了の時刻を決める。警告を返す。"""
    seen: list[dict[str, bool]] = []
    for _ in range(MAX_SETTLE):
        changed = False
        for sig, s in d.signals.items():
            if s.kind in ("input", "power"):
                continue
            if s.kind == "logic":
                v = evaluate(s.expr or {"const": False}, values)
            elif s.kind == "latch":
                v = latch_next({"priority": s.priority}, evaluate(s.set_expr or {"const": False}, values),
                               evaluate(s.reset_expr or {"const": False}, values), values.get(sig, False))
            else:  # timer
                on = evaluate(s.expr or {"const": False}, values)
                delay = float((s.timer or {}).get("delay_s") or 0)
                mode = (s.timer or {}).get("mode", "on_delay")
                out = timer_out.get(sig, False)
                if mode == "on_delay":
                    if not on:
                        pending.pop(sig, None)
                        out = False
                    elif not out:
                        if delay <= EPS:
                            out = True
                        else:
                            pending.setdefault(sig, t + delay)
                else:
                    if on:
                        pending.pop(sig, None)
                        out = True
                    elif out:
                        if delay <= EPS:
                            out = False
                        else:
                            pending.setdefault(sig, t + delay)
                timer_out[sig] = out
                v = out
            if values.get(sig) != v:
                values[sig] = v
                changed = True
        if not changed:
            return []
        snap = dict(values)
        if snap in seen:
            return ["信号が繰り返し変わり、落ち着きません（発振する論理の可能性）"]
        seen.append(snap)
    return ["信号が落ち着きません（繰り返しの上限）"]


def initial_values(d: LogicDiagram, inputs: dict[str, bool] | None = None,
                   energized: dict[str, bool] | None = None) -> dict[str, bool]:
    """入力の初期値（power は ON）と、前の状態（キープリレー・自己保持）。キーは器具番号でも信号 ID でもよい。"""
    values = {sig: s.default for sig, s in d.signals.items()}
    for k, v in (energized or {}).items():
        sig, neg = d.resolve(k)
        values[sig] = bool(v) != neg
    for k, v in (inputs or {}).items():
        sig, neg = d.resolve(k)
        values[sig] = bool(v) != neg
    return values


def solve(d: LogicDiagram, inputs: dict[str, bool] | None = None, energized: dict[str, bool] | None = None) -> dict[str, Any]:
    """時間を進めずに 1 回解く（タイマーは前の出力のまま、オンディレーは満了していない）。"""
    values = initial_values(d, inputs, energized)
    timer_out = {sig: values.get(sig, False) for sig, s in d.signals.items() if s.kind == "timer"}
    pending: dict[str, float] = {}
    warnings = _settle(d, values, timer_out, pending, 0.0)
    return {"values": values, "pending": pending, "warnings": warnings}


def simulate(d: LogicDiagram, events: list[dict[str, Any]], *, inputs: dict[str, bool] | None = None,
             energized: dict[str, bool] | None = None, until: float | None = None) -> dict[str, Any]:
    """時刻つきの操作 events（{"at": 秒, "inputs": {器具番号・信号: bool}, "label": 任意}）を順に解く。

    戻り値の timeline は状態が変わった時刻ごとの記録（values は信号 ID → ON/OFF、changes は前からの差）。
    waves は信号ごとの (時刻, 値) の並び（タイムチャート用）。
    """
    values = initial_values(d, inputs, energized)
    timer_out = {sig: values.get(sig, False) for sig, s in d.signals.items() if s.kind == "timer"}
    pending: dict[str, float] = {}
    queue = sorted(({**e, "at": float(e.get("at") or 0)} for e in events), key=lambda e: e["at"])
    warnings = _settle(d, values, timer_out, pending, 0.0)
    timeline = [{"t": 0.0, "label": "初期状態", "values": dict(values), "changes": [], "warnings": warnings,
                 "pending": {k: round(v, 6) for k, v in pending.items()}}]
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
        due_now = {sig for sig, due in pending.items() if due <= t + EPS}
        while queue and queue[0]["at"] <= t + EPS:
            event = queue.pop(0)
            for k, v in (event.get("inputs") or {}).items():
                sig, neg = d.resolve(str(k))
                if d.signals[sig].kind not in ("input", "power"):
                    raise ValueError(f"{k} は入力ではありません（{d.label(sig)} はリレーの信号）")
                values[sig] = bool(v) != neg
            labels.append(event.get("label") or "、".join(f"{d.label(d.resolve(str(k))[0])}={'ON' if v else 'OFF'}"
                                                         for k, v in (event.get("inputs") or {}).items()))
        warnings = _settle(d, values, timer_out, pending, t)
        fired = [sig for sig in sorted(due_now) if sig in pending and pending[sig] <= t + EPS]
        for sig in fired:
            timer_out[sig] = (d.signals[sig].timer or {}).get("mode", "on_delay") == "on_delay"
            values[sig] = timer_out[sig]
            del pending[sig]
        if fired:
            warnings += _settle(d, values, timer_out, pending, t)
        labels = [f"{d.label(sig)} の時間が経過" for sig in fired] + labels
        prev = timeline[-1]["values"]
        changes = [{"signal": k, "name": d.label(k), "before": prev.get(k), "after": v}
                   for k, v in values.items() if prev.get(k) != v]
        timeline.append({"t": round(t, 6), "label": " / ".join(x for x in labels if x), "values": dict(values),
                         "changes": changes, "warnings": warnings, "pending": {k: round(v, 6) for k, v in pending.items()}})
    if until is not None and timeline[-1]["t"] < until:
        timeline.append({**timeline[-1], "t": round(until, 6), "label": "終了", "changes": []})
    waves = {sig: _wave(timeline, sig) for sig in d.signals}
    return {"timeline": timeline, "waves": waves, "end": timeline[-1]["t"]}


def _wave(timeline: list[dict[str, Any]], sig: str) -> list[list[Any]]:
    out: list[list[Any]] = []
    for snap in timeline:
        v = bool(snap["values"].get(sig))
        if not out or out[-1][1] != v:
            out.append([snap["t"], v])
    return out


def compare_with_circuit(circuit: Circuit, d: LogicDiagram, events: list[dict[str, Any]], *,
                         inputs: dict[str, bool] | None = None, energized: dict[str, bool] | None = None,
                         until: float | None = None) -> dict[str, Any]:
    """同じ操作を回路の計算（電圧を解く）と論理図の両方で解き、リレーごとに一致するかを照合する。

    論理図の作り方（まとめ方・別名）の誤りや、論理図にできない回路（回り込み・ダイオードの向きしだいの経路など）を見つける。
    """
    from services.circuit_timing import run_timeline

    power_off = {sig for sig, s in d.signals.items() if s.kind == "power"}
    circuit_events = []
    for e in events:
        ins = {k: v for k, v in (e.get("inputs") or {}).items() if d.resolve(str(k))[0] not in power_off}
        circuit_events.append({**e, "inputs": ins})
    base_inputs = {k: v for k, v in (inputs or {}).items() if d.resolve(str(k))[0] not in power_off}
    ref = run_timeline(circuit, circuit_events, inputs=base_inputs, energized=energized, until=until)
    logic = simulate(d, events, inputs=inputs, energized=energized, until=until)
    times = sorted({snap["t"] for snap in ref["timeline"]} | {snap["t"] for snap in logic["timeline"]})
    mismatches = []
    checked = 0
    for t in times:
        c_snap = _at(ref["timeline"], t)
        l_snap = _at(logic["timeline"], t)
        for dev, on in c_snap["coils"].items():
            if dev not in d.alias:
                continue
            sig, neg = d.alias[dev]
            if sig in power_off:
                continue
            checked += 1
            mine = bool(l_snap["values"].get(sig)) != neg
            if dev in circuit.timers:
                on = bool(c_snap["timers"].get(dev, {}).get("output", on))
            if mine != bool(on):
                mismatches.append({"t": t, "device": dev, "circuit": bool(on), "logic": mine})
    return {"agree": not mismatches, "checked": checked, "mismatches": mismatches[:50],
            "circuit_timeline": len(ref["timeline"]), "logic_timeline": len(logic["timeline"])}


def _at(timeline: list[dict[str, Any]], t: float) -> dict[str, Any]:
    last = timeline[0]
    for snap in timeline:
        if snap["t"] <= t + EPS:
            last = snap
    return last


# ------------------------------------------------------------------------------------------- 図（SVG）

ROW = 34          # 葉 1 つの高さ
GAP = 64          # ゲートの列の間隔
LEAF_W = 236      # 入力の六角形・コネクタの欄の幅
SINK_W = 230      # 出力の欄の幅
PAD = 18
BLOCK_GAP = 26
FONT = "'Noto Sans JP','Hiragino Sans','Yu Gothic','Meiryo',sans-serif"
HIGHLIGHT = "#f4a7b9"
LINE = "#222"


@dataclass
class _Node:
    kind: str                       # "and" / "or" / "leaf" / "const"
    children: list[tuple[_Node, bool]] = field(default_factory=list)  # (子, 反転して入る)
    sig: str | None = None
    value: bool | None = None
    y: float = 0.0
    x: float = 0.0
    depth: int = 0


class _Svg:
    def __init__(self) -> None:
        self.parts: list[str] = []

    def add(self, s: str) -> None:
        self.parts.append(s)

    def line(self, x1, y1, x2, y2, *, on: bool | None = None, width: float = 1.4) -> None:
        if on:
            self.add(f'<line x1="{x1:.1f}" y1="{y1:.1f}" x2="{x2:.1f}" y2="{y2:.1f}" stroke="{HIGHLIGHT}" '
                     f'stroke-width="7" stroke-linecap="round"/>')
        self.add(f'<line x1="{x1:.1f}" y1="{y1:.1f}" x2="{x2:.1f}" y2="{y2:.1f}" stroke="{LINE}" stroke-width="{width}"/>')

    def text(self, x, y, s: str, *, size: int = 12, anchor: str = "start", weight: str = "normal", cls: str = "") -> None:
        c = f' class="{cls}"' if cls else ""
        self.add(f'<text x="{x:.1f}" y="{y:.1f}" font-size="{size}" text-anchor="{anchor}" font-weight="{weight}" '
                 f'dominant-baseline="central" fill="{LINE}"{c}>{html.escape(s)}</text>')


def _to_nodes(tree: dict[str, Any], d: LogicDiagram, inline: set[str], stack: tuple[str, ...] = ()) -> tuple[_Node, bool]:
    """信号の式 → 描く木。1 か所でしか使わないリレー（英字の無い logic）は、その条件を描き込む。"""
    if "const" in tree:
        return _Node("const", value=bool(tree["const"])), False
    if "sig" in tree:
        sig = tree["sig"]
        s = d.signals[sig]
        if s.kind == "logic" and not s.letter and not s.output and sig in inline and sig not in stack and s.expr:
            node, neg = _to_nodes(s.expr, d, inline, (*stack, sig))
            node.sig = node.sig or sig
            return node, neg != bool(tree.get("neg"))
        return _Node("leaf", sig=sig), bool(tree.get("neg"))
    if tree["op"] == "not":
        node, neg = _to_nodes(tree["args"][0], d, inline, stack)
        return node, not neg
    children = [_to_nodes(a, d, inline, stack) for a in tree["args"]]
    # すべて反転して入る AND は、紙芝居と同じく OR の出力を反転する形（¬A ∧ ¬B = ¬(A ∨ B)）にする。
    if tree["op"] == "and" and len(children) >= 2 and all(neg for _, neg in children):
        return _Node("or", [(c, False) for c, _ in children]), True
    return _Node(tree["op"], children), False


def _layout(node: _Node, row: int, top: float) -> int:
    """葉を上から順に並べ、ゲートは子の中ほどに置く。使った行数を返す。"""
    if node.kind in ("leaf", "const"):
        node.y = top + (row + 0.5) * ROW
        node.depth = 0
        return 1
    used = 0
    for child, _ in node.children:
        used += _layout(child, row + used, top)
    ys = [c.y for c, _ in node.children]
    node.y = (min(ys) + max(ys)) / 2
    node.depth = 1 + max(c.depth for c, _ in node.children)
    return used


def _value(node: _Node, values: dict[str, bool] | None) -> bool | None:
    if values is None:
        return None
    if node.kind == "const":
        node.value = node.value
        return node.value
    if node.kind == "leaf":
        node.value = bool(values.get(node.sig or "", False))
        return node.value
    vals = [(_value(c, values) or False) != neg for c, neg in node.children]
    node.value = all(vals) if node.kind == "and" else any(vals)
    return node.value


def _hexagon(svg: _Svg, x: float, y: float, w: float, label: str, *, dashed: bool, on: bool | None) -> None:
    h = 22
    pts = [(x, y), (x + 10, y - h / 2), (x + w - 10, y - h / 2), (x + w, y), (x + w - 10, y + h / 2), (x + 10, y + h / 2)]
    fill = HIGHLIGHT if on else "#fff"
    dash = ' stroke-dasharray="4 3"' if dashed else ""
    svg.add(f'<polygon points="{" ".join(f"{px:.1f},{py:.1f}" for px, py in pts)}" fill="{fill}" stroke="{LINE}" '
            f'stroke-width="1.2"{dash}/>')
    svg.text(x + w / 2, y, _fit(label, w - 18), size=11, anchor="middle")


def _connector(svg: _Svg, cx: float, y: float, letter: str, *, on: bool | None) -> None:
    fill = HIGHLIGHT if on else "#fff"
    svg.add(f'<circle cx="{cx:.1f}" cy="{y:.1f}" r="10" fill="{fill}" stroke="{LINE}" stroke-width="1.2"/>')
    svg.text(cx, y, letter, size=11, anchor="middle", weight="bold")


def _not_mark(svg: _Svg, x: float, y: float) -> None:
    """線の上の ⊠（NOT）。"""
    s = 7
    svg.add(f'<rect x="{x - s:.1f}" y="{y - s:.1f}" width="{2 * s}" height="{2 * s}" fill="#fff" stroke="{LINE}" stroke-width="1.2"/>')
    svg.add(f'<path d="M{x - s:.1f},{y - s:.1f}L{x + s:.1f},{y + s:.1f}M{x + s:.1f},{y - s:.1f}L{x - s:.1f},{y + s:.1f}" '
            f'stroke="{LINE}" stroke-width="1"/>')


def _fit(label: str, width: float, size: float = 11) -> str:
    out, used = "", 0.0
    for ch in label:
        w = size if ord(ch) > 0x2E80 else size * 0.6
        if used + w > width:
            return out + "…"
        out += ch
        used += w
    return out


def _draw_tree(svg: _Svg, d: LogicDiagram, node: _Node, x_left: float, x_root: float, depth_max: int,
               values: dict[str, bool] | None) -> None:
    """ゲートの縦棒の x は、根を x_root にして深さごとに GAP ずつ左へ。葉は x_left に揃える。"""
    def gate_x(n: _Node, level: int) -> float:
        return x_root - level * GAP

    def draw(n: _Node, level: int) -> None:
        if n.kind in ("leaf", "const"):
            return
        n.x = gate_x(n, level)
        ys = [c.y for c, _ in n.children]
        top, bottom = min(ys) - 8, max(ys) + 8
        svg.line(n.x, top, n.x, bottom, on=None, width=2.0)
        for child, neg in n.children:
            on = None if values is None else ((child.value or False) != neg)
            start = x_left + LEAF_W if child.kind in ("leaf", "const") else gate_x(child, level + 1) + 14
            if child.kind not in ("leaf", "const"):
                draw(child, level + 1)
            child_on = None if values is None else bool(child.value)
            if neg:
                mid = n.x - 20
                svg.line(start, child.y, mid - 7, child.y, on=child_on)
                svg.line(mid + 7, child.y, n.x, child.y, on=on)
                _not_mark(svg, mid, child.y)
            else:
                svg.line(start, child.y, n.x, child.y, on=on)
        # 出力の印: AND は小さい四角、OR は小さい丸（紙芝居の書き方）
        if n.kind == "and":
            svg.add(f'<rect x="{n.x:.1f}" y="{n.y - 6:.1f}" width="10" height="12" fill="#fff" stroke="{LINE}" stroke-width="1.4"/>')
        else:
            svg.add(f'<circle cx="{n.x + 6:.1f}" cy="{n.y:.1f}" r="6" fill="#fff" stroke="{LINE}" stroke-width="1.4"/>')

    def leaves(n: _Node) -> None:
        if n.kind == "leaf":
            s = d.signals[n.sig or ""]
            on = None if values is None else bool(n.value)
            if s.letter:
                svg.text(x_left + LEAF_W - 26, n.y, _fit(d.ref_label(s.id), LEAF_W - 40), size=11, anchor="end")
                _connector(svg, x_left + LEAF_W - 12, n.y, s.letter, on=on)
            else:
                _hexagon(svg, x_left, n.y, LEAF_W - 8, d.label(s.id), dashed=s.source not in ("", INPUT, "power"), on=on)
        elif n.kind == "const":
            svg.text(x_left + LEAF_W - 12, n.y, "1" if n.value else "0", size=12, anchor="end")
        for c, _ in n.children:
            leaves(c)

    draw(node, 0)
    leaves(node)


def _block_tree(d: LogicDiagram, expr: dict[str, Any] | None, inline: set[str]) -> tuple[_Node, bool]:
    return _to_nodes(expr or {"const": False}, d, inline)


def render_svg(d: LogicDiagram, values: dict[str, bool] | None = None, *, title: str | None = None) -> str:
    """論理図を SVG にする。values（信号 ID → ON/OFF）を与えると、成立している線をピンクで示す。"""
    inline = {sig for sig, s in d.signals.items() if s.kind == "logic" and not s.letter and not s.output}
    svg = _Svg()
    y = PAD
    width = 0.0
    heading = title if title is not None else d.title
    if heading:
        svg.text(PAD, y + 8, heading, size=14, weight="bold")
        y += 28
    for sig in d.block_order():
        s = d.signals[sig]
        if s.kind in ("input", "power"):
            # 入力に英字を付けた（何か所でも使う）: 六角形 → コネクタ
            on = None if values is None else bool(values.get(sig))
            yy = y + ROW / 2
            _hexagon(svg, PAD, yy, LEAF_W - 8, d.label(sig), dashed=s.source not in ("", INPUT, "power"), on=on)
            x_end = PAD + LEAF_W + 120
            svg.line(PAD + LEAF_W - 8, yy, x_end, yy, on=on)
            _connector(svg, x_end + 10, yy, s.letter or "?", on=on)
            svg.text(x_end + 26, yy - 7, _fit(d.ref_label(sig), SINK_W - 50), size=12)
            svg.text(x_end + 26, yy + 9, _fit(" / ".join(_short(x) for x in s.devices[:4]), SINK_W - 50, 10), size=10)
            right = x_end + SINK_W
            width = max(width, right + PAD)
            y += 40 + BLOCK_GAP
            svg.add(f'<line x1="{PAD}" y1="{y - BLOCK_GAP / 2:.1f}" x2="{right:.1f}" y2="{y - BLOCK_GAP / 2:.1f}" '
                    f'stroke="#ddd" stroke-width="1" stroke-dasharray="2 4"/>')
            continue
        if s.kind == "latch":
            parts = [(_block_tree(d, s.set_expr, inline), "S"), (_block_tree(d, s.reset_expr, inline), "R")]
        else:
            parts = [(_block_tree(d, s.expr, inline), "")]
        rows = 0
        for (node, _neg), _ in parts:
            rows += _layout(node, rows, y)
        depth_max = max(n.depth for (n, _), _ in parts)
        for (node, _neg), _ in parts:
            _value(node, values)
        x_left = PAD
        x_root = x_left + LEAF_W + 30 + max(depth_max - 1, 0) * GAP + 20
        x_sink = x_root + 70
        block_h = rows * ROW
        if s.kind == "latch":
            # S/R の箱
            box_top = min(n.y for (n, _), _ in parts) - 16
            box_bottom = max(n.y for (n, _), _ in parts) + 16
            for (node, neg), _port in parts:
                _draw_tree(svg, d, node, x_left, x_root, depth_max, values)
                start = x_left + LEAF_W if node.kind in ("leaf", "const") else x_root + 14
                on = None if values is None else ((node.value or False) != neg)
                if neg:
                    svg.line(start, node.y, x_sink - 27, node.y, on=None if values is None else bool(node.value))
                    _not_mark(svg, x_sink - 20, node.y)
                    svg.line(x_sink - 13, node.y, x_sink, node.y, on=on)
                else:
                    svg.line(start, node.y, x_sink, node.y, on=on)
            q_on = None if values is None else bool(values.get(sig))
            svg.add(f'<rect x="{x_sink:.1f}" y="{box_top:.1f}" width="22" height="{box_bottom - box_top:.1f}" '
                    f'fill="{HIGHLIGHT if q_on else "#fff"}" fill-opacity="0.35" stroke="{LINE}" stroke-width="1.4"/>')
            svg.line(x_sink, (box_top + box_bottom) / 2, x_sink + 22, (box_top + box_bottom) / 2, width=1.0)
            for (node, _), port in parts:
                svg.text(x_sink + 11, node.y, port, size=12, anchor="middle", weight="bold")
            out_y = (box_top + box_bottom) / 2
            out_x = x_sink + 22
            sink_note = "キープリレー"
        else:
            (node, neg), _ = parts[0]
            _draw_tree(svg, d, node, x_left, x_root, depth_max, values)
            start = x_left + LEAF_W if node.kind in ("leaf", "const") else x_root + 14
            out_y = node.y
            cur_on = None if values is None else bool(node.value)
            after_on = None if values is None else ((node.value or False) != neg)
            if neg:
                svg.line(start, out_y, x_sink - 27, out_y, on=cur_on)
                _not_mark(svg, x_sink - 20, out_y)
                svg.line(x_sink - 13, out_y, x_sink, out_y, on=after_on)
            else:
                svg.line(start, out_y, x_sink, out_y, on=after_on)
            out_x = x_sink
            sink_note = ""
            if s.kind == "timer":
                # 遅延の記号（D の形）と時間
                on = None if values is None else bool(values.get(sig))
                svg.add(f'<path d="M{x_sink:.1f},{out_y - 12:.1f}h10a12,12 0 0 1 0,24h-10z" fill="#fff" stroke="{LINE}" '
                        f'stroke-width="1.4"/>')
                svg.text(x_sink + 11, out_y + 22, _timer_label(s.timer), size=10, anchor="middle")
                out_x = x_sink + 22
                cur = on
                svg.line(out_x, out_y, out_x + 18, out_y, on=cur)
                out_x += 18
                sink_note = "タイマー"
        on = None if values is None else bool(values.get(sig))
        if s.output:
            bw, bh = 150, max(30, min(block_h - 8, 44))
            svg.line(out_x, out_y, out_x + 16, out_y, on=on)
            svg.add(f'<rect x="{out_x + 16:.1f}" y="{out_y - bh / 2:.1f}" width="{bw}" height="{bh:.1f}" '
                    f'fill="{HIGHLIGHT if on else "#fff"}" stroke="{LINE}" stroke-width="1.4"/>')
            svg.text(out_x + 16 + bw / 2, out_y - 7, _fit(d.label(sig), bw - 10, 12), size=12, anchor="middle", weight="bold")
            svg.text(out_x + 16 + bw / 2, out_y + 9, _fit(" / ".join(_short(x) for x in s.devices[:3]), bw - 10, 10),
                     size=10, anchor="middle")
            right = out_x + 16 + bw
            if s.letter:
                svg.line(right, out_y, right + 16, out_y, on=on)
                _connector(svg, right + 26, out_y, s.letter, on=on)
                right += 36
        else:
            svg.line(out_x, out_y, out_x + 20, out_y, on=on)
            _connector(svg, out_x + 30, out_y, s.letter or "?", on=on)
            svg.text(out_x + 46, out_y - 7, _fit(d.ref_label(sig), SINK_W - 50), size=12)
            note = " / ".join(_short(x) for x in s.devices[:4]) + (f"（{sink_note}）" if sink_note else "")
            svg.text(out_x + 46, out_y + 9, _fit(note, SINK_W - 50, 10), size=10)
            right = out_x + SINK_W
        width = max(width, right + PAD)
        y += max(block_h, 40) + BLOCK_GAP
        svg.add(f'<line x1="{PAD}" y1="{y - BLOCK_GAP / 2:.1f}" x2="{right:.1f}" y2="{y - BLOCK_GAP / 2:.1f}" '
                f'stroke="#ddd" stroke-width="1" stroke-dasharray="2 4"/>')
    height = y + PAD
    width = max(width, 480)
    head = (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {math.ceil(width)} {math.ceil(height)}" '
            f'width="{math.ceil(width)}" height="{math.ceil(height)}" font-family="{FONT}">'
            f'<rect width="100%" height="100%" fill="#fff"/>')
    return head + "".join(svg.parts) + "</svg>"


def formulas_text(d: LogicDiagram) -> str:
    """論理図を文字にしたもの（ブロックごとの式）。"""
    names = d.names()
    lines = []
    for sig in d.block_order():
        s = d.signals[sig]
        head = f"{s.letter + ' ' if s.letter else ''}{d.label(sig)}（{' / '.join(_short(x) for x in s.devices)}）"
        if s.kind == "latch":
            body = (f"S = {expr_text(s.set_expr or {'const': False}, names)} ; "
                    f"R = {expr_text(s.reset_expr or {'const': False}, names)}")
        elif s.kind == "timer":
            body = f"{_timer_label(s.timer)} 遅延（{expr_text(s.expr or {'const': False}, names)}）"
        elif s.kind in ("input", "power"):
            body = f"{'電源' if s.kind == 'power' else '入力'}「{d.label(sig)}」"
        else:
            body = expr_text(s.expr or {"const": False}, names)
        lines.append(f"{head} = {body}")
    return "\n".join(lines)


__all__ = ["LogicDiagram", "Signal", "build_logic", "compare_with_circuit", "default_targets", "evaluate", "expr_text",
           "formulas_text", "render_svg", "simulate", "solve", "to_text"]
