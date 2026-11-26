"""印だけを囲んだ記号に、近くに書かれた器具番号の文字を結び付ける（DD-0049）。

記号の枠は印だけを囲み、器具番号・型式・参照先の文字は枠に入れない（2026-11-26 の取り決め）。文字は図面ごとに
記号の右・上・下と書く位置が違うので、枠の中の文字を読むだけではラベルにならない。ここでは次の順で決める。

1. ページの文字（OCR の行）から、どの記号の枠にも入らない部分を取り出す（日本語の名称・端子番号だけの行は除く）。
2. 左端のそろった上下の行を 1 つの「文字のまとまり」にする（「83DX-1 / P33P22 / (EV31032)」）。
3. 記号とまとまりの組ごとに、枠どうしの隙間と向き（接点は右、コネクタは上か右、など）から距離を出す。
4. 距離の近い組から順に 1 対 1 で結ぶ。1 つのまとまりは 1 つの記号にしか付けない。
5. 2 番目に近い候補との差が小さいもの（記号の間にある文字など）は「要確認」として残し、人が画面で選ぶ。
"""

from __future__ import annotations

import re
import unicodedata
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any, Literal

from analysis import text_extraction as tx

# 器具番号を書く記号。分岐点・電源母線端は名前を持たず、「その他」（行き先の札）は札の文字まで囲む（DD-0017）。
LINK_CLASSES = frozenset({
    "relay_coil", "contact_a", "contact_b", "push_button", "solenoid", "lamp", "diode", "connector", "terminal",
})
# 枠と文字のまとまりの隙間がこれより大きい組は結ばない（150 dpi で文字 2〜3 文字分）。
LINK_MAX_GAP_PX = 40.0
# 向きの重みを掛ける前に隙間へ足す長さ。
LINK_BASE_PX = 6.0
# 1 番目と 2 番目の候補の距離の差がこれより小さいと「要確認」。
LINK_AMBIGUOUS_PX = 6.0
# 同じまとまりにする上下の行の隙間（行の高さに対する比）と、左端のずれ（行の高さに対する比）。
BLOCK_LINE_GAP = 0.8
BLOCK_LEFT_ALIGN = 1.5

Direction = Literal["inside", "right", "left", "above", "below"]
LinkStatus = Literal["linked", "要確認", "未検出"]

# 向きごとの重み。小さいほどその向きに書くことが多い。接点の左は端子番号の位置なので重くする。
_CONTACT_WEIGHTS = {"inside": 1.0, "right": 1.0, "above": 1.4, "below": 1.6, "left": 2.5}
_COIL_WEIGHTS = {"inside": 1.0, "right": 1.0, "below": 1.3, "above": 1.3, "left": 1.6}
_TERMINAL_WEIGHTS = {"inside": 1.0, "above": 1.0, "right": 1.1, "left": 1.3, "below": 1.4}
DIRECTION_WEIGHTS: dict[str, dict[str, float]] = {
    "contact_a": _CONTACT_WEIGHTS, "contact_b": _CONTACT_WEIGHTS, "push_button": _CONTACT_WEIGHTS,
    "relay_coil": _COIL_WEIGHTS, "solenoid": _COIL_WEIGHTS, "lamp": _COIL_WEIGHTS, "diode": _COIL_WEIGHTS,
    "connector": _TERMINAL_WEIGHTS, "terminal": _TERMINAL_WEIGHTS,
}

_DIGITS = re.compile(r"\(?\d{1,4}\)?")
_SHEET_REF_ONLY = re.compile(rf"\(?{tx.SHEET_REF}\)?(?:へ|より)?")


@dataclass
class Line:
    text: str
    box: tx.Box
    score: float

    @property
    def height(self) -> float:
        return max(1.0, self.box[3] - self.box[1])


@dataclass
class Block:
    """左端のそろった上下の行。最初の行が器具番号のことが多い。"""

    lines: list[Line] = field(default_factory=list)

    @property
    def box(self) -> tx.Box:
        return (min(ln.box[0] for ln in self.lines), min(ln.box[1] for ln in self.lines),
                max(ln.box[2] for ln in self.lines), max(ln.box[3] for ln in self.lines))

    @property
    def text(self) -> str:
        return " ".join(ln.text for ln in self.lines)

    @property
    def has_name(self) -> bool:
        return any(_names_device(ln.text) for ln in self.lines)


