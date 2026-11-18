"""図面のコード表現を、決まった操作で変更する（DD-0034）。LLM が使うことを前提にした変更の入口。

変更は「操作」（op）の並びで表す。操作は JSON スキーマで定義し（``OPS``）、LLM のツール定義としてそのまま公開できる
（``tool_definitions``）。変更の流れ:

1. ``context``: 変更する図面の記号・端子・配線・回路コードを、LLM が読める形で渡す。
2. ``apply_ops``: 操作を図面（アノテーションと同じ形の pages）に当てる。1 つでも不正なら何も変えずに理由を返す（全部か無し）。
3. ``preview``: 変更前後の差分（記号・配線・回路コード）、検証（回路の診断・矛盾チェックの増減）、動作の変化（入力の
   組み合わせごとのリレー・負荷）、波及範囲を出す。人（または LLM 自身）がこれを見て適用を決める。
4. 適用・履歴・取り消しは circuit_service（DB に変更セットとして前後の図面を残す）。

この層は DB を使わない。ここでは LLM を呼ばない（将来の LLM が呼ぶ操作の受け口と、その検証だけを持つ）。
"""

from __future__ import annotations

import copy
import difflib
import hashlib
import json
import random
from typing import Any

from services import circuit_code, circuit_model, circuit_whatif, consistency
from services.circuit_model import build_circuit, simulate

# 2 端子の部品として足せる種類（端子 1 が上、2 が下）
COMPONENT_CLASSES = ["contact_a", "contact_b", "relay_coil", "lamp", "solenoid", "diode"]
# 1 端子の点として足せる種類
POINT_CLASSES = ["junction", "terminal", "power_bus", "other"]
DEFAULT_SIZE = {"w": 0.02, "h": 0.03}
MAX_OPS = 200
MAX_SCENARIOS = 24

_TERMINAL = {
    "type": "object",
    "description": "記号の端子。terminal は端子の名前（2 端子の部品は上が \"1\"、下が \"2\"）か端子の参照名（SYM-0001-T1）",
    "properties": {"symbol_ref": {"type": "string"}, "terminal": {"type": "string"}},
    "required": ["symbol_ref"],
}
_PAGE = {"type": "integer", "description": "図面（プロジェクト）の ID"}
_COMPONENT = {
    "type": "object",
    "properties": {
        "class_key": {"type": "string", "enum": COMPONENT_CLASSES + POINT_CLASSES,
                      "description": "contact_a: a 接点、contact_b: b 接点、relay_coil: リレーコイル、lamp: 表示灯 など"},
        "label": {"type": "string", "description": "器具番号（例: 33HE FV2304）。別の図面のコイルの接点は「33HE FV2304 (ER21302)」"},
        "ref": {"type": "string", "description": "新しい記号の参照名（省略時は自動）"},
        "cx": {"type": "number", "minimum": 0, "maximum": 1, "description": "図面上の中心 x（0〜1。省略時はつなぐ相手の近く）"},
        "cy": {"type": "number", "minimum": 0, "maximum": 1},
        "attributes": {"type": "object", "description": "属性（例: timer_mode, delay_s, coil_resistance_ohm）"},
    },
    "required": ["class_key"],
}

