"""AI の結果（シンボル・端子・配線）のうち、エディタに足す候補を求める。DB を使わない。

取り込みは追加だけを行う。既存のシンボル・端子・配線は変更も削除もしない。
取り込んだ後の内容でもう一度求めると、追加は空になる。
"""

from __future__ import annotations

import math
import re
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal

from services.geometry import sanitize_box

# 黒丸は 1 つの電気的な点なので、AI が黒丸の上下に置いた端子を 1 つにまとめる（人の登録と同じ形）。
NODE_CLASSES = frozenset({"junction", "connector", "terminal", "power_bus"})
# 黒丸は 8〜15 px と小さく、枠の重なりでは描いた枠の大きさで一致が決まるため、中心の距離で比べる。
POINT_CLASSES = frozenset({"junction", "power_bus"})
# 記号の中の端子どうしの接続（AI の internal）を取り込む種別。人の登録では接点・電磁弁だけ両端をつなぐ。
# その他の枠（行先の矢印・カード）やリレーコイルの両端は別の電線なので、つなぐと別のネットが 1 つになる。
INTERNAL_LINK_CLASSES = frozenset({"contact_a", "contact_b", "solenoid"})
MATCH_IOU = 0.5
POINT_MATCH_PX = 12.0
TERMINAL_MATCH_PX = 10.0
# 端子番号を読めなかった端子の仮の名前。エディタでは手入力と同じ連番にする。
UNREAD_TERMINAL_NAME = re.compile(r"P\d+")

Source = Literal["structure", "predictions"]
Endpoint = tuple[int, int]  # (AI シンボルの番号, その端子の番号)


@dataclass(frozen=True)
class AiTerminal:
    tx: float
    ty: float
    name: str | None


@dataclass(frozen=True)
class AiSymbol:
    class_key: str
    cx: float
    cy: float
    w: float
    h: float
    # 一致判定に使う点（正規化）。黒丸は黒丸の中心、それ以外は枠の中心。
    px: float
    py: float
    confidence: float | None = None
    label: str | None = None
    terminals: tuple[AiTerminal, ...] = ()


@dataclass(frozen=True)
class AiCandidates:
    source: Source
    symbols: tuple[AiSymbol, ...]
    links: tuple[tuple[Endpoint, Endpoint], ...] = ()
    skipped: dict[str, int] = field(default_factory=dict)


@dataclass
class ImportPlan:
    symbols: list[dict[str, Any]]
    terminals: list[dict[str, Any]]
    connections: list[dict[str, Any]]
    matched_symbols: int
    kept_terminals: int
    kept_connections: int
    reused_terminal_refs: set[str]
    reused_connection_indexes: set[int]


def _read_name(name: Any) -> str | None:
    text = str(name or "").strip()
    return None if not text or UNREAD_TERMINAL_NAME.fullmatch(text) else text


def _class_of(class_name: str, dot_role: str | None) -> str:
    """配線が 3 方向以上に分かれる黒丸は、コネクタとして検出されていても分岐点にする。"""
    if class_name == "connector" and dot_role == "junction":
        return "junction"
    return class_name


