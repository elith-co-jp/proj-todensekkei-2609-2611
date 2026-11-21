"""図面から組んだ回路（circuit_model.Circuit）を Lcapy・SKiDL の表現に変換する（DD-0028）。

- Lcapy: 回路解析（記号計算の節点解析）と回路図の描画のライブラリ。接点は ``SW … no/nc``（開閉の状態を決めたときは
  導線 ``W`` と開放 ``O``）、コイル・表示灯などの負荷は抵抗 ``R``、母線は直流電源 ``V`` にする。
- SKiDL: 部品と端子とネットを Python で書くネットリスト生成のライブラリ。記号を部品（端子 1・2 の受動部品）、
  ノードをネットにして、KiCad のネットリストを出す。KiCad の部品ライブラリは使わない（部品の型をその場で作る）。

どちらのライブラリも必須の依存にしない。文字列（Lcapy のネットリスト、SKiDL のスクリプト）はライブラリ無しで作り、
ライブラリのオブジェクトが欲しいときだけ遅延 import する（Windows exe や Docker に入れなくてよい）。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from services.circuit_model import GROUND, R_LEAK, SUPPLY, SUPPLY_V, Circuit, Element

# 部品の参照記号の先頭の文字（IEC 81346 の機器の分類に寄せる）。K: リレー、S: 接点・スイッチ、H: 表示灯、
# Y: 電磁弁・ソレノイド、D: ダイオード、R: そのほかの負荷。
# 盤の配線図には基板の部品の形が無い。KiCad の「部品の形が無い」エラーを避ける仮の名前。
FOOTPRINT = "TodenYOLO:NoFootprint"
REF_PREFIX = {"relay_coil": "K", "contact": "S", "lamp": "H", "solenoid": "Y", "diode": "D"}


@dataclass(frozen=True)
class PartInfo:
    """変換後の部品 1 つ。ref は Lcapy・SKiDL で共通の名前（例 K1, S3）。"""

    ref: str
    element: Element
    class_key: str


def _safe_node(name: str) -> str:
    """Lcapy・SKiDL のどちらでも使えるノード名（英数字と _）。0 は基準（N 母線）。"""
    return name if name == GROUND else re.sub(r"[^0-9A-Za-z_]", "_", name)


def _comment(text: str) -> str:
    return re.sub(r"[\r\n]+", " ", text or "")


def part_list(circuit: Circuit, pages: list[dict[str, Any]] | None = None) -> list[PartInfo]:
    """素子に通し番号の参照記号を付ける。pages があれば記号のクラス（表示灯・電磁弁など）で先頭の文字を変える。"""
    class_of = {(p["id"], s["ref"]): s["class_key"] for p in pages or [] for s in p["symbols"]}
    counts: dict[str, int] = {}
    parts = []
    for e in circuit.elements:
        cls = class_of.get((e.project_id, e.symbol_ref)) or ("contact" if e.kind in ("a", "b") else
                                                             "relay_coil" if e.device else "load")
        prefix = "S" if e.kind in ("a", "b") else REF_PREFIX.get(cls, "R")
        counts[prefix] = counts.get(prefix, 0) + 1
        parts.append(PartInfo(f"{prefix}{counts[prefix]}", e, cls))
    return parts


def _supplies(circuit: Circuit) -> list[str]:
    return [n for n in circuit.nodes if n == SUPPLY or n.startswith(f"{SUPPLY}_")]


# --------------------------------------------------------------------------------------------- Lcapy


def lcapy_netlist(circuit: Circuit, closed: dict[str, bool] | None = None, *, pages: list[dict[str, Any]] | None = None,
                  leak: bool = True) -> str:
    """Lcapy のネットリスト。

    closed が None なら接点を ``SW n1 n2 no``（a 接点）/ ``nc``（b 接点）で書く（回路図・トポロジー用。Lcapy で解くには
    ``replace_switches_before(0)`` で無励磁の状態にする）。closed（素子名 → 閉）を渡すと、閉じた接点を導線 ``W``、
    開いた接点を開放 ``O`` にして、そのまま直流解析できる形にする。
    leak は浮いたノード（開いた接点の先など）に大きな抵抗を付けて、Lcapy の節点解析の行列を正則にする。
    """
    parts = part_list(circuit, pages)
    lines = ["# TodenYOLO: 図面のアノテーションから組んだ回路（Lcapy ネットリスト）"]
    for i, n in enumerate(_supplies(circuit), start=1):
        lines.append(f"V{i} {_safe_node(n)} 0 dc {SUPPLY_V:g}")
    for p in parts:
        e = p.element
        a, b = _safe_node(e.a), _safe_node(e.b)
        lines.append(f"# {p.ref}: {_comment(e.label)} ({e.project_id}:{e.symbol_ref}) device={_comment(e.device or '')}")
        if e.kind in ("a", "b"):
            if closed is None:
                lines.append(f"SW{p.ref[1:]} {a} {b} {'no' if e.kind == 'a' else 'nc'}")
            elif closed.get(e.name):
                lines.append(f"W {a} {b}")
            else:
                lines.append(f"O {a} {b}")
        elif e.kind == "d":
            # ダイオードは導通していれば導線、止めていれば開放（Lcapy では理想ダイオードを解けないため。DD-0037）。
            lines.append(f"O {a} {b}" if closed is not None and closed.get(e.name) is False else f"W {a} {b}")
        else:
            lines.append(f"R{p.ref} {a} {b} {e.resistance:g}")
    if leak:
        floating = [n for n in circuit.nodes if n != GROUND]
        lines += [f"Rleak{i} {_safe_node(n)} 0 {R_LEAK:g}" for i, n in enumerate(floating, start=1)]
    return "\n".join(lines) + "\n"


def to_lcapy(circuit: Circuit, closed: dict[str, bool] | None = None, **kwargs: Any):
    """lcapy.Circuit を返す（lcapy が必要）。"""
    from lcapy import Circuit as LcapyCircuit

    return LcapyCircuit(lcapy_netlist(circuit, closed, **kwargs))


def contact_states(circuit: Circuit, inputs: dict[str, bool], energized: dict[str, bool] | None = None) -> dict[str, bool]:
    """外部入力とコイルの励磁から接点の開閉（素子名 → 閉）。circuit_model.simulate の 1 ステップと同じ決め方。"""
    def is_on(dev: str | None) -> bool:
        return bool((energized or {}).get(dev)) if dev in circuit.coils else bool(inputs.get(dev))

    return {e.name: is_on(e.device) == (e.kind == "a") for e in circuit.elements if e.kind in ("a", "b")}


def solve_lcapy(circuit: Circuit, closed: dict[str, bool]) -> dict[str, float]:
    """開閉を決めた回路を Lcapy で解いたノードの電圧（circuit.nodes の名前 → V）。内蔵の解き方との照合用。

    Lcapy は記号計算（sympy）のため、ノードが数百の図面では内蔵の数値解法や ngspice よりずっと遅い。
    """
    cct = to_lcapy(circuit, closed)
    volts = {}
    for n in circuit.nodes:
        volts[n] = 0.0 if n == GROUND else float(cct[_safe_node(n)].V.dc.sympy)
    return volts


# --------------------------------------------------------------------------------------------- SKiDL


def _py(text: str) -> str:
    return repr(text or "")


def skidl_script(circuit: Circuit, *, pages: list[dict[str, Any]] | None = None) -> str:
    """そのまま ``python`` で実行できる SKiDL のスクリプト。実行すると KiCad のネットリスト（.net）を書き出す。"""
    parts = part_list(circuit, pages)
    lines = [
        "# TodenYOLO: 図面のアノテーションから組んだ回路（SKiDL）。実行すると circuit.net を書き出す。",
        "from skidl import KICAD8, SKIDL, TEMPLATE, Net, Part, Pin, generate_netlist",
        "",
        "def template(name, prefix):",
        "    pins = [Pin(num=i, name=str(i), func=Pin.types.PASSIVE) for i in (1, 2)]",
        f"    return Part(tool=SKIDL, name=name, ref_prefix=prefix, dest=TEMPLATE, pins=pins, footprint={FOOTPRINT!r})",
        "",
    ]
    kinds = sorted({(_template_name(p), p.ref.rstrip("0123456789")) for p in parts})
    lines += [f"{k} = template({k!r}, {prefix!r})" for k, prefix in kinds]
    lines += ["", "nets = {}"]
    nodes = sorted({_safe_node(n) for p in parts for n in (p.element.a, p.element.b)})
    lines += [f"nets[{n!r}] = Net({('GND' if n == GROUND else n)!r})" for n in nodes]
    lines.append("")
    for p in parts:
        e = p.element
        lines.append(f"# {_comment(e.label)} ({e.project_id}:{e.symbol_ref})")
        lines.append(f"p = {_template_name(p)}(ref={p.ref!r}, value={_py(e.label or e.device or p.class_key)}, tag={p.ref!r})")
        if e.device:
            lines.append(f"p.fields['Device'] = {_py(e.device)}")
        lines.append(f"p.fields['Source'] = {_py(f'{e.project_id}:{e.symbol_ref}')}")
        lines.append(f"nets[{_safe_node(e.a)!r}] += p[1]")
        lines.append(f"nets[{_safe_node(e.b)!r}] += p[2]")
    lines += ["", "generate_netlist(tool=KICAD8, file_='circuit.net')", ""]
    return "\n".join(lines)


def _template_name(p: PartInfo) -> str:
    if p.element.kind == "a":
        return "CONTACT_A"
    if p.element.kind == "b":
        return "CONTACT_B"
    return {"relay_coil": "COIL", "lamp": "LAMP", "solenoid": "SOLENOID", "diode": "DIODE"}.get(p.class_key, "LOAD")


def to_skidl(circuit: Circuit, *, pages: list[dict[str, Any]] | None = None):
    """skidl.Circuit を返す（skidl が必要）。部品の fields に器具番号（Device）と元の記号（Source）を入れる。"""
    import skidl

    ckt = skidl.Circuit()
    # SKiDL は import 時と生成時に作業フォルダへ .log / .erc / .net / *_sklib.py を書く。API・テストから使うときは書かせない
    # （ログのファイルも消える）。
    ckt.no_files = True
    templates: dict[str, Any] = {}
    nets: dict[str, Any] = {}

    def net(node: str):
        name = _safe_node(node)
        if name not in nets:
            nets[name] = skidl.Net("GND" if name == GROUND else name, circuit=ckt)
        return nets[name]

    for p in part_list(circuit, pages):
        e = p.element
        kind = _template_name(p)
        if kind not in templates:
            pins = [skidl.Pin(num=i, name=str(i), func=skidl.Pin.types.PASSIVE) for i in (1, 2)]
            templates[kind] = skidl.Part(tool=skidl.SKIDL, name=kind, ref_prefix=p.ref.rstrip("0123456789"),
                                         dest=skidl.TEMPLATE, pins=pins, footprint=FOOTPRINT)
        part = templates[kind](ref=p.ref, value=e.label or e.device or p.class_key, tag=p.ref, circuit=ckt)
        if e.device:
            part.fields["Device"] = e.device
        part.fields["Source"] = f"{e.project_id}:{e.symbol_ref}"
        for node, pin in ((e.a, 1), (e.b, 2)):
            n = net(node)
            n += part[pin]
    return ckt


def kicad_netlist(circuit: Circuit, *, pages: list[dict[str, Any]] | None = None) -> str:
    """SKiDL で作った KiCad 8 のネットリスト（S 式）。"""
    import skidl

    return str(to_skidl(circuit, pages=pages).generate_netlist(tool=skidl.KICAD8))