def _has_cjk(text: str) -> bool:
    return any(unicodedata.east_asian_width(c) in "WF" for c in text)


def _names_device(text: str) -> bool:
    """器具番号・型式の行か（英字を含み、参照先だけ・数字だけの行ではない）。"""
    compact = tx.normalize(text).replace(" ", "")
    return (any(c.isalpha() for c in compact) and len(compact) >= 2
            and _SHEET_REF_ONLY.fullmatch(compact) is None and not _has_cjk(compact))


def _attachable(text: str) -> bool:
    """まとまりの 2 行目以降に付けてよい行（器具番号の行、参照先、端子番号・番号）。"""
    compact = tx.normalize(text).replace(" ", "")
    return bool(compact) and not _has_cjk(compact) and (
        _names_device(compact) or _SHEET_REF_ONLY.fullmatch(compact) is not None or _DIGITS.fullmatch(compact) is not None)


def outside_lines(items: Iterable[tx.TextItem], boxes: list[tx.Box]) -> list[Line]:
    """OCR の行から、どの記号の枠にも入らない部分を取り出す。枠をまたぐ行（「9 |83DX-1」）は枠の外の部分だけ残す。"""
    lines: list[Line] = []
    for item in items:
        run: list[tuple[str, tx.Box]] = []
        for char, char_box in [*tx._char_boxes(item), ("\0", item["box"])]:
            center = tx._center(char_box)
            if char == "\0" or any(tx._inside(center, b) for b in boxes):
                text = "".join(c for c, _ in run).strip()
                kept = [(c, b) for c, b in run if not c.isspace()]
                if text and kept:
                    lines.append(Line(text, (kept[0][1][0], min(b[1] for _, b in kept), kept[-1][1][2],
                                             max(b[3] for _, b in kept)), float(item["score"])))
                run = []
            else:
                run.append((char, char_box))
    return lines


# OCR は記号の左に書いた隣の端子番号を、前の行の末尾にくっつけて読む（「(EV31032)8」「P33P22 12」）。
_TRAILING_TERMINAL = re.compile(r"^(.*(?:\)|\S\s))\s*(\d{1,2})$")


def strip_trailing_terminal(line: Line) -> Line:
    """行末にくっついた端子番号（1〜2 桁）を落とし、行の枠もその分だけ縮める。"""
    match = _TRAILING_TERMINAL.match(line.text)
    if match is None or not _attachable(match.group(1)) or _DIGITS.fullmatch(match.group(1).strip()):
        return line
    head = match.group(1).rstrip()
    x0, y0, x1, y1 = line.box
    keep = len(head) / max(1, len(line.text))
    return Line(head, (x0, y0, x0 + (x1 - x0) * keep, y1), line.score)


def build_blocks(lines: list[Line]) -> list[Block]:
    """左端のそろった上下の行をまとめる。器具番号・型式の行を含まないまとまり（番号・参照先だけ）は捨てる。"""
    blocks: list[Block] = []
    for line in sorted((strip_trailing_terminal(ln) for ln in lines), key=lambda ln: (ln.box[1], ln.box[0])):
        if not _attachable(line.text):
            continue
        best = None
        for block in blocks:
            last = block.lines[-1]
            gap = line.box[1] - last.box[3]
            h = min(line.height, last.height)
            aligned = abs(line.box[0] - block.lines[0].box[0]) <= BLOCK_LEFT_ALIGN * h
            if -0.5 * h <= gap <= BLOCK_LINE_GAP * h and aligned and (best is None or gap < best[0]):
                best = (gap, block)
        if best is not None:
            best[1].lines.append(line)
        else:
            blocks.append(Block([line]))
    # 数字だけの器具番号（80）や、小さな D を 0 と読んだ器具番号（5D → 50）があるので、上の数字の行も残す。
    return [block for block in blocks if block.has_name]


