"""表の読み取り（DD-0050）を測るための、正解つきの合成の図面（リレー表・表題欄）を作る。

実図面のリレー表と同じく「器具番号・端子・接点・シート・行列」のブロックを横に 2 つ・縦に 2 段並べ、右下に表題欄を置く。
文字の大きさ（150 dpi で 10〜13 px）、字体（ゴシック・CAD のような線の字体など）、スキャンの劣化（ぼかし・縮小・JPEG・ノイズ）を
種（seed）ごとに変える。正解は欄ごとの文字と枠（ページの座標）と、リレー表の行。
"""

from __future__ import annotations

import io
import random
from dataclasses import dataclass, field
from typing import Any

from PIL import Image, ImageDraw, ImageFilter, ImageFont

W, H = 1755, 1241
JP_FONTS = ["/usr/share/fonts/opentype/ipafont-gothic/ipag.ttf", "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc"]
ASCII_FONTS = [
    "/usr/share/fonts/opentype/ipafont-gothic/ipag.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
    "hershey",  # CAD の線の字体に近い（OpenCV の Hershey）
]
HEADERS = ["器具番号", "端子", "接点", "シート", "行列"]
TITLE_KEYS = ["設備記号", "名称", "シート番号", "改訂"]
TERMINALS = ["5-9-1", "8-12-4", "7-11-3", "6-10-2"]
DEVICE_HEADS = ["33HB", "33LA", "6X", "5X", "86T", "43LK", "83DX", "30M", "6D", "5M", "80", "27X", "63Q", "52C"]
MODELS = ["FV2306", "P33P22", "FV2306A", "MY4N", "P33P11"]


@dataclass
class Cell:
    text: str
    box: tuple[float, float, float, float]
    kind: str  # header / device / terminal / contact / sheet / position / title_key / title_value


@dataclass
class SyntheticTable:
    image: Image.Image
    cells: list[Cell] = field(default_factory=list)
    relay_rows: list[dict[str, str]] = field(default_factory=list)
    relay_area: tuple[float, float, float, float] = (0, 0, 0, 0)
    title_area: tuple[float, float, float, float] = (0, 0, 0, 0)
    params: dict[str, Any] = field(default_factory=dict)


def _font(path: str, size: int) -> Any:
    return None if path == "hershey" else ImageFont.truetype(path, size)


def _text(draw: ImageDraw.ImageDraw, image: Image.Image, xy: tuple[float, float], text: str, font: Any, size: int) -> tuple[float, float, float, float]:
    """文字を書き、インクの枠を返す。font が None なら Hershey（線の字体）で書く。"""
    x, y = xy
    if font is None:
        import cv2
        import numpy as np

        scale = 0.72 * size / 22.0  # 英大文字の高さを TrueType の字と同じくらい（文字の大きさの 7 割）にする
        (tw, th), _ = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, scale, 1)
        arr = np.asarray(image).copy()
        cv2.putText(arr, text, (int(x), int(y + th)), cv2.FONT_HERSHEY_SIMPLEX, scale, (0, 0, 0), 1, cv2.LINE_AA)
        image.paste(Image.fromarray(arr))
        return (x, y, x + tw, y + th)
    draw.text((x, y), text, font=font, fill="black")
    box = draw.textbbox((x, y), text, font=font)
    return tuple(float(v) for v in box)  # type: ignore[return-value]


def _sheet(rng: random.Random) -> str:
    return rng.choice(["本", "本", f"EV3103{rng.randint(1, 9)}", f"ER2{rng.randint(1000, 1399)}"])


def _position(rng: random.Random) -> str:
    return f"{rng.randint(1, 15)}{rng.choice('ABCDEFGH')}"


