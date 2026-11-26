"""展開接続図の「文字」を正解つきで合成する（DD-0050）。ラベルの結び付け（DD-0049）と属性の推定（DD-0050）を測る。

記号の枠は印だけを囲み（2026-11-26 の取り決め）、器具番号・型式・参照先は枠の外に書く。紙芝居の図面
（EV31031〜EV31036、150 dpi）の書き方をまねて、1 枚に次のものを置く。

- 上下の制御母線（PEV110500 / NEV110500）と、母線の上の縦書きの回路の名前（信号名）
- 縦の回路ごとに、コネクタ（CN7 と番号、上に 387）、I/O カードの接点（D001 / B405、C・NO の印）、リレーの接点
  （器具番号 / 型式 / (参照先)、左に端子番号）、電線番号（01）、コイル（丸の中に器具番号と型式）
- タイマー（コイルの右に「1sec」「(0-12sec)」、母線の下に型式 TDPU / TDDO）
- キープリレー（並んだ 2 つの丸の中に O・R、器具番号は 2 つの上に 1 つ）
- 増設のコイル（同じ器具番号に -1・-2 を付けて横に並べる。O・R は無い）

画像ではなく、OCR が返す形（行の文字・枠・確からしさ）を作る。OCR の読み違いは紙芝居の実際の読み取り結果から
選んだ（O と 0、D と 0、M と N、1 と I、行末への端子番号のくっつき、接点の棒の I、行の抜け、縦書きの崩れ）。
noise を 0 にすると読み違いの無い文字になる。
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Any

WIDTH, HEIGHT = 1755, 1241
TOP_Y, BOTTOM_Y = 222.0, 852.0
CHAR_W, LINE_H = 6.6, 12.0

_DEVICES = ["83DX", "83M", "30M", "6D", "5D", "6M", "5M", "43LK", "86T", "80", "6X", "5X", "33HB", "52", "27X",
            "20MB", "88M", "3-52", "49X", "63Q"]
_TYPES = ["P33P22", "P33P22", "P33P22", "MY4N", "HP2"]
_CAPTIONS = ["起動指令", "停止指令", "主制御装置選択", "定検保守装置選択", "電源正常", "引きロック指令", "引きロック解除指令",
             "CPU正常", "制御装置故障によるトリップ", "運転中", "故障", "保守運用中"]
_UNITS = ["(個別制御装置)", "(定検/保守装置)", "(主制御装置)", "(定検保守装置)", ""]
# OCR の読み違い（紙芝居の読み取り結果から）。
_CONFUSE = {"O": "0", "0": "O", "D": "0", "M": "N", "B": "8", "1": "I", "S": "5", "8": "B", "N": "M", "Q": "0"}
_CJK_CONFUSE = {"検": "换", "運": "还", "装": "画", "置": "畳", "指": "措", "令": "今", "制": "利", "御": "徊"}


@dataclass(frozen=True)
class SheetConfig:
    noise: float = 0.5          # 0 = 読み違い無し、1 = 紙芝居の OCR くらい
    label_side: str = "mixed"   # 接点のラベルの位置: right / above / below / mixed（図面ごとに違う書き方）


class _Sheet:
    def __init__(self, seed: int, config: SheetConfig) -> None:
        self.rng = random.Random(seed)
        # 読み違いは別の乱数で決める（noise を変えても図面の並びは同じ）。
        self.noise_rng = random.Random(seed * 7919 + 17)
        self.seed = seed
        self.config = config
        self.symbols: list[dict[str, Any]] = []
        self.items: list[dict[str, Any]] = []
        self.crop_items: dict[str, list[dict[str, Any]]] = {}
        self.count = 0
        side = config.label_side
        if side == "mixed":
            side = self.rng.choices(["right", "above", "below"], weights=[6, 2, 2])[0]
        self.side = side
        self.sheet_no = f"EV31{self.rng.randint(10, 99):03d}"

    # --- 記号 ---
    def symbol(self, cls: str, box: tuple[float, float, float, float], label: str = "",
               label_box: tuple[float, float, float, float] | None = None, **attributes: Any) -> dict[str, Any]:
        self.count += 1
        s = {"id": f"S{self.count:03d}", "class_name": cls, "bbox": tuple(round(v, 1) for v in box),
             "gold_label": label, "gold_label_box": label_box, "gold_attributes": {k: v for k, v in attributes.items() if v is not None}}
        self.symbols.append(s)
        return s

    # --- 文字 ---
    def text(self, text: str, x: float, y: float, *, vertical: bool = False, score: float | None = None,
             crop_of: str | None = None, clean: bool = False) -> tuple[float, float, float, float]:
        """左上 (x, y) に 1 行の文字を書き、OCR の行として足す。書いた範囲（読み違い前）を返す。"""
        n = len(text)
        wide = sum(2 if ord(c) > 0x2E80 else 1 for c in text)
        if vertical:
            # 縦書き: 1 文字ずつ下へ。日本語は 1 文字が 2 桁分の幅。
            box = (x, y, x + (2.0 if wide > n else 1.4) * CHAR_W, y + n * LINE_H * 0.95)
        else:
            box = (x, y, x + wide * CHAR_W, y + LINE_H)
        read = text if clean else self.misread(text, vertical)
        if read is None:
            return box
        jitter = self.config.noise * 1.5
        item_box = tuple(v + self.noise_rng.uniform(-jitter, jitter) for v in box)
        item = {"text": read, "score": score if score is not None else round(self.noise_rng.uniform(0.55, 0.95), 2),
                "box": tuple(round(v, 1) for v in item_box)}
        (self.crop_items.setdefault(crop_of, []) if crop_of else self.items).append(item)
        return box

    def misread(self, text: str, vertical: bool = False) -> str | None:
        noise = self.config.noise
        rng = self.noise_rng
        if rng.random() < 0.04 * noise:
            return None  # 行が読めない
        chars = list(text)
        for i, c in enumerate(chars):
            if vertical and c in _CJK_CONFUSE and rng.random() < 0.35 * noise:
                chars[i] = _CJK_CONFUSE[c]
            elif c in _CONFUSE and rng.random() < 0.06 * noise:
                chars[i] = _CONFUSE[c]
        return "".join(chars)

    def block(self, lines: list[str], x: float, y: float, *, anchor: str = "left") -> tuple[float, float, float, float]:
        """上下に並ぶ行（器具番号 / 型式 / (参照先)）。anchor = left は左端、center は中央そろえ。"""
        boxes = []
        for k, line in enumerate(lines):
            w = len(line) * CHAR_W
            lx = x - w / 2 if anchor == "center" else x + self.rng.uniform(-1.0, 1.0)
            boxes.append(self.text(line, lx, y + k * (LINE_H + 1.0)))
        return (min(b[0] for b in boxes), min(b[1] for b in boxes), max(b[2] for b in boxes), max(b[3] for b in boxes))

    # --- 部品 ---
    def contact(self, x: float, y: float, device: str, kind: str = "a", *, ref: bool = True, terminals: tuple[str, str] = ("12", "4"),
                io: bool = False, **attributes: Any) -> dict[str, Any]:
        """接点（印は 2 つの白丸と棒、上の白丸の中心が x, y）。器具番号は右・上・下、端子番号は左。"""
        box = (x - 5, y - 5, x + 9, y + 25)
        if io:
            # I/O カードの中の接点: 右に D001 / B405（2 行）、左に C・NO の印。
            lines = device.split()
            gap = self.rng.uniform(6, 12)
            label_box = self.block(lines, box[2] + gap, y + 2)
            self.text("C", x - 16, y - 6, clean=True)
            self.text("NO", x - 20, y + 16, clean=True)
            return self.symbol(f"contact_{kind}", box, device, label_box, external_io="yes", **attributes)
        name, typ = device.split()[0], device.split()[1] if len(device.split()) > 1 else self.rng.choice(_TYPES)
        lines = [name, typ] + ([f"({self.sheet_no[:-1]}{self.rng.randint(1, 6)})"] if ref else [])
        side = self.side
        gap = self.rng.uniform(2, 10)
        if side == "right":
            first = lines[0]
            if gap < 4 and self.noise_rng.random() < 0.6 * self.config.noise:
                lines = ["I" + first] + lines[1:]  # 接点の棒と器具番号がくっつく
            label_box = self.block(lines, box[2] + gap, y - 4)
        elif side == "above":
            label_box = self.block(lines, x + 2, box[1] - gap - len(lines) * (LINE_H + 1), anchor="center")
        else:
            label_box = self.block(lines, x + 2, box[3] + gap, anchor="center")
        if lines[0].startswith("I") and not name.startswith("I"):
            label_box = (label_box[0] + CHAR_W, *label_box[1:])
        self.text(terminals[0], x - 9 - CHAR_W * len(terminals[0]), y - 8, clean=self.noise_rng.random() > self.config.noise)
        self.text(terminals[1], x - 9 - CHAR_W * len(terminals[1]), y + 14, clean=self.noise_rng.random() > self.config.noise)
        return self.symbol(f"contact_{kind}", box, f"{name} {typ}", label_box, **attributes)

    def coil(self, x: float, y: float, device: str, *, inside: bool = True, mark: str | None = None,
             label: bool = True, **attributes: Any) -> dict[str, Any]:
        """コイル（半径 22 の丸、中心 x, y）。器具番号と型式は丸の中（inside）か右。端子番号 14・13 は上下。"""
        r = 22.0
        box = (x - r, y - r, x + r, y + r)
        name, typ = device.split()
        label_box = None
        if label and inside:
            label_box = self.block([name, typ], x, y - LINE_H - 1, anchor="center")
        elif label:
            label_box = self.block([name, typ], box[2] + self.rng.uniform(3, 9), y - LINE_H)
        self.text("14", x - 14, box[1] - 15, clean=True)
        self.text("13", x - 14, box[3] + 3, clean=True)
        s = self.symbol("relay_coil", box, device if label else "", label_box, **attributes)
        # キープリレーの O・R は丸の中に 1 文字。ページの OCR は小さな 1 文字を落とすので、拡大読みにだけ出る。
        if mark is not None and self.noise_rng.random() > 0.25 * self.config.noise:
            self.text(mark, x - CHAR_W / 2, y - LINE_H / 2, crop_of=s["id"], clean=True)
        return s

    def connector(self, x: float, y: float, name: str, number: str, *, wire_no: str | None = None) -> dict[str, Any]:
        """端子台（黒丸、中心 x, y）。名前は左に、番号は右に、上に電線番号（387）。"""
        box = (x - 5, y - 5, x + 5, y + 5)
        lines = [name] if wire_no is None else [wire_no, name]
        lb = self.block(lines, x - 8 - CHAR_W * max(len(t) for t in lines), y - 6 - (LINE_H + 1) * (len(lines) - 1))
        self.text(number, x + 10, y - 6, clean=True)
        return self.symbol("connector", box, " ".join(lines), lb)

    def caption(self, x: float, text: str) -> tuple[float, float, float, float]:
        """母線の上の縦書きの名前。2 列（名前と（装置））なら左右に並べる。線のすぐ左に書く。"""
        parts = [p for p in (text.split("(")[0], "(" + text.split("(")[1] if "(" in text else "") if p]
        boxes = []
        for k, part in enumerate(parts):
            length = len(part) * LINE_H * 0.95
            bx = x - 18 + k * 15
            by = TOP_Y - 14 - length
            if self.noise_rng.random() < 0.2 * self.config.noise and len(part) > 3:
                # 縦書きを 2 つの行に分けて読む
                cut = len(part) // 2
                self.text(part[:cut], bx, by, vertical=True)
                self.text(part[cut:], bx, by + cut * LINE_H * 0.95, vertical=True)
            else:
                boxes.append(self.text(part, bx, by, vertical=True))
        return boxes[0] if boxes else (x - 18, TOP_Y - 60, x, TOP_Y - 14)

    def wire_number(self, x: float, y: float, number: str) -> None:
        self.text(number, x - 14, y, vertical=True)


def _device(rng: random.Random, used: set[str]) -> str:
    for _ in range(50):
        name = rng.choice(_DEVICES)
        if rng.random() < 0.4:
            name += f"-{rng.randint(1, 3)}"
        if name not in used:
            used.add(name)
            return name
    name = f"{rng.randint(10, 99)}X"
    used.add(name)
    return name


def make_sheet(seed: int, config: SheetConfig | None = None) -> dict[str, Any]:
    """合成の 1 枚。symbols は正解（gold_label・gold_label_box・gold_attributes）つき、items はページの OCR の行。"""
    config = config or SheetConfig()
    sh = _Sheet(seed, config)
    rng = sh.rng
    sh.text("PEV110500", 176, TOP_Y - 6, clean=True)
    sh.text(f"({sh.sheet_no[:-1]}3)", 176, TOP_Y + 15, clean=True)
    sh.text("NEV110500", 176, BOTTOM_Y - 6, clean=True)
    sh.symbol("power_bus", (150, TOP_Y - 5, 160, TOP_Y + 5), "PEV110500")
    used: set[str] = set()
    x = 250.0 + rng.uniform(-20, 20)
    columns = rng.randint(4, 8)
    kinds = rng.choices(["io", "relay", "relay", "timer", "latch", "parallel"], k=columns)
    io_n = 0
    for kind in kinds:
        if x > WIDTH - 260:
            break
        caption = rng.choice(_CAPTIONS) + rng.choice(_UNITS)
        with_caption = kind in ("io", "latch") or rng.random() < 0.5
        if kind == "io":
            io_n += 1
            io = f"D{rng.randint(1, 9):03d} B{rng.randint(400, 899)}"
            sh.connector(x, TOP_Y + 110, f"CN{rng.randint(5, 23)}", str(2 * io_n - 1), wire_no="387" if rng.random() < 0.6 else None)
            contact = sh.contact(x, TOP_Y + 260, io, rng.choice("ab"), io=True,
                                 signal_name=caption if with_caption else None)
            sh.connector(x, TOP_Y + 440, f"CN{rng.randint(5, 23)}", str(2 * io_n))
            sh.wire_number(x, TOP_Y + 480, f"{io_n:02d}")
            dev = _device(rng, used)
            sh.coil(x, BOTTOM_Y - 70, f"{dev} {rng.choice(_TYPES)}", inside=rng.random() < 0.8)
            if with_caption:
                sh.caption(x, caption)
            del contact
            x += rng.uniform(95, 140)
        elif kind in ("relay", "timer"):
            for k in range(rng.randint(1, 3)):
                dev = _device(rng, used)
                sh.contact(x, TOP_Y + 40 + k * 150 + rng.uniform(0, 40), f"{dev} {rng.choice(_TYPES)}", rng.choice("aab"),
                           ref=rng.random() < 0.7, terminals=(str(rng.choice([12, 10, 11, 5, 3])), str(rng.choice([4, 6, 8, 9, 7]))))
            sh.wire_number(x, TOP_Y + 500, f"{rng.randint(1, 9):02d}")
            dev = _device(rng, used)
            attrs: dict[str, Any] = {}
            if kind == "timer":
                mode = rng.choices(["on_delay", "off_delay"], weights=[3, 1])[0]
                delay = rng.choice([0.5, 1, 1, 2, 3, 5, 10])
                attrs = {"timer_mode": mode, "delay_s": float(delay)}
            name = caption if with_caption else None
            coil = sh.coil(x, BOTTOM_Y - 70, f"{dev} {rng.choice(_TYPES)}", inside=rng.random() < 0.8, signal_name=name, **attrs)
            if kind == "timer":
                bx = coil["bbox"]
                sec = f"{attrs['delay_s']:g}sec"
                if sh.noise_rng.random() < 0.5 * config.noise:
                    sec = sec.replace("1", "I", 1)
                sh.text(sec, bx[2] + 4, bx[1] + 32, clean=True)
                if rng.random() < 0.7:
                    sh.text(f"(0-{rng.choice([3, 12, 30, 60])}sec)", bx[2] - 6, bx[1] + 44, clean=True)
                model = "TDPU" if attrs["timer_mode"] == "on_delay" else "TDDO"
                if rng.random() < 0.9:
                    if sh.noise_rng.random() < 0.5 * config.noise:
                        model = model.replace("D", "O", 1)
                    sh.text(f"(3B8-8F-{rng.randint(1, 19)})", bx[0] + 4, BOTTOM_Y + 22, clean=True)
                    sh.text(model, bx[0] + 14, BOTTOM_Y + 33, clean=True)
            if with_caption:
                sh.caption(x, caption)
            x += rng.uniform(85, 130)
        elif kind == "latch":
            # 2 列の入力（I/O カード）と、下に並んだ O・R の 2 つの丸。
            dev = "43LK" if "43LK" not in used else _device(rng, used)
            used.add(dev)
            typ = rng.choice(_TYPES)
            xs = [x, x + rng.uniform(55, 70)]
            for cx in xs:
                io_n += 1
                io = f"D{rng.randint(1, 9):03d} B{rng.randint(400, 899)}"
                name = rng.choice(["引きロック指令", "引きロック解除指令"]) + rng.choice(_UNITS)
                sh.contact(cx, TOP_Y + 260, io, "a", io=True, signal_name=name)
                sh.caption(cx, name)
            cy = BOTTOM_Y - 70
            r = 22.0
            left = xs[0]
            right = left + 2 * r + rng.uniform(4, 90)
            # 器具番号は 2 つの丸の上、中央に 1 つ
            lb = sh.block([dev, typ], (left + right) / 2, cy - r - 2 * (LINE_H + 1) - 4, anchor="center")
            set_left = rng.random() < 0.85
            a = sh.coil(left, cy, f"{dev} {typ}", label=False, mark="O" if set_left else "R",
                        latch_coil="set" if set_left else "reset")
            b = sh.coil(right, cy, f"{dev} {typ}", label=False, mark="R" if set_left else "O",
                        latch_coil="reset" if set_left else "set")
            for s in (a, b):
                s["gold_label"], s["gold_label_box"] = f"{dev} {typ}", lb
            x = max(xs[1], right) + rng.uniform(85, 130)
        else:  # parallel: 増設のコイル（83M、83M-1、83M-2）
            base = _device(rng, used).split("-")[0]
            typ = rng.choice(_TYPES)
            n = rng.randint(2, 3)
            sh.contact(x, TOP_Y + 120, f"{_device(rng, used)} {rng.choice(_TYPES)}", "a")
            for k in range(n):
                name = base if k == 0 else f"{base}-{k}"
                sh.coil(x + k * 47, BOTTOM_Y - 70, f"{name} {typ}", inside=True)
            if with_caption:
                sh.caption(x, caption)
                sh.symbols[-n]["gold_attributes"]["signal_name"] = caption
            x += 47 * (n - 1) + rng.uniform(85, 130)
    sh.symbol("power_bus", (150, BOTTOM_Y - 5, 160, BOTTOM_Y + 5), "NEV110500")
    rng.shuffle(sh.items)
    return {"seed": seed, "width": WIDTH, "height": HEIGHT, "sheet_no": sh.sheet_no, "label_side": sh.side,
            "noise": config.noise, "symbols": sh.symbols, "items": sh.items, "crop_items": sh.crop_items}


def detected(sheet: dict[str, Any]) -> list[dict[str, Any]]:
    """解析に渡す記号（正解を外した、検出の結果の形）。"""
    return [{"id": s["id"], "class_name": s["class_name"], "bbox": s["bbox"]} for s in sheet["symbols"]]
