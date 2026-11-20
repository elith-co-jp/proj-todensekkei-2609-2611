"""図面の読み取りの正解率を、文字・記号・線・別ページの参照・コイルと接点の対応・代表回路に分けて測る（DD-0035）。

正解と機械の結果は、どちらもアプリの書き出し ZIP（bundle.json）。ページはシート番号（無ければ図面名）で対応づける。

Example:
    python analysis/tools/eval_reading.py --gold gold.zip --pred pred.zip \\
        --sets drawing_sets.yaml --scenarios scenarios.yaml --train-pages train.txt --out runs/reading
drawing_sets.yaml: {DS01: [ER21301, ER21302], DS02: [...]}（無ければ全ページを 1 つのセットにする）
scenarios.yaml: {DS01: [シナリオ...]} か、circuit_scenarios の形（全セット共通）
"""

from __future__ import annotations

import argparse
import json
import sys
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import yaml

from services import reading_metrics as rm


def load_pages(paths: list[Path]) -> list[dict]:
    pages = []
    for path in paths:
        if path.suffix.lower() == ".json":
            data = json.loads(path.read_text(encoding="utf-8"))
        else:
            with zipfile.ZipFile(path) as zf:
                data = json.loads(zf.read("bundle.json"))
        pages += data["projects"] if isinstance(data, dict) else data
    return pages


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--gold", type=Path, nargs="+", required=True)
    parser.add_argument("--pred", type=Path, nargs="+", required=True)
    parser.add_argument("--sets", type=Path, help="図面セット → ページ（シート番号）の YAML")
    parser.add_argument("--scenarios", type=Path, help="代表回路の正解（YAML）")
    parser.add_argument("--train-pages", type=Path, help="学習に使ったページ（1 行 1 シート番号）")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()

    gold, pred = load_pages(args.gold), load_pages(args.pred)
    sets = yaml.safe_load(args.sets.read_text(encoding="utf-8")) if args.sets else {"all": [rm._page_key(p) for p in gold]}
    scen_data = yaml.safe_load(args.scenarios.read_text(encoding="utf-8")) if args.scenarios else None
    report = {"title": ", ".join(str(p) for p in args.gold), "sets": {}}
    for name, keys in sets.items():
        wanted = {rm.norm_text(k) for k in keys}
        g = [p for p in gold if rm._page_key(p) in wanted]
        if not g:
            continue
        scenarios = None
        if isinstance(scen_data, dict) and "scenarios" in scen_data:
            scenarios = scen_data["scenarios"]
        elif isinstance(scen_data, dict):
            scenarios = scen_data.get(name)
        report["sets"][name] = rm.score(g, pred, scenarios=scenarios)
    report["summary"] = rm.summarize(list(report["sets"].values()))
    if args.train_pages:
        train = [x.strip() for x in args.train_pages.read_text(encoding="utf-8").splitlines() if x.strip()]
        report["leakage"] = rm.leakage(sets, train, list(report["sets"]))
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "reading_metrics.json").write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    (args.out / "reading_metrics.md").write_text(rm.markdown(report), encoding="utf-8")
    print(rm.markdown(report))


if __name__ == "__main__":
    main()
