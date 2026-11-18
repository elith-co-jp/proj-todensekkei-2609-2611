"""複数の解析方法の結果を突き合わせ、矛盾を見つけて精査し、直し方を出す（DD-0031）。DB を使わない。

突き合わせる組み合わせ（check）:

| check | 方法 A | 方法 B |
|-------|--------|--------|
| relay_table | 図面の記号（アノテーション） | 図面下部のリレー表（構造解析の文字の読み取り） |
| coil_sheet | 接点の枠の中のシート番号（コイルのあるシート） | 実際にコイルがある図面 |
| wire_numbers | 配線のつながり（ノード） | 電線番号 |
| sheet_links | 行先札の組（DD-0030） | 両側の電線番号 |
| solvers | 内蔵の回路計算 | ngspice・Lcapy |
| impact | 静的な波及範囲 | もしもの結果（入力を 1 つずつ入れる） |
| stability | シミュレーション（自己保持・発振） | 接点の種類（自分のコイルの段にある b 接点） |

矛盾（issue）ごとに「精査」の結果（review: 調べたことと結論）と、直し方（proposal）を付ける。根拠がそろう直し方には
fix（記号の種類・ラベルの変更）を付け、画面から適用できる。
"""

from __future__ import annotations

import hashlib
import re
from collections import defaultdict
from typing import Any

from analysis.relay_links import match_rows
from analysis.text_extraction import SHEET_REF, normalize, same_relay_device
from services import circuit_model, circuit_whatif, sheet_links
from services.circuit_model import Circuit, canonical_device, simulate

SHEET = re.compile(SHEET_REF)
CONTACT_LABEL = {"contact_a": "a 接点", "contact_b": "b 接点"}
MAX_INPUT_TRIALS = 20
LIVE = circuit_model.SUPPLY_V / 2


def _issue_id(*parts: Any) -> str:
    return hashlib.sha1("|".join(map(str, parts)).encode()).hexdigest()[:10]


def _loc(pid: int, ref: str, names: dict[int, str]) -> dict[str, Any]:
    return {"project_id": pid, "symbol_ref": ref, "page": names.get(pid)}


def _px_symbols(page: dict[str, Any]) -> list[dict[str, Any]]:
    """アノテーションの記号を、リレー表の照合（analysis.relay_links）の形（画素の枠）にする。"""
    w, h = page.get("image_width") or 0, page.get("image_height") or 0
    return [{"id": s["ref"], "class_name": s["class_key"], "label": s.get("label") or "",
             "bbox": {"x0": (s["cx"] - s["w"] / 2) * w, "y0": (s["cy"] - s["h"] / 2) * h,
                      "x1": (s["cx"] + s["w"] / 2) * w, "y1": (s["cy"] + s["h"] / 2) * h}}
            for s in page["symbols"]]


def _first_token(text: str) -> str:
    words = re.sub(rf"\(?{SHEET_REF}\)?", " ", normalize(text)).split()
    while len(words) > 1 and words[0].isdigit():
        words = words[1:]
    return canonical_device(words[0]) if words else ""


def _near(a: str, b: str) -> bool:
    """器具番号の 1 文字違い（読み違い）か。"""
    if not a or not b or a == b or abs(len(a) - len(b)) > 1:
        return False
    if len(a) == len(b):
        return sum(x != y for x, y in zip(a, b, strict=True)) == 1
    short, long_ = sorted((a, b), key=len)
    return any(long_[:i] + long_[i + 1:] == short for i in range(len(long_)))


def _symbol_trust(symbol: dict[str, Any]) -> str:
    if symbol.get("origin") == "inference":
        conf = symbol.get("confidence")
        return f"AI の検出（確信度 {conf:.2f}）" if isinstance(conf, (int, float)) else "AI の検出"
    return "人が登録した記号"


