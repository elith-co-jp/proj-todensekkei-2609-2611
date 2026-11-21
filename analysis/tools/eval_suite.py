"""評価を 1 コマンドで回し、基準値より悪くなっていないかを確かめる（DD-0038）。

正解データの無い今は、正解のわかっている合成回路・合成図面で測る。顧客の正解（bundle.json の ZIP）が届いたら
``--gold`` と ``--pred`` を足せば、図面の読み取りの正解率（eval_reading.py）も同じ表に入る。

測るもの:
  logic     合成回路（DD-0032）: シミュレーション・回路コードの往復・波及範囲が正解と一致するか、誤りを入れた図面を矛盾チェックが見つけるか
  reading   図面を読む手順（DD-0035）: 成立条件・3 値の判定・順引きが正解と一致するか
  drawings  合成図面（PDF → 画像 → 配線の読み取り → 回路）: 配線の再現率・適合率と、回路の意味が正解と同じか。
            きれいな図面と、スキャンに似せて劣化させた図面（受領図面はスキャン）の両方。--model で記号の検出も YOLO で行う

Example:
    python analysis/tools/eval_suite.py                      # 既定の規模。基準値（analysis/eval_baseline.json）と比べる
    python analysis/tools/eval_suite.py --quick              # CI 向けの小さい規模（1 分ほど）
    python analysis/tools/eval_suite.py --write-baseline     # 今の結果を基準値にする
    python analysis/tools/eval_suite.py --model assets/models/yolo11s_symbols_best.pt --enable-ocr
    python analysis/tools/eval_suite.py --gold gold.zip --pred pred.zip   # 顧客の正解が届いたら
基準値より悪くなった指標があれば終了コード 1。結果は --out（既定 runs/eval/<日時>）の summary.json・summary.md。
"""

from __future__ import annotations

import _repo_path  # noqa: F401  リポジトリの直下を import できるようにする

import argparse
import json
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from analysis.synthetic_circuits import logic_eval
from analysis.synthetic_circuits.dataset import DatasetConfig, evaluate_dataset, generate_dataset

TOOLS = Path(__file__).resolve().parent
BASELINE = TOOLS.parent / "eval_baseline.json"
JST = timezone(timedelta(hours=9))
# 規模（--quick と既定）。基準値はこの規模ごとに持つ（規模が変わると値も変わるため）。
SCALES = {
    "quick": {"logic_seeds": 8, "faults": 1, "reading_seeds": 8, "drawings": 8},
    "full": {"logic_seeds": 40, "faults": 3, "reading_seeds": 40, "drawings": 40},
}
# 指標ごとの向き。higher: 大きいほど良い、lower: 小さいほど良い。許す悪化の幅は TOLERANCE。
DIRECTION_LOWER = ("false_alarms", "silent_harm", "wrong")
TOLERANCE = 0.005


def _rate(a: int, b: int) -> float | None:
    return round(a / b, 4) if b else None


def run_logic(seeds: int, faults: int) -> dict[str, Any]:
    report = logic_eval.evaluate(range(seeds), faults_per_kind=faults, trials=10, solvers=0, timers=False)
    clean, kinds = report["summary"]["clean"], report["summary"]["by_kind"]
    total = {k: sum(v[k] for v in kinds.values()) for k in ("n", "detected", "silent_harm", "wrong_fixes")}
    return {
        "logic.sim_agree": _rate(clean["sim_agree"], clean["sim_total"]),
        "logic.code_roundtrip_agree": _rate(clean["code_roundtrip_agree"], clean["code_roundtrip_total"]),
        "logic.impact_agree": _rate(clean["impact_agree"], clean["impact_total"]),
        "logic.false_alarms": clean["false_alarms"],
        "logic.fault_detected": _rate(total["detected"], total["n"]),
        "logic.silent_harm": total["silent_harm"],
        "logic.wrong_fixes": total["wrong_fixes"],
    }


def run_reading(seeds: int) -> dict[str, Any]:
    sys.path.insert(0, str(TOOLS))
    import validate_reading_procedure as vrp

    from services import circuit_model

    saved = circuit_model.ngspice_path
    circuit_model.ngspice_path = lambda: None  # 判定は論理だけで、回路計算は使わない（validate_reading_procedure と同じ）
    try:
        s = vrp.summarize([vrp.check_seed(seed, trials=5) for seed in range(seeds)])
    finally:
        circuit_model.ngspice_path = saved
    return {
        "reading.condition_ok": _rate(s["condition_ok"], s["loads"]),
        "reading.decided_ok": _rate(s["decided_ok"], s["decided"]),
        "reading.unknown_wrong": s["unknown_wrong"],
        "reading.missing_wrong": s["missing_wrong"],
        "reading.effects_ok": _rate(s["effects_ok"], s["effects"]),
    }


def run_drawings(count: int, work: Path, *, model: Path | None, enable_ocr: bool) -> dict[str, Any]:
    root = work / "synthetic"
    generate_dataset(root, DatasetConfig(count=count, seed=2609))
    out: dict[str, Any] = {}
    for name, scan_seed in (("clean", None), ("scan", 1000)):
        r = evaluate_dataset(root, disable_ocr=not enable_ocr, model_path=model, scan_seed=scan_seed)
        prefix = f"drawings.{name}"
        out |= {
            f"{prefix}.wire_recall": round(r["connections"]["recall"], 4),
            f"{prefix}.wire_precision": round(r["connections"]["precision"], 4),
            f"{prefix}.circuit_match": round(r["circuit_semantics"]["recall"], 4),
            f"{prefix}.symbol_f1": round(r["symbol_detection"]["f1"], 4),
            f"{prefix}.errors": r["errors"],
        }
        if enable_ocr:
            out[f"{prefix}.label_accuracy"] = r["label_ocr"]["accuracy"]
    return out


