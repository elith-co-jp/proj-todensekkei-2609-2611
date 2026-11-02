"""リレー表の行を、図面枠の行列（上辺の列番号・左右の辺の行記号）で図面の接点に結び付ける（#40 #44）。

リレー表の行（器具番号・端子 5-9-1・接点 a/b・シート・行列 10D）は、接点のあるシートと図面上の位置を指す。
列番号と行記号をページの OCR から読んで行列を図面の座標に直し、その近くの接点を器具番号とあわせて選ぶ。
結び付いた接点には、表の端子番号（端子欄は NO-COM-NC。a 接点は COM と NO、b 接点は COM と NC）と記号の種類を
図面から読んだ結果と照合した結果を付け、食い違いは要確認とする。
「本」の行は 1 枚の解析で、別シートを指す行は書き出し時に参照先の図面と組にして結び付ける。
"""

from __future__ import annotations

import re
import statistics
from typing import Any, TypedDict

from analysis.text_extraction import TextItem, normalize, same_relay_device

ROW_LETTERS = "ABCDEFGHJKLMNPQRSTUVWXYZ"  # 図面枠の行記号に I・O は使わない
COLUMN_BAND, LEFT_BAND, RIGHT_BAND = 0.07, 0.05, 0.93
# 行列は回路の始まる行を指し、接点はその行から 1 行ほど下に描かれる（正解 47 行で列のずれ ±0.8、行のずれ +0.3〜+1.1）。
COLUMN_WINDOW = 1.0
ROW_WINDOW = (-0.5, 1.5)
CONTACT_CLASSES = {"contact_a": "a接点（メーク）", "contact_b": "b接点（ブレーク）"}


class DrawingGrid(TypedDict):
    columns: list[float]  # [a, b]: 列番号 n の中心の x = a + b n
    rows: list[list[float]]  # 辺ごとの [辺の x, c, d]: 行 k の中心の y = c + d k。左右の辺の間は x で補間する


def _robust_line(points: list[tuple[float, float]]) -> list[float] | None:
    """y = a + b x を傾きの中央値で求め、0.3 目盛り以上外れた点（読み違えた番号・記号）を除いて求め直す。"""
    for _ in range(2):
        slopes = [(y2 - y1) / (x2 - x1) for i, (x1, y1) in enumerate(points) for x2, y2 in points[i + 1:] if x2 != x1]
        if len(points) < 3 or not slopes:
            return None
        b = statistics.median(slopes)
        a = statistics.median(y - b * x for x, y in points)
        points = [(x, y) for x, y in points if abs(y - a - b * x) <= 0.3 * abs(b)]
    return [a, b] if len(points) >= 3 else None


def drawing_grid(items: list[TextItem], width: int, height: int) -> DrawingGrid | None:
    """図面枠の外に並ぶ列番号（1〜24）と行記号（A〜Z）の位置から、行列と図面の座標の対応を求める。"""
    columns, sides = [], {"left": [], "right": []}
    for item in items:
        text = normalize(item["text"]).replace(" ", "")
        x, y = (item["box"][0] + item["box"][2]) / 2, (item["box"][1] + item["box"][3]) / 2
        if y < COLUMN_BAND * height and re.fullmatch(r"\d{1,2}", text):
            columns.append((float(text), x))
        elif len(text) == 1 and text in ROW_LETTERS and (x < LEFT_BAND * width or x > RIGHT_BAND * width):
            sides["left" if x < LEFT_BAND * width else "right"].append((ROW_LETTERS.index(text), y, x))
    column_line = _robust_line(columns)
    rows = []
    for labels in sides.values():
        line = _robust_line([(k, y) for k, y, _ in labels])
        if line:
            rows.append([statistics.median(x for _, _, x in labels), *line])
    return {"columns": column_line, "rows": rows} if column_line and rows else None


def grid_coordinates(grid: DrawingGrid, x: float, y: float) -> tuple[float, float]:
    """図面の点の（列番号, 行記号の順番）。どちらも小数で、行記号は A が 0。"""
    a, b = grid["columns"]
    if len(grid["rows"]) == 1:
        _, c, d = grid["rows"][0]
    else:
        (x0, c0, d0), (x1, c1, d1) = sorted(grid["rows"])[:2]
        t = min(1.0, max(0.0, (x - x0) / (x1 - x0)))
        c, d = c0 + t * (c1 - c0), d0 + t * (d1 - d0)
    return (x - a) / b, (y - c) / d


