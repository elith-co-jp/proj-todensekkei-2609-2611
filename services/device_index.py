"""機器索引（DD-0044）: 器具番号ごとのコイルと接点の相互参照、電線番号の一覧、Excel 出力。DB を使わない。

展開接続図を読む人が毎日していること（「このリレーの接点はどのページのどこにあるか」「この電線番号はどこからどこへ
行くか」「a 接点・b 接点を何個使っているか」）を、組んだ回路（circuit_model.Circuit）とアノテーションから一覧にする。

- 器具（devices）: リレー（選んだ図面にコイルがある）・外部入力（コイルが無い接点）・負荷（表示灯・電磁弁など）。
  コイルと接点の場所（ページ・位置・枠）、a / b 接点の数、タイマーの設定、接点の枠に書かれたコイルのシート番号。
- 要確認（notes）: 読み取りの誤りや図面範囲の不足で起きやすいことだけを挙げる。判定はしない（検査タブの整合性
  チェックとは役割を分け、ここでは「どこを見ればよいか」を示す）。
- 電線番号（wire_numbers）: 番号ごとに、その番号の配線がつなぐ記号（分岐点を除く）とページ。同じ番号が別のノードにあれば印を付ける。

位置は図面の幅・高さに対する割合（0〜1）と、人が読む「左上・中央・右下」などの言葉で返す。区画番号（図枠の
1〜8・A〜F）は図面ごとに違い、読み取りも無いため使わない。
"""

from __future__ import annotations

import io
import re
from collections import defaultdict
from typing import Any

from analysis.text_extraction import SHEET_REF
from analysis.wiring.classes import PASS_THROUGH
from services import sheet_links
from services.circuit_model import Circuit, canonical_device, device_key

RELAY = "relay"
INPUT = "input"
LOAD = "load"
ROLE_LABEL = {RELAY: "リレー", INPUT: "外部入力", LOAD: "負荷"}
KIND_LABEL = {"a": "a 接点", "b": "b 接点"}
TIMER_LABEL = {"on_delay": "オンディレー", "off_delay": "オフディレー"}
_SHEET = re.compile(SHEET_REF)
_WIRE_SPACE = re.compile(r"\s+")


def area_name(cx: float, cy: float) -> str:
    """図面の中の位置を 3×3 の言葉にする（例: 左上、中央、右下）。"""
    col = "左" if cx < 1 / 3 else "右" if cx > 2 / 3 else "中"
    row = "上" if cy < 1 / 3 else "下" if cy > 2 / 3 else "中"
    if col == row == "中":
        return "中央"
    if col == "中":
        return f"中{row}"
    if row == "中":
        return f"{col}中"
    return f"{col}{row}"


def _page_name(page: dict[str, Any]) -> str:
    return str(page.get("sheet_no") or page.get("name") or page["id"])


def _sheet_ref(label: str | None) -> str | None:
    """接点の枠に書かれたコイルのシート番号（「33HB FV2306 (ER21236)」の ER21236）。"""
    m = re.search(r"[(（]([^)）]*)[)）]?", label or "")
    if not m:
        return None
    found = _SHEET.search(m.group(1).replace(" ", "").upper())
    return found.group(0) if found else None


def _norm_wire(text: str) -> str:
    return _WIRE_SPACE.sub("", str(text)).upper()


class _Locator:
    def __init__(self, pages: list[dict[str, Any]]) -> None:
        self.names = {p["id"]: _page_name(p) for p in pages}
        self.sheets = {p["id"]: sheet_links.page_sheet(p) for p in pages}
        self.symbols = {(p["id"], s["ref"]): s for p in pages for s in p["symbols"]}

    def __call__(self, pid: int, ref: str) -> dict[str, Any]:
        s = self.symbols.get((pid, ref), {})
        cx, cy = float(s.get("cx", 0.5)), float(s.get("cy", 0.5))
        return {"project_id": pid, "page": self.names.get(pid, str(pid)), "symbol_ref": ref,
                "label": s.get("label") or "", "cx": cx, "cy": cy, "w": float(s.get("w", 0.0)), "h": float(s.get("h", 0.0)),
                "area": area_name(cx, cy), "origin": s.get("origin")}


