"""表の欄（リレー表・表題欄など）の OCR の文字を、元の表と同じ並びの行と列に並べる（DD-0051）。

OCR は文字のまとまりごとに枠（ページの座標）を返すだけで、どの行・どの列の文字かは持たない。
ここでは文字の枠の上下の位置で行を、左右の重なりで列を決め、元の表に近い格子（行 × 列）にする。
人が格子の欄ごとに読みを確かめて直せるようにするため、文字の無い欄も行と列の位置から決まる。

表の罫線（``ruling_lines``）があれば、それも使う。

- OCR が列の文字を 1 つも読めなかった列（リレー表の接点の A・B は小さく、まとめて読み落とすことがある）も、
  罫線で区切られ横の罫線が通っていれば空の列として作る。人がそこに書き足せる。
- 見出しが 2 つの列にまたがって 1 語に読まれても、罫線の位置で列を分ける。

器具番号の欄のように、複数の行をまとめた欄の文字は行の間に書かれる。その文字だけの行は作らず、
同じ列が空いている上か下の行に入れる。

``build_grid`` は文字の枠と罫線だけを受け取る純粋な関数で、合成した文字の枠で試験できる。
"""

from __future__ import annotations

from itertools import pairwise
from statistics import median
from typing import Any

Box = tuple[float, float, float, float]

# 同じ行とみなす中心の高さのずれ（文字の高さの中央値に対する割合）
ROW_TOLERANCE = 0.55
# 行の間に書かれた文字（まとめた欄）を隣の行に入れる、中心どうしの近さ（文字の高さに対する割合）
ROW_MERGE = 0.9
# 列の幅を決めるのに使わない幅の広い文字（見出しが複数の列にまたがるなど）。幅の中央値に対する倍率
WIDE_ITEM = 3.0
# 文字の枠が列に重なる割合がこれ以上なら、その列にかかるとみなす
COLUMN_OVERLAP = 0.3
# 罫線で区切られた空の列・行を作る最小の幅（文字の高さに対する割合）
EMPTY_MIN = 0.8
# 罫線とみなす線の長さ（文字の高さに対する倍率）と、罫線から欄の内側までの余白（px）
RULE_LENGTH = 2.5
RULE_MARGIN = 2.0

Rules = dict[str, list[tuple[float, float, float]]]


def _box(value: Any) -> Box:
    if isinstance(value, dict):
        return (float(value["x0"]), float(value["y0"]), float(value["x1"]), float(value["y1"]))
    x0, y0, x1, y1 = (float(v) for v in value)
    return (x0, y0, x1, y1)


def _inside(box: Box, area: Box) -> bool:
    cx, cy = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2
    return area[0] <= cx <= area[2] and area[1] <= cy <= area[3]


def items_in(items: list[dict[str, Any]], area: Any) -> list[dict[str, Any]]:
    """表の範囲に中心が入る文字。"""
    region = _box(area)
    return [item for item in items if _inside(_box(item["box"]), region)]


def _columns(boxes: list[Box]) -> list[list[float]]:
    """文字の左右の範囲を、重なるものどうしでつなげて列にする。隣の列との隙間が狭い表もあるので、隙間は足さない。"""
    widths = [b[2] - b[0] for b in boxes]
    limit = WIDE_ITEM * median(widths)
    narrow = [b for b in boxes if b[2] - b[0] <= limit] or boxes
    columns: list[list[float]] = []
    for x0, _, x1, _ in sorted(narrow):
        if columns and x0 <= columns[-1][1]:
            columns[-1][1] = max(columns[-1][1], x1)
        else:
            columns.append([x0, x1])
    return columns


def _positions(lines: list[tuple[float, float, float]]) -> list[float]:
    """罫線の位置（近い線はまとめる）。"""
    out: list[float] = []
    for pos in sorted(line[0] for line in lines):
        if out and pos - out[-1] <= 3:
            continue
        out.append(pos)
    return out


