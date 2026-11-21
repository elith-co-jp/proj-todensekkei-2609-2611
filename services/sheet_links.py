"""行先札・来先札（シート間参照）で、図面をまたいで配線をつなぐ（DD-0030）。

図面 A の行先札「ER21212へ」と、図面 ER21212 の来先札「(ER21211)」「ER21211より」は同じ線の両端。
次の規則で札どうしを組にし、両方の札のノードを 1 つにする。

1. 札は「その他」の記号で、ラベルにシート番号（括弧つき、「へ」「より」つき、またはシート番号だけ）があるもの。
   盤・カード・端子台・遮断器の名前（構造解析の classify_other と同じ規則）が入ったラベルは札にしない。
   種別「シート間参照」の配線（external_ref にシート番号）も、その配線の終点を札として扱う。
2. 札 t（図面 A、行先 B）と札 u（図面 B、行先 A）が向かい合うときだけ組の候補にする。シート番号は枝番（-3）を
   除いて比べる（図面のシート番号は人が確定したもの、無ければ図面名の中のシート番号）。
3. 札の識別（ラベルのシート番号以外の語、つながる配線の電線番号、external_ref の語）が両方にあれば、共通の語が
   あるときだけ候補にする（強い候補）。どちらかに識別が無ければ弱い候補。
4. t の最良の候補が u だけで、u の最良の候補も t だけのとき組にする。弱い候補どうしの組は「推定」と記録する。
   決まらない札、行先の図面が選ばれていない札は「確認が必要な箇所」に出す。
"""

from __future__ import annotations

import re
import unicodedata
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

from analysis.text_extraction import _BREAKER, _CARD, _EQUIPMENT, _TERMINAL_UNIT, SHEET_REF

_SHEET = re.compile(SHEET_REF)
_TAG_LABEL = re.compile(rf"\(({SHEET_REF})\)|({SHEET_REF})(?:へ|より|ヘ)?")
_SPLIT = re.compile(r"[\s()（）、,/]+")


def _norm(text: str | None) -> str:
    return unicodedata.normalize("NFKC", text or "").upper()


def base_sheet(sheet: str | None) -> str:
    """ER21011-3 → ER21011（枝番を除く）。"""
    return (sheet or "").split("-")[0]


def page_sheet(page: dict[str, Any]) -> str | None:
    """図面のシート番号: 確定したシート番号、無ければ図面名の中のシート番号。"""
    for text in (page.get("sheet_no"), page.get("name")):
        m = _SHEET.search(_norm(text).replace(" ", ""))
        if m:
            return m.group(0)
    return None


def _idents(text: str, drop: str) -> set[str]:
    rest = _norm(text).replace(drop, " ") if drop else _norm(text)
    rest = rest.replace("へ", " ").replace("より", " ").replace("ヘ", " ")
    return {t for t in _SPLIT.split(rest) if t and t not in ("-",)}


def sheet_tag_target(symbol: dict[str, Any]) -> str | None:
    """札の行先のシート番号（札でなければ None）。"""
    if symbol.get("class_key") != "other":
        return None
    label = _norm(symbol.get("label"))
    compact = label.replace(" ", "")
    if not compact or _BREAKER.search(compact) or _CARD.search(compact) or _TERMINAL_UNIT.search(compact):
        return None
    if any(_EQUIPMENT.search(t) for t in label.split()) or _EQUIPMENT.search(compact):
        return None
    m = _TAG_LABEL.search(compact)
    return (m.group(1) or m.group(2)) if m else None


@dataclass
class Tag:
    project_id: int
    symbol_ref: str
    own_sheet: str | None
    target: str
    node: Any                      # union-find のキー（札のノード）
    idents: set[str] = field(default_factory=set)
    source: str = "symbol"         # symbol（札の記号）/ connection（種別「シート間参照」の配線）
    label: str = ""

    def describe(self) -> dict[str, Any]:
        return {"project_id": self.project_id, "symbol_ref": self.symbol_ref, "sheet": self.own_sheet,
                "target": self.target, "label": self.label}


def symbol_tag(page: dict[str, Any], symbol: dict[str, Any], node: Any, wire_numbers: set[str]) -> Tag | None:
    target = sheet_tag_target(symbol)
    if target is None:
        return None
    label = symbol.get("label") or ""
    idents = _idents(label, target)
    for text in wire_numbers:
        if text:
            sheet = _SHEET.search(_norm(text).replace(" ", ""))
            idents |= _idents(text, sheet.group(0) if sheet else "")
    return Tag(page["id"], symbol["ref"], page_sheet(page), target, node, idents, "symbol", label)