def direction_and_gap(symbol_box: tx.Box, block_box: tx.Box) -> tuple[Direction, float]:
    """記号の枠から見た文字のまとまりの向きと、枠どうしの隙間（接したり重なったりすれば 0）。

    まとまりの中心が枠の中なら「inside」（コイルの丸の中の名前）。それ以外は中心どうしの向きで決める。
    """
    sx0, sy0, sx1, sy1 = symbol_box
    bx0, by0, bx1, by1 = block_box
    dx = max(0.0, bx0 - sx1, sx0 - bx1)
    dy = max(0.0, by0 - sy1, sy0 - by1)
    scx, scy = tx._center(symbol_box)
    bcx, bcy = tx._center(block_box)
    if tx._inside((bcx, bcy), symbol_box):
        return "inside", 0.0
    gap = (dx * dx + dy * dy) ** 0.5
    if dx > dy or (dx == dy and abs(bcx - scx) / max(1.0, sx1 - sx0 + bx1 - bx0) >= abs(bcy - scy) / max(1.0, sy1 - sy0 + by1 - by0)):
        return ("right" if bcx > scx else "left"), gap
    return ("below" if bcy > scy else "above"), gap


# 「その他」の枠のうち、盤・カードの外形のような大きな枠は文字を持たない（中の文字は中の記号のもの）。
OWNER_OTHER_MAX_PX = 200.0


def owner_boxes(symbols: list[dict[str, Any]]) -> list[tx.Box]:
    """文字を自分のものにする枠: 器具番号を書く記号と、小さな「その他」（行き先の札など）。"""
    out = []
    for s in symbols:
        box = tx._as_box(s["bbox"])
        cls = s.get("class_name") or ""
        if cls in LINK_CLASSES or (cls == "other" and max(box[2] - box[0], box[3] - box[1]) <= OWNER_OTHER_MAX_PX):
            out.append(box)
    return out


def link_cost(class_name: str, symbol_box: tx.Box, block: Block) -> tuple[float, Direction, float]:
    """(距離, 向き, 隙間)。距離は隙間に向きの重みを掛け、最初の行の中心のずれを少し足したもの。"""
    direction, gap = direction_and_gap(symbol_box, block.box)
    weights = DIRECTION_WEIGHTS.get(class_name, _COIL_WEIGHTS)
    first = block.lines[0].box
    if direction in ("right", "left", "inside"):
        # 右・左の文字は、最初の行が記号の上端〜中ほどに並ぶ。
        offset = max(0.0, symbol_box[1] - first[3], first[1] - tx._center(symbol_box)[1])
    else:
        offset = max(0.0, symbol_box[0] - first[2], first[0] - symbol_box[2])
    # 接した文字（隙間 0）でも向きの差が出るよう、隙間に LINK_BASE_PX を足してから重みを掛ける。
    return (gap + LINK_BASE_PX) * weights[direction] - LINK_BASE_PX + 0.5 * offset, direction, gap


def trim_glued(token: str, lexicon: Counter) -> str:
    """語の前後にくっついた接点の棒（I・1）と端子番号（1〜2 桁）を、図面の中でより多く読めている語に合わせて落とす。

    「IP33P228」は「P33P22」＋端子番号 8、「183DX-112」は棒＋「83DX-1」＋端子番号 12。
    """
    own = lexicon.get(token, 0)
    best, best_n = token, own
    for head in range(2):
        for tail in range(3):
            if head == tail == 0 or len(token) - head - tail < 3:
                continue
            if head and token[0] not in "I1|L":
                continue
            if tail and not token[len(token) - tail:].isdigit():
                continue
            core = token[head:len(token) - tail]
            n = lexicon.get(core, 0)
            if n >= 2 and n > 2 * best_n and any(c.isalpha() for c in core):
                best, best_n = core, n
    return best


def label_text(block: Block, class_name: str, lexicon: Counter | None) -> str:
    """まとまりの文字を記号のラベルにする（端の記号の読み違いを落とし、図面の中の多い語に直す）。"""
    tokens = [t for t in (tx._clean_token(w) for w in tx.normalize(block.text).split()) if t]
    if lexicon is not None:
        tokens = [tx.correct_token(trim_glued(t, lexicon), lexicon) for t in tokens]
    if class_name in tx.RELAY_REF_CLASSES:
        tokens = [tx.repair_device_token(t) for t in tokens]
    return " ".join(tokens)


