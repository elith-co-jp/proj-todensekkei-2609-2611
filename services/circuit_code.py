"""図面から組んだ回路を、読めて書き換えられる Python のコードにする（DD-0029）。

書き出したコードは ``CircuitCode`` の呼び出しを並べたもので、実行すると ``circuit_model.Circuit`` が組み上がる。
コードを書き換えて（接点を足す・器具番号を変える・抵抗を変える）組み直せば、図面に手を入れずに
「もしも」を試せる。元の図面の記号は ``source=(図面 ID, 記号 ref)`` で追える。

図面上の位置（端子の座標・配線の線）はコードに入れない。位置の正本はアノテーション（bundle.json）で、
コードは電気的なつながりと機器の対応（リレーのコイルと接点）の正本にする。
"""

from __future__ import annotations

import re
from typing import Any

from services.circuit_interop import part_list
from services.circuit_model import R_LOAD, Circuit, Element, _spice_name

KIND_CALL = {"a": "contact_a", "b": "contact_b"}


class CircuitCode:
    """回路を 1 行ずつ書くための入れ物。``build()`` で circuit_model.Circuit にする。"""

    def __init__(self, title: str = "") -> None:
        self.title = title
        self.elements: list[Element] = []
        self.refs: dict[str, Element] = {}
        self.extra_nodes: list[str] = []
        self.diagnostics: list[dict[str, Any]] = []
        self.timers: dict[str, dict[str, Any]] = {}
        self.latches: dict[str, dict[str, Any]] = {}

    # 素子 ---------------------------------------------------------------------------------------------
    def _add(self, ref: str, kind: str, a: str, b: str, *, source: tuple[int, str], label: str = "",
             device: str | None = None, resistance: float = R_LOAD) -> Element:
        if ref in self.refs:
            raise ValueError(f"参照記号 {ref} が重なっています")
        pid, symbol_ref = source
        prefix = "SW" if kind in ("a", "b") else "D" if kind == "d" else "R"
        element = Element(f"{prefix}_{pid}_{_spice_name(symbol_ref)}", kind, a, b, pid, symbol_ref, label, device, resistance)
        self.elements.append(element)
        self.refs[ref] = element
        return element

    def contact_a(self, ref: str, a: str, b: str, *, device: str, source: tuple[int, str], label: str = "",
                  instant: bool = False) -> Element:
        """a 接点（メーク）。器具番号 device のコイルが励磁（または外部入力が On）で閉じる。
        device がタイマーなら時間の経過で閉じる（instant=True の瞬時接点はコイルに従う）。"""
        e = self._add(ref, "a", a, b, source=source, label=label, device=device)
        e.instant = instant
        return e

    def contact_b(self, ref: str, a: str, b: str, *, device: str, source: tuple[int, str], label: str = "",
                  instant: bool = False) -> Element:
        """b 接点（ブレーク）。器具番号 device が動作すると開く。"""
        e = self._add(ref, "b", a, b, source=source, label=label, device=device)
        e.instant = instant
        return e

    def timer(self, device: str, mode: str, delay_s: float) -> None:
        """器具番号 device のリレーをタイマーにする。mode は "on_delay"（オンディレー）か "off_delay"（オフディレー）。"""
        if mode not in ("on_delay", "off_delay"):
            raise ValueError(f"タイマーの種類 {mode} は on_delay か off_delay")
        self.timers[device] = {"mode": mode, "delay_s": float(delay_s)}

    def coil(self, ref: str, a: str, b: str, *, device: str, source: tuple[int, str], label: str = "",
             resistance: float = R_LOAD, latch: str | None = None) -> Element:
        """リレーのコイル。両端の電圧で励磁し、同じ器具番号の接点を開閉する。
        latch="set" / "reset" はキープリレーのセット（動作）・リセット（復帰）のコイル（DD-0045）。"""
        e = self._add(ref, "load", a, b, source=source, label=label, device=device, resistance=resistance)
        if latch is not None:
            if latch not in ("set", "reset"):
                raise ValueError(f"キープリレーのコイル {latch} は set か reset")
            self.latches.setdefault(device, {"set": [], "reset": [], "priority": "reset"})[latch].append(e.name)
        return e

    def diode(self, ref: str, a: str, b: str, *, source: tuple[int, str], label: str = "", directed: bool = False) -> Element:
        """ダイオード。directed=True なら a がアノード、b がカソードで、逆向きには流れない。False なら両向きに流れる（DD-0037）。"""
        e = self._add(ref, "d", a, b, source=source, label=label)
        e.directed = directed
        return e

    def load(self, ref: str, a: str, b: str, *, source: tuple[int, str], label: str = "", resistance: float = R_LOAD) -> Element:
        """表示灯・電磁弁・ダイオードなど、ほかを開閉しない負荷。"""
        return self._add(ref, "load", a, b, source=source, label=label, resistance=resistance)

    # そのほか -----------------------------------------------------------------------------------------
    def nodes(self, *names: str) -> None:
        """素子がつながっていないノード（端子台だけの点など）も残すときに使う。"""
        self.extra_nodes += [n for n in names if n not in self.extra_nodes]

    def note(self, kind: str, message: str, **detail: Any) -> None:
        """図面から回路にできなかった箇所（確認が必要な箇所）。"""
        self.diagnostics.append({"kind": kind, "message": message, **detail})

    def build(self) -> Circuit:
        coils: dict[str, list[Element]] = {}
        for e in self.elements:
            if e.kind == "load" and e.device:
                coils.setdefault(e.device, []).append(e)
        inputs = sorted({e.device for e in self.elements if e.kind in ("a", "b") and e.device and e.device not in coils})
        nodes = sorted({n for e in self.elements for n in (e.a, e.b)} | set(self.extra_nodes))
        timers = {dev: dict(cfg) for dev, cfg in self.timers.items() if dev in coils}
        latches = {dev: {k: list(v) if isinstance(v, list) else v for k, v in cfg.items()}
                   for dev, cfg in self.latches.items() if dev in coils}
        return Circuit(list(self.elements), nodes, {}, coils, inputs, list(self.diagnostics), timers=timers, latches=latches)


