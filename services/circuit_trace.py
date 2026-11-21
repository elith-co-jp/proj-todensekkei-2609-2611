"""展開接続図を読む手順の機械化: 成立条件の逆引き・順引きと、成立・不成立・判定不能の判定（DD-0035）。DB を使わない。

人が図面を読む手順（2026-11-18 の画面共有で説明された順）をそのまま処理にする。

1. 問いを置き、対象を一点に決める（逆引き: このコイル・出力が動く条件は？ / 順引き: この器具が変わったら何に影響する？）。
2. 記号を読み、参照先を照合する: 接点の器具番号から、同じ器具番号のコイルを探す。コイルが図面セットに無ければ、
   接点のラベルの参照シートで「範囲外の外部入力」「参照先欠落（そのシートを受け取っていない）」「未解決（参照先に
   コイルが無い・器具番号が読めない）」に分ける。
3. コイルだけでなく、電源側（P）から戻り側（N）までの経路を見る: 対象の負荷の両端から、接点だけを通って電源と
   戻りに着く経路を全部列挙する。コイルより下（戻り側）の接点も条件に含める。
4. 直列は AND、並列は OR: 1 本の経路（枝）の接点がすべて導通して初めて成立。枝のどれか 1 本が端から端まで導通すれば成立。
   a 接点は器具の動作で閉、b 接点は動作で開。
5. 接点を動かす元へ繰り返し辿る: 条件に出てくる器具がリレーなら、そのコイルの成立条件を同じ手順で求める。
   外部入力は図面だけで補わず、値が与えられなければ「不明」。参照先欠落・未解決も「不明」。
6. 判定: 枝ごとに 3 値（成立・不成立・判定不能）で評価し、どの接点で切れたか・どれが不明かを、ページと位置つきで返す。

位置の正本はアノテーション（pages）で、ここでは素子の (図面 ID, 記号 ref) からページ名・中心の座標を引いて根拠に付ける。
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict, deque
from dataclasses import dataclass
from typing import Any

from analysis.text_extraction import SHEET_REF
from services import sheet_links
from services.circuit_model import GROUND, SUPPLY, Circuit, Element, canonical_device

ON, OFF, UNKNOWN = "成立", "不成立", "判定不能"
SIDE_SUPPLY, SIDE_RETURN = "電源側", "戻り側"
# 1 つの負荷の枝（電源から戻りまでの経路）の数の上限。超えたら打ち切り、結果に truncated を付ける。
MAX_BRANCHES = 256
MAX_DEPTH = 12

# 器具の出どころ（接点を動かす元）
RELAY = "relay"                  # 図面セットにコイルがあるリレー
INPUT = "input"                  # この図面範囲の外部入力（押しボタン・I/O・上位からの指令など）
MISSING_REFERENCE = "missing_reference"  # 参照先のシートを受け取っていない
UNRESOLVED = "unresolved"        # 参照先にコイルが無い・器具番号が読めない
SOURCE_LABEL = {RELAY: "リレー（図面内にコイル）", INPUT: "外部入力（図面範囲外）",
                MISSING_REFERENCE: "参照先欠落", UNRESOLVED: "未解決"}

_SHEET = re.compile(SHEET_REF)
_TRUTHY = {"1", "true", "yes", "y", "on", "はい", "外部", "外部入力", "io", "i/o"}

Literal = tuple[str, str]  # (器具番号, "a" / "b")


def _is_supply(node: str) -> bool:
    return node == SUPPLY or node.startswith(f"{SUPPLY}_")


def _is_bus(node: str) -> bool:
    return node == GROUND or _is_supply(node)


# ------------------------------------------------------------------------------------------- 根拠（位置）


class Locator:
    """(図面 ID, 記号 ref) → ページ名・ラベル・中心の座標（0〜1）。pages が無ければ ID だけ。"""

    def __init__(self, pages: list[dict[str, Any]] | None = None) -> None:
        self.pages = pages or []
        self.names = {p["id"]: p.get("sheet_no") or p.get("name") or str(p["id"]) for p in self.pages}
        self.symbols = {(p["id"], s["ref"]): s for p in self.pages for s in p.get("symbols") or []}

    def __call__(self, e: Element) -> dict[str, Any]:
        s = self.symbols.get((e.project_id, e.symbol_ref)) or {}
        out = {"project_id": e.project_id, "page": self.names.get(e.project_id, str(e.project_id)),
               "symbol_ref": e.symbol_ref, "label": e.label or s.get("label") or ""}
        if s:
            out |= {"cx": s.get("cx"), "cy": s.get("cy"), "w": s.get("w"), "h": s.get("h")}
        return out


# ------------------------------------------------------------------------------------------- 器具の出どころ


def device_sources(circuit: Circuit, pages: list[dict[str, Any]] | None = None,
                   external: list[str] | tuple[str, ...] = ()) -> dict[str, dict[str, Any]]:
    """接点の器具番号 → 出どころ（RELAY / INPUT / MISSING_REFERENCE / UNRESOLVED）と理由。

    external: 外部入力と決めた器具番号（I/O 表から）。接点の属性 external_io が真でも外部入力にする。
    """
    locate = Locator(pages)
    page_sheets = {sheet_links.base_sheet(sheet_links.page_sheet(p)) for p in pages or [] if sheet_links.page_sheet(p)}
    declared = {canonical_device(d) for d in external}
    out: dict[str, dict[str, Any]] = {}
    for e in circuit.elements:
        if e.kind not in ("a", "b") or not e.device:
            continue
        dev = e.device
        if dev in circuit.coils:
            out.setdefault(dev, {"device": dev, "source": RELAY, "reason": "同じ器具番号のコイルが図面セットにある",
                                 "coils": [locate(c) for c in circuit.coils[dev]], "contacts": []})
            out[dev]["contacts"].append(locate(e))
            continue
        symbol = locate.symbols.get((e.project_id, e.symbol_ref)) or {}
        attrs = symbol.get("attributes") or {}
        current = out.get(dev)
        found: dict[str, Any]
        if canonical_device(dev) in declared or str(attrs.get("external_io") or "").strip().lower() in _TRUTHY:
            found = {"source": INPUT, "reason": "外部入力（I/O）と指定されている"}
        elif "（器具番号なし）" in dev:
            found = {"source": UNRESOLVED, "reason": "接点の器具番号が読めない"}
        else:
            m = _SHEET.search((e.label or "").upper().replace(" ", ""))
            ref_sheet = m.group(0) if m else None
            if ref_sheet and pages and sheet_links.base_sheet(ref_sheet) not in page_sheets:
                found = {"source": MISSING_REFERENCE, "ref_sheet": ref_sheet,
                         "reason": f"参照先のシート {ref_sheet} が図面セットに無い"}
            elif ref_sheet and pages:
                found = {"source": UNRESOLVED, "ref_sheet": ref_sheet,
                         "reason": f"参照先のシート {ref_sheet} に {dev} のコイルが見つからない"}
            else:
                found = {"source": INPUT, "reason": "コイルが図面セットに無く、参照先の記載も無い（図面範囲外の入力とみなす）"}
        # 同じ器具の接点で判断が分かれたら、確かでない方（未解決 > 参照先欠落 > 外部入力）を採る。
        rank = {INPUT: 0, MISSING_REFERENCE: 1, UNRESOLVED: 2}
        if current is None or rank[found["source"]] > rank[current["source"]]:
            out[dev] = {"device": dev, **found, "contacts": (current or {}).get("contacts", [])}
        out[dev].setdefault("contacts", []).append(locate(e))
    return out


# ------------------------------------------------------------------------------------------- 経路（枝）


@dataclass(frozen=True)
class Branch:
    """電源から負荷を通って戻りまでの 1 本の経路。contacts は電源側から順、sides は各接点が負荷の電源側か戻り側か。"""

    load: Element
    contacts: tuple[Element, ...]
    sides: tuple[str, ...]
    supply: str

    def literals(self) -> frozenset[Literal]:
        return frozenset((e.device or "", e.kind) for e in self.contacts)

    def contradictory(self) -> bool:
        """同じ器具の a 接点と b 接点が直列（どの状態でも導通しない枝）。"""
        kinds: dict[str, set[str]] = defaultdict(set)
        for dev, kind in self.literals():
            kinds[dev].add(kind)
        return any(len(k) > 1 for k in kinds.values())


def _adjacency(circuit: Circuit) -> dict[str, list[tuple[Element, str]]]:
    adj: dict[str, list[tuple[Element, str]]] = defaultdict(list)
    for e in circuit.elements:
        if e.kind in ("a", "b", "d") and e.a != e.b:
            adj[e.a].append((e, e.b))
            adj[e.b].append((e, e.a))
    return adj


def _passes(e: Element, node: str, nxt: str, upstream: bool) -> bool:
    """ダイオードを node → nxt の向きにたどれるか（DD-0037）。電流は アノード（a）→ カソード（b）に流れる。
    upstream は負荷から電源へ電流と逆向きにたどる場合。向きの分からないダイオードはどちら向きにもたどれる。"""
    if e.kind != "d" or not e.directed:
        return True
    return (node, nxt) == ((e.b, e.a) if upstream else (e.a, e.b))


def _walks(adj: dict[str, list[tuple[Element, str]]], start: str, goal, limit: int, *, upstream: bool = False):
    """start から接点（とダイオード）だけを通って goal(node) に着く単純な経路。母線（P・0）は終点にしかならない。
    ダイオードは条件の変数にならないので経路の素子には入れず、向きだけを守る。"""
    out: list[tuple[list[Element], list[str]]] = []
    if goal(start):
        return [([], [start])], False
    stack = [(start, [], [start])]
    truncated = False
    while stack:
        node, elems, nodes = stack.pop()
        for e, nxt in adj.get(node, []):
            if nxt in nodes or any(x is e for x in elems) or not _passes(e, node, nxt, upstream):
                continue
            path = elems if e.kind == "d" else elems + [e]
            if goal(nxt):
                out.append((path, nodes + [nxt]))
                if len(out) >= limit:
                    return out, True
            elif not _is_bus(nxt):
                stack.append((nxt, path, nodes + [nxt]))
    return out, truncated


def branches(circuit: Circuit, load: Element, *, limit: int = MAX_BRANCHES,
             adj: dict[str, list[tuple[Element, str]]] | None = None) -> tuple[list[Branch], bool]:
    """負荷 load を通る電源 → 戻りの枝。他の負荷は通らない。(枝, 上限で打ち切ったか)。"""
    adj = adj if adj is not None else _adjacency(circuit)
    found: dict[tuple[str, ...], Branch] = {}
    truncated = False
    for top, bottom in ((load.a, load.b), (load.b, load.a)):
        ups, t1 = _walks(adj, top, _is_supply, limit, upstream=True)
        downs, t2 = _walks(adj, bottom, lambda n: n == GROUND, limit)
        truncated |= t1 or t2
        for up_elems, up_nodes in ups:
            for down_elems, down_nodes in downs:
                if set(up_nodes) & set(down_nodes):
                    continue
                contacts = tuple(reversed(up_elems)) + tuple(down_elems)
                key = tuple(e.name for e in contacts)
                if key in found:
                    continue
                sides = (SIDE_SUPPLY,) * len(up_elems) + (SIDE_RETURN,) * len(down_elems)
                found[key] = Branch(load, contacts, sides, up_nodes[-1])
                if len(found) >= limit:
                    return list(found.values()), True
    return list(found.values()), truncated


# ------------------------------------------------------------------------------------------- 論理式


def _lit(device: str, kind: str) -> dict[str, Any]:
    return {"lit": device, "kind": kind}


def factor(terms: list[frozenset[Literal]]) -> dict[str, Any]:
    """積和形（枝ごとの AND の OR）を、共通の接点をくくり出した式の木にする。"""
    terms = list(dict.fromkeys(terms))
    # 吸収: ある枝の接点の集合を含む枝は、その枝が成立すれば成立するので不要。
    terms = [t for t in terms if not any(o < t for o in terms)]
    if not terms:
        return {"const": False}
    if any(not t for t in terms):
        return {"const": True}
    common = frozenset.intersection(*terms)
    rest = [t - common for t in terms]
    parts = [_lit(d, k) for d, k in sorted(common)]
    if any(not t for t in rest):
        tail = None  # くくり出した残りのどれかが空 → 残りは常に成立
    elif len(rest) == 1:
        parts += [_lit(d, k) for d, k in sorted(rest[0])]
        tail = None
    else:
        counts = Counter(x for t in rest for x in t)
        pick, n = max(sorted(counts.items()), key=lambda kv: kv[1])
        if n == 1:
            tail = {"op": "or", "args": [_and([_lit(d, k) for d, k in sorted(t)]) for t in sorted(rest, key=sorted)]}
        else:
            with_x = [t - {pick} for t in rest if pick in t]
            without = [t for t in rest if pick not in t]
            args = [_and([_lit(*pick), factor(with_x)])]
            if without:
                args.append(factor(without))
            tail = args[0] if len(args) == 1 else {"op": "or", "args": _flatten("or", args)}
    if tail is not None:
        parts.append(tail)
    return _and(parts)


def _flatten(op: str, args: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for a in args:
        if a.get("op") == op:
            out += a["args"]
        elif a.get("const") is True and op == "and":
            continue
        else:
            out.append(a)
    return out


def _and(parts: list[dict[str, Any]]) -> dict[str, Any]:
    parts = _flatten("and", parts)
    if not parts:
        return {"const": True}
    return parts[0] if len(parts) == 1 else {"op": "and", "args": parts}


def _quote(name: str) -> str:
    return f'"{name}"' if re.search(r"[\s()&|!~¬∧∨\"]", name) or name.upper() in ("AND", "OR", "NOT", "TRUE", "FALSE") else name


def to_text(tree: dict[str, Any], *, ascii_ops: bool = False) -> str:
    """式の木 → 文字列。既定は ∧ ∨ ¬、ascii_ops なら & | !（parse で読み戻せる）。"""
    AND, OR, NOT = (" & ", " | ", "!") if ascii_ops else (" ∧ ", " ∨ ", "¬")
    if "const" in tree:
        return "TRUE" if tree["const"] else "FALSE"
    if "lit" in tree:
        return (NOT if tree.get("kind") == "b" else "") + _quote(tree["lit"])
    if tree["op"] == "not":
        return NOT + "(" + to_text(tree["args"][0], ascii_ops=ascii_ops) + ")"
    sep = AND if tree["op"] == "and" else OR
    inner = []
    for a in tree["args"]:
        text = to_text(a, ascii_ops=ascii_ops)
        inner.append(f"({text})" if a.get("op") in ("and", "or") and a["op"] != tree["op"] else text)
    return sep.join(inner)


_TOKEN = re.compile(r'\s*(?:(?P<q>"[^"]*"|「[^」]*」)|(?P<op>[()&|!~¬∧∨])|(?P<w>[^\s()&|!~¬∧∨"「」]+))')


def parse(text: str) -> dict[str, Any]:
    """「A & (B | C) & !D」「A ∧ (B ∨ C)」「A AND (B OR NOT C)」を式の木にする。空白を含む器具番号は "..." か「...」で囲む。"""
    tokens: list[tuple[str, str]] = []
    pos = 0
    text = text.strip()
    while pos < len(text):
        m = _TOKEN.match(text, pos)
        if not m or m.end() == pos:
            raise ValueError(f"式を読めません: {text[pos:pos + 10]!r}")
        pos = m.end()
        if m.group("q"):
            tokens.append(("id", m.group("q")[1:-1]))
        elif m.group("op"):
            op = m.group("op")
            tokens.append(("op", {"∧": "&", "∨": "|", "¬": "!", "~": "!"}.get(op, op)))
        else:
            word = m.group("w")
            upper = word.upper()
            if upper in ("AND", "OR", "NOT"):
                tokens.append(("op", {"AND": "&", "OR": "|", "NOT": "!"}[upper]))
            elif upper in ("TRUE", "FALSE"):
                tokens.append(("const", upper))
            else:
                tokens.append(("id", word))
    i = 0

    def peek() -> tuple[str, str] | None:
        return tokens[i] if i < len(tokens) else None

    def take() -> tuple[str, str]:
        nonlocal i
        if i >= len(tokens):
            raise ValueError("式が途中で終わっています")
        i += 1
        return tokens[i - 1]

    def expr_or() -> dict[str, Any]:
        args = [expr_and()]
        while peek() == ("op", "|"):
            take()
            args.append(expr_and())
        return args[0] if len(args) == 1 else {"op": "or", "args": _flatten("or", args)}

    def expr_and() -> dict[str, Any]:
        args = [expr_not()]
        while peek() == ("op", "&"):
            take()
            args.append(expr_not())
        return args[0] if len(args) == 1 else {"op": "and", "args": _flatten("and", args)}

    def expr_not() -> dict[str, Any]:
        if peek() == ("op", "!"):
            take()
            inner = expr_not()
            if "lit" in inner:
                return _lit(inner["lit"], "a" if inner["kind"] == "b" else "b")
            return {"op": "not", "args": [inner]}
        return atom()

    def atom() -> dict[str, Any]:
        kind, value = take()
        if (kind, value) == ("op", "("):
            inner = expr_or()
            if take() != ("op", ")"):
                raise ValueError("括弧が閉じていません")
            return inner
        if kind == "const":
            return {"const": value == "TRUE"}
        if kind == "id":
            return _lit(value, "a")
        raise ValueError(f"式の {value!r} の位置がおかしい")

    tree = expr_or()
    if i != len(tokens):
        raise ValueError("式の終わりに余分な文字があります")
    return tree


def variables(tree: dict[str, Any]) -> set[str]:
    if "lit" in tree:
        return {tree["lit"]}
    return set().union(*(variables(a) for a in tree.get("args", [])))


def kleene(tree: dict[str, Any], value) -> bool | None:
    """3 値の評価。value(器具番号) は True（動作）/ False（非動作）/ None（不明）。"""
    if "const" in tree:
        return bool(tree["const"])
    if "lit" in tree:
        v = value(tree["lit"])
        return None if v is None else (v if tree.get("kind", "a") == "a" else not v)
    vals = [kleene(a, value) for a in tree["args"]]
    if tree["op"] == "not":
        return None if vals[0] is None else not vals[0]
    if tree["op"] == "and":
        return False if False in vals else (None if None in vals else True)
    return True if True in vals else (None if None in vals else False)


def rename(tree: dict[str, Any], mapping) -> dict[str, Any]:
    if "lit" in tree:
        return {**tree, "lit": mapping(tree["lit"])}
    if "args" in tree:
        return {**tree, "args": [rename(a, mapping) for a in tree["args"]]}
    return tree


def equivalent(a: dict[str, Any], b: dict[str, Any], *, max_exact: int = 16, samples: int = 4096) -> dict[str, Any]:
    """2 つの式が同じ論理か。変数が max_exact 個以下なら全通り、多ければ乱数の組で。違えば反例を 1 つ返す。"""
    import itertools
    import random

    names = sorted(variables(a) | variables(b))
    if len(names) <= max_exact:
        combos = itertools.product([False, True], repeat=len(names))
        exhaustive = True
    else:
        rng = random.Random(0)
        combos = ([rng.random() < 0.5 for _ in names] for _ in range(samples))
        exhaustive = False
    for combo in combos:
        values = dict(zip(names, combo, strict=False))
        if kleene(a, values.get) != kleene(b, values.get):
            return {"equal": False, "exhaustive": exhaustive, "counterexample": values}
    return {"equal": True, "exhaustive": exhaustive, "counterexample": None}


# ------------------------------------------------------------------------------------------- 対象を決める


def resolve_target(circuit: Circuit, target: str | dict[str, Any]) -> tuple[str, list[Element]]:
    """器具番号（コイル）・負荷のラベル・{project_id, symbol_ref} から負荷の素子。"""
    loads = [e for e in circuit.elements if e.kind == "load"]
    if isinstance(target, dict):
        key = (int(target["project_id"]), str(target["symbol_ref"]))
        hit = [e for e in loads if (e.project_id, e.symbol_ref) == key]
        if not hit:
            raise ValueError(f"負荷 {key[0]}:{key[1]} が回路にありません")
        e = hit[0]
        return (e.device if e.device in circuit.coils else e.label or e.symbol_ref), (
            circuit.coils.get(e.device, hit))
    name = str(target).strip()
    if name in circuit.coils:
        return name, circuit.coils[name]
    canon = canonical_device(name)
    for dev, es in circuit.coils.items():
        if canonical_device(dev) == canon:
            return dev, es
    hit = [e for e in loads if (e.label or "").strip() == name or canonical_device((e.label or "").strip()) == canon]
    if not hit:
        hit = [e for e in loads if (e.label or "").split() and canonical_device(e.label.split()[0]) == canon]
    if not hit:
        raise ValueError(f"{name} というコイル・負荷が回路にありません")
    return name, hit


def load_name(circuit: Circuit, e: Element) -> str:
    return e.device if e.device in circuit.coils else (e.label or f"{e.project_id}:{e.symbol_ref}")


# ------------------------------------------------------------------------------------------- 逆引きと判定


class Tracer:
    """1 つの回路の成立条件を求め、3 値で判定する。条件はキャッシュする。"""

    def __init__(self, circuit: Circuit, pages: list[dict[str, Any]] | None = None, *,
                 external: list[str] | tuple[str, ...] = (), limit: int = MAX_BRANCHES) -> None:
        self.circuit = circuit
        self.locate = Locator(pages)
        self.sources = device_sources(circuit, pages, external)
        self.limit = limit
        self._adj = _adjacency(circuit)
        self._cache: dict[str, dict[str, Any]] = {}
        # 行先の分からない札（参照先欠落・組にならない札）が着くノード → 札の説明。
        self.open_ends: dict[str, dict[str, Any]] = {}
        unresolved = {(d.get("project_id"), d.get("symbol_ref")): d for d in circuit.diagnostics
                      if d.get("kind") == "sheet_link_unresolved"}
        for key, node in circuit.node_of.items():
            d = unresolved.get((key[0], key[1]))
            if d is not None and not _is_bus(node):
                self.open_ends.setdefault(node, {"page": self.locate.names.get(key[0], str(key[0])), "symbol_ref": key[1],
                                                 "target": d.get("target"), "message": d.get("message")})

    def open_ends_near(self, loads: list[Element]) -> list[dict[str, Any]]:
        """負荷から接点（開閉を問わない）でたどれる、行先の分からない札。母線は越えない。"""
        if not self.open_ends:
            return []
        start = [n for e in loads for n in (e.a, e.b) if not _is_bus(n)]
        seen, queue, found = set(start), deque(start), []
        while queue:
            node = queue.popleft()
            if node in self.open_ends:
                found.append(self.open_ends[node])
            for _e, nxt in self._adj.get(node, []):
                if nxt not in seen and not _is_bus(nxt):
                    seen.add(nxt)
                    queue.append(nxt)
        return found

    # 条件 -----------------------------------------------------------------------------------------
    def condition(self, target: str | dict[str, Any]) -> dict[str, Any]:
        name, loads = resolve_target(self.circuit, target)
        key = name if not isinstance(target, dict) else f"{target['project_id']}:{target['symbol_ref']}"
        if key in self._cache:
            return self._cache[key]
        found: list[Branch] = []
        truncated = False
        for load in loads:
            bs, t = branches(self.circuit, load, limit=self.limit, adj=self._adj)
            found += bs
            truncated |= t
        live = [b for b in found if not b.contradictory()]
        tree = factor([b.literals() for b in live])
        out = {"target": name, "kind": "relay" if name in self.circuit.coils else "load",
               "loads": [self.locate(e) for e in loads], "branches": found, "tree": tree,
               "text": to_text(tree), "formula": to_text(tree, ascii_ops=True), "truncated": truncated,
               "devices": sorted({d for b in live for d, _ in b.literals()})}
        self._cache[key] = out
        return out

    # 判定 -----------------------------------------------------------------------------------------
    def evaluate(self, target: str | dict[str, Any], states: dict[str, bool | None] | None = None,
                 energized: dict[str, bool] | None = None, *, max_depth: int = MAX_DEPTH) -> dict[str, Any]:
        """states: 器具番号 → 動作（True）/ 非動作（False）/ 不明（None）。与えた器具はリレーでも固定する。
        energized: リレーの前の状態（自己保持のように自分の接点が条件に入るときに使う）。"""
        given = {canonical_device(k): v for k, v in (states or {}).items()}
        before = {canonical_device(k): bool(v) for k, v in (energized or {}).items()}
        memo: dict[str, tuple[bool | None, str]] = {}
        stack: list[str] = []
        cycle_hits = [0]

        def value(dev: str, depth: int = 0) -> tuple[bool | None, str]:
            c = canonical_device(dev)
            if c in given:
                return given[c], "given"
            if dev in memo:
                return memo[dev]
            src = self.sources.get(dev, {}).get("source", RELAY if dev in self.circuit.coils else INPUT)
            if src != RELAY:
                return None, src
            if dev in stack:
                # 自己保持など、辿っている途中のリレーに戻った: 前の状態を使う（無ければ不明）。
                cycle_hits[0] += 1
                return (before[c], "previous") if c in before else (None, "self_hold")
            if depth >= max_depth:
                return None, "depth"
            hits = cycle_hits[0]
            stack.append(dev)
            cond = self.condition(dev)
            v = kleene(cond["tree"], lambda d: value(d, depth + 1)[0]) if cond["branches"] else None
            stack.pop()
            result = (v, "derived" if cond["branches"] else "no_path")
            # 辿っている途中のリレーの状態に頼らずに決まった値だけを覚える。
            if cycle_hits[0] == hits:
                memo[dev] = result
            return result

        cond = self.condition(target)
        name = cond["target"]
        # 対象のリレー自身の接点（自己保持）は、前の状態で見る。
        if name in self.circuit.coils:
            stack.append(name)
        rows = []
        for b in cond["branches"]:
            contacts = []
            for e, side in zip(b.contacts, b.sides, strict=False):
                v, how = value(e.device or "")
                conducts = None if v is None else (v if e.kind == "a" else not v)
                contacts.append({**self.locate(e), "device": e.device, "kind": e.kind, "side": side,
                                 "device_state": v, "how": how, "conducts": conducts})
            if any(c["conducts"] is False for c in contacts):
                status = OFF
            elif all(c["conducts"] is True for c in contacts):
                status = ON
            else:
                status = UNKNOWN
            rows.append({"status": status, "supply": b.supply, "contacts": contacts,
                         "open": [c for c in contacts if c["conducts"] is False],
                         "unknown": [c for c in contacts if c["conducts"] is None],
                         "contradictory": b.contradictory()})
        tree_value = kleene(cond["tree"], lambda d: value(d)[0]) if rows else None
        devices = []
        for dev in cond["devices"]:
            v, how = value(dev)
            devices.append({"device": dev, "state": v, "how": how,
                            "source": self.sources.get(dev, {}).get("source"),
                            "reason": self.sources.get(dev, {}).get("reason")})
        if name in self.circuit.coils:
            stack.pop()
        reasons: list[str] = []
        if not rows:
            status = UNKNOWN
            reasons.append("電源から戻りまでの経路が見つかりません（配線の読み取り漏れ・薄い線の可能性。原本を確認してください）")
        elif any(r["status"] == ON for r in rows):
            status = ON
        elif all(r["status"] == OFF for r in rows):
            status = OFF
        else:
            status = UNKNOWN
            for r in rows:
                for c in r["unknown"]:
                    reasons.append(_unknown_reason(c, self.sources.get(c["device"] or "", {})))
            reasons = list(dict.fromkeys(reasons))
        if status != ON:
            # 図面の外へ続く札の先は見えない。そこに別の枝がありうるので「不成立」と断定しない。
            loads = resolve_target(self.circuit, target)[1]
            for end in self.open_ends_near(loads):
                status = UNKNOWN
                reasons.append(f"{end['page']} の札 {end['symbol_ref']} の先（{end.get('target') or '行先不明'}）が"
                               "図面セットに無く、その先の経路を判定できません")
        dead = sum(1 for b in cond["branches"] if b.contradictory())
        if dead:
            reasons.append(f"同じ器具の a 接点と b 接点が直列の枝が {dead} 本あり、どの状態でも導通しません（読み違いの可能性）")
        if cond["truncated"]:
            reasons.append(f"経路が多いため {self.limit} 本で打ち切りました")
        return {"target": name, "kind": cond["kind"], "loads": cond["loads"], "status": status,
                "formula": cond["formula"], "text": cond["text"], "tree": cond["tree"],
                "formula_value": tree_value, "branches": rows, "reasons": reasons, "devices": devices,
                "truncated": cond["truncated"]}

    # 繰り返し辿る ----------------------------------------------------------------------------------
    def explain(self, target: str | dict[str, Any], *, max_depth: int = MAX_DEPTH) -> dict[str, Any]:
        """逆引き: 対象の条件と、条件に出てくるリレーの条件を、外部入力・参照先欠落・未解決に着くまで辿った木。"""
        seen: set[str] = set()

        def node(name: str | dict[str, Any], depth: int) -> dict[str, Any]:
            cond = self.condition(name)
            out = {"target": cond["target"], "kind": cond["kind"], "formula": cond["formula"], "text": cond["text"],
                   "loads": cond["loads"], "branch_count": len(cond["branches"]), "truncated": cond["truncated"],
                   "inputs": [], "relays": []}
            if not cond["branches"]:
                out["note"] = "電源から戻りまでの経路が見つかりません"
            seen.add(cond["target"])
            for dev in cond["devices"]:
                src = self.sources.get(dev, {})
                kind = src.get("source", INPUT)
                if kind == RELAY:
                    if dev in seen:
                        out["relays"].append({"target": dev, "kind": "relay", "note": "上で辿った（自己保持・相互の参照）"})
                    elif depth >= max_depth:
                        out["relays"].append({"target": dev, "kind": "relay", "note": "深さの上限"})
                    else:
                        out["relays"].append(node(dev, depth + 1))
                else:
                    out["inputs"].append({"device": dev, "source": kind, "source_label": SOURCE_LABEL[kind],
                                          "reason": src.get("reason"), "ref_sheet": src.get("ref_sheet")})
            return out

        return node(target, 0)

    # 順引き ---------------------------------------------------------------------------------------
    def all_loads(self) -> list[tuple[str, dict[str, Any]]]:
        """コイル（器具番号ごと）と、コイル以外の負荷（素子ごと）。"""
        out: list[tuple[str, dict[str, Any]]] = [(dev, {"device": dev}) for dev in sorted(self.circuit.coils)]
        for e in self.circuit.elements:
            if e.kind == "load" and e.device not in self.circuit.coils:
                out.append((load_name(self.circuit, e), {"project_id": e.project_id, "symbol_ref": e.symbol_ref}))
        return out

    def effects(self, device: str, *, states: dict[str, bool | None] | None = None,
                energized: dict[str, bool] | None = None, max_depth: int = 20) -> dict[str, Any]:
        """順引き: device（リレー・入力）の接点を条件に含む負荷を段数つきで。必須（全部の枝にある）か一部の枝だけかも返す。
        states を与えると、device を反転したときの各負荷の判定の変化（成立 → 不成立 など）も返す。"""
        canon = canonical_device(device)
        known = {canonical_device(d): d for d in set(self.sources) | set(self.circuit.coils)}
        if canon not in known:
            raise ValueError(f"器具番号 {device} の接点が回路にありません")
        device = known[canon]
        conds = {name: self.condition(t.get("device", t)) for name, t in self.all_loads()}
        targets = dict(self.all_loads())
        affected: dict[str, dict[str, Any]] = {}
        queue = deque([(device, 0)])
        seen = {device}
        while queue:
            dev, depth = queue.popleft()
            if depth >= max_depth:
                continue
            for name, cond in conds.items():
                live = [b for b in cond["branches"] if not b.contradictory()]
                hits = [b for b in live if any(e.device == dev for e in b.contacts)]
                if not hits or name in affected or name == device:
                    continue
                contacts = {(e.project_id, e.symbol_ref): {**self.locate(e), "kind": e.kind, "side": side}
                            for b in hits for e, side in zip(b.contacts, b.sides, strict=False) if e.device == dev}
                affected[name] = {"target": name, "kind": cond["kind"], "depth": depth + 1, "via": dev,
                                  "role": "必須（全部の枝にある）" if len(hits) == len(live) else "一部の枝だけ（ほかの枝しだい）",
                                  "formula": cond["formula"], "contacts": list(contacts.values()), "loads": cond["loads"]}
                if name in self.circuit.coils and name not in seen:
                    seen.add(name)
                    queue.append((name, depth + 1))
        out = {"device": device, "source": self.sources.get(device, {}).get("source",
                                                                          RELAY if device in self.circuit.coils else INPUT),
               "affected": sorted(affected.values(), key=lambda x: (x["depth"], x["target"]))}
        if states is not None:
            now = {canonical_device(k): v for k, v in states.items()}
            current = now.get(canon)
            if current is None and device in self.circuit.coils:
                current = self.evaluate(device, states, energized)["formula_value"]
            if current is None:
                out["state_change"] = {"skipped": f"{device} の今の状態が不明のため、変化は判定できません"}
            else:
                flipped = {**states, device: not current}
                changes = []
                for name in affected:
                    t = targets[name] if "device" not in targets[name] else name
                    a = self.evaluate(t, {**states, device: current}, energized)["status"]
                    b = self.evaluate(t, flipped, energized)["status"]
                    changes.append({"target": name, "before": a, "after": b, "changed": a != b})
                out["state_change"] = {"from": current, "to": not current, "targets": changes}
        return out


def _unknown_reason(contact: dict[str, Any], source: dict[str, Any]) -> str:
    dev = contact.get("device") or "?"
    how = contact.get("how")
    where = f"{contact.get('page')} {contact.get('symbol_ref')}"
    if how == INPUT:
        return f"{dev}（{where}）は外部入力で、状態が与えられていません"
    if how == MISSING_REFERENCE:
        return f"{dev}（{where}）の参照先 {source.get('ref_sheet') or ''} が図面セットに無いため判定できません（参照先を求める）"
    if how == UNRESOLVED:
        return f"{dev}（{where}）は{source.get('reason') or '参照先が分からない'}ため判定できません"
    if how == "self_hold":
        return f"{dev}（{where}）は自己保持で、前の状態が与えられていません"
    if how == "no_path":
        return f"{dev} のコイルに電源から戻りまでの経路が見つかりません"
    if how == "depth":
        return f"{dev} は辿る深さの上限を超えました"
    return f"{dev}（{where}）の状態が分かりません"


# ------------------------------------------------------------------------------------------- JSON 化


def branch_json(b: Branch, locate: Locator) -> dict[str, Any]:
    return {"supply": b.supply, "contradictory": b.contradictory(),
            "contacts": [{**locate(e), "device": e.device, "kind": e.kind, "side": s} for e, s in zip(b.contacts, b.sides, strict=False)]}


def condition_json(tracer: Tracer, target: str | dict[str, Any]) -> dict[str, Any]:
    cond = tracer.condition(target)
    return {k: v for k, v in cond.items() if k != "branches"} | {
        "branches": [branch_json(b, tracer.locate) for b in cond["branches"]],
        "sources": {d: {k: v for k, v in tracer.sources.get(d, {}).items() if k != "contacts"} for d in cond["devices"]}}