def from_structure(structure: dict[str, Any], class_keys: Iterable[str]) -> AiCandidates:
    """構造解析の結果から候補を作る。重複した黒丸と、表題欄・リレー表の中の文字は候補にしない。"""
    known = set(class_keys)
    size = structure["image_size"]
    width, height = float(size["width"]), float(size["height"])
    symbols: list[AiSymbol] = []
    port_of: dict[tuple[str, str], Endpoint] = {}
    mirrored: list[int] = []
    skipped = {"duplicate_dots": 0, "text_zone": 0}
    for s in structure.get("symbols", []):
        role = s.get("dot_role") or {}
        if role.get("suppressed"):
            skipped["duplicate_dots"] += 1
            continue
        if s.get("status") == "suppressed_text_zone":
            skipped["text_zone"] += 1
            continue
        class_key = _class_of(s["class_name"], role.get("role"))
        if class_key not in known:
            continue
        box = s["bbox"]
        cx, cy, w, h = sanitize_box(
            (box["x0"] + box["x1"]) / 2 / width, (box["y0"] + box["y1"]) / 2 / height,
            (box["x1"] - box["x0"]) / width, (box["y1"] - box["y0"]) / height,
        )
        ports = s.get("terminals") or []
        dot = (role.get("dot") or {}).get("center")
        index = len(symbols)
        if class_key in NODE_CLASSES:
            if dot:
                x, y = dot[0] / width, dot[1] / height
            elif ports:
                x = sum(p["point"][0] for p in ports) / len(ports) / width
                y = sum(p["point"][1] for p in ports) / len(ports) / height
            else:
                x, y = cx, cy
            name = next((n for p in ports if (n := _read_name(p.get("name")))), None)
            terminals = (AiTerminal(x, y, name),)
            port_of.update({(s["id"], p["ref"]): (index, 0) for p in ports})
        else:
            terminals = tuple(AiTerminal(p["point"][0] / width, p["point"][1] / height, _read_name(p.get("name"))) for p in ports)
            port_of.update({(s["id"], p["ref"]): (index, i) for i, p in enumerate(ports)})
            if class_key in INTERNAL_LINK_CLASSES and len(terminals) == 1:
                # 接点の両端は配線の向きに沿って向かい合う。片端しか見つからなければ枠の中心で折り返す。
                t = terminals[0]
                vertical = ports[0].get("wire_orientation", "v" if abs(t.ty - cy) * h >= abs(t.tx - cx) * w else "h") == "v"
                terminals += (AiTerminal(t.tx, 2 * cy - t.ty, None) if vertical else AiTerminal(2 * cx - t.tx, t.ty, None),)
                mirrored.append(index)
        px, py = (dot[0] / width, dot[1] / height) if dot else (cx, cy)
        symbols.append(AiSymbol(
            class_key, cx, cy, w, h, px, py,
            confidence=s.get("confidence"), label=(s.get("label") or "").strip() or None, terminals=terminals,
        ))
    links: list[tuple[Endpoint, Endpoint]] = [((i, 0), (i, 1)) for i in mirrored]
    seen: set[frozenset[Endpoint]] = {frozenset(link) for link in links}
    for conn in (structure.get("from_to") or {}).get("connections", []):
        a = port_of.get((conn["from"]["symbol_id"], conn["from"]["terminal_ref"]))
        b = port_of.get((conn["to"]["symbol_id"], conn["to"]["terminal_ref"]))
        if a is None or b is None or a == b or frozenset((a, b)) in seen:
            continue
        if a[0] == b[0] and symbols[a[0]].class_key not in INTERNAL_LINK_CLASSES:
            continue
        seen.add(frozenset((a, b)))
        links.append((a, b))
    return AiCandidates("structure", tuple(symbols), tuple(links), skipped)


def from_predictions(predictions: Iterable[dict[str, Any]], class_keys: Iterable[str]) -> AiCandidates:
    """推論のシンボル候補（正規化座標）だけから候補を作る。端子と配線は無い。"""
    known = set(class_keys)
    symbols = tuple(
        AiSymbol(p["class_key"], p["cx"], p["cy"], p["w"], p["h"], p["cx"], p["cy"], confidence=p.get("confidence"))
        for p in predictions
        if p["class_key"] in known
    )
    return AiCandidates("predictions", symbols)


def _iou(a: AiSymbol, s: dict[str, Any]) -> float:
    ix = min(a.cx + a.w / 2, s["cx"] + s["w"] / 2) - max(a.cx - a.w / 2, s["cx"] - s["w"] / 2)
    iy = min(a.cy + a.h / 2, s["cy"] + s["h"] / 2) - max(a.cy - a.h / 2, s["cy"] - s["h"] / 2)
    if ix <= 0 or iy <= 0:
        return 0.0
    inter = ix * iy
    return inter / (a.w * a.h + s["w"] * s["h"] - inter)


def _similarity(a: AiSymbol, s: dict[str, Any], width: int, height: int) -> float:
    """枠の IoU。黒丸は中心の距離で、POINT_MATCH_PX で MATCH_IOU になるように換算する。種別は問わない
    （人が種別を直したシンボルにも重ねない）。"""
    if a.class_key in POINT_CLASSES or s["class_key"] in POINT_CLASSES:
        distance = math.hypot((a.px - s["cx"]) * width, (a.py - s["cy"]) * height)
        return 1.0 - (1.0 - MATCH_IOU) * distance / POINT_MATCH_PX if distance <= POINT_MATCH_PX else 0.0
    return _iou(a, s)


def _greedy(pairs: Iterable[tuple[float, int, int]]) -> dict[int, int]:
    """似ている組から 1 対 1 に対応付ける。pairs は (似ている度合い, 左の番号, 右の番号)。"""
    out: dict[int, int] = {}
    used: set[int] = set()
    for _, left, right in sorted(pairs, key=lambda p: (-p[0], p[1], p[2])):
        if left in out or right in used:
            continue
        out[left] = right
        used.add(right)
    return out


def _terminal_matches(a: AiSymbol, existing: dict[str, Any], width: int, height: int) -> dict[int, str]:
    """AI の端子と、既存シンボルの近い端子の対応。黒丸どうしは、既存の端子のうち最も近いものに寄せる。"""
    current = existing.get("terminals") or []
    if not current or not a.terminals:
        return {}

    def distance(t: AiTerminal, e: dict[str, Any]) -> float:
        return math.hypot((t.tx - e["tx"]) * width, (t.ty - e["ty"]) * height)

    if a.class_key in NODE_CLASSES and existing["class_key"] in NODE_CLASSES:
        return {0: min(current, key=lambda e: distance(a.terminals[0], e))["ref"]}
    pairs = [
        (-d, i, j)
        for i, t in enumerate(a.terminals)
        for j, e in enumerate(current)
        if (d := distance(t, e)) <= TERMINAL_MATCH_PX
    ]
    return {i: current[j]["ref"] for i, j in _greedy(pairs).items()}