OPS: dict[str, dict[str, Any]] = {
    "add_component": {
        "description": "部品（接点・コイル・表示灯など）を図面に足す。配線は connect で別につなぐ（insert_in_series なら同時）。",
        "properties": {"project_id": _PAGE, "component": _COMPONENT},
        "required": ["project_id", "component"],
    },
    "remove_component": {
        "description": "記号を消す。その記号の配線も消える。",
        "properties": {"project_id": _PAGE, "symbol_ref": {"type": "string"}},
        "required": ["project_id", "symbol_ref"],
    },
    "set_label": {
        "description": "記号のラベル（器具番号）を変える。接点のラベルを変えると、どのリレーで開閉するかが変わる。",
        "properties": {"project_id": _PAGE, "symbol_ref": {"type": "string"}, "label": {"type": "string"}},
        "required": ["project_id", "symbol_ref", "label"],
    },
    "set_class": {
        "description": "記号の種類を変える（例: a 接点を b 接点に）。",
        "properties": {"project_id": _PAGE, "symbol_ref": {"type": "string"},
                       "class_key": {"type": "string", "enum": COMPONENT_CLASSES + POINT_CLASSES}},
        "required": ["project_id", "symbol_ref", "class_key"],
    },
    "set_attribute": {
        "description": "記号の属性（定数）を変える。value が null なら消す。例: タイマーの時間 delay_s、種類 timer_mode（on_delay / off_delay）、コイル抵抗 coil_resistance_ohm。",
        "properties": {"project_id": _PAGE, "symbol_ref": {"type": "string"}, "key": {"type": "string"},
                       "value": {"type": ["string", "number", "boolean", "null"]}},
        "required": ["project_id", "symbol_ref", "key", "value"],
    },
    "connect": {
        "description": "2 つの端子を配線でつなぐ。",
        "properties": {"project_id": _PAGE, "from": _TERMINAL, "to": _TERMINAL,
                       "wire_no": {"type": "string", "description": "電線番号（任意）"}},
        "required": ["project_id", "from", "to"],
    },
    "disconnect": {
        "description": "2 つの端子の間の配線を外す（同じ 2 端子の間の配線をすべて）。",
        "properties": {"project_id": _PAGE, "from": _TERMINAL, "to": _TERMINAL},
        "required": ["project_id", "from", "to"],
    },
    "insert_in_series": {
        "description": "2 端子の間の配線を切り、その間に部品を直列に入れる（例: 段にインターロックの b 接点を足す）。"
                       "from 側に部品の端子 1、to 側に端子 2 をつなぐ。外した配線の電線番号は from 側に残す。",
        "properties": {"project_id": _PAGE, "from": _TERMINAL, "to": _TERMINAL, "component": _COMPONENT,
                       "wire_no": {"type": "string", "description": "部品と to 側の間の新しい電線番号（任意）"}},
        "required": ["project_id", "from", "to", "component"],
    },
    "set_wire_no": {
        "description": "2 端子の間の配線の電線番号を変える。",
        "properties": {"project_id": _PAGE, "from": _TERMINAL, "to": _TERMINAL, "wire_no": {"type": ["string", "null"]}},
        "required": ["project_id", "from", "to", "wire_no"],
    },
}


def op_schema() -> dict[str, Any]:
    """操作 1 つの JSON スキーマ（op で種類を選ぶ）。"""
    return {"oneOf": [{"type": "object", "properties": {"op": {"const": name}, **spec["properties"]},
                       "required": ["op", *spec["required"]], "description": spec["description"]}
                      for name, spec in OPS.items()]}


def tool_definitions() -> list[dict[str, Any]]:
    """LLM のツール定義（Anthropic Messages API の tools の形: name・description・input_schema）。

    edit_circuit は変更をまとめて試す（適用しない）。適用は人の確認の後で API（/api/circuit/edit/apply）から行う。
    """
    return [
        {
            "name": "get_circuit_context",
            "description": "変更する図面の記号（ref・種類・ラベル・位置・端子）、配線、回路コード、器具の一覧を返す。変更の前に呼ぶ。",
            "input_schema": {"type": "object", "properties": {"project_ids": {"type": "array", "items": {"type": "integer"}}},
                             "required": ["project_ids"]},
        },
        {
            "name": "preview_circuit_edit",
            "description": "図面の変更（操作の並び）を試し、差分・検証の結果・動作の変化・波及範囲を返す。図面は変えない。"
                           "検証で errors が出たら操作を直して呼び直す。",
            "input_schema": {
                "type": "object",
                "properties": {
                    "project_ids": {"type": "array", "items": {"type": "integer"}},
                    "title": {"type": "string", "description": "変更の短い名前（例: 33HE の段にインターロックを足す）"},
                    "reason": {"type": "string", "description": "変更の理由・根拠"},
                    "ops": {"type": "array", "items": op_schema(), "maxItems": MAX_OPS},
                },
                "required": ["project_ids", "title", "ops"],
            },
        },
    ]


