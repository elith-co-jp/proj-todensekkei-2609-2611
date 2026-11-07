"""配線解析の調整値。値はすべて 150 dpi の画像の画素数（px）か比率で、根拠は各項目の説明と DD に書く。

各段の関数はここの値を引数（既定値 DEFAULT）で受け取る。値を変えて試すときは dataclasses.replace で別の
値を作って渡し、モジュールの定数は書き換えない。環境変数で切り替えるものは from_env で読む。
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field


@dataclass(frozen=True)
class GraphParams:
    """配線（線分）から交点・端点のノードと辺を作るときの距離（DD-0009 §8）。"""

    # これより近い点は 1 つのノードにする。横線の行は約 11 px 間隔で並ぶ（ER21276）ので、それより小さくする。
    node_merge_px: int = 6
    # ノードが配線の上にあるとみなす、配線と直角の向きの距離。
    on_line_across_px: int = 6
    # 配線の検出した端の先で、ほかの配線と交わる・ノードを持つとみなす距離（16〜18 px は下の行に届いた、ER21277）。
    line_reach_px: int = 8


@dataclass(frozen=True)
class PortParams:
    """記号の端子の位置を決めるときの値（DD-0009 §8・§9）。"""

    # 分岐点は横線 1 本・縦線 1 本の上にある。中心に最も近い線からこれ以内の平行線だけを端子にする。
    junction_axis_tolerance_px: float = 3.0
    # 端子が 2 個に満たない接点・コイルで、画像の線から端子を決める（SEQANNO_LINE_PORTS=0 で無効）。
    line_ports: bool = True
    # 枠の外へ続く線の長さの下限。文字の画は短い。
    line_min_run_px: int = 10
    # 端子の位置を選ぶときに枠の外を見る長さ。
    line_probe_px: int = 40
    # 配線を探してたどる長さの上限。
    line_trace_max_px: int = 400
    # たどるときに許す線の途切れ。
    line_gap_px: int = 2
    # たどる線がコネクタ・端子台の枠に入り、その中心がたどる線からこれ以内にあれば、その記号の端子につなぐ。
    # コネクタの間の短い線は配線として検出されない（ER21315 の ABN GW の上）。0 でつながない。
    line_pass_through_px: float = 6.0
    # コイルの薄い点線（ER21256）: 灰色がこれ未満の画素を線とみなす。
    faint_gray: int = 240
    # 枠の外の行のうち、線がある行の割合の下限（近くの文字の列は 0.25 前後、線は 0.45 以上）。
    faint_coverage: float = 0.35
    # 点線をたどるときに許す途切れ。
    faint_gap_px: int = 6


@dataclass(frozen=True)
class LinkParams:
    """端子を配線・ノードに結ぶときの距離。"""

    terminal_wire_px: float = 10.0
    terminal_node_px: float = 28.0
    # 分岐点の記号から、これ以内のノードを黒丸のある（つながる）交点とみなす。
    junction_node_px: float = 24.0


@dataclass(frozen=True)
class BridgeParams:
    """端子から配線グラフをたどって接続を求めるとき、線の途切れを橋渡しする条件。"""

    node_anchor_px: float = 24.0
    terminal_gap_bridge_max_px: float = 240.0
    wire_gap_bridge_max_px: float = 80.0
    gap_bridge_align_px: float = 28.0
    wire_gap_terminal_anchor_px: float = 18.0
    # 分岐点どうしは検出した配線でだけ結ぶ。並んだ黒丸を結ぶと別々の配線が 1 つになった（ER21277）。
    terminal_terminal_bridge_max_px: float = 0.0
    endpoint_bridge_max_px: float = 60.0
    # 配線の端から、その先の直交する配線にだけ橋渡しする（自分の線の上を戻らない）。
    wire_gap_bridge_outward_only: bool = True
    # 橋渡しが通ってよい空白の長さ。配線の上の印は 38 px 未満、カードの + と - の点の間は 50 px を超える。0 で確認しない。
    wire_gap_bridge_max_blank_px: float = 45.0
    terminal_gap_bridge_max_blank_px: float = 45.0
    # 橋渡しは機器の端子を越えない。コイルの中で終わる配線の端からは同じコイルのもう一方の端子を（ER21298）、
    # 端子からの橋渡しはほかの接点・コイルの端子を（ER21315 の 30GW）。
    # 端子が橋渡しの線から横に何 px 以内なら「線の上」とみなすか。0 で確認しない。
    device_jump_across_px: float = 4.0
    # 橋渡しの線に沿った範囲の端の遊び（画素の丸め）。
    device_jump_end_px: float = 1.0


@dataclass(frozen=True)
class WiringParams:
    graph: GraphParams = field(default_factory=GraphParams)
    ports: PortParams = field(default_factory=PortParams)
    link: LinkParams = field(default_factory=LinkParams)
    bridge: BridgeParams = field(default_factory=BridgeParams)
    # 一直線に並ぶ未接続の端子の間の薄い線を配線として足す（DD-0007 §4.22、SEQANNO_FAINT_LINES=0 で無効）。
    faint_lines: bool = True

    @classmethod
    def from_env(cls, environ: dict[str, str] | None = None) -> WiringParams:
        env = os.environ if environ is None else environ
        return cls(ports=PortParams(line_ports=env.get("SEQANNO_LINE_PORTS", "1") != "0"),
                   faint_lines=env.get("SEQANNO_FAINT_LINES", "1") != "0")


DEFAULT = WiringParams.from_env()

