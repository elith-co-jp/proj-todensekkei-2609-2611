"""紙芝居（2026-11-25 受領「図面の読み方」）の展開接続図 EV31031〜EV31036 を、アノテーションの形（pages）で再現する（DD-0045）。

紙芝居の最終ページの論理図（22号 LNG高圧ポンプ IBD の起動・停止）を、図面の読み取り結果から作れるかを確かめるための
正解つきの例。器具番号・接点の a/b・シート番号・コイルの並列（増設のリレー）・タイマー・キープリレーは原図のとおり。
座標は原図に合わせていない（記号の並びだけ）。I/O カードの接点（D001〜D009）は他盤（DDC・SAM）からの信号なので
外部入力（属性 external_io）にし、論理図の名前（属性 signal_name）は紙芝居の最終ページの書き方にした。

| シート | 内容 |
|---|---|
| EV31031 | 主回路・M/C の操作回路。6X（起動指令）・5X（停止指令）の接点が 110RV I/F AXR を経て M/C へ |
| EV31032 | 83D（主制御装置選択指令 D009）、83DX・83DX-1〜3（主制御装置選択）、300（CPU 正常）、80・80-1（電源正常） |
| EV31033 | 30M（定検保守装置 CPU 正常 D008）、83M・83M-1〜3（定検保守装置選択 = ¬83DX ∧ 30M）、20MB（1 秒）、86T・86T-1 |
| EV31034 | 6D・5D（個別制御装置の起動・停止指令）、6M・5M（定検/保守装置の起動・停止指令） |
| EV31035 | 43LK（引きロック。O = セット、R = リセットのキープリレー） |
| EV31036 | 6X（起動指令）・5X（停止指令） |
"""

from __future__ import annotations

from typing import Any

WIDTH, HEIGHT = 1755, 1241
TOP_Y, BOTTOM_Y = 120.0, 1080.0
SHEETS = ["EV31031", "EV31032", "EV31033", "EV31034", "EV31035", "EV31036"]
TITLE = "22号 LNG高圧ポンプ IBD"

# 論理図の名前（紙芝居の最終ページ）。器具番号 → 名前。記号の属性 signal_name として入れる。
NAMES = {
    "D009 B413": "主制御装置選択指令",
    "83DX P33P22": "主制御装置選択",
    "D008 B662": "定検保守装置CPU正常",
    "83M P33P22": "定検保守装置選択",
    "D001 B405": "起動指令（個別制御装置）",
    "D002 B406": "停止指令（個別制御装置）",
    "D001 B855": "起動指令（定検/保守装置）",
    "D002 B856": "停止指令（定検/保守装置）",
    "D003 B407": "引きロック指令（主制御装置）",
    "D004 B408": "引きロック解除指令（主制御装置）",
    "D003 B857": "引きロック指令（定検保守装置）",
    "D004 B858": "引きロック解除指令（定検保守装置）",
    "43LK P33P22": "引ロック",
    "86T P33P22": "制御装置故障によるトリップ",
    "80 P33P22": "電源正常",
    "6X P33P22": "起動指令",
    "5X P33P22": "停止指令",
    "D008 B412": "CPU正常（主制御装置）",
}

# 他盤（DDC・SAM）からの信号。紙芝居の「制御盤(他盤)からの信号 → END」。
EXTERNAL = {"D009 B413", "D008 B412", "D008 B662", "D001 B405", "D002 B406", "D001 B855", "D002 B856",
            "D003 B407", "D004 B408", "D003 B857", "D004 B858", "52 a"}

OUTPUTS = ["6X P33P22", "5X P33P22"]


def C(label: str, kind: str = "a", **attrs: Any) -> tuple[str, Any]:
    """接点。label は図面の書き方（「83DX-1 P33P22 (EV31032)」）。"""
    return ("c", (label, kind, attrs))


def S(*items: Any) -> tuple[str, Any]:
    """直列。"""
    return ("s", list(items))


def P(*items: Any) -> tuple[str, Any]:
    """並列。"""
    return ("p", list(items))