def build_index(pages: list[dict[str, Any]], circuit: Circuit) -> dict[str, Any]:
    """器具の相互参照と電線番号の一覧。"""
    locate = _Locator(pages)
    devices: dict[str, dict[str, Any]] = {}

    def entry(device: str, role: str) -> dict[str, Any]:
        if device not in devices:
            devices[device] = {"device": device, "role": role, "role_label": ROLE_LABEL[role],
                               "coils": [], "contacts": [], "loads": [], "labels": set(), "notes": []}
        return devices[device]

    unlabeled: list[dict[str, Any]] = []
    for e in circuit.elements:
        if e.kind in ("a", "b"):
            loc = locate(e.project_id, e.symbol_ref)
            if device_key(e.label) is None:
                unlabeled.append({**loc, "kind": e.kind, "kind_label": KIND_LABEL[e.kind]})
                continue
            role = RELAY if e.device in circuit.coils else INPUT
            d = entry(e.device or "", role)
            d["contacts"].append({**loc, "kind": e.kind, "kind_label": KIND_LABEL[e.kind], "coil_sheet": _sheet_ref(e.label),
                                  "instant": e.instant})
            d["labels"].add(device_key(e.label))
        elif e.kind == "load":
            loc = locate(e.project_id, e.symbol_ref)
            if e.device and e.device in circuit.coils:
                d = entry(e.device, RELAY)
                d["coils"].append(loc)
                d["labels"].add(device_key(e.label))
            else:
                symbol = locate.symbols.get((e.project_id, e.symbol_ref), {})
                name = device_key(e.label) or f"{loc['page']} {e.symbol_ref}"
                d = entry(name, LOAD)
                d["loads"].append({**loc, "class_label": symbol.get("class_label") or symbol.get("class_key") or ""})

    pages_in_set = {s for s in locate.sheets.values() if s}
    for dev, d in devices.items():
        timer = circuit.timers.get(dev)
        d["timer"] = ({**timer, "mode_label": TIMER_LABEL.get(timer.get("mode"), timer.get("mode"))} if timer else None)
        d["a_count"] = sum(c["kind"] == "a" for c in d["contacts"])
        d["b_count"] = sum(c["kind"] == "b" for c in d["contacts"])
        d["pages"] = sorted({x["page"] for x in d["coils"] + d["contacts"] + d["loads"]})
        variants = sorted(x for x in d.pop("labels") if x and x != dev)
        d["variants"] = variants
        notes: list[dict[str, str]] = d["notes"]
        if d["role"] == RELAY and not d["contacts"]:
            notes.append({"kind": "no_contacts", "message": "選んだ図面に接点が無い（ほかの図面にあるか、接点の器具番号の読み違い）"})
        if len(d["coils"]) > 1:
            where = "、".join(f"{c['page']} {c['area']}" for c in d["coils"])
            notes.append({"kind": "duplicate_coil", "message": f"コイルが {len(d['coils'])} 個ある（{where}）。器具番号の重複か読み違い"})
        if variants:
            notes.append({"kind": "label_variants",
                          "message": f"表記が違うものを同じ器具にまとめた: {'、'.join(variants)}（O と 0 などの読み違いの可能性）"})
        coil_pages = {locate.sheets.get(c["project_id"]) for c in d["coils"]}
        written = sorted({c["coil_sheet"] for c in d["contacts"] if c["coil_sheet"]})
        if d["role"] == RELAY:
            if wrong := [s for s in written if s not in coil_pages]:
                notes.append({"kind": "coil_sheet_mismatch",
                              "message": f"接点の枠に書かれたコイルのシート {'、'.join(wrong)} と、コイルのあるページが違う"})
        elif d["role"] == INPUT and (missing := [s for s in written if s in pages_in_set]):
            notes.append({"kind": "coil_missing_in_set",
                          "message": f"コイルのシート {'、'.join(missing)} を選んでいるのにコイルが見つからない（器具番号の読み違いの可能性）"})
        elif d["role"] == INPUT and written:
            notes.append({"kind": "coil_outside_set",
                          "message": f"コイルはシート {'、'.join(written)}（選んだ図面の外）。外部入力として扱っている"})
        for x in d["coils"] + d["contacts"] + d["loads"]:
            x["sort"] = (x["page"], x["cy"], x["cx"])
        for key in ("coils", "contacts", "loads"):
            d[key].sort(key=lambda x: x.pop("sort"))

    order = {RELAY: 0, INPUT: 1, LOAD: 2}
    listed = sorted(devices.values(), key=lambda d: (order[d["role"]], canonical_device(d["device"])))
    wires = wire_numbers(pages, circuit, locate)
    unlabeled.sort(key=lambda x: (x["page"], x["cy"], x["cx"]))
    return {
        "pages": [{"id": p["id"], "name": locate.names[p["id"]]} for p in pages],
        "devices": listed,
        "unlabeled_contacts": unlabeled,
        "wire_numbers": wires,
        "counts": {
            "relays": sum(d["role"] == RELAY for d in listed), "inputs": sum(d["role"] == INPUT for d in listed),
            "loads": sum(d["role"] == LOAD for d in listed),
            "contacts": sum(len(d["contacts"]) for d in listed) + len(unlabeled),
            "wire_numbers": len(wires), "notes": sum(len(d["notes"]) for d in listed) + len(unlabeled)
            + sum(1 for w in wires if w["note"]),
        },
    }


