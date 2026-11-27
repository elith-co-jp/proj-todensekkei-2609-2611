"""表の欄を 1 つずつ切り出して読み直し、欄の書式で読みを選んで直す（DD-0051 §6）。

ページ全体の OCR は、表の小さな文字を次のように読み違える（合成の図面で測った。analysis/tools/eval_table_reading.py）。

- 罫線をまたいで隣の欄とつながる（「器具番号端子」「接点シート」「P33P117-11-3」）。
- 1 文字だけの欄（接点の A・B、シートの「本」）や太字の見出しを読み落とす。
- 0 と O・D、1 と I などを取り違える（「1OD」「1 1D」）。

ここでは格子（analysis/table_grid.py）の欄ごとに画像を切り出し、罫線と欄の外にはみ出した字を消して、拡大して文字の認識だけを
かける（文字の検出をしないので 1 文字でも読める）。読みはページの OCR と比べ、列の書式（端子 5-9-1・接点 A/B・
シート EV31031/本・行列 14D、表題欄の項目名）に合うほう、合わなければ確からしさの高いほうを選び、書式に合わせて直す。
"""

from __future__ import annotations

import difflib
import re
import unicodedata
from typing import Any

from PIL import Image

from analysis.text_extraction import SHEET_REF, correct_token, link_relay_table, parse_relay_table

RELAY_HEADERS = ("器具番号", "端子", "接点", "シート", "行列")
TITLE_KEYS = ("設備記号", "部分単位", "シート番号", "名称", "改訂", "図番", "件名")
# 欄を切り出すときの拡大後の文字の高さ（px）と、欄の外側に足す余白（ページの px）
TARGET_HEIGHT = 40
PAD = 3
# 見出しの語に寄せる似かたの下限（difflib の比）
HEADER_RATIO = 0.5

_TERMINAL = re.compile(r"^\d{1,2}-\d{1,2}-\d{1,2}$")
_POSITION = re.compile(r"^\d{1,2}[A-HJ-NP-Z]$")
_SHEET = re.compile(rf"^(?:{SHEET_REF}|本)$")
_CONTACT = re.compile(r"^[AB]$")
_DEVICE = re.compile(r"^(?=.*\d)(?=.*[A-Z])[A-Z0-9/-]{2,12}$|^\d{1,3}$")
FORMATS: dict[str, re.Pattern[str]] = {
    "terminal": _TERMINAL, "contact": _CONTACT, "sheet": _SHEET, "position": _POSITION, "device": _DEVICE,
}


def norm(text: str) -> str:
    return unicodedata.normalize("NFKC", text or "").replace(" ", "").replace("　", "").upper()


# ------------------------------------------------------------------------------------------- 書式で直す
_DIGIT_LIKE = str.maketrans({"O": "0", "Q": "0", "D": "0", "I": "1", "L": "1", "|": "1", "!": "1", "Z": "2", "S": "5",
                             "B": "8", "G": "6", "T": "7"})


def _digits(text: str) -> str:
    return text.translate(_DIGIT_LIKE)


def fix(text: str, kind: str) -> str:
    """列の書式に合わせて読みを直す。直せなければそのまま（正規化だけ）返す。"""
    value = norm(text).replace("~", "-").replace("_", "-").replace("ー", "-").replace("—", "-").replace("−", "-")
    if kind == "terminal":
        parts = [p for p in re.split(r"-+", value) if p]
        if len(parts) == 3:
            value = "-".join(_digits(p) for p in parts)
        elif re.fullmatch(r"[0-9OIL|]{3,6}", value):
            value = _digits(value)
        return value
    if kind == "contact":
        mapping = {"8": "B", "3": "B", "R": "B", "4": "A", "Λ": "A", "Д": "A"}
        return mapping.get(value, value)
    if kind == "position":
        match = re.fullmatch(r"([0-9OIL|]{1,2})([A-Z0-9])", value)
        if match:
            head, tail = _digits(match.group(1)), match.group(2)
            tail = {"0": "D", "8": "B", "6": "G", "5": "S", "2": "Z", "1": "I"}.get(tail, tail)
            value = head + tail
        return value
    if kind == "sheet":
        if value in {"木", "末", "未", "本"}:
            return "本"
        match = re.fullmatch(r"([A-Z]{1,3})([0-9OIL|]{5})", value)
        if match:
            prefix = match.group(1).replace("0", "O")
            value = prefix + _digits(match.group(2))
        return value
    return value