def _src(e: Element) -> str:
    return f"({e.project_id}, {e.symbol_ref!r})"


def _comment(text: str) -> str:
    return re.sub(r"[\r\n]+", " ", text or "")


def to_python(circuit: Circuit, *, pages: list[dict[str, Any]] | None = None, title: str = "") -> str:
    """回路を実行できる Python のコードにする。実行すると変数 ``circuit`` に circuit_model.Circuit ができる。"""
    parts = part_list(circuit, pages)
    sheet = {p["id"]: p.get("sheet_no") or p.get("name") or str(p["id"]) for p in pages or []}
    lines = [
        f'"""TodenYOLO の回路コード: {_comment(title) or "図面のアノテーションから組んだ回路"}',
        "",
        "ノード名: P / P_xxx は電源の正側（PER 母線）、0 は負側（NER 母線）、N001… は配線でつながった端子の集まり。",
        "source=(図面 ID, 記号 ref) で元の図面の記号に戻れる。このファイルを書き換えて実行し直すと、",
        "書き換えた回路でシミュレーションできる（例: python このファイル.py）。",
        '"""',
        "",
        "from services.circuit_code import CircuitCode",
        "",
        f"c = CircuitCode({title!r})",
    ]
    used = {n for e in circuit.elements for n in (e.a, e.b)}
    current_page = None
    for p in parts:
        e = p.element
        if e.project_id != current_page:
            current_page = e.project_id
            lines += ["", f"# ---- 図面 {sheet.get(e.project_id, e.project_id)}（ID {e.project_id}）"]
        args = f"{p.ref!r}, {e.a!r}, {e.b!r}"
        label = f", label={e.label!r}" if e.label else ""
        if e.kind in ("a", "b"):
            instant = ", instant=True" if e.instant else ""
            lines.append(f"c.{KIND_CALL[e.kind]}({args}, device={e.device!r}, source={_src(e)}{label}{instant})")
        elif e.kind == "d":
            directed = ", directed=True" if e.directed else ""
            lines.append(f"c.diode({args}, source={_src(e)}{label}{directed})")
        elif e.device and e.device in circuit.coils:
            res = "" if e.resistance == R_LOAD else f", resistance={e.resistance:g}"
            role = next((r for r in ("set", "reset") if e.name in circuit.latches.get(e.device, {}).get(r, [])), None)
            latch = f", latch={role!r}" if role else ""
            lines.append(f"c.coil({args}, device={e.device!r}, source={_src(e)}{label}{res}{latch})")
        else:
            res = "" if e.resistance == R_LOAD else f", resistance={e.resistance:g}"
            lines.append(f"c.load({args}, source={_src(e)}{label}{res})")
    if circuit.timers:
        lines += ["", "# ---- タイマー（オンディレー: 励磁が続いて delay_s 秒で接点が動く。オフディレー: 切れて delay_s 秒で戻る）"]
        lines += [f"c.timer({dev!r}, {cfg['mode']!r}, {cfg['delay_s']:g})" for dev, cfg in sorted(circuit.timers.items())]
    extra = [n for n in circuit.nodes if n not in used]
    if extra:
        lines += ["", "# 素子のつながっていないノード", f"c.nodes({', '.join(repr(n) for n in extra)})"]
    if circuit.diagnostics:
        lines += ["", "# ---- 確認が必要な箇所（図面から回路にできなかったところ）"]
        for d in circuit.diagnostics:
            detail = "".join(f", {k}={v!r}" for k, v in d.items() if k not in ("kind", "message"))
            lines.append(f"c.note({d.get('kind')!r}, {d.get('message', '')!r}{detail})")
    lines += [
        "",
        "circuit = c.build()",
        "",
        'if __name__ == "__main__":',
        "    from services.circuit_model import simulate",
        "",
        "    result = simulate(circuit, inputs={})  # 外部入力を On にするなら {'PB1': True}",
        "    for device, state in result['coils'].items():",
        "        print(device, '励磁' if state['energized'] else '無励磁')",
        "",
    ]
    return "\n".join(lines)


def from_python(code: str) -> Circuit:
    """to_python で書いた（または書き換えた）コードを実行して回路にする。

    任意のコードを実行するため、利用者が手元で書いたコード専用。API で外から受け取ったコードには使わない。
    """
    namespace: dict[str, Any] = {"__name__": "todenyolo_circuit_code"}
    exec(compile(code, "<circuit code>", "exec"), namespace)  # noqa: S102  手元のコード専用（上の注意）
    circuit = namespace.get("circuit")
    if not isinstance(circuit, Circuit):
        raise TypeError("コードに circuit = c.build() がありません")
    return circuit