def connection_tag(page: dict[str, Any], connection: dict[str, Any], node: Any) -> Tag | None:
    external = _norm(connection.get("external_ref")).replace(" ", "")
    m = _SHEET.search(external)
    if not m:
        return None
    idents = _idents(external, m.group(0))
    if connection.get("wire_no"):
        idents.add(_norm(connection["wire_no"]))
    return Tag(page["id"], connection["to_symbol_ref"], page_sheet(page), m.group(0), node, idents, "connection",
               connection.get("external_ref") or "")


def link_tags(tags: list[Tag], page_sheets: dict[int, str | None]) -> tuple[list[tuple[Tag, Tag, str]], list[dict[str, Any]]]:
    """札を組にする。戻り値は（組と確からしさ "matched" / "guessed" の一覧, 確認が必要な箇所）。"""
    loaded = {base_sheet(s) for s in page_sheets.values() if s}
    candidates: dict[int, list[tuple[int, int]]] = defaultdict(list)  # 札の番号 → [(強さ, 相手の番号)]
    for i, t in enumerate(tags):
        for j, u in enumerate(tags):
            if j <= i or t.project_id == u.project_id or not t.own_sheet or not u.own_sheet:
                continue
            if base_sheet(t.target) != base_sheet(u.own_sheet) or base_sheet(u.target) != base_sheet(t.own_sheet):
                continue
            if t.idents and u.idents:
                if not t.idents & u.idents:
                    continue
                strength = 2
            else:
                strength = 1
            candidates[i].append((strength, j))
            candidates[j].append((strength, i))

    def best(i: int) -> tuple[int, list[int]]:
        if not candidates[i]:
            return 0, []
        top = max(s for s, _ in candidates[i])
        return top, [j for s, j in candidates[i] if s == top]

    pairs: list[tuple[Tag, Tag, str]] = []
    paired: set[int] = set()
    diagnostics: list[dict[str, Any]] = []
    for i, t in enumerate(tags):
        if i in paired:
            continue
        strength, mine = best(i)
        if len(mine) == 1 and mine[0] not in paired:
            j = mine[0]
            _, theirs = best(j)
            if theirs == [i]:
                pairs.append((t, tags[j], "matched" if strength == 2 else "guessed"))
                paired |= {i, j}
                continue
        if not t.own_sheet:
            message = "この図面のシート番号が分からないため、札をつなげません（図面情報でシート番号を入れてください）"
        elif base_sheet(t.target) not in loaded:
            message = f"行先 {t.target} の図面が選ばれていないため、この札の先はつなぎません"
        elif not mine:
            message = f"行先 {t.target} の図面に、この図面（{t.own_sheet or 'シート番号なし'}）へ向かう札が見つかりません"
        else:
            message = f"行先 {t.target} の図面に、組になりうる札が {len(mine)} 個あり 1 つに決まりません（電線番号やラベルで区別してください）"
        diagnostics.append({"kind": "sheet_link_unresolved", "project_id": t.project_id, "symbol_ref": t.symbol_ref,
                            "target": t.target, "message": message})
    for t, u, how in pairs:
        if how == "guessed":
            diagnostics.append({"kind": "sheet_link_guessed", "project_id": t.project_id, "symbol_ref": t.symbol_ref,
                                "target": t.target,
                                "message": f"札 {t.label or t.symbol_ref} と {u.own_sheet} の札 {u.label or u.symbol_ref} を、"
                                           "向かい合う札が 1 組だけのためつなぎました（電線番号があれば確かめられます）"})
    return pairs, diagnostics


def page_references(page: dict[str, Any]) -> list[dict[str, Any]]:
    """図面の中の行先札と種別「シート間参照」の配線が指すシート番号（図面セットの過不足を見るため。DD-0041）。"""
    refs = [{"target": t, "symbol_ref": s["ref"], "label": s.get("label") or "", "source": "symbol"}
            for s in page.get("symbols") or [] if (t := sheet_tag_target(s)) is not None]
    for c in page.get("connections") or []:
        if c.get("kind") != "sheet_ref":
            continue
        m = _SHEET.search(_norm(c.get("external_ref")).replace(" ", ""))
        if m:
            refs.append({"target": m.group(0), "symbol_ref": c.get("to_symbol_ref"), "label": c.get("external_ref") or "",
                         "source": "connection"})
    return refs