def _position(position: str) -> tuple[int, int] | None:
    match = re.fullmatch(r"(\d{1,2})([A-Z])", position or "")
    if not match or match.group(2) not in ROW_LETTERS:
        return None
    return int(match.group(1)), ROW_LETTERS.index(match.group(2))


def _center(symbol: dict[str, Any]) -> tuple[float, float]:
    box = symbol["bbox"]
    return (box["x0"] + box["x1"]) / 2, (box["y0"] + box["y1"]) / 2


def match_rows(
    rows: list[dict[str, str]], row_indexes: list[int], symbols: list[dict[str, Any]], grid: DrawingGrid,
) -> dict[int, dict[str, Any]]:
    """行列の窓の中の接点を、器具番号が同じものを優先し、近い順に 1 行 1 接点で選ぶ。

    戻り値は行番号ごとの {symbol_id, device_match, unique}。unique は、器具番号が同じ接点が窓の中にそれ 1 つ。
    """
    contacts = [(s, grid_coordinates(grid, *_center(s))) for s in symbols if s.get("class_name") in CONTACT_CLASSES]
    candidates: dict[int, list[tuple[bool, float, str]]] = {}
    for index in row_indexes:
        cell = _position(rows[index]["position"])
        if cell is None:
            continue
        found = candidates.setdefault(index, [])
        for symbol, (u, v) in contacts:
            du, dv = u - cell[0], v - cell[1]
            if abs(du) <= COLUMN_WINDOW and ROW_WINDOW[0] <= dv <= ROW_WINDOW[1]:
                same = same_relay_device(symbol.get("label") or "", rows[index]["device"])
                found.append((same, abs(du) + 0.5 * abs(dv - 0.5), symbol["id"]))
    pairs = sorted((not same, distance, index, sid) for index, found in candidates.items() for same, distance, sid in found)
    out: dict[int, dict[str, Any]] = {index: {"symbol_id": None, "device_match": False, "unique": False} for index in candidates}
    taken: set[str] = set()
    for not_same, _, index, sid in pairs:
        if out[index]["symbol_id"] is None and sid not in taken:
            taken.add(sid)
            same_count = sum(1 for same, _, _ in candidates[index] if same)
            out[index] = {"symbol_id": sid, "device_match": not not_same, "unique": not not_same and same_count == 1}
    return out


def table_numbers(row: dict[str, str]) -> list[str]:
    """接点が使う 2 つの端子番号。端子欄は NO-COM-NC の順（5-9-1）。"""
    parts = (row.get("terminal") or "").split("-")
    if len(parts) != 3 or row.get("contact") not in {"a", "b"}:
        return []
    no, com, nc = parts
    return [com, no] if row["contact"] == "a" else [com, nc]


def apply_row(row: dict[str, str], index: int, match: dict[str, Any], symbol: dict[str, Any] | None,
              project_id: Any = None) -> dict[str, Any]:
    """結び付いた接点に表の端子番号と種類を照合した結果を付け、row_links の 1 件を返す。

    片方の端だけ番号が読めて表の番号と合うとき、もう片方の端に表の残りの番号を付ける（name_source: relay_table）。
    種類は、器具番号が同じ接点が窓の中にそれ 1 つのときだけ表に合わせて直す。それ以外の食い違いは要確認。
    """
    link: dict[str, Any] = {"row": index, "position": row["position"], "sheet": row["sheet"], "symbol_id": None,
                            "status": "要確認", "reasons": []}
    if symbol is None:
        link["reasons"].append("行列の位置に接点がない")
        return link
    link["symbol_id"] = symbol["id"]
    reasons = [] if match["device_match"] else ["接点のラベルの器具番号が表と違う"]
    numbers = table_numbers(row)
    terminals = symbol.get("terminals") or []
    if match["device_match"] and match["unique"] and len(numbers) == len(terminals) == 2:
        for terminal in terminals:
            alternative = str(terminal.get("ocr_number_alternative") or "")
            if not alternative or not alternative.isdigit() or str(terminal.get("name") or "") in numbers:
                continue
            candidate = [alternative if t is terminal else str(t.get("name") or "") for t in terminals]
            if set(candidate) == set(numbers):
                terminal["name"] = alternative
                terminal["name_source"] = "relay_table_verified_ocr"
                break
    read = [str(t.get("name")) for t in terminals if str(t.get("name") or "").isdigit()]
    terminal_check = None if not numbers else "未読" if not read else "一致" if set(read) <= set(numbers) else "不一致"
    if terminal_check == "不一致":
        reasons.append("端子番号が表と違う")
    if terminal_check == "一致" and len(read) == 1 and len(terminals) == 2 and match["device_match"]:
        unread = next(t for t in terminals if not str(t.get("name") or "").isdigit())
        unread["name"], unread["name_source"] = next(n for n in numbers if n != read[0]), "relay_table"
    table_class = f"contact_{row['contact']}" if row.get("contact") in {"a", "b"} else None
    class_check = None
    if table_class:
        class_check = "一致" if symbol.get("class_name") == table_class else "不一致"
        if class_check == "不一致" and match["unique"]:
            symbol["detected_class"] = symbol["class_name"]
            symbol["class_name"] = symbol["type"] = table_class
            symbol["class_label"] = CONTACT_CLASSES[table_class]
            symbol["class_source"] = "relay_table"
            class_check = "表に合わせて変更"
        elif class_check == "不一致":
            reasons.append("接点の種類（a/b）が表と違う")
    symbol["relay_row"] = {
        "project_id": project_id, "row": index, "device": row["device"], "terminal": row["terminal"],
        "contact": row.get("contact") or "", "position": row["position"], "table_terminal_numbers": numbers,
        "terminal_check": terminal_check, "class_check": class_check,
        "status": "要確認" if reasons else "linked", "reasons": reasons,
    }
    link.update(status=symbol["relay_row"]["status"], reasons=reasons)
    return link