# ------------------------------------------------------------------------------------------- 文脈


def context(pages: list[dict[str, Any]], bus_texts: dict[int, list[dict[str, Any]]] | None = None) -> dict[str, Any]:
    """LLM に渡す図面の文脈。記号・端子・配線を短い形にし、回路コードと器具の一覧を添える。"""
    circuit = build_circuit(copy.deepcopy(pages), bus_texts)
    return {
        "pages": [{
            "project_id": p["id"], "sheet_no": p.get("sheet_no"), "name": p.get("name"),
            "symbols": [{"ref": s["ref"], "class_key": s["class_key"], "label": s.get("label"),
                         "cx": round(s["cx"], 4), "cy": round(s["cy"], 4),
                         "terminals": [t.get("name") or t["ref"] for t in s.get("terminals") or []],
                         **({"attributes": s["attributes"]} if s.get("attributes") else {})} for s in p["symbols"]],
            "wires": [{"from": [c["from_symbol_ref"], _term_name(p, c["from_symbol_ref"], c.get("from_terminal_ref"))],
                       "to": [c["to_symbol_ref"], _term_name(p, c["to_symbol_ref"], c.get("to_terminal_ref"))],
                       **({"wire_no": c["wire_no"]} if c.get("wire_no") else {})} for c in p["connections"]],
        } for p in pages],
        "relays": sorted(circuit.coils), "inputs": circuit.inputs,
        "timers": [{"device": d, **cfg} for d, cfg in sorted(circuit.timers.items())],
        "code": circuit_code.to_python(circuit, pages=pages),
        "base_hash": pages_hash(pages),
    }


def _term_name(page: dict[str, Any], symbol_ref: str, terminal_ref: str | None) -> str | None:
    for s in page["symbols"]:
        if s["ref"] == symbol_ref:
            for t in s.get("terminals") or []:
                if t["ref"] == terminal_ref:
                    return t.get("name") or t["ref"]
    return terminal_ref


_HASH_SYMBOL = ("ref", "class_key", "label", "cx", "cy", "w", "h", "attributes")
_HASH_WIRE = ("from_symbol_ref", "from_terminal_ref", "to_symbol_ref", "to_terminal_ref", "wire_no", "kind", "external_ref")


