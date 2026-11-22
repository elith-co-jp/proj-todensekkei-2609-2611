"""アノテーションの対応表（Excel / CSV）の書き出し・読み込み・照合（DD-0035）。DB を使わない。

東電設計様との正解のやり取りに使う表。列は会議資料の提案どおり:

    図面セットID／ページID／要素ID／種別／原文／参照・接続先ID／判定／根拠・備考

要素 ID は「ページ:記号 ref」、端子は「ページ:記号 ref/端子名」。Elith が候補を書き出し、東電設計様が判定の列を
直して返す。返ってきた表を読み込み、機械の表と関係ごと（配線・別ページの継続・コイルと接点の対応）に照合する。
座標付けやデータの整形は Elith 側で行うので、表には位置を入れない（位置の正本はアノテーション）。

もう 1 枚のシート「代表回路」は、シナリオ（circuit_scenarios）を 1 行 1 例で書く表。
"""

from __future__ import annotations

import csv
import io
from collections import defaultdict
from typing import Any

from services import circuit_trace as t
from services import sheet_links
from services.circuit_model import Circuit

COLUMNS = ["図面セットID", "ページID", "要素ID", "種別", "原文", "参照・接続先ID", "判定", "根拠・備考"]
SCENARIO_COLUMNS = ["シナリオID", "問い", "対象・起点", "成立条件", "入力の状態", "期待する判定", "影響先", "ページ", "備考"]

# 関係の種別（照合の単位）
WIRE = "配線"
SHEET_REF = "別ページの継続"
COIL_CONTACT = "コイルと接点の対応"
RELATIONS = (WIRE, SHEET_REF, COIL_CONTACT)
CONNECTED = {"接続", "接続確定", "接続（人が確認）", "接続（AI推定・要確認）", "対応", "確定", "推定", "コイルと対応", "○", "ok"}
NOT_CONNECTED = {"非接続", "交差", "誤り", "×", "ng", "なし"}
UNKNOWN = {"不明", "要確認", "?", "判定不能", "参照先欠落", "未解決"}


def _page_id(page: dict[str, Any]) -> str:
    return str(page.get("sheet_no") or sheet_links.page_sheet(page) or page.get("name") or page["id"])


def _terminal_names(page: dict[str, Any]) -> dict[tuple[str, str], str]:
    return {(s["ref"], term["ref"]): str(term.get("name") or term["ref"]) for s in page["symbols"]
            for term in s.get("terminals") or []}


def export_rows(pages: list[dict[str, Any]], circuit: Circuit, *, drawing_set: str = "") -> list[dict[str, str]]:
    """機械（AI と人の入力）の対応表。記号 1 個 1 行、配線 1 本 1 行、コイルと接点の対応 1 組 1 行、札の組 1 組 1 行。"""
    ids = {p["id"]: _page_id(p) for p in pages}
    tracer = t.Tracer(circuit, pages)
    rows: list[dict[str, str]] = []

    def row(pid: int, element: str, kind: str, text: str = "", target: str = "", judge: str = "", note: str = "") -> None:
        rows.append(dict(zip(COLUMNS, [drawing_set, ids.get(pid, str(pid)), element, kind, text, target, judge, note], strict=False)))

    for p in pages:
        pid = p["id"]
        for s in p["symbols"]:
            row(pid, f"{ids[pid]}:{s['ref']}", s.get("class_label") or s["class_key"], s.get("label") or "",
                judge="AI候補（要確認）" if s.get("origin") == "ai" else "")
        terms = _terminal_names(p)
        for c in p["connections"]:
            if c.get("kind", "wire") not in ("wire", "sheet_ref"):
                continue
            a = f"{ids[pid]}:{c['from_symbol_ref']}/{terms.get((c['from_symbol_ref'], c.get('from_terminal_ref')), '')}"
            b = f"{ids[pid]}:{c['to_symbol_ref']}/{terms.get((c['to_symbol_ref'], c.get('to_terminal_ref')), '')}"
            judge = "接続（AI推定・要確認）" if c.get("origin") == "ai" else "接続（人が確認）"
            note = "；".join(x for x in [f"電線番号 {c['wire_no']}" if c.get("wire_no") else "",
                                         f"参照 {c['external_ref']}" if c.get("external_ref") else ""] if x)
            row(pid, a.rstrip("/"), WIRE if c.get("kind", "wire") == "wire" else SHEET_REF, "", b.rstrip("/"), judge, note)
    for link in circuit.sheet_links:
        f, to = link["from"], link["to"]
        row(f["project_id"], f"{ids.get(f['project_id'])}:{f['symbol_ref']}", SHEET_REF, f.get("label") or "",
            f"{ids.get(to['project_id'])}:{to['symbol_ref']}", "確定" if link["how"] == "matched" else "推定（要確認）",
            "行先札・来先札の組")
    for d in circuit.diagnostics:
        if d.get("kind") == "sheet_link_unresolved" and d.get("project_id") is not None:
            row(d["project_id"], f"{ids.get(d['project_id'])}:{d.get('symbol_ref', '')}", SHEET_REF, "", "", "未対応",
                d.get("message", ""))
    for dev, src in sorted(tracer.sources.items()):
        coils = [f"{c['page']}:{c['symbol_ref']}" for c in src.get("coils", [])]
        for contact in src.get("contacts", []):
            element = f"{ids.get(contact['project_id'])}:{contact['symbol_ref']}"
            if src["source"] == t.RELAY:
                for coil in coils:
                    row(contact["project_id"], element, COIL_CONTACT, contact.get("label", ""), coil, "対応",
                        f"器具番号 {dev}")
            else:
                row(contact["project_id"], element, COIL_CONTACT, contact.get("label", ""), src.get("ref_sheet") or "",
                    t.source_label(src), src.get("reason") or "")
    return rows