def _device_link(relay_table: dict[str, Any], device: str) -> dict[str, Any] | None:
    return next((link for link in relay_table.get("links", []) if link["device"] == device), None)


def link_relay_rows(text_result: dict[str, Any], symbols: list[dict[str, Any]], width: int, height: int) -> dict[str, int]:
    """1 枚の図面で、シートが「本」の行を行列の位置の接点に結び付ける。drawing_grid は書き出し時の照合にも使う。"""
    items = [{"text": t["text"], "score": t.get("score", 1.0), "box": tuple(t["box"])} for t in text_result.get("texts", [])]
    grid = drawing_grid(items, width, height)
    text_result["drawing_grid"] = grid
    relay_table = text_result.get("relay_table") or {}
    rows = relay_table.get("rows", [])
    own = [i for i, row in enumerate(rows) if row["sheet"] == "本" and row["position"]]
    counts = {"rows": len(own), "linked": 0}
    if grid is None or not own:
        return counts
    by_id = {s["id"]: s for s in symbols}
    for index, match in match_rows(rows, own, symbols, grid).items():
        link = apply_row(rows[index], index, match, by_id.get(match["symbol_id"]))
        _device_link(relay_table, rows[index]["device"]).setdefault("row_links", []).append(link)
        counts["linked"] += link["symbol_id"] is not None
    return counts


def link_document_relay_rows(pages: list[tuple[str, Any, dict[str, Any]]]) -> tuple[int, int]:
    """書き出す図面どうしで、別シートを指すリレー表の行を、そのシートの図面の行列の位置の接点に結び付ける。

    pages は（シート番号, プロジェクト ID, 構造 JSON）。戻り値は（参照先が書き出しに含まれる行の数, 接点が見つかった数）。
    """
    by_sheet = {sheet: (project_id, page) for sheet, project_id, page in pages if sheet}
    total = linked = 0
    for _, project_id, page in pages:
        relay_table = page.get("relay_table") or {}
        rows = relay_table.get("rows", [])
        for sheet, (target_id, target) in by_sheet.items():
            # 保存済みの構造 JSON には、行列を読む前の版の行（position・sheet が無い）もある。
            wanted = [i for i, row in enumerate(rows) if row.get("position") and row.get("sheet") not in (None, "本")
                      and normalize(row["sheet"]).replace(" ", "") == sheet]
            if not wanted or target is page or not target.get("drawing_grid"):
                continue
            total += len(wanted)
            by_id = {s["id"]: s for s in target.get("symbols", [])}
            for index, match in match_rows(rows, wanted, target.get("symbols", []), target["drawing_grid"]).items():
                link = apply_row(rows[index], index, match, by_id.get(match["symbol_id"]), project_id)
                link["project_id"] = target_id
                device_link = _device_link(relay_table, rows[index]["device"])
                if device_link is not None:
                    device_link.setdefault("row_links", []).append(link)
                linked += link["symbol_id"] is not None
    return total, linked