def _crossed(lines: list[tuple[float, float, float]], a: float, b: float) -> bool:
    """a〜b の帯を横切る罫線があるか（帯が表の欄の中か、表と表の間の余白か）。"""
    return any(start <= a + RULE_MARGIN and end >= b - RULE_MARGIN for _, start, end in lines)


def _with_rules(spans: list[list[float]], rules: list[tuple[float, float, float]],
                across: list[tuple[float, float, float]], height: float) -> list[list[float]]:
    """文字から決めた列（行）を罫線で分け、罫線で区切られた空の列（行）を足す。"""
    lines = _positions(rules)
    out: list[list[float]] = []
    for start, end in spans:
        # 文字の列の中を通る罫線で分ける。罫線から列の端までが文字の半分より狭い切れ端（はみ出した字）は分けない
        cuts = [p for p in lines if start + max(RULE_MARGIN, 0.5 * height) < p < end - max(RULE_MARGIN, 0.5 * height)]
        edges = [start, *cuts, end]
        out.extend([a, b] for a, b in pairwise(edges))
    for a, b in pairwise(lines):
        lo, hi = a + RULE_MARGIN, b - RULE_MARGIN
        if hi - lo < EMPTY_MIN * height or not _crossed(across, a, b):
            continue
        if any(min(hi, s[1]) - max(lo, s[0]) > RULE_MARGIN for s in out):
            continue
        out.append([lo, hi])
    return sorted(out)


def _spanned(box: Box, columns: list[list[float]]) -> list[int]:
    """文字の枠がかかる列（連続した列の番号）。どの列にもかからなければ中心に一番近い列。"""
    hits = []
    for index, (c0, c1) in enumerate(columns):
        overlap = min(box[2], c1) - max(box[0], c0)
        if overlap > 0 and overlap >= COLUMN_OVERLAP * min(c1 - c0, box[2] - box[0]):
            hits.append(index)
    if hits:
        return list(range(hits[0], hits[-1] + 1))
    cx = (box[0] + box[2]) / 2
    return [min(range(len(columns)), key=lambda i: abs((columns[i][0] + columns[i][1]) / 2 - cx))]


Entry = tuple[Box, dict[str, Any], list[int]]


def _cluster_rows(entries: list[Entry], height: float) -> list[list[Entry]]:
    rows: list[list[Entry]] = []
    for entry in sorted(entries, key=lambda e: (e[0][1] + e[0][3]) / 2):
        cy = (entry[0][1] + entry[0][3]) / 2
        if rows:
            last = rows[-1]
            mean = sum((b[1] + b[3]) / 2 for b, _, _ in last) / len(last)
            if abs(cy - mean) <= ROW_TOLERANCE * height:
                last.append(entry)
                continue
        rows.append([entry])
    return rows


def _center_y(row: list[Entry]) -> float:
    return sum((b[1] + b[3]) / 2 for b, _, _ in row) / len(row)


def _merge_between_rows(rows: list[list[Entry]], height: float) -> tuple[list[list[Entry]], list[list[Entry]]]:
    """行の間に書かれた文字だけの行を、同じ列が空いている近い行に入れる。戻り値は（行, 行の上下を決める文字）。"""
    core = [list(row) for row in rows]
    merged = [list(row) for row in rows]
    alive = [True] * len(rows)
    for index, row in enumerate(rows):
        cy = _center_y(row)
        neighbors = [j for j in (index - 1, index + 1) if 0 <= j < len(rows) and alive[j] and len(rows[j]) >= len(row)]
        neighbors = [j for j in neighbors if abs(_center_y(rows[j]) - cy) < ROW_MERGE * height]
        neighbors.sort(key=lambda j: (abs(_center_y(rows[j]) - cy), j))
        placement: dict[int, list[Entry]] = {}
        for entry in row:
            target = next((j for j in neighbors
                           if not set(entry[2]) & {c for e in merged[j] + placement.get(j, []) for c in e[2]}), None)
            if target is None:
                placement = {}
                break
            placement.setdefault(target, []).append(entry)
        if not placement:
            continue
        for j, moved in placement.items():
            merged[j].extend(moved)
        alive[index] = False
    return [m for m, a in zip(merged, alive, strict=True) if a], [c for c, a in zip(core, alive, strict=True) if a]