def scenario_template() -> list[dict[str, str]]:
    """代表回路の記入例（会議資料の架空の回路）。"""
    return [
        dict(zip(SCENARIO_COLUMNS, ["S1", "起動条件", "Y", "A & (B | C) & D", "A=1, B=0, C=1, D=1", "成立", "", "P03",
                                    "C 側の枝で通電（架空の例）"], strict=False)),
        dict(zip(SCENARIO_COLUMNS, ["S1", "起動条件", "Y", "", "A=1, B=1, C=1, D=0", "不成立", "", "", "戻り側が切れている"], strict=False)),
        dict(zip(SCENARIO_COLUMNS, ["S1", "起動条件", "Y", "", "A=1, B=0, C=?, D=1", "判定不能", "", "", "C の参照先が不明"], strict=False)),
        dict(zip(SCENARIO_COLUMNS, ["S2", "影響", "A", "", "", "", "Y", "", "A が変わったら影響する出力"], strict=False)),
    ]


# ------------------------------------------------------------------------------------------- 書き出し


def to_csv(rows: list[dict[str, str]], columns: list[str] = COLUMNS) -> bytes:
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=columns, extrasaction="ignore")
    w.writeheader()
    w.writerows(rows)
    return ("﻿" + buf.getvalue()).encode("utf-8")  # Excel で文字化けしないよう BOM を付ける


def to_xlsx(rows: list[dict[str, str]], scenarios: list[dict[str, str]] | None = None) -> bytes:
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill

    wb = Workbook()
    sheets = [("対応表", COLUMNS, rows), ("代表回路", SCENARIO_COLUMNS, scenarios if scenarios is not None else scenario_template())]
    for i, (title, columns, data) in enumerate(sheets):
        ws = wb.active if i == 0 else wb.create_sheet()
        ws.title = title
        ws.append(columns)
        for cell in ws[1]:
            cell.font = Font(bold=True)
            cell.fill = PatternFill("solid", fgColor="DDEBF7")
        for r in data:
            ws.append([r.get(c, "") for c in columns])
        for j, c in enumerate(columns):
            width = max([len(str(c))] + [len(str(r.get(c, ""))) for r in data[:500]])
            ws.column_dimensions[ws.cell(1, j + 1).column_letter].width = min(60, max(10, width * 1.6))
        ws.freeze_panes = "A2"
    out = io.BytesIO()
    wb.save(out)
    return out.getvalue()


# ------------------------------------------------------------------------------------------- 読み込み


def read_table(blob: bytes, filename: str = "") -> dict[str, list[dict[str, str]]]:
    """xlsx（対応表・代表回路のシート）か CSV（対応表）を読む。"""
    if filename.lower().endswith(".csv") or not blob.startswith(b"PK"):
        text = blob.decode("utf-8-sig")
        return {"対応表": [{k: (v or "").strip() for k, v in r.items()} for r in csv.DictReader(io.StringIO(text))],
                "代表回路": []}
    from openpyxl import load_workbook

    wb = load_workbook(io.BytesIO(blob), read_only=True, data_only=True)
    out: dict[str, list[dict[str, str]]] = {"対応表": [], "代表回路": []}
    for ws in wb.worksheets:
        values = list(ws.iter_rows(values_only=True))
        if not values:
            continue
        header = [str(v or "").strip() for v in values[0]]
        kind = "代表回路" if "シナリオID" in header else "対応表" if "要素ID" in header else None
        if kind is None:
            continue
        for v in values[1:]:
            r = {h: ("" if x is None else str(x).strip()) for h, x in zip(header, v, strict=False) if h}
            if any(r.values()):
                out[kind].append(r)
    return out


def _states(text: str) -> dict[str, Any]:
    """「A=1, B=0, C=?」→ {A: True, B: False, C: None}。"""
    out: dict[str, Any] = {}
    for part in text.replace("、", ",").replace("，", ",").split(","):
        if "=" not in part:
            continue
        k, v = part.split("=", 1)
        v = v.strip()
        out[k.strip().strip('"')] = None if v in ("?", "？", "不明", "") else v.lower() in ("1", "true", "on", "動作", "閉")
    return out