class Checker:
    def __init__(self, pages: list[dict[str, Any]], structures: dict[int, dict[str, Any] | None],
                 circuit: Circuit, *, solvers: bool = True) -> None:
        self.pages = pages
        self.structures = structures
        self.circuit = circuit
        self.solvers = solvers
        self.names = {p["id"]: p.get("sheet_no") or p["name"] for p in pages}
        self.symbols = {(p["id"], s["ref"]): s for p in pages for s in p["symbols"]}
        self.sheet_page = {sheet_links.base_sheet(sh): p for p in pages if (sh := sheet_links.page_sheet(p))}
        self.issues: list[dict[str, Any]] = []
        self.checks: list[dict[str, Any]] = []

    def add(self, check: str, severity: str, title: str, message: str, locations: list[dict[str, Any]],
            review: list[str], proposal: str, fix: dict[str, Any] | None = None, key: Any = None) -> None:
        self.issues.append({
            "id": _issue_id(check, key if key is not None else (title, message)), "check": check, "severity": severity,
            "title": title, "message": message, "locations": locations, "review": review, "proposal": proposal,
            "fix": fix,
        })

    def record(self, check: str, a: str, b: str, compared: int, skipped: str | None = None) -> None:
        found = sum(1 for i in self.issues if i["check"] == check)
        status = "skipped" if skipped else "issues" if found else "ok"
        self.checks.append({"check": check, "method_a": a, "method_b": b, "compared": compared, "issues": found,
                            "status": status, "skipped_reason": skipped})

    # ----------------------------------------------------------------------------------------- リレー表
    def relay_table(self) -> None:
        compared = 0
        have_table = False
        matched_contacts: set[tuple[int, str]] = set()
        rows_by_device: dict[str, int] = defaultdict(int)
        for page in self.pages:
            structure = self.structures.get(page["id"]) or {}
            rows = (structure.get("relay_table") or {}).get("rows") or []
            if not rows:
                continue
            have_table = True
            pid = page["id"]
            coils = [s for s in page["symbols"] if s["class_key"] == "relay_coil"]
            for device in dict.fromkeys(r["device"] for r in rows if r.get("device")):
                compared += 1
                if any(same_relay_device(s.get("label") or "", device) for s in coils):
                    continue
                near = [s for s in coils if _near(_first_token(s.get("label") or ""), _first_token(device))]
                review = [f"リレー表に {device} がある（表はコイルのある図面に書かれる）",
                          f"この図面のコイル {len(coils)} 個のラベルに {device} が無い"]
                if len(near) == 1:
                    s = near[0]
                    review.append(f"1 文字違いのコイル {s.get('label')}（{s['ref']}）がある → ラベルの読み違いの可能性が高い")
                    self.add("relay_table", "error", "リレー表の器具のコイルが無い",
                             f"リレー表の {device} のコイルが図面に見つかりません", [_loc(pid, s["ref"], self.names)],
                             review, f"コイル {s['ref']} のラベルを「{device}」に直す",
                             {"type": "set_label", "project_id": pid, "symbol_ref": s["ref"], "value": device},
                             key=(pid, device))
                else:
                    review.append("1 文字違いのコイルも無い → コイルの検出漏れか、表の読み違い" if not near
                                  else f"1 文字違いのコイルが {len(near)} 個あり 1 つに決まらない")
                    self.add("relay_table", "warning", "リレー表の器具のコイルが無い",
                             f"リレー表の {device} のコイルが図面に見つかりません",
                             [_loc(pid, s["ref"], self.names) for s in near], review,
                             "図面でコイルを探して登録するか、リレー表の読みを確かめる", key=(pid, device))
            # 行列の位置の接点（「本」はこの図面、ほかは選んだ図面のうちそのシート）。
            for index, row in enumerate(rows):
                if not row.get("position") or not row.get("device"):
                    continue
                sheet = row.get("sheet") or ""
                target = page if sheet == "本" else self.sheet_page.get(
                    sheet_links.base_sheet(normalize(sheet).replace(" ", "")))
                if target is None:
                    continue
                grid = (self.structures.get(target["id"]) or {}).get("drawing_grid")
                if not grid:
                    continue
                rows_by_device[row["device"]] += 1
                compared += 1
                self._relay_row(pid, row, index, target, grid, matched_contacts)
        # 表に無い接点: 表がある器具の接点で、どの行にも結び付かなかったもの（選んだ図面の中だけ）。
        for (pid, ref), s in self.symbols.items():
            if s["class_key"] not in CONTACT_LABEL or (pid, ref) in matched_contacts:
                continue
            device = next((d for d in rows_by_device if same_relay_device(s.get("label") or "", d)), None)
            if device is None:
                continue
            self.add("relay_table", "warning", "リレー表に無い接点",
                     f"{s.get('label')}（{CONTACT_LABEL[s['class_key']]}）がリレー表のどの行の位置にも当たりません",
                     [_loc(pid, ref, self.names)],
                     [f"リレー表の {device} の行は {rows_by_device[device]} 行で、行列の位置にこの接点が無い",
                      "接点のラベルの読み違い（別の器具）か、表の行の読み落とし"],
                     "接点のラベルと、リレー表の行列の位置を確かめる", key=(pid, ref))
        self.record("relay_table", "図面の記号", "リレー表", compared,
                    None if have_table else "構造解析（リレー表の読み取り）の結果がある図面がありません")

    def _relay_row(self, pid: int, row: dict[str, str], index: int, target: dict[str, Any], grid: dict[str, Any],
                   matched_contacts: set[tuple[int, str]]) -> None:
        tid = target["id"]
        where = f"{self.names.get(tid)} の {row['position']}"
        found = match_rows([row], [0], _px_symbols(target), grid).get(0) or {}
        ref = found.get("symbol_id")
        if ref is None:
            self.add("relay_table", "warning", "リレー表の位置に接点が無い",
                     f"リレー表の {row['device']}（{row.get('contact')} 接点）の位置 {where} に接点がありません",
                     [{"project_id": tid, "symbol_ref": None, "page": self.names.get(tid), "position": row["position"]}],
                     ["表の行列を図面の座標に直し、その窓の中の接点を探した", "窓の中に接点の記号が無い → 接点の検出漏れの可能性"],
                     f"{where} 付近で接点を探して登録する", key=(pid, index, "missing"))
            return
        symbol = self.symbols[(tid, ref)]
        matched_contacts.add((tid, ref))
        table_class = f"contact_{row.get('contact')}" if row.get("contact") in ("a", "b") else None
        if not found.get("device_match"):
            review = [f"{where} の窓で最も近い接点は {symbol.get('label') or 'ラベルなし'}（{ref}）",
                      f"器具番号が表の {row['device']} と違う"]
            close = _near(_first_token(symbol.get("label") or ""), _first_token(row["device"])) or not symbol.get("label")
            fix = None
            if close:
                review.append("ラベルが空か 1 文字違い → 読み違いの可能性が高い")
                fix = {"type": "set_label", "project_id": tid, "symbol_ref": ref, "value": row["device"]}
            self.add("relay_table", "error" if close else "warning", "接点の器具番号が表と違う",
                     f"{where} の接点 {symbol.get('label') or ref} は、表では {row['device']}",
                     [_loc(tid, ref, self.names)], review,
                     f"接点 {ref} のラベルを「{row['device']}」に直す" if fix else "接点のラベルと表の位置を確かめる",
                     fix, key=(pid, index, "device"))
            return
        if table_class and symbol["class_key"] != table_class:
            review = [f"{where} の接点 {ref} は器具番号が表と一致（{row['device']}）",
                      f"図面では {CONTACT_LABEL.get(symbol['class_key'])}、表では {CONTACT_LABEL[table_class]}",
                      f"記号: {_symbol_trust(symbol)}"]
            unique = found.get("unique")
            review.append("窓の中で器具番号が一致する接点はこれ 1 つ → 表の種類が正しい可能性が高い" if unique
                          else "窓の中に同じ器具番号の接点が複数ある → どれが表の行か確かめる")
            self.add("relay_table", "error", "接点の種類（a/b）が表と違う",
                     f"{where} の {row['device']} の接点は、図面では {CONTACT_LABEL.get(symbol['class_key'])}、"
                     f"表では {CONTACT_LABEL[table_class]}", [_loc(tid, ref, self.names)], review,
                     f"接点 {ref} を {CONTACT_LABEL[table_class]}にする",
                     {"type": "set_class", "project_id": tid, "symbol_ref": ref, "value": table_class} if unique else None,
                     key=(pid, index, "class"))

    # ------------------------------------------------------------------------------- 接点のコイルのシート
    def coil_sheet(self) -> None:
        compared = 0
        coil_pages: dict[str, set[int]] = defaultdict(set)
        for dev, coils in self.circuit.coils.items():
            for e in coils:
                coil_pages[canonical_device(dev.split()[0])].add(e.project_id)
        for (pid, ref), s in self.symbols.items():
            if s["class_key"] not in CONTACT_LABEL:
                continue
            m = SHEET.search(normalize(s.get("label") or "").replace(" ", ""))
            if not m:
                continue
            target = self.sheet_page.get(sheet_links.base_sheet(m.group(0)))
            if target is None:
                continue
            compared += 1
            number = _first_token(s.get("label") or "")
            where = coil_pages.get(number, set())
            if target["id"] in where:
                continue
            coils_there = [c for c in target["symbols"] if c["class_key"] == "relay_coil"]
            near = [c for c in coils_there if _near(_first_token(c.get("label") or ""), number)]
            review = [f"接点のラベルではコイルは {m.group(0)}（{self.names.get(target['id'])}）にある",
                      f"その図面に {number} のコイルが無い"
                      + (f"（{'、'.join(self.names.get(p, str(p)) for p in sorted(where))} にはある）" if where else "")]
            if len(near) == 1 and not where:
                c = near[0]
                review.append(f"その図面に 1 文字違いのコイル {c.get('label')}（{c['ref']}）がある → 接点のラベルの読み違いの可能性")
                label = re.sub(re.escape(normalize(s.get("label") or "").split()[0]),
                               normalize(c.get("label") or "").split()[0], normalize(s.get("label") or ""), count=1)
                fix = {"type": "set_label", "project_id": pid, "symbol_ref": ref, "value": label}
                proposal = f"接点 {ref} のラベルを「{label}」に直す"
            else:
                fix, proposal = None, "接点のラベル（器具番号・シート番号）とコイルの図面を確かめる"
            self.add("coil_sheet", "error" if fix or where else "warning", "接点の指すシートにコイルが無い",
                     f"接点 {s.get('label')} はコイルが {m.group(0)} にあると書かれていますが、見つかりません",
                     [_loc(pid, ref, self.names)], review, proposal, fix, key=(pid, ref))
        self.record("coil_sheet", "接点のラベルのシート番号", "コイルのある図面", compared,
                    None if compared else "選んだ図面を指すシート番号つきの接点のラベルがありません")

    # --------------------------------------------------------------------------------------- 電線番号
    def wire_numbers(self) -> None:
        compared = 0
        node_of = self.circuit.node_of
        for page in self.pages:
            pid = page["id"]
            by_number: dict[str, set[str]] = defaultdict(set)
            by_node: dict[str, set[str]] = defaultdict(set)
            where: dict[tuple[str, str], list[str]] = defaultdict(list)
            for w in self.circuit.wires:
                if w["project_id"] != pid:
                    continue
                c = self._connection(page, w)
                number = normalize((c or {}).get("wire_no") or "").replace(" ", "")
                if not number:
                    continue
                node = node_of.get(w["from"])
                if node is None:
                    continue
                compared += 1
                by_number[number].add(node)
                if not circuit_whatif._is_bus(node):
                    by_node[node].add(number)
                where[(number, node)] += [w["from"][1], w["to"][1]]
            for number, nodes in by_number.items():
                if len(nodes) < 2:
                    continue
                groups = sorted(nodes, key=lambda n: len(where[(number, n)]))
                minority = groups[0]
                self.add("wire_numbers", "warning", "同じ電線番号が別々のつながりにある",
                         f"{self.names.get(pid)} の電線番号 {number} の配線が {len(nodes)} つのつながりに分かれています",
                         [_loc(pid, ref, self.names) for ref in dict.fromkeys(where[(number, minority)])],
                         [f"電線番号 {number} の配線が {len(nodes)} つのノードにある（同じ番号は同じ線のはず）",
                          f"いちばん配線の少ないつながり（{minority}）の記号を先に確かめる",
                          "配線の途切れ（つなぎ忘れ・分岐点の見落とし）か、電線番号の読み違い"],
                         "途切れている配線をつなぐか、電線番号を直す", key=(pid, "number", number))
            for node, numbers in by_node.items():
                if len(numbers) < 2:
                    continue
                refs = [ref for n in sorted(numbers) for ref in where[(n, node)]]
                self.add("wire_numbers", "warning", "1 つのつながりに電線番号が 2 つ以上",
                         f"{self.names.get(pid)} の 1 つのつながりに電線番号 {'・'.join(sorted(numbers))} が混ざっています",
                         [_loc(pid, ref, self.names) for ref in dict.fromkeys(refs)],
                         ["違う電線番号の配線が 1 つのノードにつながっている", "余分な接続（誤った分岐点・交差の誤認）か、電線番号の読み違い"],
                         "余分な接続を外すか、電線番号を直す", key=(pid, "node", node, tuple(sorted(numbers))))
        self.record("wire_numbers", "配線のつながり", "電線番号", compared,
                    None if compared else "電線番号の付いた配線がありません")

    def _connection(self, page: dict[str, Any], wire: dict[str, Any]) -> dict[str, Any] | None:
        cache = page.setdefault("_wire_lookup", {})
        if not cache:
            for c in page["connections"]:
                cache.setdefault((c["from_symbol_ref"], c["to_symbol_ref"]), c)
        return cache.get((wire["from"][1], wire["to"][1]))

    # --------------------------------------------------------------------------------------- 行先札
    def sheet_link_numbers(self) -> None:
        compared = 0
        numbers: dict[tuple[int, str], set[str]] = defaultdict(set)
        for page in self.pages:
            for c in page["connections"]:
                if c.get("wire_no"):
                    for side in ("from", "to"):
                        numbers[(page["id"], c[f"{side}_symbol_ref"])].add(normalize(c["wire_no"]).replace(" ", ""))
        for link in self.circuit.sheet_links:
            a, b = link["from"], link["to"]
            na, nb = numbers.get((a["project_id"], a["symbol_ref"]), set()), numbers.get((b["project_id"], b["symbol_ref"]), set())
            if not na or not nb:
                continue
            compared += 1
            if na & nb:
                continue
            self.add("sheet_links", "error", "行先札の両側で電線番号が違う",
                     f"{a['sheet']} の札 {a['label'] or a['symbol_ref']}（{'・'.join(sorted(na))}）と "
                     f"{b['sheet']} の札 {b['label'] or b['symbol_ref']}（{'・'.join(sorted(nb))}）",
                     [_loc(a["project_id"], a["symbol_ref"], self.names), _loc(b["project_id"], b["symbol_ref"], self.names)],
                     ["札を組にした（向かい合う札）が、両側の配線の電線番号に共通のものが無い",
                      "札の組み違い（同じ行先の別の札）か、電線番号の読み違い"],
                     "札のラベルに電線番号を入れて組を確かめる", key=(a["project_id"], a["symbol_ref"]))
        self.record("sheet_links", "行先札の組", "両側の電線番号", compared,
                    None if self.circuit.sheet_links else "図面間でつないだ行先札がありません")

    # ----------------------------------------------------------------------------- シミュレーション
    def _trials(self) -> list[dict[str, bool]]:
        return [{}] + [{dev: True} for dev in self.circuit.inputs[:MAX_INPUT_TRIALS]]

    def solver_agreement(self) -> None:
        engines = []
        if circuit_model.ngspice_path():
            engines.append("ngspice")
        try:
            import lcapy  # noqa: F401
            engines.append("Lcapy")
        except ImportError:
            pass
        if not self.solvers or not engines or not self.circuit.elements:
            self.record("solvers", "内蔵の回路計算", "ngspice・Lcapy", 0,
                        "ngspice も Lcapy も入っていません" if self.solvers and self.circuit.elements else "対象外")
            return
        from services import circuit_interop

        compared = 0
        for inputs in self._trials():
            closed = circuit_interop.contact_states(self.circuit, inputs, {})
            text = circuit_model.netlist(self.circuit, closed)
            builtin = {k: v for k, v in circuit_model._solve_mna(text)[0].items()}
            results = {"内蔵": {n: circuit_model._volt({k.lower(): v for k, v in builtin.items()}, n) for n in self.circuit.nodes}}
            if "ngspice" in engines:
                volts = circuit_model._solve_ngspice(text, circuit_model.ngspice_path())[0]
                results["ngspice"] = {n: circuit_model._volt(volts, n) for n in self.circuit.nodes}
            if "Lcapy" in engines:
                results["Lcapy"] = circuit_interop.solve_lcapy(self.circuit, closed)
            compared += 1
            live = {name: {n for n, v in r.items() if v >= LIVE} for name, r in results.items()}
            differ = {n for s in live.values() for n in s} - set.intersection(*live.values())
            if not differ:
                continue
            votes = {n: [name for name, s in live.items() if n in s] for n in sorted(differ)}
            label = "、".join(f"{k}={'ON' if v else 'OFF'}" for k, v in inputs.items()) or "すべての入力 OFF"
            self.add("solvers", "warning", "回路計算の方法で結果が違う",
                     f"{label} のとき、{len(differ)} 個のノードで電圧の有無が方法によって違います",
                     [], [f"{n}: 電圧ありと解いた方法 = {'・'.join(v) or 'なし'}" for n, v in list(votes.items())[:10]]
                     + ["多数決で外れた方法の解き方（開いた接点の扱い・浮いたノード）を確かめる"],
                     "浮いたノード（どこにもつながらない線）や短絡を図面で確かめる", key=(label,))
        self.record("solvers", "内蔵の回路計算", "・".join(engines), compared)

    def impact_vs_whatif(self) -> None:
        compared = 0
        for dev in self.circuit.inputs[:MAX_INPUT_TRIALS]:
            reach = circuit_whatif.impact(self.circuit, dev)
            devices = {d["device"] for d in reach["devices"]}
            loads = {(x["project_id"], x["symbol_ref"]) for x in reach["loads"]}
            r = circuit_whatif.what_if(self.circuit, {}, {}, {"inputs": {dev: True}})
            compared += 1
            outside = [c["device"] for c in r["changes"]["coils"] if c["device"] not in devices]
            outside_loads = [x for x in r["changes"]["loads"] if (x["project_id"], x["symbol_ref"]) not in loads]
            if outside or outside_loads:
                self.add("impact", "error", "波及範囲の外で状態が変わった",
                         f"{dev} を ON にすると、波及範囲に入っていない {len(outside) + len(outside_loads)} 個が変わりました",
                         [_loc(x["project_id"], x["symbol_ref"], self.names) for x in outside_loads],
                         ["静的な波及範囲は取りこぼさない側のはず → 回路の組み方の矛盾",
                          f"範囲外で変わったリレー: {'、'.join(outside) or 'なし'}"],
                         "開発者に報告する（回路の組み方の不具合）", key=(dev,))
        self.record("impact", "静的な波及範囲", "もしも（入力を 1 つずつ ON）", compared,
                    None if self.circuit.inputs else "外部入力がありません")

    def stability(self) -> None:
        compared = 0
        rung = circuit_whatif.rungs(self.circuit) if self.circuit.elements else {}
        for inputs in self._trials():
            r = simulate(self.circuit, inputs, {})
            compared += 1
            if not any("落ち着きません" in w for w in r["warnings"]):
                continue
            label = "、".join(f"{k}=ON" for k in inputs) or "すべての入力 OFF"
            for dev, coils in self.circuit.coils.items():
                own = {rung[e.name] for e in coils}
                selfcut = [e for e in self.circuit.elements
                           if e.kind == "b" and e.device == dev and rung[e.name] in own]
                for e in selfcut:
                    s = self.symbols.get((e.project_id, e.symbol_ref)) or {}
                    self.add("stability", "warning", "自分の b 接点でコイルを切っている（発振）",
                             f"{label} のとき、{dev} のコイルの段に {dev} 自身の b 接点があり、状態が落ち着きません",
                             [_loc(e.project_id, e.symbol_ref, self.names)],
                             ["シミュレーションでリレーの状態が繰り返し変わった",
                              f"{dev} のコイルと同じ段に {dev} の b 接点（{e.symbol_ref}）がある → 自分で自分を切る回路",
                              "自己保持の回路なら a 接点のはず → a/b の読み違いの可能性", f"記号: {_symbol_trust(s)}"],
                             f"接点 {e.symbol_ref} を a 接点にする（リレー表があれば表で確かめる）",
                             {"type": "set_class", "project_id": e.project_id, "symbol_ref": e.symbol_ref,
                              "value": "contact_a"}, key=(e.project_id, e.symbol_ref))
            break
        self.record("stability", "シミュレーション", "接点の種類", compared,
                    None if self.circuit.elements else "回路の素子がありません")

    def run(self) -> dict[str, Any]:
        for step in (self.relay_table, self.coil_sheet, self.wire_numbers, self.sheet_link_numbers,
                     self.solver_agreement, self.impact_vs_whatif, self.stability):
            step()
        for page in self.pages:
            page.pop("_wire_lookup", None)
        # 同じ記号への直し方をまとめる: 同じ直し方は 1 つにし、違う直し方がぶつかるときは人の判断に回す。
        by_symbol: dict[tuple, list[dict[str, Any]]] = defaultdict(list)
        for issue in self.issues:
            if issue.get("fix"):
                f = issue["fix"]
                by_symbol[(f["type"], f["project_id"], f["symbol_ref"])].append(issue)
        for group in by_symbol.values():
            values = {i["fix"]["value"] for i in group}
            if len(values) > 1:
                for i in group:
                    i["review"].append("同じ記号に別の直し方を出した矛盾がある → 人が判断する")
                    i["fix"] = None
                continue
            for i in group[1:]:
                group[0]["review"].append(f"（「{i['title']}」の精査でも同じ直し方になった）")
                i["review"].append(f"（「{group[0]['title']}」と同じ直し方。そちらで適用する）")
                i["fix"] = None
        order = {"error": 0, "warning": 1, "info": 2}
        self.issues.sort(key=lambda i: (order[i["severity"]], i["check"]))
        return {"checks": self.checks, "issues": self.issues,
                "summary": {"errors": sum(i["severity"] == "error" for i in self.issues),
                            "warnings": sum(i["severity"] == "warning" for i in self.issues),
                            "fixable": sum(bool(i["fix"]) for i in self.issues)}}


def check(pages: list[dict[str, Any]], structures: dict[int, dict[str, Any] | None], *,
          bus_texts: dict[int, list[dict[str, Any]]] | None = None, solvers: bool = True) -> dict[str, Any]:
    circuit = circuit_model.build_circuit(pages, bus_texts)
    return Checker(pages, structures, circuit, solvers=solvers).run()