def build_grid(items: list[dict[str, Any]], area: Any, rules: Rules | None = None) -> dict[str, Any]:
    """表の範囲の文字を、行 × 列の格子にする。

    ``rules`` は表の罫線 ``{"x": [(x, y0, y1), ...], "y": [(y, x0, x1), ...]}``（``ruling_lines`` の戻り値）。

    戻り値:
      - ``bbox``: 表の範囲（指定の範囲と文字の範囲を合わせたもの）
      - ``rows``: 行の上下 ``{"y0", "y1"}``（上から）
      - ``cols``: 列の左右 ``{"x0", "x1"}``（左から）
      - ``cells``: 文字のある欄 ``{"row", "col", "colspan", "text", "score", "box", "parts"}``。
        同じ欄に入った複数の文字は左から空白でつなぎ、確からしさは一番低いものにする。
    """
    region = _box(area)
    found = [(_box(item["box"]), item) for item in items_in(items, region)]
    if not found:
        return {"bbox": [round(v, 1) for v in region], "rows": [], "cols": [], "cells": []}
    height = median(b[3] - b[1] for b, _ in found) or 1.0
    rules = rules or {"x": [], "y": []}
    columns = _with_rules(_columns([b for b, _ in found]), rules.get("x", []), rules.get("y", []), height)
    entries: list[Entry] = [(box, item, _spanned(box, columns)) for box, item in found]
    rows, core = _merge_between_rows(_cluster_rows(entries, height), height)
    spans = [[min(b[1] for b, _, _ in row), max(b[3] for b, _, _ in row)] for row in core]
    # 罫線で区切られた、文字の 1 つも無い行（行の上下は罫線の内側）
    order = sorted(range(len(spans)), key=lambda i: spans[i][0])
    rows, spans = [rows[i] for i in order], [spans[i] for i in order]
    with_empty = _with_rules([list(s) for s in spans], rules.get("y", []), rules.get("x", []), height)
    added = [s for s in with_empty if not any(abs(s[0] - t[0]) < 1e-6 and abs(s[1] - t[1]) < 1e-6 for t in spans)
             and not any(min(s[1], t[1]) - max(s[0], t[0]) > 0 for t in spans)]
    layout = sorted([(s, row) for s, row in zip(spans, rows, strict=True)] + [(s, []) for s in added], key=lambda p: p[0][0])
    grouped: dict[tuple[int, int], list[Entry]] = {}
    for row_index, (_, row) in enumerate(layout):
        for entry in row:
            grouped.setdefault((row_index, entry[2][0]), []).append(entry)
    cells = []
    for (row_index, col_index), parts in sorted(grouped.items()):
        parts.sort(key=lambda p: p[0][0])
        boxes = [p[0] for p in parts]
        cells.append({
            "row": row_index,
            "col": col_index,
            "colspan": max(len(p[2]) for p in parts),
            "text": " ".join(str(p[1]["text"]).strip() for p in parts).strip(),
            "score": round(min(float(p[1].get("score", 1.0)) for p in parts), 3),
            "box": [round(v, 1) for v in (min(b[0] for b in boxes), min(b[1] for b in boxes),
                                           max(b[2] for b in boxes), max(b[3] for b in boxes))],
            "parts": len(parts),
        })
    row_bounds = [{"y0": round(s[0], 1), "y1": round(s[1], 1)} for s, _ in layout]
    col_bounds = [{"x0": round(c0, 1), "x1": round(c1, 1)} for c0, c1 in columns]
    bbox = (
        min(region[0], col_bounds[0]["x0"]), min(region[1], row_bounds[0]["y0"]),
        max(region[2], col_bounds[-1]["x1"]), max(region[3], row_bounds[-1]["y1"]),
    )
    return {"bbox": [round(v, 1) for v in bbox], "rows": row_bounds, "cols": col_bounds, "cells": cells}


