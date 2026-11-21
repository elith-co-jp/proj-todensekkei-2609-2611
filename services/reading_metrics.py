"""図面の読み取りの正解率を、文字・記号・線・別ページの参照・コイルと接点の対応・代表回路に分けて測る（DD-0035）。

会議資料 07「文字と回路の正解率を分けて測る」の測り方:

- 記号: 種類別の適合率・再現率（枠の IoU ≥ 0.5 で正解の記号に当て、種類も同じなら正解）。
- 文字: 当てた記号の原文（ラベル）と端子番号の完全一致（全角半角・空白の違いは無視）。器具番号（先頭の語）も別に数える。
- 線: 正しい端子の組の適合率・再現率。機械の配線の両端を正解の端子に当て、正解に同じ組があれば正解。
  正解に無い組のうち、正解では別のネット（つながっていない）のものを「誤結線」として別に数える（同じネットなら経路違い）。
- 別ページの参照: 行先札・来先札の組（circuit_model の sheet_links）の参照先 ID の一致。
- コイルと接点の対応: 接点 → コイルの組の一致と、コイルの無い接点の出どころ（外部入力・参照先欠落・未解決）の一致。
- 代表回路: 正解のシナリオ（circuit_scenarios）を機械の図面で照合した合格数。
- 人の修正量: 直すのに要る操作の数の目安（記号の追加・削除・種類の変更、ラベルの書き直し、配線の追加・削除）。

評価は図面セット単位でまとめ、学習に使った図面セットのページが評価に混じっていないかも調べる（同じ図面・設備の
似たページを学習と評価に分けない）。入力は正解と機械の pages（serialize_project / bundle.json の projects の形）。
"""

from __future__ import annotations

import math
import re
import unicodedata
from collections import Counter, defaultdict
from typing import Any

from services import circuit_scenarios
from services import circuit_trace as t
from services.circuit_model import build_circuit

IOU_MIN = 0.5
TERMINAL_MAX = 0.03  # 端子を当てる距離（画像の幅・高さに対する割合）


def norm_text(text: str | None) -> str:
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", text or "")).upper()


def _iou(a: dict[str, Any], b: dict[str, Any]) -> float:
    ax0, ay0, ax1, ay1 = a["cx"] - a["w"] / 2, a["cy"] - a["h"] / 2, a["cx"] + a["w"] / 2, a["cy"] + a["h"] / 2
    bx0, by0, bx1, by1 = b["cx"] - b["w"] / 2, b["cy"] - b["h"] / 2, b["cx"] + b["w"] / 2, b["cy"] + b["h"] / 2
    iw, ih = max(0.0, min(ax1, bx1) - max(ax0, bx0)), max(0.0, min(ay1, by1) - max(ay0, by0))
    inter = iw * ih
    union = a["w"] * a["h"] + b["w"] * b["h"] - inter
    return inter / union if union > 0 else 0.0


def match_symbols(gold: list[dict[str, Any]], pred: list[dict[str, Any]], iou_min: float = IOU_MIN) -> dict[str, str]:
    """機械の記号 ref → 正解の記号 ref（種類を問わず、IoU の大きい順に 1 対 1）。"""
    pairs = sorted(((_iou(g, p), gi, pi) for gi, g in enumerate(gold) for pi, p in enumerate(pred)), reverse=True)
    used_g, used_p, out = set(), set(), {}
    for iou, gi, pi in pairs:
        if iou < iou_min:
            break
        if gi in used_g or pi in used_p:
            continue
        used_g.add(gi)
        used_p.add(pi)
        out[pred[pi]["ref"]] = gold[gi]["ref"]
    return out