class _Sheet:
    def __init__(self, pid: int, name: str) -> None:
        self.pid, self.name = pid, name
        self.symbols: list[dict[str, Any]] = []
        self.wires: list[tuple[tuple[str, str], tuple[str, str], str]] = []
        self.count = 0
        self.column = 1.0
        self.nodes: dict[str, tuple[str, str]] = {}
        top = self.add("P", "power_bus", "PEV110500", 40, TOP_Y, 1)
        bottom = self.add("N", "power_bus", "NEV110500", 40, BOTTOM_Y, 1)
        self.nodes["P"], self.nodes["N"] = _t(top, 1), _t(bottom, 1)

    def ref(self, prefix: str) -> str:
        self.count += 1
        return f"{prefix}-{self.count:03d}"

    def add(self, ref: str, cls: str, label: str, x: float, y: float, terms: int, attrs: dict[str, Any] | None = None,
            w: float = 40, h: float = 50) -> dict[str, Any]:
        points = [(x, y)] if terms == 1 else [(x, y - h / 2), (x, y + h / 2)]
        symbol = {"ref": ref, "class_key": cls, "class_label": cls, "label": label, "cx": x / WIDTH, "cy": y / HEIGHT,
                  "w": w / WIDTH, "h": h / HEIGHT, "origin": "manual", "confidence": None, "note": None,
                  "attributes": dict(attrs or {}),
                  "terminals": [{"ref": f"{ref}-T{i}", "name": str(i), "tx": px / WIDTH, "ty": py / HEIGHT}
                                for i, (px, py) in enumerate(points, start=1)]}
        self.symbols.append(symbol)
        return symbol

    def junction(self, x: float, y: float) -> tuple[str, str]:
        return _t(self.add(self.ref("J"), "junction", "", x, y, 1, w=10, h=10), 1)

    def wire(self, a: tuple[str, str], b: tuple[str, str], wire_no: str = "") -> None:
        self.wires.append((a, b, wire_no))

    def net(self, expr: Any, a: tuple[str, str], b: tuple[str, str], x: float, y0: float, y1: float) -> None:
        """a から b までを式どおりの接点でつなぐ。"""
        kind, body = expr
        if kind == "c":
            label, ab, attrs = body
            device = " ".join(label.split()[:2])
            attrs = dict(attrs)
            if device in EXTERNAL:
                attrs.setdefault("external_io", "yes")
            if device in NAMES:
                attrs.setdefault("signal_name", NAMES[device])
            c = self.add(self.ref("C"), f"contact_{ab}", label, x, (y0 + y1) / 2, 2, attrs)
            self.wire(a, _t(c, 1))
            self.wire(_t(c, 2), b)
        elif kind == "s":
            n = len(body)
            points = [a] + [self.junction(x, y0 + (y1 - y0) * k / n) for k in range(1, n)] + [b]
            for k, item in enumerate(body):
                self.net(item, points[k], points[k + 1], x, y0 + (y1 - y0) * k / n, y0 + (y1 - y0) * (k + 1) / n)
        else:
            for k, item in enumerate(body):
                xx = x + 70 * k
                ja, jb = self.junction(xx, y0), self.junction(xx, y1)
                self.wire(a, ja)
                self.wire(jb, b)
                self.net(item, ja, jb, xx, y0 + 10, y1 - 10)

    def rung(self, expr: Any, loads: list[tuple[str, str, dict[str, Any]]], *, start: str = "P", node: str | None = None,
             width: int = 1) -> None:
        """start（母線か名前を付けた点）から式の接点を通り、負荷（並列）を経て N へ。node を付けると式の終わりの点に名前を付ける。"""
        x = 80 + 90 * self.column
        self.column += width
        top = self.nodes[start]
        if start == "P":
            j = self.junction(x, TOP_Y)
            self.wire(top, j)
            top = j
        if expr is None:
            end = top
        else:
            end = self.junction(x, 700)
            self.net(expr, top, end, x, TOP_Y + 40, 690)
        if node:
            self.nodes[node] = end
        for k, (label, cls, attrs) in enumerate(loads):
            xx = x + 70 * k
            device = " ".join(label.split()[:2])
            attrs = dict(attrs)
            if device in NAMES and cls == "relay_coil":
                attrs.setdefault("signal_name", NAMES[device])
            load = self.add(self.ref("R"), cls, label, xx, 950, 2, attrs, w=60, h=60)
            jb = self.junction(xx, BOTTOM_Y)
            self.wire(end, _t(load, 1))
            self.wire(_t(load, 2), jb)
            self.wire(jb, self.nodes["N"])

    def page(self) -> dict[str, Any]:
        connections = [{"from_symbol_ref": a[0], "from_terminal_ref": a[1], "to_symbol_ref": b[0], "to_terminal_ref": b[1],
                        "kind": "wire", "wire_no": no or None} for a, b, no in self.wires]
        return {"id": self.pid, "name": f"{self.name}.png", "sheet_no": self.name, "image_width": WIDTH,
                "image_height": HEIGHT, "symbols": self.symbols, "connections": connections}


def _t(symbol: dict[str, Any], i: int) -> tuple[str, str]:
    return symbol["ref"], symbol["terminals"][i - 1]["ref"]


def _coil(label: str, **attrs: Any) -> tuple[str, str, dict[str, Any]]:
    return (label, "relay_coil", attrs)


