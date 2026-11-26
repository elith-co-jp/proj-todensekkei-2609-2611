"""図面に書かれた文字から、記号の属性（動き方と名前）を推定する（DD-0050）。

属性は論理図・シミュレーションの動き方を決める（DD-0045）。人が入れていた次の 4 つを、図面の文字から候補として入れる。

| 属性 | 図面の書き方（紙芝居 EV31031〜EV31036） | 推定 |
|---|---|---|
| タイマー（timer_mode・delay_s） | コイルの右に「1sec」と設定範囲「(0〜12sec)」、母線の下に型式「TDPU」 | 秒は右の文字、種類は型式（TDPU = オンディレー、TDDO = オフディレー） |
| キープリレー（latch_coil） | 並んだ 2 つの丸の中に「O」「R」、器具番号「43LK P33P22」は 2 つの上に 1 つ | 丸の中の O / S / SET = セット、R / RESET = リセット。対の片方だけ読めたら残りは逆 |
| 信号名（signal_name） | 母線の上に縦書きで回路の名前（「起動指令（個別制御装置）」） | 同じ縦の列の外部入力の接点、無ければコイル |
| 外部入力（external_io） | I/O カードの接点「D001 B405」 | 器具番号が I/O の点番号（D + 3 桁）の接点 |

推定した属性は候補なので、人が確かめる。決めきれなかったもの（種類を秒の書き方から決めた、対の片方から決めた、名前の文字が
崩れているかもしれない）は review に理由を残す。I/O 表がある場合は services.io_table が名前と外部入力を上書きする。
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

from analysis import text_extraction as tx

COIL_CLASSES = frozenset({"relay_coil", "solenoid", "lamp"})
CONTACT_CLASSES = frozenset({"contact_a", "contact_b", "push_button"})

# --- タイマー ---------------------------------------------------------------------------------------------------
# 秒の書き方。OCR は 1 を I・l、0 を O と読むので、数字の部分だけ寄せる（「Isec」→ 1 秒）。
_SECONDS = re.compile(r"^([0-9ILO]{1,3}(?:\.[0-9O]{1,2})?)(SEC|S|秒)$")
# 設定範囲「(0-12sec)」「0〜12sec」は時間ではないので先に除く。
_RANGE = re.compile(r"\(?[0-9ILO.]+\s*[-~〜～]\s*[0-9ILO.]+\s*(?:SEC|S|秒)?\)?")
# 型式・注記から種類。OCR は TDPU を TOPU・T0PU と読む。
_ON_DELAY = re.compile(r"T[DO0Q]P[UV]|ONDELAY|オンディレ|限時動作")
_OFF_DELAY = re.compile(r"T[DO0Q][DO0Q]{2}|OFFDELAY|オフディレ|限時復帰")
# 秒の文字を探す範囲（コイルの枠の大きさに対する比）。右に 3 個分、上下に半分。
TIMER_RIGHT = 3.0
TIMER_VERTICAL = 0.6
# 型式（TDPU）を探す範囲。コイルの列（左右 1.5 個分）で、コイルの下 2.5 個分まで（母線の下に書く）。
MODE_COLUMN = 1.5
MODE_BELOW = 2.5

# --- キープリレー ---------------------------------------------------------------------------------------------------
_SET_MARKS = {"O", "0", "S", "SET", "セット"}
_RESET_MARKS = {"R", "RESET", "リセット"}
# 対のコイル: 同じ高さ（高さの 0.3 倍まで）で、枠どうしの隙間がコイルの幅の 2.5 倍以下（紙芝居の 43LK は約 1.7 倍）。
PAIR_GAP = 2.5
PAIR_ROW = 0.3

# --- 信号名 ---------------------------------------------------------------------------------------------------
# 縦書きの名前の列と記号の列のずれ（px、150 dpi）。名前は回路の線のすぐ左に書く。
CAPTION_COLUMN_PX = 24.0
# 名前の上下の行（2 列の縦書き）を 1 つにまとめる左右の隙間（文字幅に対する比）。
CAPTION_JOIN = 1.8
# I/O カードの点番号（D001）。他盤からの入力。
_IO_POINT = re.compile(r"^D\d{3}$")


@dataclass
class Evidence:
    attribute: str
    value: Any
    text: str
    box: tuple[float, float, float, float] | None
    review: str | None = None


@dataclass
class SymbolGuess:
    attributes: dict[str, Any] = field(default_factory=dict)
    evidence: list[Evidence] = field(default_factory=list)

    def add(self, attribute: str, value: Any, text: str, box: Any = None, review: str | None = None) -> None:
        if attribute in self.attributes:
            return
        self.attributes[attribute] = value
        self.evidence.append(Evidence(attribute, value, text, tuple(box) if box is not None else None, review))

    @property
    def review(self) -> list[str]:
        return [e.review for e in self.evidence if e.review]

    def as_dict(self) -> dict[str, Any]:
        return {
            "attributes": dict(self.attributes),
            "evidence": [{"attribute": e.attribute, "value": e.value, "text": e.text,
                          "box": [round(v, 1) for v in e.box] if e.box else None, "review": e.review}
                         for e in self.evidence],
            "review": self.review,
        }


def _compact(text: str) -> str:
    return tx.normalize(text).replace(" ", "")


def _box(symbol: dict[str, Any]) -> tx.Box:
    return tx._as_box(symbol["bbox"])


def _gap(a: tx.Box, b: tx.Box) -> float:
    dx = max(0.0, b[0] - a[2], a[0] - b[2])
    dy = max(0.0, b[1] - a[3], a[1] - b[3])
    return (dx * dx + dy * dy) ** 0.5


def read_seconds(text: str) -> float | None:
    """「1sec」「Isec」「0.5S」「2秒」→ 秒。設定範囲（0-12sec）だけの文字は None。"""
    compact = _RANGE.sub("", _compact(text))
    match = _SECONDS.match(compact)
    if match is None:
        return None
    number = match.group(1).replace("I", "1").replace("L", "1").replace("O", "0")
    try:
        value = float(number)
    except ValueError:
        return None
    return value if 0 < value <= 3600 else None


def read_timer_mode(text: str) -> str | None:
    compact = _compact(text).replace("-", "")
    if _OFF_DELAY.search(compact):
        return "off_delay"
    if _ON_DELAY.search(compact):
        return "on_delay"
    return None


def device_of(label: str | None) -> str:
    """器具番号（ラベルの先頭の語。前に付いた電線番号・端子番号は飛ばす）の照合キー。"""
    words = [w for w in tx.normalize(label or "").split() if not re.fullmatch(r"\(?[A-Z]{1,3}\d{5}(?:-\d{1,2})?\)?", w)]
    while len(words) > 1 and words[0].isdigit():
        words = words[1:]
    return tx.match_key(words[0]) if words else ""


def _timer_guesses(items: list[tx.TextItem], coils: list[dict[str, Any]], guesses: dict[str, SymbolGuess]) -> None:
    timer_coils = [c for c in coils if (c.get("class_name") or "") == "relay_coil"]
    if not timer_coils:
        return
    boxes = {str(c["id"]): _box(c) for c in timer_coils}
    seconds: dict[str, tuple[float, float, dict[str, Any]]] = {}
    modes: dict[str, tuple[float, str, dict[str, Any]]] = {}
    for item in items:
        ibox = tx._as_box(item["box"])
        icx, icy = tx._center(ibox)
        if any(tx._inside((icx, icy), b) for b in boxes.values()):
            continue
        value = read_seconds(item["text"])
        mode = read_timer_mode(item["text"])
        if value is None and mode is None:
            continue
        best_sec: tuple[float, str] | None = None
        best_mode: tuple[float, str] | None = None
        for sid, (x0, y0, x1, y1) in boxes.items():
            w, h = max(1.0, x1 - x0), max(1.0, y1 - y0)
            # 時間はコイルの右に書く。文字の左端がコイルの中心より左なら、左のコイルの文字。
            if value is not None and ibox[0] >= (x0 + x1) / 2 and icx <= x1 + TIMER_RIGHT * w \
                    and y0 - TIMER_VERTICAL * h <= icy <= y1 + TIMER_VERTICAL * h:
                d = _gap((x0, y0, x1, y1), ibox)
                if best_sec is None or d < best_sec[0]:
                    best_sec = (d, sid)
            if mode is not None and abs(icx - (x0 + x1) / 2) <= MODE_COLUMN * w and y0 - h <= icy <= y1 + MODE_BELOW * h:
                d = abs(icx - (x0 + x1) / 2) + max(0.0, icy - y1)
                if best_mode is None or d < best_mode[0]:
                    best_mode = (d, sid)
        if best_sec is not None and (best_sec[1] not in seconds or best_sec[0] < seconds[best_sec[1]][0]):
            seconds[best_sec[1]] = (best_sec[0], value, item)  # type: ignore[assignment]
        if best_mode is not None and (best_mode[1] not in modes or best_mode[0] < modes[best_mode[1]][0]):
            modes[best_mode[1]] = (best_mode[0], mode, item)  # type: ignore[assignment]
    for sid in sorted(set(seconds) | set(modes)):
        guess = guesses.setdefault(sid, SymbolGuess())
        if sid in modes:
            _d, mode, item = modes[sid]
            guess.add("timer_mode", mode, item["text"], item["box"])
        else:
            _d, _v, item = seconds[sid]
            guess.add("timer_mode", "on_delay", item["text"], item["box"],
                      review="タイマーの種類は型式が読めず、時間の書き方からオンディレーとした")
        if sid in seconds:
            _d, value, item = seconds[sid]
            guess.add("delay_s", value, item["text"], item["box"])
        else:
            guess.evidence.append(Evidence("delay_s", None, "", None, review="タイマーの時間が読めない"))


def _latch_mark(items: Iterable[tx.TextItem], box: tx.Box) -> tuple[str, dict[str, Any]] | None:
    for item in items:
        if not tx._inside(tx._center(tx._as_box(item["box"])), box, 2.0):
            continue
        compact = unicodedata.normalize("NFKC", item["text"]).upper().replace(" ", "")
        if compact in _SET_MARKS:
            return "set", item
        if compact in _RESET_MARKS:
            return "reset", item
    return None


def _same_row(a: tx.Box, b: tx.Box) -> float | None:
    """同じ高さに並ぶ 2 つのコイルの枠の隙間（並んでいなければ None）。"""
    h = max(1.0, a[3] - a[1])
    w = max(1.0, a[2] - a[0])
    if abs(tx._center(a)[1] - tx._center(b)[1]) > PAIR_ROW * h:
        return None
    gap = max(b[0] - a[2], a[0] - b[2])
    return gap if -0.2 * w <= gap <= PAIR_GAP * w else None


def _latch_guesses(items: list[tx.TextItem], coils: list[dict[str, Any]], crop_items: dict[str, list[tx.TextItem]],
                   guesses: dict[str, SymbolGuess], labels: dict[str, dict[str, Any]]) -> None:
    """丸の中に O / R を読めたコイルを、同じ高さで隣のコイルと対にする（逆の印、近い順）。"""
    relay = [c for c in coils if (c.get("class_name") or "") == "relay_coil"]
    marks: dict[str, tuple[str, dict[str, Any]]] = {}
    for c in relay:
        found = _latch_mark([*crop_items.get(str(c["id"]), []), *items], _box(c))
        if found is not None:
            marks[str(c["id"])] = found
    paired: set[str] = set()
    for a in sorted((c for c in relay if str(c["id"]) in marks), key=lambda c: _box(c)[0]):
        ia = str(a["id"])
        if ia in paired:
            continue
        best = None
        for b in relay:
            ib = str(b["id"])
            if ib == ia or ib in paired:
                continue
            gap = _same_row(_box(a), _box(b))
            la, lb = device_of(a.get("label")), device_of(b.get("label"))
            if gap is None or (la and lb and la != lb):
                continue
            opposite = ib in marks and marks[ib][0] != marks[ia][0]
            same = ib in marks and not opposite
            if same:
                continue
            rank = (0 if opposite else 1, gap)
            if best is None or rank < best[0]:
                best = (rank, b)
        if best is None:
            continue
        b = best[1]
        ib = str(b["id"])
        paired.update((ia, ib))
        for this in (ia, ib):
            if this in marks:
                role, item = marks[this]
                guesses.setdefault(this, SymbolGuess()).add("latch_coil", role, item["text"], item["box"])
        if ib not in marks:
            role = "reset" if marks[ia][0] == "set" else "set"
            guesses.setdefault(ib, SymbolGuess()).add(
                "latch_coil", role, marks[ia][1]["text"], marks[ia][1]["box"],
                review="キープリレーの対の片方の印が読めず、もう片方の逆とした")
        # 器具番号は 2 つの丸の上に 1 つだけ書く。結べなかった方に、対のコイルの器具番号を入れる。
        for this, other in ((a, b), (b, a)):
            if not (this.get("label") or "").strip() and (other.get("label") or "").strip():
                labels[str(this["id"])] = {"label": other["label"], "label_box": other.get("label_box"),
                                           "review": "ラベルはキープリレーの対のコイルから"}
    # 対にならなかったコイルの中の O / R は、キープリレーと決められない（表示灯の印などと区別できない）ので使わない。


def top_bus_y(items: list[tx.TextItem], symbols: list[dict[str, Any]]) -> float | None:
    """上の制御母線の高さ。母線の名前（PEV110500・PER00001）の行か、電源母線端の記号から。"""
    ys = [tx._center(_box(s))[1] for s in symbols if (s.get("class_name") or "") == "power_bus"]
    for item in items:
        if re.fullmatch(r"P[A-Z]{1,3}\d{3,}", _compact(item["text"])):
            ys.append(tx._as_box(item["box"])[3])
    if ys:
        return min(ys)
    bodies = [_box(s)[1] for s in symbols if (s.get("class_name") or "") in COIL_CLASSES | CONTACT_CLASSES]
    return min(bodies) if bodies else None


@dataclass
class Caption:
    text: str
    box: tx.Box

    @property
    def x(self) -> float:
        return tx._center(self.box)[0]


def captions(texts: list[dict[str, Any]], top_y: float, page_height: float) -> list[Caption]:
    """母線より上の日本語の名前（縦書き）を、縦の列ごとにまとめる。2 列の縦書き（名前と（装置））は 1 つにする。"""
    parts = []
    for t in texts:
        text = (t.get("text") or "").strip()
        box = tx._as_box(t["box"])
        if not text or not tx._has_cjk(text) or t.get("zone"):
            continue
        if not (0.04 * page_height < tx._center(box)[1] < top_y + 4):
            continue
        if (box[3] - box[1]) < 1.5 * (box[2] - box[0]):
            continue  # 横書きの文字（表の見出し・注記）は名前にしない
        parts.append(Caption(text, box))
    parts.sort(key=lambda c: c.box[0])
    groups: list[list[Caption]] = []
    for part in parts:
        width = max(4.0, part.box[2] - part.box[0])
        if groups and part.box[0] - groups[-1][-1].box[2] <= CAPTION_JOIN * width \
                and part.box[1] <= max(c.box[3] for c in groups[-1]) and part.box[3] >= min(c.box[1] for c in groups[-1]):
            groups[-1].append(part)
        else:
            groups.append([part])
    out = []
    for group in groups:
        # 括弧で始まる列（（個別制御装置））は後ろ、それ以外は左から。
        ordered = sorted(group, key=lambda c: (c.text.lstrip().startswith(("(", "（")), c.box[0]))
        text = "".join(unicodedata.normalize("NFKC", c.text).replace(" ", "") for c in ordered)
        box = (min(c.box[0] for c in group), min(c.box[1] for c in group),
               max(c.box[2] for c in group), max(c.box[3] for c in group))
        out.append(Caption(text, box))
    return out


def is_io_point(label: str | None) -> bool:
    words = tx.normalize(label or "").split()
    return bool(words) and _IO_POINT.fullmatch(words[0].replace("O", "0")) is not None


def _signal_guesses(texts: list[dict[str, Any]], symbols: list[dict[str, Any]], top_y: float | None, page_height: float,
                    guesses: dict[str, SymbolGuess]) -> list[dict[str, Any]]:
    if top_y is None:
        return []
    unused = []
    for caption in captions(texts, top_y, page_height):
        tol = max(CAPTION_COLUMN_PX, 1.5 * (caption.box[2] - caption.box[0]))
        column = [s for s in symbols if abs(tx._center(_box(s))[0] - caption.x) <= tol and _box(s)[1] > top_y]
        inputs = [s for s in column if (s.get("class_name") or "") in CONTACT_CLASSES and is_io_point(s.get("label"))]
        coils = [s for s in column if (s.get("class_name") or "") in COIL_CLASSES]
        if inputs:
            target = min(inputs, key=lambda s: _box(s)[1])
        elif coils:
            target = min(coils, key=lambda s: abs(tx._center(_box(s))[0] - caption.x))
        else:
            unused.append({"text": caption.text, "box": [round(v, 1) for v in caption.box]})
            continue
        guesses.setdefault(str(target["id"]), SymbolGuess()).add(
            "signal_name", caption.text, caption.text, caption.box,
            review="信号名は図面上部の縦書きの文字から（読み違いを確かめる）")
    return unused


def infer_attributes(
    items: list[tx.TextItem], symbols: list[dict[str, Any]], *, texts: list[dict[str, Any]] | None = None,
    crop_items: dict[str, list[tx.TextItem]] | None = None, page_height: float = 1241.0,
) -> dict[str, Any]:
    """記号 id ごとの属性の候補。

    items はページの OCR の行、symbols は構造 JSON の記号（label・label_box を読んだ後）、texts は classify_texts の結果
    （日本語の名前は読み直し後の文字を使う）、crop_items は記号 id → 枠の拡大 OCR の行（コイルの中の O・R）。
    返り値: {"symbols": {id: {"attributes", "evidence", "review"}}, "labels": {id: {"label", "label_box", "review"}},
    "captions": [どの記号にも結べなかった名前]}。
    """
    guesses: dict[str, SymbolGuess] = {}
    labels: dict[str, dict[str, Any]] = {}
    coils = [s for s in symbols if (s.get("class_name") or "") in COIL_CLASSES]
    _timer_guesses(items, coils, guesses)
    _latch_guesses(items, coils, crop_items or {}, guesses, labels)
    for s in symbols:
        if (s.get("class_name") or "") in CONTACT_CLASSES and is_io_point(s.get("label")):
            guesses.setdefault(str(s["id"]), SymbolGuess()).add("external_io", "yes", s.get("label") or "", s.get("label_box"))
    named = [{**s, "label": labels.get(str(s["id"]), {}).get("label", s.get("label"))} for s in symbols]
    unused = _signal_guesses(texts if texts is not None else _texts_of(items), named, top_bus_y(items, symbols), page_height, guesses)
    return {"symbols": {sid: g.as_dict() for sid, g in guesses.items()}, "labels": labels, "captions": unused}


def _texts_of(items: list[tx.TextItem]) -> list[dict[str, Any]]:
    return [{"text": i["text"], "box": list(i["box"])} for i in items]