def _terminal_map(gold: dict[str, Any], pred: dict[str, Any], symbol_map: dict[str, str]) -> dict[tuple[str, str], tuple[str, str]]:
    """機械の端子 → 正解の端子。当てた記号の中で、いちばん近い端子（同じ名前があればそれ）。"""
    gold_syms = {s["ref"]: s for s in gold["symbols"]}
    out = {}
    for s in pred["symbols"]:
        g = gold_syms.get(symbol_map.get(s["ref"], ""))
        if g is None:
            continue
        gterms = [x for x in g.get("terminals") or [] if x.get("tx") is not None]
        for term in s.get("terminals") or []:
            if term.get("tx") is None:
                continue
            same = [x for x in gterms if norm_text(x.get("name")) and norm_text(x.get("name")) == norm_text(term.get("name"))]
            cands = same or gterms
            best = min(cands, key=lambda x: math.hypot(x["tx"] - term["tx"], x["ty"] - term["ty"]), default=None)
            if best is not None and (same or math.hypot(best["tx"] - term["tx"], best["ty"] - term["ty"]) <= TERMINAL_MAX):
                out[(s["ref"], term["ref"])] = (g["ref"], best["ref"])
        if not s.get("terminals") and len(gterms) == 1:
            out[(s["ref"], "")] = (g["ref"], gterms[0]["ref"])
    return out


def _edges(page: dict[str, Any]) -> set[frozenset[tuple[str, str]]]:
    out = set()
    for c in page["connections"]:
        if c.get("kind", "wire") not in ("wire", "sheet_ref"):
            continue
        a = (c["from_symbol_ref"], c.get("from_terminal_ref") or "")
        b = (c["to_symbol_ref"], c.get("to_terminal_ref") or "")
        if a != b:
            out.add(frozenset([a, b]))
    return out


def _nets(edges: set[frozenset[tuple[str, str]]]) -> dict[tuple[str, str], tuple[str, str]]:
    parent: dict[tuple[str, str], tuple[str, str]] = {}

    def find(x):
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for e in edges:
        a, b = tuple(e) if len(e) == 2 else (next(iter(e)),) * 2
        parent[find(a)] = find(b)
    return {k: find(k) for k in list(parent)}


def _prf(tp: int, fp: int, fn: int) -> dict[str, Any]:
    return {"tp": tp, "fp": fp, "fn": fn, "precision": round(tp / (tp + fp), 4) if tp + fp else None,
            "recall": round(tp / (tp + fn), 4) if tp + fn else None}


def score_page(gold: dict[str, Any], pred: dict[str, Any]) -> dict[str, Any]:
    """1 ページの記号・文字・線。"""
    smap = match_symbols(gold["symbols"], pred["symbols"])
    gsym = {s["ref"]: s for s in gold["symbols"]}
    # 記号（種類別）
    per_class: dict[str, list[int]] = defaultdict(lambda: [0, 0, 0])
    matched_gold = set(smap.values())
    class_changes = 0
    for s in pred["symbols"]:
        g = gsym.get(smap.get(s["ref"], ""))
        if g is not None and g["class_key"] == s["class_key"]:
            per_class[s["class_key"]][0] += 1
        else:
            per_class[s["class_key"]][1] += 1
            if g is not None:
                per_class[g["class_key"]][2] += 1
                class_changes += 1
    for g in gold["symbols"]:
        if g["ref"] not in matched_gold:
            per_class[g["class_key"]][2] += 1
    # 文字
    labels, devices, terminals = [0, 0], [0, 0], [0, 0]
    label_errors = []
    for s in pred["symbols"]:
        g = gsym.get(smap.get(s["ref"], ""))
        if g is None or not norm_text(g.get("label")):
            continue
        labels[1] += 1
        devices[1] += 1
        if norm_text(s.get("label")) == norm_text(g.get("label")):
            labels[0] += 1
        else:
            label_errors.append({"symbol": g["ref"], "gold": g.get("label"), "pred": s.get("label")})
        gtok = (g.get("label") or "").split()
        ptok = (s.get("label") or "").split()
        if gtok and ptok and norm_text(gtok[0]) == norm_text(ptok[0]):
            devices[0] += 1
    tmap = _terminal_map(gold, pred, smap)
    gold_terms = {(g["ref"], x["ref"]): x for g in gold["symbols"] for x in g.get("terminals") or []}
    for (sref, tref), gkey in tmap.items():
        gname = norm_text(gold_terms.get(gkey, {}).get("name"))
        if not gname or not tref:
            continue
        terminals[1] += 1
        pname = next((norm_text(x.get("name")) for s in pred["symbols"] if s["ref"] == sref
                      for x in s.get("terminals") or [] if x["ref"] == tref), "")
        terminals[0] += int(pname == gname)
    # 線
    gold_edges = _edges(gold)
    gold_net = _nets(gold_edges)
    mapped = set()
    unmapped = 0
    for e in _edges(pred):
        ends = [tmap.get(x) or tmap.get((x[0], "")) for x in e]
        if any(x is None for x in ends) or ends[0] == ends[1]:
            unmapped += 1
            continue
        mapped.add(frozenset(ends))
    tp = len(mapped & gold_edges)
    fp_edges = mapped - gold_edges
    wrong = [e for e in fp_edges if len({gold_net.get(x, x) for x in e}) > 1]
    wires = _prf(tp, len(fp_edges) + unmapped, len(gold_edges - mapped)) | {
        "wrong_connections": len(wrong), "route_differences": len(fp_edges) - len(wrong), "unmatched_ends": unmapped}
    # 記号の削除（余分）・追加（見落とし）・種類の変更、ラベルの書き直し、配線の削除・追加
    corrections = (len(pred["symbols"]) - len(smap)) + (len(gold["symbols"]) - len(smap)) + class_changes \
        + (labels[1] - labels[0]) + wires["fp"] + wires["fn"]
    return {
        "symbols": {k: _prf(*v) for k, v in sorted(per_class.items())},
        "symbols_all": _prf(*(sum(v[i] for v in per_class.values()) for i in range(3))),
        "text": {"label_exact": _rate(*labels), "device_exact": _rate(*devices), "terminal_exact": _rate(*terminals),
                 "label_errors": label_errors[:30]},
        "wires": wires,
        "corrections": corrections,
        "symbol_map": smap,
    }