def pages_hash(pages: list[dict[str, Any]]) -> str:
    """図面の内容（記号・端子・配線）のハッシュ。試した時と適用する時で図面が変わっていないかを確かめる。"""
    body = [{
        "id": p["id"],
        "symbols": sorted(([{k: s.get(k) for k in _HASH_SYMBOL}
                            | {"terminals": [[t["ref"], t.get("name")] for t in s.get("terminals") or []]}
                            for s in p["symbols"]]), key=lambda x: x["ref"]),
        "wires": sorted(json.dumps({k: c.get(k) for k in _HASH_WIRE}, sort_keys=True, ensure_ascii=False)
                        for c in p["connections"]),
    } for p in sorted(pages, key=lambda p: p["id"])]
    return hashlib.sha256(json.dumps(body, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()[:16]


# ------------------------------------------------------------------------------------------- 適用


class EditError(ValueError):
    def __init__(self, errors: list[dict[str, Any]]) -> None:
        super().__init__("; ".join(f"操作 {e['index'] + 1}（{e['op']}）: {e['message']}" for e in errors))
        self.errors = errors


class _Editor:
    def __init__(self, pages: list[dict[str, Any]]) -> None:
        self.pages = {p["id"]: p for p in pages}
        self.applied: list[str] = []

    def page(self, op: dict[str, Any]) -> dict[str, Any]:
        page = self.pages.get(op.get("project_id"))
        if page is None:
            raise ValueError(f"図面 {op.get('project_id')} が変更の対象にありません")
        return page

    @staticmethod
    def symbol(page: dict[str, Any], ref: str) -> dict[str, Any]:
        for s in page["symbols"]:
            if s["ref"] == ref:
                return s
        raise ValueError(f"記号 {ref} が図面 {page.get('sheet_no') or page['id']} にありません")

    def terminal(self, page: dict[str, Any], end: dict[str, Any]) -> tuple[str, str]:
        s = self.symbol(page, str(end.get("symbol_ref")))
        terms = s.get("terminals") or []
        name = end.get("terminal")
        if name is None:
            if len(terms) == 1:
                return s["ref"], terms[0]["ref"]
            raise ValueError(f"記号 {s['ref']} の端子（terminal）を指定してください（{', '.join(t.get('name') or t['ref'] for t in terms)}）")
        for t in terms:
            if str(name) in (t["ref"], str(t.get("name"))):
                return s["ref"], t["ref"]
        raise ValueError(f"記号 {s['ref']} に端子 {name} がありません")

    @staticmethod
    def _new_ref(page: dict[str, Any], wanted: str | None, prefix: str) -> str:
        used = {s["ref"] for s in page["symbols"]}
        if wanted:
            if wanted in used:
                raise ValueError(f"参照名 {wanted} はもう使われています")
            return wanted
        k = 1
        while f"{prefix}-{k:04d}" in used:
            k += 1
        return f"{prefix}-{k:04d}"

    def add(self, page: dict[str, Any], component: dict[str, Any], near: list[tuple[str, str]] | None = None) -> dict[str, Any]:
        cls = component.get("class_key")
        if cls not in COMPONENT_CLASSES + POINT_CLASSES:
            raise ValueError(f"種類 {cls} は足せません（{', '.join(COMPONENT_CLASSES + POINT_CLASSES)}）")
        ref = self._new_ref(page, component.get("ref"), "EDIT")
        cx, cy = component.get("cx"), component.get("cy")
        if cx is None or cy is None:
            points = [(t["tx"], t["ty"]) for sref, tref in near or [] for t in self.symbol(page, sref)["terminals"] if t["ref"] == tref]
            cx = cx if cx is not None else (sum(p[0] for p in points) / len(points) if points else 0.5)
            cy = cy if cy is not None else (sum(p[1] for p in points) / len(points) if points else 0.5)
        cx, cy = min(max(float(cx), 0.0), 1.0), min(max(float(cy), 0.0), 1.0)
        w, h = DEFAULT_SIZE["w"], DEFAULT_SIZE["h"]
        if cls in COMPONENT_CLASSES:
            terminals = [{"ref": f"{ref}-T1", "name": "1", "tx": cx, "ty": max(0.0, cy - h / 2), "origin": "manual"},
                         {"ref": f"{ref}-T2", "name": "2", "tx": cx, "ty": min(1.0, cy + h / 2), "origin": "manual"}]
        else:
            terminals = [{"ref": f"{ref}-T1", "name": "1", "tx": cx, "ty": cy, "origin": "manual"}]
        symbol = {"ref": ref, "class_key": cls, "class_label": cls, "label": component.get("label") or None,
                  "cx": cx, "cy": cy, "w": w, "h": h, "note": None, "origin": "manual", "confidence": None,
                  "attributes": dict(component.get("attributes") or {}), "terminals": terminals}
        page["symbols"].append(symbol)
        return symbol

    @staticmethod
    def wire(page: dict[str, Any], a: tuple[str, str], b: tuple[str, str], wire_no: str | None) -> None:
        if a == b:
            raise ValueError("同じ端子どうしはつなげません")
        page["connections"].append({"ref": None, "from_symbol_ref": a[0], "from_terminal_ref": a[1], "to_symbol_ref": b[0],
                                    "to_terminal_ref": b[1], "wire_no": wire_no or None, "net_id": None, "kind": "wire",
                                    "external_ref": None, "note": None, "origin": "manual", "attributes": {}})

    @staticmethod
    def wires_between(page: dict[str, Any], a: tuple[str, str], b: tuple[str, str]) -> list[dict[str, Any]]:
        ends = {a, b}
        return [c for c in page["connections"]
                if {(c["from_symbol_ref"], c.get("from_terminal_ref")), (c["to_symbol_ref"], c.get("to_terminal_ref"))} == ends]

    def apply(self, op: dict[str, Any]) -> str:
        kind = op.get("op")
        if kind not in OPS:
            raise ValueError(f"操作 {kind} はありません（{', '.join(OPS)}）")
        missing = [k for k in OPS[kind]["required"] if k not in op]
        if missing:
            raise ValueError(f"{', '.join(missing)} がありません")
        page = self.page(op)
        if kind == "add_component":
            s = self.add(page, op["component"])
            return f"{s['ref']}（{s['class_key']} {s.get('label') or ''}）を足した"
        if kind == "remove_component":
            s = self.symbol(page, op["symbol_ref"])
            page["symbols"].remove(s)
            before = len(page["connections"])
            page["connections"] = [c for c in page["connections"] if s["ref"] not in (c["from_symbol_ref"], c["to_symbol_ref"])]
            return f"{s['ref']}（{s.get('label') or s['class_key']}）と配線 {before - len(page['connections'])} 本を消した"
        if kind == "set_label":
            s = self.symbol(page, op["symbol_ref"])
            old, s["label"] = s.get("label"), op["label"] or None
            return f"{s['ref']} のラベル: {old} → {s['label']}"
        if kind == "set_class":
            s = self.symbol(page, op["symbol_ref"])
            new = op["class_key"]
            if new not in COMPONENT_CLASSES + POINT_CLASSES:
                raise ValueError(f"種類 {new} にはできません")
            if (new in COMPONENT_CLASSES) != (len(s.get("terminals") or []) == 2):
                raise ValueError(f"{s['ref']} の端子は {len(s.get('terminals') or [])} 個で、{new} にできません")
            old = s["class_key"]
            s["class_key"] = s["class_label"] = new
            return f"{s['ref']} の種類: {old} → {new}"
        if kind == "set_attribute":
            s = self.symbol(page, op["symbol_ref"])
            attrs = s.setdefault("attributes", {})
            old = attrs.get(op["key"])
            if op["value"] is None:
                attrs.pop(op["key"], None)
            else:
                attrs[op["key"]] = op["value"]
            return f"{s['ref']} の {op['key']}: {old} → {op['value']}"
        a = self.terminal(page, op["from"])
        b = self.terminal(page, op["to"])
        found = self.wires_between(page, a, b)
        if kind == "connect":
            if found:
                raise ValueError(f"{a[0]} と {b[0]} はもうつながっています")
            self.wire(page, a, b, op.get("wire_no"))
            return f"{a[1]} と {b[1]} をつないだ"
        if not found:
            raise ValueError(f"{a[1]} と {b[1]} の間に配線がありません")
        if kind == "disconnect":
            page["connections"] = [c for c in page["connections"] if c not in found]
            return f"{a[1]} と {b[1]} の配線 {len(found)} 本を外した"
        if kind == "set_wire_no":
            for c in found:
                c["wire_no"] = op["wire_no"] or None
            return f"{a[1]} と {b[1]} の電線番号を {op['wire_no']} にした"
        # insert_in_series
        if op["component"].get("class_key") not in COMPONENT_CLASSES:
            raise ValueError("直列に入れられるのは 2 端子の部品だけです")
        number = found[0].get("wire_no")
        page["connections"] = [c for c in page["connections"] if c not in found]
        s = self.add(page, op["component"], near=[a, b])
        self.wire(page, a, (s["ref"], s["terminals"][0]["ref"]), number)
        self.wire(page, (s["ref"], s["terminals"][1]["ref"]), b, op.get("wire_no"))
        return f"{a[1]} と {b[1]} の間に {s['ref']}（{s['class_key']} {s.get('label') or ''}）を入れた"


def apply_ops(pages: list[dict[str, Any]], ops: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[str]]:
    """操作を順に当てた図面のコピーと、操作ごとの説明。1 つでも不正なら EditError（何も変えない）。"""
    if len(ops) > MAX_OPS:
        raise EditError([{"index": MAX_OPS, "op": "-", "message": f"操作は {MAX_OPS} 個まで"}])
    editor = _Editor(copy.deepcopy(pages))
    notes, errors = [], []
    for i, op in enumerate(ops):
        try:
            notes.append(editor.apply(op))
        except ValueError as exc:
            errors.append({"index": i, "op": op.get("op"), "message": str(exc)})
    if errors:
        raise EditError(errors)
    return list(editor.pages.values()), notes


def fix_to_op(fix: dict[str, Any]) -> dict[str, Any]:
    """矛盾チェック（DD-0031）の直し方を、変更の操作にする（同じ経路で試し・適用・取り消しができる）。"""
    if fix["type"] == "set_class":
        return {"op": "set_class", "project_id": fix["project_id"], "symbol_ref": fix["symbol_ref"], "class_key": fix["value"]}
    if fix["type"] == "set_label":
        return {"op": "set_label", "project_id": fix["project_id"], "symbol_ref": fix["symbol_ref"], "label": fix["value"]}
    raise ValueError(f"直し方 {fix['type']} は操作にできません")


# ------------------------------------------------------------------------------------------- 試す


def _symbol_diff(before: list[dict[str, Any]], after: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for pb, pa in zip(sorted(before, key=lambda p: p["id"]), sorted(after, key=lambda p: p["id"]), strict=True):
        sb, sa = {s["ref"]: s for s in pb["symbols"]}, {s["ref"]: s for s in pa["symbols"]}
        for ref in sorted(set(sb) | set(sa)):
            if ref not in sb:
                out.append({"project_id": pa["id"], "symbol_ref": ref, "change": "added",
                            "after": {k: sa[ref].get(k) for k in ("class_key", "label", "attributes")}})
            elif ref not in sa:
                out.append({"project_id": pa["id"], "symbol_ref": ref, "change": "removed",
                            "before": {k: sb[ref].get(k) for k in ("class_key", "label", "attributes")}})
            else:
                fields = {k: {"before": sb[ref].get(k), "after": sa[ref].get(k)}
                          for k in ("class_key", "label", "attributes") if sb[ref].get(k) != sa[ref].get(k)}
                if fields:
                    out.append({"project_id": pa["id"], "symbol_ref": ref, "change": "changed", "fields": fields})
        key = lambda c: (c["from_symbol_ref"], c.get("from_terminal_ref"), c["to_symbol_ref"], c.get("to_terminal_ref"), c.get("wire_no"))
        wb, wa = {key(c) for c in pb["connections"]}, {key(c) for c in pa["connections"]}
        out += [{"project_id": pa["id"], "wire": {"from": [k[0], k[1]], "to": [k[2], k[3]], "wire_no": k[4]}, "change": "added"}
                for k in sorted(wa - wb, key=str)]
        out += [{"project_id": pa["id"], "wire": {"from": [k[0], k[1]], "to": [k[2], k[3]], "wire_no": k[4]}, "change": "removed"}
                for k in sorted(wb - wa, key=str)]
    return out


def _scenarios(inputs: list[str], focus: list[str], seed: int = 0) -> list[dict[str, bool]]:
    """入力の組み合わせ: すべて OFF、1 つずつ ON、すべて ON、乱数。変更に関わる入力（focus）は、乱数の組み合わせで
    その入力だけを反転したものも足す（その入力で動作が変わるかを見るため）。"""
    out = [{}] + [{i: True} for i in inputs] + [dict.fromkeys(inputs, True)]
    rng = random.Random(seed)
    randoms = [{i: rng.random() < 0.5 for i in inputs} for _ in range(MAX_SCENARIOS)] if inputs else []
    out += randoms[: max(0, MAX_SCENARIOS - len(out))]
    base = out[: len(inputs) + 2] + randoms[:8]
    for f in focus:
        out += [{**r, f: not r.get(f, False)} for r in base if r.get(f) is not None or f not in r]
    unique: dict[str, dict[str, bool]] = {}
    for r in out:
        unique.setdefault(json.dumps(sorted((k, v) for k, v in r.items() if v)), r)
    return list(unique.values())


def netlist_text(circuit: circuit_model.Circuit, pages: list[dict[str, Any]]) -> list[str]:
    """変更の前後で比べやすい回路の行（素子ごと）。ノードは母線の名前か、つながる端子のうち最小のもので名付けるので、
    変更で素子の番号やノードの通し番号が変わっても、変わっていない素子の行は同じになる。"""
    sheet = {p["id"]: p.get("sheet_no") or str(p["id"]) for p in pages}
    names = {(p["id"], s["ref"], t["ref"]): f"{s['ref']}.{t.get('name') or t['ref']}"
             for p in pages for s in p["symbols"] for t in s.get("terminals") or []}
    members: dict[str, list[str]] = {}
    for key, node in circuit.node_of.items():
        if key in names:
            members.setdefault(node, []).append(f"{sheet.get(key[0], key[0])}:{names[key]}")

    def label(node: str) -> str:
        if node == circuit_model.GROUND or node.startswith(circuit_model.SUPPLY):
            return node
        return f"<{min(members.get(node, [node]))}>"

    kinds = {"a": "a接点", "b": "b接点", "load": "負荷"}
    lines = []
    for e in sorted(circuit.elements, key=lambda e: (e.project_id, e.symbol_ref)):
        kind = "コイル" if e.kind == "load" and e.device in circuit.coils else kinds[e.kind]
        timer = circuit.timers.get(e.device) if kind == "コイル" else None
        extra = f" タイマー {timer['mode']} {timer['delay_s']:g}s" if timer else ""
        lines.append(f"{sheet.get(e.project_id, e.project_id)}:{e.symbol_ref} {kind} {e.device or e.label or ''}"
                     f"{extra} {label(e.a)} — {label(e.b)}")
    return lines


def _state(circuit: circuit_model.Circuit, inputs: dict[str, bool]) -> dict[str, Any]:
    r = simulate(circuit, inputs, {})
    return {"coils": {d: s["energized"] for d, s in r["coils"].items()},
            "loads": {(x["project_id"], x["symbol_ref"]): (x["label"], x["energized"]) for x in r["loads"]},
            "warnings": r["warnings"]}


def _issue_key(issue: dict[str, Any]) -> tuple:
    return (issue["check"], issue["title"], issue["message"])


def preview(pages: list[dict[str, Any]], ops: list[dict[str, Any]], *,
            structures: dict[int, dict[str, Any] | None] | None = None,
            bus_texts: dict[int, list[dict[str, Any]]] | None = None) -> dict[str, Any]:
    """変更を試す。図面は変えない。不正な操作は errors に返す（そのときほかの結果は無い）。"""
    base_hash = pages_hash(pages)
    try:
        after_pages, notes = apply_ops(pages, ops)
    except EditError as exc:
        return {"ok": False, "base_hash": base_hash, "errors": exc.errors}
    before = build_circuit(copy.deepcopy(pages), bus_texts)
    after = build_circuit(copy.deepcopy(after_pages), bus_texts)
    netlist_diff = "\n".join(difflib.unified_diff(netlist_text(before, pages), netlist_text(after, after_pages),
                                                  "変更前", "変更後", lineterm="", n=0))

    # 動作の変化: 両方の回路の入力を合わせた組み合わせで解き、リレー・負荷の状態が違うものを出す。
    touched = _touched_devices(before, after, pages, after_pages)
    inputs = sorted(set(before.inputs) | set(after.inputs))
    scenarios = _scenarios(inputs, [i for i in inputs if i in touched])
    behaviour = []
    for scenario in scenarios:
        a, b = _state(before, scenario), _state(after, scenario)
        coils = [{"device": d, "before": a["coils"].get(d), "after": b["coils"].get(d)}
                 for d in sorted(set(a["coils"]) | set(b["coils"])) if a["coils"].get(d) != b["coils"].get(d)]
        loads = [{"project_id": k[0], "symbol_ref": k[1], "label": (b["loads"].get(k) or a["loads"].get(k))[0],
                  "before": a["loads"][k][1] if k in a["loads"] else None,
                  "after": b["loads"][k][1] if k in b["loads"] else None}
                 for k in sorted(set(a["loads"]) | set(b["loads"]))
                 if (a["loads"].get(k) or (None, None))[1] != (b["loads"].get(k) or (None, None))[1]]
        new_warnings = [w for w in b["warnings"] if w not in a["warnings"]]
        if coils or loads or new_warnings:
            label = "、".join(f"{k}={'ON' if v else 'OFF'}" for k, v in scenario.items() if v) or "すべての入力 OFF"
            behaviour.append({"inputs": label, "coils": coils, "loads": loads, "warnings": new_warnings})

    # 波及範囲: 変えた記号の器具（接点・コイル）と、変更で増減した器具から、変更後の回路でたどる。
    impact = []
    for dev in sorted(touched):
        if dev in after.coils or dev in after.inputs:
            r = circuit_whatif.impact(after, dev)
            impact.append({"device": dev, "devices": [d["device"] for d in r["devices"]],
                           "loads": [x["label"] or x["symbol_ref"] for x in r["loads"] if x["device"] is None]})

    # 検証: 回路の診断と矛盾チェック（DD-0031）の、変更で増えたもの・消えたもの。
    diag_b = {(d["kind"], d.get("message")) for d in before.diagnostics}
    diag_new = [d for d in after.diagnostics if (d["kind"], d.get("message")) not in diag_b]
    check_b = consistency.check(copy.deepcopy(pages), structures or {}, bus_texts=bus_texts, solvers=False)
    check_a = consistency.check(copy.deepcopy(after_pages), structures or {}, bus_texts=bus_texts, solvers=False)
    kb, ka = {_issue_key(i) for i in check_b["issues"]}, {_issue_key(i) for i in check_a["issues"]}
    introduced = [i for i in check_a["issues"] if _issue_key(i) not in kb]
    resolved = [i for i in check_b["issues"] if _issue_key(i) not in ka]
    brief = lambda i: {k: i[k] for k in ("check", "severity", "title", "message", "locations")}
    return {
        "ok": True, "base_hash": base_hash, "after_hash": pages_hash(after_pages), "notes": notes,
        "diff": _symbol_diff(pages, after_pages), "netlist_diff": netlist_diff,
        "circuit": {"relays_added": sorted(set(after.coils) - set(before.coils)),
                    "relays_removed": sorted(set(before.coils) - set(after.coils)),
                    "inputs_added": sorted(set(after.inputs) - set(before.inputs)),
                    "inputs_removed": sorted(set(before.inputs) - set(after.inputs)),
                    "timers_changed": sorted(d for d in set(before.timers) | set(after.timers)
                                             if before.timers.get(d) != after.timers.get(d))},
        "behaviour": behaviour, "scenarios": len(scenarios), "impact": impact,
        "validation": {"diagnostics_added": diag_new, "issues_added": [brief(i) for i in introduced],
                       "issues_resolved": [brief(i) for i in resolved],
                       "errors": sum(i["severity"] == "error" for i in introduced)},
        "after_pages": after_pages,
    }


def _touched_devices(before: circuit_model.Circuit, after: circuit_model.Circuit,
                     pages: list[dict[str, Any]], after_pages: list[dict[str, Any]]) -> set[str]:
    changed = {(d["project_id"], d["symbol_ref"]) for d in _symbol_diff(pages, after_pages) if "symbol_ref" in d}
    wired = {(d["project_id"], end[0]) for d in _symbol_diff(pages, after_pages) if "wire" in d
             for end in (d["wire"]["from"], d["wire"]["to"])}
    out: set[str] = set()
    for circuit in (before, after):
        for e in circuit.elements:
            if e.device and (e.project_id, e.symbol_ref) in changed | wired:
                out.add(e.device)
    # 配線だけの変更（直列に入れた部品・外した線）は、その段のコイルも
    rung = circuit_whatif.rungs(after)
    rungs_touched = {rung[e.name] for e in after.elements if (e.project_id, e.symbol_ref) in changed | wired}
    out |= {e.device for e in after.elements if e.kind == "load" and e.device and rung[e.name] in rungs_touched}
    return out | (set(after.coils) ^ set(before.coils)) | (set(after.inputs) ^ set(before.inputs))