_KANA_LIKE = str.maketrans({"卜": "ト", "一": "ー", "-": "ー", "—": "ー", "口": "ロ", "工": "エ", "夕": "タ", "力": "カ"})


def snap(text: str, words: tuple[str, ...]) -> str | None:
    """見出し・項目名を決まった語に寄せる（形の似た漢字とかなはそろえて比べる）。似た語が無ければ None。"""
    value = norm(text).translate(_KANA_LIKE)
    if not value:
        return None
    best = max(words, key=lambda w: difflib.SequenceMatcher(None, value, w).ratio())
    return best if difflib.SequenceMatcher(None, value, best).ratio() >= HEADER_RATIO else None


# ------------------------------------------------------------------------------------------ 欄の切り出し
def cell_rects(grid: dict[str, Any]) -> list[dict[str, Any]]:
    """欄の範囲（隣の行・列の文字の間の真ん中で区切る。画面の並べ方と同じ）。"""
    def bounds(spans: list[tuple[float, float]]) -> list[tuple[float, float]]:
        out = []
        for i, (a, b) in enumerate(spans):
            start = a - PAD if i == 0 else (spans[i - 1][1] + a) / 2
            end = b + PAD if i == len(spans) - 1 else (b + spans[i + 1][0]) / 2
            out.append((start, end))
        return out

    rows = bounds([(r["y0"], r["y1"]) for r in grid["rows"]])
    cols = bounds([(c["x0"], c["x1"]) for c in grid["cols"]])
    return [{"row": r, "col": c, "box": (cols[c][0], rows[r][0], cols[c][1], rows[r][1])}
            for r in range(len(rows)) for c in range(len(cols))]


def _clean_crop(gray: Any) -> Any:
    """切り出した欄から罫線（長く続く墨の行・列）と、欄の縁にかかった隣の欄の字を消す。墨が残らなければ None。"""
    import cv2
    import numpy as np

    ink = (gray < 200).astype(np.uint8)
    h, w = ink.shape
    if h < 3 or w < 3:
        return None
    ink[ink.sum(axis=1) >= 0.6 * w, :] = 0
    ink[:, ink.sum(axis=0) >= 0.6 * h] = 0
    count, labels, stats, _ = cv2.connectedComponentsWithStats(ink, connectivity=8)
    keep = np.zeros_like(ink)
    for index in range(1, count):
        x, y, cw, ch, area = stats[index]
        # 欄の縁にかかる塊は、隣の欄の字か罫線の切れ端（自分の字は欄の内側に収まるよう切り出している）
        if area < 3 or x == 0 or y == 0 or x + cw >= w or y + ch >= h:
            continue
        keep[labels == index] = 1
    if not keep.any():
        return None
    ys, xs = np.nonzero(keep)
    y0, y1, x0, x1 = ys.min(), ys.max() + 1, xs.min(), xs.max() + 1
    out = np.full((y1 - y0, x1 - x0), 255, dtype=np.uint8)
    out[keep[y0:y1, x0:x1] > 0] = gray[y0:y1, x0:x1][keep[y0:y1, x0:x1] > 0]
    return out