def cell_box(grid: dict[str, Any], row: int, col: int, colspan: int = 1) -> Box:
    """欄の位置（文字の無い欄に人が書き足したときに使う）。行・列の範囲から決める。"""
    r, c = grid["rows"][row], grid["cols"][col]
    last = grid["cols"][min(col + max(colspan, 1) - 1, len(grid["cols"]) - 1)]
    return (float(c["x0"]), float(r["y0"]), float(last["x1"]), float(r["y1"]))


def _runs(mask: Any, min_len: int) -> list[tuple[int, int, int]]:
    """2 値の配列の各行で、min_len 以上続く墨の区間（行, 始め, 終わり）。"""
    import numpy as np

    padded = np.pad(mask.astype(np.int8), ((0, 0), (1, 1)))
    diff = np.diff(padded, axis=1)
    out = []
    for row, start in zip(*np.nonzero(diff == 1), strict=True):
        end = int(np.argmax(diff[row, start:] == -1)) + start
        if end - start >= min_len:
            out.append((int(row), int(start), int(end)))
    return out


def _segments(runs: list[tuple[int, int, int]]) -> list[tuple[float, float, float]]:
    """隣り合う行の区間（太い線）を 1 本の線にまとめる。"""
    lines: list[list[float]] = []
    for row, start, end in sorted(runs):
        for line in lines:
            if row - line[3] <= 1 and min(end, line[2]) - max(start, line[1]) > 0:
                line[1], line[2], line[3] = min(line[1], start), max(line[2], end), row
                break
        else:
            lines.append([row, start, end, row])
    return [((line[0] + line[3]) / 2, line[1], line[2]) for line in lines]


def ruling_lines(image: Any, area: Any, text_height: float = 12.0) -> Rules | None:
    """表の範囲の罫線（ページの座標）。画像の処理に numpy を使う。使えなければ None。"""
    try:
        import numpy as np
    except ImportError:  # pragma: no cover - 解析の環境には必ずある
        return None
    x0, y0, x1, y1 = (round(v) for v in _box(area))
    x0, y0 = max(0, x0 - 4), max(0, y0 - 4)
    x1, y1 = min(image.width, x1 + 4), min(image.height, y1 + 4)
    if x1 - x0 < 4 or y1 - y0 < 4:
        return None
    ink = np.asarray(image.crop((x0, y0, x1, y1)).convert("L")) < 128
    min_len = max(12, int(RULE_LENGTH * text_height))
    horizontal = [(y0 + pos, x0 + a, x0 + b) for pos, a, b in _segments(_runs(ink, min_len))]
    vertical = [(x0 + pos, y0 + a, y0 + b) for pos, a, b in _segments(_runs(ink.T, min_len))]
    return {"x": vertical, "y": horizontal}


def expand_to_rules(image: Any, area: Any, text_height: float = 12.0) -> Box:
    """表の範囲を、範囲にかかる罫線の端まで広げる（文字から決めた範囲は、読み落とした端の列を含まないことがある）。

    横の罫線は範囲の上下の内側にあるものだけ、縦の罫線は広げた左右の内側で範囲に重なるものだけを使う（図面の配線で
    広げないため）。広げるのは各辺で文字の高さの 8 倍（40 px 以上）まで。
    """
    x0, y0, x1, y1 = _box(area)
    margin = max(40.0, 8 * text_height)
    rules = ruling_lines(image, (x0 - margin, y0 - margin, x1 + margin, y1 + margin), text_height)
    if not rules:
        return (x0, y0, x1, y1)
    rows = [(a, b) for y, a, b in rules["y"] if y0 <= y <= y1 and min(b, x1) > max(a, x0)]
    nx0 = max(x0 - margin, min([x0, *(a for a, _ in rows)]))
    nx1 = min(x1 + margin, max([x1, *(b for _, b in rows)]))
    cols = [(a, b) for x, a, b in rules["x"] if nx0 - 2 <= x <= nx1 + 2 and min(b, y1) > max(a, y0)]
    ny0 = max(y0 - margin, min([y0, *(a for a, _ in cols)]))
    ny1 = min(y1 + margin, max([y1, *(b for _, b in cols)]))
    return (nx0, ny0, nx1, ny1)
