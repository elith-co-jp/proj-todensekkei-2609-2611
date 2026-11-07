"""未学習図面の 6 項目を 1 コマンドで出す（#37）。

検出 F1、種別の正解率、器具番号・端子番号の読取、接続の F1、シート番号、リレー表の紐付け。
交差検証の検出結果（holdout_cv.py の --work）を入力にし、3 つの評価コマンドを順に実行してまとめる。
学習とテストの図面の重なりは holdout_cv.py が学習時に検査している。

Example:
    python analysis/tools/evaluate_all.py --gold a.zip b.zip --cv-run runs/cv --work runs/eval_all --cache runs/ocr_cache
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

TOOLS = Path(__file__).resolve().parent


def run(script: str, *args: object) -> str:
    command = [sys.executable, str(TOOLS / script), *map(str, args)]
    return subprocess.run(command, check=True, capture_output=True, text=True).stdout


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--gold", type=Path, nargs="+", required=True)
    parser.add_argument("--cv-run", type=Path, required=True, help="holdout_cv.py の --work（fold*/pred/<SHEET>.json）")
    parser.add_argument("--work", type=Path, required=True, help="配線の評価結果の置き場")
    parser.add_argument("--cache", type=Path, required=True, help="OCR 結果のキャッシュ置き場")
    parser.add_argument("--relay-gt", type=Path, help="リレー表の行の正解 JSON の置き場")
    parser.add_argument("--unseen-pages", type=Path, help="シート番号だけ測る未学習図面の PNG")
    parser.add_argument("--sheet-gt", type=Path, help="未学習図面のシート番号の正解 JSON")
    parser.add_argument("--label-llm", help="器具番号を読み直すローカル LLM の URL（任意）")
    args = parser.parse_args()

    detection = json.loads(run("holdout_cv.py", "--gold", *args.gold, "--work", args.cv_run, "--rescore"))
    run("eval_wiring_cv.py", "--gold", *args.gold, "--pred-run", args.cv_run, "--work", args.work,
        "--conditions", "pred", "--ocr-cache", args.cache / "wiring")
    wiring = json.loads((args.work / "summary.json").read_text(encoding="utf-8"))["pred"]["all"]
    text_args: list[object] = ["--gold", *args.gold, "--cache", args.cache, "--metrics", "labels,relay,sheet"]
    for flag, value in (("--relay-gt", args.relay_gt), ("--unseen-pages", args.unseen_pages),
                        ("--sheet-gt", args.sheet_gt), ("--label-llm", args.label_llm)):
        if value:
            text_args += [flag, value]
    text = json.loads(run("evaluate_text_extraction.py", *text_args))
    labels = text["labels"]["by_group"]["symbol:ALL"]
    symbols = detection["symbol_f1"]
    out = {
        "drawings": detection["drawings"],
        "symbol_detection": {**symbols, "gold_symbols": symbols["tp"] + symbols["fn"]},
        "symbol_type_accuracy": detection["type_accuracy"],
        "device_number_accuracy": {"accuracy": labels["device_acc"], "labels": labels["n"]},
        "terminal_numbers": wiring["terminal_numbers"],
        "connection_symbol_pairs": wiring["symbol_pairs"],
        "sheet_number_top1": {"gold": text["sheet_gold"], "unseen": text.get("sheet_unseen")},
        "relay_table": {"rows": text["relay"]["rows"], "coil_links": text["relay"]["link"]["coil"]},
    }
    print(json.dumps(out, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()