def crop_cell(gray_page: Any, box: tuple[float, float, float, float], target: int = TARGET_HEIGHT, binary: bool = False) -> Any:
    """欄を切り出して文字だけにし、文字の高さが target になるよう拡大して余白を付ける（RGB の配列）。

    binary なら拡大した後に 2 値にする（ぼけたスキャンで、字の形をはっきりさせる）。
    """
    import cv2
    import numpy as np

    x0, y0, x1, y1 = (int(round(v)) for v in box)
    x0, y0 = max(0, x0), max(0, y0)
    x1, y1 = min(gray_page.shape[1], x1), min(gray_page.shape[0], y1)
    if x1 - x0 < 3 or y1 - y0 < 3:
        return None
    clean = _clean_crop(gray_page[y0:y1, x0:x1])
    if clean is None or clean.shape[0] < 3:
        return None
    scale = max(1.0, min(8.0, target / clean.shape[0]))
    big = cv2.resize(clean, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
    if binary:
        _, big = cv2.threshold(big, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    margin = 12
    canvas = np.full((big.shape[0] + 2 * margin, big.shape[1] + 2 * margin), 255, dtype=np.uint8)
    canvas[margin:margin + big.shape[0], margin:margin + big.shape[1]] = big
    return cv2.cvtColor(canvas, cv2.COLOR_GRAY2RGB)


def recognize(reader: Any, crops: list[Any]) -> list[tuple[str, float]]:
    """文字の認識だけをかける（検出しないので 1 文字の欄も読める）。"""
    out: list[tuple[str, float]] = []
    for crop in crops:
        if crop is None:
            out.append(("", 0.0))
            continue
        result, _ = reader(crop, use_det=False, use_cls=False, use_rec=True)
        text, score = (result[0][0], float(result[0][1])) if result else ("", 0.0)
        out.append((str(text), score))
    return out


# --------------------------------------------------------------------------------------------- 列の種類
HEADER_KIND = {"器具番号": "device", "端子": "terminal", "接点": "contact", "シート": "sheet", "行列": "position"}
KIND_HEADER = {v: k for k, v in HEADER_KIND.items()}
# リレー表のブロックの列の並び（端子の列からのずれ → 書式）
BLOCK_OFFSETS = {-1: "device", 1: "contact", 2: "sheet", 3: "position"}


def _votes(values: dict[tuple[int, int], list[str]], col: int, kind: str) -> int:
    pattern = FORMATS[kind]
    return sum(1 for (_, c), texts in values.items() if c == col and any(pattern.match(fix(t, kind)) for t in texts if t))


def column_kinds(grid: dict[str, Any], values: dict[tuple[int, int], list[str]], table: str) -> dict[int, str]:
    """列ごとの書式（terminal / contact / sheet / position / device / text）。

    リレー表は「器具番号・端子・接点・シート・行列」のブロックの並びなので、書式のはっきりした端子の列を先に決め、
    その左を器具番号、右の 3 列を接点・シート・行列とする。端子の列から決まらない列は、読みの多くが合う書式にする。
    """
    count = len(grid["cols"])
    kinds = {col: "text" for col in range(count)}
    if table != "relay_table":
        return kinds
    terminal_cols = [c for c in range(count) if _votes(values, c, "terminal") >= 2]
    for col in terminal_cols:
        kinds[col] = "terminal"
    for col in terminal_cols:
        for offset, kind in BLOCK_OFFSETS.items():
            target = col + offset
            if 0 <= target < count and kinds[target] == "text":
                kinds[target] = kind
    for col in range(count):
        if kinds[col] != "text":
            continue
        votes = {kind: _votes(values, col, kind) for kind in ("contact", "position", "sheet", "device")}
        best = max(votes, key=lambda k: votes[k])
        if votes[best] >= 2:
            kinds[col] = best
    return kinds


def _header_rows(values: dict[tuple[int, int], list[str]], kinds: dict[int, str]) -> set[int]:
    """リレー表の見出しの行。見出しの語に似た欄が 2 つ以上ある行か、端子の列が空で、すぐ下の行から端子が並ぶ行。"""
    rows = sorted({row for row, _ in values})
    terminal_cols = [c for c, k in kinds.items() if k == "terminal"]

    def has_terminal(row: int) -> bool:
        return any(_TERMINAL.match(fix(t, "terminal")) for c in terminal_cols for t in values.get((row, c), []) if t)

    out = set()
    for row in rows:
        similar = sum(1 for (r, _), texts in values.items()
                      if r == row and any(snap(t, RELAY_HEADERS) for t in texts if t and not re.search(r"\d", t)))
        # 見出しの行には数字が無い（端子を読み違えた行を見出しにしない）
        digits = any(re.search(r"\d", t) for (r, _), texts in values.items() if r == row for t in texts if t)
        below = has_terminal(row + 1) and not has_terminal(row) and not digits
        if (similar >= 2 and not has_terminal(row)) or (below and (row == rows[0] or not has_terminal(row - 1))):
            out.add(row)
    return out


def _choose(candidates: list[tuple[str, float]], kind: str) -> tuple[str, float]:
    """読みの候補から、列の書式に合うもの、合わなければ確からしさの高いものを選び、書式に合わせて直す。"""
    pattern = FORMATS.get(kind)
    scored = []
    for text, score in candidates:
        if not norm(text):
            continue
        value = fix(text, kind)
        fits = bool(pattern and pattern.match(value))
        scored.append((fits, score, value))
    if not scored:
        return "", 0.0
    fits, score, value = max(scored)
    return value, round(score, 3)


def reread_grids(image: Image.Image, grids: dict[str, dict[str, Any]], reader: Any,
                 lexicon: Any = None) -> dict[str, dict[str, Any]]:
    """表ごとの格子の欄を読み直した格子を返す（行・列は同じ。欄はすべて 1 列ずつ。元の読みは ``page_text`` に残す）。

    lexicon（text_extraction.page_lexicon）があれば、器具番号の欄の読みを図面の中で多く読めている語に直す
    （コイルの横の 33HB などと同じ器具番号が表にも書かれる）。
    """
    import numpy as np

    gray = np.asarray(image.convert("L"))
    out = {}
    for table, grid in grids.items():
        if not grid["cells"]:
            out[table] = grid
            continue
        rects = cell_rects(grid)
        page = {}
        for cell in grid["cells"]:
            # 複数の列にまたがって読まれた文字は、どの列の字か分からないので読み直しだけを使う
            page[(cell["row"], cell["col"])] = (cell["text"], cell["score"]) if cell.get("colspan", 1) == 1 else ("", 0.0)
        crops, crop_boxes = [], []
        for rect in rects:
            known = next((c for c in grid["cells"] if (c["row"], c["col"]) == (rect["row"], rect["col"])), None)
            x0, y0, x1, y1 = rect["box"]
            if known is not None and known.get("colspan", 1) == 1:
                # 文字の枠が欄の上下からはみ出す（まとめた欄の器具番号）ときは、文字の枠まで広げる
                y0, y1 = min(y0, known["box"][1] - PAD), max(y1, known["box"][3] + PAD)
            crop_boxes.append((x0, y0, x1, y1))
            crops.append(crop_cell(gray, (x0, y0, x1, y1)))
        reads = recognize(reader, crops)
        values = {(r["row"], r["col"]): [page.get((r["row"], r["col"]), ("", 0.0))[0], read[0]]
                  for r, read in zip(rects, reads, strict=True)}
        kinds = column_kinds(grid, values, table)
        header_rows = _header_rows(values, kinds) if table == "relay_table" else set()
        # 書式の決まった列で、どの読みも書式に合わない欄は、拡大の大きさと 2 値化を変えて読み直す
        retry = [i for i, (rect, read, crop) in enumerate(zip(rects, reads, crops, strict=True))
                 if crop is not None and rect["row"] not in header_rows and kinds[rect["col"]] in FORMATS
                 and not any(FORMATS[kinds[rect["col"]]].match(fix(t, kinds[rect["col"]]))
                             for t in (read[0], page.get((rect["row"], rect["col"]), ("", 0.0))[0]) if t)]
        extra: dict[int, list[tuple[str, float]]] = {i: [] for i in retry}
        for target, binary in ((TARGET_HEIGHT, True), (28, False), (56, True)):
            boxes = [crop_boxes[i] for i in retry]
            for i, read in zip(retry, recognize(reader, [crop_cell(gray, b, target, binary) for b in boxes]), strict=True):
                extra[i].append(read)
        cells = []
        for index, (rect, read, crop) in enumerate(zip(rects, reads, crops, strict=True)):
            key = (rect["row"], rect["col"])
            old = page.get(key, ("", 0.0))
            if crop is None and not norm(old[0]):
                continue
            kind = "header" if rect["row"] in header_rows else kinds[rect["col"]]
            candidates = [old, read, *extra.get(index, [])] if crop is not None else [old]
            if kind == "header":
                word = KIND_HEADER.get(kinds[rect["col"]]) or next(
                    (w for w in (snap(t, RELAY_HEADERS) for t, _ in candidates if t) if w), None)
                text, score = (word, max(c[1] for c in candidates)) if word else _choose(candidates, "text")
            elif table == "title_block" and rect["col"] == 0:
                word = next((w for w in (snap(t, TITLE_KEYS) for t, _ in candidates if t) if w), None)
                text, score = (word, max(c[1] for c in candidates)) if word else _choose(candidates, "text")
            else:
                text, score = _choose(candidates, kind)
                if kind == "device" and lexicon:
                    text = " ".join(correct_token(word, lexicon) for word in text.split())
            if not text:
                continue
            known = next((c for c in grid["cells"] if (c["row"], c["col"]) == key), None)
            box = known["box"] if known is not None and known.get("colspan", 1) == 1 else [round(v, 1) for v in rect["box"]]
            cells.append({"row": key[0], "col": key[1], "colspan": 1, "text": text, "score": score, "box": box,
                          "parts": 1, "page_text": old[0], "kind": kind})
        _vote_repeats(cells)
        out[table] = {**grid, "cells": cells, "reread": True}
    return out


def _one_off(a: str, b: str) -> bool:
    """1 文字だけ違う（置き換え 1 つ）。"""
    return len(a) == len(b) and sum(x != y for x, y in zip(a, b, strict=True)) == 1


def _vote_repeats(cells: list[dict[str, Any]]) -> None:
    """表の中で繰り返す値（端子の並び・型式・シート番号）に、1 度だけ出た 1 文字違いの読みを寄せる。

    リレー表の端子の並び（5-9-1・8-12-4 など）や型式（FV2306）は、ブロックごとに同じものが繰り返し書かれる。
    2 度以上読めた値があれば、1 度だけの 1 文字違いはその読み違いとみなす。
    """
    for kind in ("terminal", "device", "sheet"):
        group = [c for c in cells if c.get("kind") == kind]
        counts: dict[str, int] = {}
        for cell in group:
            counts[cell["text"]] = counts.get(cell["text"], 0) + 1
        for cell in group:
            if counts[cell["text"]] != 1:
                continue
            near = [v for v, n in counts.items() if n >= 2 and _one_off(v, cell["text"])]
            if len(near) == 1:
                cell["text"] = near[0]


def grid_items(grid: dict[str, Any]) -> list[dict[str, Any]]:
    """格子の欄の文字を、OCR の文字と同じ形（text・score・box）にする。"""
    return [{"text": c["text"], "score": c["score"], "box": tuple(c["box"])} for c in grid["cells"] if c["text"]]


def relay_rows_from_grids(texts: list[dict[str, Any]], grids: dict[str, dict[str, Any]], width: int, height: int,
                          symbols: list[dict[str, Any]] | None = None) -> list[dict[str, str]]:
    """リレー表の行を、読み直した格子の列の書式から読む（器具番号・端子・接点・シート・行列）。

    格子に列の書式が無い（読み直していない）ときは、表の範囲の OCR の文字を欄の文字に置き換えて parse_relay_table で読む。
    """
    from analysis.table_grid import items_in

    grid = grids["relay_table"]
    if grid.get("reread"):
        rows = _rows_by_kind(grid)
        if rows:
            if symbols is not None:
                link_relay_table(rows, symbols)
            return rows
    inside = {id(t) for t in items_in(texts, grid["bbox"])}
    items = [t for t in texts if id(t) not in inside] + grid_items(grid)
    rows = parse_relay_table(items, width, height)
    if symbols is not None:
        link_relay_table(rows, symbols)
    return rows


def _rows_by_kind(grid: dict[str, Any]) -> list[dict[str, str]]:
    """端子の列ごとに、見出しの行で区切ったブロックの行を読む。器具番号はブロックの左の列の上から 2 行。"""
    by_key = {(c["row"], c["col"]): c for c in grid["cells"]}
    headers = sorted({c["row"] for c in grid["cells"] if c.get("kind") == "header"})
    terminal_cols = sorted({c["col"] for c in grid["cells"] if c.get("kind") == "terminal"})
    rows_out: list[dict[str, str]] = []
    count = len(grid["rows"])

    def text(row: int, col: int, kind: str) -> str:
        cell = by_key.get((row, col))
        return cell["text"] if cell is not None and cell.get("kind") == kind else ""

    for col in terminal_cols:
        bounds = [*headers, count]
        starts = [0, *[h + 1 for h in headers]]
        for start in starts:
            end = min((b for b in bounds if b >= start), default=count)
            # 端子の書式に合う行が 1 つでもあればブロックとし、書式に合わない読みの行も行として残す（人が直せるように）
            if not any(_TERMINAL.match(text(r, col, "terminal")) for r in range(start, end)):
                continue
            block = [r for r in range(start, end) if text(r, col, "terminal")]
            device = " ".join(t for t in (text(r, col - 1, "device") for r in range(start, end)) if t)
            device = " ".join(device.split()[:2])
            for row in block:
                rows_out.append({
                    "device": device, "terminal": text(row, col, "terminal"),
                    "contact": text(row, col + 1, "contact").lower(), "sheet": text(row, col + 2, "sheet"),
                    "position": text(row, col + 3, "position"),
                })
    return rows_out
