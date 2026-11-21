"""正解つきの合成回路で、回路の理解・シミュレーション・矛盾チェックを検証する（DD-0032）。

Example:
    python analysis/tools/validate_circuit_logic.py --seeds 40 --faults 3 --solvers 5 --out runs/logic_validation
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from analysis.synthetic_circuits import logic_eval  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--seeds", type=int, default=40, help="合成する回路の数")
    parser.add_argument("--faults", type=int, default=3, help="誤りの種類ごとに、回路 1 件あたり入れる回数")
    parser.add_argument("--trials", type=int, default=20, help="動作の比較に使うランダムな入力・励磁の数")
    parser.add_argument("--solvers", type=int, default=0, help="回路計算どうし（ngspice・Lcapy）も比べる回路の数")
    parser.add_argument("--timers", action="store_true", help="リレーの一部をタイマーにし、時間の流れも正解と比べる（DD-0033）")
    parser.add_argument("--edits", type=int, default=0,
                        help="変更の操作（DD-0034）を当てた図面が、変えた正本の正解と一致するかも調べる回路の数")
    parser.add_argument("--out", type=Path, default=Path("runs/logic_validation"))
    args = parser.parse_args()
    start = time.time()
    report = logic_eval.evaluate(range(args.seeds), faults_per_kind=args.faults, trials=args.trials, solvers=args.solvers,
                                 timers=args.timers)
    if args.edits:
        rows = [logic_eval.edit_roundtrip(seed) for seed in range(args.edits)]
        report["edit_roundtrip"] = {"circuits": args.edits, "agree": sum(r["agree"] for r in rows),
                                    "total": sum(r["total"] for r in rows)}
    report["seconds"] = round(time.time() - start, 1)
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    (args.out / "report.md").write_text(logic_eval.markdown(report), encoding="utf-8")
    print(logic_eval.markdown(report))
    if "edit_roundtrip" in report:
        e = report["edit_roundtrip"]
        print(f"変更の操作の往復（{e['circuits']} 回路）: {e['agree']}/{e['total']} 一致")
    print(f"{report['seconds']} 秒。{args.out}/report.json, report.md")


if __name__ == "__main__":
    main()