def wire_numbers(pages: list[dict[str, Any]], circuit: Circuit, locate: _Locator | None = None) -> list[dict[str, Any]]:
    """電線番号ごとに、その番号の配線がつなぐ記号（分岐点を除く）とページ。同じ番号が別のノードにあれば note を付ける。"""
    locate = locate or _Locator(pages)
    found: dict[str, dict[str, Any]] = {}
    nodes: dict[str, set[str]] = defaultdict(set)
    for p in pages:
        pid = p["id"]
        for c in p["connections"]:
            raw = str(c.get("wire_no") or "").strip()
            if not raw or c.get("kind", "wire") not in ("wire", "sheet_ref"):
                continue
            key = _norm_wire(raw)
            w = found.setdefault(key, {"wire_no": raw, "spellings": set(), "connections": 0, "ends": {}})
            w["spellings"].add(raw)
            w["connections"] += 1
            for ref in (c["from_symbol_ref"], c["to_symbol_ref"]):
                s = locate.symbols.get((pid, ref), {})
                # 分岐点・ラベルの無い通り抜けの点は、人が図面で探す目印にならないので並べない
                if s.get("class_key") == "junction" or (s.get("class_key") in PASS_THROUGH and not s.get("label")):
                    continue
                if (pid, ref) not in w["ends"]:
                    w["ends"][(pid, ref)] = {**locate(pid, ref), "class_label": s.get("class_label") or s.get("class_key") or ""}
            node = circuit.node_of.get((pid, c["from_symbol_ref"], c.get("from_terminal_ref") or ""))
            if node:
                nodes[key].add(node)
    out = []
    for key, w in found.items():
        n = len(nodes.get(key, ()))
        spellings = sorted(w.pop("spellings"))
        w["ends"] = sorted(w["ends"].values(), key=lambda e: (e["page"], e["cy"], e["cx"]))
        w["pages"] = sorted({e["page"] for e in w["ends"]})
        w["node_count"] = n
        notes = []
        if n > 1:
            notes.append(f"同じ番号が {n} つの別のノードにある（つながりの読み落としか、番号の読み違い。検査タブの電線番号の照合で確認）")
        if len(spellings) > 1:
            notes.append(f"書き方が違う: {'、'.join(spellings)}")
        w["note"] = "。".join(notes)
        out.append(w)
    return sorted(out, key=lambda w: _natural(w["wire_no"]))


def _natural(text: str) -> list[Any]:
    return [(0, int(t)) if t.isdigit() else (1, t) for t in re.split(r"(\d+)", _norm_wire(text)) if t]


# ------------------------------------------------------------------------------------------- Excel

DEVICE_COLUMNS = ["器具番号", "種類", "コイルのページ", "コイルの位置", "a 接点数", "b 接点数", "接点の場所", "タイマー", "要確認"]
CONTACT_COLUMNS = ["器具番号", "種類", "接点", "ページ", "位置", "記号 ref", "原文", "枠のコイルシート", "コイルのページ", "備考"]
WIRE_COLUMNS = ["電線番号", "ページ", "つながる記号", "位置", "種類", "ノード数", "要確認"]
NOTE_COLUMNS = ["対象", "ページ", "位置", "内容"]