def link_labels(
    items: list[tx.TextItem], symbols: list[dict[str, Any]], *, lexicon: Counter | None = None,
    zones: list[dict[str, Any]] | None = None, max_gap: float = LINK_MAX_GAP_PX,
) -> dict[str, dict[str, Any]]:
    """記号 id ごとに、結び付けた文字のまとまりを返す。

    返り値: {symbol id: {"label", "label_box": [x0,y0,x1,y1], "status", "direction", "gap", "candidates"}}。
    結ぶ相手が無い記号は status「未検出」で label は空。
    """
    boxes = owner_boxes(symbols)
    lines = [ln for ln in outside_lines(tx.drop_glyph_marks(items, zones), boxes)
             if not (zones and tx.zone_of(ln.box, zones))]
    blocks = build_blocks(lines)
    targets = [s for s in symbols if (s.get("class_name") or "") in LINK_CLASSES]
    pairs: list[tuple[float, int, int, Direction, float]] = []
    costs: dict[int, list[tuple[float, int]]] = {}
    block_costs: dict[int, list[tuple[float, int]]] = {}
    for si, symbol in enumerate(targets):
        sbox = tx._as_box(symbol["bbox"])
        for bi, block in enumerate(blocks):
            cost, direction, gap = link_cost(symbol.get("class_name") or "", sbox, block)
            if gap > max_gap:
                continue
            pairs.append((cost, si, bi, direction, gap))
            costs.setdefault(si, []).append((cost, bi))
            block_costs.setdefault(bi, []).append((cost, si))
    used_symbols: set[int] = set()
    used_blocks: set[int] = set()
    chosen: dict[int, tuple[float, int, Direction, float]] = {}
    for cost, si, bi, direction, gap in sorted(pairs, key=lambda p: (p[0], p[1], p[2])):
        if si in used_symbols or bi in used_blocks:
            continue
        used_symbols.add(si)
        used_blocks.add(bi)
        chosen[si] = (cost, bi, direction, gap)
    owner = {bi: (cost, si) for si, (cost, bi, _d, _g) in chosen.items()}
    out: dict[str, dict[str, Any]] = {}
    for si, symbol in enumerate(targets):
        ranked = sorted(costs.get(si, []))
        candidates = [{"text": blocks[bi].text, "box": [round(v, 1) for v in blocks[bi].box], "cost": round(c, 1)}
                      for c, bi in ranked[:3]]
        if si not in chosen:
            out[str(symbol["id"])] = {"label": "", "label_box": None, "status": "未検出", "direction": None,
                                     "gap": None, "candidates": candidates}
            continue
        cost, bi, direction, gap = chosen[si]
        block = blocks[bi]
        # 他の記号がもっと近くで取ったまとまり・もっと近いまとまりを取った記号は、競り合いに数えない。
        rivals = [c for c, b in ranked if b != bi and c - cost < LINK_AMBIGUOUS_PX
                  and not (b in owner and owner[b][0] < c - LINK_AMBIGUOUS_PX)]
        other_symbols = [c for c, s in block_costs.get(bi, []) if s != si and c - cost < LINK_AMBIGUOUS_PX
                         and not (s in chosen and chosen[s][0] < c - LINK_AMBIGUOUS_PX)]
        ambiguous = bool(rivals or other_symbols)
        out[str(symbol["id"])] = {
            "label": label_text(block, symbol.get("class_name") or "", lexicon),
            "label_box": [round(v, 1) for v in block.box],
            "status": "要確認" if ambiguous else "linked",
            "direction": direction, "gap": round(gap, 1), "candidates": candidates,
            "line_height": round(min(ln.height for ln in block.lines), 1),
        }
    return out


def inside_label_is_name(label: str, class_name: str) -> bool:
    """枠の中から読んだラベルに器具番号（英字を含む語）があるか。数字だけ（端子番号・電線番号）なら名前ではない。"""
    del class_name
    return any(any(c.isalpha() for c in t) for t in tx.normalize(label).split())


# 接点・コイルのうち枠の中に器具番号が読めたものがこの割合以上なら、その図面は「印と名前を囲む」枠（DD-0017）とみなす。
NAMED_BOX_RATIO = 0.5


