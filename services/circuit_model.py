"""図面のアノテーションを回路にして、電気の On/Off をシミュレーションする（DD-0009）。DB を使わない。

- ノード: 配線でつながった端子の集まり。コネクタ・分岐点・端子・母線端は通り抜けの点で、端子をすべて同じノードにする。
  母線（PER… / NER…）は図面をまたいで 1 つのノード（P と 0）にする。
- 素子: a 接点・b 接点は開閉するスイッチ、リレーコイル・電磁弁・表示灯などは負荷の抵抗。電源は P と 0 の間の直流。
- 接点は、器具番号と機器名（例「33HB FV2306」）が同じコイルで開閉する。選んだ図面にコイルが無ければ外部入力にする。
- シミュレーション: 接点の開閉を決めて回路を解き（SPICE）、コイルの電圧から励磁を決め、接点を開閉し直す。状態が
  変わらなくなるまで繰り返す。前の状態から始めるので、自己保持の回路は押した後も保持される。
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from analysis.wiring import classes

PASS_CLASSES = classes.PASS_THROUGH
CONTACT_CLASSES = {"contact_a": "a", "contact_b": "b"}
LOAD_CLASSES = frozenset({"relay_coil", "solenoid", "lamp", "diode"})
SUPPLY_V = 110.0
R_ON, R_OFF = 0.01, 1e9
R_LOAD = 1000.0
R_LEAK = 1e9
# コイルの両端の電圧が電源の 7 割以上なら励磁とする（直列の負荷や接点の抵抗で少し下がっても動く）。
PICKUP_RATIO = 0.7
MAX_STEPS = 30
SHORT_CURRENT_A = 10.0
GROUND = "0"
SUPPLY = "P"
# 母線の名前（PER…/NER…）は母線の線のすぐ上に書かれる（150 dpi で 10 px 前後下が線）。
BUS_BELOW_TEXT = 12
BUS_TOLERANCE = 12
# 同じ記号の端子どうしを結ぶ配線（DD-0022）。接点・コイルなどの機器では素子そのもの、カード（2B-2RSAM など）の列の
# 上の黒丸と下の黒丸（+ と -、約 60 px の縦の線）もカードの入出力の素子で、導線にしない。盤の枠（2RAXP・2RMMP）の中の
# 線（枠を横切る 30 px 未満の線、母線に沿った横の線）は導線。
ELEMENT_LINK_MIN_PX = 30.0
ELEMENT_LINK_ALIGN_PX = 6.0

Key = tuple[int, str, str]  # (図面 ID, 記号 ref, 端子 ref)


@dataclass
class Element:
    name: str
    kind: str              # "a" / "b"（接点）, "load"（コイル・負荷）
    a: str
    b: str
    project_id: int
    symbol_ref: str
    label: str
    device: str | None = None   # コイル・接点の器具番号（開閉の対応づけ）
    resistance: float = R_LOAD  # 負荷の抵抗。属性 coil_resistance_ohm / resistance_ohm があればその値（DD-0010）


@dataclass
class Circuit:
    elements: list[Element]
    nodes: list[str]
    node_of: dict[Key, str]
    coils: dict[str, list[Element]]          # 器具番号 → コイル
    inputs: list[str]                        # 外部入力にした器具番号（コイルが選んだ図面に無い接点）
    diagnostics: list[dict[str, Any]]
    wires: list[dict[str, Any]] = field(default_factory=list)


def device_key(label: str | None) -> str | None:
    """「33HB FV2306 (ER21236)」「83D-2 ESD(ER21271)」→ 器具番号と機器名。括弧の参照先シートは空白が無くても除く。"""
    tokens = [t for t in re.split(r"\s+", re.sub(r"[(（][^)）]*[)）]?", " ", label or "").strip()) if t]
    return " ".join(tokens[:2]) if tokens else None


# OCR が取り違えやすい字（O と 0、I と 1、B と 8）。器具番号はこの違いだけの別の器具を使わない。
_CONFUSABLE = str.maketrans({"O": "0", "I": "1", "B": "8"})


def canonical_device(device: str) -> str:
    """器具番号の照合用の形。「20VO HV2111」と「20V0 HV2111」を同じ器具とみなす。"""
    return device.upper().translate(_CONFUSABLE)


def _bus_side(text: str | None) -> str | None:
    """母線の名前からノード名。PER2100100 → P_2100100（電源の正側）、NER… → 0（負側は共通の基準）。"""
    compact = (text or "").replace(" ", "").upper()
    if compact.startswith("PER"):
        return f"{SUPPLY}_{compact[3:]}" if compact[3:] else SUPPLY
    if compact.startswith("NER"):
        return GROUND
    return None


def _spice_name(name: str) -> str:
    return re.sub(r"[^0-9A-Za-z_]", "_", name)


def _load_resistance(attributes: dict[str, Any]) -> float:
    for key in ("coil_resistance_ohm", "resistance_ohm"):
        try:
            value = float(attributes.get(key))
        except (TypeError, ValueError):
            continue
        if value > 0:
            return value
    return R_LOAD


def joins_inside_symbol(symbol: dict[str, Any], from_ref: str | None, to_ref: str | None, width: float, height: float) -> bool:
    """同じ記号の 2 つの端子を結ぶ配線が導線か（DD-0022）。通り抜けの点は導線、機器（接点・コイルなど）は素子、
    「その他」（カード・盤の枠）は縦に長い線（カードの + と -）だけ素子で、ほかは導線。"""
    if symbol["class_key"] in PASS_CLASSES:
        return True
    if symbol["class_key"] != "other" or width <= 0 or height <= 0:
        return False
    points = {t["ref"]: (t["tx"] * width, t["ty"] * height) for t in symbol.get("terminals") or [] if t.get("tx") is not None}
    a, b = points.get(from_ref or ""), points.get(to_ref or "")
    if a is None or b is None:
        return False
    return not (abs(a[0] - b[0]) <= ELEMENT_LINK_ALIGN_PX and abs(a[1] - b[1]) >= ELEMENT_LINK_MIN_PX)


def build_circuit(pages: list[dict[str, Any]], bus_texts: dict[int, list[dict[str, Any]]] | None = None) -> Circuit:
    """pages は serialize_project の形。bus_texts は図面 ID ごとの構造解析の文字（PER/NER の位置で母線を決める）。"""
    parent: dict[Any, Any] = {}

    def find(x: Any) -> Any:
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(x: Any, y: Any) -> None:
        parent[find(x)] = find(y)

    diagnostics: list[dict[str, Any]] = []
    symbols: dict[tuple[int, str], dict[str, Any]] = {}
    wires = []
    for page in pages:
        pid = page["id"]
        for s in page["symbols"]:
            symbols[(pid, s["ref"])] = s
            terms = [(pid, s["ref"], t["ref"]) for t in s.get("terminals") or []]
            for k in terms:
                find(k)
            if s["class_key"] in PASS_CLASSES:
                for k in terms[1:]:
                    union(terms[0], k)
        for c in page["connections"]:
            if c.get("kind", "wire") != "wire":
                continue
            ends = []
            for side in ("from", "to"):
                sym = symbols.get((pid, c[f"{side}_symbol_ref"]))
                tref = c.get(f"{side}_terminal_ref")
                if sym is None:
                    break
                if tref is None:
                    terms = sym.get("terminals") or []
                    if len(terms) == 1 or sym["class_key"] in PASS_CLASSES and terms:
                        tref = terms[0]["ref"]
                    else:
                        diagnostics.append({"kind": "wire_without_terminal", "project_id": pid,
                                            "symbol_ref": sym["ref"], "message": "端子を指定していない配線は回路に含めません"})
                        break
                ends.append((pid, sym["ref"], tref))
            if len(ends) == 2:
                # 接点・コイルの両端やカードの + と - を結ぶ線は、エディタで素子そのものを表す（人の登録も同じ）。導線にはしない。
                # 盤の枠の中の線は導線（joins_inside_symbol）。
                if ends[0][1] == ends[1][1] and not joins_inside_symbol(
                        symbols[(pid, ends[0][1])], ends[0][2], ends[1][2], page.get("image_width") or 0, page.get("image_height") or 0):
                    continue
                union(ends[0], ends[1])
                wires.append({"project_id": pid, "from": ends[0], "to": ends[1]})

    # 母線: 母線端の記号の位置に近い PER/NER の文字で決める。文字が無ければ上の母線端を P、下を 0 と推定する。
    bus_root: dict[Any, str] = {}
    for page in pages:
        pid = page["id"]
        ends = [s for s in page["symbols"] if s["class_key"] == "power_bus" and s.get("terminals")]
        texts = [t for t in (bus_texts or {}).get(pid, []) if _bus_side(t.get("text"))]
        if not ends and texts:
            # 母線端の記号が無ければ、PER/NER の文字のすぐ下の高さに並ぶ分岐点を母線の上の点とみなす。
            # 左右に並ぶ回路では、母線の名前はそれぞれの左端に書かれる。名前の左端から、同じ高さの次の名前の手前までが範囲。
            for text in texts:
                line_y = text["box"][3] + BUS_BELOW_TEXT
                right = min((o["box"][0] for o in texts if o is not text and o["box"][0] > text["box"][0]
                             and abs(o["box"][3] - text["box"][3]) <= BUS_TOLERANCE), default=float("inf"))
                dots = [s for s in page["symbols"] if s["class_key"] == "junction" and s.get("terminals")
                        and abs(s["cy"] * page["image_height"] - line_y) <= BUS_TOLERANCE
                        and text["box"][0] - BUS_TOLERANCE <= s["cx"] * page["image_width"] < right - BUS_TOLERANCE]
                for s in dots:
                    bus_root[find((pid, s["ref"], s["terminals"][0]["ref"]))] = _bus_side(text["text"])
                if dots:
                    diagnostics.append({"kind": "bus_guessed", "project_id": pid,
                                        "message": f"母線端が無いため、{text['text']} の下に並ぶ分岐点 {len(dots)} 個を母線としました"})
        for s in ends:
            root = find((pid, s["ref"], s["terminals"][0]["ref"]))
            side = None
            if texts:
                x, y = s["cx"] * page["image_width"], s["cy"] * page["image_height"]
                near = min(texts, key=lambda t: abs((t["box"][1] + t["box"][3]) / 2 - y) + 0.2 * abs(t["box"][0] - x))
                side = _bus_side(near["text"])
            elif len(ends) == 2:
                side = SUPPLY if s["cy"] == min(e["cy"] for e in ends) else GROUND
                diagnostics.append({"kind": "bus_guessed", "project_id": pid, "symbol_ref": s["ref"],
                                    "message": "PER/NER の文字が無いため、上の母線端を P、下を N と推定しました"})
            if side is None:
                diagnostics.append({"kind": "bus_unknown", "project_id": pid, "symbol_ref": s["ref"],
                                    "message": "母線端が PER か NER か分かりません"})
                continue
            if bus_root.get(root, side) != side:
                diagnostics.append({"kind": "bus_conflict", "project_id": pid, "symbol_ref": s["ref"],
                                    "message": "PER と NER が配線でつながっています"})
            bus_root[root] = side
    if not bus_root:
        diagnostics.append({"kind": "no_supply", "message": "母線端（PER/NER）が見つからないため電源を置けません"})

    names: dict[Any, str] = {}
    count = 0
    node_of: dict[Key, str] = {}
    for key in list(parent):
        if not isinstance(key, tuple) or len(key) != 3:
            continue
        root = find(key)
        if root not in names:
            if root in bus_root:
                names[root] = bus_root[root]
            else:
                count += 1
                names[root] = f"N{count:03d}"
        node_of[key] = names[root]

    elements: list[Element] = []
    coils: dict[str, list[Element]] = defaultdict(list)
    # 照合用の形 → 最初に見つけたコイルの器具番号。O と 0 だけが違うコイル・接点を同じリレーにする。
    coil_by_canonical: dict[str, str] = {}
    for (pid, ref), s in sorted(symbols.items(), key=lambda kv: (kv[0][0], kv[0][1])):
        cls, terms = s["class_key"], s.get("terminals") or []
        kind = CONTACT_CLASSES.get(cls) or ("load" if cls in LOAD_CLASSES else None)
        if kind is None:
            continue
        if len(terms) != 2:
            diagnostics.append({"kind": "terminals_missing", "project_id": pid, "symbol_ref": ref, "label": s.get("label"),
                                "message": f"{s.get('class_label') or cls}の端子が {len(terms)} 個のため回路に含めません（2 個必要）"})
            continue
        a, b = node_of[(pid, ref, terms[0]["ref"])], node_of[(pid, ref, terms[1]["ref"])]
        element = Element(f"{'SW' if kind in ('a', 'b') else 'R'}_{pid}_{_spice_name(ref)}", kind, a, b, pid, ref,
                          s.get("label") or "", device_key(s.get("label")) if cls in CONTACT_CLASSES or cls == "relay_coil" else None,
                          _load_resistance(s.get("attributes") or {}))
        elements.append(element)
        if cls == "relay_coil" and element.device:
            element.device = coil_by_canonical.setdefault(canonical_device(element.device), element.device)
            coils[element.device].append(element)

    # ラベルに機器名が無い接点（「33LA」）は、器具番号が同じコイルが 1 つだけなら、そのコイルで開閉する。
    by_number: dict[str, list[str]] = defaultdict(list)
    for dev in coils:
        by_number[canonical_device(dev.split()[0])].append(dev)
    for e in elements:
        if e.kind not in ("a", "b") or not e.device or e.device in coils:
            continue
        if canonical_device(e.device) in coil_by_canonical:
            e.device = coil_by_canonical[canonical_device(e.device)]
        elif len(by_number.get(canonical_device(e.device.split()[0]), [])) == 1:
            e.device = by_number[canonical_device(e.device.split()[0])][0]
    page_names = {p["id"]: p.get("sheet_no") or p.get("name") or str(p["id"]) for p in pages}
    inputs: set[str] = set()
    for e in elements:
        if e.kind not in ("a", "b"):
            continue
        if e.device is None:
            e.device = f"{page_names.get(e.project_id, e.project_id)} {e.symbol_ref}（器具番号なし）"
            diagnostics.append({"kind": "contact_unlabeled", "project_id": e.project_id, "symbol_ref": e.symbol_ref,
                                "message": "器具番号の無い接点は外部入力にしました"})
        if e.device not in coils:
            inputs.add(e.device)
    for dev in coils:
        if not any(e.kind in ("a", "b") and e.device == dev for e in elements):
            diagnostics.append({"kind": "coil_without_contacts", "device": dev,
                                "message": f"コイル {dev} の接点が選んだ図面にありません"})
    nodes = sorted(set(node_of.values()))
    return Circuit(elements, nodes, node_of, dict(coils), sorted(inputs), diagnostics, wires)


def netlist(circuit: Circuit, closed: dict[str, bool], title: str = "TodenYOLO circuit") -> str:
    """開閉を決めた回路の SPICE ネットリスト（LTspice / ngspice）。closed は接点の素子名 → 閉。"""
    supplies = [n for n in circuit.nodes if n == SUPPLY or n.startswith(f"{SUPPLY}_")] or [SUPPLY]
    lines = [f"* {title}"] + [f"V_{_spice_name(n)} {_spice_name(n)} {GROUND} DC {SUPPLY_V:g}" for n in supplies]
    for e in circuit.elements:
        comment = f"* {e.label} ({e.project_id}:{e.symbol_ref})"
        if e.kind in ("a", "b"):
            # 開閉を決めた接点は抵抗にする（素子名の先頭の R が抵抗を表す）。
            lines += [comment, f"R{e.name} {_spice_name(e.a)} {_spice_name(e.b)} {R_ON if closed.get(e.name) else R_OFF:g}"]
        else:
            lines += [comment, f"{e.name} {_spice_name(e.a)} {_spice_name(e.b)} {e.resistance:g}"]
    lines += [f"R_LEAK_{_spice_name(n)} {_spice_name(n)} 0 {R_LEAK:g}" for n in circuit.nodes if n != GROUND]
    lines += [".op", ".end"]
    return "\n".join(lines) + "\n"


def _solve_mna(text: str) -> tuple[dict[str, float], float]:
    """抵抗と直流電源 1 つだけのネットリストを節点解析で解く（ngspice が無い環境用）。"""
    resistors, sources = [], []
    for line in text.splitlines():
        parts = line.split()
        if not parts or parts[0].startswith(("*", ".")):
            continue
        if parts[0].upper().startswith("V"):
            sources.append((parts[1], parts[2], float(parts[-1])))
        else:
            resistors.append((parts[1], parts[2], float(parts[3])))
    names = sorted(({n for r in resistors for n in r[:2]} | {n for s in sources for n in s[:2]}) - {GROUND})
    index = {n: i for i, n in enumerate(names)}
    size = len(names) + len(sources)
    g = np.zeros((size, size))
    rhs = np.zeros(size)
    for a, b, r in resistors:
        for x, y in ((a, b), (b, a)):
            if x != GROUND:
                g[index[x], index[x]] += 1 / r
                if y != GROUND:
                    g[index[x], index[y]] -= 1 / r
    for j, (plus, minus, volts) in enumerate(sources):
        k = len(names) + j
        for node, sign in ((plus, 1), (minus, -1)):
            if node != GROUND:
                g[index[node], k] += sign
                g[k, index[node]] += sign
        rhs[k] = volts
    solution = np.linalg.solve(g, rhs)
    current = float(sum(abs(solution[len(names) + j]) for j in range(len(sources))))
    return {n: float(solution[i]) for n, i in index.items()} | {GROUND: 0.0}, current


def ngspice_path() -> str | None:
    return os.environ.get("SEQANNO_NGSPICE") or shutil.which("ngspice")


def _solve_ngspice(text: str, binary: str) -> tuple[dict[str, float], float]:
    control = text.replace(".op\n.end", ".control\nop\nprint all\nprint all#branch\nquit\n.endc\n.end")
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "circuit.cir"
        path.write_text(control, encoding="ascii", errors="replace")
        out = subprocess.run([binary, "-b", str(path)], capture_output=True, text=True, timeout=60).stdout
    values: dict[str, float] = {GROUND: 0.0}
    current = 0.0
    for line in out.splitlines():
        m = re.match(r"\s*([\w()#.]+)\s*=\s*([-+0-9.eE]+)", line)
        if not m:
            continue
        name, value = m.group(1).lower(), float(m.group(2))
        if name.startswith("i(") or name.endswith("#branch"):
            current += abs(value)
        else:
            values[name.removeprefix("v(").removesuffix(")")] = value
    return values, current


def solve(text: str) -> tuple[dict[str, float], float, str]:
    """ノードの電圧（小文字の SPICE 名）、電源の電流、使った解き方。"""
    binary = ngspice_path()
    if binary:
        values, current = _solve_ngspice(text, binary)
        if len(values) > 1:
            return values, current, "ngspice"
    values, current = _solve_mna(text)
    return {k.lower(): v for k, v in values.items()}, current, "builtin"


def _volt(volts: dict[str, float], node: str) -> float:
    return volts.get(_spice_name(node).lower(), 0.0)


def simulate(circuit: Circuit, inputs: dict[str, bool], energized: dict[str, bool] | None = None) -> dict[str, Any]:
    """外部入力の On/Off と前の励磁状態から、状態が変わらなくなるまで解く。"""
    state = {dev: bool((energized or {}).get(dev)) for dev in circuit.coils}
    seen: list[dict[str, bool]] = []
    warnings: list[str] = []
    engine = "builtin"

    def is_on(dev: str | None) -> bool:
        return state[dev] if dev in circuit.coils else bool(inputs.get(dev))

    steps = 0
    for steps in range(1, MAX_STEPS + 1):  # noqa: B007  回数を結果に出す
        closed = {e.name: is_on(e.device) == (e.kind == "a") for e in circuit.elements if e.kind in ("a", "b")}
        volts, current, engine = solve(netlist(circuit, closed))
        coil_v = {dev: max(abs(_volt(volts, e.a) - _volt(volts, e.b)) for e in es) for dev, es in circuit.coils.items()}
        new = {dev: coil_v[dev] >= PICKUP_RATIO * SUPPLY_V for dev in circuit.coils}
        if new == state:
            break
        if new in seen:
            warnings.append("リレーの状態が繰り返し変わり、落ち着きません（発振する回路の可能性）")
            state = new
            break
        seen.append(state)
        state = new
    if abs(current) > SHORT_CURRENT_A:
        warnings.append(f"電源に {abs(current):.0f} A 流れています。P と N の短絡の可能性があります")
    return {
        "engine": engine, "steps": steps, "warnings": warnings, "supply_current": current,
        "coils": {dev: {"energized": state[dev], "voltage": round(coil_v[dev], 2)} for dev in circuit.coils},
        "contacts": [{"project_id": e.project_id, "symbol_ref": e.symbol_ref, "device": e.device, "kind": e.kind,
                      "closed": closed[e.name]} for e in circuit.elements if e.kind in ("a", "b")],
        "nodes": {n: round(_volt(volts, n), 2) for n in circuit.nodes},
        "netlist": netlist(circuit, closed),
    }


def export_netlist(circuit: Circuit, inputs: dict[str, bool] | None = None) -> str:
    """LTspice・ngspice でそのまま動かせるネットリスト。接点はコイルの電圧で開閉するスイッチ、外部入力は .param。"""
    supplies = [n for n in circuit.nodes if n == SUPPLY or n.startswith(f"{SUPPLY}_")] or [SUPPLY]
    lines = [
        "* TodenYOLO: 図面のアノテーションから組んだ回路。外部入力の .param を 0/1 で切り替えて .op で解く",
        f".model SW_A SW(Ron={R_ON:g} Roff={R_OFF:g} Vt=0.5 Vh=0.1)",
        f".model SW_B SW(Ron={R_OFF:g} Roff={R_ON:g} Vt=0.5 Vh=0.1)",
        *[f"V_{_spice_name(n)} {_spice_name(n)} {GROUND} DC {SUPPLY_V:g}" for n in supplies],
    ]
    control: dict[str, str] = {}
    for dev, coils in circuit.coils.items():
        coil = coils[0]
        name = f"CTL_{_spice_name(dev)}"
        # コイルの電圧が電源の PICKUP_RATIO 以上で 1。abs と比較は LTspice と ngspice のどちらでも使える。
        lines.append(f"B_{name} {name} 0 V=(abs(V({_spice_name(coil.a)})-V({_spice_name(coil.b)}))>{PICKUP_RATIO * SUPPLY_V:g})")
        control[dev] = name
    for dev in circuit.inputs:
        name = f"CTL_{_spice_name(dev)}"
        lines += [f".param S_{_spice_name(dev)}={int(bool((inputs or {}).get(dev)))}",
                  f"V_{name} {name} 0 DC {{S_{_spice_name(dev)}}}"]
        control[dev] = name
    for e in circuit.elements:
        lines.append(f"* {e.label} ({e.project_id}:{e.symbol_ref})")
        if e.kind in ("a", "b"):
            lines.append(f"S{e.name} {_spice_name(e.a)} {_spice_name(e.b)} {control[e.device]} 0 SW_{e.kind.upper()}")
        else:
            lines.append(f"{e.name} {_spice_name(e.a)} {_spice_name(e.b)} {e.resistance:g}")
    lines += [f"R_LEAK_{_spice_name(n)} {_spice_name(n)} 0 {R_LEAK:g}" for n in circuit.nodes if n != GROUND]
    lines += [".op", ".end"]
    return "\n".join(lines) + "\n"