def _free_ref(make: Callable[[int], str], start: int, used: set[str]) -> str:
    n = start
    while make(n) in used:
        n += 1
    used.add(make(n))
    return make(n)


def _connection_keys(endpoint_a: tuple[str, str | None], endpoint_b: tuple[str, str | None]) -> list[frozenset]:
    """既存の配線と同じとみなす鍵。人がシンボル単位（端子なし）で引いた配線も同じ配線とみなす。"""
    (sa, ta), (sb, tb) = endpoint_a, endpoint_b
    return [frozenset(((sa, x), (sb, y))) for x in (ta, None) for y in (tb, None)]


def plan_import(
    candidates: AiCandidates,
    symbols: Sequence[dict[str, Any]],
    connections: Sequence[dict[str, Any]],
    width: int,
    height: int,
) -> ImportPlan:
    """エディタの現在の内容（symbols / connections は保存形式の dict）に足す候補を求める。"""
    matches = _greedy(
        (score, i, j)
        for i, a in enumerate(candidates.symbols)
        for j, s in enumerate(symbols)
        if (score := _similarity(a, s, width, height)) >= MATCH_IOU
    )
    used_symbol_refs = {s["ref"] for s in symbols}
    used_terminal_refs = {t["ref"] for s in symbols for t in s.get("terminals") or []}
    refs: list[str] = []
    new_symbols: list[dict[str, Any]] = []
    new_terminals: list[dict[str, Any]] = []
    terminal_ref: dict[Endpoint, str] = {}
    reused_terminals: set[str] = set()
    kept_terminals = 0
    for i, a in enumerate(candidates.symbols):
        existing = symbols[matches[i]] if i in matches else None
        if existing is not None:
            ref = existing["ref"]
            have = len(existing.get("terminals") or [])
            reused = _terminal_matches(a, existing, width, height)
        else:
            ref = _free_ref(lambda n: f"SYM-{n:04d}", len(symbols) + len(new_symbols) + 1, used_symbol_refs)
            have, reused = 0, {}
        refs.append(ref)
        added: list[dict[str, Any]] = []
        for t_index, t in enumerate(a.terminals):
            if t_index in reused:
                terminal_ref[(i, t_index)] = reused[t_index]
                reused_terminals.add(reused[t_index])
                kept_terminals += 1
                continue
            number = have + len(added) + 1
            t_ref = _free_ref(lambda n, ref=ref: f"{ref}-T{n}", number, used_terminal_refs)
            added.append({
                "ref": t_ref, "name": t.name or str(number),
                "tx": min(1.0, max(0.0, t.tx)), "ty": min(1.0, max(0.0, t.ty)), "origin": "inference",
            })
            terminal_ref[(i, t_index)] = t_ref
        if existing is not None:
            new_terminals.extend({"symbol_ref": ref, **t} for t in added)
        else:
            new_symbols.append({
                "ref": ref, "class_key": a.class_key, "label": a.label,
                "cx": a.cx, "cy": a.cy, "w": a.w, "h": a.h, "note": None,
                "origin": "inference", "confidence": a.confidence, "terminals": added,
            })

    existing_keys: dict[frozenset, int] = {}
    for index, c in enumerate(connections):
        key = frozenset(((c["from_symbol_ref"], c.get("from_terminal_ref")), (c["to_symbol_ref"], c.get("to_terminal_ref"))))
        existing_keys.setdefault(key, index)
    added_keys: set[frozenset] = set()
    new_connections: list[dict[str, Any]] = []
    reused_connections: set[int] = set()
    kept_connections = 0
    for a, b in candidates.links:
        end_a, end_b = (refs[a[0]], terminal_ref[a]), (refs[b[0]], terminal_ref[b])
        if end_a == end_b:
            kept_connections += 1
            continue
        hit = next((existing_keys[k] for k in _connection_keys(end_a, end_b) if k in existing_keys), None)
        if hit is not None:
            kept_connections += 1
            reused_connections.add(hit)
            continue
        key = frozenset((end_a, end_b))
        if key in added_keys:
            continue
        added_keys.add(key)
        new_connections.append({
            "from_symbol_ref": end_a[0], "from_terminal_ref": end_a[1],
            "to_symbol_ref": end_b[0], "to_terminal_ref": end_b[1],
            "wire_no": None, "net_id": None, "kind": "wire", "external_ref": None, "note": None,
            "origin": "inference",
        })
    return ImportPlan(
        new_symbols, new_terminals, new_connections, len(matches),
        kept_terminals, kept_connections, reused_terminals, reused_connections,
    )