def link_targets(symbols: list[dict[str, Any]], inside_labels: dict[int, str]) -> list[dict[str, Any]]:
    """近くの文字をラベルに使う記号。inside_labels は id(記号) → 枠の中の文字から作ったラベル。

    - 印だけを囲んだ図面（接点・コイルの多くが枠の中に器具番号を持たない）: 枠の中に英字の語が無い記号すべて。
    - 印と名前を囲んだ図面: 枠の中が空の接点・コイルだけ。コネクタの枠の中の番号（13）は名前の一部なので結ばない。
    検出の枠の付け方は学習したモデルで決まるので、図面ごとに枠の中の文字から判断する。
    """
    relay = [s for s in symbols if (s.get("class_name") or "") in tx.RELAY_REF_CLASSES]
    named = sum(1 for s in relay if inside_label_is_name(inside_labels.get(id(s), ""), s.get("class_name") or ""))
    mark_only = not relay or named < NAMED_BOX_RATIO * len(relay)
    out = []
    for s in symbols:
        cls = s.get("class_name") or ""
        label = inside_labels.get(id(s), "")
        if cls not in LINK_CLASSES or inside_label_is_name(label, cls):
            continue
        if mark_only or (cls in tx.RELAY_REF_CLASSES and not label.strip()):
            out.append(s)
    return out


# ---------- 今の枠（印と名前）を、印だけの枠とラベルの位置に分ける ----------
INK_GRAY = 160
MARK_MARGIN_PX = 2


def label_words(items: list[tx.TextItem], box: Any, class_name: str) -> tuple[list[tx.TextItem], list[tx.TextItem]]:
    """枠の内側の語を (ラベルの語, それ以外の文字) に分ける。分け方は symbol_label と同じ（端子番号・端子名を除く）。"""
    words = tx.words_in_box(tx.drop_glyph_marks(items), box)
    band = tx._name_band(box) if class_name in tx.COIL_NAME_CLASSES else None
    keep, rest = [], []
    for w in words:
        token = tx._clean_token(tx.normalize(w["text"]))
        is_name = bool(token) and (
            class_name not in tx.TERMINAL_CLASSES or not tx._TERMINAL_NOISE.fullmatch(token)
            or (band is not None and tx._in_band(w["box"], band))
            or (class_name in tx.CONTACT_NAME_CLASSES and tx._contact_name_letter(token, w["box"], box)))
        (keep if is_name else rest).append(w)
    return keep, rest