def _where(items: list[dict[str, Any]]) -> str:
    return "、".join(f"{x['page']} {x['area']}" for x in items)


def _name(end: dict[str, Any]) -> str:
    return f"{end['label']}（{end['symbol_ref']}）" if end.get("label") else end["symbol_ref"]


def rows(index: dict[str, Any]) -> dict[str, list[list[Any]]]:
    """Excel の各シートの行（見出しを除く）。"""
    device_rows, contact_rows, note_rows = [], [], []
    for d in index["devices"]:
        timer = d.get("timer")
        timer_text = f"{timer['mode_label']} {timer['delay_s']} 秒" if timer else ""
        holders = d["coils"] or d["loads"]
        device_rows.append([d["device"], d["role_label"], "、".join(sorted({c["page"] for c in holders})), _where(holders),
                            d["a_count"], d["b_count"], _where(d["contacts"]), timer_text,
                            "／".join(n["message"] for n in d["notes"])])
        coil_pages = "、".join(sorted({c["page"] for c in d["coils"]}))
        for c in d["contacts"]:
            contact_rows.append([d["device"], d["role_label"], c["kind_label"] + ("（瞬時）" if c.get("instant") else ""),
                                 c["page"], c["area"], c["symbol_ref"], c["label"], c.get("coil_sheet") or "",
                                 coil_pages, "AI 候補（要確認）" if c.get("origin") == "ai" else ""])
        for n in d["notes"]:
            first = (d["coils"] + d["contacts"] + d["loads"] or [{"page": "", "area": ""}])[0]
            note_rows.append([d["device"], first["page"], first["area"], n["message"]])
    for c in index["unlabeled_contacts"]:
        contact_rows.append(["（器具番号なし）", "外部入力", c["kind_label"], c["page"], c["area"], c["symbol_ref"], c["label"],
                             "", "", "器具番号が読めないため、どのリレーの接点か分からない"])
        note_rows.append([f"{c['symbol_ref']}（{c['kind_label']}）", c["page"], c["area"],
                          "器具番号の無い接点。外部入力として扱っている"])
    wire_rows = []
    for w in index["wire_numbers"]:
        for e in w["ends"]:
            wire_rows.append([w["wire_no"], e["page"], _name(e), e["area"], e["class_label"], w["node_count"], w["note"]])
        if w["note"]:
            note_rows.append([f"電線番号 {w['wire_no']}", "、".join(w["pages"]), "", w["note"]])
    return {"機器一覧": device_rows, "接点の場所": contact_rows, "電線番号": wire_rows, "要確認": note_rows}


def to_xlsx(index: dict[str, Any]) -> bytes:
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill

    columns = {"機器一覧": DEVICE_COLUMNS, "接点の場所": CONTACT_COLUMNS, "電線番号": WIRE_COLUMNS, "要確認": NOTE_COLUMNS}
    data = rows(index)
    wb = Workbook()
    for i, (title, cols) in enumerate(columns.items()):
        ws = wb.active if i == 0 else wb.create_sheet()
        ws.title = title
        ws.append(cols)
        for cell in ws[1]:
            cell.font = Font(bold=True)
            cell.fill = PatternFill("solid", fgColor="DDEBF7")
        for r in data[title]:
            ws.append(r)
            for cell in ws[ws.max_row]:
                # OCR の文字が「=」で始まっても式にしない
                if isinstance(cell.value, str) and cell.value.startswith("="):
                    cell.data_type = "s"
                cell.alignment = Alignment(vertical="top", wrap_text=isinstance(cell.value, str) and len(cell.value) > 40)
        for j, c in enumerate(cols):
            width = max([len(str(c))] + [len(str(r[j])) for r in data[title][:500]])
            ws.column_dimensions[ws.cell(1, j + 1).column_letter].width = min(60, max(8, width * 1.6))
        ws.freeze_panes = "A2"
        ws.auto_filter.ref = ws.dimensions
    out = io.BytesIO()
    wb.save(out)
    return out.getvalue()