def scenarios_from_rows(rows: list[dict[str, str]]) -> list[dict[str, Any]]:
    """代表回路のシートの行をシナリオにする（同じシナリオ ID の行は例を足す）。"""
    out: dict[str, dict[str, Any]] = {}
    for r in rows:
        sid = r.get("シナリオID") or f"S{len(out) + 1}"
        s = out.setdefault(sid, {"id": sid, "question": r.get("問い") or "起動条件", "cases": [], "affected": []})
        who = r.get("対象・起点") or ""
        if s["question"] in ("影響", "順引き", "影響確認", "forward"):
            s.setdefault("origin", who)
            s["affected"] += [x.strip() for x in (r.get("影響先") or "").replace("、", ",").split(",") if x.strip()]
        else:
            s.setdefault("target", who)
        if r.get("成立条件"):
            s["condition"] = r["成立条件"]
        if r.get("入力の状態") or r.get("期待する判定"):
            s["cases"].append({"states": _states(r.get("入力の状態") or ""), "expected": r.get("期待する判定"),
                               "note": r.get("備考")})
        if r.get("ページ"):
            s["pages"] = list(dict.fromkeys(s.get("pages", []) + [x.strip() for x in r["ページ"].replace("、", ",").split(",")
                                                                    if x.strip()]))
    return list(out.values())


# ------------------------------------------------------------------------------------------- 照合


def _judge(text: str) -> str:
    t_ = (text or "").strip()
    if t_ in NOT_CONNECTED:
        return "no"
    if t_ in UNKNOWN or "要確認" in t_ and "接続" not in t_:
        return "unknown"
    return "yes"


def _pairs(rows: list[dict[str, str]], kind: str) -> dict[frozenset[str], str]:
    out: dict[frozenset[str], str] = {}
    for r in rows:
        if r.get("種別") != kind or not r.get("参照・接続先ID"):
            continue
        key = frozenset([r["要素ID"], r["参照・接続先ID"]])
        out[key] = _judge(r.get("判定", ""))
    return out


def compare(gold_rows: list[dict[str, str]], machine_rows: list[dict[str, str]]) -> dict[str, Any]:
    """関係（配線・別ページの継続・コイルと接点の対応）ごとの適合率・再現率と、原文（ラベル）の完全一致率。

    正解で「非接続」の組を機械が出していれば誤り（特に誤結線）、正解で「不明」の組は数えない。
    """
    result: dict[str, Any] = {}
    for kind in RELATIONS:
        gold = _pairs(gold_rows, kind)
        pred = {k for k, v in _pairs(machine_rows, kind).items() if v != "no"}
        positives = {k for k, v in gold.items() if v == "yes"}
        judged = {k for k, v in gold.items() if v != "unknown"}
        tp = pred & positives
        fp = {k for k in pred if k in judged and k not in positives} | {k for k in pred if k not in gold}
        fn = positives - pred
        result[kind] = {
            "gold": len(positives), "machine": len(pred), "tp": len(tp), "fp": len(fp), "fn": len(fn),
            "wrong_connections": len({k for k in pred if gold.get(k) == "no"}),
            "precision": round(len(tp) / (len(tp) + len(fp)), 4) if tp or fp else None,
            "recall": round(len(tp) / len(positives), 4) if positives else None,
            "missed": sorted(" ↔ ".join(sorted(k)) for k in fn)[:50],
            "extra": sorted(" ↔ ".join(sorted(k)) for k in fp)[:50],
        }
    gold_text = {r["要素ID"]: r.get("原文", "") for r in gold_rows if r.get("種別") not in RELATIONS and r.get("原文")}
    mine_text = {r["要素ID"]: r.get("原文", "") for r in machine_rows if r.get("種別") not in RELATIONS}
    common = [k for k in gold_text if k in mine_text]
    exact = [k for k in common if gold_text[k].replace(" ", "") == mine_text[k].replace(" ", "")]
    result["原文"] = {"compared": len(common), "exact": len(exact),
                    "exact_rate": round(len(exact) / len(common), 4) if common else None,
                    "missing_elements": len([k for k in gold_text if k not in mine_text]),
                    "mismatches": [{"要素ID": k, "正解": gold_text[k], "機械": mine_text[k]}
                                   for k in common if k not in exact][:50]}
    kinds = defaultdict(lambda: [0, 0])
    for r in gold_rows:
        if r.get("種別") not in RELATIONS:
            kinds[r["種別"]][0] += 1
            kinds[r["種別"]][1] += int(r["要素ID"] in mine_text)
    result["要素"] = {k: {"gold": v[0], "found": v[1]} for k, v in sorted(kinds.items())}
    return result