def _continues_outside(gray: Any, mask: Any, crop: tuple[int, int, int, int], reach: int) -> bool:
    """枠の縁に掛かるつながりが、縁から reach 画素離れた所でもインクか（枠の外へ続く線か）。

    枠をきつく付けて縁に触れた文字は、縁の外へ数画素しか続かないので線にならない。
    """
    import numpy as np

    left, top, right, bottom = crop
    height, width = gray.shape[:2]
    ys, xs = np.nonzero(mask)
    probes = []
    if len(ys) == 0:
        return False
    half: list[tuple[int, int]] = []
    mid = max(2, reach // 2)
    if ys.min() == 0:
        probes += [(top - reach, left + x) for x in xs[ys == 0]]
        half += [(top - mid, left + x) for x in xs[ys == 0]]
    if ys.max() == mask.shape[0] - 1:
        probes += [(bottom - 1 + reach, left + x) for x in xs[ys == mask.shape[0] - 1]]
        half += [(bottom - 1 + mid, left + x) for x in xs[ys == mask.shape[0] - 1]]
    if xs.min() == 0:
        probes += [(top + y, left - reach) for y in ys[xs == 0]]
        half += [(top + y, left - mid) for y in ys[xs == 0]]
    if xs.max() == mask.shape[1] - 1:
        probes += [(top + y, right - 1 + reach) for y in ys[xs == mask.shape[1] - 1]]
        half += [(top + y, right - 1 + mid) for y in ys[xs == mask.shape[1] - 1]]
    def inked(py: int, px: int) -> bool:
        y0, y1, x0, x1 = max(0, py - 1), min(height, py + 2), max(0, px - 1), min(width, px + 2)
        return y0 < y1 and x0 < x1 and bool((np.asarray(gray[y0:y1, x0:x1]) < INK_GRAY).any())

    # 縁から半分の所と reach の所の両方がインクなら、線が続いている。
    return any(inked(hy, hx) and inked(py, px) for (py, px), (hy, hx) in zip(probes, half, strict=True))


def split_mark_box(
    gray: Any, box: Any, items: list[tx.TextItem], class_name: str, terminals: list[tuple[float, float]] | None = None,
) -> tuple[tx.Box, tx.Box | None]:
    """印と名前を囲んだ枠を、(印だけの枠, ラベルの文字の枠) に分ける。

    枠の中のインクをつながり（連結成分）に分け、1 文字分の大きさまでのつながりのうち、OCR の語の枠に入るもの・語と同じ行に
    あるもの（OCR が読み落とした 5D など）を文字とみなして除く。接点の棒（「○|○」の縦棒）は文字より背が高く、白丸は線と
    つながっているので印に残る（枠の縁に掛かるつながりは線として残す）。残ったインクの外側に MARK_MARGIN_PX の余白を付け、端子は必ず枠に入れる。
    ラベルの文字の枠は、ラベルの語と、その列にある読み落とした文字を囲む。
    gray は図面の濃さの画像（numpy の 2 次元配列、小さいほど濃い）。ラベルの語が無ければ枠はそのまま。
    """
    import cv2
    import numpy as np

    x0, y0, x1, y1 = tx._as_box(box)
    names, rest = label_words(items, box, class_name)
    if not names:
        return (x0, y0, x1, y1), None
    lx0, ly0 = min(w["box"][0] for w in names), min(w["box"][1] for w in names)
    lx1, ly1 = max(w["box"][2] for w in names), max(w["box"][3] for w in names)
    left, top = max(0, int(x0)), max(0, int(y0))
    right, bottom = min(gray.shape[1], int(np.ceil(x1))), min(gray.shape[0], int(np.ceil(y1)))
    ink = (np.asarray(gray[top:bottom, left:right]) < INK_GRAY).astype(np.uint8)
    count, labels, stats, _ = cv2.connectedComponentsWithStats(ink, connectivity=8)
    in_circle = class_name in tx.COIL_NAME_CLASSES
    words = [w for w in [*names, *rest] if not (in_circle and tx._in_band(w["box"], tx._name_band(box)))]
    char_h = float(np.median([w["box"][3] - w["box"][1] for w in words])) if words else 0.0
    kept: list[tuple[int, int, int, int]] = []
    for k in range(1, count):
        cx, cy, cw, ch = (int(v) for v in stats[k, :4])
        gx0, gy0, gx1, gy1 = left + cx, top + cy, left + cx + cw, top + cy + ch
        center = ((gx0 + gx1) / 2, (gy0 + gy1) / 2)
        if cw * ch <= 4:
            continue  # 2×2 画素までの点は読み込みのごみ。印にも文字にも数えない
        # 枠の外まで長く続くつながりは線（白丸とつながった配線）なので、小さくても印に残す。
        line = _continues_outside(gray, labels == k, (left, top, right, bottom), max(4, int(char_h)))
        small = ch <= 1.3 * char_h and cw <= 2.0 * char_h and not line
        in_word = any(tx._inside(center, w["box"], 1.0) for w in words)
        in_row = any(w["box"][1] - 2 <= center[1] <= w["box"][3] + 2 for w in words)
        # 端子（白丸）から離れた小さなつながりは、OCR が読まなかった端子番号などの文字。
        far = bool(terminals) and all(abs(center[0] - tx_) + abs(center[1] - ty_) > 1.5 * char_h for tx_, ty_ in terminals)
        if not (small and (in_word or in_row or far)):
            kept.append((gx0, gy0, gx1, gy1))
        elif not in_word and lx0 - char_h <= center[0] <= lx1 + char_h and not in_circle:
            # 読み落とした文字のうちラベルの列にあるもの（器具番号の行の 5D など）はラベルの枠に入れる。
            lx0, ly0, lx1, ly1 = min(lx0, gx0), min(ly0, gy0), max(lx1, gx1), max(ly1, gy1)
    label_box = (float(lx0), float(ly0), float(lx1), float(ly1))
    if not kept:
        return (x0, y0, x1, y1), label_box
    mx0 = min(k[0] for k in kept) - MARK_MARGIN_PX
    my0 = min(k[1] for k in kept) - MARK_MARGIN_PX
    mx1 = max(k[2] for k in kept) + MARK_MARGIN_PX
    my1 = max(k[3] for k in kept) + MARK_MARGIN_PX
    for tx_, ty_ in terminals or []:
        mx0, my0 = min(mx0, tx_ - MARK_MARGIN_PX), min(my0, ty_ - MARK_MARGIN_PX)
        mx1, my1 = max(mx1, tx_ + MARK_MARGIN_PX), max(my1, ty_ + MARK_MARGIN_PX)
    mark = (max(x0, float(mx0)), max(y0, float(my0)), min(x1, float(mx1)), min(y1, float(my1)))
    return mark, label_box
