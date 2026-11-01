"""リレー表の行と接点の紐付けを、手で読んだリレー表の正解（relay_gt）と比べる（#40 #44）。

eval_wiring_cv.py が --work に書いた構造 JSON（oracle・pred）を、アプリの書き出しと同じ document_structure で
図面どうし結び付けてから採点する。

- row_links: 位置（行列）と正解の行が指す接点の組。正解の行は contact_terminal_gt.json（id = <表の図面>#<行番号>）で
  正解データの接点に対応付けてある。OCR で読んだ表の行は器具番号と端子で正解の行に合わせる。
  device_level は従来の紐付け（器具番号が同じ接点の集まり・行の集まり）に正しい接点・行が入っているか。
- class: 接点 a/b の種類の正解率と contact_b の F1（交差検証の検出、IoU 0.5）。表の種類を当てる前と後。
  リレー表のある図面だけと、全図面の 2 通り。
- contact_terminal_numbers: 書き出し後の接点の端子番号（--contact-gt）。

例:
    python analysis/tools/eval_relay_links.py --gold a.zip b.zip --work runs/wiring --pred-run runs/cv \\
        --contact-gt contact_terminal_gt.json
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from eval_wiring_cv import contact_number_outcomes, file_predictions, oracle_predictions, structure_predictions  # noqa: E402
from holdout_cv import gold_items, load_gold, match, prf, score_symbols  # noqa: E402

from analysis.text_extraction import match_key, same_relay_device  # noqa: E402

CONTACTS = {"contact_a", "contact_b"}


def ocr_row_index(rows: list[dict[str, str]], truth: dict[str, Any]) -> int | None:
    """正解の行に当たる、OCR で読んだ表の行（器具番号と端子が同じ。複数あれば行列も同じもの）。"""
    same = [i for i, r in enumerate(rows) if same_relay_device(truth["device"], r["device"])
            and match_key(r["terminal"]) == match_key(truth["terminal"])]
    exact = [i for i in same if rows[i]["position"] == truth["position"]]
    return (exact or same or [None])[0]


def gold_of(drawing: dict[str, Any], pred: list[dict[str, Any]], structure: dict[str, Any], class_of: dict[str, str]) -> dict:
    """構造 JSON の記号 ID から正解データの記号の ref へ（種類を問わず IoU 0.5 で対応）。"""
    active = [{**p, "class_name": class_of.get(p["structure_id"], p["class_name"])} if p["kind"] == "symbol" else p
              for p in structure_predictions(structure, pred)]
    scored = score_symbols(gold_items(drawing), active)
    return scored, {scored["p_sym"][pi]["structure_id"]: scored["g_sym"][gi]["ref"] for gi, pi, _ in scored["matches"]}


def class_counts(drawing, pred, structure, class_of) -> Counter:
    """接点 a/b の種類の正解（種類を問わず対応した組のうち）と contact_b の検出の TP/FP/FN。"""
    active = [{**p, "class_name": class_of.get(p["structure_id"], p["class_name"])} if p["kind"] == "symbol" else p
              for p in structure_predictions(structure, pred)]
    scored = score_symbols(gold_items(drawing), active)
    out = Counter()
    g_sym, p_sym = scored["g_sym"], scored["p_sym"]
    for gi, pi, _ in match(g_sym, p_sym, class_aware=False):
        if g_sym[gi]["class_name"] in CONTACTS and p_sym[pi]["class_name"] in CONTACTS:
            out["class_n"] += 1
            out["class_ok"] += g_sym[gi]["class_name"] == p_sym[pi]["class_name"]
    per_b = scored["per_class"].get("contact_b", Counter())
    out.update(b_tp=per_b["tp"], b_fp=per_b["fp"], b_fn=per_b["fn"])
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--gold", type=Path, nargs="+", required=True)
    parser.add_argument("--work", type=Path, required=True, help="eval_wiring_cv の --work（oracle/ pred/ に構造 JSON）")
    parser.add_argument("--pred-run", type=Path, required=True)
    parser.add_argument("--contact-gt", type=Path, required=True, help="contact_terminal_gt.json（正解の行と接点の対応）")
    parser.add_argument("--details", action="store_true")
    args = parser.parse_args()

    from services.structure_service import document_structure

    _, drawings = load_gold(args.gold)
    tune = set(sorted(d["sheet"] for d in drawings)[0::2])
    truths = json.loads(args.contact_gt.read_text(encoding="utf-8"))["contacts"]
    summary: dict[str, Any] = {}
    for condition in ("oracle", "pred"):
        folder = args.work / condition
        present = [d for d in drawings if (folder / f"{d['sheet']}.json").exists()]
        pages, preds, before = {}, {}, {}
        for d in present:
            page = json.loads((folder / f"{d['sheet']}.json").read_text(encoding="utf-8"))
            page["source"] = {**(page.get("source") or {}), "project_id": d["sheet"]}
            pages[d["sheet"]] = page
            preds[d["sheet"]] = oracle_predictions(d) if condition == "oracle" else file_predictions(args.pred_run, d)
            before[d["sheet"]] = {s["id"]: s.get("detected_class", s["class_name"]) for s in page["symbols"]}
        document_structure(list(pages.values()))
        by_sheet = {d["sheet"]: d for d in present}
        links, details = {k: Counter() for k in ("tune", "report", "all")}, []
        for truth in truths:
            coil, target = truth["coil_sheet"], truth["sheet"]
            if coil not in pages or target not in pages:
                continue
            outcome = row_outcome(truth, pages[coil], pages[target], by_sheet[target], preds[target])
            for k in ("tune" if target in tune else "report", "all"):
                links[k].update(outcome)
            details.append(f"{condition} {truth['id']} {target} {truth['position']} {dict(outcome)}")
        classes = {}
        for scope in ("with_relay_table", "all"):
            before_c, after_c = Counter(), Counter()
            for sheet, page in pages.items():
                if scope == "with_relay_table" and not (page.get("relay_table") or {}).get("rows"):
                    continue
                after = {s["id"]: s["class_name"] for s in page["symbols"]}
                before_c.update(class_counts(by_sheet[sheet], preds[sheet], page, before[sheet]))
                after_c.update(class_counts(by_sheet[sheet], preds[sheet], page, after))
            classes[scope] = {
                when: {"drawings": sum(1 for p in pages.values() if scope == "all" or (p.get("relay_table") or {}).get("rows")),
                       "class_accuracy": round(c["class_ok"] / c["class_n"], 4) if c["class_n"] else None,
                       "class_scored": c["class_n"], "contact_b": prf(c["b_tp"], c["b_fp"], c["b_fn"])}
                for when, c in (("before", before_c), ("after", after_c))
            }
            classes[scope]["changed"] = sum(
                1 for s, p in pages.items() if scope == "all" or (p.get("relay_table") or {}).get("rows")
                for sym in p["symbols"] if sym.get("class_source") == "relay_table")
        numbers = {k: Counter() for k in ("tune", "report", "all")}
        for truth in truths:
            if truth["sheet"] in pages:
                d = by_sheet[truth["sheet"]]
                scored, _ = gold_of(d, preds[truth["sheet"]], pages[truth["sheet"]], {})
                symbols = {s["id"]: s for s in pages[truth["sheet"]]["symbols"]}
                outcome = contact_number_outcomes([truth], scored["g_sym"], scored["p_sym"], scored["matches"], symbols)
                for k in ("tune" if truth["sheet"] in tune else "report", "all"):
                    numbers[k].update(outcome)
        summary[condition] = {
            "drawings": len(pages),
            "row_links": {k: dict(v) for k, v in links.items()},
            "classes": classes,
            "contact_terminal_numbers_after_export": {
                k: {"ends": v["ends"], "ends_correct": v["ends_correct"], "ends_wrong": v["ends_wrong"],
                    "ends_unread": v["ends_unread"], "pairs_correct": v["contacts_pair_correct"], "contacts": v["contacts"]}
                for k, v in numbers.items()},
        }
        if args.details:
            summary[condition]["details"] = details
    print(json.dumps(summary, ensure_ascii=False, indent=1))


def row_outcome(truth: dict[str, Any], coil_page: dict[str, Any], target_page: dict[str, Any],
                drawing: dict[str, Any], pred: list[dict[str, Any]]) -> Counter:
    """正解の 1 行: 表の行を読めたか、位置で正しい接点に結び付いたか、従来の器具番号の紐付けに入っていたか。"""
    out = Counter(rows=1)
    rows = (coil_page.get("relay_table") or {}).get("rows", [])
    index = ocr_row_index(rows, truth)
    _, sid_to_gold = gold_of(drawing, pred, target_page, {})
    gold_to_sid = {g: s for s, g in sid_to_gold.items()}
    right = gold_to_sid.get(truth["gold_ref"])
    if right is None:
        out["contact_undetected"] += 1
    if index is None:
        out["row_unread"] += 1
        return out
    link = next((link for device in coil_page["relay_table"].get("links", [])
                 for link in device.get("row_links", []) if link["row"] == index), None)
    if link is None or link["symbol_id"] is None:
        out["unlinked"] += 1
    elif right is not None and link["symbol_id"] == right:
        out["linked_correct"] += 1
        out["status_linked" if link["status"] == "linked" else "status_review"] += 1
        check = next((s.get("relay_row") or {} for s in target_page["symbols"] if s["id"] == right), {})
        out[f"terminal_check_{check.get('terminal_check')}"] += 1
    else:
        out["linked_wrong"] += 1
    device = next((d for d in coil_page["relay_table"].get("links", []) if d["device"] == rows[index]["device"]), {})
    if coil_page is target_page:
        out["device_level_correct"] += right is not None and right in device.get("contact_symbol_ids", [])
    else:
        relay = next((s.get("relay_link") or {} for s in target_page["symbols"] if s["id"] == right), {})
        out["device_level_correct"] += index in relay.get("relay_table_rows", [])
    return out


if __name__ == "__main__":
    main()