def run_gold(gold: list[Path], pred: list[Path], work: Path) -> dict[str, Any]:
    """顧客の正解と機械の結果（どちらもアプリの書き出し ZIP）を eval_reading.py で比べ、主な正解率を取り出す。"""
    out_dir = work / "reading"
    subprocess.run([sys.executable, str(TOOLS / "eval_reading.py"), "--gold", *map(str, gold), "--pred", *map(str, pred),
                    "--out", str(out_dir)], check=True)
    report = json.loads((out_dir / "reading_metrics.json").read_text(encoding="utf-8"))
    flat: dict[str, Any] = {}

    def walk(prefix: str, value: Any) -> None:
        if isinstance(value, dict):
            for k, v in value.items():
                walk(f"{prefix}.{k}", v)
        elif isinstance(value, (int, float)) and not isinstance(value, bool) and any(
                w in prefix for w in ("recall", "precision", "f1", "accuracy", "rate")):
            flat[prefix] = round(float(value), 4)

    walk("gold", report)
    return flat


def compare(metrics: dict[str, Any], baseline: dict[str, Any]) -> list[dict[str, Any]]:
    """基準値より悪くなった指標。基準値に無い指標・値が None の指標は比べない。"""
    worse = []
    for key, base in baseline.items():
        now = metrics.get(key)
        if now is None or base is None:
            continue
        lower = key.rsplit(".", 1)[-1].startswith(DIRECTION_LOWER) or key.endswith(".errors")
        tol = 0 if isinstance(base, int) and not isinstance(base, bool) and lower else TOLERANCE
        if (now > base + tol) if lower else (now < base - tol):
            worse.append({"metric": key, "baseline": base, "now": now})
    return worse


def markdown(scale: str, metrics: dict[str, Any], worse: list[dict[str, Any]], baseline: dict[str, Any] | None,
             seconds: float) -> str:
    when = datetime.now(JST).strftime("%Y-%m-%d %H:%M")
    lines = [f"# 評価（{when} JST、規模 {scale}、{seconds:.0f} 秒）", "",
             "| 指標 | 今回 | 基準値 |", "|---|---:|---:|"]
    bad = {w["metric"] for w in worse}
    for key, value in metrics.items():
        base = (baseline or {}).get(key, "")
        mark = " ⚠" if key in bad else ""
        lines.append(f"| {key}{mark} | {value} | {base} |")
    lines += ["", f"基準値より悪くなった指標: {len(worse)} 件" + ("" if baseline else "（基準値なし）")]
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--quick", action="store_true", help="CI 向けの小さい規模で回す")
    parser.add_argument("--only", nargs="+", choices=["logic", "reading", "drawings"], help="一部だけ回す")
    parser.add_argument("--model", type=Path, help="記号の検出に使う YOLO の重み（無ければ正解の記号の位置を使う）")
    parser.add_argument("--enable-ocr", action="store_true", help="合成図面で文字の読み取り（RapidOCR）も行う")
    parser.add_argument("--gold", type=Path, nargs="+", help="顧客の正解（アプリの書き出し ZIP）")
    parser.add_argument("--pred", type=Path, nargs="+", help="機械の結果（アプリの書き出し ZIP）。--gold と組で使う")
    parser.add_argument("--baseline", type=Path, default=BASELINE)
    parser.add_argument("--write-baseline", action="store_true", help="今回の結果をこの規模の基準値として書く")
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    if bool(args.gold) != bool(args.pred):
        parser.error("--gold と --pred は組で指定する")

    scale = "quick" if args.quick else "full"
    if args.model or args.enable_ocr:
        scale += "+model" if args.model else ""
        scale += "+ocr" if args.enable_ocr else ""
    size = SCALES["quick" if args.quick else "full"]
    parts = args.only or ["logic", "reading", "drawings"]
    out = args.out or Path("runs/eval") / datetime.now(JST).strftime("%Y%m%d-%H%M%S")
    out.mkdir(parents=True, exist_ok=True)

    start = time.time()
    metrics: dict[str, Any] = {}
    with tempfile.TemporaryDirectory() as tmp:
        if "logic" in parts:
            metrics |= run_logic(size["logic_seeds"], size["faults"])
        if "reading" in parts:
            metrics |= run_reading(size["reading_seeds"])
        if "drawings" in parts:
            metrics |= run_drawings(size["drawings"], Path(tmp), model=args.model, enable_ocr=args.enable_ocr)
        if args.gold:
            metrics |= run_gold(args.gold, args.pred, out)
    seconds = time.time() - start

    baselines = json.loads(args.baseline.read_text(encoding="utf-8")) if args.baseline.exists() else {}
    baseline = baselines.get(scale)
    worse = compare(metrics, baseline) if baseline else []
    summary = {"scale": scale, "seconds": round(seconds, 1), "metrics": metrics, "worse": worse,
               "when": datetime.now(JST).isoformat(timespec="seconds")}
    (out / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
    text = markdown(scale, metrics, worse, baseline, seconds)
    (out / "summary.md").write_text(text, encoding="utf-8")
    print(text)
    print(f"結果: {out}/summary.json, summary.md")

    if args.write_baseline:
        # 顧客の正解の指標は図面の組で変わるので、基準値には入れない
        baselines[scale] = {k: v for k, v in metrics.items() if not k.startswith("gold.")}
        args.baseline.write_text(json.dumps(baselines, ensure_ascii=False, indent=1, sort_keys=True) + "\n", encoding="utf-8")
        print(f"基準値を書きました: {args.baseline}（{scale}）")
    elif worse:
        print("基準値より悪くなった指標があります:", file=sys.stderr)
        for w in worse:
            print(f"  {w['metric']}: {w['baseline']} → {w['now']}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