def build(*, sheets: list[str] | None = None) -> dict[str, Any]:
    """紙芝居の図面を pages（serialize_project の形）と母線の文字（bus_texts）にする。sheets で一部のシートだけにできる。"""
    s = {name: _Sheet(i + 1, name) for i, name in enumerate(SHEETS)}

    # EV31031: 主回路・M/C の操作回路（起動指令・停止指令の使用先。紙芝居 1 ページ目の ②）
    s["EV31031"].rung(C("6X P33P22 (EV31036)"), [("M/C 投入（110RV I/F AXR 起動指令）", "solenoid", {})])
    s["EV31031"].rung(C("5X P33P22 (EV31036)"), [("M/C 引外し（110RV I/F AXR 停止指令）", "solenoid", {})])

    # EV31032: 主制御装置の選択・CPU 正常・電源正常
    s["EV31032"].rung(C("D008 B412"), [_coil("300 P33P22")])
    s["EV31032"].rung(C("D009 B413"), [_coil("83D P33P22")])
    s["EV31032"].rung(C("83D P33P22"), [_coil("83DX P33P22"), _coil("83DX-1 P33P22"), _coil("83DX-2 P33P22"),
                                        _coil("83DX-3 P33P22")], width=4)
    s["EV31032"].rung(None, [_coil("80 P33P22"), _coil("80-1 P33P22")], width=2)

    # EV31033: 定検保守装置の選択（主制御装置が選ばれていないときだけ）と、どちらも選ばれていないときのトリップ（1 秒）
    s["EV31033"].rung(C("D008 B662"), [_coil("30M P33P22")])
    s["EV31033"].rung(C("83DX P33P22 (EV31032)", "b"), [], node="04")
    s["EV31033"].rung(C("30M P33P22"), [_coil("83M P33P22"), _coil("83M-1 P33P22"), _coil("83M-2 P33P22"),
                                        _coil("83M-3 P33P22")], start="04", width=4)
    s["EV31033"].rung(C("83M P33P22", "b"), [_coil("20MB P33P22", timer_mode="on_delay", delay_s=1)], start="04")
    s["EV31033"].rung(C("20MB P33P22"), [_coil("86T P33P22"), _coil("86T-1 P33P22")], width=2)

    # EV31034: 起動・停止指令（個別制御装置 = DDC、定検/保守装置 = SAM）
    s["EV31034"].rung(C("D001 B405"), [_coil("6D P33P22")])
    s["EV31034"].rung(C("D002 B406"), [_coil("5D P33P22")])
    s["EV31034"].rung(C("D001 B855"), [_coil("6M P33P22")])
    s["EV31034"].rung(C("D002 B856"), [_coil("5M P33P22")])

    # EV31035: 引きロック 43LK（O = セット、R = リセット）
    s["EV31035"].rung(P(S(C("83DX-1 P33P22 (EV31032)"), C("D003 B407")), S(C("83M-1 P33P22 (EV31033)"), C("D003 B857"))),
                      [_coil("43LK P33P22", latch_coil="O")], width=2)
    s["EV31035"].rung(P(S(C("83DX-1 P33P22 (EV31032)"), C("D004 B408")), S(C("83M-1 P33P22 (EV31033)"), C("D004 B858"))),
                      [_coil("43LK P33P22", latch_coil="R")], width=2)

    # EV31036: 起動指令 6X・停止指令 5X（紙芝居 6 ページ目）
    s["EV31036"].rung(S(P(S(C("83DX-1 P33P22 (EV31032)"), C("6D P33P22 (EV31034)")),
                          S(C("83M-1 P33P22 (EV31033)"), C("6M P33P22 (EV31034)"))),
                        C("43LK P33P22 (EV31035)", "b")),
                      [_coil("6X P33P22")], width=2)
    s["EV31036"].rung(P(S(C("83DX-1 P33P22 (EV31032)"), C("5D P33P22 (EV31034)")),
                        S(C("83M-1 P33P22 (EV31033)"), C("5M P33P22 (EV31034)")),
                        C("86T P33P22 (EV31033)"), C("43LK P33P22 (EV31035)"), C("80 P33P22 (EV31032)", "b")),
                      [_coil("5X P33P22")], width=5)

    chosen = [name for name in SHEETS if sheets is None or name in sheets]
    pages = [s[name].page() for name in chosen]
    bus_texts = {s[name].pid: [{"text": "PEV110500", "box": [20, TOP_Y - 30, 160, TOP_Y - 12]},
                               {"text": "NEV110500", "box": [20, BOTTOM_Y - 30, 160, BOTTOM_Y - 12]}] for name in chosen}
    return {"pages": pages, "bus_texts": bus_texts, "outputs": list(OUTPUTS), "names": dict(NAMES), "title": TITLE}


# 紙芝居の最終ページの論理（正本）。信号は器具番号の先頭の語・入力は器具番号。
EXPECTED = {
    "A": "D009",                                             # 主制御装置選択（83DX = 83D = D009）
    "B": "!A & D008_SAM",                                    # 定検保守装置選択 83M
    "C": "S: (A & D003_DDC) | (B & D003_SAM) ; R: (A & D004_DDC) | (B & D004_SAM)",  # 引ロック 43LK
    "D": "1sec(!(A | B))",                                   # 制御装置故障によるトリップ 86T（20MB）
    "6X": "((A & D001_DDC) | (B & D001_SAM)) & !C",          # 起動
    "5X": "(A & D002_DDC) | (B & D002_SAM) | D | C | !80",   # 停止
}