def make_table(seed: int) -> SyntheticTable:
    rng = random.Random(seed)
    size = rng.choice([10, 11, 12, 13])
    jp_path = rng.choice(JP_FONTS)
    ascii_path = rng.choice(ASCII_FONTS)
    jp, asc = _font(jp_path, size), _font(ascii_path, size)
    pitch = rng.choice([size + 6, size + 7, size + 8])
    # 欄の幅は、その列に書く一番長い文字が収まる幅（実図面の表と同じく、字は罫線の内側に収まる）
    measure = Image.new("RGB", (10, 10))
    longest = [["器具番号", "FV2306A", "P33P22"], ["端子", "8-12-4"], ["接点", "A", "B"], ["シート", "EV31038", "ER21399"],
               ["行列", "15H"]]
    widths = []
    for texts in longest:
        widest = 0.0
        for text in texts:
            font = jp if any(ord(c) > 127 for c in text) else asc
            box = _text(ImageDraw.Draw(measure), measure, (0, 0), text, font, size)
            widest = max(widest, box[2] - box[0])
        widths.append(int(widest) + rng.randint(7, 14))
    cols = [0]
    for w in widths:
        cols.append(cols[-1] + w)
    image = Image.new("RGB", (W, H), "white")
    draw = ImageDraw.Draw(image)
    draw.rectangle((20, 20, W - 20, H - 20), outline="black")
    table = SyntheticTable(image=image, params={"seed": seed, "size": size, "jp": jp_path, "ascii": ascii_path, "pitch": pitch})
    x0s = [60, 60 + cols[-1] + rng.randint(30, 50)]
    top = 900
    used = rng.sample(DEVICE_HEADS, 4)
    for bx_index, bx in enumerate(x0s):
        y = top
        for stack in range(2):
            head, model = used[bx_index * 2 + stack], rng.choice(MODELS)
            rows = 4
            height = pitch * (rows + 1)
            draw.rectangle((bx, y, bx + cols[-1], y + height), outline="black")
            for c in cols[1:-1]:
                draw.line((bx + c, y, bx + c, y + height), fill="black")
            draw.line((bx, y + pitch, bx + cols[-1], y + pitch), fill="black")
            for i, label in enumerate(HEADERS):
                box = _text(draw, image, (bx + cols[i] + 3, y + (pitch - size) / 2 - 1), label, jp, size)
                table.cells.append(Cell(label, box, "header"))
            used_rows = rng.randint(1, 3)
            for r in range(rows):
                ty = y + pitch * (r + 1)
                if r:
                    draw.line((bx + cols[1], ty, bx + cols[-1], ty), fill="black")
                terminal = TERMINALS[r]
                values = {"terminal": terminal, "contact": "", "sheet": "", "position": ""}
                if r < used_rows:
                    values.update(contact=rng.choice("AB"), sheet=_sheet(rng), position=_position(rng))
                for i, kind in enumerate(("terminal", "contact", "sheet", "position"), start=1):
                    text = values[kind]
                    if not text:
                        continue
                    font = jp if text == "本" else asc
                    box = _text(draw, image, (bx + cols[i] + 3, ty + (pitch - size) / 2 - 1), text, font, size)
                    table.cells.append(Cell(text, box, kind))
                table.relay_rows.append({
                    "device": f"{head} {model}", "terminal": terminal, "contact": values["contact"].lower(),
                    "sheet": values["sheet"], "position": values["position"],
                })
            mid = y + pitch + (rows * pitch) / 2
            for line, text in enumerate((head, model)):
                box = _text(draw, image, (bx + 4, mid - size - 2 + line * (size + 4)), text, asc, size)
                table.cells.append(Cell(text, box, "device"))
            y += height + rng.randint(4, 10)
        table.relay_area = (x0s[0] - 6, top - 6, x0s[1] + cols[-1] + 6, y + 6)
    # 表題欄
    tx0, ty0 = 1300, 1060
    key_w, row_h = 100, rng.choice([30, 34, 38])
    tx1, ty1 = 1730, ty0 + row_h * len(TITLE_KEYS)
    draw.rectangle((tx0, ty0, tx1, ty1), outline="black")
    draw.line((tx0 + key_w, ty0, tx0 + key_w, ty1), fill="black")
    values = [f"{rng.randint(10, 39)}-LNG-HP", rng.choice(["LNG高圧ポンプ", "主制御装置", "非常用発電機"]), f"EV3103{rng.randint(1, 9)}",
              rng.choice("ABCD")]
    for i, (key, value) in enumerate(zip(TITLE_KEYS, values, strict=True)):
        ry = ty0 + row_h * i
        if i:
            draw.line((tx0, ry, tx1, ry), fill="black")
        table.cells.append(Cell(key, _text(draw, image, (tx0 + 6, ry + (row_h - size) / 2 - 1), key, jp, size), "title_key"))
        font = jp if any(ord(c) > 127 for c in value) else asc
        table.cells.append(Cell(value, _text(draw, image, (tx0 + key_w + 8, ry + (row_h - size) / 2 - 1), value, font, size), "title_value"))
    table.title_area = (tx0, ty0, tx1, ty1)
    table.image = _degrade(image, rng, table.params)
    return table


def _degrade(image: Image.Image, rng: random.Random, params: dict[str, Any]) -> Image.Image:
    """スキャンの劣化: 縮小して戻す・ぼかし・ノイズ・JPEG。種ごとに強さを変える。"""
    level = rng.choice(["clean", "light", "scan", "scan"])
    params["degrade"] = level
    if level == "clean":
        return image
    if level == "scan":
        factor = rng.uniform(0.8, 0.92)
        small = image.resize((int(image.width * factor), int(image.height * factor)), Image.Resampling.BILINEAR)
        image = small.resize(image.size, Image.Resampling.BILINEAR)
    image = image.filter(ImageFilter.GaussianBlur(rng.uniform(0.3, 0.6)))
    import numpy as np

    arr = np.asarray(image.convert("L")).astype(np.float32)
    arr += np.random.default_rng(rng.randint(0, 10_000)).normal(0, 6 if level == "scan" else 4, arr.shape)
    image = Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8)).convert("RGB")
    buf = io.BytesIO()
    image.save(buf, format="JPEG", quality=rng.randint(60, 85))
    return Image.open(io.BytesIO(buf.getvalue())).convert("RGB")