def _rate(ok: int, total: int) -> dict[str, Any]:
    return {"ok": ok, "total": total, "rate": round(ok / total, 4) if total else None}


def _page_key(page: dict[str, Any]) -> str:
    return norm_text(page.get("sheet_no") or page.get("name") or str(page.get("id")))


def _relations(pages: list[dict[str, Any]]) -> dict[str, Any]:
    circuit = build_circuit(pages)
    tracer = t.Tracer(circuit, pages)
    links = {frozenset([(x["project_id"], x["symbol_ref"]) for x in (link["from"], link["to"])]) for link in circuit.sheet_links}
    coil_pairs, sources = set(), {}
    for src in tracer.sources.values():
        for c in src.get("contacts", []):
            key = (c["project_id"], c["symbol_ref"])
            sources[key] = src["source"]
            for coil in src.get("coils", []):
                coil_pairs.add((key, (coil["project_id"], coil["symbol_ref"])))
    return {"circuit": circuit, "links": links, "coil_pairs": coil_pairs, "sources": sources}


def score(gold_pages: list[dict[str, Any]], pred_pages: list[dict[str, Any]], *,
          scenarios: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """1 つの図面セット（ページの集まり）の正解率。ページはシート番号（無ければ図面名）で対応づける。"""
    pred_by_key = {_page_key(p): p for p in pred_pages}
    pages, smaps = [], {}
    pid_map: dict[int, int] = {}
    for g in gold_pages:
        p = pred_by_key.get(_page_key(g))
        if p is None:
            pages.append({"page": _page_key(g), "missing": True})
            continue
        row = score_page(g, p)
        smaps[(p["id"])] = (g["id"], row.pop("symbol_map"))
        pid_map[p["id"]] = g["id"]
        pages.append({"page": _page_key(g), **row})
    # 別ページの参照・コイルと接点の対応（機械の記号を正解の記号に置き換えて比べる）
    gold_rel, pred_rel = _relations(gold_pages), _relations([p for p in pred_pages if p["id"] in pid_map])

    def to_gold(key: tuple[int, str]) -> tuple[int, str] | None:
        gid, smap = smaps.get(key[0], (None, {}))
        ref = smap.get(key[1])
        return (gid, ref) if gid is not None and ref else None

    def mapped(items, arity_pairs: bool):
        out, lost = set(), 0
        for item in items:
            parts = list(item)
            conv = [to_gold(x) for x in parts]
            if any(c is None for c in conv):
                lost += 1
                continue
            out.add(frozenset(conv) if not arity_pairs else tuple(conv))
        return out, lost

    links, lost_links = mapped(pred_rel["links"], False)
    coil, lost_coil = mapped(pred_rel["coil_pairs"], True)
    sources_ok = sources_total = 0
    for key, src in pred_rel["sources"].items():
        g = to_gold(key)
        if g is None or g not in gold_rel["sources"]:
            continue
        sources_total += 1
        sources_ok += int(gold_rel["sources"][g] == src)
    out = {
        "pages": pages,
        "sheet_links": _prf(len(links & gold_rel["links"]), len(links - gold_rel["links"]) + lost_links,
                            len(gold_rel["links"] - links)),
        "coil_contact": _prf(len(coil & gold_rel["coil_pairs"]), len(coil - gold_rel["coil_pairs"]) + lost_coil,
                             len(gold_rel["coil_pairs"] - coil)),
        "contact_sources": _rate(sources_ok, sources_total),
    }
    if scenarios:
        on_pred = circuit_scenarios.evaluate(pred_rel["circuit"], [p for p in pred_pages if p["id"] in pid_map], scenarios)
        on_gold = circuit_scenarios.evaluate(gold_rel["circuit"], gold_pages, scenarios)
        out["scenarios"] = {"pred": {k: on_pred[k] for k in ("passed", "total", "by_check")},
                            "gold": {k: on_gold[k] for k in ("passed", "total", "by_check")},
                            "failed": [r for r in on_pred["scenarios"] if not r["ok"]][:20]}
    out["summary"] = summarize([out])
    return out


def summarize(results: list[dict[str, Any]]) -> dict[str, Any]:
    """図面セットの結果をまとめる（件数を足してから率にする）。"""
    sym: dict[str, list[int]] = defaultdict(lambda: [0, 0, 0])
    text = defaultdict(lambda: [0, 0])
    wires = Counter()
    rel = defaultdict(Counter)
    corrections = pages = missing = 0
    for r in results:
        for p in r["pages"]:
            pages += 1
            if p.get("missing"):
                missing += 1
                continue
            for k, v in p["symbols"].items():
                for i, f in enumerate(("tp", "fp", "fn")):
                    sym[k][i] += v[f]
            for k in ("label_exact", "device_exact", "terminal_exact"):
                text[k][0] += p["text"][k]["ok"]
                text[k][1] += p["text"][k]["total"]
            wires.update({k: p["wires"][k] for k in ("tp", "fp", "fn", "wrong_connections", "route_differences")})
            corrections += p["corrections"]
        for k in ("sheet_links", "coil_contact"):
            rel[k].update({f: r[k][f] for f in ("tp", "fp", "fn")})
        rel["contact_sources"].update({"ok": r["contact_sources"]["ok"], "total": r["contact_sources"]["total"]})
    out = {
        "pages": pages, "missing_pages": missing,
        "symbols": {k: _prf(*v) for k, v in sorted(sym.items())},
        "symbols_all": _prf(*(sum(v[i] for v in sym.values()) for i in range(3))),
        "text": {k: _rate(*v) for k, v in text.items()},
        "wires": _prf(wires["tp"], wires["fp"], wires["fn"]) | {"wrong_connections": wires["wrong_connections"],
                                                                "route_differences": wires["route_differences"]},
        "sheet_links": _prf(rel["sheet_links"]["tp"], rel["sheet_links"]["fp"], rel["sheet_links"]["fn"]),
        "coil_contact": _prf(rel["coil_contact"]["tp"], rel["coil_contact"]["fp"], rel["coil_contact"]["fn"]),
        "contact_sources": _rate(rel["contact_sources"]["ok"], rel["contact_sources"]["total"]),
        "corrections": corrections,
    }
    scen = [r["scenarios"]["pred"] for r in results if "scenarios" in r]
    if scen:
        out["scenarios"] = _rate(sum(s["passed"] for s in scen), sum(s["total"] for s in scen))
    return out


def leakage(drawing_sets: dict[str, list[str]], train_pages: list[str], eval_sets: list[str]) -> list[dict[str, Any]]:
    """評価に使う図面セットのページが学習に使われていないか。drawing_sets: セット → ページ（シート番号）。"""
    train = {norm_text(p) for p in train_pages}
    out = []
    for name in eval_sets:
        used = sorted(p for p in drawing_sets.get(name, []) if norm_text(p) in train)
        if used:
            out.append({"drawing_set": name, "pages_in_training": used,
                        "message": f"図面セット {name} の {len(used)} ページが学習に使われています（評価は未使用のセットで）"})
    return out


def markdown(report: dict[str, Any]) -> str:
    s = report["summary"]

    def pr(x: dict[str, Any]) -> str:
        f = lambda v: "-" if v is None else f"{v:.3f}"  # noqa: E731
        return f"適合率 {f(x['precision'])} / 再現率 {f(x['recall'])}（TP {x['tp']}・FP {x['fp']}・FN {x['fn']}）"

    def rate(x: dict[str, Any]) -> str:
        return "-" if x.get("rate") is None else f"{x['rate']:.3f}（{x['ok']}/{x['total']}）"

    lines = [f"# 読み取りの正解率（{report.get('title', '')}）", "",
             f"- ページ: {s['pages']}（機械側に無いページ {s['missing_pages']}）",
             f"- 記号（全体）: {pr(s['symbols_all'])}",
             (f"- 文字: ラベル完全一致 {rate(s['text'].get('label_exact', {}))}、器具番号 {rate(s['text'].get('device_exact', {}))}、"
             f"端子番号 {rate(s['text'].get('terminal_exact', {}))}"),
             f"- 線（端子の組）: {pr(s['wires'])}、誤結線 {s['wires']['wrong_connections']}、経路違い {s['wires']['route_differences']}",
             f"- 別ページの参照: {pr(s['sheet_links'])}",
             f"- コイルと接点の対応: {pr(s['coil_contact'])}、接点の出どころの一致 {rate(s['contact_sources'])}",
             f"- 人の修正量の目安: {s['corrections']} 操作"]
    if "scenarios" in s:
        lines.append(f"- 代表回路: {rate(s['scenarios'])}")
    lines += ["", "## 記号（種類別）", "", "| 種類 | 適合率 | 再現率 | TP | FP | FN |", "|---|---|---|---|---|---|"]
    for k, v in s["symbols"].items():
        lines.append(f"| {k} | {v['precision'] if v['precision'] is not None else '-'} | "
                     f"{v['recall'] if v['recall'] is not None else '-'} | {v['tp']} | {v['fp']} | {v['fn']} |")
    if report.get("sets"):
        lines += ["", "## 図面セット別", "", "| 図面セット | ページ | 記号 F | 線 適合率 | 線 再現率 | 誤結線 | 参照 再現率 | 代表回路 |",
                  "|---|---|---|---|---|---|---|---|"]
        for name, r in report["sets"].items():
            x = r["summary"]
            sa = x["symbols_all"]
            f1 = (2 * sa["tp"] / (2 * sa["tp"] + sa["fp"] + sa["fn"])) if sa["tp"] else 0
            sc = x.get("scenarios", {})
            lines.append(f"| {name} | {x['pages']} | {f1:.3f} | {x['wires']['precision']} | {x['wires']['recall']} | "
                         f"{x['wires']['wrong_connections']} | {x['sheet_links']['recall']} | {rate(sc) if sc else '-'} |")
    if report.get("leakage"):
        lines += ["", "## 学習との重なり（要確認）", ""] + [f"- {x['message']}" for x in report["leakage"]]
    return "\n".join(lines) + "\n"
